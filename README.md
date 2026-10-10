# PIPELINE STUDY

성격이 다른 여러 도메인의 데이터셋을 하나의 레이크하우스 패턴으로 적재·변환하고,
재현 가능한 분석 질문까지 연결하는 학습·포트폴리오 프로젝트다.

> 이 문서와 [`docs/`](docs/README.md)는 **저장소를 클론해 자기 환경에서 실행하려는 사람과
> 에이전트**를 대상으로 한다. 열린 항목은 [Issues](https://github.com/Melting-Face/pipeline-study/issues)에 있다.

**파이프라인은 수단이고 분석이 목적**이다. 데이터셋별 질문은 달라도 다음 처리 패턴은
재사용한다.

```text
원천 파일
  → Dagster 적재
  → Iceberg bronze
  → dbt silver/gold
  → 노트북 탐색
  → 분석 리포트
```

| 축 | 하는 일 | 규칙 정본 |
| --- | --- | --- |
| 파이프라인 | S3 → Iceberg 적재, dbt 변환, 오케스트레이션 | [Dagster](docs/conventions/dagster.md) · [dbt](docs/conventions/dbt.md) |
| 분석 | gold 지표·코호트, 노트북 탐색, 리포트 | [분석](docs/conventions/analysis.md) |

데이터셋과 분석 질문은 [데이터셋 스키마·피처](docs/dataset_schema.md), 전체 구성과
데이터 흐름은 [아키텍처 개요](docs/architectures/overview.md)에서 확인할 수 있다.

## 빠른 시작

> **환경 등급**: 이 저장소의 클러스터(kind on Podman, macOS)와 compose 스택은 **dev(개발·검증) 환경**이다.
> 운영(prod) 환경이 아니며, 운영 배포 대상은 아직 없다([OCI k3s](docs/architectures/oci.md)는 보류).

처음 설치한다면 [환경 세팅](docs/setup.md)을 위에서 아래로 따른다. 도구 설치부터 클러스터 기동·수렴 확인·
회수까지의 명령이 모두 거기 있다(클러스터 기동 명령은 [§3](docs/setup.md#3-로컬-kubernetes)).

클러스터는 **Terraform 스택 A**, ingress-nginx·ArgoCD는 **스택 B**가 만들고, 오퍼레이터 이하는
**ArgoCD가 `gitops/charts/`에서 수렴**시킨다(`git push`로 바뀐다). 데이터 층(스토리지·카탈로그)은 아직 이전 전이라
지금은 오퍼레이터까지만 뜬다 — 범위는 [환경 세팅 §지금 되는 범위](docs/setup.md#지금-되는-범위).
Dagster는 클러스터에 배포하지 않고 호스트에서 돌린다.

> 이 프로젝트의 로컬 컨테이너 런타임은 `podman`이다. 문서에 남아 있는
> `docker compose` 예시는 `podman compose`로 실행한다.

## 주요 작업

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
uv run dg check defs
uv run pytest
```

테스트 계층과 각 성공 신호가 보증하는 범위는 [테스트 규약](docs/test.md)에 있다.

## 문서 지도

| 문서 | 언제 읽는가 |
| --- | --- |
| [문서 홈](docs/README.md) | 전체 문서 목차와 처음 읽는 순서 |
| [환경 세팅](docs/setup.md) | 최초 설치, 기동·접속·회수, 자주 만나는 함정 |
| [아키텍처 개요](docs/architectures/overview.md) | 서비스 위치, 데이터 흐름, 컴퓨트·스토리지 관계 |
| [재설계](docs/redesign.md) | Kubernetes 단일 플랫폼 목표와 단계별 게이트 |
| [코딩 컨벤션](docs/conventions/README.md) | Python·Dagster·dbt·Docker·Kubernetes·Terraform 규칙 |
| [운영](docs/operations.md) | 환경변수 전파, 보존 정책, 운영 절차 |
| [보안·거버넌스](docs/security.md) | 원천 데이터·비밀정보·DUA·공개 통제 |
| [참고 문서](docs/references.md) | 설계와 규칙이 근거로 삼는 외부 1차 문서 |

문서는 한국어로 작성하고 식별자·명령어·경로는 원문 그대로 표기한다. 규칙을 바꿀
때는 [문서 동기화 규약](docs/doc-sync.md)에 따라 정본과 요약 문서를 함께 갱신한다.

## AI 에이전트 작업 방식

Claude Code와 Codex는 같은 전문 역할 체계를 사용하되 런타임 설정은 분리한다.
메인 에이전트가 작업을 분해하고, 구현·검증·보안·기록 역할은 필요한 경우에만 배정한다.

- 공통 역할·게이트: [에이전트 오케스트레이션](docs/conventions/agents.md)
- Codex 설정 차이: [Codex 에이전트 구성](docs/conventions/codex.md)
- Claude Code 요약: [`CLAUDE.md`](CLAUDE.md)
- Codex 요약: [`AGENTS.md`](AGENTS.md)

커밋·푸시·배포·외부 발행 같은 비가역 작업은 사용자가 최종 승인한다.
