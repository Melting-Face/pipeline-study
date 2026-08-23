# pipeline-study 문서

이 프로젝트의 아키텍처와 코딩 규칙을 정리한 문서 모음입니다.
(GitHub Wiki로 이식 가능하도록 평면 구조로 작성)

이 저장소는 **파이프라인(수단) + 분석(목적)** 두 축으로 굴러갑니다. 중환자 데이터를 레이크하우스로
적재·변환하는 것은 **SOFA → Sepsis-3 같은 임상 질문에 답하기 위한 준비**이고, 그 답을 내는 규칙은
[분석 컨벤션](conventions/analysis.md)에 있습니다.

## 목차

### 아키텍처 ([architectures/](architectures/README.md))

기술별 **개요 + 프로젝트 결정 관점**(채택 이유·대안 비교). 채택 ✅ / 채택·이행중 🚧 / 미채택·향후 🔎.

- **[재설계 로드맵](redesign.md) 🚧 — 호스트 Dagster + K8s(kind on Podman)로의 이행. 컴퓨트=Spark(배치)+Flink(스트림), Trino 제거·dbt→dbt-spark. 목표 토폴로지·급소·자원(**8 CPU / 22,888 MiB**, BATCH·STREAM **동시 기동**)·PoC 우선 PDCA**
- [전체 아키텍처 / 데이터 흐름](architectures/overview.md) 🚧 — 현행 Dagster · dbt · Iceberg · SeaweedFS 스택 **스냅샷**(재설계 이행 중 — Trino 경로는 제거 대상), **bronze 적재 템플릿(S3→Iceberg)**
- [Docker/Compose](architectures/docker.md) ✅ — 현행 채택
- [Spark](architectures/spark.md) 🚧 · [Flink](architectures/flink.md) 🚧 · [Kubernetes](architectures/k8s.md) 🚧 — 재설계로 이행중
- [OCI + Terraform + k3s](architectures/oci.md) 🔎 — 클라우드 이행 경로(Always Free A1 ARM, IaC로 k3s 부트스트랩)
- [Trino](architectures/trino.md) 🔎 — 현행까지 채택, 재설계로 제거
- [모니터링·관측](architectures/monitoring.md) 🔎 — Prometheus 선언이 남아 있고 profile을 켜면 수집도 되지만, **보는 대상이 정본과 갈린** 상태(켜면 초록불이 떠서 오히려 판별이 어렵다). 현행 관측 실태(healthcheck 2/6·probe 2개·알림 0건)와 Grafana·kube-prometheus-stack·metrics-server·Alertmanager를 **지금 쓰지 않는 이유**

### 데이터셋

- [데이터셋 스키마·피처 레퍼런스](dataset_schema.md) — MIMIC-IV(icu·hosp 11테이블)·eICU(3테이블) 원천 스키마와 **SOFA→Sepsis-3 실버 파이프라인(22 모델)** 매핑

### 분석 (analysis)

파이프라인이 만든 테이블로 **질문에 답하는** 축. 규칙 정본은 컨벤션에 있고, 실행 환경은 노트북 README에 있다.

- [분석 컨벤션](conventions/analysis.md) — **gold 모델 / 노트북 / 리포트 3층 분리**와 그 판단 기준, 결론 수치의 재현 경로, DUA·재식별 통제의 작업 절차 반영
- [`notebooks/README.md`](../notebooks/README.md) — 호스트 Jupyter Lab(포트 8889) 실행·Spark Connect 접속·셀 출력 통제
- 리포트는 `docs/analyses/<NN>-<slug>.md`에 쌓는다(**아직 없음** — 첫 분석 때 생성)

### 외부 공개 (publishing)

같은 결론이라도 **독자가 다르면 기준이 다르다** — `analyses/`는 저장소를 **아는 사람**이,
`posts/`는 **모르는 사람**이 읽는다. 공개는 커밋보다 강한 기준이고, 발행은 **사람**이 한다.

- [외부 공개 컨벤션](conventions/publishing.md) — 규칙 정본
- [`docs/posts/README.md`](posts/README.md) — 공개 산출물 디렉터리(블로그 원고·공유 자료·발표 자료) 규약과 `analyses/`와의 차이

### 철학

- [코딩 철학](philosophy.md) — 단순함·명시적·가독성·비밀정보 참조·재사용 추출·추적 용이성 (PEP 20 / 12-Factor / Rule of Three), 그리고 🔴 **원칙 7 「성공 신호를 의심한다」** — "통과"가 *검사했다*인지 *실행됐다*뿐인지 구분, 부정 결과는 **관측 경로가 살아 있었음을 함께 확인**, 새 게이트는 **일부러 위반시켜** 본다. 층이 쌓여 있다: 관측 *범위*의 편향 · 관측이 *경쟁 가설을 분리하는가* · 기록의 *시간축* · **수치가 그 문장의 대상을 세고 있는가**(계측 *단위* — 🔴 틀린 값보다 **단위가 어긋난 정답**이 위험하다. 검산을 통과하며 남는다)

### 도구 (tooling)

- [Claude Code 스킬](skills.md) — Agent Skills 카탈로그: **잠긴 스킬**(skills-lock.json)과 **작업 유형별 매핑**(dbt·Spark·K8s·CI 등), 사용 규칙(프로젝트 컨벤션 우선). 🔴 스킬은 **세 축으로 갈린다** — 🔒 lock 등재 / ⚙️ **디스크 설치·lock 밖** / 🌐 **런타임 제공(디스크에 없음)**. 워커에는 `Skill` 도구가 없어 ⚙️는 `Read`로 쓸 수 있지만 🌐는 파일이 없어 **워커 지시문에 적으면 죽은 참조**다

### 코딩 규칙 (conventions)

| 문서                                | 내용                                                   |
| ----------------------------------- | ------------------------------------------------------ |
| [공통 규칙](conventions/general.md) | 언어, 들여쓰기, 커밋 메시지, 릴리스/태그, pre-commit, 디렉토리 규칙 |
| [Git 워크플로](conventions/git.md)  | 브랜치 전략, 논리적 커밋 단위, 커밋 금지/대상, **병렬 세션 git worktree(충돌 회피)**, AI 세션 git 규칙 |
| [Python](conventions/python.md)     | ruff, 타입 힌트, 예외 처리, 의존성 관리, 스크립트 절차형(함수/클래스 최소화) |
| [Dagster](conventions/dagster.md)   | 에셋 정의(함수형), 메타데이터, 서브프로젝트 체크리스트, 잡·스케줄 |
| [dbt](conventions/dbt.md)           | 모델 레이어링, 네이밍, 테스트, sqlfluff, Trino/Iceberg, dbt-spark 타깃, **방언 흡수(내장 vs dispatch 매크로·의미론 검증)** |
| [**분석**](conventions/analysis.md) | **3층 분리**(gold 모델 / 노트북 / 리포트)와 배치 기준, gold 실행 규칙(grain·네이밍·테스트), 노트북 재현성·셀 출력 커밋 금지, **결론 수치는 모델 경유**·코호트 attrition·엔진 병기, 리포트 구성 |
| [**외부 공개**](conventions/publishing.md) | **공개는 커밋보다 강한 기준**(커밋해도 되지만 공개하면 안 되는 것), 소규모 셀(<5) 마스킹·DUA 재배포 제한, 수치의 엔진 병기, 출처 등급(A~D), 산출물 규약(`docs/posts/<NN>-<slug>.md`), **발행은 사람이**(워커 금지)·`security` 컨펌 게이트, 강제 수단의 한계 |
| [타임존](conventions/timezone.md)   | 저장=UTC / 표시·스케줄=KST, `execution_timezone`, tz-aware datetime |
| [테스트](test.md)                   | 테스트 계층(피라미드)·우선순위, dbt 스키마/단위/singular·Dagster pytest·스모크 |
| [Docker](conventions/docker.md)     | Compose 앵커, `latest` 금지, healthcheck, `deploy.resources`, profiles, Dockerfile |
| [**관측·모니터링**](conventions/monitoring.md) | 서비스 추가 시 **관측 수단 등록 의무**("안 둔다"도 유효한 선언 — 빠뜨린 것과 구분), **계측 대상 없이 수집기를 두지 않는다**(`profiles` opt-in도 면제 아님), **관측 경로 생존 확인**(부정 결과에 타깃 목록·`up`·로그 최신 타임스탬프 병기 — 원칙 7의 운영판), 관측 수치는 **시각(`date` 실측)·모집단·계측 도구 + 계측 단위** 병기. 현행 실태는 [architectures/monitoring.md](architectures/monitoring.md) |
| [Kubernetes](conventions/k8s.md)    | (이행) 워크로드 유형, requests/limits, probe, ConfigMap·Secret, RBAC, Helm + **Spark/Flink Operator·호스트 Dagster 트리거(Pipes)·kind on Podman/레지스트리·러너 이미지 빌드·Ingress(UI 고정 URL)·컴퓨트 동시 기동** |
| [Terraform/IaC](conventions/terraform.md) | (도입) 스택 구조, 버전 고정·lock 커밋, `terraform fmt`(2-space) 고정, state·비밀 커밋 금지, cloud-init 선언형, templatefile 주의 |
| [**컨벤션 인덱스**](conventions/README.md) | `docs/conventions/` 전체 목차·읽는 순서·정본 원칙 |
| [에이전트 오케스트레이션](conventions/agents.md) | AI 세션 **3계층**(supervisor→director→subagent, director 우선 1명) 역할·경계, **상호작용 로그·승인 게이트·단일 기록자**, **기록관 저널** 규약(개인 Obsidian 볼트 `$OBSIDIAN_VAULT/agents/<날짜>/<미션>.md`, 작업일자별·미션당 1파일, repo 커밋 금지), **구조도**(계층·배정·상향 보고·권한 매트릭스), **기록 시점(체크포인트)**·미션 판단 기준·수동 보정 `/journal`, **에스컬레이션**(권한 밖·특이사항 → supervisor), **`director`는 판정자**(도구로 직접 작업하지 않고 계획·배정·「계획 대비 실행 정합」 판정), **`security` 컨펌은 G1(계획 1회)+G2(작업내용 1회)+Δ(계획 밖 조건부)**, **`archivist` 전담 기록**(single-writer 유지·supervisor 폴백), **정합성 가드 hook**(`scripts/journal_guard.py` — `NN` 넘버링 경합 차단·저널 누락 경고).<br/>`.claude/agents/` **14종**(2026-08-22 실측 — `ls .claude/agents/*.md \| wc -l`) — 판정자 `director` / 데이터 `data-engineer`·`data-verifier`·`data-qa`·**`data-extractor`** / 인프라 `devops-engineer`·`devops-verifier`·`devops-qa` / 도메인 `analyst`·`tech-writer` / 도메인 공통 `researcher`(외부 근거)·`security`(노출·규제) / 계층 밖 `archivist`(관측·기록)·`skill-matcher`(스킬 배선). 🔴 **`data-extractor`는 Rule of Three의 명시적 예외**다(사용자 결정 2026-08-22) — 신설 근거가 배정 반복이 아니라 **노출 통제**이고, 실측상 배정 이력은 **0회**였다(`docs/analyses/` 0편). `analyst`와 방법은 겹치나 산출물이 **결론 ↔ 데이터 그 자체**로 갈리고 착지가 **저장소 안 ↔ 밖**으로 갈린다. **이 신설을 선례로 삼아 다른 워커를 늘리지 않는다.** 🔴 이 중 **`security`·`archivist`·`skill-matcher`·`tech-writer` 4종은 `director` 관할 밖**(supervisor 직접 배정)이며, 「계층 밖」과는 **다른 축**이다 — 앞 3종은 계층 자체를 감사·기록해서, `tech-writer`는 director의 행동 규칙이 담긴 정본을 써서다(이해충돌 기준). 🔴 **`tech-writer`는 `docs/security.md`·`docs/skills.md`를 쓸 수 없다**(2026-08-22 `worker_path_guard.py` **`except` 축** — 판정 대상이 판정 근거를 고치지 못하게 한 기계 강제. 로직 22/22 + ✅ **라이브 실발동 확인** — 2026-08-22 3셀, 🔴 **`Edit` 도구 한정**이며 `Write`·`NotebookEdit`은 미시도. 🔴 **`except`는 워커별이라 전파되지 않아** supervisor·다른 워커는 막지 않는다). 같은 날 **정본 설계 게이트가 축소**돼 `docs/conventions/**`·`docs/architectures/**`는 **`ask` 없이** 편집된다(판단 축을 경로 → 가역성으로 이동, 최종 관문은 커밋 1회). 🔴 **각 워커의 경계·권한·가드 세부는 여기 복제하지 않는다 — 정본은 [`conventions/agents.md`](conventions/agents.md) §구조도·권한 매트릭스이고, 갈리면 그쪽이 사실이다** |

### 운영 (operations)

- [환경변수·운영 정책](operations.md) — `.env`→compose→`EnvVar` 전파 체인, Iceberg snapshot·로그 보존 정책, **§2-1 로컬 세션 로그 정리**(`cleanupPeriodDays` — 🔴 **대화형 기동에서만** 돌고 보존 단위는 파일이 아니라 **세션**(`subagents/`는 부모 수명을 따른다). 같은 디렉터리의 자동 메모리가 함께 지워지지 않도록 **수동 삭제는 `-name '*.jsonl'`로 유형 한정**), **§3 토큰 비용 계측**(`scripts/token_cost_report.py` — 캐시 읽기/캐시 쓰기/출력/미캐시 입력 4축 분리 집계, **2026-08-21 · 2026-08-23 관측 2회를 나란히** 둔다 — 백분율은 관측 시점의 `CLAUDE.md` 바이트와 함께 읽는다)
- [리소스 산정](resource-sizing.md) — 호스트 자원에 따른 서비스 옵션 조정(Trino 3파일 결합·daemon OOM 계산·Postgres·SeaweedFS)

### 보안 (security)

- [보안·데이터 거버넌스 정책](security.md) — ISMS-P 인증기준(101)·의료데이터 보안 규제(개인정보보호법 가명정보 특례·보건의료데이터 가이드라인·HIPAA Safe Harbor)와 **통제 방침·보증 범위** 매핑. 비식별 연구 데이터셋 + DUA 전제.
  🔴 **현행 실태·미비점·미해소는 이 문서에 없다** — 저장소가 공개이고 이 경로가 GitHub Security Policy 페이지의 탐색 대상(`.github/`→루트→`docs/`)이라 **저장소 밖 비공개 기록**(`$OBSIDIAN_VAULT/security/posture.md`)으로 분리했다(2026-08-23)

## 핵심 원칙 요약

> 가치(왜)는 [코딩 철학](philosophy.md), 아래는 빠른 규칙 참조(어떻게).

1. **주석은 한국어, 식별자(변수·함수·모델명)는 영어**
2. **들여쓰기 스페이스 4칸** (Python·YAML·SQL 공통) — 단, `.tf`는 `terraform fmt` 규정에 따라 **2-space 예외**
3. **포매터/린터 고정** — Python: `ruff`, SQL: `sqlfluff`, Terraform: `terraform fmt`
4. **커밋 메시지는 한국어 `type: 설명`** 형식

## 문서 작성·유지 규칙

- 이 프로젝트에서 정한 **규칙·결정·작업 패턴은 최대한 문서로 남긴다**.
- 문서는 한국어로 작성한다.
- 코드 식별자·명령어·경로는 영어/원문 그대로 표기한다.
- 규칙을 바꿀 때는 **`CLAUDE.md`·이 `docs/`·`README.md`를 함께 갱신**하여 단일 출처(single source of truth)를 유지한다.
- 외부 레퍼런스는 각 문서 하단 `참고` 섹션에 링크와 함께 남긴다.

상세 규칙과 인덱스:

- [문서 동기화(doc-sync)](doc-sync.md) — 단일 출처 원칙, 변경 유형별 동기화 체인
- [참고 문서(references)](references.md) — 규칙·설계가 근거로 삼는 외부 표준 인덱스
