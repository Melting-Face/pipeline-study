# 아키텍처 문서 (architectures)

이 프로젝트의 전체 스택과, 각 처리·배포 기술을 **프로젝트 결정 관점**으로 정리한다.
채택 ✅ / 채택·이행중(PoC 게이트) 🚧 / 미채택(참고·향후 검토) 🔎로 표기한다.

> **재설계 진행중**: 호스트 Dagster + K8s(Spark Operator)로의 이행 로드맵은 [../redesign.md](../redesign.md).

## 목차

| 문서 | 상태 | 내용 |
| --- | --- | --- |
| [overview.md](overview.md) | 🚧 | 현행 스택 스냅샷·데이터 흐름(Dagster·dbt·Iceberg·SeaweedFS) — **재설계 이행 중**이라 Trino 경로는 제거 대상이고, 스냅샷은 관측 시점과 함께 읽는다 |
| [docker.md](docker.md) | ✅ | 컨테이너·compose 배포(채택) |
| [spark.md](spark.md) | 🚧 | 배치 엔진 — 대용량 인제스트 + dbt-spark 마트 + 유지보수(이행중). 🔴 **Spark 3.5.9 → 4.1 상향은 「결정 완료 · 이행 전」**(2026-08-23) — 아직 아무것도 굽지 않았으니 러너는 여전히 3.5.9다. 근거는 최신화가 아니라 **지원 범위 복귀**(핀한 Spark Operator chart 1.8.0이 **"drop Spark 3.5"**)이고, 최신인 4.2가 아닌 이유는 Iceberg가 `iceberg-spark-runtime-4.2_2.13`을 **발행하지 않기** 때문이다. 🔴 **같은 날 실측 2건은 별개 축**이다 — `createOrReplace`의 **계보 절단**(고아 스냅샷 7건)과 `create_changelog_view` **의미론**(`UPDATE`는 `append`가 아니라 `overwrite`)은 현행 3.5.9에서 **실행으로 관측**됐다 |
| [flink.md](flink.md) | 🚧 | 스트림 엔진 — 실시간 Sepsis-3 조기경보. 오퍼레이터는 **`scripts/k8s-operators.sh` 기본 설치**(제외하려면 `INSTALL_FLINK=false`). ✅ **Iceberg 배치 왕복 실증**(2026-08-22, 오퍼레이터 1.15.0)에 이어 ✅ **스트리밍 왕복까지 실증**됐다(2026-08-23) — **Spark append → Flink 스트리밍 읽기 → Iceberg 싱크 → Spark 되읽기**를 장수명 잡 하나로 관통했고, 삼중 증거(데이터 시차 12행/3행 · 스냅샷 `engine-name=flink` · 두 스냅샷의 `flink.job-id` 일치)로 닫았다. ⇒ **⏸(스트리밍 미착수) 해제**: 착수 전 선행 3건이 **전건 해소**(체크포인트 설정 RocksDB · `flink-s3-fs-hadoop` 플러그인은 러너 이미지 `0.3.0`에 포함 · 소스 테이블 `poc.sample_flink`로 실제 실행). 체크포인트는 **79회 완료/1회 실패**, SeaweedFS 체크섬 손상 없음(번들 SDK가 **v1**). 🔴 **「Phase 3 완료」가 아니다** — 실증 잡은 컬럼 하나를 붙이는 최소 SQL이고 **실시간 SOFA/Sepsis-3 피처 계산 · Dagster 수명주기 관리 · 배치↔스트림 교차검증**이 남았다. 🔴 `미확인`: **체크포인트로부터의 복구** · 실패 1건 원인 · **싱크 커밋 주기 약 100초**의 이유 · 부적격(overwrite) 소스에서의 동작. 🔴 **순서 함정**: `DELETE_ON_CANCELLATION` 기본값이라 **취소하면 체크포인트가 지워진다**(증거는 취소 전에 수집). 🔴 **스트림 소스는 Redpanda → Iceberg bronze changelog**(2026-08-23 결정·같은 날 이행) — **Redpanda 미도입 유지**는 **기각이 아니라 대체되어 불필요해진 것**이다. 🔴 **스트리밍 TM 상주 피크 `4750m (59%) / 8772Mi (39%)` 최초 실측**(배치 동시 피크 84%/52%보다 낮음) — 다만 §9-3 **경계 ①의 전제는 여전히 깨지며 규약 개정은 결정 대기**([../resource-sizing.md](../resource-sizing.md) §(C-2)). 세션 클러스터는 검증 후 회수 규율에 따라 내려 둔다(회수 후 기준선 복귀 확인) |
| [k8s.md](k8s.md) | 🚧 | 컨테이너 오케스트레이션 — 컴퓨트·데이터 서비스 이전(이행중) |
| [oci.md](oci.md) | 🔎 | 클라우드 이행 — OCI Always Free A1(ARM) + Terraform + k3s(학습·확장 경로) |
| [trino.md](trino.md) | 🔎 | MPP SQL 엔진 — 현행 compose까지 채택, **재설계로 제거**(dbt→dbt-spark) |
| [monitoring.md](monitoring.md) | 🔎 | 모니터링·관측 — Prometheus 선언이 compose에 남아 있고 `--profile monitoring`이면 **수집도 된다**. 그런데 **보는 대상이 정본이 아니라**(스토리지 정본은 K8s로 이전, 그쪽엔 메트릭 포트 없음) 미채택. 현행 관측 실태(healthcheck·probe·메트릭·알림)와 대안 미채택 사유. 규칙 정본은 [conventions/monitoring.md](../conventions/monitoring.md) |

## 각 문서 형식

**개요 / 이 프로젝트에서의 위치(채택 이유·대안 비교) / 운영 메모 / 참고(공식 문서)**.

> 배포·운영 **규칙**은 [conventions/docker.md](../conventions/docker.md)·[conventions/k8s.md](../conventions/k8s.md),
> 자원 **수치**는 [resource-sizing.md](../resource-sizing.md)에서 단일 관리한다.
