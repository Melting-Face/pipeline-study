# 문서 재구성 설계 — 포트폴리오(README·docs) + 스터디(wiki) + 코드 동기화

## 요약

- **왜**: README·docs가 "왜 골랐나(채택 결정)"에 치우쳐, 처음 온 사람도 규칙을 찾는 사람도 길을 잃는다.
  게다가 문서 20곳이 현재 코드와 어긋나 있다(정찰 결과, 아래 부록 A).
- **무엇**: 비유는 **포트폴리오와 스터디**다.
  -  README·docs = **포트폴리오**. 위층은 검토자용 요약, 아래층은 따라 하는 학습자용 실행 경로(사용자 결정: "둘 다,
    층을 나눠서").
  -  wiki = **스터디 노트**. 기술마다 한 장이고, 한 장 안에 개념 → 이 저장소에서의 쓰임 → 실습 → 고장 대응 → 함정을
    모두 담는다(사용자 결정).
  -  기술별 실습·고장 대응 명령의 **정본은 wiki 장**이다(사용자 결정). docs/setup은 전체 스택을 한 번에 올리는 절차만
    갖는다.
- **순서**: 미션 3개, 미션마다 PR 하나.
  ① 어긋난 사실 바로잡기 → ② 포트폴리오 재구성 → ③ wiki 스터디 재작성.
  사실을 먼저 맞추지 않으면 틀린 내용을 새 구조로 옮겨 심게 된다.

**승인 후 첫 단계**
- 이 설계를 이 문서(`docs/superpowers/specs/`)로 옮긴다. 미션 ①의 worktree에서 같은 PR에 포함한다.
- 이어서 superpowers:writing-plans로 미션 ① 계획을 쓴다. 미션 ②·③은 앞 미션이 머지된 뒤 각각 계획한다.

## PDCA

| 단계 | 내용 |
|---|---|
| Plan | 이 설계 + 미션별 writing-plans 계획 |
| Do | 미션 ①→②→③, 각자 `scripts/worktree-new.sh`로 만든 worktree·브랜치·PR |
| Check | doc_lint(전체·`--links`), 이관 무손실 토큰 diff, 낡은 표현 grep(대조군 병기), 새 Explore 에이전트로 드리프트 재대조, wiki는 reviewer 체크리스트 E |
| Act | 남은 드리프트는 Issue 한 줄(범위 동결), 규칙이 바뀌었으면 CLAUDE.md·AGENTS.md·docs 한 벌 갱신 |

## 목표 / 비목표 / 반(反)목표

**목표**
- 검토자는 README 위층만 읽고 몇 분 안에 세 가지를 안다: 무엇을 만들었나, 스택이 어떻게 맞물리나, 어떤 판단을 했나.
- 학습자는 한 갈래 경로로 클론부터 기동까지 간다. README → setup 하나이고, 중간에 갈라지지 않는다.
- wiki 한 장만 읽고도 그 기술을 이해하고, 띄우고, 고칠 수 있다.
- 문서의 사실 서술이 현재 코드·설정과 일치한다.

**비목표**
- 코드·설정의 동작 변경. 예외는 `.env.example`의 주석·누락 키 보강 정도이고, 동작은 바꾸지 않는다.
- 새 기술 도입, 새 lint 게이트 신설.

**반목표** — 기술적으로 완성돼도 이렇게 되면 실패다(사용자 지정)
1.  **같은 내용이 여러 곳에 있다.** 절차는 wiki, 규칙은 conventions, 결정은 architectures에 한 곳씩만 두고, 나머지
   자리는 링크로 잇는다.
2.  **규칙 문서가 약해진다.** conventions·security·risk·agents·skills는 **내용을 줄이지 않는다.** 사실 교정과 동선
   재배치만 한다.
   삭제·병합 전에 유효 규칙을 하나씩 대조한다(메모리 simplification-can-relax-controls).
3. **여전히 길고 빽빽하다.** 한 문장에 한 생각, 머리에는 3줄 요약을 두고, 표는 대조할 때만 쓴다.
4. **교훈·판단이 사라진다.** 회고와 결정 근거는 지우지 않는다.
   - 옮길 때 문장은 짧게 **다시 써도 된다**(사용자 결정).
   - 대신 원문에서 주장·교훈을 한 줄씩 뽑아 **주장 목록**을 만들고, 이관본에 하나씩 대응하는지 대조해 무손실을 증명한다.
   - 대조표(원문 주장 → 이관본 위치)는 PR 본문에 붙인다.

## 제약 (기존 게이트·규칙)

- `scripts/doc_lint.py`: 한 줄 120 표시폭(한글은 2칸), 표 셀 200, 🔴 5개, 문서 500줄, 괄호 중첩 금지.
  일자 금지(wiki는 면제), 시제 어휘 금지(wiki 포함), 상대 링크·`#앵커` 실재 검사.
  wiki 노트는 Home·_Sidebar 양쪽에 등재해야 한다(`unindexed` 차단).
- `docs/`는 3축(재현·시제·위협)을 따른다. 실측 수치·일자·상태를 두지 않는다(doc-sync.md §실무 규칙 7).
- wiki 규칙(publishing.md §4-1): 하위 디렉터리 금지, 영문 kebab-case 파일명, 한국어 H1, 프론트매터 금지,
  관측하지 않은 출력을 지어내지 않는다. 커밋 전 reviewer 체크리스트 E와 사용자 확인을 받는다. **공개 위키로 미러된다.**
- `§` 절 번호 참조는 어떤 게이트도 잡지 않는다. 절을 옮기거나 분할하기 전에 `§` 참조를 먼저 센다.
- 공유 트리 규칙: 루트는 읽기 전용이다. 미션마다 worktree를 쓰고, 커밋·푸시는 사용자가 요청할 때만 한다.

## 미션 ① 사실 바로잡기 (구조 불변)

부록 A의 20건을 고친다. 구조나 문체는 건드리지 않는다.
- 높음 6건: PR2 대기 서술(README·setup), redesign.md의 Dagster in-cluster, Trino "제거",
  overview.md 토폴로지(Trino·Airflow·Prometheus 누락, SeaweedFS는 클러스터 밖, core는 빈 profile),
  `var.apps` 서술, `dg check defs` 전제.
- 중간·낮음 14건:
  - 데이터셋과 `common/` 목록(CLAUDE.md·AGENTS.md 포함, 사실만 고침)
  - SeaweedFS 소유(compose가 아니라 스택 A)
  - Spark Connect·dbt-spark 잔여 서술
  - `.env.example` 누락 키 `TRINO_*`·`DBT_TARGET`·`FLINK_*`·`POC_*`와 미구현 주석
  - 공개 wiki 3곳(Home·spark-on-k8s·kubectl-judgement) — 체크리스트 E 대상
  -  템플릿 README 2개(dg·dbt 스타터)는 짧은 안내로 교체하거나 삭제. **빌더 판단, 삭제를 택하면 PR 본문에 근거를
    적는다.**
-  루트에 git 미추적 빈 디렉터리(`dagster_project/defs/`·`tests/`)가 있다. 로컬 잔재라 이 PR 범위 밖이다. 사용자에게
  한 줄로 알린다.

## 미션 ② 포트폴리오 재구성 (README·docs)

**README.md — 두 층**
- **위층(검토자용, 한 화면 남짓)**
  - 한 줄 정의와 파이프라인 그림
  - 스택 표: 기술 / 이 프로젝트에서의 역할 한 줄 / 상세 링크
  - 핵심 판단 3~5개: 판단 한 줄 + 근거 링크
  - 교훈 하이라이트: wiki 장 링크
- **아래층(학습자용)**
  - 전제: OS, rootful podman, VM 자원
  - 명령 5줄 안팎과 수렴 확인 1줄 → 상세는 setup.md
  - 동작하는 명령만 싣는다. 미션 ①에서 확인한 것만 쓴다.
- AI 에이전트 작업 방식은 맨 아래로 내리고 링크만 둔다.

**docs/README.md — 허브를 독자별 4칸으로**
1. **둘러보기**: overview, 스택 문서
2. **따라 하기**: setup, local-k8s
3. **기여자·에이전트용 규칙**: conventions, security, risk, test, skills, doc-sync, philosophy
4. **결정 기록**: redesign, argocd-gitops, `superpowers/specs·plans`

파일 이동은 최소화한다(링크·`§` 참조 보존). 동선은 허브 재배치로 만든다.

**docs/architectures/*.md — 템플릿을 "역할 먼저"로 바꾼다**
- 새 순서:
  1. 이 프로젝트에서의 역할(3줄)
  2. 어떻게 도나 — 관련 파일, 흐름, 다른 스택과의 연결
  3. 왜 이걸 골랐나 — 기존 결정과 대안 비교, **내용 보존**
  4. 교훈 → wiki 장 링크
- 기존 "운영 메모"는 성격대로 나눈다. 절차는 wiki로 옮기고, 결정 근거는 3번 절에 남긴다.
- `architectures/README.md`의 형식 정의도 같이 바꾼다.
- 빠진 `architectures/airflow.md`를 새로 쓴다.
- overview.md는 현행 토폴로지 기준으로 다시 쓴다. 철거 전 스냅샷은 "결정 기록"으로 보내거나 지운다.

**docs/setup.md**
- "지금 되는 범위" 상태표를 뺀다.
- 전체 스택을 한 번에 올리는 한 갈래 절차만 둔다. local-k8s.md와의 분기는 "자동(기본) / 층별 수동(학습용)"으로 명시한다.
- 기술별 확인·고장 대응은 wiki 장 링크로 대신한다.

**규칙 갱신 — 한 벌로**
- 대상: CLAUDE.md, AGENTS.md, doc-sync.md, publishing.md §4-1, conventions/general.md(문서 유형 정의가 있으면).
- 독자 정의를 바꾼다. 기존 "클론해 돌리려는 사람·에이전트"를 **"검토자(위층)·학습자(아래층)"**로 고친다.
- wiki 역할을 바꾼다. 기존 "학습 노트·별개의 글"을 **"기술별 스터디 장 + 실습·고장 대응 절차의 정본"**으로 고친다.
- CLAUDE.md는 모든 요청에 곱해지는 비용이다. 순증 바이트를 0 이하로 맞춘다.

## 미션 ③ wiki 스터디 재작성

**장 템플릿** (한 장 = 한 기술 또는 방법론)
1. **개념**: 이 기술이 무엇인지, 외부 1차 문서 링크
2. **이 저장소에서의 쓰임**: 실재하는 파일 경로·YAML·코드 인용, 흐름
3.  **실습**: 띄우기 → 확인 → 내리기를 명령으로. 확인 단계에는 "무엇이 보이면 성공인지"를 쓴다. 출력 예시는 실제로
   관측한 것만 싣는다.
4. **고장 대응**: 증상 → 먼저 볼 것 → 처방
5. **함정**: 기존 회고 이관. 무손실로 옮긴다.

**장 후보**
-  기술 장(빌더가 최종 확정): Dagster, dbt+Trino(cross-engine SQL), Spark on K8s, Flink on K8s, kind 부트스트랩,
  오퍼레이터,
  ArgoCD GitOps, Iceberg 카탈로그(CNPG), SeaweedFS(S3), 모니터링(Prometheus), Airflow, Terraform
- 방법론 장: 성공 신호를 의심한다(how-to-read 확장), 분석 3층·메달리온, Claude Code 에이전트 운영

**기존 노트 10편**
- 장으로 흡수하고, 매핑표를 계획에 넣는다.
- 공개 위키 URL은 slug가 바뀌면 깨진다. **기존 slug를 장 이름으로 쓰는 쪽을 우선한다.**
-  장이 500줄을 넘으면 `<slug>.md`(개념·쓰임·실습)와 `<slug>-pitfalls.md`(고장 대응·함정) 2쪽으로 나눈다.
  flink-on-k8s(500줄)가 첫 대상이다.

**실습 관측 — 실제로 띄워서 받는다(사용자 결정)**
- 미션 ③에서 kind 클러스터를 올리고, 장마다 띄우기 → 확인 → 내리기를 실제로 돌려 받은 출력만 싣는다.
- 기동 절차(`k8s-up.sh` → `terraform apply` 스택 A·B)는 비가역 게이트 대상이다(risk.md §4).
  실행 직전에 reviewer 보안 체크리스트 1회와 사용자 승인을 받는다. 승인을 받은 턴에서 중단 조건을 다시 잰다.
- 착수 전 Podman VM 시계를 확인한다(수면 뒤 시계가 밀리면 S3 RequestTimeTooSkewed가 난다).
-  검증용으로 띄운 상주 컴퓨트는 그 자리에서 내린다. 회수는 `podman stop`으로 하고, `k8s-down.sh`(클러스터 삭제)는
  쓰지 않는다.
- 공개 위키에 싣는 출력은 체크리스트 E로 거른다. 호스트 경로·계정명·키·내부 IP는 싣지 않는다.

**발행·검증**
- 실습 명령은 저장소의 스크립트·매니페스트와도 대조한다.
- 공개 위키라 커밋 전에 reviewer 체크리스트 E와 사용자 확인을 받는다.
- Home·_Sidebar를 장 구조로 다시 짠다.

## 내가 정한 것 (검토 질문에 대한 제 판단 — 읽으며 확인해 주세요)

-  **규칙 문서 안의 절차는 그대로 둔다.** conventions·local-k8s 등에 있는 명령 중 규칙을 집행하는 절차가 있다(예: CNPG
  비밀번호 회전 한 벌).
  이런 것은 옮기지 않고, wiki 장은 해당 절로 **링크**만 건다. 중복 없이 반목표 1·2를 함께 지키는 방법이다.
  wiki가 정본으로 갖는 것은 규칙 문서에 없는 기술별 기동·확인·고장 대응뿐이다.
-  **기존 wiki slug는 하나도 없애지 않는다.** 노트를 합치지 않는다. 기존 노트는 같은 slug로 장이 되거나 장의 하위
  쪽(`-pitfalls` 등)이 된다.
  공개 URL이 깨지지 않는다.
-  **airflow.md의 "왜 골랐나"는 `docs/superpowers/specs/`의 Airflow on ArgoCD 설계에서 가져온다.** 근거가 없는 부분은
  지어내지 않고 비워 둔 채 사용자에게 묻는다.
- 미션 ①은 아래 부록 A의 건별 "맞는 값"을 정답으로 쓴다. 착수 시 해당 근거 파일을 다시 열어 확인한다.

## 빌더에게 남긴 것

- 장의 최종 목록, 분할 경계, 이관 매핑의 세부
- 템플릿 README 2개를 교체할지 삭제할지
- 미션 ①을 PR 1개로 할지 2개로 할지(높음 / 나머지)
- overview.md의 낡은 스냅샷을 지울지 결정 기록으로 옮길지
- wiki에서 저장소 파일로 가는 링크의 형식. 확인처는 `scripts/wiki_linkify.py`이고, 상대 링크가 미러에서 살아남는지 본다.

## 검증 (미션 공통)

1. `python3 scripts/doc_lint.py`와 `python3 scripts/doc_lint.py --links`를 repo 루트에서 돌린다. 기존 위반 0을 유지한다.
2.  이관 무손실: 옮긴 절마다 주장 목록 대조표에서 대응 없는 항목이 0건이어야 한다. 표의 행 수는 원문 주장 수와 같아야
   한다.
3. 낡은 표현 grep: `PR2`, `in-cluster`, `Spark Connect`(현재형), `Trino 제거`, `dbt-spark`.
   **대조군 문자열을 같은 호출에서 함께 센다.** 0건이 "검사가 안 돌았다"와 구분되게 한다.
4. 미션마다 끝나면 새 Explore 에이전트로 부록 A를 다시 대조한다. 0건이면 그 대조의 모집단을 함께 적는다.
5. `§` 참조 개수를 재배치 전후로 센다. 깨진 참조는 0건이어야 한다.
6. wiki: reviewer 체크리스트 E, 그리고 Home·Sidebar 등재(`--links` 통과).

## 부록 A — 드리프트 20건 (정찰 결과 요약)

높음 6 + 중간 10 + 낮음 4 = 20건. 형식: 위치 — 문서의 주장 → **맞는 값**(근거)

**높음**
1. README.md:37-39, docs/setup.md:7-17·88·248·289 — "데이터 층은 PR2에서, 적재·dbt는 안 돈다(⏸)"
   → **PR2·PR3-B 머지 완료**(gitops/apps/{storage-external,catalog-postgres,trino,airflow,prometheus},
   scripts/k8s-secrets.sh, #174·#183·#196·#220)
2. docs/redesign.md:24-36·58-63 — "Dagster in-cluster, 호스트 `dg dev` 폐기, CNPG에 dagster DB"
   → **호스트 실행, in-cluster 철거, dagster DB 없음**, Airflow도 토폴로지에 있음(compose.yml:62-66,
   operations.md:85-89, architectures/dagster.md:14-16)
3. docs/README.md:31, architectures/README.md:12 — "Trino 제거(대상)"
   → **Trino가 dbt 유일 엔진이자 유지보수 엔진, 제거 번복**(gitops/apps/trino/config.yaml, dbt_pipelines/profiles.yml
   `type: trino`, common/trino.py, #220·#231)
4.  architectures/overview.md:12-14·23·30 — 구성 목록에서 Trino·Airflow·Prometheus 누락, SeaweedFS in-cluster, 메타
   Postgres 있음, "core는 항상 뜬다"
   → **SeaweedFS는 클러스터 밖(스택 A), 메타 Postgres 없음, compose 전 서비스에 profiles(core 비어 있음)**
5. docs/setup.md:148 — "앱 목록은 terraform/platform/variables.tf의 `apps`"
   → **`var.apps` 없음, 앱 목록은 `gitops/apps/*/config.yaml`**(#219, argocd-gitops.md:86-94)
6. README.md:81 vs setup.md:16·216 — README는 `uv run dg check defs`를 정상 경로로 제시
   → setup은 "의존성 결함으로 실패"라 적고 원인 미기재, `DAGSTER_HOME`·`.env` 전제 누락, CI에서도 빠짐(ci.yml:259)
   → **착수 시 실제 실패 원인을 재현해 확정한다.**

**중간**
7. CLAUDE.md:91-95, conventions/dagster.md:143-161, overview.md:237-251 — common·defs·automation 목록
   → common에 `fetch`·`physionet`·`polygon`·`fred` 추가, defs에
   `usgs_water`·`frankfurter_fx`·`polygon_market`·`fred_calendar`·`stock_forecast`·`flink`·`poc`·`raw_assets.py` 추가,
   automation은 잡·스케줄 9쌍 이상
   → 실제 경로는 `dagster/dockerfile.d/src/src/dagster_project/`
8.  .env.example:84·89-92 — "fred 미구현, `raise_masked()` 필요" → **구현됨**(common/fred.py:16 `FredResource`,
   defs/fred_calendar/assets.py, resources.py:273·291)
9.  operations.md:47-54 — compose `trino`·`seaweedfs`·in-cluster daemon env 서술 → **compose에는
   dagster-webserver·dagster-daemon·postgres뿐**
10.  setup.md:174, conventions/k8s.md:15, resource-sizing.md:111·320, redesign.md:33 — "SeaweedFS는 compose" → **스택
    A `docker_container`**(terraform/cluster/kind/main.tf:154-175)
11. architectures/k8s.md:24-26 — "SeaweedFS를 K8s에 배포, Trino 제거" → **SeaweedFS는 밖, Trino는 안**
12. architectures/argocd.md:16·41 — "`k8s-secrets.sh` 아직 없음" → **있음**
13.  argocd-gitops.md:22·70 — "Spark Connect 기동/회수" → **Connect 철거**(#236), `k8s/spark/`에는
    `sparkapplication-poc.yaml`만 남음
14. architectures/README.md:16 — "dbt-spark 마트" → **dbt-spark 제거**
15.  conventions/analysis.md:178-181 — "Spark Connect가 상시 소비, 집계는 Spark에서" → **Connect 철거, SQL 엔진은
    Trino**(같은 문서 172행과 모순)
16. dataset_schema.md:183-184 — "실버 모델은 MIMIC-IV만" → **`models/stock_forecast/tables/`에 10개 더 있음**

**낮음**
17. dagster/dockerfile.d/src/README.md, dbt_pipelines/README.md — 스캐폴드 원문(`pip install -e ".[dev]"`는 무효)
18.  env 키 — `TRINO_*`·`DBT_TARGET`·`TRINO_USER`·`FLINK_*`·`POC_*`가 .env.example에 없음(common/constants.py:65-67,
    defs/flink, defs/poc가 읽음)
    → `NEWS_RSS_USER_AGENT`는 Dagster 코드가 읽지 않음(scripts/stock_source_access_probe.py만 씀)
19.  공개 wiki — Home.md:72-75 "Dagster in-cluster", spark-on-k8s.md:8-9 "Spark Connect 사용"(현재형),
    kubectl-judgement.md:108-114 `deploy/spark-connect` 예시. Trino·Airflow 언급 없음
20. 기타
    - docs/README.md: argocd-gitops "리뷰 대기"(구현됨), Dagster "K8s in-cluster", airflow 문서 부재
    -  "메타 DB 위치는 PR2가 정한다" 5곳(k8s.md:448, operations.md:104, resource-sizing.md:401, compose.yml:111,
      .env.example:16)
    - test.md에 Airflow DAG 테스트(`images/airflow/tests/dag-import.test.sh`) 누락

**일치 확인됨(대조 모집단)**: 헬퍼·리소스 이름, dbt 매크로·타깃, compose profiles, CNPG `-rw`, mimic 모델 22개·source
 테이블 수
