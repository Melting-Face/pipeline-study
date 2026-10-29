# 미션 ③ wiki 스터디 재작성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** wiki를 "기술별 스터디 장 + 실습·고장 대응 절차의 정본"으로 바꾼다. 장 하나만 읽고도 그 기술을
이해하고, 띄우고, 고칠 수 있게 한다.

**Architecture:** 기존 노트는 **slug를 그대로 두고 장으로 승격**한다. 새 절(개념·쓰임·실습·고장 대응)을 앞에 더하고,
기존 본문은 「함정 — 회고」 아래로 제목만 한 단계 내린다. 그래서 무손실 증명은 미션 ②와 같이
「삭제 줄 = 강등된 제목 줄뿐」이다. 예외는 500줄 분할(flink) 하나이고, 거기만 주장 대조표를 쓴다.
실습 출력은 **실제로 띄워 관측한 것만** 싣는다.

**Tech Stack:** Markdown, `scripts/doc_lint.py`(호스트 `python3`), `scripts/wiki_linkify.py`, kind on Podman,
Terraform 스택 A·B,
`kubectl`.

**Spec:** [설계 문서](../specs/2026-10-24-docs-portfolio-study-design.md) — 「미션 ③」, 「내가 정한 것」, 「검증」

## Global Constraints

- 반목표 4개를 지킨다(중복 금지·규칙 문서 약화 금지·빽빽함 금지·교훈 소실 금지).
- 장 템플릿(설계 원문): 1 **개념** — 무엇인지 + 외부 1차 문서 링크 / 2 **이 저장소에서의 쓰임** — 실재 경로·YAML·코드
  인용, 흐름 /
  3 **실습** — 띄우기 → 확인 → 내리기, 확인마다 "무엇이 보이면 성공인지", **출력은 실제 관측만** /
  4 **고장 대응** — 증상 → 먼저 볼 것 → 처방 / 5 **함정** — 기존 회고 무손실 이관.
- 기존 wiki slug는 **하나도 없애지 않는다**. 노트를 합치지 않는다. 장이 500줄을 넘으면 `<slug>.md` +
  `<slug>-pitfalls.md`.
- 규칙 문서 안의 집행 절차(예: CNPG 비밀번호 회전 한 벌)는 **옮기지 않고 링크만** 건다.
- wiki 규칙(publishing.md §4-1): 평평한 경로, 영문 kebab-case, 한국어 H1, 프론트매터 금지, `wiki/README.md` 금지,
  저장소 원본 링크는 `.md`를 붙인다, 참조형 링크·HTML `<a href>` 금지.
- 저장소 파일로 가는 링크는 절대 URL `https://github.com/Melting-Face/pipeline-study/blob/main/<경로>`로 쓴다.
  `wiki_linkify.py`가 절대 URL을 건드리지 않는다(그 docstring 「다루지 않는 것」).
- `doc_lint`: 한 줄 120 표시폭, 문서 500줄, 괄호 중첩 금지, 시제 어휘 금지(wiki 포함), 일자는 wiki 면제.
  새 노트는 Home·_Sidebar 양쪽에 등재(`unindexed` 차단).
- 공개 출력에는 호스트 절대경로·계정명·키·내부 IP를 싣지 않는다.
- 클러스터 기동(`k8s-up.sh` → 스택 A·B apply)은 비가역 게이트: 실행 직전 reviewer 체크리스트 E 1회 + 사용자 승인,
  **승인을 받은 턴에서 중단 조건을 다시 잰다**. 착수 전 Podman VM 시계 확인.
- 띄운 상주 컴퓨트는 그 자리에서 내린다. 회수는 `podman stop`, **`k8s-down.sh`(클러스터 삭제)는 쓰지 않는다**.
- `wiki/**` 커밋 전 reviewer 체크리스트 E와 사용자 확인. 커밋·푸시는 사용자 요청 시에만.
- CLAUDE.md 순증 바이트 0 이하. 커밋 메시지 `docs(scope): 한국어 설명` 72자 이내 +
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## 설계와 다르게 정한 것 (Ruling)

- **미션 ③을 PR 3개로 나눈다** — ③-1 규칙·구조·기존 노트 장 전환(클러스터 불필요) / ③-2 실습·고장 대응 관측(클러스터) /
  ③-3 신규 장 7개 + 미션 ②에서 넘어온 이관.
  - 이유: 장 15개 + 클러스터 관측을 PR 하나에 담으면 체크리스트 E와 사용자 확인이 한 번에 2천 줄 넘게 된다.
    공개 위키는 회수가 어려워 리뷰 단위를 작게 둔다.
  - 틀렸을 때 비용: PR 2개 분량의 리뷰·머지 왕복.
  - 설계만이 아니라 [agents.md](../../conventions/agents.md) 미션 규칙 ①(미션 = PR 하나)의 **예외**다.
    사용자가 이 예외를 승인했다.
  - 클러스터 기동은 ③-2에서 **1회**만 한다. 그 기동에서 ③-3 신규 장 7편의 명령도 함께 관측해 볼트에 남기고,
    ③-3은 그 로그를 소비한다. 재기동은 관측이 빠졌을 때만 하는 예외다.
- **ArgoCD 장 slug는 `argocd-bootstrap`** — `docs/argocd-gitops.md:370`이 이미 이 이름으로 예고했다.
- **기존 본문은 「함정 — 회고」 한 절 아래에 둔다** — 설계는 회고를 「함정」으로 이관하라고 한다. 「왜 Dagster였나」
  같은 판단 서술도
  회고의 일부라 쪼개지 않는다. 쪼개면 무손실 증명이 대조표로 바뀌고 비용이 커진다.
  - **허용 삭제 줄은 둘이다**: 강등된 제목 줄, 그리고 3행 머리 인용의 「학습 노트.」 접두가 든 1줄.
    머리 인용은 역할 개정(Task 1) 때문에 바꿔야 한다. 접두만 바꾸고 나머지 문장은 그대로 둔다.
- **wiki 커밋은 웨이브 단위** — E는 커밋 전 1회가 규칙이라, 태스크마다 커밋하지 않고 PR(웨이브) 끝에 E → 사용자 확인
  → 커밋한다.
  - 비-wiki 변경(Task 1의 규칙 문서)은 wiki 커밋과 섞지 않고 따로 먼저 커밋한다.

## 장 목록과 매핑

| 장 slug | 다루는 것 | 출처 | 분할·하위 쪽 | PR |
| --- | --- | --- | --- | --- |
| `dagster-orchestration` | Dagster | 기존 노트 | `dagster-partitions`(하위 쪽, 페이지 유지) | ③-1 |
| `cross-engine-sql` | dbt + Trino | 기존 노트 | — | ③-1 |
| `spark-on-k8s` | Spark on K8s | 기존 노트 | — | ③-1 |
| `flink-on-k8s` | Flink on K8s | 기존 노트(500줄) | `flink-on-k8s-pitfalls`(신규 분할) · `streaming-source-choice`(하위 쪽) | ③-1 |
| `k8s-bootstrap` | kind 부트스트랩 | 기존 노트 | — | ③-1 |
| `k8s-operators` | 오퍼레이터 | 기존 노트 | — | ③-1 |
| `how-to-read` | 성공 신호를 의심한다(방법론) | 기존 노트 확장 | `kubectl-judgement`(하위 쪽) | ③-1 |
| `claude-code-cli` | Claude Code 에이전트 운영(방법론) | 기존 노트 | — | ③-1 |
| `argocd-bootstrap` | ArgoCD GitOps | 신규 | — | ③-3 |
| `iceberg-catalog` | Iceberg 카탈로그(CNPG) | 신규 | — | ③-3 |
| `seaweedfs-s3` | SeaweedFS(S3) | 신규 | — | ③-3 |
| `prometheus-monitoring` | 모니터링(Prometheus) | 신규 | — | ③-3 |
| `airflow-on-k8s` | Airflow | 신규 | — | ③-3 |
| `terraform-stacks` | Terraform 스택 A·B | 신규 | — | ③-3 |
| `analysis-layers` | 분석 3층·메달리온(방법론) | 신규 | — | ③-3 |

하위 쪽(sub page)은 장 템플릿을 따르지 않고, 상위 장의 「함정」에서 링크한다.

## Review Focus

1. **관측하지 않은 출력이 실습에 들어간다.** 공개 위키에서 거짓은 회수가 어렵다.
   - ③-2의 출력 펜스마다 출처 주석(`# 관측: <명령>`)이 붙고, 같은 출력이 관측 로그(볼트)에 있어야 한다.
2. **공개 출력에 환경 고유값이 섞인다.** 호스트 경로·계정명·키·내부 IP·파드 해시를 놓치기 쉽다.
   - 커밋 전 `grep -nE '/Users/|10\.[0-9]+\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.' wiki/*.md`를 돌린다.
     대조군으로 `localhost`도 같은 호출에서 센다.
3. **slug·앵커가 깨진다.** 공개 URL과 `docs/`에서 들어오는 링크 10종(`grep -o 'wiki/[a-z0-9-]*\.md'`)이 대상이다.
   - 제목 강등은 앵커 슬러그를 바꾸지 않지만, 분할(flink)은 바꾼다.
4. **실습 명령이 저장소와 어긋난다.** 장의 명령이 setup.md·local-k8s.md·매니페스트와 다르면 학습자 경로가 둘이 된다.
   - 명령 줄마다 `grep -F`로 출처 파일에 실재해야 한다(출처를 펜스 위 한 줄에 밝힌다).
5. **검증용 상주 컴퓨트가 남는다.** Flink 세션·Spark 잡이 대상이다.
   - ③-2 각 실습의 「내리기」 뒤에 `kubectl get flinkdeployment,sparkapplication -A`를 돌려 빈 목록을 확인하고, 세션
     끝에
     `podman stop`을 확인한다.

---

## PR ③-1 — 규칙·구조·기존 노트 장 전환 (클러스터 불필요)

### Task 0: `§` 참조 기준선

- [ ] **Step 1:** 미션 시작 전 `§` 참조를 센다.
  - 명령: `grep -rnoE '§[^ )·,]+' --include='*.md' docs wiki CLAUDE.md AGENTS.md README.md`
  - 결과를 scratchpad에 남긴다. 값과 함께 **무엇을 세는지**(파일·줄·참조 단위)를 적는다.
  - Task 10 Step 2가 이 값과 비교한다.

### Task 1: wiki 역할 개정 — 한 벌로

**Files:**
- Modify: `docs/conventions/publishing.md` §4-1(역할 문장 + 「장 템플릿」 소절 신설 + 「링크 표기」 보강)
- Modify: `CLAUDE.md:18`, `docs/README.md`(wiki 언급), `docs/doc-sync.md:37`(「위키 학습 노트」 행)
- `wiki/Home.md:1·21`(H1·절 제목)은 wiki라 Task 2에서 고친다. 11편의 머리 인용은 Task 3·4·5에서 고친다.
- `AGENTS.md`는 같은 문장이 있을 때만 고친다(`grep -n 'wiki' AGENTS.md`)
- `docs/conventions/general.md`는 wiki 역할 문장이 없어 대상이 아니다(설계의 규칙 갱신 목록과 대조함).

**Interfaces:**
- Produces: 역할 문장 "`wiki/`는 **기술별 스터디 장 + 실습·고장 대응 절차의 정본**이다(규칙 문서의 집행 절차는 링크만)".
  Task 2~7이 이 정의를 따른다.

- [ ] **Step 1: RED** — 아래 명령의 히트 ≥ 1.

  ```bash
  grep -nE '학습 노트([^북]|$)' CLAUDE.md AGENTS.md docs/README.md docs/doc-sync.md docs/conventions/publishing.md
  ```

  - 모집단은 이 5개 파일이다. `[^북]`은 `analysis.md`의 「학습 노트북」 같은 부분 일치를 빼려는 것이다.
  - 대조군 `grep -c '4-1' docs/conventions/publishing.md` > 0.
- [ ] **Step 2: 교정**
  - 각 자리의 역할 문장을 Produces 문장으로 바꾼다.
  - publishing.md §4-1에 「장 템플릿」 소절을 더한다. 5절 이름·순서는 Global Constraints 그대로, 500줄 분할 규칙,
    하위 쪽 정의를 넣는다.
  - §4-1 「링크 표기」에 저장소 파일 링크 규칙을 더한다. 형식은 절대 URL
    `https://github.com/Melting-Face/pipeline-study/blob/main/<경로>`이다.
    절대 URL은 `doc_lint --links`가 대상 실재를 보지 않는다. 그래서 대체 검사(경로를 뽑아 `ls`)를 같은 자리에 적는다.
  - §4-1의 기존 통제(반출·예시 경계·E 게이트)는 **한 줄도 지우지 않는다**.
- [ ] **Step 3: GREEN**
  - Step 1과 같은 명령·모집단으로 히트 0
  - `git diff -U0 -- docs/conventions/publishing.md | grep -E '^-[^-]'`가 역할 문장 줄뿐
  - CLAUDE.md `wc -c` 전후 순증 ≤ 0
  - `doc_lint` 위반 0
- [ ] **Step 4: Commit** — `docs(conventions): wiki를 기술별 스터디 장과 절차 정본으로 정의한다`

### Task 2: Home·_Sidebar를 장 구조로

**Files:**
- Modify: `wiki/Home.md`, `wiki/_Sidebar.md`

**Interfaces:**
- Consumes: 위 「장 목록과 매핑」 표
- Produces: 묶음 이름 4개 — 「파이프라인」(Dagster·dbt+Trino) · 「컴퓨트」(Spark·Flink) · 「플랫폼」(kind·오퍼레이터)
  · 「방법론」.
  ③-3 신규 장도 이 묶음에 들어간다.

- [ ] **Step 1: RED** — `_Sidebar.md`·`Home.md`가 링크하는 slug 목록을 scratchpad에 남긴다.
- [ ] **Step 2: 재구성**
  - 장은 묶음별로 두고, 하위 쪽은 상위 장 아래 들여쓰기로 둔다.
  - Home은 "장 하나 = 한 기술, 5절 템플릿" 안내와 묶음별 목록을 둔다. 기존 Home 본문은 지우지 않고 아래로 내린다.
  - Home의 H1(1행)과 「학습 노트」 절 제목(21행)을 새 역할 이름으로 바꾼다(Task 1 Produces).
- [ ] **Step 3: GREEN** — Step 1의 등재 slug가 전부 남는다(빠진 것 0), `doc_lint --links` 0(unindexed 포함)
- [ ] **Step 4: 웨이브 끝에 커밋**(Task 5와 함께)

### Task 3: 기존 노트 7편을 장으로 승격

**Files:**
- Modify: `wiki/dagster-orchestration.md`, `wiki/cross-engine-sql.md`, `wiki/spark-on-k8s.md`, `wiki/k8s-bootstrap.md`,
  `wiki/k8s-operators.md`, `wiki/how-to-read.md`, `wiki/claude-code-cli.md`

**Interfaces:**
- Consumes: Task 1 장 템플릿
- Produces: 각 장의 `## 실습` 절. **③-1에서는 명령까지만** 쓰고 기대 출력 펜스는 두지 않는다. ③-2가 관측 출력을 채운다.

- [ ] **Step 1: RED** — 파일마다 `grep -c '^## 개념' <f>` → 0. 강등할 기존 H2 목록과 그 앵커를 가리키는 링크 수를
      기록한다
  (`grep -rn '<slug>.md#' wiki docs README.md`).
  새 H2 4개(`개념`·`이 저장소에서의 쓰임`·`실습`·`고장 대응`)와 `함정 — 회고`가 기존 제목과 같은 앵커를 만들지도 센다.
  기대값 0(미션 착수 전 grep 0건), 겹치면 앵커에 `-1`이 붙어 들어오는 링크가 바뀐다.
- [ ] **Step 2: 적용**(파일마다)
  - H1과 머리 인용 바로 뒤에 `## 개념`·`## 이 저장소에서의 쓰임`·`## 실습`·`## 고장 대응`을 넣는다.
  - 기존 본문 앞에 `## 함정 — 회고`를 두고, 기존 H2를 H3로, H3을 H4로 한 단계씩 내린다. 본문 줄은 손대지 않는다.
  - 예외는 3행 머리 인용 1줄이다. 「학습 노트.」 접두만 새 역할 이름으로 바꾼다(Ruling).
  - 「개념」의 외부 문서 링크는 `docs/references.md`·해당 `docs/architectures/<기술>.md` 「참고」에 이미 있는 URL만
    쓴다.
  - 「쓰임」의 경로는 `docs/architectures/<기술>.md` 「어떻게 도나」에서 복사하고, `ls`로 다시 확인한다.
  - 「실습」 명령은 `docs/setup.md`·`docs/setup/local-k8s.md`에서 복사하고, 출처를 펜스 위 한 줄에 밝힌다.
  - 「고장 대응」은 `docs/setup.md` §8 「증상별 조치」와 기존 본문에 **이미 적힌** 증상만 옮긴다(증상 → 먼저 볼 것 →
    처방).
    없는 칸은 만들지 않는다.
  - `how-to-read`는 방법론 장이라 「실습」을 "판정 명령 하나를 골라 대조군과 함께 돌려 보기"로 쓴다. 명령은
    `kubectl-judgement`에서 가져온다.
- [ ] **Step 3: GREEN — 무손실**
  - 파일마다 `git diff -U0 -- <f> | grep -E '^-[^-]'`가 **강등된 제목 줄 + 머리 인용 1줄뿐**
  - 문서마다 500줄 이하. 넘으면 Task 4의 분할 규칙을 적용하고 Ruling을 남긴다(후보: `k8s-operators` 270줄,
    `claude-code-cli` 248줄)
  - 「실습」 명령 줄마다 출처 파일에서 `grep -F` ≥ 1
  - `doc_lint`·`--links` 0
- [ ] **Step 4: 웨이브 끝에 커밋**

### Task 4: flink 장 분할 (500줄)

**Files:**
- Modify: `wiki/flink-on-k8s.md`
- Create: `wiki/flink-on-k8s-pitfalls.md`
- Modify: `wiki/Home.md`, `wiki/_Sidebar.md`(하위 쪽 등재)

- [ ] **Step 1: 주장 목록** — `flink-on-k8s.md` H2별 주장을 한 줄씩 뽑아 대조표(scratchpad)로 만든다. 행 수를 기록한다.
  같은 호출에서 `flink-on-k8s.md#`를 가리키는 링크 수를 센다.
- [ ] **Step 2: 분할**
  - `flink-on-k8s.md`: H1·머리 인용 + 개념·쓰임·실습·고장 대응 + 「함정 — 회고」는 요지 3줄과 `-pitfalls` 링크
  - `flink-on-k8s-pitfalls.md`: 기존 본문 전체(H1 「Flink를 K8s 위에 올리며 — 함정」), 줄 단위로 그대로 옮긴다
- [ ] **Step 3: GREEN**
  - 대조표 모든 행에 새 위치가 있다. 빈 칸 0, 행 수가 Step 1과 같다.
  - 원문의 비어 있지 않은 줄이 두 파일 중 한 곳에 글자 그대로 있다. 다른 줄은 강등된 제목과 머리 인용 1줄뿐이다
    (미션 ② overview 검사 스크립트와 같은 방식).
  - 대조표(원문 주장 → 이관본 위치)는 PR ③-1 본문에 붙인다(설계 반목표 4).
  - Step 1에서 센 앵커 링크를 새 파일로 고쳤다. `--links` 0.
  - 두 파일 모두 500줄 이하
- [ ] **Step 4: 웨이브 끝에 커밋**

### Task 5: 하위 쪽 연결 + ③-1 게이트

**Files:**
- Modify: `wiki/dagster-partitions.md`·`wiki/streaming-source-choice.md`·`wiki/kubectl-judgement.md` — 머리 인용 아래
  "상위 장" 링크 1줄만

- [ ] **Step 1:** 하위 쪽 3편에 상위 장 링크를 더한다. 삭제 줄은 머리 인용 1줄뿐이다(Ruling).
- [ ] **Step 2: 낡은 표현 grep** — `grep -cE 'PR2|in-cluster|Spark Connect|Trino 제거|dbt-spark' wiki/*.md`와
  대조군 `grep -c 'kind' wiki/*.md`를 같은 호출에서 센다. 현재형 서술이 남으면 고치고, 과거형(「철거했다」)은 둔다.
- [ ] **Step 3: Review Focus 2·3 검사** — 환경 고유값 grep(대조군 `localhost` 병기), 들어오는 링크 10종 실재
- [ ] **Step 4: reviewer 체크리스트 E**(Agent `model` 명시) → 사용자 확인
- [ ] **Step 5: Commit**(사용자 요청 시) — `docs(wiki): 기존 노트를 5절 템플릿의 스터디 장으로 승격한다`. PR ③-1.

---

## PR ③-2 — 실습·고장 대응 관측 (클러스터)

### Task 6: 클러스터 기동 게이트

**Files:** 없음(관측 로그는 볼트 `$OBSIDIAN_VAULT/agents/<날짜>/` 저널에 둔다. 저장소에는 두지 않는다)

- [ ] **Step 1: 중단 조건 측정**
  - Podman VM 시계: 호스트 epoch과 `podman machine ssh 'date +%s'`의 차이 ≤ 5초
  - `podman machine inspect`에서 rootful 확인
  - 루트 체크아웃이 `main`이고 `git status --short`가 비어 있다(스택 A는 루트에서만 apply)
- [ ] **Step 2: 계획 작성** — 명령은 setup.md §3 「올리기」 블록 그대로. 롤백은 `podman stop`, `k8s-down.sh` 금지.
- [ ] **Step 3: reviewer 체크리스트 E**(model 명시) → 사용자 승인
- [ ] **Step 4: 승인을 받은 턴에서 Step 1을 다시 잰다** → 통과하면 기동한다.
  `kubectl get applications -n argocd` 전부 `Synced`·`Healthy`면 수렴이다.

### Task 7: 장별 실습 관측과 기입

**Files:**
- Modify: ③-1의 장 7편 `## 실습`·`## 고장 대응`(+ `flink-on-k8s.md`)

- [ ] **Step 1:** 장마다 띄우기 → 확인 → 내리기를 실제로 돌린다. 출력은 볼트 관측 로그에 원문으로 남긴다.
  - 같은 기동에서 ③-3 신규 장 7편(Task 8)의 명령도 관측한다. 로그에는 장 slug를 붙여 ③-3이 찾아 쓰게 한다(Ruling).
- [ ] **Step 2:** 장의 확인 단계에 "무엇이 보이면 성공인지"와 관측 출력 발췌를 넣는다. 펜스 첫 줄은
      `# 관측: <명령>`이다.
  환경 고유값은 `<…>`로 가린다.
- [ ] **Step 3:** 내리기 뒤에 `kubectl get flinkdeployment,sparkapplication -A`가 빈 목록인지 확인한다(Review Focus 5).
- [ ] **Step 4:** 관측 중 만난 고장은 「고장 대응」에 증상 → 먼저 볼 것 → 처방으로 더한다. 관측한 것만 쓴다.
- [ ] **Step 5: GREEN**
  - 출력 펜스마다 같은 출력이 볼트 로그에 있다.
  - 환경 고유값 grep 0(대조군 병기)
  - `doc_lint`·`--links` 0
  - 세션 끝 `podman stop` 후 `podman machine list`가 정지 상태
- [ ] **Step 6:** setup.md §8 「증상별 조치」의 기술별 항목 끝에 해당 장 「고장 대응」 링크를 더한다(삭제 0줄).
- [ ] **Step 7:** reviewer E → 사용자 확인 → Commit(요청 시)
  — `docs(wiki): 장마다 관측한 실습과 고장 대응을 싣는다`. PR ③-2.

---

## PR ③-3 — 신규 장 + 미션 ②에서 넘어온 이관

### Task 8: 신규 장 7편

**Files:**
- Create: `wiki/argocd-bootstrap.md`, `wiki/iceberg-catalog.md`, `wiki/seaweedfs-s3.md`,
  `wiki/prometheus-monitoring.md`,
  `wiki/airflow-on-k8s.md`, `wiki/terraform-stacks.md`, `wiki/analysis-layers.md`
- Modify: `wiki/Home.md`, `wiki/_Sidebar.md`(Task 2 묶음에 등재), `docs/argocd-gitops.md:370`(백틱 → 링크)
- Modify: 대응 `docs/architectures/<기술>.md`의 「교훈」 절에 새 장 링크 1줄(설계 미션 ② 템플릿 4번 절과 동선을 닫는다)

- [ ] **Step 1: 근거 수집** — 장마다 출처는 `docs/architectures/<기술>.md`(역할·어떻게 도나·운영 메모), 해당
      conventions 절,
  매니페스트다. 규칙 문서의 집행 절차는 **링크만** 건다(예: CNPG 비밀번호 회전 → `conventions/k8s/cnpg.md`).
- [ ] **Step 2: 작성** — 5절 템플릿. 실습은 Task 7 Step 1이 볼트에 남긴 관측 출력만 쓴다.
  관측이 빠져 클러스터를 다시 띄워야 하면 Task 6 게이트를 처음부터 다시 탄다(예외).
  「함정」은 기존 docs에 **기록된** 교훈만 쓰고 출처 절을 밝힌다. 없으면 절을 두지 않는다.
- [ ] **Step 3: GREEN** — 경로 전부 `ls` 실재, 명령 `grep -F` 출처 실재, 500줄 이하, `doc_lint`·`--links` 0, unindexed 0

### Task 9: 아키텍처 「운영 메모」 절차 이관

**Files:**
- Modify: `docs/architectures/*.md` 「운영 메모」, 대응 wiki 장 「실습」·「고장 대응」

- [ ] **Step 1: 분류표** — 「운영 메모」 항목마다 *절차*(명령·순서)인지 *결정 근거*인지 판정한다. 행 수를 기록한다.
- [ ] **Step 2:** 절차는 wiki 장으로 옮기고, 원래 자리에는 장 링크 1줄을 둔다. 결정 근거는 「왜 이걸 골랐나」로 옮긴다.
  규칙을 집행하는 절차는 옮기지 않는다.
- [ ] **Step 3: GREEN** — 분류표 모든 행에 새 위치가 있고 빈 칸 0, `§` 참조 수 전후 대조, `doc_lint`·`--links` 0.
  분류표는 PR ③-3 본문에 붙인다.
- [ ] **Step 4:** reviewer E → 사용자 확인 → Commit(요청 시) — `docs(wiki): 신규 장 7편과 운영 절차 이관을 더한다`.
      PR ③-3.

### Task 10: 최종 대조

- [ ] **Step 1:** 새 Explore 에이전트로 설계 부록 A를 다시 대조한다. 0건이면 그 모집단(대조한 파일·건수)을 함께 적는다.
- [ ] **Step 2:** `§` 참조 수를 Task 0 기준선과 같은 명령·모집단으로 다시 세어 깨진 참조 0을 확인한다.
- [ ] **Step 3:** 독립 리뷰 1회(reviewer, model 명시). Review Focus 5항과 무손실, Ruling 타당성을 본다.
