# PIPELINE STUDY

성격이 다른 여러 도메인의 데이터셋을 **하나의 레이크하우스 패턴**(Dagster + dbt + Iceberg)으로
적재·변환·분석하며, 그 패턴이 도메인을 갈아끼워도 성립하는지 검증하는 학습·포트폴리오다.

**파이프라인은 수단, 분석이 목적**이다. 데이터셋마다 답할 질문이 다르고, 파이프라인은
그 질문에 닿기 위한 **공통 수단**이다. 두 축이 다음처럼 나뉜다.

| 축 | 하는 일 | 규칙 정본 |
| --- | --- | --- |
| **파이프라인** | S3 → Iceberg 적재, dbt 실버 피처, 오케스트레이션 | [dagster](docs/conventions/dagster.md) · [dbt](docs/conventions/dbt.md) |
| **분석** | gold 지표·코호트, 노트북 탐색, 리포트 | [analysis](docs/conventions/analysis.md) |

현재 적재된 데이터셋과 각각의 질문은 [`docs/dataset_schema.md`](docs/dataset_schema.md)에 있다.
데이터셋을 추가하는 것은 새 축을 만드는 일이 아니라 **같은 패턴에 입력을 하나 더 붙이는 일**이다.

> 단일 호스트 Docker Compose에서 **호스트 Dagster + 로컬 Kubernetes**로 이행 중이다.
> 로드맵과 단계별 게이트는 [`docs/redesign.md`](docs/redesign.md).

## 문서 (docs)

아키텍처와 코딩 규칙은 [`docs/`](docs/README.md)에 있다.
규칙·결정·작업 패턴은 최대한 문서로 남기고, `CLAUDE.md`·`docs/`·`README.md`를
함께 갱신해 단일 출처를 유지한다.

- [환경 세팅](docs/setup.md) — **처음 여기서 시작한다**
- [코딩 철학](docs/philosophy.md) · [재설계 로드맵](docs/redesign.md)
- [전체 아키텍처 / 데이터 흐름](docs/architectures/overview.md) · [리소스 산정](docs/resource-sizing.md)
- [분석 컨벤션](docs/conventions/analysis.md) — gold 모델 / 노트북 / 리포트 3층
- [에이전트 오케스트레이션](docs/conventions/agents.md) — 아래 §AI 에이전트 구조
- 코딩 규칙 — [공통](docs/conventions/general.md) · [Python](docs/conventions/python.md) ·
  [Dagster](docs/conventions/dagster.md) · [dbt](docs/conventions/dbt.md) · [K8s](docs/conventions/k8s.md)

## 구성 요소

| 계층 | 어디서 도나 | 무엇으로 |
| --- | --- | --- |
| 오케스트레이션 | 호스트 | Dagster webserver·daemon (메타 스토리지는 compose `postgres`) |
| 배치 컴퓨트 | K8s | Apache Spark Operator → `SparkApplication` |
| SQL 엔드포인트 | K8s | Spark Connect 서버 — dbt-spark가 `spark.remote`로 붙는다 |
| 스트림 컴퓨트 | K8s | Flink Operator → `FlinkDeployment` |
| 테이블 포맷 | — | Iceberg. JDBC 카탈로그는 CloudNativePG `catalog-postgres` |
| 오브젝트 스토어 | K8s | SeaweedFS — S3 호환, path-style |
| UI 진입점 | K8s | ingress-nginx `*.localtest.me:8080` |
| 변환 | 호스트 | dbt — `dbt-trino`에서 `dbt-spark`로 이행 중 |
| 분석 | 호스트 | Jupyter Lab `:8889` → Spark Connect |

몇 가지 전제가 이 표에 안 보인다.

- **Spark·Flink는 같은 Iceberg 카탈로그를 공유**한다. 카탈로그 이름은 전 엔진 `iceberg`로 통일한다 —
  다르면 같은 DB를 봐도 서로의 테이블이 안 보인다.
- **HTTP UI와 gRPC만 Ingress로 나간다.** JDBC·S3는 `port-forward`를 쓴다.
- **상주 컴퓨트는 평시 내려가 있다.** Spark Connect는 `--replicas=0`이 기본이다.
- 러너 이미지 태그와 버전의 사실은 문서가 아니라 `k8s/**/*.yaml`의 `image:` 값이다.

## 실행방법

**절차 정본은 [`docs/setup.md`](docs/setup.md)** 다 — 사전 요구 도구, 단계별 전제, 접속 경로,
그리고 조용히 틀리는 함정들이 거기 있다. 아래는 **이미 세팅된 환경을 다시 올리는 최단 경로**다.

```shell
cp .env.example .env                       # 최초 1회 — 값 채우는 법은 docs/setup.md §2

./scripts/k8s-up.sh                        # kind 클러스터 + 레지스트리 + ingress-nginx
./scripts/k8s-operators.sh                 # Spark · Flink · CloudNativePG
./scripts/k8s-poc-storage.sh               # SeaweedFS + Iceberg 카탈로그

podman compose up -d postgres              # Dagster 메타 스토리지
cd dagster/dockerfile.d/src
export DAGSTER_HOME="$PWD"                 # 미지정 시 임시 sqlite로 빠져 런이 UI에 안 남는다
uv run dg dev                              # http://localhost:3000
```

정리는 `./scripts/k8s-down.sh`(podman machine은 기본 보존).

> 🔴 **이 환경에는 `docker` 바이너리가 없다** — 컨테이너 런타임은 **podman**이고 compose는
> `podman compose`로 돈다. 문서의 `docker compose ...`는 전부 그렇게 읽는다.
>
> 🔴 **상주 컴퓨트는 쓰기 직전에 올리고 끝난 자리에서 내린다** — Spark Connect는 평시
> `--replicas=0`이라 15002 포트포워드 실패는 고장이 아니라 **회수된 상태**다([`docs/setup.md`](docs/setup.md) §3-2).

### 컴퓨트 기동·회수 (검증 후 반드시 내린다)

상주 컴퓨트는 **켜둔 채 잊으면 예산을 계속 갉아먹는다**(과거 13시간 유출 전례 —
[`docs/conventions/k8s.md`](docs/conventions/k8s.md) §9-3). 쓰기 직전에 올리고 **끝난 자리에서 내린다.**

```shell
kubectl scale deploy/spark-connect --replicas=1           # Spark Connect — 평시 0
kubectl scale deploy/spark-connect --replicas=0           # 회수

kubectl apply  -f k8s/flink/flinkdeployment-session.yaml  # Flink 세션 — JM이 상주 점유한다
kubectl delete -f k8s/flink/flinkdeployment-session.yaml  # 회수
```

Spark·Flink **동시 기동은 허용**되며, 지켜야 할 경계는 `spark.executor.instances` ≤ 1이다.

### 접속 경로 · 러너 이미지 · dbt 타깃

Web UI(Ingress)·`port-forward` 주소표, 러너 이미지 빌드, dbt 타깃별 전제는
**[`docs/setup.md`](docs/setup.md)** §3-1 · §4 · §8에 있다.

### 모델 추가

dbt 모델은 `dbt_pipelines/models/<dataset>/`에 `.sql`을 추가하면 자동 반영된다.
각 데이터셋 subproject가 **`@dbt_assets(select="fqn:<dataset>")`** 로 자기 모델만 소유한다
(`path:` 셀렉터는 cwd 글롭이라 정의 로드 시 모델이 수집되지 않는다 — [`docs/conventions/dbt.md`](docs/conventions/dbt.md)).

접속 타깃은 `DBT_TARGET`으로 고르며 **기본값은 `spark_connect`** 다. 타깃별 전제는
[`docs/setup.md`](docs/setup.md) §8.

```shell
kubectl scale deploy/spark-connect --replicas=1          # 먼저 올린다
kubectl port-forward svc/spark-connect 15002:15002       # 별도 터미널

dbt build                                                # spark_connect (기본)
DBT_TARGET=dev dbt build                                 # Trino로 값 대조
```

> **같은 SQL이 엔진에 따라 값이 갈린 사례가 있다**(`dbt.datediff` — Spark는 경과시간 `ceil`,
> Trino는 경계 교차). 그래서 Trino 타깃은 제거 대상이면서도 **값 대조의 정본**으로 남아 있다.
> 수치를 문서·리포트에 옮길 때는 **산출 엔진을 병기**한다([`docs/conventions/dbt.md`](docs/conventions/dbt.md)).

### 노트북 (옵션)

ad-hoc 탐색은 Dagster와 **같은 venv**의 Jupyter Lab(**포트 8889**)으로 한다 —
실행법은 [`docs/setup.md`](docs/setup.md) §7, 작성 규칙은
[`notebooks/README.md`](notebooks/README.md)와 [`docs/conventions/analysis.md`](docs/conventions/analysis.md).

## AI 에이전트 구조 (Claude Code)

이 저장소는 작업 자체도 규약화한다 — **전문 서브에이전트에 역할·권한을 나눠 배정**하고,
"누가 무엇을 왜 했는가"를 기록관 저널에 남긴다. 규칙 정본은
[`docs/conventions/agents.md`](docs/conventions/agents.md), 요약은 `CLAUDE.md` 운영 섹션에 있다.

> **아래 두 그림은 축약본이다.** 워커 목록·권한·게이트의 **정본은
> [`docs/conventions/agents.md`](docs/conventions/agents.md) §구조도**이고, 갈리면 그쪽이 사실이다.

### 구조 — 누가 누구를 배정하는가

```mermaid
flowchart TB
    U(["🚦 사용자 · 최종 게이트<br/>커밋 · 발행 · apply는 사람이 승인"])
    SUP["supervisor · 메인 루프<br/>미션 정의 · 계획 · 배정 · 취합 · 보고<br/>판정축: 계획 대비 실행 정합"]

    subgraph impl["구현 축 · 쓰기 O · model=inherit"]
        DE["data-engineer<br/>Dagster 에셋 · dbt 모델"]
        OE["devops-engineer<br/>compose · k8s · Terraform"]
        AN["analyst<br/>notebooks/** · docs/analyses/**"]
    end

    subgraph judge["판정 축 · 읽기 전용 · model=sonnet"]
        DV["data-verifier<br/>값 실측 대조"]
        DQ["data-qa<br/>테스트·게이트 감사"]
        OV["devops-verifier<br/>런타임 실측 대조"]
        OQ["devops-qa<br/>선언·게이트 감사"]
    end

    RES["researcher · 외부 1차 출처<br/>질의 유출 통제 · 인젝션 격리"]

    subgraph outside["게이트·기록 · 자기 판정 대상을 배정·수정하지 않는다"]
        SEC["security · 반출 · 규제 컨펌 게이트<br/>계획 1회 · 작업내용 1회 · 델타 조건부"]
        ARC["archivist · 저널 기록 전담"]
        SKM["skill-matcher · 스킬 배선 감사"]
        TW["tech-writer · 쓰기 O<br/>docs/** · README.md · 발행 금지<br/>except: security.md · skills.md"]
    end
    JR[("미션 저널<br/>$OBSIDIAN_VAULT/agents/날짜/NN-미션.md")]

    U <-->|"요청 ⇅ 보고 · 비가역 승인"| SUP
    SUP <-.->|"자문 질의 ⇅ 계획 · 게이트 설계"| DIR
    SUP <-->|"배정 ⇅ 산출물"| impl
    SUP <-->|"배정 ⇅ 발견"| judge
    SUP <-->|"질의 ⇅ 근거 · 출처등급 A~D"| RES
    SUP <-->|"컨펌 요청 ⇅ 승인 · 반려"| SEC
    SUP <-->|"감사 요청 ⇅ 별점 판정"| SKM
    SUP <-->|"배정 ⇅ 문서 · 원고"| TW
    SUP -->|"체크포인트 이벤트"| ARC
    ARC -->|기록| JR
```

- 축(**구현 / 실측 대조 / 체계 감사**)은 도메인이 달라도 **동일**하다 — 판단 규칙을 하나로 유지하려는 것.
  분석·공개는 **새 축이 아니라 새 도메인**이라 구현 축 1명(`analyst`·`tech-writer`)만 두고 판정은 재사용한다.
- **판정자는 쓰지 않는다** — `disallowedTools: Write, Edit, NotebookEdit`으로 미부여(난이도)가 아니라 거부(강제).
  판정 축은 다섯이고 서로 중첩되지 않는다: 값=`*-verifier` / 체계=`*-qa` / 노출=`security` /
  배선=`skill-matcher` / 기록=`archivist`. **「계획 대비 실행 정합」은 supervisor가 직접 진다.**
- 🔴 **판정자는 자기 판정 대상을 배정·수정하지 않는다.** 2계층이라 배정 주체가 supervisor 하나뿐이어서
  이 원칙의 **강제는 도구 축**에 있다 — 판정자 6종의 쓰기 거부, `tech-writer`의 `except`(아래).
  **「계층 밖」은 `archivist`·`skill-matcher` 2종**이다(도메인 작업을 하지 않고 계층 자체를 감사·기록한다).
- **`tech-writer`는 저장소의 문서 소유자**다 — `docs/**`와 최상위 `README.md`를 쓴다. 단 가드는 디렉터리
  단위라 `docs/analyses/**`(내용은 `analyst` 소관)와 `docs/conventions/**`(규칙 신설은 supervisor 소관)는
  **규율로만** 갈린다. **2026-08-22부터 `docs/conventions/**` 에는 `ask` 프롬프트도 없다**(정본 설계 게이트
  축소 — 판단 축을 **경로 → 가역성**으로 옮겼고, 문서 편집은 git이 되돌리므로 최종 관문을 **커밋 1회**로 모았다).
  ✅ 반대로 **`docs/security.md`·`docs/skills.md`는 규율에서 기계 강제로 올라갔다** — 이 워커를 **판정하는
  근거 문서**라, `worker_path_guard.py`의 **`except` 축**(`allow`/`deny`보다 먼저 평가·대소문자 무시)으로
  `deny`한다. 판정 대상이 판정 기준을 고치면 통제가 성립하지 않기 때문이고, **문안 정합조차 예외가 아니다**.
- **워커가 워커를 못 부르니 supervisor가 릴레이한다** — `skill-matcher`는 새 스킬 후보를 **직접 검색하지 않고**
  `researcher`에 보낼 **조사 요청서**를 반환한다(`skill-matcher`→supervisor→`researcher`→supervisor→채점·제안
  →`security`→🚦사람). 찾기는 `researcher`, 배선 판정은 `skill-matcher`, 출처 신뢰성은 `security`로 **셋이 갈린다** —
  **감사자가 배선까지 하면 자기가 배선한 것을 자기가 감사**하게 되어 이 워커의 존재 이유가 사라진다.
- **2026-08-23에 3계층 → 2계층으로 바뀌었다.** 중간 계층 `director`를 폐기했다 — 3축 실측이 모두 같은
  방향이었다: ⓐ 정의 파일 17,430바이트인데 판정 축 문구가 정본 5~6곳 **중복** ⓑ 저널 65건 중 **실배정 1건**
  (91~97% 미경유) ⓒ `Agent` 호출 발행 시 `Agent is disabled for this session, in subagents as well as here`로
  **배정 불가 재현**(대조군: 같은 워커의 `Bash` 성공 · supervisor의 `Agent` 8회 성공).
  폐기 이유는 "안 돌아서"가 아니라 **유령 행위자**다 — 절차가 존재하지 않는 행위자를 지시하면
  *지켜지지 않는 것이 아니라 지킬 수 없고*, 실제로 그 때문에 **`security` 컨펌이 하루 0회**였던 기록이 있다.
  잃은 것은 **Δ 이탈 보고의 독립 주체** 하나이고, `security`가 G2에서 **변경 파일 집합을 직접 재구성**해
  대조하는 것으로 대체했다. 상세 [`agents.md` §director 폐기](docs/conventions/agents.md).

### 파이프라인 — 미션 한 건이 흐르는 경로

```mermaid
flowchart LR
    A(["사용자 요청"]) --> B{"미션인가?<br/>파일변경 · 위임 · 결정 · 비가역"}
    B -->|아니오| Z(["단순 응답 · 기록 없음"])
    B -->|예| C["저널 개시<br/>NN 번호는 journal_guard가 발급"]
    C --> P["분해 · 계획<br/>권한 매니페스트"]
    P --> G1{"security 컨펌 ①<br/>계획 · 미션당 1회"}
    G1 -->|반려| P
    G1 -->|승인| D["배정<br/>도메인 × 축"]
    D --> E["구현 워커<br/>쓰기 · 경로 한정"]
    E -.->|"계획 밖 경로 · 비가역 · 외부발신"| GD{"Δ 델타 컨펌<br/>해당 항목만"}
    GD -.->|승인| E
    D --> F["판정 워커<br/>실측 대조 · 체계 감사"]
    D --> R["researcher<br/>외부 1차 출처"]
    R --> E
    E --> F
    F -->|"불일치 · 갭"| E
    F --> G{"security 컨펌 ②<br/>작업내용 · 미션당 1회"}
    G -->|반려| E
    G -->|승인| H{"비가역인가?<br/>커밋 · apply · 발행 · DROP"}
    H -->|예| I(["🚦 사람 승인 게이트"])
    H -->|아니오| J["적용"]
    I --> J
    J --> K["archivist 기록<br/>결과 · 상호작용 로그 · 실행 메타"]
    K --> L(["사용자 보고"])
```

- **`security` 컨펌은 배정마다가 아니라 2점 + 델타다**(2026-08-20 개정) — ①**계획 전체** 1회
  ②**미션 전체 작업내용** 1회 Δ계획 밖(쓰기 경로·비가역·외부 발신) 발생 시 그 항목만. 배정 시점엔
  산출물이 없어 **읽기 전용 `security`가 볼 재료가 없기** 때문이고, 비용 절감이 목적이 아니다(호출 `2N+`→`2+Δ`).
  둘 다 **"한 벌"이 단위**다 — 워커별로 쪼개면 **파일 사이의 조합에서 생기는 노출**을 못 본다.
  **비가역은 Δ/①에서 실행 *전에* 판정**하며 ②로 미루지 않는다. 개정 효력은 3셀 대조 전까지 **`미확인`**이다.

**기계 강제층(hook)** — 위 흐름의 규율 중 일부만 실제로 강제된다. 결정값은 `allow`·`deny`·`ask`·`defer` 넷뿐이다.

| 가드 | 배선 | 막는 것 |
| --- | --- | --- |
| [`journal_guard.py`](scripts/journal_guard.py) | `SessionStart` · `PreToolUse(Write)` · `Stop` | 저널 `NN` 넘버링 경합 · 규약 위반 생성 · 기록 누락 경고 |
| [`session_sync_guard.py`](scripts/session_sync_guard.py) | `PreToolUse(Bash·Agent·Edit\|Write\|NotebookEdit)` | 병렬 세션의 중복 작업 · 워킹트리 전역 git 명령 |
| [`protected_paths_guard.py`](scripts/protected_paths_guard.py) | `PreToolUse(Bash)` | 보호 경로(`.env`·lock 등) 우회 수정 |
| [`worker_path_guard.py`](scripts/worker_path_guard.py) | 각 워커 프론트매터 `hooks` | 워커별 쓰기 경로 이탈(`allow`/`deny`/`except`) + 가드 스크립트 자기보호 |
| [`analyst_path_guard.py`](scripts/analyst_path_guard.py) | `analyst` 프론트매터 `hooks` | 같은 목적 |

- **`hooks`를 세션 도중 고치면 그 세션에는 반영되지 않는다** —
  배선을 바꾸면 **새 세션에서** 3셀 대조를 다시 돌린다.
- 🔴 **경계표에 적혀 있다고 막히는 것이 아니다** — 경계 정의와 배선은 다른 층이라,
  정의만 있고 호출자가 없으면 한 번도 실행되지 않는다.
  강제되는 것은 **배선된 (가드 × 워커) 쌍**이다.
- 🔴 **`Bash` 경유 쓰기는 파일 가드를 우회**한다 — 그래서 "파일 수정을 `Bash`로 하라"는 지시는 거부한다.
- **발행(업로드)은 어느 워커도 하지 않는다.** 외부 발신은 비가역이라 마지막 게이트는 **사람**이 갖는다
  (자동화하지 않는 것이 설계). 공개 기준은 [`docs/conventions/publishing.md`](docs/conventions/publishing.md).

## REF

규칙·설계가 근거로 삼는 외부 표준의 인덱스는 [`docs/references.md`](docs/references.md)에 있다.
아래는 이 저장소가 실제로 쓰는 스택의 1차 문서다.

### Dagster

- 배포 옵션(Kubernetes): https://docs.dagster.io/deployment/oss/deployment-options/kubernetes
- `dagster.yaml`(인스턴스 설정): https://docs.dagster.io/deployment/oss/dagster-yaml
- `workspace.yaml`(코드 로케이션): https://docs.dagster.io/guides/build/projects/workspaces/workspace-yaml
- Dagster Pipes / `PipesK8sClient`: https://docs.dagster.io/api/python-api/libraries/dagster-k8s

### dbt

- dbt-trino(현행): https://github.com/starburstdata/dbt-trino
- dbt-spark(이행 대상): https://docs.getdbt.com/docs/core/connect-data-platform/spark-setup
- 크로스 데이터베이스 매크로: https://docs.getdbt.com/reference/dbt-jinja-functions/cross-database-macros

### 레이크하우스 / 컴퓨트

- Apache Iceberg: https://iceberg.apache.org/docs/latest/
- Apache Spark Kubernetes Operator: https://apache.github.io/spark-kubernetes-operator/
- Apache Flink Kubernetes Operator: https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-main/
- SeaweedFS(S3 API): https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API

### 로컬 K8s

- kind(Podman provider): https://kind.sigs.k8s.io/
- kind 로컬 레지스트리: https://kind.sigs.k8s.io/docs/user/local-registry/
- ingress-nginx: https://kubernetes.github.io/ingress-nginx/
