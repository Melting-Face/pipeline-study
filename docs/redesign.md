# 재설계 로드맵 — 호스트 Dagster + Kubernetes(Spark Operator)

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
  로컬에서 재현한다. Dagster는 **호스트 PC**에 두고, 컴퓨트는 **로컬 K8s의 Spark Operator**로 옮긴다.
- **로드맵의 종착지는 인프라가 아니라 분석이다.** Phase 0~4는 수단을 세우고, **Phase 5에서 gold 마트·
  리포트로 닫힌다**(§4). 인프라가 "도는 것"은 완료 조건이 아니다.

## 2. 목표 아키텍처 (토폴로지)

```
┌───────────────── 호스트 PC (control plane) ─────────────────────┐
│  Dagster webserver + daemon   (uv run dg dev)                    │
│    • 배치: PipesK8sClient로 SparkApplication(CRD) 제출·폴링         │
│    • 스트림: FlinkDeployment(CRD) 제출·수명주기 관리                 │
│    • dbt CLI(dbt-spark) → Spark Connect 대상 실행 (※ 아래 주 참조)  │
│  Dagster 메타 Postgres (호스트/compose 유지)                       │
└───────────────┬──────────────── kubeconfig ────────────────────┘
                │ k8s API · (필요 시) port-forward
┌───────────────▼─────────── 로컬 K8s (kind on Podman) ───────────┐
│  Spark Operator (Helm) → SparkApplication → driver/executor      │  [BATCH]
│  Flink Operator (Helm) → FlinkDeployment → JobManager/TaskManager│  [STREAM]
│  Iceberg bronze 테이블 ← 스트림 소스(changelog 읽기)             │  [STREAM]
│  SeaweedFS  (StatefulSet+PVC) ← S3(path-style)·IB 웨어하우스·체크포인트│
│  CloudNativePG (Helm) → Cluster(CRD) → Catalog Postgres(+PVC)     │
│                                       ← Iceberg JDBC 카탈로그      │
│  로컬 레지스트리 (kind local-registry)                             │
└──────────────────────────────────────────────────────────────────┘
   Iceberg 공유:  Spark(batch write) ↔ dbt-spark(마트) ↔ Flink(stream r/w)
   ※ BATCH(Spark)·STREAM(Flink)은 동시 기동 허용(2026-08-22 실측 — 경계 3개는 conventions/k8s.md §9-3)
   ※ 스트림 소스는 Redpanda → Iceberg bronze로 변경(2026-08-23 결정·같은 날 이행). Redpanda는 미도입 유지
   ※ [STREAM] 경로는 2026-08-23 실증됨 — Spark append → Flink 스트리밍 읽기 → Iceberg 싱크 → Spark 되읽기
      체크포인트(SeaweedFS)·RocksDB 배포 완료. 단 Dagster의 스트림 잡 수명주기 관리는 아직 목표(미착수)
```

> 🔴 **※ dbt 경로는 아직 "클러스터 대상 실행"이 아니다**(2026-08-22 실측). Spark Connect 서버는
> **`--master local[2]`**, 즉 **파드 한 개 안의 로컬 모드**로 돌고 있어 executor가 따로 뜨지 않는다.
> 위 그림의 화살표는 **목표 상태**이며, 현재는 "K8s 파드 안의 단일 JVM"이 정확한 서술이다.
> 한계(병렬도 ≈ 1·driver 힙 1g·`shuffle.partitions` 200)와 `k8s://` 전환에 필요한 2가지는
> [architectures/spark.md](architectures/spark.md) §`--master local[2]`. **이번 범위에서 전환하지 않는다** —
> 22모델을 아직 못 돌려 성능 문제가 발현하지 않았다.
>
> ⚠️ **배치 경로(`SparkApplication`)는 이와 별개로 오퍼레이터가 driver/executor를 띄운다.**
> 그림에서 두 경로가 같은 "Spark"로 보이지만 **실행 모델이 다르다.**

- **Dagster는 클러스터 밖(호스트)** 에서 컨트롤 플레인 역할만 한다. Databricks/EMR을 트리거하는 것과
  동일한 패턴이며, `dg dev` 기반 **빠른 개발 루프**를 유지한다.
- **컴퓨트·데이터 서비스는 K8s로 통일**한다(하이브리드 이중관리 회피). 컴퓨트는 **Spark(배치)+Flink(스트림)**.
- **Trino는 제거**한다. dbt는 **dbt-spark**로 이관하고, ad-hoc 조회는 Spark SQL로 대체한다.
- 자원 배분(**8 CPU / 22,888 MiB** VM — `scripts/k8s-env.sh`가 정본, **동시 기동 허용**)은
  [resource-sizing.md](resource-sizing.md) "Kubernetes 재설계 시나리오"와 [conventions/k8s.md](conventions/k8s.md) §9-3.
  🔴 백분율의 분모는 VM 총량이 아니라 **노드 Allocatable**(`8000m` / `22843508Ki`)이다.

## 3. 핵심 결정 (설계 급소)

### 급소 ① — Spark에 "진짜 일"을 준다 (컴퓨트 분업)

현재 데이터 규모(최대 파일 ≈ 3.3GB)는 그 자체로 Spark/Flink가 필수는 아니다. 따라서 **역할이 겹치지 않도록**
분업을 명시해 "엔진을 위한 엔진"(오버엔지니어링)을 방지한다.
lineage(배치): **Spark(bronze·인제스트) → Iceberg → dbt-spark(silver/gold)** /
lineage(스트림): **Iceberg bronze(changelog 스트리밍 읽기) → Flink(실시간 피처·경보) → Iceberg**.
🔴 **스트림 소스가 Redpanda에서 바뀌었고**(2026-08-23 결정) **같은 날 이행됐다** — 근거는
[architectures/flink.md](architectures/flink.md) §스트림 소스를 Redpanda에서 Iceberg changelog로 바꾼 근거,
실행 결과는 같은 문서 §스트리밍 왕복 실증.
🔴 **이 lineage에서 실증된 구간은 「Iceberg → Flink → Iceberg」의 *경로*까지**이고,
가운데의 **실시간 피처·경보 계산은 아직 없다**(실증 잡은 컬럼 하나를 붙이는 최소 SQL이다).

| 계층 | 엔진 | 대상(예) | 비고 |
| --- | --- | --- | --- |
| **bronze 적재(대용량)** | **Spark** `SparkApplication` | `mimiciv.chartevents`·`labevents`·`eicu.nurse_charting` | 기존 `load_heavy_csv_gz_to_iceberg`(boto3 청크 append) **대체** → 커스텀 코드 은퇴 + Spark 존재이유 확보 |
| bronze 적재(일반) | Dagster IO매니저(`pa.Table`) 유지 | 소형 테이블 | 현행 경로 유지(YAGNI) |
| **silver/gold 변환** | **dbt-spark**(Trino 대체) | `sofa`·`sepsis3`·`suspicion_of_infection` 등 22모델 | dbt 자산·스키마테스트 보존, 어댑터만 `dbt-trino`→`dbt-spark` |
| **실시간 스트리밍** | **Flink** `FlinkDeployment` | Iceberg **bronze changelog** → **실시간 SOFA/Sepsis-3 조기경보** | Flink의 존재이유(급소① 동일 논리). **Iceberg 소스**(← Redpanda, 2026-08-23 변경), Iceberg 싱크, 체크포인트=SeaweedFS. ✅ **경로는 2026-08-23 실증**(스트리밍 왕복 + 체크포인트 79회) · 🎯 **피처 계산은 미착수**. 🔴 스트리밍 읽기는 **append 스냅샷만** 보므로 `merge into`를 쓰는 dbt 실버 테이블은 소스가 될 수 없다 |
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
| Dagster↔컴퓨트 트리거 | **PipesK8sClient + SparkApplication/FlinkDeployment 제출·폴링** | Pipes가 로그·materialization 회수. `K8sRunLauncher`는 in-cluster 배포용이라 부적합 |
| 오브젝트 스토어 | **SeaweedFS 유지** + `path-style` 강제 | Spark·Flink S3A 모두 `fs.s3a.path.style.access=true` 필수 |
| 스트림 소스 | **Iceberg bronze 테이블**(changelog 스트리밍 읽기) — 🔴 **Redpanda 미도입 유지**(2026-08-23 변경, ✅ **같은 날 이행**) | 신규 상주 인프라 0(브로커·소스DB·Debezium 불요) ⇒ §9-3 경계 ③(Redpanda 예산 재계산) 미발동. 🔴 Redpanda는 **기각이 아니라 대체되어 불필요해진 것**이다. 종전 "vitalsign 리플레이"는 **dbt 실버 모델을 인위적으로 되돌리는** 구성이었다([architectures/flink.md](architectures/flink.md) §스트림 소스 변경 근거) |
| 데이터 서비스 위치 | SeaweedFS·카탈로그 Postgres **K8s로 이전** | 단일 패러다임(K8s) 통일 |
| 카탈로그 Postgres 관리 | **CloudNativePG 오퍼레이터**(← Deployment+emptyDir) | PVC·failover·PITR·튜닝이 CR 한 장. Spark·Flink 오퍼레이터와 **같은 선언형 패러다임**. 이전 구성은 재기동만으로 카탈로그가 소멸했다 |
| SeaweedFS 관리 | **StatefulSet 유지**(오퍼레이터 미채택 🔎) | 오퍼레이터는 master/volume/filer 분리로 **+500m/+1Gi** 상주 순증인데, 이미 PVC라 막을 유실 급소가 없다. Phase 2 이후 재검토 |
| Dagster 실행 위치 | **호스트 유지** | 개발 루프 속도 + 컨트롤/컴퓨트 분리 시연 |

## 4. 단계별 이행 플랜 (PoC 우선 · PDCA)

> 원칙: **커스텀 글루(Dagster↔Spark Operator)의 실현성을 PoC로 먼저 확인**해 리스크를 가장 크게 줄인 뒤 이행한다.
> 각 Phase는 **성공 게이트**를 통과해야 다음으로 넘어간다.

### Phase 0 — PoC (실현성 검증) ✅ 게이트 통과 (2026-08-18)

- **Plan**: kind(on Podman) 클러스터 + Spark Operator(Helm) 위에, 최소 SparkApplication을 **Dagster 자산이
  `PipesK8sClient`로 제출**하고 Iceberg 테이블에 write까지 성공시킨다.
- **Do**: ① `scripts/k8s-up.sh`(podman machine rootful **당시 6/16** + kind + 로컬 레지스트리, config는 `k8s/kind-cluster.yaml`)
  — 🔴 이 6/16은 **Phase 0 실행 당시의 값**이다. 이후 `scripts/k8s-env.sh`가 **8 CPU / 22,888 MiB**로 올랐으므로
  현재 값을 볼 때는 스크립트를 본다(스크립트가 정본).
  ② `scripts/k8s-operators.sh`(Spark Operator Helm; **Flink 오퍼레이터는 기본 설치** — 제외하려면 `INSTALL_FLINK=false`)
  ③ PySpark+Iceberg 러너 이미지 빌드·push
  ④ SeaweedFS/카탈로그 Postgres 최소 기동 ⑤ Dagster 자산에서 CRD 제출·폴링. 정리는 `scripts/k8s-down.sh`.
- **Check(성공 게이트)**: Iceberg 테이블 1개가 Spark로 append되고 **Spark SQL로 조회**되며, Dagster UI에
  로그·materialization이 회수된다.
- **Act**: 검증된 최소 골격을 리소스(`SparkOperatorResource`)·러너 이미지 규격으로 확정.
- **검증 결과(2026-08-18)**: 호스트 Dagster 자산 → `SparkApplication`(Apache CRD `spark.apache.org/v1`) 제출·폴링 →
  Iceberg `iceberg.poc.sample` write + Spark SQL read-back → driver 로그 회수 → materialization 메타
  `rows=3` 기록, webserver GraphQL 노출 확인. 당시 러너 이미지 `localhost:5001/spark-runner:0.2.0`(S3A 포함)
  — **현행은 `0.4.0`**(Spark Connect 추가, 빌드·push 절차는 [conventions/k8s.md](conventions/k8s.md) §10).
- **이 과정에서 고친 잠복 결함 3건**(모두 조용히 실패하던 것):
  ① Apache 이전이 `k8s/`·`scripts/`에만 적용되고 **Dagster 글루(`defs/poc/`)는 Kubeflow 스펙**으로 남아 있었다.
  ② `assets.py`에 `@dg.definitions`가 같이 있어 **자산이 자동발견에서 누락**됐다([conventions/dagster.md](conventions/dagster.md)).
  ③ driver 파드 이름을 `<app>-driver`로 조립했으나 Apache는 `<app>-<attempt>-driver`라 **로그 회수가 빈 문자열**이었다.

### Phase 1 — 데이터 서비스 K8s 이전 + dbt 엔진 전환 🚧 진행중 (2026-08-18 착수)

- **Plan/Do**: SeaweedFS·카탈로그 Postgres를 **Helm/매니페스트**로 K8s에 배포([conventions/k8s.md](conventions/k8s.md) 규칙 준수).
  dbt 어댑터를 **`dbt-trino`→`dbt-spark`** 로 교체하고 Spark SQL 엔드포인트(Thrift/Connect)에 연결.
- **Check**: 22모델이 dbt-spark로 `dbt build` 통과(SQL 방언 차이 교정), 스키마테스트 유지.
- **Act**: compose에서 Trino 제거·env 전파 체인 재확인([operations.md](operations.md)).
- **진행 상황(2026-08-18)**
  - 데이터 서비스는 **이미 K8s에 있음**(SeaweedFS·카탈로그 Postgres, PoC 단계에서 선행).
  - **(2026-08-19)** 카탈로그 Postgres를 **CloudNativePG `Cluster`로 재정의**했다 —
    기존 `Deployment`+`emptyDir`는 파드 재기동만으로 카탈로그가 소멸하는 구성이었다.
    보존할 데이터가 없는 시점이라 이행 비용이 최저(재적재 전제).
    **적용 완료** — CNPG 1.30.0(chart 0.29.0) 설치, `Cluster` 1인스턴스 + PVC 5Gi 기동,
    Spark 잡이 새 카탈로그에 `iceberg.poc.sample` 등록까지 확인. 접속은 `catalog-postgres-rw`.
  - `dbt-spark 1.11.0` 설치. **pyspark는 3.5 계열로 핀**(클러스터 러너 Spark 3.5.9와 맞춤).
  - 프로파일 2종 추가 — **`spark_session`**(호스트 로컬 Spark, 상시 서비스 0) /
    **`spark_connect`**(클러스터 **Spark Connect 서버**, 컴퓨트=K8s). 둘 다 **연결 확인 완료**.
    dbt-spark의 `server_side_parameters`가 세션 생성 시 `builder.config()`로 적용되므로
    Iceberg 설정 전체를 프로파일에 두면서 **비밀정보는 `env_var()` 참조**로 유지한다.
  - `dbt show --target spark_connect`로 **Iceberg 테이블 조회 성공**(dbt→Connect→Iceberg 전 구간).
  - **`dbt compile` 22모델 전부 통과**. 단, 그 전에 `source.yml`의 `database: iceberg`를 제거해야 했다
    (dbt-spark는 relation에 database 설정을 금지 — `Cannot set database in spark!`).
  - **(2026-08-23) 🟡 엔진 버전 축이 바뀌었다 — Spark 3.5.9 → 4.1 상향 결정(결정 완료 · 이행 전)**
    - 🔴 **이 항목만 성격이 다르다.** 위 진행 상황은 전부 **실행된 것**이고, 이것은 **아직 아무것도
      올리지 않은 결정**이다. 러너 이미지는 여전히 **3.5.9**다.
    - 근거는 *"최신이라서"* 가 아니라 **지원 범위 복귀**다 — 저장소가 핀한 Spark Operator
      chart **1.8.0**(appVersion 1.0.0)이 **"drop Spark 3.5"** 를 명시하고 있어 **현행 조합이 이미
      공식 지원 밖**이다. 최신인 **4.2가 아니라 4.1**인 이유는 Iceberg가 `-4.2` 런타임을
      발행하지 않기 때문이다(규약: 최신이 아니라 **Iceberg가 지원하는 짝**).
    - 의존성 핀은 **`pyspark<3.6`만 해제**하고 **`dbt-spark<1.12`는 유지**한다.
    - 확인 항목 표·발견·미해결 2건은 [architectures/spark.md](architectures/spark.md)
      §Spark 3.5.9 → 4.1 상향 결정.
- **SQL 방언(정적 스캔) — 전부 해소** / 정본·규칙은 [conventions/dbt.md](conventions/dbt.md) "방언 차이는 크로스 어댑터 매크로로 흡수한다"

  | 구문 | 파일 수 | 상태 | Trino → Spark |
  | --- | --- | --- | --- |
  | `INTERVAL '1' HOUR` 리터럴 | 8 → **0** | ✅ 해소(`52e7cde`) | `{{ dbt.dateadd(...) }}` **내장** 매크로 |
  | `date_diff('unit', a, b)` | 3 → **0** | ✅ 해소(`589bd5a`) | `{{ elapsed(...) }}` **프로젝트 dispatch** 매크로 |
  | `CROSS JOIN UNNEST(...)` | 1 → **0** | ✅ 해소(`589bd5a`) | `{{ unnest_array(...) }}` **프로젝트 dispatch** 매크로 |
  | `sequence(...)`·`date_trunc(...)` | 3 | ✅ 무조치 | 양쪽 엔진에 동일 의미로 존재 |

  > 🔴 **`dbt.datediff`는 일부러 쓰지 않았다** — `spark__datediff`는 경과시간 `ceil`, Trino 네이티브는
  > **경계 교차**라 임계값 비교(`ventilation >= 14`·`urine_output_rate <= 5`)에서 silver 피처 값이 갈린다.
  > Trino를 정본으로 두고 Spark에 같은 수식을 재현했다. **"도는 것"이 아니라 "같은 값"이 이행 기준이다.**
  > 두 타깃 compile·렌더 대조는 통과했고 **실행 검증만 원천 데이터 부재로 보류**다.

- **선행 조건(로드맵에 없던 발견)**: bronze 테이블이 **K8s 카탈로그에 없다**(compose 쪽 카탈로그에만 존재).
  실행 단계 검증은 **Phase 2(대용량 적재 Spark 이전)와 순서가 얽힌다** — 데이터 이관이 먼저다.

### Phase 2 — 대용량 bronze 인제스트 Spark 전환 ⏸ **원천 일부 확보, 핵심 대용량은 미확보**

> 🔴 **2026-08-19 정정** — 2026-08-18의 "원천이 어디에도 없다"는 **오진이었다**.
> compose SeaweedFS의 `warehouse` 버킷에 원천 csv.gz **9개(압축 110.7MB)** 가 실재했다.
> 당시엔 **K8s SeaweedFS만** 조회했고 compose 쪽은 컨테이너가 `Exited`라 S3 API가 죽어 있어
> "버킷 0개"로 읽혔다. **죽은 엔드포인트의 조회 실패를 데이터 부재로 판정하면 안 된다** —
> 부재 판정은 **조회 경로가 살아 있을 때만** 유효하다.
> (`iceberg_catalog` DB 부재·호스트 csv.gz 부재는 사실이었다.)
>
> **2026-08-19 이관 완료**: compose SeaweedFS → K8s SeaweedFS로 S3→S3 스트리밍 복사.
> 크기 대조 9/9 + **gzip CRC 전수 검증 9/9** 통과(크기만으로는 SeaweedFS 체크섬 손상을 못 잡는다).
> 이제 원천은 **K8s SeaweedFS `s3://warehouse/raw/` 단일 소재**이고, compose SeaweedFS 컨테이너는 제거했다
> (호스트 바인드 마운트 `./seaweedfs/data` 309MB는 남아 사실상 원본 백업).
>
> **확보 현황** — `scripts/upload_raw_to_seaweedfs.py` 매니페스트(=자산이 실제로 읽는 파일) 14개 기준:
>
> | 구분 | 파일 |
> | --- | --- |
> | ✅ 확보(4) | `eicu/patient`·`eicu/diagnosis`·`mimiciv/hosp/admissions`·`mimiciv/hosp/d_labitems` |
> | ✖ **부재(10)** | `eicu/nurseCharting` · `mimiciv/icu/{icustays,chartevents,inputevents,outputevents,d_items}` · `mimiciv/hosp/{patients,labevents,prescriptions,microbiologyevents}` |
> | ➕ 여분(5) | `mimiciv/hosp/{diagnoses_icd,drgcodes,d_icd_diagnoses,d_icd_procedures,d_hcpcs}` — 매니페스트 밖 |
>
> → **이 단계가 겨냥한 대용량 3종**(`chartevents`·`labevents`·`nurseCharting`)이 **전부 부재**라
> Spark 인제스트 전환은 여전히 대기다. 22모델이 요구하는 7 source 중에도 `admissions` 하나뿐이라
> **`dbt build` 실행 검증도 불가**하다. 나머지는 **PhysioNet 자격증명 + DUA** 대상이라
> 사용자가 직접 받아야 한다(저장소 커밋 금지). 받은 뒤 `scripts/upload_raw_to_seaweedfs.py`로 올린다.
> → 다만 **eICU 2종은 실물이므로 bronze 적재 자산을 실제로 돌려볼 수 있다**(경로 검증 가능).
>
> **데이터 없이 미리 끝낸 것**(합성 3행으로 전 구간 검증 후 정리):
> S3(csv.gz) → Dagster 자산 → Iceberg 적재 → **Spark Connect에서 조회**까지 K8s 스택에서 통과.
> 이 과정에서 카탈로그 이름 분리·SeaweedFS 체크섬 두 결함을 찾아 고쳤다([conventions/k8s.md](conventions/k8s.md) §11).
>
> 🔴 **체크섬 결함은 "고쳤다"로 닫히지 않았다**(2026-08-22). 그때 고친 것은 **한쪽 경로**뿐이고,
> Iceberg를 `1.6.1 → 1.11.0`으로 올리자 **번들 AWS SDK v2가 `2.26.20 → 2.44.4`로 바뀌면서**
> `S3FileIO` 경로에서 같은 증상이 재발했다 — `metadata.json`이 aws-chunked 프레이밍째 저장돼
> **`DROP TABLE`조차 실패**했다. 해법은 `AWS_REQUEST_CHECKSUM_CALCULATION`·
> `AWS_RESPONSE_CHECKSUM_VALIDATION`을 **driver·executor 양쪽에** 주는 것이다.
> 📌 **"이 결함은 고쳤다"를 적을 때는 *어느 경로에 대해* 고쳤는지 함께 적는다** —
> 경위·오진 과정은 [architectures/spark.md](architectures/spark.md) §SeaweedFS 체크섬 결함.

- **Plan/Do**: `chartevents`·`labevents`·`eicu.nurse_charting` 적재를 **SparkApplication**으로 이전,
  `load_heavy_csv_gz_to_iceberg`(boto3 청크) **은퇴**.
- **Check**: 행 수·스키마가 기존 적재분과 일치(회귀), small-files 대비 파일 크기 개선 확인.
- **Act**: 유지보수(compaction) 순서 재점검(compact→expire→orphan, [spark.md](architectures/spark.md)).
- **(2026-08-23) 🟡 Spark 4.1 상향 결정이 이 단계에 두 가지를 얹는다**(결정만 됨 · 이행 전)
  - **S3A 좌표가 v1 → v2로 바뀐다** — Hadoop 3.4.0이 S3A를 AWS SDK v2로 옮기며 v1
    `aws-java-sdk-bundle` JAR을 제거했다(`HADOOP-18820`). 이 단계는 `s3a://`로 원본 csv.gz를 읽는
    **유일한 경로**라 영향을 직접 받는다. 좌표표는 [architectures/spark.md](architectures/spark.md) §러너 이미지 버전.
  - 🔴 **S3A 체크섬 축이 새로 열린다** — 이 저장소가 이미 두 번 겪은 SeaweedFS aws-chunked 손상이
    **S3A 축에서 재현될 수 있다.** 완화 env가 SDK v2 전역이라 덮을 것으로 **보이나 이것은 추론**이며,
    **일부러 env를 빼서 손상이 재현되는지 확인**하기 전까지 상태는 `미확인`이다.
  - 🔴 **값 정합 검증 수단은 여전히 없다** — 대용량 3종 부재로 `dbt build` 실행 검증이 불가한데,
    엔진을 3.5 → 4.1로 올리면 **값이 갈릴 가능성이 새로 생긴다.** `sqlfluff`·`dbt compile` 통과를
    값 정합으로 읽지 않는다.

### Phase 3 — Flink 실시간 스트리밍 (Flink의 존재이유) 🚧 **진행중 — ✅ 배치 왕복(08-22) · ✅ 스트리밍 왕복(08-23) 실증**

> 🔴 **2026-08-23 상태 — ⏸(스트리밍 미착수)를 해제한다.** 종전 판이 적던
> *"체크포인트·RocksDB는 하나도 배포되지 않았다"* 는 **더 이상 사실이 아니다.**
> **Spark append(소스) → Flink 스트리밍 읽기 → Iceberg 싱크 → Spark 되읽기**를
> **장수명 잡 하나**(`5c748c8cc55f4e9ef82b51a19a2972a3`, state RUNNING)로 관통했다.
>
> | 축 | 닫힌 것 (2026-08-23 실측) |
> | --- | --- |
> | **스트리밍 왕복** | 초기 스캔 `flink-batch` **12행** 이후, 잡이 **살아 있는 채로** Spark가 나중에 append한 `spark-incr` **3행**을 처리(**약 6분 39초 차**). 로그가 아니라 **테이블로 판별** |
> | **삼중 증거** | ⓐ 데이터(`src`·처리 시각 시차) ⓑ 스냅샷 summary(`engine-name=flink` · `iceberg-version=Apache Iceberg 1.11.0`, `added-records` 12/3) ⓒ 두 스냅샷의 `flink.job-id`가 **제출 잡 ID와 일치** |
> | **체크포인트 · RocksDB · S3 상태** | **배포됨** — `state.backend.type=rocksdb`, `execution.checkpointing.dir=s3://warehouse/flink-checkpoints`. **79회 완료 / 1회 실패**, `chk-79` state_size **1,025 B** · e2e **45 ms**, `_metadata` **2,481 B로 온전**(SeaweedFS 체크섬 손상 없음 — 번들 SDK가 **v1**) |
> | **`flink-s3-fs-hadoop` 플러그인** | 러너 이미지 **`flink-runner:0.3.0`** 에 포함(**31,696,370 B**, 네트워크 다운로드 0) |
>
> **남은 것**(= Phase 3이 아직 🚧인 이유) — 이번 잡은 **경로를 여는 최소 SQL**(`ingested_at` 컬럼 부가)이다.
> ① **실시간 SOFA/Sepsis-3 피처 계산**(이벤트타임 윈도우) ② **Dagster의 잡 수명주기 관리**
> ③ **배치(dbt-spark)↔스트림 값 교차검증**(Phase 5에서 리포트로 닫힌다).
>
> 🔴 **`미확인`으로 남긴 것**: **체크포인트로부터의 복구**(파일이 온전한 것과 복구되는 것은 다른 축) ·
> **체크포인트 실패 1건의 원인**(첫 회로 추정하나 확인하지 않았다) ·
> **싱크 커밋 주기가 약 100초인 이유**(값은 관측, 원인 미규명) ·
> **부적격 소스(overwrite 섞인 테이블)에서의 동작**(append-only 소스로만 돌렸다).
>
> 🔴 **순서 함정** — `externalized-checkpoint-retention` 기본값이 **`DELETE_ON_CANCELLATION`** 이라
> `flink cancel` 후 `flink-checkpoints/`가 **0.0 KiB로 떨어졌다**. **증거 수집은 취소 *전에* 한다**
> (회수 규율과 증거 규율이 충돌하는 지점 — [architectures/flink.md](architectures/flink.md) §순서 함정).
>
> 🔴 **오판 1건을 기록으로 남긴다** — 증분 검증에서 **120초 동안 12행 그대로**여서
> *"증분이 흐르지 않는다"* 고 판정했으나 **틀렸다.** 원인은 ① **Spark Connect 세션의 메타데이터 캐시**
> (`REFRESH TABLE` 후 15행) ② **싱크 커밋 간격이 체크포인트 간격(10s)이 아니라 약 100초**.
> ⇒ **부정 결과는 조회 경로가 신선한지 함께 확인해야 유효하다**([philosophy.md](philosophy.md) 원칙 7).

> **2026-08-22 상태 — 전제 조건이 하나 닫혔다.** 오퍼레이터 **1.15.0** + `FlinkDeployment` 세션
> 클러스터로 **Spark ↔ Flink Iceberg 배치 왕복**을 실증했다
> (Spark 적재 → Flink 읽기 → **Flink 쓰기** → Spark 되읽기). 데이터 컬럼·스냅샷 메타데이터
> (`engine-name`·`iceberg-version`)·`flink.job-id` ↔ 잡 `jid` 일치의 **삼중 증거**로 닫았고,
> 같은 카탈로그에 `spark`·`flink` 두 서명이 공존함을 확인했다([architectures/flink.md](architectures/flink.md)).
>
> ⇒ **"Flink가 이 레이크하우스에 쓸 수 있는가"는 더 이상 미확인이 아니다.**
> 🔴 **당시 남아 있던 것은 스트리밍 고유 부분**이었다 — 체크포인트·RocksDB가 **하나도 배포되지 않은
> 상태**였고, 배치는 잡 완료 시점에 커밋해 체크포인트가 필요 없었지만 **스트리밍은 체크포인트 단위로
> 커밋**하므로 착수 시 **체크포인트 설정 + `flink-s3-fs-hadoop` 플러그인 + 러너 이미지 재빌드가
> 동시에** 필요하다고 적었다. ✅ **이 셋은 2026-08-23에 한 벌로 해소됐다**(위 블록) — 예측이 맞았고,
> 위에 적힌 대로 **동시에** 필요했다.
>
> **검증 후 세션 클러스터는 그 자리에서 다시 내렸다.** 유휴 비용은 **JM 1000m/2048Mi**뿐이고
> (TM은 잡 제출 +7초 기동 → 46~52초 생존 → 자동 회수), 🔴 **사유는 시분할 위반이 아니라 회수 규율이다** —
> 경계 ①은 오히려 **JM 상주를 전제로** 동시 기동을 허용한다([conventions/k8s.md](conventions/k8s.md) §9-3).
> 쓰지 않는 상주분을 놀리지 않는다는 것이 근거이고, **예산 여유는 회수를 면제하지 않는다.**
> 2026-08-19에 같은 구성이 **13시간 샜고 발견 경로가 성능 이상이 아니라 "안 쓰는 것 정리"였다** —
> 그래서 회수를 다음으로 미루지 않는다. 🔴 `INSTALL_FLINK` **기본값은 `true`(기본 설치)** 라
> 재기동하면 **오퍼레이터는 다시 뜬다** — 빼려면 `INSTALL_FLINK=false`를 준다(스크립트가 정본).
> 다만 오퍼레이터가 떠도 **세션 클러스터(`FlinkDeployment`)는 별도 적용**이라 JM이 자동으로 서지는 않는다.
>
> ⚠️ **드리프트 교정**: 2026-08-19판이 *"cert-manager도 함께 제거했다"* 고 적은 것은 **거짓**이다.
> **CNPG barman 플러그인이 cert-manager를 무조건 요구**하므로 줄곧 `Running`이었다.
> 그 문장은 *실행한 명령의 기록*이었지 *관측된 상태*가 아니었다([architectures/flink.md](architectures/flink.md) §드리프트 교정).

> 🔴 **2026-08-23 스트림 소스 결정이 바뀌었고(결정 완료) 같은 날 이행됐다** — **Redpanda 배포 전제를 뺀다.**
> 소스는 **Iceberg bronze 테이블의 changelog 스트리밍 읽기**이고 **Redpanda는 미도입 유지**다.
> 🔴 **기각이 아니라 대체되어 불필요해진 것**이다(소스가 이미 있는 Iceberg 테이블이라 브로커·소스DB·
> Debezium이 전부 불요). 신규 상주 인프라가 0이라 [conventions/k8s.md](conventions/k8s.md) §9-3
> **경계 ③(Redpanda 도입 시 예산 재계산)이 발동하지 않는다.** 근거·급소·반증은
> [architectures/flink.md](architectures/flink.md) §스트림 소스를 Redpanda에서 Iceberg changelog로 바꾼 근거.
>
> 🔴 **다만 이 변경이 새 충돌을 하나 만들었다** — **스트리밍 잡은 TaskManager가 상시 생존**하므로
> §9-3 **경계 ①**(*"Flink 상주는 JM만 · TM은 온디맨드·수명 46~52초"*)의 **전제가 깨진다.**
> 선택지는 (가) 경계 ① 개정 / (나) **시연 창 안에서만 돌리고 그 자리에서 회수**이며 **기본안은 (나)**.
> ✅ **2026-08-23 실증은 (나)로 진행**했고 회수 후 기준선(`2250m / 3140Mi`)으로 정확히 복귀했다.
> 🔴 **상주 피크가 처음 측정됐다 — `4750m (59%) / 8772Mi (39%)`**(JM + TM + Spark Connect,
> 분모는 노드 Allocatable). **배치 동시 피크(84% / 52%)보다 낮다.** 그럼에도
> **"예산이 통과한다"와 "경계 ①의 전제가 성립한다"는 다른 축**이라
> **규약 개정 여부는 사용자 결정 대기**다([resource-sizing.md](resource-sizing.md) §(C-2)).
> 결정 전까지 스트리밍 상시 운전은 하지 않는다.

- **Plan/Do**: **Redpanda 배포는 하지 않는다.** Iceberg **bronze 테이블을 스트리밍 소스로 읽어**
  **`FlinkDeployment`** 잡이 이벤트타임 윈도우로 **실시간 SOFA/Sepsis-3 조기경보**를 계산해
  Iceberg에 싱크. 체크포인트는 SeaweedFS(S3), 상태 백엔드 RocksDB. Dagster가 잡 수명주기를 관리.
  - 🔴 **소스 테이블은 append-only여야 한다** — Iceberg Flink 스트리밍 읽기는 `IncrementalAppendScan`
    기반이라 **append 스냅샷만** 보고 overwrite·delete 스냅샷은 지원하지 않는다. 따라서
    **`merge into`를 쓰는 dbt 실버 테이블은 소스가 될 수 없고**, 소스는 **bronze append 테이블로 고정**한다.
    (*"`streaming-skip-overwrite-snapshots`로 스킵 가능"* 이라는 서술은 **거짓**이다 —
    `FlinkReadOptions.java` 전문과 공식 read option 22개 대조로 부재 확인.)
  - ✅ **잡 제출 형태는 정해졌고 실행됐다** — ConfigMap SQL + `sql-client.sh -f`
    (`k8s/flink/iceberg-stream-job.yaml`, 배치 선례는 `iceberg-batch-job.yaml`).
    **`FlinkSessionJob` CR은 쓰지 않는다** — `jarURI`가 필수라 SQL 한 장에 jar 빌드·배포
    파이프라인이 딸려온다. 🔴 **apply 순서는 ConfigMap 2종 → FlinkDeployment**(역순이면
    JM이 `CreateContainerConfigError`), **마운트 경로는 배치/스트림을 나눈다**
    (`/opt/flink/sql` · `/opt/flink/sql-stream` — 겹치면 뒤엣것이 앞엣것을 가린다).
  - **`allowed-schemes`는 `local`로 좁혀 둔다**(런타임 외부 jar fetch 차단 — 의존성은 이미지에 굽는다).
- **Check**: 스트림 입력 대비 경보 산출 정확성·지연 관측, 배치(Spark)와 **동시 기동** 시 경계 3개 준수 확인
  (**Flink 상주는 JM만** · **`spark.executor.instances` ≤ 1** · Redpanda 도입 시 경계 재계산)
  ([resource-sizing.md](resource-sizing.md) · [conventions/k8s.md](conventions/k8s.md) §9-3).
  🔴 **경계 ①은 스트리밍 잡에 그대로 적용되지 않는다**(TM 상주) — 위 상태 블록의 (가)/(나) 참조.
  상주분은 **2026-08-23에 측정됐고 규약 개정은 결정 대기**다. 경계 ③은 Redpanda 미도입으로 **발동하지 않는다.**
  🔴 **경보 산출 정확성·지연은 아직 관측 대상이 없다** — 피처 계산 잡이 없기 때문이다(위 §남은 것 ①).
- **Act**: 배치 결과(dbt-spark)와 스트림 결과의 정합(동일 피처 정의) 교차검증. **미착수** —
  양쪽 산출물이 아직 없다(배치는 Phase 2 원천 미확보, 스트림은 피처 잡 미작성).

### Phase 4 — 오케스트레이션 정착 + 문서·컨벤션 확정

- **Plan/Do**: `defs/` 자산·리소스를 Spark/Flink 경로로 재배선(`PipesK8sClient`·`SparkOperatorResource`·Flink 트리거),
  automation 갱신. Dagster **에셋 명시·분리 정의** 컨벤션 유지(팩토리 금지).
- **Check**: `dg check defs`·스모크(`dbt build`) 통과([test.md](test.md)). 에셋 pytest는 실인프라 미접속 격리 유지.
- **Act**: `CLAUDE.md`·`README.md`·`docs/`를 최종 동기화(단일 출처), 상태 마커 🚧→✅.
- **후속 과제**: ① REST 카탈로그(급소②) ② ML/윈도우 피처 계층 ③ (선택) Dagster in-cluster 배포(`K8sRunLauncher`) 비교.

### Phase 5 — 분석 계층 (로드맵의 종착지) ⏸ **원천 데이터 적재 이후**

> Phase 0~4는 **수단**(컴퓨트·스토리지·오케스트레이션)을 세운다. 이 저장소의 **목적은 분석**이므로
> 로드맵은 여기서 끝난다. 규칙 정본은 [conventions/analysis.md](conventions/analysis.md),
> 소비 계층 그림은 [architectures/overview.md](architectures/overview.md).

- **전제**: ① Phase 2(원천 적재)가 끝나 silver 22모델이 **실제 데이터로** 돌아야 한다.
  ② **연구 질문이 먼저 정해져야 한다** — 질문 없이 만든 gold는 재사용되지 않는 집계일 뿐이다.
- **Plan/Do**: ① 연구 질문 확정(예: Sepsis-3 발생률·SOFA 궤적·조기경보 지연) →
  ② 질문별 **gold 마트** 정의(`tags=['gold']`, grain을 `schema.yml` description에 명시) →
  ③ 노트북으로 탐색하고 반복되는 조회를 gold로 승격 → ④ 첫 리포트(`docs/analyses/01-*.md`) 작성.
- **Check(성공 게이트)**:
  - 리포트 1편이 **gold/dbt 모델만 인용**해 수치가 재현된다(노트북 임시 SQL 인용 0건).
  - 해당 gold 모델의 **grain 유니크·범위 테스트 통과**([test.md](test.md) §1).
  - 참조 노트북이 `nbconvert`로 **전 셀 실행 성공**([test.md](test.md) §6).
  - 코호트 **attrition 표**와 **한계 섹션**이 리포트에 있다.
- **Act**: 실사용에서 드러난 규칙의 빈틈을 `conventions/analysis.md`에 되먹인다.
- **연계**: Phase 3(Flink 실시간 경보)의 산출과 배치(dbt-spark) 산출의 **동일 피처 정의 교차검증**이
  여기서 리포트로 닫힌다. Phase 4 후속 과제의 **ML/윈도우 피처 계층**도 이 단계의 연장이다.

> **현재 상태(2026-08-19)**: 규칙(`conventions/analysis.md`)과 실행 환경(호스트 Jupyter Lab +
> Spark Connect)은 서 있고, **gold 0개 · 리포트 0편 · 연구 질문 미정**이다.
> 이 셋 중 **연구 질문이 병목**이며 데이터 부재(Phase 2)와는 별개로 지금 정할 수 있다.

## 5. 리스크·트레이드오프 (정직한 기록)

| 관점 | 평가 | 메모 |
| --- | --- | --- |
| 정확성/학습가치 | ★★★★★ | Spark/Flink 2개 K8s Operator(선언형 CRD)·배치+스트림 시연 = 강한 포트폴리오 신호 |
| 리스크 | ★★★☆☆ | Dagster↔Operator canonical 예제 부재(커스텀 글루) + **dbt-trino→dbt-spark SQL 방언 교정** 필요(Phase 0·1로 방어) |
| 비용 | ★★★☆☆ | 로컬이라 클라우드 비용 0, 단 **단일 PC RAM 압박**(2엔진+SeaweedFS). **8 CPU / 22,888 MiB** VM에서 **동시 기동**으로 수용(실측 피크 CPU 84% / Mem 52%). 🔴 **Redpanda는 미도입 유지로 결정**돼(2026-08-23) 이 축의 증분이 없어졌다. **스트리밍 TM 상주분은 같은 날 실측됐다 — 상주 피크 `4750m (59%) / 8772Mi (39%)` 로 배치 동시 피크보다 낮다.** 다만 §9-3 경계 ① 개정 여부는 **결정 대기**([resource-sizing.md](resource-sizing.md) · [conventions/k8s.md](conventions/k8s.md) §9-3) |
| 효율(개발 루프) | ★★☆☆☆ | in-process 대비 느려짐(이미지 빌드→레지스트리 push→CRD 제출). **의도된 학습 트레이드오프**로 수용 |

- **데이터 규모 대비 Spark/Flink 과함**은 인정하고, 급소①의 분업(대용량 인제스트=Spark, 실시간 경보=Flink)으로 정당성을 확보한다.
- **Trino 제거 비용**: 성숙한 인터랙티브 SQL·`dbt-trino`를 잃는다. ad-hoc 조회 편의가 낮아지고 22모델 방언 교정이 필요하다(수용된 트레이드오프).

## 6. 참고 (공식 문서)

- Apache Spark Kubernetes Operator(GA 1.0.0, Kubeflow에서 이전): https://apache.github.io/spark-kubernetes-operator/ · 릴리스: https://github.com/apache/spark-kubernetes-operator/releases
- Apache Flink Kubernetes Operator: https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-main/
- Dagster Pipes / dagster-k8s(PipesK8sClient): https://docs.dagster.io/api/python-api/libraries/dagster-k8s
- Dagster & Spark: https://docs.dagster.io/integrations/libraries/spark
- dbt-spark 어댑터: https://docs.getdbt.com/docs/core/connect-data-platform/spark-setup
- Spark on Kubernetes: https://spark.apache.org/docs/latest/running-on-kubernetes.html
- Redpanda(Kafka API) — 🔎 **미도입 유지**(2026-08-23, 스트림 소스가 Iceberg로 변경): https://docs.redpanda.com/
- Iceberg Flink 읽기(스트리밍·append 제약): https://iceberg.apache.org/docs/latest/flink-queries/
- Iceberg JDBC 카탈로그: https://iceberg.apache.org/docs/latest/jdbc/ · REST 카탈로그 권고: https://trino.io/docs/current/object-storage/metastores.html
- kind Podman provider: https://kind.sigs.k8s.io/docs/user/rootless/ · 로컬 레지스트리: https://kind.sigs.k8s.io/docs/user/local-registry/
