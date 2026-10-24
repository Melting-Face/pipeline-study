# 미션 ① 문서–코드 사실 동기화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** README·CLAUDE.md·AGENTS.md·docs·wiki·`.env.example`의 사실 서술 20건을 현재 코드·설정에 맞춘다. 구조와
 문체는 바꾸지 않는다.

**Architecture:** 파일 묶음별 태스크 6개와 최종 검증 1개로 나눈다.
각 태스크의 "테스트"는 낡은 표현 grep이다. 고치기 전 >0, 고친 뒤 0이 나와야 하고, 같은 호출에서 대조군 문자열을 함께
센다.
여기에 `doc_lint`를 더한다.

**Tech Stack:** Markdown, `scripts/doc_lint.py`(호스트 `python3`), `grep -c`, `git`.

**Spec:** [설계 문서](../specs/2026-10-24-docs-portfolio-study-design.md) — 「미션 ①」과 「부록 A」(건별 *맞는
 값*과 근거 파일)

## Global Constraints

- 구조·문체 불변. 사실 서술만 고친다. 절 추가·삭제·이동은 하지 않는다(그건 미션 ②의 몫).
- 규칙 문서(conventions·security·risk·agents·skills)는 **사실 교정 외에 내용을 줄이지 않는다.**
- `doc_lint`: 한 줄 120 표시폭(한글은 2칸), 일자 `20YY-MM-DD` 금지(wiki 면제), 괄호 중첩 금지.
- 시제 어휘(`TODO`·`예정이다` 등) 금지. <!-- tense-ok -->
-  `docs/`에 실측 수치·일자·상태를 새로 넣지 않는다(doc-sync.md §실무 규칙 7). "PR2 머지 완료" 같은 이력 서술 대신
  **현행을 현재형으로** 쓴다.
-  부록 A의 줄 번호는 정찰 시점의 값이다. 편집 전에 해당 근거 파일을 **다시 열어** 맞는 값을 확인한다. 코드와 문서가
  어긋나면 코드가 사실이다.
- CLAUDE.md는 모든 요청에 곱해지는 비용이다. 사실 교정으로 순증하는 바이트를 최소화한다(목록은 나열 대신 요약 표현).
- 미션 ① 전용 worktree(`scripts/worktree-new.sh docs/sync-docs-with-code`)에서만 쓴다.
  **커밋·푸시는 사용자가 요청할 때만** 한다.
  아래 Commit 단계는 그 요청이 있을 때 실행한다.
-  커밋 메시지: Conventional Commits, `docs(scope): 한국어 설명`, 제목 72자 이내. 끝에 `Co-Authored-By: Claude Opus
  5.5 <noreply@anthropic.com>`.

## Review Focus

1.  **역방향 오교정** — 이력·철거 기록으로 *의도적으로* 남긴 서술(예: "in-cluster 판본은 철거됐다", "Spark Connect
   철거")을 현행 서술로 착각해 지우는 실수. grep 히트마다 문맥이 "현행 주장"인지 "이력"인지 판정하고, 이력이면 둔다.
2.  **같은 사실의 다른 표현** — "Trino 제거"만 고치고 "Trino 경로는 제거 대상"·"Trino는 철거" 같은 변형을 놓친다.
   태스크마다 변형 패턴을 grep에 함께 넣는다.
3.  **새 오류 주입** — 맞는 값을 기억에 의존해 쓰는 것(예: 데이터셋 이름 철자, 앱 목록). 반드시 `ls`·`grep`으로 실물을
   뽑아 붙인다.
4.  **`§`·앵커 깨짐** — 제목 문구를 고치면 `#앵커` 링크가 깨진다. 제목은 바꾸지 않는다. 바꿔야 하면 `doc_lint
   --links`로 확인한다.
5.  **공개 wiki 반출** — wiki 교정 문장에 내부 IP·호스트 경로·계정명이 섞이는 것. Task 6의 reviewer 체크리스트 E가
   막는다.

---

### Task 1: README·setup의 진입 경로 사실 교정 (부록 A #1·#5·#6, #10의 setup.md:174)

**Files:**
- Modify: `README.md`(빠른 시작 37–39행 부근, 검사 절 81행 부근)
- Modify: `docs/setup.md`(7–17·88·148·174·216·248·289행 부근)

**Interfaces:**
- Produces: `dg check defs`의 실패 원인 한 줄 진단. Task 7의 PR 본문과 미션 ②가 README 아래층 명령을 고를 때 쓴다.

- [ ] **Step 1: 고치기 전 grep — 위반이 있음을 확인**

```bash
grep -cE 'PR2|⏸|var\.apps|`apps`' README.md docs/setup.md; grep -c 'Trino' README.md docs/setup.md
```

Expected: 첫 명령은 파일별 합계 >0, 둘째 명령(대조군)도 >0.

- [ ] **Step 2: `dg check defs` 실패 원인 재현**

`dagster/dockerfile.d/src`에서 `DAGSTER_HOME`과 `.env`를 setup.md §5대로 맞추고 `uv run dg check defs`를 실행한다.
출력의 첫 에러 줄과, 성공했다면 자산 수를 기록한다. 결과는 README·setup에 넣지 않고 PR 본문에만 적는다.

- [ ] **Step 3: 교정**

-  PR2 대기·⏸ 서술 → 데이터 층(SeaweedFS·카탈로그 CNPG·Secret)이 `gitops/apps/*`와 `scripts/k8s-secrets.sh`로
  올라온다는 현행 서술로 바꾼다. "지금 되는 범위" 표의 ⏸ 셀은 실제 상태로 고친다.
- `var.apps` → 앱 목록은 `gitops/apps/*/config.yaml`(디렉터리 하나 = 앱 하나).
- SeaweedFS "compose" → Terraform 스택 A의 `docker_container`(`terraform/cluster/kind/main.tf`).
-  README의 `dg check defs`: Step 2 결과에 맞춘다. 성공하면 전제(`DAGSTER_HOME`·`.env`)를 한 줄 덧붙이고, 실패하면
  setup.md의 경고와 같은 표현으로 맞춘다.

- [ ] **Step 4: 고친 뒤 grep + lint**

```bash
grep -nE 'PR2|⏸|var\.apps' README.md docs/setup.md; grep -c 'Trino' README.md docs/setup.md
python3 scripts/doc_lint.py README.md docs/setup.md
```

Expected: 첫 명령 출력이 0줄이거나, 남은 히트가 전부 이력 서술이다(Review Focus 1). 대조군은 >0이고, lint는 `위반 0건`.

- [ ] **Step 5: Commit (사용자 요청 시)** — `docs(setup): 데이터 층·앱 목록·dg check 서술을 현행에 맞춘다`

### Task 2: 아키텍처·허브 문서의 토폴로지 교정 (부록 A #2·#3·#4·#11·#12·#14·#20-a)

**Files:**
- Modify: `docs/redesign.md`(24–36·58–63), `docs/architectures/overview.md`(12–14·23·30)
- Modify: `docs/architectures/k8s.md`(24–26), `docs/architectures/argocd.md`(16·41)
- Modify: `docs/architectures/README.md`(12·16), `docs/README.md`(31, argocd-gitops·Dagster 행)

**Interfaces:**
- Consumes: 실물 목록 `ls gitops/apps/`, `compose.yml`의 서비스·profiles
-  Produces: 현행 구성 문장 하나 — "클러스터 안: ArgoCD·Spark Operator·Flink Operator·CNPG
  카탈로그·Trino·Airflow·Prometheus·ingress-nginx / 클러스터 밖: SeaweedFS(스택 A) / 호스트: Dagster". 이 문장은 `ls
  gitops/apps/`로 확정한 뒤 Task 3·6이 같은 표현을 재사용한다.

- [ ] **Step 1: 고치기 전 grep**

```bash
grep -cE 'in-cluster|Trino.{0,6}(제거|철거)|dbt-spark|아직 없다|리뷰 대기|dagster DB|항상 뜬다' docs/redesign.md docs/architectures/{overview,k8s,argocd,README}.md docs/README.md
grep -c 'Iceberg' docs/redesign.md docs/architectures/overview.md
```

Expected: 첫 합계 >0, 대조군 >0.

- [ ]  **Step 2: 실물 확정** — `ls gitops/apps/`, `grep -n 'profiles' compose.yml`, `grep -n 'type:'
      dagster/dockerfile.d/src/dbt_pipelines/profiles.yml`

- [ ] **Step 3: 교정**

-  redesign.md: Dagster의 in-cluster·Deployment ×2·`dg dev` 폐기·dagster DB 서술 → 호스트 실행, in-cluster 철거,
  Airflow가 토폴로지에 있음. 문서가 *로드맵*이라 원래 계획을 서술하는 줄이면 "(이후 번복: 호스트 실행)" 한 구절만
  덧붙인다.
-  overview.md: 구성 목록을 Task 2의 Produces 문장으로 바꾼다. "core는 항상 뜬다" → "compose의 모든 서비스가 profile
  opt-in이다(core 비어 있음)". 메타 Postgres 언급을 제거한다.
- k8s.md: "SeaweedFS를 K8s에 배포(Trino 제거)" → SeaweedFS는 클러스터 밖, Trino는 안.
- argocd.md: "`k8s-secrets.sh` 아직 없다" → 있다.
-  architectures/README.md: Trino·overview 행의 "제거 대상"을 고친다. spark 행의 "dbt-spark 마트"를 지운다(dbt 엔진은
  Trino).
-  docs/README.md: Trino 행 → 채택(dbt 엔진·유지보수). argocd-gitops → 구현됨. Dagster 컨벤션 설명의 "K8s in-cluster
  배포"를 지운다.

- [ ]  **Step 4: 고친 뒤 grep + lint** — Step 1의 첫 명령을 `-n`으로 다시 돌린다. 남은 히트는 전부 이력 서술이어야
      한다. 대조군 >0, `python3 scripts/doc_lint.py <수정 파일들>` → `위반 0건`.

- [ ] **Step 5: Commit (사용자 요청 시)** — `docs(architectures): 현행 토폴로지(Trino·Airflow·외부 S3)로 교정한다`

### Task 3: 운영·컨벤션 문서의 잔여 서술 교정 (부록 A #9·#10·#13·#15·#16·#20-b·#20-c)

**Files:**
- Modify: `docs/operations.md`(47–54·104), `docs/conventions/k8s.md`(15·448), `docs/resource-sizing.md`(111·320·401)
- Modify: `docs/redesign.md`(33), `docs/argocd-gitops.md`(22·70), `docs/conventions/analysis.md`(178–181)
- Modify: `docs/dataset_schema.md`(183–184), `docs/test.md`(테스트 계층 표)

**Interfaces:**
- Consumes: Task 2의 현행 구성 문장

- [ ] **Step 1: 고치기 전 grep**

```bash
grep -cE 'compose.{0,10}seaweedfs|seaweedfs.{0,10}compose|PR2가 정한다|Spark Connect|MIMIC-IV만' docs/operations.md docs/conventions/k8s.md docs/resource-sizing.md docs/redesign.md docs/argocd-gitops.md docs/conventions/analysis.md docs/dataset_schema.md
grep -c 'dag-import' docs/test.md; grep -c 'dbt' docs/test.md
```

Expected: 첫 합계 >0, `dag-import` 0, 대조군 `dbt` >0.

- [ ] **Step 2: 교정**

-  operations.md: compose에 `trino`·`seaweedfs`·in-cluster daemon이 있다는 서술 → compose에는
  `dagster-webserver`·`dagster-daemon`·`postgres` 셋뿐이다(`compose.yml`로 확인).
-  SeaweedFS "compose" 4곳(k8s.md:15, resource-sizing.md:111·320, redesign.md:33) → 스택 A `docker_container`.
  resource-sizing.md:320의 "`compose.yml`의 `seaweedfs` command" → `terraform/cluster/kind/main.tf`의 해당 리소스.
-  "메타 DB 위치는 PR2가 정한다" 3곳(k8s.md:448, operations.md:104, resource-sizing.md:401) → 메타 Postgres는 따로
  없다. Airflow 메타는 CNPG의 별도 롤·DB다(CLAUDE.md 인프라 절과 같은 표현).
- argocd-gitops.md: 사람 소유 작업에서 "Spark Connect" 기동·회수를 지우고 Flink 세션만 남긴다.
- analysis.md:178–181: Spark Connect 상시 소비·"집계는 Spark에서" → SQL 엔진은 Trino(같은 문서 172행과 일치시킨다).
-  dataset_schema.md:183: "실버는 MIMIC-IV만" → `ls dagster/dockerfile.d/src/dbt_pipelines/models/`로 실버 모델이 있는
  데이터셋을 뽑아 적는다.
-  test.md: 테스트 계층 표에 Airflow DAG import 테스트(`images/airflow/tests/dag-import.test.sh`) 한 행을 추가한다.
  `conventions/docker.md`의 기존 서술과 같은 표현을 쓴다.

- [ ]  **Step 3: 고친 뒤 grep + lint** — Step 1의 첫 명령을 `-n`으로 다시 돌린다. 이력 외 히트 0, `dag-import` ≥1,
      대조군 >0, lint `위반 0건`.

- [ ] **Step 4: Commit (사용자 요청 시)** — `docs(ops): SeaweedFS 소유·메타 DB·Spark Connect 잔여 서술을 고친다`

### Task 4: 코드 목록 교정 — CLAUDE.md·AGENTS.md·dagster 컨벤션 (부록 A #7)

**Files:**
-  Modify: `CLAUDE.md`(「공통 라이브러리 + 자동발견 정의」 절), `AGENTS.md`(같은 내용 절, 78행 부근
  `dagster_project/common/` 표기)
- Modify: `docs/conventions/dagster.md`(143–161), `docs/architectures/overview.md`(237–251 트리)

**Interfaces:**
-  Consumes: 실물 목록. `P=dagster/dockerfile.d/src/src/dagster_project`, `ls $P/common $P/defs`, `grep -cE
  '^[a-z_]+_(job|schedule) *=' $P/defs/automation.py`

- [ ] **Step 1: 고치기 전 grep**

```bash
for f in fetch physionet polygon fred usgs_water stock_forecast; do printf '%s ' $f; grep -c "$f" docs/conventions/dagster.md; done; grep -c 'helper' docs/conventions/dagster.md
```

Expected: 일부 항목이 0이고, 대조군 `helper` >0.

- [ ] **Step 2: 교정**

-  conventions/dagster.md와 overview.md 트리: `ls` 결과대로 `common/`·`defs/<dataset>/`·`raw_assets.py`를 채운다.
  automation은 "dbt_all_job·dbt_all_schedule" 대신 "데이터셋별 잡·스케줄"로 쓴다(개수는 적지 않는다 — 시제 축).
-  CLAUDE.md·AGENTS.md: `common/` 목록에 새 모듈을 **한 줄 요약으로** 더한다(예: "원천 접속
  헬퍼(`fetch`·`physionet`·`polygon`·`fred`)"). 데이터셋 이름은 나열하지 않는다. 경로 표기에는 실제 위치
  `dagster/dockerfile.d/src/src/` 접두가 처음 한 번 보이게 한다.
- 두 파일은 같은 사실을 같은 표현으로 갖는다(codex.md 동기화 규칙).

- [ ] **Step 3: 고친 뒤 확인**

```bash
for f in fetch physionet polygon fred usgs_water stock_forecast; do printf '%s ' $f; grep -c "$f" docs/conventions/dagster.md; done
git diff --stat -- CLAUDE.md AGENTS.md
python3 scripts/doc_lint.py CLAUDE.md AGENTS.md docs/conventions/dagster.md docs/architectures/overview.md
```

Expected: 모든 항목 ≥1, CLAUDE.md 변경은 한 자릿수 줄, lint `위반 0건`.

- [ ] **Step 4: Commit (사용자 요청 시)** — `docs(dagster): common·defs 목록을 현행 모듈에 맞춘다`

### Task 5: 설정 예시·템플릿 README 교정 (부록 A #8·#17·#18, #20-b의 compose.yml·.env.example)

**Files:**
- Modify: `.env.example`(16·84·89–92행 부근, 누락 키 추가)
- Modify: `compose.yml`(111행 **주석만**)
- Modify or Delete: `dagster/dockerfile.d/src/README.md`, `dagster/dockerfile.d/src/dbt_pipelines/README.md`

**Interfaces:**
-  Consumes: 코드가 읽는 키 목록. `grep -rhoE 'EnvVar\("[A-Z_]+"\)|environ(\.get)?\(?\[?"[A-Z_]+"'
  dagster/dockerfile.d/src/src | sort -u`

- [ ] **Step 1: 누락 키 판정**

위 명령의 키 집합에서 `grep -oE '^#? *[A-Z_]+=' .env.example`의 키 집합을 뺀다.
Expected: `TRINO_HOST`·`TRINO_PORT`·`TRINO_USER`·`DBT_TARGET`·`FLINK_*`·`POC_*` 계열이 차집합에 나온다. 정확한 이름은
이 출력이 정본이다.

- [ ] **Step 2: 교정**

-  `.env.example`: 차집합 키를 기존 블록 형식(주석 + `KEY=`)으로 추가한다. 기본값은 코드의 기본값을 옮기고, 비밀은 빈
  값으로 둔다. fred "미구현"과 `raise_masked()` 주석 → 구현됨(`common/fred.py` `FredResource`). 16행의 "PR2가 정한다"
  → Task 3과 같은 표현.
-  `NEWS_RSS_USER_AGENT` 주석: "Dagster 코드가 아니라 `scripts/stock_source_access_probe.py`가 읽는다"로 고친다. 키는
  지우지 않는다.
- `compose.yml:111`: 주석만 Task 3과 같은 표현으로 고친다. YAML 값은 건드리지 않는다.
-  템플릿 README 2개: 짧은 안내(이 디렉터리가 무엇인지 한 줄 + `docs/setup.md`·`docs/conventions/dagster.md` 링크)로
  **교체**한다. 삭제하지 않는 이유: dg 스캐폴드 도구가 재생성할 수 있고, 안내 한 줄이 잘못된 명령보다 낫다(내 판단).

- [ ] **Step 3: 확인**

```bash
docker compose config --quiet; echo "compose_exit=$?"
```

Step 1의 차집합을 다시 계산한다. Expected: `compose_exit=0`, 차집합 비어 있음. 의도적으로 뺀 키는 그 이유를 PR 본문에
적는다.

- [ ] **Step 4: Commit (사용자 요청 시)** — `docs(env): .env.example 누락 키와 낡은 주석을 고친다`

### Task 6: 공개 wiki 3곳 교정 (부록 A #19) — 🔴 공개 반출 게이트

**Files:**
- Modify: `wiki/Home.md`(72–75), `wiki/spark-on-k8s.md`(8–9), `wiki/kubectl-judgement.md`(108–114)

**Interfaces:**
- Consumes: Task 2의 현행 구성 문장

- [ ] **Step 1: 고치기 전 grep**

```bash
grep -nE 'in-cluster|Spark Connect|spark-connect' wiki/Home.md wiki/spark-on-k8s.md wiki/kubectl-judgement.md; grep -c 'Spark' wiki/spark-on-k8s.md
```

- [ ] **Step 2: 교정**

- Home.md: "Dagster in-cluster 배포" → 호스트 실행. SQL·dbt 엔진이 Trino라는 사실을 한 구절 더한다.
- spark-on-k8s.md:8–9: Spark Connect를 현재형으로 쓴 것 → 과거형("썼다가 철거했다").
-  kubectl-judgement.md: `deploy/spark-connect` 예시는 **회고 속 관측이면 둔다**(관측하지 않은 출력을 지어내면 안
  된다). 다만 첫 등장에 "(철거된 배포)" 한 구절을 단다.

- [ ]  **Step 3: lint** — `python3 scripts/doc_lint.py wiki/Home.md wiki/spark-on-k8s.md wiki/kubectl-judgement.md &&
      python3 scripts/doc_lint.py --links` → 위반 0, 미등재 0

- [ ]  **Step 4: reviewer 체크리스트 E** — `reviewer` 에이전트(Agent `model` 명시)에게 이 세 파일의 diff만 넘겨
      publishing.md 체크리스트 E를 점검받는다. 발견이 0이 아니면 고치고 다시 받는다.

- [ ] **Step 5: 사용자 확인** — diff와 reviewer 결과를 보여주고 공개 반영 승인을 받는다. 승인 없이는 커밋하지 않는다.

- [ ] **Step 6: Commit (사용자 요청 시)** — `docs(wiki): Dagster 호스트 실행·Spark Connect 철거를 반영한다`

### Task 7: 최종 검증과 PR

**Files:** 없음(검증만). PR 본문을 쓴다.

- [ ] **Step 1: 전체 lint** — `python3 scripts/doc_lint.py; python3 scripts/doc_lint.py --links` → 둘 다 위반 0건

- [ ] **Step 2: 저장소 전역 낡은 표현 grep(대조군 병기)**

```bash
grep -rnE 'PR2|in-cluster|Trino.{0,6}(제거|철거)|dbt-spark|var\.apps' README.md CLAUDE.md AGENTS.md docs wiki --include=*.md | grep -v 'superpowers/' ; grep -rc 'Iceberg' README.md CLAUDE.md | head -2
```

Expected: 남은 히트는 전부 이력 서술이다. 히트마다 판정을 PR 본문에 표로 적는다. 대조군은 >0.

- [ ]  **Step 3: 독립 재대조** — 새 Explore 에이전트에게 부록 A 20건을 넘겨 "아직 어긋난 것"을 찾게 한다. 결과가
      0건이면 대조한 파일 목록(모집단)을 함께 받는다. 0이 아니면 해당 태스크로 돌아간다.

- [ ]  **Step 4: 범위 동결** — 작업 중 발견한 새 드리프트는 고치지 말고 Issue 한 줄로 남긴다. 단, 이 PR의 lint를 깨는
      것은 예외로 고친다.

- [ ] **Step 5: PR (사용자 요청 시)**
  - 설계([설계 문서](../specs/2026-10-24-docs-portfolio-study-design.md))와 이 계획 파일을 같은 PR에 포함한다.
  - `git push -u origin docs/sync-docs-with-code` 전에 `git branch -vv`로 upstream이 `origin/main`이 아닌지 확인한다.
  - `gh pr create --base main`로 연다. 본문에 다음을 넣는다.
    - 부록 A 20건 체크표
    - `dg check defs` 진단(Task 1 Step 2)
    - 이력 히트 판정표
    - 템플릿 README 교체 근거
    - 루트의 미추적 빈 디렉터리 안내 한 줄
  - 본문 끝에 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`를 붙인다.
