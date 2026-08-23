# Apache Flink (아키텍처 · 프로젝트 관점)

## 개요

Flink는 **상태 기반 스트림 처리 엔진**이다. 무한 스트림을 이벤트 시간(event-time) 기준으로 낮은
지연에 처리하고, 체크포인트로 **정확히 한 번(exactly-once)** 상태를 보장한다. JobManager가 조율하고
TaskManager가 병렬 처리하며, 배치는 스트림의 특수 경우로 취급한다(통합 API).

- 최신 안정: **Flink 2.3.0**(2026-06). (2.0은 2025-03의 메이저 마일스톤)

## 이 프로젝트에서의 위치 — 🚧 채택·이행중

**Flink는 배치 전용이 아니다.** 오퍼레이터 + `FlinkDeployment` 세션 클러스터로
**Spark ↔ Flink Iceberg 왕복**을 배치·스트리밍 양쪽에서 닫았다 —
**Spark append(소스) → Flink 스트리밍 읽기 → Iceberg 싱크 → Spark 되읽기**를
장수명 잡 하나로 관통한다. 체크포인트·RocksDB·S3 상태 저장이 실제로 배포돼 있다.

**스트림 소스는 Iceberg bronze 스트리밍 읽기**이고 **Redpanda는 미도입 유지**다 —
기각이 아니라 **대체되어 불필요해진 것**이다.

📌 **각 Phase의 진행 상태는 저장소 밖에 있다** — `$OBSIDIAN_VAULT/status/redesign-progress.md`.

### 회수 규율 — 검증 후 그 자리에서 내린다

세션 클러스터를 띄워 두면 **잡이 없어도 JobManager가 상주 자원을 먹는다.**
🔴 **예산 여유는 회수를 면제하지 않는다** — 동시 기동이 허용된 것은 예산이 늘어서이지
"놀려도 괜찮아져서"가 아니다.

🔴 **스트리밍에는 축이 하나 더 붙는다** — TM이 잡 수명 내내 살아 동시 기동 허용의 전제가 깨진다.
그래서 **시연 창 한정 + 즉시 회수**로 운용하고 **상시 운전은 하지 않는다.**
잡 제출 **전에** 회수 시점을 정한다. 정본은 [conventions/k8s.md](../conventions/k8s.md) §9-3.

### 배치에서 성립한 것을 스트리밍에 일반화하지 않는다

| 축 | 배치 | 스트리밍 |
| --- | --- | --- |
| Iceberg 싱크 커밋 | **잡 완료 시점** | **체크포인트 단위** |
| 체크포인트 | 불필요 | **필수** — `notifyCheckpointComplete`에서 커밋한다 |
| `flink-s3-fs-hadoop` 플러그인 | 불필요 | **필요**(러너 이미지 재빌드 동반) |
| TaskManager | 온디맨드 — 잡과 함께 뜨고 회수된다 | **잡 수명 내내 상주** |

📌 **"체크포인트가 필요 없다"가 아니라 "이 잡에는 필요 없다"** 로 읽는다.
배치 성공을 "S3 플러그인 없이 된다"로 일반화하면 스트리밍 착수 시점에 같은 조사를 반복한다.

🔴 **"TM은 온디맨드"는 배치 잡에서 관측된 성질이지 Flink의 성질이 아니다.**

### 순서 함정 — 취소하면 체크포인트가 지워진다

`DELETE_ON_CANCELLATION`이 걸려 있으면 **`flink cancel`이 체크포인트를 함께 지운다.**
복구를 검증하려면 **취소 전에** 체크포인트 경로를 확보한다 — 지운 뒤에는 되돌릴 수 없다.

## 운영 메모

- **소스**: Iceberg bronze 테이블 스트리밍 읽기(`streaming=true` · `monitor-interval` ·
  `starting-strategy=TABLE_SCAN_THEN_INCREMENTAL`).
  **싱크**: Iceberg — Spark와 **동일 JDBC 카탈로그**를 공유한다(낙관적 동시성).
- **체크포인트**: S3 호환 SeaweedFS 재사용(path-style 강제), 상태 백엔드 **RocksDB**.
  `execution.checkpointing.*` + `state.backend.type`으로 선언한다.
- **카탈로그 정합**: Spark·Flink 동시 writer 구조라 장기적으로 REST 카탈로그 이행 유인이 크다.
- **REST와 UI가 같은 포트다** — UI를 열면 **잡 제출 API도 함께 나간다**.
  "UI만 열었다"로 읽지 않는다.

## 스트림 소스를 Iceberg changelog로 정한 근거

**전제부터 교정됐다.** 출발 질문은 *"Flink CDC 구성을 어떻게 보는가"* 였으나 되물어 확인한 결과
뜻한 것은 **DB CDC가 아니라 Iceberg 변경분(changelog)** 이었다.
이 교정만으로 **브로커도 Debezium도 필요 없어졌다.**

| 근거 | 내용 |
| --- | --- |
| ⓐ **신규 상주 인프라 0** | 소스가 이미 있는 Iceberg 테이블이라 브로커·소스 DB·Debezium이 전부 불필요하다 |
| ⓑ **기존 계획이 자기모순이었다** | 실버 모델을 인위적으로 되돌려 스트림처럼 흘리는 구성이었다. 이미 계산된 결과를 원재료인 척 되먹이는 것이라 스트리밍의 존재이유를 약화시킨다 |
| ⓒ **Flink CDC는 버전이 안 맞는다** | 지원 Flink 버전이 현행과 짝이 아니다. 버전은 **Iceberg가 지원하는 짝**으로 고정한다 |

Spark 쪽 배치 변경분 조회는 Iceberg 프로시저 `create_changelog_view`를 쓴다 —
**Spark 자체 DSv2 CDC API에 기대지 않는다**(현행 엔진 버전에 없다).

### 급소 — Flink 스트리밍 읽기는 **append 스냅샷만** 본다

이 절에서 가장 비싼 제약이다. 놓치면 소스 테이블을 잘못 고르고
**틀린 게 아니라 조용히 빠진 결과**를 얻는다.

- Iceberg Flink 스트리밍 소스는 **`IncrementalAppendScan` 기반**이라
  **overwrite·delete 스냅샷을 지원하지 않는다.**
- ⇒ 🔴 **`merge into`를 쓰는 dbt 실버 테이블은 스트림 소스로 쓸 수 없다.**
  **소스는 bronze append 테이블로 고정**한다.
- **`UPDATE` 한 번이면 그 테이블은 스트림 소스 자격을 잃는다** —
  `UPDATE`가 `overwrite` 스냅샷을 남기기 때문이다. `merge into`만의 문제가 아니다.
- ⚠️ 이 제약은 **읽기 축**이다. 쓰기 축에는 별도 제약이 붙는다 — Iceberg Flink **upsert 싱크는
  format v2 + primary key가 필수**이고 **`OVERWRITE`와 상호배타**다.

#### 확인된 축과 확인되지 않은 축을 가른다

**"append만 본다"는 문서화된 제약이지 관측한 사실이 아니다.**
닫힌 것은 *적격 소스에서 스트리밍 읽기가 흐른다*까지이고,
**부적격 소스에서 무엇이 어떻게 빠지는지**는 돌려보지 않았다 —
그 축은 **overwrite 스냅샷이 섞인 테이블로 잡을 돌려야** 닫힌다.

이 구분이 중요한 이유는 **실패 모드가 에러가 아니라 조용한 누락**이기 때문이다.

#### 반증 — "overwrite 스냅샷을 스킵하는 옵션"은 없다

*"옵션 하나로 스킵할 수 있다"* 는 서술이 돌지만 **거짓이다.**
소스코드와 공식 read option 목록 어디에도 해당 항목이 없다.

📌 **C·D 등급만으로 단정하지 않는다** — 특히 *"옵션 하나로 제약을 우회할 수 있다"* 는 형태의
요약은 **검증 비용보다 유혹이 크다.**

### Flink CDC(DB CDC)는 ★☆☆☆☆ — 비권장

소스 DB·Debezium·브로커가 전부 새로 필요하고, 이 저장소의 원천은 **파일(csv.gz)이지 OLTP DB가 아니다.**
CDC를 쓰려면 없는 DB를 먼저 만들어야 한다 — **목적과 수단이 뒤집힌다.**

## 참고

- Flink 문서(stable): https://flink.apache.org/documentation/flink-stable/
- 다운로드/릴리스: https://flink.apache.org/downloads/
- Flink Kubernetes Operator: https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-main/
- Flink + Iceberg connector: https://iceberg.apache.org/docs/latest/flink/
- Iceberg Flink 읽기(스트리밍·`IncrementalAppendScan`): https://iceberg.apache.org/docs/latest/flink-queries/
- Iceberg Flink 설정(read option 목록): https://iceberg.apache.org/docs/latest/flink-configuration/
- Flink CDC(🔎 **비권장 · 기각** — 3.6.0 지원 Flink 1.20.x·2.2.x, 현행 2.1): https://nightlies.apache.org/flink/flink-cdc-docs-stable/
- Redpanda(🔎 **미도입 유지** — 스트림 소스가 Iceberg로 바뀌어 불필요): https://docs.redpanda.com/
