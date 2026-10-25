# 미션 ② README·docs 포트폴리오 재구성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** README를 검토자(위층)·학습자(아래층) 2층으로, docs 허브를 독자별 4칸으로,
아키텍처 문서를 "역할 → 어떻게 도나 → 왜 골랐나 → 교훈" 순서로 재구성한다.

**Architecture:** 내용은 지우지 않고 **앞에 더하고 순서를 바꾼다**.
기존 결정·교훈 서술은 제자리에 두고 제목만 새 템플릿에 맞춘다. 그래서 무손실 증명은
「삭제 줄 = 이름을 바꾼 제목 줄뿐」으로 단순해진다. 예외는 overview.md 재작성 하나이고, 거기만 주장 대조표를 쓴다.

**Tech Stack:** Markdown, `scripts/doc_lint.py`(호스트 `python3`), `grep`, `git diff --numstat`.

**Spec:** [설계 문서](../specs/2026-10-24-docs-portfolio-study-design.md) — 「미션 ②」

## Global Constraints

- 반목표 4개를 지킨다.
  - 중복 금지: 절차는 setup·wiki, 규칙은 conventions, 결정은 architectures에 한 곳씩만 둔다.
  - 규칙 문서 약화 금지: conventions·security·risk·agents·skills는 **내용을 바꾸지 않는다**.
  - 빽빽함 금지: 한 문장에 한 생각, 표는 대조할 때만 쓴다.
  - 교훈 소실 금지.
- `doc_lint`: 한 줄 120 표시폭(한글은 2칸), 문서 500줄, 괄호 중첩 금지, 일자 금지(wiki 면제). <!-- tense-ok -->
- 시제 어휘(`TODO`·`예정이다`) 금지. <!-- tense-ok -->
- `docs/`에 실측 수치·일자·상태를 새로 넣지 않는다(doc-sync.md §실무 규칙 7).
- 새로 쓰는 모든 경로·명령은 `ls`·`grep`으로 실재를 확인한 뒤 쓴다. 기억으로 쓰지 않는다.
- **제목(H2·H3)을 바꾸기 전에** 그 앵커를 가리키는 링크를 전역 grep으로 센다(`#<slug>`·`§<제목>`).
  링크가 있으면 링크도 같은 커밋에서 고친다.
- CLAUDE.md 순증 바이트는 0 이하로 둔다(모든 요청에 곱해진다).
- 미션 ② 전용 worktree(`docs/portfolio-restructure`)에서만 쓴다. 커밋·푸시는 사용자가 요청할 때만 한다.
-  커밋 메시지: `docs(scope): 한국어 설명`, 제목 72자 이내. 끝에 `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`.

## 설계와 다르게 정한 것 (Ruling)

- **wiki 역할 개정과 setup→wiki 링크는 미션 ③으로 미룬다.**
  - 설계는 미션 ②에서 wiki를 "실습·고장 대응 절차의 정본"으로 선언하라고 한다.
  - 하지만 그 장들은 미션 ③에서 생긴다. 지금 선언하면 setup.md가 **없는 장을 가리키는 약속**을 하게 된다.
  - 그래서 미션 ②는 **독자 정의(README·docs)만** 바꾼다. wiki 규칙(publishing.md §4-1)은 손대지 않는다.
  - 틀렸을 때 비용: 미션 ③에서 규칙 문서를 한 번 더 고친다.
- **"운영 메모" 절은 이번에 옮기지 않는다.** 절차를 wiki로 옮기는 것은 미션 ③의 일이다. 이번에는 제목만 유지한다.
- **setup.md의 「지금 되는 범위」 표는 지우지 않는다.**
  - 미션 ①에서 이 표는 상태표가 아니라 **절별 전제 표**로 바뀌었다. 학습자에게 필요한 정보다.
  - 그래서 제목만 「절별 전제」로 바꾼다. README가 이 앵커를 가리키므로 같은 커밋에서 링크도 고친다.

## Review Focus

1. **역할 한 줄이 문서마다 다르게 쓰인다.**
   - README 스택 표, docs 허브, 각 아키텍처 문서 머리의 역할 문장이 서로 다르면 단일 출처가 깨진다.
   - Task 2가 정한 문장을 Task 3·4·5가 **복사**한다.
2. **"어떻게 도나" 절에 틀린 경로가 섞인다.** 새로 쓰는 서술이라 미션 ①의 드리프트를 다시 만들 수 있다.
3.  **앵커 깨짐.** 제목 변경은 `doc_lint --links`가 상대 링크 앵커만 잡는다. 본문의 `§제목` 참조는 아무 게이트도 잡지
   않는다.
4. **README 위층이 길어진다.** 검토자가 한 화면에 보지 못하면 실패다. 위층은 **60줄 이하**로 둔다(코드 펜스 포함).
5. **README 아래층에 안 도는 명령이 들어간다.** setup.md에서 실재하는 명령만 복사한다.
   클러스터를 띄우지 않으니 실행 검증은 못 한다. 그래서 **setup.md와 글자 단위로 같은지**를 확인한다.

---

### Task 1: 독자 정의 개정 — 한 벌로 (규칙 문서)

**Files:**
- Modify: `CLAUDE.md:15`, `AGENTS.md`(같은 문장이 있으면), `docs/doc-sync.md:136-141`
- Modify: `docs/conventions/publishing.md:31-38`(왼쪽 칸 독자), `docs/conventions/general.md:208`(필요 시)
- (README.md:6 · docs/README.md:7-11은 Task 2·3이 새 구조로 다시 쓴다)

**Interfaces:**
- Produces: 독자 정의 문장. "`README.md`·`docs/`의 독자는 둘이다 — **위층은 검토자**(무엇을 왜 만들었나를 몇 분에),
  **아래층·`docs/`는 학습자**(클론해 자기 환경에서 돌리고 고친다)."
  Task 2·3이 이 문장을 그대로 쓴다.

- [ ]  **Step 1: RED** — `grep -nE '클론해 (돌리려는|자기 환경)' CLAUDE.md AGENTS.md docs/doc-sync.md
      docs/conventions/publishing.md`
  → 히트 ≥3. 대조군 `grep -c '재현' docs/doc-sync.md` → >0.
- [ ] **Step 2: 교정**
  - 각 자리의 독자 문장을 Produces 문장으로 바꾼다.
  - 바꾸는 것은 독자 정의뿐이다. **축 1(재현) 판정 규칙은 그대로 둔다** — 학습자 층에 그대로 적용된다.
  - publishing.md는 "왼쪽 칸의 독자는 저장소를 아는 사람이 아니다"라는 경고를 유지한다. 검토자도 저장소를 모른다.
- [ ] **Step 3: GREEN**
  - Step 1 grep에서 이력 외 히트 0
  - `git diff --numstat -- CLAUDE.md`에서 삭제 ≥ 추가 바이트(`wc -c` 전후 비교)
  - `python3 scripts/doc_lint.py CLAUDE.md AGENTS.md docs/doc-sync.md docs/conventions/publishing.md` → 위반 0
- [ ] **Step 4: Commit (사용자 요청 시)** — `docs(conventions): README·docs 독자를 검토자·학습자 2층으로 정의한다`

### Task 2: README 2층 재작성

**Files:**
- Modify: `README.md`(전면 재구성)

**Interfaces:**
- Consumes: Task 1 독자 문장
- Produces: **스택 역할 한 줄 표**. Task 3·4·5가 이 문장을 그대로 복사한다.
  착수 시 각 문장의 근거 경로를 `ls`로 확인한다.

| 기술 | 역할 한 줄 | 상세 |
| --- | --- | --- |
| Dagster | 호스트에서 도는 오케스트레이터 — 적재 자산과 dbt 자산을 정의·실행한다(Airflow로 이관 중) | `architectures/dagster.md` |
| Airflow | 클러스터 안 오케스트레이터 — Dagster에서 이관한 DAG를 실행한다 | `architectures/airflow.md`(Task 5) |
| dbt · Trino | silver/gold 변환 — dbt가 k8s Trino에서 SQL을 실행하고, Trino는 Iceberg 유지보수도 맡는다 | `architectures/trino.md` |
| Iceberg · CNPG | 테이블 포맷과 JDBC 카탈로그 — 모든 엔진이 카탈로그 `iceberg` 하나를 공유한다 | `conventions/k8s/cnpg.md` |
| SeaweedFS | S3 호환 오브젝트 스토리지 — 클러스터 밖 컨테이너(Terraform 스택 A) | `architectures/storage.md` |
| Spark | 대용량 배치 엔진 — Spark Operator가 `SparkApplication`을 실행한다 | `architectures/spark.md` |
| Flink | 스트림 엔진 PoC — Iceberg 스트리밍 읽기, 세션 클러스터는 쓸 때만 띄운다 | `architectures/flink.md` |
| Kubernetes | 로컬 단일 노드 플랫폼 — kind on Podman | `architectures/k8s.md` |
| ArgoCD | `gitops/`의 선언으로 클러스터 앱을 수렴시킨다 | `architectures/argocd.md` |
| Terraform | 스택 A(클러스터·S3) · 스택 B(ingress·ArgoCD) | `architectures/terraform.md` |
| Prometheus | 클러스터 안 메트릭 수집 | `architectures/monitoring.md` |

- [ ]  **Step 1: RED** — `wc -l README.md`와 위층 끝(「빠른 시작」 전)까지 줄 수, `grep -c '^| ' README.md`(스택 표 행
      0이어야 함)
- [ ] **Step 2: 위층 작성**(검토자, 60줄 이하)
  - 첫 문단: 무엇을 만들었나 2문장. 기존 1·3행의 의미를 유지한다.
  - 파이프라인 그림(기존 `text` 블록 유지)과 스택 역할 표(Produces)
  - **핵심 판단** 4개. 판단 한 줄과 근거 링크, 근거는 문서 안 실재 절이다.
    - Trino 제거 결정을 번복하고 dbt 엔진으로 유지 → `docs/redesign.md` 「Trino 제거 결정 번복」
    - S3를 클러스터 밖에 둠 — 재생성 때 레이크가 같이 사라지지 않게 → `docs/redesign.md` 「그 외 결정」 표
    - 카탈로그 Postgres를 CNPG로 관리 — 재기동만으로 카탈로그가 소멸한 뒤 → `docs/redesign.md` 「그 외 결정」 표
    - 엔진 버전은 최신이 아니라 Iceberg가 지원하는 짝 → `docs/architectures/spark.md`·`flink.md`
  - **교훈 하이라이트** 3개. 기존 wiki 노트 링크 `wiki/<slug>.md`와 한 줄 요지다.
    - 후보: `cross-engine-sql`, `dagster-orchestration`, `kubectl-judgement`. 요지는 각 노트 머리 인용에서 가져온다.
- [ ] **Step 3: 아래층 작성**(학습자)
  - 전제 3줄: `docs/setup.md` 「검증 환경」 표에서 OS·podman rootful·VM 크기를 옮긴다(출처 링크 병기).
  -  명령: setup.md §0·§1·§3 「올리기」 블록에서 **그대로 복사**한 핵심 명령과 수렴 확인 `kubectl get applications -n
    argocd`
    - 안내 한 줄: "명령은 루트 체크아웃에서, 상세·내리기는 setup.md"
  - 기존 「주요 작업」(dbt·노트북·검사)과 「문서 지도」는 아래층에 남긴다. 「문서 지도」는 Task 3의 4칸과 이름을 맞춘다.
  - 「AI 에이전트 작업 방식」은 맨 아래 3줄 + 링크로 줄인다(내용은 agents.md에 이미 있다).
- [ ] **Step 4: GREEN**
  - 위층 줄 수 ≤ 60
  - 아래층 명령 각 줄이 `grep -F`로 setup.md에 실재(줄마다 1 이상)
  - 핵심 판단·교훈 링크의 대상 절이 실재(`grep -n '<제목>' <파일>`)
  - `doc_lint README.md` · `--links` 위반 0
- [ ] **Step 5: Commit (사용자 요청 시)** — `docs(readme): 검토자 요약과 학습자 실행 경로 2층으로 재구성한다`

### Task 3: docs 허브를 독자별 4칸으로

**Files:**
- Modify: `docs/README.md`(재배치), `docs/setup.md:6`(제목 「지금 되는 범위」→「절별 전제」 + 링크 1곳)

**Interfaces:**
- Consumes: Task 1 독자 문장, Task 2 역할 표(아키텍처 칸의 설명 열)

- [ ] **Step 1: RED**
  - `grep -c '^## ' docs/README.md`로 현재 절 수를 센다.
  - docs/README.md의 상대 링크 대상(고유 `.md` 경로) 수를 센다. 이 값은 재배치 후 이상이어야 한다.
  - `grep -rn '지금-되는-범위' --include=*.md .`로 앵커 참조를 센다(README 1건 예상).
- [ ] **Step 2: 재배치**
  - 4칸과 순서:
    1. **둘러보기** — overview, 아키텍처 표(설명 열 = Task 2 역할 한 줄)
    2. **따라 하기** — setup, local-k8s, market-data-keys, 노트북
    3. **기여자·에이전트용 규칙** — conventions 표, test, security, risk, skills, doc-sync, philosophy, 핵심 원칙 요약
    4. **결정 기록** — redesign, argocd-gitops, `superpowers/specs`·`plans` 디렉터리 링크
  - 「데이터셋·분석」·「외부 공개」 절은 1·2칸 사이 독립 절로 둔다. 독자가 둘 다라 한 칸에 넣지 않는다.
  - 허브의 기존 링크는 **하나도 빼지 않는다**(Step 1의 링크 수 이상).
- [ ]  **Step 3: setup.md 제목 변경** — `## 지금 되는 범위` → `## 절별 전제`. Step 1에서 찾은 앵커 참조를 같은
      커밋에서 고친다.
- [ ] **Step 4: GREEN** — 링크 대상 수 ≥ Step 1, `지금-되는-범위` 참조 0, `doc_lint --links` 0
- [ ] **Step 5: Commit (사용자 요청 시)** — `docs(hub): 문서 허브를 독자별 4칸으로 재배치한다`

### Task 4: 아키텍처 템플릿 적용 (기존 15개 문서)

**Files:**
- Modify: `docs/architectures/README.md`(「각 문서 형식」 절, 목차 표 설명 열)
- Modify: `docs/architectures/` 아래 14개 — dagster·trino·spark·flink·k8s·argocd·docker·storage·terraform·
  operators·monitoring·oci·kafka·duckdb (overview.md는 Task 6)

**Interfaces:**
- Consumes: Task 2 역할 한 줄

새 템플릿(architectures/README.md 「각 문서 형식」의 정본):

```text
# <기술>
## 이 프로젝트에서의 역할     ← 신규. 첫 줄 = Task 2 역할 한 줄, 3줄 이내
## 어떻게 도나               ← 신규. 관련 파일 경로 · 흐름 · 다른 스택과의 연결(채택 문서만)
## 왜 이걸 골랐나            ← 기존 「이 프로젝트에서의 위치」 등 결정 절, 내용 그대로
## 운영 메모                 ← 기존 그대로(절차 이관은 미션 ③)
## 교훈                      ← 신규. 관련 wiki 노트 링크(있을 때만)
## 참고                      ← 기존 그대로
```

- 미채택 문서(kafka·duckdb·oci)는 「어떻게 도나」를 두지 않는다. 대신 「역할」에 "쓰지 않는다 — 대신 X" 한 줄을 둔다.
- 「교훈」은 wiki에 대응 노트가 있을 때만 둔다.
  - 매핑: dagster→`dagster-orchestration`·`dagster-partitions`, trino→`cross-engine-sql`, spark→`spark-on-k8s`
  - flink→`flink-on-k8s`·`streaming-source-choice`, k8s→`k8s-bootstrap`·`kubectl-judgement`, operators→`k8s-operators`

- [ ]  **Step 1: RED** — 문서마다 `grep -c '^## 이 프로젝트에서의 역할' <파일>` → 0. 제목을 바꿀 기존 H2 목록과 그
      앵커 참조 수를 기록한다.
- [ ]  **Step 2: architectures/README.md 형식 절 교체** — 위 템플릿. 목차 표 설명 열은 Task 2 역할 한 줄로 바꾼다(상태
      열 유지).
- [ ] **Step 3: 문서별 적용**
  -  순서: dagster → trino → spark → flink → k8s → argocd → storage → terraform → operators → monitoring → docker →
    oci → kafka → duckdb
  -  「어떻게 도나」의 경로는 문서마다 `ls`로 확인한다. 각 문서의 기존 본문(머리 인용·운영 메모)에서 이미 확인된
    경로를 우선 쓴다.
  - 기존 결정 절의 H2 제목만 「왜 이걸 골랐나」로 바꾼다. 하위 H3과 본문은 그대로 둔다.
- [ ] **Step 4: GREEN — 무손실**
  - 문서마다 `git diff -U0 -- <파일> | grep -E '^-[^-]'`의 줄이 **전부 바뀐 제목 줄**이다. 다른 삭제가 있으면 되돌린다.
  - `grep -c '^## 이 프로젝트에서의 역할'` 문서당 1
  - 문서마다 500줄 이하(operators·storage 확인)
  - `doc_lint` · `--links` 위반 0
- [ ] **Step 5: Commit (사용자 요청 시)** — `docs(architectures): 역할·동작을 결정 앞에 두는 템플릿으로 바꾼다`

### Task 5: `architectures/airflow.md` 신규

**Files:**
- Create: `docs/architectures/airflow.md`
- Modify: `docs/architectures/README.md`(목차 1행), `docs/README.md`(둘러보기 표 1행)

**Interfaces:**
- Consumes: Task 2 역할 한 줄(Airflow 행), Task 4 템플릿

- [ ] **Step 1: 근거 수집**
  - 소스는 다섯이다.
    - [Airflow on ArgoCD 설계](../specs/2026-10-11-airflow-argocd-design.md) 「결정 요약」·「구성 요소」
    - [원화 종가 Airflow 설계](../specs/2026-10-24-airflow-us-equity-krw-design.md)
    - `docs/conventions/airflow.md`
    - `gitops/apps/airflow/config.yaml`
    - `images/airflow/dags/`
  - 「왜 골랐나」에는 설계 문서에 **적힌 이유만** 쓴다.
  - 이유가 없는 부분은 쓰지 않는다. PR 본문에 "근거 부재 — 사용자 확인 필요"로 남긴다.
- [ ] **Step 2: 작성** — Task 4 템플릿대로 쓴다.
  -  「어떻게 도나」에는 DAG 파일 목록, `lakehouse` 패키지, Cosmos `DbtTaskGroup`,
    Connection←Secret←`k8s-secrets.sh`를 담는다.
  - 규칙은 conventions/airflow.md로 **링크만** 건다(중복 금지).
- [ ] **Step 3: GREEN** — 언급한 경로가 전부 `ls`로 실재, `doc_lint` · `--links` 0
- [ ] **Step 4: Commit (사용자 요청 시)** — `docs(architectures): Airflow 문서를 추가한다`

### Task 6: overview.md를 현행 토폴로지 중심으로 재구성

**Files:**
- Modify: `docs/architectures/overview.md`(425줄)

**Interfaces:**
- Consumes: Task 2 역할 표

- [ ] **Step 1: 주장 목록**
  - 현재 overview.md의 H2별로 주장을 한 줄씩 뽑아 scratchpad 표로 만든다(원문 줄 → 주장).
  - 이 표가 무손실 대조의 모집단이다. 행 수를 기록한다.
- [ ] **Step 2: 재구성** — 새 순서는 다음과 같다.
  1. **한눈에** — 현행 배치 그림 하나. `text` 블록으로 그리고, 클러스터 안 / 클러스터 밖 / 호스트 3구역으로 나눈다.
     구역별 구성은 미션 ①이 overview 머리에 정리한 목록을 따른다.
  2. **구성 요소** — 3구역별 표. 역할 열 = Task 2 역할 한 줄.
  3. **데이터 흐름** — 원천 → raw → bronze → silver/gold → 노트북 → 리포트, 단계마다 담당 엔진·파일 경로.
  4. **bronze 적재 템플릿** — 기존 절 유지.
  5. **로컬 compose(옵션)** — 기존 compose 구성도·프로세스 분리·포트 표.
     ⚠️ 머리 경고문 "이 문서의 compose 서술은 대부분 폐기 전 판본"은 실제로 폐기된 서술을 지운 뒤 다시 판정한다.
- [ ] **Step 3: GREEN — 무손실**
  - Step 1 표의 모든 행에 "새 위치" 또는 "삭제 근거"(현행과 모순 — 근거 파일 병기)를 채운다.
  - 빈 칸 0, 행 수가 Step 1과 같아야 한다.
  - 500줄 이하, `doc_lint` · `--links` 0
- [ ] **Step 4: Commit (사용자 요청 시)** — `docs(overview): 현행 배치 그림과 데이터 흐름 중심으로 재구성한다`

### Task 7: setup.md 한 갈래 정리

**Files:**
- Modify: `docs/setup.md` §3 머리(local-k8s.md 분기 문단)

- [ ] **Step 1: RED** — `grep -n '처음이라면' docs/setup.md`가 1건이다. 이 문단이 경로를 둘로 가른다.
- [ ] **Step 2: 교정**
  - 분기를 "**기본은 이 절의 단축 경로** · 층마다 무엇이 생기는지 배우려면 `setup/local-k8s.md`(학습용 수동)"로 바꾼다.
  - 명령은 바꾸지 않는다.
- [ ] **Step 3: GREEN** — `처음이라면` 0, `local-k8s.md` 링크 유지, `doc_lint` 0
- [ ] **Step 4: Commit (사용자 요청 시)** — Task 3 커밋에 합쳐도 된다(같은 파일).

### Task 8: 최종 검증과 PR

- [ ] **Step 1:** `python3 scripts/doc_lint.py` 전수 · `--links` → 0
- [ ] **Step 2: 역할 문장 단일성**
  - Task 2 표의 역할 한 줄 11개를 각각 `grep -rF`로 센다.
  - 기대: README 1 · docs/README(해당 시) 1 · 해당 아키텍처 문서 1. 변형 표현이 있으면 통일한다.
- [ ] **Step 3: 독립 리뷰**
  - `reviewer`에게 브랜치 diff 전체를 맡긴다(Agent `model` 명시).
  -  점검: Review Focus 5항, 규칙 문서 내용 불변(`git diff -- docs/conventions docs/security.md docs/risk.md` 중 Task
    1 외 0줄), 각 Ruling 타당성.
- [ ] **Step 4: 사용자에게 보여준다**
  - README 위층 전문 렌더(파일 경로)
  - 무손실 근거(Task 4 삭제 줄 목록, Task 6 대조표)
- [ ] **Step 5: PR (사용자 요청 시)**
  - push 전 `git branch -vv`로 upstream이 없음을 확인한다. `gh pr create --base main`
  -  본문에 Ruling 3건과 Task 6 대조표 요약을 넣는다. 끝에 `🤖 Generated with [Claude
    Code](https://claude.com/claude-code)`
