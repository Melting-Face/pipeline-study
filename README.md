# PIPELINE STUDY

성격이 다른 여러 도메인의 데이터셋을 하나의 레이크하우스 패턴으로 적재·변환하고,
재현 가능한 분석 질문까지 연결하는 학습·포트폴리오 프로젝트다. **파이프라인은 수단이고 분석이 목적**이다.

> 이 문서와 [`docs/`](docs/README.md)의 독자는 둘이다 — **위층은 검토자**(무엇을 왜 만들었나를 몇 분에),
> **아래층·`docs/`는 학습자**(클론해 자기 환경에서 돌리고 고친다). 위층은 「빠른 시작」 앞까지다.

```text
원천 파일
  → Dagster 적재
  → Iceberg bronze
  → dbt silver/gold
  → 노트북 탐색
  → 분석 리포트
```

## 스택

| 기술 | 역할 한 줄 | 상세 |
| --- | --- | --- |
| Dagster | 호스트에서 도는 오케스트레이터 — 적재 자산과 dbt 자산을 정의·실행한다 | [dagster](docs/architectures/dagster.md) |
| Airflow | 클러스터 안 오케스트레이터 — Dagster에서 이관한 DAG를 실행한다 | [airflow](docs/conventions/airflow.md) |
| dbt · Trino | silver/gold 변환 — dbt가 k8s Trino에서 SQL을 실행하고, Trino는 Iceberg 유지보수도 맡는다 | [trino](docs/architectures/trino.md) |
| Iceberg · CNPG | 테이블 포맷과 JDBC 카탈로그 — 모든 엔진이 카탈로그 `iceberg` 하나를 공유한다 | [cnpg](docs/conventions/k8s/cnpg.md) |
| SeaweedFS | S3 호환 오브젝트 스토리지 — 클러스터 밖 컨테이너(Terraform 스택 A) | [storage](docs/architectures/storage.md) |
| Spark | 대용량 배치 엔진 — Spark Operator가 `SparkApplication`을 실행한다 | [spark](docs/architectures/spark.md) |
| Flink | 스트림 엔진 PoC — Iceberg 스트리밍 읽기, 세션 클러스터는 쓸 때만 띄운다 | [flink](docs/architectures/flink.md) |
| Kubernetes | 로컬 단일 노드 플랫폼 — kind on Podman | [k8s](docs/architectures/k8s.md) |
| ArgoCD | `gitops/`의 선언으로 클러스터 앱을 수렴시킨다 | [argocd](docs/architectures/argocd.md) |
| Terraform | 스택 A(클러스터·S3) · 스택 B(ingress·ArgoCD) | [terraform](docs/architectures/terraform.md) |
| Prometheus | 클러스터 안 메트릭 수집 | [monitoring](docs/architectures/monitoring.md) |

## 핵심 판단

- Trino 제거 결정을 번복하고 dbt 엔진으로 유지했다 — [재설계 「Trino 제거 결정 번복」](docs/redesign.md#trino-제거-결정-번복)
- S3를 클러스터 밖에 뒀다 — 재생성 때 레이크가 같이 사라지지 않게 — [재설계 「그 외 결정」](docs/redesign.md#그-외-결정)
- 카탈로그 Postgres를 CNPG로 관리한다 — 재기동만으로 카탈로그가 소멸한 뒤 — [재설계 「그 외 결정」](docs/redesign.md#그-외-결정)
- 엔진 버전은 최신이 아니라 Iceberg가 지원하는 짝으로 고정한다 —
  [Spark](docs/architectures/spark.md) · [Flink](docs/architectures/flink.md)

## 교훈 하이라이트

- [엔진마다 값이 갈리는 SQL](wiki/cross-engine-sql.md) — "양쪽에서 돈다"와 "양쪽에서 같은 값이 나온다"는 다른 기준이다.
- [Dagster 오케스트레이션](wiki/dagster-orchestration.md) — Dagster에서 가장 무서운 실패는 예외가 아니라 침묵이다.
- [kubectl로 판정하기](wiki/kubectl-judgement.md) — 부정 결과는 관측 경로가 살아 있었을 때만 유효하다.

## 빠른 시작

> **환경 등급**: 이 저장소의 클러스터(kind on Podman, macOS)와 compose 스택은 **dev(개발·검증) 환경**이다.
> 운영(prod) 환경이 아니며, 운영 배포 대상은 아직 없다([OCI k3s](docs/architectures/oci.md)는 보류).

**전제**([환경 세팅 「검증 환경」](docs/setup.md#검증-환경)):

- 호스트: macOS · Apple Silicon(arm64)
- 컨테이너 런타임: podman 5.x · `podman machine`(applehv VM) · **rootful**
- VM 크기: 8 CPU · 26702 MiB · 93 GiB

명령은 저장소 **루트 체크아웃**에서 실행한다. 도구 설치·`.env`·스택 A 변수 준비·내리기 등 상세는
[환경 세팅](docs/setup.md)(클러스터는 [§3](docs/setup.md#3-로컬-kubernetes))을 위에서 아래로 따른다.

```shell
git clone https://github.com/Melting-Face/pipeline-study.git && cd pipeline-study
cp .env.example .env

scripts/k8s-up.sh
terraform -chdir=terraform/cluster/kind init                # 클러스터 + SeaweedFS 컨테이너 + 버킷
terraform -chdir=terraform/cluster/kind apply
export KUBECONFIG=~/.kube/lakehouse.config
./scripts/k8s-secrets.sh                  # 앱보다 먼저 — Airflow Secret 8종 포함
terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform apply   # 기본값: kind-lakehouse · 8080 · target_revision=main

kubectl get applications -n argocd        # 전부 Synced · Healthy 면 수렴
```

클러스터는 **Terraform 스택 A**, ingress-nginx·ArgoCD는 **스택 B**가 만들고, 오퍼레이터 이하는
**ArgoCD가 `gitops/charts/`에서 수렴**시킨다(`git push`로 바뀐다). 데이터 층(카탈로그 CNPG·Trino)도
ArgoCD 앱이고, S3(SeaweedFS)만 스택 A의 컨테이너다 — 절별 전제는 [환경 세팅 「절별 전제」](docs/setup.md#절별-전제).
Dagster는 클러스터에 배포하지 않고 호스트에서 돌린다.

웹 UI(ArgoCD·Airflow·Trino·Flink·Prometheus·Dagster) 주소와 로그인 방법은
[환경 세팅 §3-1 접근 경로](docs/setup.md#3-1-접근-경로)에 모아 두었다.

> 이 프로젝트의 로컬 컨테이너 런타임은 `podman`이다. 문서에 남아 있는
> `docker compose` 예시는 `podman compose`로 실행한다.

## 주요 작업

데이터셋별 질문은 달라도 위 처리 패턴은 재사용한다.

| 축 | 하는 일 | 규칙 정본 |
| --- | --- | --- |
| 파이프라인 | S3 → Iceberg 적재, dbt 변환, 오케스트레이션 | [Dagster](docs/conventions/dagster.md) · [dbt](docs/conventions/dbt.md) |
| 분석 | gold 지표·코호트, 노트북 탐색, 리포트 | [분석](docs/conventions/analysis.md) |

데이터셋과 분석 질문은 [데이터셋 스키마·피처](docs/dataset_schema.md), 전체 구성과
데이터 흐름은 [아키텍처 개요](docs/architectures/overview.md)에서 확인할 수 있다.

### dbt 모델 작업

모델은 `dagster/dockerfile.d/src/dbt_pipelines/models/<dataset>/` 아래에 둔다.
레이어·네이밍·`source()`·`fqn:` 셀렉터 규칙은 [dbt 컨벤션](docs/conventions/dbt.md),
실행 전제는 [환경 세팅 §8](docs/setup.md#8-문제-해결)을 따른다.

```shell
cd dagster/dockerfile.d/src
uv run dbt build --project-dir dbt_pipelines --profiles-dir dbt_pipelines
```

### 노트북과 분석

탐색은 Dagster와 같은 가상환경의 Jupyter Lab을 사용한다. 실행법은
[노트북 안내](notebooks/README.md), 결론 수치와 리포트 규칙은
[분석 컨벤션](docs/conventions/analysis.md)이 정본이다.

```shell
cd dagster/dockerfile.d/src
uv run --group notebook jupyter lab --port 8889 --notebook-dir ../../../notebooks
```

분석 리포트는 `docs/analyses/`에 두며, 작성 규약은
[분석 리포트 안내](docs/analyses/README.md)를 따른다.

### 검사

```shell
pre-commit run --all-files

cd dagster/dockerfile.d/src
DAGSTER_HOME="$(mktemp -d)" uv run --env-file ../../../.env dg check defs   # .env 필요
uv run pytest
```

테스트 계층과 각 성공 신호가 보증하는 범위는 [테스트 규약](docs/test.md)에 있다.

## 문서 지도

[문서 홈](docs/README.md)은 독자별 4칸으로 나뉜다.

| 칸 | 문서 | 언제 읽는가 |
| --- | --- | --- |
| 둘러보기 | [아키텍처 개요](docs/architectures/overview.md) | 서비스 위치, 데이터 흐름, 컴퓨트·스토리지 관계 |
| 따라 하기 | [환경 세팅](docs/setup.md) | 최초 설치, 기동·접속·회수, 자주 만나는 함정 |
| 따라 하기 | [운영](docs/operations.md) | 환경변수 전파, 보존 정책, 운영 절차 |
| 기여자·에이전트용 규칙 | [코딩 컨벤션](docs/conventions/README.md) | Python·Dagster·dbt·Docker·Kubernetes·Terraform 규칙 |
| 기여자·에이전트용 규칙 | [보안·거버넌스](docs/security.md) | 원천 데이터·비밀정보·DUA·공개 통제 |
| 기여자·에이전트용 규칙 | [참고 문서](docs/references.md) | 설계와 규칙이 근거로 삼는 외부 1차 문서 |
| 결정 기록 | [재설계](docs/redesign.md) | Kubernetes 단일 플랫폼 목표와 단계별 게이트 |

열린 항목은 [Issues](https://github.com/Melting-Face/pipeline-study/issues)에 있다.
문서는 한국어로 작성하고 식별자·명령어·경로는 원문 그대로 표기한다. 규칙을 바꿀
때는 [문서 동기화 규약](docs/doc-sync.md)에 따라 정본과 요약 문서를 함께 갱신한다.

## AI 에이전트 작업 방식

Claude Code와 Codex는 같은 전문 역할 체계를 쓰고, 메인 에이전트가 작업을 분해해 역할을 배정한다.
비가역 작업(커밋·푸시·배포·외부 발행)은 사용자가 최종 승인한다.
상세: [에이전트 오케스트레이션](docs/conventions/agents.md) · [Codex 구성](docs/conventions/codex.md) ·
[`CLAUDE.md`](CLAUDE.md) · [`AGENTS.md`](AGENTS.md)
