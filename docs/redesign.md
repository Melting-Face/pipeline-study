# 재설계 로드맵 — Kubernetes 단일 플랫폼(Dagster·Spark·Flink)

> **상태**: 🚧 **채택·이행중(PoC 게이트)**. 방향은 확정, 전면 이행은 **Phase 0 PoC 성공을 전제**로 단계적으로 진행한다.
> **동기**: 단일 호스트 compose의 확장성/성능 한계 극복 + **학습·포트폴리오**(K8s Operator·Spark-on-K8s 실전 패턴 시연).
> **연관**: 아키텍처 [architectures/k8s.md](architectures/k8s.md)·[architectures/spark.md](architectures/spark.md),
> 규칙 [conventions/k8s.md](conventions/k8s.md)·[conventions/docker.md](conventions/docker.md),
> 자원 수치 [resource-sizing.md](resource-sizing.md), 현행 스택 [architectures/overview.md](architectures/overview.md).

## 1. 배경과 목표

- **현행**: 단일 호스트 **Docker Compose**(dagster webserver/daemon·postgres·trino·seaweedfs·prometheus).
  Dagster가 in-process/subprocess로 실행하고, dbt-on-Trino가 모든 변환을 담당한다([overview.md](architectures/overview.md)).
- **한계**: 단일 노드 자원 상한(Trino 메모리 제약·[resource-sizing.md](resource-sizing.md)), 스케일아웃 경로 부재.
- **목표 지향점**: **학습/포트폴리오**. 실제 프로덕션 패턴인 **오케스트레이터(컨트롤 플레인) ↔ 원격 컴퓨트 분리**를
  로컬에서 재현한다. **분리는 프로세스 경계이지 머신 경계가 아니다** — Dagster도 같은 클러스터의
  워크로드로 두되(Deployment ×2), 컴퓨트는 **오퍼레이터가 띄우는 별도 파드**로 옮긴다.
- **로드맵의 종착지는 인프라가 아니라 분석이다.** Phase 0~4는 수단을 세우고, **Phase 5에서 gold 마트·
  리포트로 닫힌다**(§4). 인프라가 "도는 것"은 완료 조건이 아니다.

## 2. 목표 아키텍처 (토폴로지)

```
┌─────────────────── 로컬 K8s (kind on Podman) ────────────────────┐
│  Dagster webserver + daemon (Deployment ×2) — 컨트롤 플레인        │
│    • 배치(Spark) : SparkApplication(CRD) 제출·폴링                 │  [BATCH]
│    • 배치(Flink) : FlinkDeployment 기동 → sql-client exec → 회수   │  [BATCH]
│    • 스트림(Flink): 잡 수명주기 관리 — 아직 목표(미착수)           │  [STREAM]
│    • dbt CLI(dbt-spark) → Spark Connect 대상 실행 (※ 아래 주 참조) │
│  ── 이하 컴퓨트·데이터(같은 클러스터) ──                           │
│  Spark Operator (Helm) → SparkApplication → driver/executor       │  [BATCH]
│  Flink Operator (Helm) → FlinkDeployment → JobManager/TaskManager │  [BATCH·STREAM]
│  Iceberg bronze 테이블 ← 스트림 소스(changelog 읽기)              │  [STREAM]
│  SeaweedFS  (StatefulSet+PVC) ← S3(path-style)·IB 웨어하우스·체크포인트│
│  CloudNativePG (Helm) → Cluster(CRD) → Postgres(+PVC)             │
│      ← Iceberg JDBC 카탈로그(iceberg DB) · Dagster 메타(dagster DB) │
│  로컬 레지스트리 (kind local-registry)                             │
└──────────────────────────────────────────────────────────────────┘
   Iceberg 공유:  Spark(batch write) ↔ dbt-spark(마트) ↔ Flink(batch·stream r/w)
   ※ BATCH(Spark)·STREAM(Flink)은 동시 기동 허용(실측 — 경계 3개는 conventions/k8s.md §9-3)
   ※ 스트림 소스는 Redpanda → Iceberg bronze로 변경(결정·같은 날 이행). Redpanda는 미도입 유지
   ※ [STREAM] 경로는 실증됨 — Spark append → Flink 스트리밍 읽기 → Iceberg 싱크 → Spark 되읽기
      체크포인트(SeaweedFS)·RocksDB 배포 완료.
   ※ **[BATCH] Flink는 Dagster 자산으로 배선됐다**(`defs/flink/`) — 세션 클러스터 기동 → SQL 실행 →
      **회수까지 자산 하나가 진다**(`finally`의 `teardown()`). ⚠️ 단 **선언이지 실측이 아니다**:
      코드는 main에 있으나 `terraform apply`·이미지 재빌드 전이라 클러스터는 아직 안 움직였다.
   ※ **스트림 잡 수명주기 관리는 여전히 미착수다.** 배치에서 성립한 것을 일반화하지 않는다 —
      TM이 잡 수명 내내 상주해 동시 기동 허용의 전제가 깨진다([architectures/flink.md](architectures/flink.md)).
```

> **※ dbt 경로는 아직 "클러스터 대상 실행"이 아니다**(실측). Spark Connect 서버는
> **`--master local[2]`**, 즉 **파드 한 개 안의 로컬 모드**로 돌고 있어 executor가 따로 뜨지 않는다.
> 위 그림의 화살표는 **목표 상태**이며, 현재는 "K8s 파드 안의 단일 JVM"이 정확한 서술이다.
> 한계(병렬도 ≈ 1·driver 힙 1g·`shuffle.partitions` 200)와 `k8s://` 전환에 필요한 2가지는
> [architectures/spark.md](architectures/spark.md) §`--master local[2]`. **이번 범위에서 전환하지 않는다** —
> 22모델을 아직 못 돌려 성능 문제가 발현하지 않았다.
>
> ⚠️ **배치 경로(`SparkApplication`)는 이와 별개로 오퍼레이터가 driver/executor를 띄운다.**
> 그림에서 두 경로가 같은 "Spark"로 보이지만 **실행 모델이 다르다.**

- **Dagster도 클러스터 안(in-cluster)** 에서 컨트롤 플레인 역할을 한다. 오케스트레이터와 컴퓨트를
  가르는 것은 **프로세스·수명 경계**이지 머신 경계가 아니다 — Databricks/EMR을 트리거하는 패턴은
  그대로이고, 다만 트리거하는 쪽이 같은 클러스터에 산다.
  🔴 **종전 서술("호스트 PC에 두고 `dg dev`로 돈다")은 폐기됐다.** 호스트 시절에는 클러스터에 닿기 위해
  port-forward 2개와 TLS Ingress + 로컬 CA 신뢰 주입이 필요했고, in-cluster에서 그 우회가 전부
  **서비스 DNS 직결**로 사라졌다. 정본은 [architectures/dagster.md](architectures/dagster.md).
- **컴퓨트·데이터 서비스는 K8s로 통일**한다(하이브리드 이중관리 회피). 컴퓨트는 **Spark(배치)+Flink(스트림)**.
- **Trino는 제거**한다. dbt는 **dbt-spark**로 이관하고, ad-hoc 조회는 Spark SQL로 대체한다.
- 자원 배분(**8 CPU / 26,702 MiB** VM — `scripts/k8s-env.sh`가 정본, **동시 기동 허용**)은
  [resource-sizing.md](resource-sizing.md) "Kubernetes 재설계 시나리오"와 [conventions/k8s.md](conventions/k8s.md) §9-3.
  🔴 백분율의 분모는 VM 총량이 아니라 **노드 Allocatable**(`8000m` / `26679964Ki` = 26054Mi 내림)이다.
  **수치의 정본은 [resource-sizing.md](resource-sizing.md) §(A)** 이고 여기는 요약이다 —
  자원을 바꾸면 **양쪽을 한 벌로** 갱신한다(상향 시 이 줄이 낡은 채 남아 있었다).

## 3. 핵심 결정 (설계 급소)

### 급소 ① — Spark에 "진짜 일"을 준다 (컴퓨트 분업)

현재 데이터 규모(최대 파일 ≈ 3.3GB)는 그 자체로 Spark/Flink가 필수는 아니다. 따라서 **역할이 겹치지 않도록**
분업을 명시해 "엔진을 위한 엔진"(오버엔지니어링)을 방지한다.
lineage(배치): **Spark(bronze·인제스트) → Iceberg → dbt-spark(silver/gold)** /
lineage(스트림): **Iceberg bronze(changelog 스트리밍 읽기) → Flink(실시간 피처·경보) → Iceberg**.
**스트림 소스가 Redpanda에서 바뀌었고** **같은 날 이행됐다** — 근거는
[architectures/flink.md](architectures/flink.md) §스트림 소스를 Redpanda에서 Iceberg changelog로 바꾼 근거,
실행 결과는 같은 문서 §스트리밍 왕복 실증.
**이 lineage에서 실증된 구간은 「Iceberg → Flink → Iceberg」의 *경로*까지**이고,
가운데의 **실시간 피처·경보 계산은 아직 없다**(실증 잡은 컬럼 하나를 붙이는 최소 SQL이다).

| 계층 | 엔진 | 대상(예) | 비고 |
| --- | --- | --- | --- |
| **bronze 적재(대용량)** | **Spark** `SparkApplication` | `mimiciv.chartevents`·`labevents`·`eicu.nurse_charting` | 기존 `load_heavy_csv_gz_to_iceberg`(boto3 청크 append) **대체** → 커스텀 코드 은퇴 + Spark 존재이유 확보 |
| bronze 적재(일반) | Dagster IO매니저(`pa.Table`) 유지 | 소형 테이블 | 현행 경로 유지(YAGNI) |
| **silver/gold 변환** | **dbt-spark**(Trino 대체) | `sofa`·`sepsis3`·`suspicion_of_infection` 등 22모델 | dbt 자산·스키마테스트 보존, 어댑터만 `dbt-trino`→`dbt-spark` |
| **실시간 스트리밍** | **Flink** `FlinkDeployment` | Iceberg **bronze changelog** → **실시간 SOFA/Sepsis-3 조기경보** | Flink의 존재이유. Iceberg 소스·싱크, 체크포인트는 SeaweedFS |
| Iceberg 유지보수 | Spark `rewrite_data_files`·`remove_orphan_files` | maintenance job | Trino 제거로 컴팩션도 Spark로 이관([spark.md](architectures/spark.md)) |
| ad-hoc 조회 | Spark SQL(Trino 대체) | 검증·탐색 | 인터랙티브 편의는 Trino보다 낮음(트레이드오프) |

### 급소 ② — Trino+Spark 동시 쓰기용 카탈로그

- **지금**: 기존 **Postgres 기반 Iceberg JDBC 카탈로그를 Spark·Flink가 공유**한다. Iceberg 낙관적
  동시성(compare-and-swap)으로 병행 R/W가 가능하다. 메타데이터 테이블(`iceberg_tables`,
  `iceberg_namespace_properties`) 스키마를 양쪽이 동일하게 보게 정합을 유지한다.
- **후속(선택)**: JDBC 카탈로그는 향후 Iceberg breaking change에 취약할 수 있어 **REST 카탈로그**
  (Nessie·Polaris·lakekeeper)가 권장된다. Spark+Flink 동시 writer 구조라 REST 카탈로그 이행 유인이 크다(별도 과제).

### 그 외 결정

| 포인트 | 결정 | 근거 |
| --- | --- | --- |
| 로컬 K8s 배포판 | **kind on Podman(rootful)** | Docker Desktop 탈피. kind Podman provider는 experimental이라 rootful 머신 필수([conventions/k8s.md](conventions/k8s.md) §10) |
| 컴퓨트 엔진 | **Spark(배치) + Flink(스트림)**, **Trino 제거** | 배치=Spark(bronze+dbt-spark), 스트림=Flink(실시간 경보). 역할 분리 |
| dbt 실행 엔진 | **dbt-spark**(← dbt-trino) | Trino 제거 대응. dbt-spark는 dbt Labs 유지보수 어댑터 |
| Dagster↔컴퓨트 트리거 | **자체 리소스**(← `PipesK8sClient`, 개정) | Spark=CRD 제출·폴링 / Flink=CRD 기동 + `exec` 스트림. 아래 주 참조 |
| 오브젝트 스토어 | **SeaweedFS 유지** + `path-style` 강제 | Spark·Flink S3A 모두 `fs.s3a.path.style.access=true` 필수 |
| 스트림 소스 | **Iceberg bronze 테이블**(changelog 스트리밍 읽기) | 신규 상주 인프라 0 — 브로커·소스DB·Debezium이 불요하다. Redpanda는 미도입 유지 |
| 데이터 서비스 위치 | SeaweedFS·카탈로그 Postgres **K8s로 이전** | 단일 패러다임(K8s) 통일 |
| 카탈로그 Postgres 관리 | **CloudNativePG 오퍼레이터**(← Deployment+emptyDir) | PVC·failover·PITR·튜닝이 CR 한 장. Spark·Flink 오퍼레이터와 **같은 선언형 패러다임**. 이전 구성은 재기동만으로 카탈로그가 소멸했다 |
| SeaweedFS 관리 | **StatefulSet 유지**(오퍼레이터 미채택 🔎) | 오퍼레이터는 master/volume/filer 분리로 **+500m/+1Gi** 상주 순증인데, 이미 PVC라 막을 유실 급소가 없다. Phase 2 이후 재검토 |
| Dagster 실행 위치 | **in-cluster**(개정 — 구 판정은 "호스트 유지") | 우회 경로(port-forward 2개 + TLS Ingress + CA 주입)가 서비스 DNS 직결로 대체된다. 호스트 headroom 회수는 예산 설계의 전제였다. run launcher는 `DefaultRunLauncher` 유지 |

> **※ 트리거 행 개정 — `PipesK8sClient`를 쓰지 않는다.**
> Pipes는 K8s Job/Pod를 띄우는 방식인데, Flink SQL 경로는
> [conventions/k8s.md](conventions/k8s.md) §C5 조건 2가 *"stdout이 컨테이너 로그가 되는
> Job/파드 형태로 만들지 않는다"* 로 못 박아 **정면 충돌**한다(로그로 나가면 크리덴셜 회수 불가).
> Spark 경로도 실제로는 Pipes가 아니라 자체 `SparkOperatorResource`이고 `dagster-k8s`는 **미설치**다 —
> 종전 서술은 **착수 전 계획이 그대로 남아 있던 것**이며, Spark 배선 시점에 이미 사실과 달랐다.
> `K8sRunLauncher`는 축이 다르다(run 자체를 파드로 띄우는 옵션이라 트리거 수단이 아니다).
> 경로별 비교는 [architectures/dagster.md](architectures/dagster.md) §원격 컴퓨트 트리거.

## 4. 이행 플랜

**PoC 우선 · PDCA**로 단계를 나눈다. 각 Phase는 **게이트를 통과해야** 다음으로 넘어간다.

| Phase | 무엇을 증명하는가 |
| --- | --- |
| **0** | PoC — 이 토폴로지가 **실현 가능한가** |
| **1** | 데이터 서비스 K8s 이전 + dbt 엔진 전환 |
| **2** | 대용량 bronze 인제스트를 Spark로 전환 |
| **3** | Flink 실시간 스트리밍 — **Flink의 존재 이유** |
| **4** | 오케스트레이션 정착 + 문서·컨벤션 확정 |
| **5** | 분석 계층 — 로드맵의 종착지 |

🔴 **Phase 게이트는 "돌았다"가 아니라 "같은 값이 나온다"로 통과시킨다.**
`dbt compile` 통과·`dg check` 통과는 구문이 맞았다는 뜻이지 엔진이 같은 값을 낸다는 뜻이 아니다.

📌 **각 Phase의 진행 상태는 저장소 밖에 있다** — `$OBSIDIAN_VAULT/status/redesign-progress.md`.
무엇이 끝났고 무엇이 막혀 있는지는 **그때그때 바뀌는 사실**이라 여기 두면
아무도 손대지 않아도 낡는다.

## 5. 리스크·트레이드오프 (정직한 기록)

| 관점 | 평가 | 메모 |
| --- | --- | --- |
| 정확성/학습가치 | ★★★★★ | Spark/Flink 2개 K8s Operator(선언형 CRD)·배치+스트림 시연 = 강한 포트폴리오 신호 |
| 리스크 | ★★★☆☆ | Dagster↔Operator canonical 예제 부재(커스텀 글루) + **dbt-trino→dbt-spark SQL 방언 교정** 필요(Phase 0·1로 방어) |
| 비용 | ★★★☆☆ | 로컬이라 클라우드 비용 0, 단 **단일 PC RAM 압박**이 급소다. 동시 기동으로 수용하되 회수 규율을 지킨다 |
| 효율(개발 루프) | ★★☆☆☆ | in-process 대비 느려짐(이미지 빌드→레지스트리 push→CRD 제출). **의도된 학습 트레이드오프**로 수용 |

- **데이터 규모 대비 Spark/Flink 과함**은 인정하고, 급소①의 분업으로 정당성을 확보한다
  — 대용량 인제스트는 Spark, 실시간 경보는 Flink.
- **Trino 제거 비용**: 성숙한 인터랙티브 SQL과 `dbt-trino`를 잃는다.
  ad-hoc 조회 편의가 낮아지고 방언 교정이 필요하다(수용된 트레이드오프).

## 6. 참고 (공식 문서)

- Apache Spark Kubernetes Operator(GA 1.0.0, Kubeflow에서 이전): https://apache.github.io/spark-kubernetes-operator/ · 릴리스: https://github.com/apache/spark-kubernetes-operator/releases
- Apache Flink Kubernetes Operator: https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-main/
- Dagster Pipes / dagster-k8s(PipesK8sClient): https://docs.dagster.io/api/python-api/libraries/dagster-k8s
  — 🔎 **검토 후 미채택**(§3 트리거 행). 링크는 기각 근거를 확인할 수 있도록 남긴다.
- Kubernetes API — Pod exec(`connect_get_namespaced_pod_exec`): https://kubernetes.io/docs/reference/kubernetes-api/workload-resources/pod-v1/#proxy
- Apache Flink SQL Client(`-i` init 파일·옵션): https://nightlies.apache.org/flink/flink-docs-release-2.1/docs/dev/table/sqlclient/
- Dagster & Spark: https://docs.dagster.io/integrations/libraries/spark
- dbt-spark 어댑터: https://docs.getdbt.com/docs/core/connect-data-platform/spark-setup
- Spark on Kubernetes: https://spark.apache.org/docs/latest/running-on-kubernetes.html
- Redpanda(Kafka API) — 🔎 **미도입 유지**(스트림 소스가 Iceberg로 변경): https://docs.redpanda.com/
- Iceberg Flink 읽기(스트리밍·append 제약): https://iceberg.apache.org/docs/latest/flink-queries/
- Iceberg JDBC 카탈로그: https://iceberg.apache.org/docs/latest/jdbc/ · REST 카탈로그 권고: https://trino.io/docs/current/object-storage/metastores.html
- kind Podman provider: https://kind.sigs.k8s.io/docs/user/rootless/ · 로컬 레지스트리: https://kind.sigs.k8s.io/docs/user/local-registry/
