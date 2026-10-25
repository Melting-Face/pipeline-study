# 아키텍처 문서 (architectures)

이 프로젝트의 전체 스택과, 각 처리·배포 기술을 **프로젝트 결정 관점**으로 정리한다.
채택 ✅ / 채택·이행중(PoC 게이트) 🚧 / 미채택(참고·향후 검토) 🔎로 표기한다.

> **재설계 진행중**: 호스트 Dagster + K8s(Spark Operator)로의 이행 로드맵은 [../redesign.md](../redesign.md).

## 목차

| 문서 | 상태 | 역할 | 다루는 결정 |
| --- | --- | --- | --- |
| [overview.md](overview.md) | 🚧 | 현행 배치와 데이터 흐름 | 현행 스택 스냅샷·데이터 흐름(Dagster·dbt·Iceberg·SeaweedFS) — Trino는 dbt 엔진으로 유지(제거 번복). 현행 배치 그림·구성 요소·bronze 적재 템플릿 |
| [docker.md](docker.md) | ✅ | 호스트 보조 서비스 — `compose.yml`의 `profiles`로 전부 opt-in 기동한다 | 컨테이너·compose 배포(채택) |
| [argocd.md](argocd.md) | ✅ | `gitops/`의 선언으로 클러스터 앱을 수렴시킨다 | GitOps 컨트롤러 — **`git push`로 클러스터가 수렴**. ApplicationSet(git files) + RollingSync를 고른 이유와 기각한 대안, 데이터 보호 4겹 |
| [dagster.md](dagster.md) | ✅ | 호스트에서 도는 오케스트레이터 — 적재 자산과 dbt 자산을 정의·실행한다 | 오케스트레이터 — 배치 결정 기록(in-cluster 배포는 ArgoCD 전환으로 철거, 현행은 호스트 실행). raw 매니페스트 · `DefaultRunLauncher` 유지 이유와 대안 비교 |
| [airflow.md](airflow.md) | 🚧 | 클러스터 안 오케스트레이터 — Dagster에서 이관한 DAG를 실행한다 | 오케스트레이터 — 클러스터 안 정기 실행 주체. umbrella 차트 · `CeleryExecutor,KubernetesExecutor` · 메타 DB는 CNPG 별도 롤·DB |
| [spark.md](spark.md) | 🚧 | 대용량 배치 엔진 — Spark Operator가 `SparkApplication`을 실행한다 | 배치 엔진 — 대용량 인제스트 · Iceberg 쓰기(dbt 엔진은 Trino). 버전은 **최신이 아니라 Iceberg가 지원하는 짝**으로 고른다 |
| [flink.md](flink.md) | 🚧 | 스트림 엔진 PoC — Iceberg 스트리밍 읽기, 세션 클러스터는 쓸 때만 띄운다 | 스트림 엔진 — 실시간 조기경보. Iceberg bronze 스트리밍 읽기를 소스로 쓴다 |
| [k8s.md](k8s.md) | 🚧 | 로컬 단일 노드 플랫폼 — kind on Podman | 컨테이너 오케스트레이션 — 컴퓨트·데이터 서비스 이전(이행중) |
| [operators.md](operators.md) | ✅ | Spark·Flink·CNPG 오퍼레이터 — CR 한 장으로 엔진과 카탈로그 DB를 선언한다 | 오퍼레이터 패턴 3종(Spark·Flink·PostgreSQL) — **왜 셋 중 둘만 전용 이미지를 굽는가**. 판단축은 오퍼레이터가 아니라 런타임의 확장 적재 방식이다 |
| [storage.md](storage.md) | ✅ | S3 호환 오브젝트 스토리지 — 클러스터 밖 컨테이너(Terraform 스택 A) | 오브젝트 스토리지 — 현행 **SeaweedFS**. 교체 검토의 동기는 **정확성 축 하나**이고, 후보 판정 수단은 **실측 프로브뿐**이다 |
| [terraform.md](terraform.md) | ✅ | 스택 A(클러스터·S3) · 스택 B(ingress·ArgoCD) | IaC — 스택 A(cluster)·B(platform)가 ArgoCD까지 설치. 분할 축은 부트스트랩이 아니라 **폭발반경**이다 |
| [oci.md](oci.md) | 🔎 | 쓰지 않는다(보류) — 대신 로컬 kind on Podman | 클라우드 이행 — OCI Always Free A1(ARM) + Terraform + k3s(학습·확장 경로) |
| [trino.md](trino.md) | 🚧 | silver/gold 변환 — dbt가 k8s Trino에서 SQL을 실행하고, Trino는 Iceberg 유지보수도 맡는다 | MPP SQL 엔진 — dbt 엔진으로 **K8s upstream 앱**(재설계의 제거 결정 번복). compose Trino는 철거 |
| [monitoring.md](monitoring.md) | ✅ | 클러스터 안 메트릭 수집 | 모니터링·관측 — 클러스터 안 Prometheus(server 단독), Grafana·Loki·Robusta 등을 **지금 쓰지 않는 이유** |
| [kafka.md](kafka.md) | 🔎 | 쓰지 않는다 — 대신 Iceberg bronze 스트리밍 읽기가 스트림 소스다 | 이벤트 스트리밍 — **미도입 유지**(스트림 소스가 Iceberg로 대체). 근거 인덱스 + 재검토 트리거 |
| [duckdb.md](duckdb.md) | 🔎 | 쓰지 않는다 — 대신 조회 엔진은 k8s Trino | 인프로세스 OLAP — **미채택**. 선언은 남아 있었고 실행 경로가 없었다 |

> **그림**: 위 문서들이 산문으로 기술하는 구조를 한 화면으로 보려면 [diagrams/](diagrams/) —
> archify JSON 스펙 3종(적재 파이프라인 · 로컬 K8s 배치 · AI 세션 오케스트레이션).
> HTML은 생성물이라 커밋하지 않고, 재생성 명령과 **수치의 재검토 트리거**는 그 디렉터리의 README에 둔다.

## 각 문서 형식

역할을 결정보다 먼저 둔다 — 독자가 "이게 뭘 하나"를 안 뒤에 "왜 골랐나"를 읽게 한다.

```text
# <기술>
## 이 프로젝트에서의 역할     ← 첫 줄 = 위 목차 「역할」 열 문장, 3줄 이내
## 어떻게 도나               ← 관련 파일 경로 · 흐름 · 다른 스택과의 연결(채택 문서만)
## 개요                      ← 기술 일반 소개
## 왜 이걸 골랐나            ← 채택 이유·대안 비교(제목 뒤에 상태 표기를 붙인다)
## 운영 메모
## 교훈                      ← 관련 wiki 노트 링크(있을 때만)
## 참고                      ← 공식 문서
```

- 미채택 문서(🔎)는 「어떻게 도나」를 두지 않는다. 「역할」 첫 줄을 "쓰지 않는다 — 대신 X"로 쓴다.
- 역할 문장은 목차 표·[문서 허브](../README.md)·루트 `README.md` 스택 표와 **글자 그대로 같다**. 바꿀 때는 넷을 함께 고친다.

> 배포·운영 **규칙**은 [conventions/docker.md](../conventions/docker.md)·[conventions/k8s.md](../conventions/k8s.md),
> 자원 **수치**는 [resource-sizing.md](../resource-sizing.md)에서 단일 관리한다.
