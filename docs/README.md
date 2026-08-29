# pipeline-study 문서

이 저장소는 **파이프라인(수단) + 분석(목적)** 두 축이다. 도메인이 다른 여러 데이터셋을
같은 레이크하우스 패턴으로 적재·변환하는 것은 **데이터셋별 질문에 답하기 위한 준비**이고,
답을 내는 규칙은 [분석 컨벤션](conventions/analysis.md)에 있다.

> **누가 읽는가 — 이 저장소를 가져가 돌리려는 사람과 에이전트**
> `README.md`와 `docs/`는 저장소를 **클론해 자기 환경에서 실행하려는 독자**를 대상으로 한다.
> 그래서 여기 있는 문장은 **남의 환경에서도 참**이어야 한다 — 호스트 절대경로·개인 머신
> 스펙·계정명은 담지 않는다. 저장소를 **모르는 사람**이 읽는 [`posts/`](posts/README.md)와
> `wiki/`는 독자도 반출 통제도 다르다([publishing](conventions/publishing.md)).

## 처음 왔다면

1. [**환경 세팅**](setup.md) — 사전 요구 도구부터 검증·회수까지. 절차의 정본이다
2. [전체 아키텍처 / 데이터 흐름](architectures/overview.md) — 무엇이 어디서 도는가
3. [코딩 철학](philosophy.md) — 왜 이렇게 결정했는가

## 아키텍처

기술별 **개요 + 결정 관점**(채택 이유·대안 비교). 인덱스는 [architectures/](architectures/README.md).

| 문서 | 내용 |
| --- | --- |
| [재설계 로드맵](redesign.md) | K8s로의 이행. 목표 토폴로지·급소·PDCA |
| [전체 아키텍처](architectures/overview.md) | Dagster · dbt · Iceberg · SeaweedFS 스택과 bronze 적재 템플릿 |
| [Dagster](architectures/dagster.md) | 오케스트레이터 — in-cluster 배포 결정과 대안 비교 |
| [Docker/Compose](architectures/docker.md) | 현행 채택 |
| [Spark](architectures/spark.md) · [Flink](architectures/flink.md) | 재설계 컴퓨트 |
| [Kubernetes](architectures/k8s.md) | 로컬 K8s 플랫폼 (kind on Podman) |
| [Terraform](architectures/terraform.md) | IaC — 로컬 K8s 플랫폼 이행, 폭발반경 기준 스택 분할 |
| [OCI + Terraform + k3s](architectures/oci.md) | 클라우드 이행 경로 |
| [Trino](architectures/trino.md) | 현행까지 채택, 재설계로 제거 |
| [모니터링·관측](architectures/monitoring.md) | Grafana·Loki·Robusta 등을 **지금 쓰지 않는 이유** |

## 코딩 규칙 (conventions)

인덱스와 읽는 순서는 [conventions/README.md](conventions/README.md).

| 문서 | 내용 |
| --- | --- |
| [공통](conventions/general.md) | 언어·들여쓰기·커밋 메시지·릴리스·pre-commit·**문서 작성 규약** |
| [Git 워크플로](conventions/git.md) | 브랜치·커밋 단위·**병렬 세션 worktree** |
| [Python](conventions/python.md) | ruff·타입 힌트·예외·의존성·스크립트 절차형 |
| [Dagster](conventions/dagster.md) | 함수형 에셋 정의·메타데이터·잡·스케줄 · **K8s in-cluster 배포** |
| [dbt](conventions/dbt.md) | 레이어링·네이밍·테스트·sqlfluff·**방언 흡수** |
| [분석](conventions/analysis.md) | gold / 노트북 / 리포트 **3층 분리**와 배치 기준 |
| [외부 공개](conventions/publishing.md) | 공개는 커밋보다 강한 기준. 소규모 셀 마스킹·DUA·출처 등급 |
| [타임존](conventions/timezone.md) | 저장 UTC / 표시·스케줄 KST |
| [테스트](test.md) | 테스트 계층과 우선순위 |
| [Docker](conventions/docker.md) | 앵커·태그 고정·healthcheck·`deploy.resources`·profiles |
| [관측·모니터링](conventions/monitoring.md) | 서비스 추가 시 **관측 수단 등록 의무** |
| [Kubernetes](conventions/k8s.md) | 워크로드·requests/limits·probe·RBAC·Operator·Ingress |
| [CNPG Postgres](conventions/k8s/cnpg.md) | 카탈로그·메타 DB · 선언 롤 · 백업 · PVC 제약 |
| [Terraform/IaC](conventions/terraform.md) | 스택 구조·버전 고정·state 커밋 금지 |
| [에이전트 오케스트레이션](conventions/agents.md) | AI 세션 2계층·권한·게이트·저널 |
| [Codex 에이전트 구성](conventions/codex.md) | Claude와 분리된 Codex 지침·워커·권한·hook·스킬 운영 |

## 데이터셋 · 분석

- [데이터셋 스키마·피처](dataset_schema.md) — MIMIC-IV·eICU 원천 스키마와 SOFA→Sepsis-3 매핑
- [분석 컨벤션](conventions/analysis.md) — 규칙 정본
- [`notebooks/README.md`](../notebooks/README.md) — Jupyter Lab 실행·Spark Connect 접속·셀 출력 통제
- 리포트는 `docs/analyses/<NN>-<slug>.md`에 쌓는다

## 외부 공개

같은 결론이라도 **독자가 다르면 기준이 다르다.** `analyses/`는 저장소를 **아는 사람**이,
`posts/`는 **모르는 사람**이 읽는다. 공개는 커밋보다 강한 기준이고, **발행은 사람이 한다.**

- [외부 공개 컨벤션](conventions/publishing.md) — 규칙 정본
- [`docs/posts/README.md`](posts/README.md) — 공개 산출물 디렉터리 규약

## 운영 · 보안 · 도구

| 문서 | 내용 |
| --- | --- |
| [환경 세팅](setup.md) | 절차 정본 — 사전 요구·기동·검증·회수·함정 |
| [환경변수·운영 정책](operations.md) | `.env`→compose→`EnvVar` 전파 체인, 보존 정책, 토큰 비용 계측, 클러스터 재생성 |
| [리소스 산정](resource-sizing.md) | 호스트 자원에 따른 서비스 옵션 조정 |
| [보안·데이터 거버넌스 **정책**](security.md) | ISMS-P·의료데이터 규제 매핑, 통제 방침과 보증 범위 |
| [Agent Skills](skills.md) | Claude/Codex 공용 스킬 카탈로그와 통제 규칙 (허브) |
| ↳ [출처 등급·통제](skills/sourcing.md) | A~D 등급 정의·C등급 통제·lock 관리의 **정본** |
| ↳ [C등급 단서](skills/caveats.md) | 등재의 **조건**이 되는 단서 원문 |
| ↳ [배선 메커니즘](skills/wiring.md) | `tools:`/`disallowedTools`/`skills:` 프로브·프리로드 자격 |
| ↳ 인벤토리 실측 | **볼트로 이관** — `$OBSIDIAN_VAULT/status/skills-inventory.md`(스냅샷·비공개) |

> **보안 실태는 이 저장소에 없다.** 저장소가 공개이고 이 경로가 GitHub Security Policy 페이지의
> 탐색 대상(`.github/` → 루트 → `docs/`)이라, 미비점 목록이 첫 화면이 될 수 있다.
> 현행 실태는 `$OBSIDIAN_VAULT/security/posture.md`에 둔다.

## 핵심 원칙 요약

> 가치(왜)는 [코딩 철학](philosophy.md), 아래는 빠른 규칙 참조(어떻게).

1. **주석은 한국어, 식별자는 영어**
2. **들여쓰기 스페이스 4칸** — `.tf`는 `terraform fmt`의 2-space 예외
3. **포매터/린터 고정** — Python `ruff` · SQL `sqlfluff` · Terraform `terraform fmt` · 문서 `doc_lint.py`
4. **커밋 메시지는 한국어 `type: 설명`**
5. 🔴 **성공 신호를 의심한다** — "통과"가 *검사했다*인지 *실행됐다*뿐인지 구분한다

## 문서 작성·유지 규칙

- 규칙·결정·작업 패턴은 최대한 문서로 남긴다. 문서는 한국어로 쓰고 식별자·명령어·경로는 원문 그대로.
- 공통 규칙을 바꿀 때는 `AGENTS.md`·`CLAUDE.md`·`docs/`·`README.md`를 **함께 갱신**해 단일 출처를 유지한다.
- 정량 상한과 시제 축은 [공통 규칙](conventions/general.md) §문서 작성 규약이 정본이다.

상세 인덱스:

- [문서 동기화(doc-sync)](doc-sync.md) — 단일 출처 원칙, 변경 유형별 동기화 체인
- [참고 문서(references)](references.md) — 규칙·설계가 근거로 삼는 외부 표준 인덱스
