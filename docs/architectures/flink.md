# Apache Flink (아키텍처 · 프로젝트 관점)

## 개요

Flink는 **상태 기반 스트림 처리 엔진**이다. 무한 스트림을 이벤트 시간(event-time) 기준으로 낮은
지연에 처리하고, 체크포인트로 **정확히 한 번(exactly-once)** 상태를 보장한다. JobManager가 조율하고
TaskManager가 병렬 처리하며, 배치는 스트림의 특수 경우로 취급한다(통합 API).

- 최신 안정: **Flink 2.3.0**(2026-06). (2.0은 2025-03의 메이저 마일스톤)

## 이 프로젝트에서의 위치 — 🚧 채택·이행중 · ✅ **Iceberg 배치 왕복 실증(2026-08-22)** · ✅ **스트리밍 왕복 실증(2026-08-23)**

> **Flink는 더 이상 배치 전용이 아니다.** 2026-08-22에 오퍼레이터 **1.15.0** + `FlinkDeployment`
> 세션 클러스터로 **Spark ↔ Flink Iceberg 배치 왕복**을 닫았고(아래 §배치 왕복 실증),
> **2026-08-23에 스트리밍 왕복까지 닫았다**(아래 §스트리밍 왕복 실증) — **Spark append(소스) →
> Flink 스트리밍 읽기 → Iceberg 싱크 → Spark 되읽기**를 장수명 잡 하나로 관통했다.
> ⇒ 체크포인트·RocksDB·S3 상태 저장이 **실제로 배포됐고**, 종전 문서가 적던
> *"체크포인트·RocksDB는 하나도 배포되지 않았다"* 는 서술은 **2026-08-23부로 거짓**이다.
> 🔴 **스트림 소스는 Redpanda가 아니라 Iceberg bronze 스트리밍 읽기다**(2026-08-23 결정 → **같은 날 이행** —
> 아래 §스트림 소스를 Redpanda에서 Iceberg changelog로 바꾼 근거). **Redpanda는 미도입 유지**이며,
> 이는 **기각이 아니라 대체되어 불필요해진 것**이다.
>
> **아직 남은 것**(= 이 문서를 "Phase 3 완료"로 읽지 않는 이유): **실시간 SOFA/Sepsis-3 피처 계산**
> (이번 잡은 컬럼 하나를 붙이는 최소 SQL이다) · **Dagster 잡 수명주기 관리** ·
> **배치↔스트림 값 교차검증**. 상세 구분은 [redesign.md](../redesign.md) **Phase 3**.
>
> **검증 후 세션 클러스터는 그 자리에서 내렸다**(2026-08-23 스트리밍 검증분도 동일 — 세션
> 클러스터·Spark Connect **회수 완료**, 회수 후 기준선 `2250m / 3140Mi`로 정확히 복귀).
> 🔴 **스트리밍은 여기에 축이 하나 더 붙는다** — TM이 잡 수명 내내 살아 §9-3 **경계 ①의 전제가
> 깨지므로**, 이번에는 **(나) 시연 창 한정 + 즉시 회수**로 진행했다. 상주분 실측은 나왔고
> (아래 §자원 실측) **2026-08-23 사용자 결정으로 (나)가 확정**됐다 — **경계 ①은 개정하지 않고
> 적용 범위를 명시**하는 쪽이며, 정본은 [conventions/k8s.md](../conventions/k8s.md) §9-3
> §경계 ①의 스트리밍 단서다. 이 문서는 규약을 바꾸지 않는다.
> 🔴 **사유는 시분할이 아니라 회수 규율이다** —
> 2026-08-22 실측(3워크로드 동시 상주 피크 CPU 84% / Mem 52%)으로 규약이 **시분할 금지 → 동시
> 기동 허용**으로 바뀌었고, 경계 ①은 오히려 **Flink JM 상주를 전제로** 동시 기동을 허용한다
> ([conventions/k8s.md](../conventions/k8s.md) §9-3). 그럼에도 내리는 이유는 **잡 없는 세션
> 클러스터가 JM 1 CPU / 2Gi를 놀리기 때문**이고, **예산 여유는 회수를 면제하지 않는다**
> (2026-08-19에 **13시간 샌 전력**이 있고, 발견 경로가 성능 이상이 아니라 "안 쓰는 것 정리"였다는
> 점이 이 규율의 근거다 — [conventions/k8s.md](../conventions/k8s.md) §회수 규율).
> **"중단"과 "삭제"의 분리**(trino 선례) — 자원은 즉시 회수하되 결정·검증 결과·매니페스트·러너
> 이미지는 남긴다. 오퍼레이터 복구는 `./scripts/k8s-operators.sh` 한 줄이다 — **`INSTALL_FLINK`
> 기본값이 `true`라 지정 없이 설치된다**(제외하려면 `INSTALL_FLINK=false`, 스크립트가 정본).
> 🔴 다만 이것으로 돌아오는 것은 **오퍼레이터까지**이고, 잡을 돌리려면 세션 클러스터
> (`FlinkDeployment`)를 따로 세운다(롤백 비용 ≈ 0).

- **채택 방향**: [재설계](../redesign.md)에서 컴퓨트를 **Spark(배치)+Flink(스트림)** 으로 나누며 Flink를 도입한다.
  Trino는 제거한다. 전체 로드맵은 [redesign.md](../redesign.md) Phase 3.
- **역할(Flink의 존재이유)**: MIMIC/eICU는 본래 배치 데이터지만, **Iceberg bronze 테이블의 변경분
  (changelog)을 스트리밍으로 읽어** 스트림을 만들고 **이벤트타임 윈도우로 실시간 SOFA/Sepsis-3
  조기경보**를 계산한다. 배치(Spark/dbt-spark)와 **역할이 겹치지 않는** 스트리밍 유스케이스를 부여해
  "엔진을 위한 엔진"을 피한다([redesign.md](../redesign.md) 급소①).
  🔴 **소스가 Redpanda에서 바뀌었고(2026-08-23 결정) 같은 날 이행됐다** — 근거는 아래
  §스트림 소스를 Redpanda에서 Iceberg changelog로 바꾼 근거, 실행 결과는 §스트리밍 왕복 실증.
  **Redpanda는 미도입 유지**(대체되어 불필요)다. 🔴 **다만 이번에 실증된 것은 "스트리밍 경로가
  흐른다"까지**이고, **실시간 SOFA/Sepsis-3 피처 계산은 아직 없다**(잡 SQL은 컬럼 하나를 붙인다).
- **실행 방식**: **Flink Kubernetes Operator**(Helm)로 **`FlinkDeployment`(CRD)** 를 배포한다.
  JobManager/TaskManager를 선언적으로 관리하고, Dagster(호스트)가 잡 수명주기를 트리거·관측한다.
- **버전(실측 고정)**: 오퍼레이터 **1.15.0** / Flink **2.1.3** / Iceberg **1.11.0**.
  Flink 2.2가 나와 있어도 `iceberg-flink-runtime-2.2`가 **없어서** 2.1로 맞춘다 — 짝이 맞는 조합이 우선이다.
- **Web UI(Spark 대비 장점)**: JobManager가 상주하므로 **UI가 계속 살아 있다**(8081, `<name>-rest` Service).
  Spark는 driver JVM이 끝나면 UI도 사라져 History Server가 필요하지만, Flink 세션 클러스터는 그렇지 않다.
  접근은 `kubectl port-forward svc/<name>-rest 8081:8081`.
- **읽기 검증(2026-08-18)**: 세션 클러스터에서 **Spark가 적재한 Iceberg 테이블을 Flink SQL로 조회**
  (`iceberg.poc.sample` 3행). 두 엔진이 **같은 JDBC 카탈로그 + SeaweedFS**를 공유함을 실증했다(급소② 전제).
  🔴 **이때 검증된 것은 읽기 한 방향뿐이다** — 쓰기는 2026-08-22에 닫혔다(아래).
- **Spark 스트리밍 대비**: Flink=네이티브 스트림(레코드 단위·낮은 지연·풍부한 상태) /
  Spark Structured Streaming=마이크로배치. 저지연·상태 중심이라 Flink를 택한다.

## ✅ Iceberg 배치 왕복 실증 (2026-08-22)

**Spark 적재 → Flink 읽기 → Flink 쓰기 → Spark 읽기**를 한 바퀴 돌려 두 엔진이 같은 카탈로그·
같은 스토리지 위에서 **양방향으로** 상호운용됨을 확인했다.

| 단계 | 수행 주체 | 내용 |
| --- | --- | --- |
| ① 적재 | Spark | `iceberg.poc.sample` (3행) |
| ② 읽기 | Flink SQL | 같은 테이블을 조회 — `spark_rows = 3` |
| ③ 쓰기 | Flink SQL | `INSERT INTO iceberg.poc.sample_flink SELECT …, 'flink-batch' AS src` |
| ④ 되읽기 | Spark Connect | Flink 산출물 3행 확인, `src = 'flink-batch'` |

🔴 **삼중 증거로 닫았다.** "행이 보인다"는 단독으로는 약한 신호라, 서로 위조 관계가 없는 층 셋을 겹쳤다.

| 층 | 증거 |
| --- | --- |
| ⓐ **데이터** | 산출 행의 `src` 컬럼 값이 `flink-batch` |
| ⓑ **테이블 메타데이터** | 스냅샷 summary에 `engine-name: flink` · `iceberg-version: Apache Iceberg 1.11.0` |
| ⓒ **잡 신원** | 스냅샷의 `flink.job-id`가 **Flink 잡 overview의 `jid`와 일치** |

🔴 **같은 카탈로그에 두 엔진 서명이 공존한다** — `poc.sample`은 `1.6.1` / `spark`,
`poc.sample_flink`는 `1.11.0` / `flink`로 기록된다. 즉 커밋 주체가 메타데이터에 남으므로,
다중 writer 환경에서 **"누가 이 스냅샷을 만들었나"를 사후에 물을 수 있다**(급소② 논의의 실측 근거).

### 배치 모드라서 성립한 것 (범위를 좁혀 읽어라)

`SET 'execution.runtime-mode' = 'batch'`로 돌렸다. 🔴 **배치에서 Iceberg 싱크는 잡 완료 시점에
커밋**하므로 **체크포인트가 필요 없었고**, 그래서 `flink-s3-fs-hadoop` 플러그인을 이번에 넣지 않고도
성립했다. 스트리밍은 **체크포인트 단위로 커밋**한다.

📌 **"체크포인트가 필요 없다"가 아니라 "이 잡에는 필요 없다"** 로 읽어야 한다.
스트리밍으로 넘어가는 순간 **체크포인트 설정 + `flink-s3-fs-hadoop` 플러그인 + 러너 이미지
재빌드가 동시에** 필요해진다. 이번 성공을 "S3 플러그인 없이 된다"로 일반화하면 Phase 3 착수
시점에 같은 조사를 반복하게 된다.

### 자원 프로파일 — TaskManager는 온디맨드다 (🔴 **배치 잡 한정**)

| 구성요소 | 수명 | 비용 |
| --- | --- | --- |
| JobManager | 세션 클러스터와 함께 상주 | **1000m / 2048Mi** — 유휴 비용의 전부 |
| TaskManager | 잡 제출 **+7초**에 기동, **46~52초** 생존, 잡 종료 시 자동 회수 | 잡이 없으면 0 |

⇒ 세션 클러스터를 띄워 둘 때 실제로 새는 것은 **JM 하나**다. 그래서 회수 판단이 단순하다
(잡을 안 돌릴 거면 내린다).

🔴 **이 표를 스트리밍에 일반화하지 마라 — 2026-08-23에 반례가 나왔다.**
**스트리밍 잡의 TM은 잡 수명 내내 산다**(상주 피크 `4750m (59%)` / `8772Mi (39%)`, 아래
§자원 실측). "TM은 온디맨드"는 **배치 잡에서 관측된 성질**이지 Flink의 성질이 아니다.

### 구현 방식 — `sql-client.sh -f` + ConfigMap

잡은 **ConfigMap에 담은 SQL을 `sql-client.sh -f`로 실행**하는 형태로 만들었다
(`k8s/flink/iceberg-batch-job.yaml`). **`FlinkSessionJob` CR은 쓰지 않았다** — `jarURI`가 필수라
SQL 한 장을 돌리자고 jar 빌드·배포 파이프라인을 세워야 해서 과대했다.

- **`allowed-schemes`를 `local;https` → `local`로 좁혔다.** 런타임에 외부에서 jar를 받아오는
  경로를 끊는 편이 공급망상 안전하고, 필요한 의존성은 **이미지에 굽는다**.

## ✅ 스트리밍 왕복 실증 (2026-08-23)

**Spark append(소스) → Flink 스트리밍 읽기 → Iceberg 싱크 → Spark 되읽기**를 **장수명 잡 하나**로
관통했다. 2026-08-22 배치 왕복과 **같은 삼중 증거 방식**이고, 이번 축은 **스트리밍**이다.

> 관측 시각 **2026-08-23 17:1x~17:37 KST**(잡 실행 창). 처리 시각(`ingested_at`)은 **UTC 저장**,
> 스냅샷 타임스탬프는 **KST 표시**다([conventions/timezone.md](../conventions/timezone.md)).

### 준비 — 러너 이미지 `0.3.0` · apply 순서

| 항목 | 실측 |
| --- | --- |
| 러너 이미지 | **`flink-runner:0.2.0` → `0.3.0`** 빌드·push 완료(레지스트리 태그 `0.1.0`·`0.2.0`·`0.3.0`) |
| S3 파일시스템 플러그인 | `flink-s3-fs-hadoop-2.1.3.jar` **31,696,370 B** 가 `/opt/flink/plugins/s3-fs-hadoop/`에 실재 — **이미지와 파드 양쪽에서 확인** |
| 네트워크 다운로드 | **0건** — 베이스 이미지 `/opt/flink/opt/`에서 복사한다(`Dockerfile.flink-runner`) |
| apply 순서 | **ConfigMap 2종 → FlinkDeployment**(역순이면 JM이 `CreateContainerConfigError`) |
| JM 기동 | `DEPLOYING → DEPLOYED_NOT_READY → READY` **24초** |
| SQL 마운트 | `/opt/flink/sql`(배치) · `/opt/flink/sql-stream`(스트림) **2개 확인** — 같은 경로에 겹치면 뒤엣것이 앞엣것을 가린다 |

### 🔴 삼중 증거 — 잡 `5c748c8cc55f4e9ef82b51a19a2972a3`

잡 이름 `insert-into_iceberg.poc.sample_stream`, state **RUNNING**.

| 층 | 증거 | 실측 |
| --- | --- | --- |
| ⓐ **데이터** | `poc.sample_stream`에 `src`별로 두 묶음이 **시차를 두고** 들어왔다 | `flink-batch` **12행**(`08:22:39.507~.579 UTC`) / `spark-incr` **3행**(`08:29:17.501~.509 UTC`) — **약 6분 39초 간격** |
| ⓑ **스냅샷 메타데이터** | 싱크 스냅샷 2건의 summary | 둘 다 `engine-name=flink` · `iceberg-version=Apache Iceberg 1.11.0 (commit 6976e020…)`, `added-records` **12** / **3** |
| ⓒ **잡 동일성** | 두 스냅샷의 `flink.job-id` | 모두 `5c748c8cc55f4e9ef82b51a19a2972a3` — **제출한 잡 ID와 일치** |

🔴 **ⓐ가 이번 실증의 핵심이다.** 12행은 `TABLE_SCAN_THEN_INCREMENTAL`의 초기 스캔분이고, `spark-incr`
3행은 **잡이 뜬 뒤 Spark가 소스에 append한 것**이다. 즉 잡이 **살아 있는 채로 나중에 도착한 데이터를
처리**했음을 **로그가 아니라 테이블로** 판별했다. ⓒ가 두 커밋이 **같은 장수명 잡**의 것임을 못 박는다.

### 체크포인트 (S3 = SeaweedFS) — 배포됨

| 항목 | 실측 |
| --- | --- |
| 완료 / 실패 | **79회 완료 / 1회 실패** |
| 최신 | `chk-79`, `external_path = s3://warehouse/flink-checkpoints/<jobid>/chk-79` |
| 크기 · 지연 | state_size **1,025 B**, end-to-end **45 ms** |
| `_metadata` | **2,481 B로 온전**(0바이트 아님) |

🔴 **SeaweedFS 체크섬 손상은 없었다.** 근거는 번들 SDK가 **v1**이라는 점 하나다 — 이미지 실측으로
`com/amazonaws/*` **3,887개** / `software/amazon/awssdk/*` **0개**이고, SeaweedFS의 aws-chunked 결함은
**SDK v2 기본 동작**이라 이 경로에 해당하지 않는다([spark.md](spark.md) §SeaweedFS 체크섬 결함).
⇒ 매니페스트 주석의 *"실행으로 확인해야 한다"* 가 **이번에 확인됐다.**

🔴 **다만 "체크포인트에서 복구된다"는 검증하지 않았다 — `미확인`이다.**
**파일이 온전해 보이는 것**과 **복구 가능한 것**은 다른 축이고, 복구는 잡을 죽였다 되살려야 닫힌다.
**실패 1건도 `미확인`이다** — 첫 회로 추정하나 확인하지 않았다(추정을 결론으로 쓰지 않는다).

#### 🔴 순서 함정 — 취소하면 체크포인트가 지워진다

`externalized-checkpoint-retention` 기본값이 **`DELETE_ON_CANCELLATION`** 이라
`flink cancel` 직후 `flink-checkpoints/`가 **0.0 KiB(디렉터리 마커 2개만)** 로 떨어졌다.

⇒ **증거 수집은 반드시 취소 *전에* 한다.** 잡을 내린 뒤 체크포인트를 보러 가면 **"애초에 없었던 것"과
구분이 안 되는 상태**를 보게 된다. 회수 규율(그 자리에서 내린다)과 증거 규율(내리기 전에 뜬다)이
**충돌하는 지점**이므로 순서를 못 박아 둔다.

### 자원 실측 — 스트리밍 TM 상주 (경계 ①의 최초 실측)

> **모집단**: `kubectl describe node`의 Allocated resources(= Σ**requests**). **분모는 노드 Allocatable**
> `8000m` / `22843508Ki`(≈22308Mi). 배분 정본은 [resource-sizing.md](../resource-sizing.md) §(C-2).

| 상태 | CPU | Mem |
| --- | --- | --- |
| 회수 후 기준선 | 2250m (28%) | 3140Mi (14%) |
| 🔴 **스트리밍 상주 피크**(JM + TM + Spark Connect) | **4750m (59%)** | **8772Mi (39%)** |
| (참고) 2026-08-22 **배치 동시 피크** | 6750m (84%) | 11638Mi (52%) |

워크로드별 requests 실측: JM `1 / 2Gi` · TM `1 / 2Gi` · spark-connect `500m / 1536Mi` ·
seaweedfs `300m / 768Mi` · catalog-postgres `250m / 512Mi`.

- ⇒ **스트리밍 상주가 배치 동시 피크보다 낮다**(59% < 84%). 예산 축만 보면 여유가 있다.
- 🔴 **그럼에도 경계 ①의 전제는 여전히 깨진다** — 경계 ①은 *"TM은 온디맨드·수명 1분 미만"* 을
  동시 기동 허용의 근거로 삼는데, **스트리밍 TM은 잡 수명 내내 산다.** 즉 **"낮다"와 "전제가 성립한다"는
  다른 축**이고, 수치가 통과한다고 규약이 자동으로 맞아떨어지지는 않는다.
- ✅ **(나)로 확정(2026-08-23 사용자 결정)** — **시연 창 한정 + 즉시 회수**이며 **경계 ①은 개정하지 않는다.**
  2026-08-23 실증도 (나)로 진행했고 **회수 후 기준선으로 정확히 복귀**했다.
  🔴 **확정 사유는 예산이 아니라 회수 규율이다** — 예산은 남지만(59% < 84%) *"예산이 남는다"* 가
  *"상주시켜도 된다"* 는 아니며, (가)를 택하면 *"TM이 떠 있으면 잡이 도는 중"* 이라는
  **`kubectl get pods` 한 번으로 되는 이분법을 잃는다**(회색 지대의 판정에는 누적 시간 계측이
  필요한데 그 계측기가 없다). 결정 전문·운영 규칙·재검토 트리거는 정본
  [conventions/k8s.md](../conventions/k8s.md) §9-3 §경계 ①의 스트리밍 단서에 있으며,
  **이 문서가 규약을 바꾸지 않는다**는 점은 그대로다.

### 🔴 이번에 낸 오판 1건 — "증분이 흐르지 않는다"는 틀린 판정이었다

증분 검증에서 **120초 동안 싱크가 12행 그대로**여서 *"증분이 흐르지 않는다"* 고 **일단 판정했다.
그 판정은 틀렸다.** 원인이 둘 겹쳐 있었다.

| # | 원인 | 확인 |
| --- | --- | --- |
| ① | **Spark Connect 세션이 테이블 메타데이터를 캐시**해 새 스냅샷을 보지 못했다 | `REFRESH TABLE` 후 **15행**으로 보였다 |
| ② | 싱크 **커밋 간격이 체크포인트 간격(10s)이 아니라 약 100초**였다 | 싱크 스냅샷 타임스탬프 `17:24:28 · 17:26:08 · 17:27:48 · 17:29:18 · 17:30:58 · 17:32:38`(KST). 실제 `added=3` 커밋은 **`17:29:18`** — `ingested_at 08:29:17 UTC`와 같은 시각이다 |

- **교훈 ⓐ — 조회 세션의 캐시가 "데이터가 없다"로 보인다.** 부정 결과(*"안 들어왔다"*)는
  **조회 경로가 신선한지 함께 확인해야** 유효하다([philosophy.md](../philosophy.md) 원칙 7).
  여기서 관측 경로를 의심하지 않았다면 **멀쩡히 도는 잡을 실패로 기록**할 뻔했다.
- **교훈 ⓑ — 체크포인트 주기와 싱크 커밋 주기는 다른 축이다.** *"10초마다 커밋된다"* 로 읽으면
  관측 창을 너무 좁게 잡는다. 매니페스트 주석의 *"최소 10초 이상 기다린 뒤 조회"* 도 **하한이지
  기대값이 아니다.**
- 🔴 **왜 약 100초인지는 `미확인`이다.** 값은 관측했으나 원인(소스 폴링·커밋 병합·유휴 처리 등)은
  규명하지 않았다 — **추정을 적지 않는다.**

### 남은 증거·정리 상태

- `poc.sample_stream`(**15행**) · `poc.sample_flink`(**15행** — `spark-incr` 3행 추가됨)는 **증거로 보존**했다.
- 세션 클러스터·Spark Connect는 **회수 완료**(파드 2개만 잔류).
- 게이트: `pre-commit run --files <k8s 3개 + 스크립트>` → **실행 11개 Passed / 8개 Skipped**.

## 운영 메모 — 🟢 **스트리밍 계층은 배포됐다**(일부 항목은 여전히 목표)

> 🔴 **2026-08-23부로 이 절의 대부분이 현행이 됐다.** 종전 판이 적던 *"체크포인트·RocksDB는 하나도
> 배포되지 않았다"* 는 **더 이상 사실이 아니다**(위 §스트리밍 왕복 실증). 아래는 항목별로
> **🟢 현행(실측)** 과 **🎯 목표(Phase 3 잔여)** 를 갈라 적는다 — 설계안과 실측을 같은 문단에 두면
> 섞인다([philosophy.md](../philosophy.md) 원칙 7).

- 🟢 **소스**: **Iceberg bronze 테이블 스트리밍 읽기**(2026-08-23 결정 → **같은 날 이행** · 아래 §근거).
  실증 잡은 `poc.sample_flink`를 `streaming=true` · `monitor-interval=5s` ·
  `starting-strategy=TABLE_SCAN_THEN_INCREMENTAL`로 읽었다. **Redpanda는 미도입 유지**(불필요).
  **싱크**: Iceberg(Spark와 동일 JDBC 카탈로그 공유, 낙관적 동시성) — `poc.sample_stream`에 커밋 확인.
- 🟢 **체크포인트**: S3 호환 **SeaweedFS 재사용**(path-style 강제), 상태 백엔드 **RocksDB** —
  `execution.checkpointing.{interval,mode,dir,storage}` + `state.backend.type=rocksdb`로 **배포됨**
  (`k8s/flink/flinkdeployment-session.yaml`). **79회 완료 / 1회 실패**(실패 1건 `미확인`),
  `flink-s3-fs-hadoop` 플러그인은 러너 이미지 **`0.3.0`에 포함**됐다.
  🔴 이 항목이 면제될 수 없었던 이유가 실행으로 확인됐다 — Iceberg Flink 싱크는
  **`notifyCheckpointComplete`에서 커밋**하므로 스트리밍에는 체크포인트가 **필수**다.
  🔴 **`미확인`: 체크포인트로부터의 복구**(파일 온전성과 다른 축) · **커밋 주기 약 100초의 원인**.
- **자원·동시 기동**: JM+TM는 **배치(Spark)와 동시 실행이 허용**된다(2026-08-22 실측 피크
  CPU 84% / Mem 52%, 분모는 노드 Allocatable `8000m`/`22843508Ki`). 단 경계가 셋이다 —
  **Flink 상주는 JM만**(TM은 잡 제출 시 온디맨드·수명 46~52초) · **`spark.executor.instances` ≤ 1** ·
  **Redpanda 미도입**(도입 시 경계 재계산). 정본 [conventions/k8s.md](../conventions/k8s.md) §9-3,
  배분은 [resource-sizing.md](../resource-sizing.md) "Kubernetes 재설계 시나리오".
  🔴 **경계 ①의 전제는 스트리밍에서 깨진다**(TM 상주). 상주 피크 **`4750m (59%)` / `8772Mi (39%)`** 가
  2026-08-23에 **처음 측정**됐고(위 §자원 실측), 같은 날 **(나) 시연 창 한정 + 즉시 회수로 확정**됐다 —
  **경계 ①은 개정하지 않고 적용 범위를 명시**한다(정본 §9-3 §경계 ①의 스트리밍 단서).
  ⇒ **스트리밍 상시 운전은 하지 않으며**, 잡 제출 **전에** 회수 시점을 정한다.
- 🎯 **목표(Phase 3 잔여)**: **실시간 SOFA/Sepsis-3 피처 계산**(이벤트타임 윈도우) ·
  **Dagster의 잡 수명주기 관리** · **배치(dbt-spark)↔스트림 값 교차검증**.
  이번 실증 잡은 **경로를 여는 최소 SQL**(`ingested_at` 컬럼 부가)이지 피처 계산이 아니다.
- **카탈로그 정합**: Spark·Flink 동시 writer 구조라 장기적으로 REST 카탈로그 이행 유인이 크다([redesign.md](../redesign.md) 급소②).

## 스트림 소스를 Redpanda에서 Iceberg changelog로 바꾼 근거 (2026-08-23) — ✅ **결정 완료 · 같은 날 이행됨**

> 🔴 **이 절은 결정의 기록이다.** 근거는 전부 **1차 출처(Iceberg·Flink 소스코드·공식 문서) 대조**이며
> 관측 시각은 **2026-08-23**이다. **결정 당시에는 아무것도 클러스터에서 돌려보지 않았고**,
> 같은 날 **위 §스트리밍 왕복 실증**으로 이행됐다 — **결정의 근거와 실행의 결과를 섞어 읽지 않는다.**

**전제부터 교정됐다.** 출발 질문은 *"Flink CDC 구성을 어떻게 보는가"* 였으나, 되물어 확인한 결과
뜻한 것은 **DB CDC가 아니라 Iceberg 변경분(changelog)** 이었다. 이 교정만으로
**브로커도 Debezium도 필요 없어졌다.**

### 판정 — ★★★★★ 먼저 한다

| 근거 | 내용 |
| --- | --- |
| ⓐ **신규 상주 인프라 0** | 소스가 이미 있는 Iceberg 테이블이므로 Redpanda·소스 DB·Debezium이 전부 불필요하다. ⇒ [../conventions/k8s.md](../conventions/k8s.md) §9-3 **경계 ③(Redpanda 도입 시 예산 재계산)이 발동하지 않는다.** |
| ⓑ **기존 계획이 자기모순이었다** | 종전 Phase 3의 "`vitalsign` 리플레이"는 **dbt 실버 모델을 인위적으로 되돌려 스트림처럼 흘리는** 구성이었다. 이미 계산된 결과를 원재료인 척 되먹이는 것이라 스트리밍의 존재이유를 오히려 약화시킨다. |
| ⓒ **Flink CDC(DB CDC)는 버전이 안 맞는다** | Flink CDC **3.6.0**이 지원하는 Flink는 **1.20.x · 2.2.x** 다. 이 저장소 현행은 **2.1**이라 애초에 짝이 아니다(버전은 *Iceberg가 지원하는 짝*으로 고정 — 위 §버전). |

- Spark 쪽 배치 변경분 조회는 **Iceberg 프로시저 `create_changelog_view`** 를 쓴다
  ([spark.md](spark.md) §changelog는 4.1이 주지 않는다). 🔴 **Spark 4.2의 `CHANGES`(`SPARK-55948`)에
  기대지 않는다** — 그것은 Spark 자체 DSv2 CDC API이고 **4.1에는 없다.**

### 🔴 급소 — Flink 스트리밍 읽기는 **append 스냅샷만** 본다

이 절에서 가장 비싼 제약이다. 이걸 놓치면 소스 테이블을 잘못 고르고, **틀린 게 아니라 조용히
빠진 결과**를 얻는다.

- Iceberg Flink 스트리밍 소스는 **`IncrementalAppendScan` 기반**이다. 즉 **append 스냅샷만** 읽고
  **overwrite·delete 스냅샷은 지원하지 않는다**(`apache/iceberg` #1949).
- ⇒ 🔴 **`merge into`를 쓰는 dbt 실버 테이블은 스트림 소스로 쓸 수 없다.**
  **소스는 bronze append 테이블로 고정**한다.
- ⚠️ 이 제약은 **읽기 축**이다. 쓰기 축에는 별도 제약이 붙는다 — Iceberg Flink **upsert 싱크는
  format v2 + primary key가 필수**이고 **`OVERWRITE`와 상호배타**다.

#### ✅ 실증 — `UPDATE`가 `overwrite` 스냅샷을 남긴다 (2026-08-23 실측)

> 🔴 **이 소절만 실측이다.** 이 절의 나머지(스트림 소스 전환)는 여전히 **결정 단계**이고,
> 아래는 그 결정의 **급소가 실제로 물리는지**를 Spark 쪽에서 확인한 결과다.
> 수단은 `scripts/iceberg_changelog_probe.py`(엔진 **Spark 3.5.9 / Iceberg 1.11.0**),
> 게이트 규약은 [../test.md](../test.md) §5-3, 의미론 전문은 [spark.md](spark.md)
> §`create_changelog_view` 의미론이다.

프로브 테이블에 append 2회 + `UPDATE` 1행을 만들자 스냅샷 ops가 **`append, append, overwrite`** 로
남았다. ⇒ 🔴 **한 행을 고치는 것만으로 그 테이블은 스트림 소스 자격을 잃는다.**
`merge into`를 쓰는 dbt 실버 테이블뿐 아니라 **평범한 `UPDATE` 한 번도 같은 결과**를 만든다.

##### 소스 적격성 진단표 (2026-08-23 · 카탈로그 실측)

| 테이블 | 행수 | 계보 | ops | `spark_changelog` | `flink_stream` |
| --- | --- | --- | --- | --- | --- |
| `poc.sample` | 3 | 1 (고아 7) | `overwrite` | 가능 | **불가** |
| `poc.sample_flink` | 12 | 4 | `append` | 가능 | **가능** |

🔴 **이 표는 진단 시점의 스냅샷이다** — 같은 날 늦게 스트리밍 실증에서 `spark-incr` 3행을 append해
`poc.sample_flink`는 **15행**이 됐고, 싱크 테이블 `poc.sample_stream`(**15행**)이 새로 생겼다
(위 §스트리밍 왕복 실증). **행수는 관측 시각과 함께 읽는다.**

- ⇒ 🔴 **진단 시점 카탈로그에서 Flink 스트림 소스로 쓸 수 있는 테이블은 `poc.sample_flink` 하나뿐이었다.**
  `eicu`·`mimiciv` 네임스페이스는 **테이블 0개**라 후보가 아예 없다(원천 미확보).
- `poc.sample`이 `불가`인 이유는 두 겹이다 — **ops가 `overwrite`**이고, 게다가
  `createOrReplace`가 **계보를 끊어 고아 스냅샷 7건**을 남겼다
  ([spark.md](spark.md) §`createOrReplace`는 계보를 끊는다).

##### 🔴 확인된 축과 확인되지 않은 축을 가른다

| 축 | 상태 |
| --- | --- |
| Spark `create_changelog_view`가 overwrite 테이블에서도 뷰를 만든다 | ✅ **2026-08-23 실측** |
| 소스 테이블의 **적격성**(ops·계보) 판정 | ✅ **2026-08-23 실측** |
| **Flink 스트리밍 읽기 자체** | ✅ **2026-08-23 실측**(같은 날 늦게 — 위 §스트리밍 왕복 실증) |
| **`append`가 아닌 스냅샷을 실제로 건너뛰는가** | 🔴 **`미검증`** — 실증은 append-only 소스(`poc.sample_flink`)로만 돌렸다 |

📌 **"append만 본다"는 여전히 문서화된 제약이지 관측한 사실이 아니다.** 이번에 닫힌 것은
**적격 소스에서 스트리밍 읽기가 흐른다**까지이고, **부적격 소스에서 무엇이 어떻게 빠지는지**는
돌려보지 않았다 — 그 축은 **overwrite 스냅샷이 섞인 테이블로 잡을 돌려야** 닫힌다.
🔴 이 구분이 중요한 이유는 실패 모드가 **에러가 아니라 조용한 누락**이기 때문이다.

#### 🔴 반증된 것 — "overwrite 스냅샷을 스킵하는 옵션"은 없다

조사 중 *"`streaming-skip-overwrite-snapshots` 옵션으로 스킵할 수 있다"* 는 서술이 나왔다.
**거짓이다.**

| 대조한 것 | 결과 |
| --- | --- |
| `FlinkReadOptions.java`(main) 전문 | 해당 상수 **없음** |
| 공식 `flink-configuration.md`의 read option 목록(**22개**) | 해당 항목 **없음** |

📌 **C등급 검색 요약이 소스코드 대조로 뒤집힌 두 번째 사례**다(첫 번째는 [spark.md](spark.md)
§Iceberg 1.6.1 → 1.11.0 상향 근거의 `Deprecated` vs `drop`). **C·D 등급만으로 단정하지 않는다** —
특히 *"옵션 하나로 제약을 우회할 수 있다"* 는 형태의 요약은 검증 비용보다 유혹이 크다.

### 착수 전 선행 3건 — ✅ **전건 해소 (2026-08-23) · 남은 선행 0**

| # | 선행 항목 | 상태 |
| --- | --- | --- |
| ⓐ | **체크포인트 스토리지** — `flink-s3-fs-hadoop` 플러그인 | ✅ **해소(2026-08-23)** — 러너 이미지 **`0.3.0`** 의 `/opt/flink/plugins/s3-fs-hadoop/`에 `flink-s3-fs-hadoop-2.1.3.jar`(**31,696,370 B**) 실재. **이미지·파드 양쪽 확인**, 네트워크 다운로드 0 |
| ⓑ | **체크포인트 설정**(간격·상태 백엔드 RocksDB·경로) | ✅ **해소(2026-08-23)** — `flinkdeployment-session.yaml`에 `execution.checkpointing.{interval=10s,mode=EXACTLY_ONCE,dir,storage=filesystem}` + `state.backend.type=rocksdb`. **79회 완료 / 1회 실패**(실패 1건 `미확인`) |
| ⓒ | **소스 테이블 선정** — append-only 제약 충족 | ✅ **해소(2026-08-23)** — 후보 **`poc.sample_flink`** 로 실제 잡을 돌렸다(위 §스트리밍 왕복 실증). `poc.sample`은 `overwrite`+고아 계보라 탈락, `eicu`·`mimiciv`는 테이블 0개 |

🔴 **ⓑ는 "안 하면 느려진다"가 아니라 "안 하면 결과가 안 나온다"** 였다. 배치 왕복이
체크포인트 없이 성립한 것을 일반화했다면 여기서 막혔을 것이다(위 §배치 모드라서 성립한 것).

🔴 **"남은 선행 0"을 "Phase 3 완료"로 읽지 않는다.** 닫힌 것은 **착수 조건**이고, Phase 3의
목적물(실시간 SOFA/Sepsis-3 피처·Dagster 수명주기·배치↔스트림 교차검증)은 그대로 남아 있다.

🔴 **ⓒ가 "미정"에서 "후보 1건"으로 바뀐 것을 "해결됐다"로 읽지 않는다.** 후보가 하나뿐이라는 것은
**3행짜리 PoC 테이블 말고는 아무것도 없다**는 뜻이고, Phase 2에서 bronze를 Spark로 옮길 때
쓰기 모드를 `createOrReplace`로 두면 **새로 만든 bronze도 후보가 되지 못한다**
([spark.md](spark.md) §Phase 2 함의 — 쓰기 모드 수정 여부는 `미결`).

### 경계 ①의 전제 — 측정 완료 · ✅ **(나)로 확정 (2026-08-23 사용자 결정)**

[../conventions/k8s.md](../conventions/k8s.md) §9-3 **경계 ①** 은
*"Flink 상주는 JobManager뿐 · TM은 잡 제출 시 온디맨드로 뜨고 수명 46~52초 뒤 자동 회수"* 를
**동시 기동 허용의 전제**로 삼는다. **스트리밍 잡은 TM이 상시 생존하므로 이 전제가 깨진다.**

| 선택지 | 내용 | 비용 | 판정 |
| --- | --- | --- | --- |
| (가) | 경계 ① 자체를 개정한다(TM 상주를 예산에 반영) | 예산 재계산 + 규약 정본 개정 | ❌ 미채택 |
| (나) | **시연 창 안에서만 돌리고 그 자리에서 회수한다** | 규약 무개정. 회수 규율에 그대로 얹힌다 | ✅ **채택(2026-08-23)** |

- ✅ **(나)로 확정됐다(2026-08-23 사용자 결정)** — 경계 ①을 건드리지 않고, 이미 있는 회수 규율
  (검증이 끝나는 그 자리에서 내린다)에 스트리밍 잡을 얹는다. **2026-08-23 실증도 (나)로 진행**했고
  회수 후 기준선으로 정확히 복귀했다. ⇒ 경계 ①은 **폐기·개정된 것이 아니라 적용 범위가 명시된 것**이다.
- 🔴 **수치는 채워졌고, 그 수치가 결정 사유는 아니다.** 종전 이 절의 공백(`미측정`)이 채워져
  스트리밍 상주 피크가 **`4750m (59%)` / `8772Mi (39%)`** 로 관측됐고(위 §자원 실측)
  **배치 동시 피크(84% / 52%)보다 낮다** — 즉 **예산은 남는다.** 그럼에도 (나)인 이유는 **회수 규율**이며,
  **2026-08-19에 잡 없는 세션 클러스터가 13시간 샌 전례**(발견 경로가 성능 이상이 아니라
  **"안 쓰는 것 정리"**)가 근거다. **예산이 남으면 새는 것을 아무도 눈치채지 못한다.**
- 🔴 **(가)를 택하지 않은 이유는 판정 가능성이다.** TM 상주를 허용하면 *"TM이 떠 있으면 잡이 도는 중"* 이라는
  **`kubectl get pods` 한 번으로 되는 이분법을 잃고**, *"얼마나 오래 상주해도 되는가"* 라는 회색 지대가
  생긴다 — 그 판정에는 **누적 시간 계측이 필요한데 지금 그 계측기가 없다.**
- **재검토 트리거**: 스트리밍을 시연이 아니라 **상시**로 돌려야 할 때 — **실시간 SOFA/Sepsis-3 조기경보를
  데모가 아니라 상시 운영**으로 올릴 때([../redesign.md](../redesign.md)). 그때
  [../resource-sizing.md](../resource-sizing.md) §(B)·§(C-2)를 **TM 상주 기준으로 재실측**하고 다시 판정한다.
- 🔴 **그래도 이 절은 규약 정본을 바꾸지 않는다.** **"예산이 통과한다"와 "경계 ①의 전제가 성립한다"는
  다른 축**이고, 결정의 정본 문안·운영 규칙은
  [../conventions/k8s.md](../conventions/k8s.md) §9-3 §경계 ①의 스트리밍 단서에 있다.
  **스트리밍 상시 운전은 하지 않는다.** 자원 축의 서술은
  [../resource-sizing.md](../resource-sizing.md) §(C)·§(C-2).

### Flink CDC(DB CDC)는 ★☆☆☆☆ — 비권장

혼동을 막기 위해 **기각 사유를 남긴다**(다음에 같은 질문이 다시 나온다).

- **캡처 대상 OLTP가 없다** — 이 저장소의 원천은 정적 csv.gz이고, 변경을 만들어내는 운영 DB가 없다.
- **버전 부적합** — Flink CDC 3.6.0은 Flink **1.20.x · 2.2.x** 지원, 현행은 **2.1**.
- **예산** — 브로커(Redpanda) 도입이 따라붙어 §9-3 경계 ③(재계산)이 발동한다.
- **거버넌스** — 캡처 대상이 생기더라도 진료 데이터의 행 단위 스트림을 늘리는 방향이라
  [../security.md](../security.md)의 반출·보관 통제를 먼저 통과해야 한다.

## ⚠️ 드리프트 교정 — cert-manager는 제거되지 않았다

2026-08-19 문서는 Flink 스택을 내리면서 **"cert-manager도 함께 제거했다"** 고 적었다.
**이 서술은 거짓이다** — cert-manager는 **줄곧 `Running`이었다**.

- **왜 그렇게 적혔나**: cert-manager를 Flink Operator의 webhook 의존으로만 인식했고,
  Flink를 내리는 커맨드 묶음에 넣었으니 지워졌으리라고 **확인 없이 기록**했다.
- **실제**: **CNPG(CloudNativePG)의 barman 플러그인이 cert-manager를 무조건 요구**한다.
  즉 카탈로그 Postgres가 살아 있는 한 cert-manager는 내려갈 수 없다.
- **교훈**: "함께 제거했다"는 **실행한 명령의 기록**이지 **관측된 상태**가 아니었다.
  제거를 적을 때는 `kubectl get`으로 **부재를 확인**한 뒤 적는다(부정 결과는 관측 경로가
  살아 있었음을 함께 확인해야 유효하다 — [philosophy.md](../philosophy.md) 원칙 7).

## 참고

- Flink 문서(stable): https://flink.apache.org/documentation/flink-stable/
- 다운로드/릴리스: https://flink.apache.org/downloads/
- Flink Kubernetes Operator: https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-main/
- Flink + Iceberg connector: https://iceberg.apache.org/docs/latest/flink/
- Iceberg Flink 읽기(스트리밍·`IncrementalAppendScan`): https://iceberg.apache.org/docs/latest/flink-queries/
- Iceberg Flink 설정(read option 목록): https://iceberg.apache.org/docs/latest/flink-configuration/
- Flink CDC(🔎 **비권장 · 기각** — 3.6.0 지원 Flink 1.20.x·2.2.x, 현행 2.1): https://nightlies.apache.org/flink/flink-cdc-docs-stable/
- Redpanda(🔎 **미도입 유지** — 스트림 소스가 Iceberg로 바뀌어 불필요): https://docs.redpanda.com/
