# Dagster (아키텍처 · 프로젝트 관점)

## 개요

Dagster는 **자산(asset) 중심 오케스트레이터**다. "어떤 태스크를 어떤 순서로"가 아니라
"어떤 데이터 자산이 무엇에 의존하는가"를 선언하고, 그 그래프에서 실행 계획을 유도한다.
프로세스는 셋으로 나뉜다 — **webserver**(UI·GraphQL) / **daemon**(스케줄·센서·런큐) /
**code server**(사용자 코드 로드). 셋은 같은 **instance**(메타 스토리지)를 공유해야 서로를 본다.

- 이 저장소 고정 버전: **dagster 1.12.12 / dagster-* 0.28.12**

## 이 프로젝트에서의 위치 — ✅ 채택 (in-cluster)

**오케스트레이터도 클러스터 워크로드다**. webserver·daemon을 kind 클러스터
`lakehouse`의 Deployment 2개로 두고 UI는 `http://dagster.localtest.me:8080` Ingress로 낸다.
Spark·Flink·SeaweedFS·CNPG와 **같은 층**에 놓여, 컨트롤 플레인만 다른 실행 환경에 있던 상태가 끝났다.

### 무엇이 바뀌었나 — 우회 경로의 소멸

호스트 Dagster 시절에는 클러스터에 닿기 위해 **port-forward 2개**(Spark Connect 15002 ·
SeaweedFS 18333)와 **TLS Ingress + 로컬 CA 신뢰 주입**(`GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`)이 필요했다.
in-cluster에서는 이 전부가 **서비스 DNS 직결**로 대체된다.

| 축 | 호스트 Dagster | in-cluster |
| --- | --- | --- |
| Spark Connect | `sc://spark-grpc.localtest.me:8443/;use_ssl=true` + CA 파일 | `sc://spark-connect:15002` (평문) |
| S3 | `http://localhost:18333` (port-forward) | `http://seaweedfs:8333` |
| 카탈로그 PG | `localhost:15432` (port-forward) | `catalog-postgres-rw:5432` |
| 메타 PG | compose `postgres` | 같은 CNPG의 `dagster` DB |

평문 gRPC가 된 것은 **보안이 낮아진 게 아니라 구간이 사라진 것**이다 — TLS는 호스트↔클러스터
구간의 통제였고 in-cluster에는 그 구간 자체가 없다.

### 대안 비교

| 선택지 | 판정 | 이유 |
| --- | --- | --- |
| **raw 매니페스트** | ✅ 채택 | 이 저장소의 워크로드 층 패턴(Spark Connect·SeaweedFS·CNPG Cluster 모두 raw). `resources`·probe·관측 주석을 직접 통제해 예산표가 거짓이 될 여지가 없다 |
| 공식 Helm 차트 `dagster/dagster` | 🔎 미채택 | 업스트림 표준이지만 전제가 어긋난다 — 아래 참조 |
| `DefaultRunLauncher` | ✅ 채택 | run이 daemon 파드의 서브프로세스라 자원 상한이 **파드 하나로 예측 가능**하다 |
| `K8sRunLauncher`(run당 Pod) | 🔎 후속 | 학습 가치가 크지만 일시 파드가 Spark와 CPU를 다퉈 경계를 다시 짜야 한다 |
| 전용 gRPC 코드서버(3-way) | 🔎 후속 | "Terminate run"이 크로스 파드로 닿게 되지만 파드가 하나 더 늘어난다 |

**Helm 차트를 안 쓴 이유**는 품질이 아니라 **전제**다. 차트는 `K8sRunLauncher` + gRPC 코드서버 +
자체 PostgreSQL subchart를 전제로 설계돼 있어, 이 프로젝트가 고른 셋(DefaultRunLauncher · 임베디드
코드서버 · 기존 CNPG 재사용)을 **전부 override**해야 한다. 게다가 `resources`를 명시하지 않으면
BestEffort로 떠 예산표가 처음부터 거짓이 되는데, 그 함정은 Flink Operator에서 이미 겪었다.
차트의 override 키 이름은 **미확인**이다 — raw를 택했으므로 확인할 필요가 없어졌다.

**오퍼레이터는 없다.** Spark·Flink·CNPG는 오퍼레이터를 Helm으로 설치하고 워크로드는 raw YAML로 두는데,
Dagster에는 오퍼레이터라는 층이 아예 없어 **워크로드 축 하나**로만 존재한다.

## 운영 메모

### run 고아 — `run_monitoring`을 켤 수 없다

`DefaultRunLauncher`는 `supports_check_run_worker_health`가 `False`라 `run_monitoring`을 켜면
`NotImplementedError`다. 결과로 **daemon 파드가 재시작하면 진행 중이던 run이 `STARTED`로 고아가 된다.**
부재가 *결정*이며, 이것이 `K8sRunLauncher` 후속 과제의 첫 근거다.

### 성공한 run이 관측 실패를 가린다

첫 in-cluster 실행에서 자산은 `RUN_SUCCESS`인데 **compute log의 S3 업로드가 실패**하고 있었다.
Dagster는 compute log 업로드 예외를 삼키므로 **run 상태만 보면 알 수 없다** — 버킷을 직접 봐야 드러난다.
⇒ 관측 경로는 "run이 성공했다"가 아니라 **"로그가 목적지에 도착했다"** 로 확인한다.

### 이미지 — 베이스 버전이 dbt/pyspark에 묶인다

`python:3.13-slim`으로는 **빌드가 통째로 실패**한다. `pyspark[connect]<3.6`이 `numpy<2`를 요구하는데
numpy 1.26.x에는 cp313 휠이 없어 소스 빌드로 떨어지고 `-slim`에 컴파일러가 없다. 베이스는 **3.12**이고,
올리려면 **pyspark의 numpy 상한이 먼저 풀려야 한다**(베이스만 올리면 다시 깨진다).

### `.dockerignore`는 조용히 죽는다

패턴은 **빌드 컨텍스트 루트 기준**인데 이 저장소의 코드는 전부 `src/` 아래에 있다.
그래서 `.venv/`·`logs/`·`storage/` 같은 접두어 없는 패턴은 **아무것도 매칭하지 않았고**,
호스트 venv 1.2GB가 이미지에 들어가 있었다(3.64GB → 수정 후 1.37GB).
규칙이 *있다*는 것과 *매칭된다*는 것은 다른 축이다 — 고친 뒤에는 **이미지 크기로 효과를 확인**한다.

### 두 DB, 두 계정

`POSTGRES_*`(메타)와 `ICEBERG_CATALOG_*`(카탈로그)는 in-cluster에서 처음으로 갈린다.
`common/constants.py`가 후자 미지정 시 전자로 **폴백**하므로 빠뜨리면 **접속은 되고 접근에서 거부**된다.
규칙 정본은 [conventions/dagster.md](../conventions/dagster.md) §K8s in-cluster 배포.

## 참고

외부 공식 문서는 [`../references.md`](../references.md)에 단일 관리한다 — URL을 여기 복제하지 않는다.
이 문서와 직접 관련된 항목: Dagster 배포(OSS) · `dagster.yaml` · dagster-aws(S3 compute log) ·
dagster-k8s · CloudNativePG.
