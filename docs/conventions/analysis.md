# 분석 컨벤션 (analysis)

이 저장소는 **파이프라인(수단) + 분석(목적)** 두 축으로 굴러간다. 이 문서는 **분석 축의 규칙 정본**이다.
파이프라인 축(적재·변환 정의)의 정본은 [`dagster.md`](dagster.md)·[`dbt.md`](dbt.md)이며,
여기서는 그 결과물을 **해석해 결론을 내는 작업**의 규칙을 정한다.

> **DUA·재배포 제한이 걸린 데이터셋**을 다룰 때는 분석 산출물의 반출·공유가 통제 대상이다.
> 통제 정본은 [`../security.md`](../security.md)이고, 이 문서는 그 통제를 분석 작업 절차에 반영한다.
> 현재 적재된 MIMIC-IV·eICU가 여기에 해당한다.

## 1. 분석은 3층으로 나눈다

같은 질문이라도 **어디에 두느냐**로 재사용성·검증 가능성이 갈린다. 판단 기준을 먼저 못박는다.

| 층 | 위치 | 성격 | 검증 |
| --- | --- | --- | --- |
| **gold 모델** | `dbt_pipelines/models/<dataset>/` (`tags=['gold']`) | 합의된 지표·코호트를 **재현 가능한 테이블**로 고정 | dbt 스키마 테스트 **필수** |
| **노트북** | `notebooks/` | 탐색(EDA)·가설 확인·일회성 조회 | 없음 (결론의 근거로 쓰지 않는다) |
| **리포트** | `docs/analyses/<NN>-<slug>.md` | 질문 → 방법 → 수치 → 해석·한계 | 인용 수치가 gold/dbt 모델 경유인지 |

**어디에 둘지 판단**:

- 같은 조회를 **3회 이상** 하거나, 리포트가 그 수치를 인용하면 → **gold 모델로 올린다**
  (코딩 철학 #5 Rule of Three, [`../philosophy.md`](../philosophy.md)).
- 한 번 보고 버릴 조회, 분포·이상치 눈으로 확인 → **노트북**.
- **정의(에셋·모델)는 노트북에 두지 않는다.** 단일 출처는 `dagster_project/defs/`·`models/`다.
  노트북에서 검증한 로직은 **모델·에셋으로 옮긴 뒤** 노트북은 지운다.

## 2. gold 레이어 규칙

[`dbt.md`](dbt.md)가 정의한 메달리온 3층 중 gold(marts)의 **실행 기준**이다.
레이어는 디렉터리가 아니라 **tag**로 표기한다(`tags=['gold']`).

- **materialization은 `table`** — 분석은 반복 조회라 view는 매번 재계산 비용을 문다.
- **grain을 모델 설명 첫 줄에 쓴다.** "1행 = 무엇인가"(예: `1행 = stay_id`)를
  `schema.yml`의 `description`에 남기고, 같은 컬럼 조합에 유니크 테스트를 단다
  ([`../test.md`](../test.md) §1 — grain 테스트는 ★★★★★ 우선순위).
- **지표·코호트 정의는 SQL 주석이 아니라 `schema.yml` `description`에** 둔다.
  리포트가 인용할 때 링크할 곳이 필요하고, `dbt docs`로 노출되기 때문이다.
- **네이밍은 `<dataset>__<subject>`** (예: `mimic_iv__sepsis3_cohort`).
  레이어명(`gold_`)을 접두어로 붙이지 않는다 — 레이어는 tag가 표기한다.
- **silver를 건너뛰고 source에서 바로 gold를 만들지 않는다.** 원천 정제·개념화는 silver의 일이고,
  gold는 **집계·코호트 확정**만 한다(관심사 분리).

```sql
-- models/mimic_iv/tables/mimic_iv__sepsis3_cohort.sql
-- 1행 = stay_id (ICU 재실 1건). 코호트 정의는 schema.yml description 참조.
{{ config(materialized='table', tags=['gold']) }}

select
    s.stay_id,
    s.subject_id,
    s.sepsis3,
    t.icu_los_hours
from {{ ref('sepsis3') }} as s
inner join {{ ref('icustay_times') }} as t using (stay_id)
```

## 3. 노트북 규칙

실행 방법·포트·venv 공유 이유는 [`../../notebooks/README.md`](../../notebooks/README.md)에 있다.
여기서는 **작성 규칙**만 정한다.

- **파일명은 `NN-<slug>.ipynb`** — `NN`은 두 자리 순번(`00-lakehouse-connect.ipynb` 선례).
  순번은 읽는 순서를 뜻하지 실행 의존을 뜻하지 않는다.
- **위→아래 1회 실행으로 재현**되어야 한다. 셀을 건너뛰거나 되돌아가야 재현되는 노트북은 고친다.
  `scripts/`의 절차형 규칙과 같은 논리다 — **실행 순서 = 읽는 순서**([`python.md`](python.md)).
- **첫 셀은 마크다운으로 목적·입력 테이블·전제**를 적는다. 3개월 뒤의 자신이 첫 독자다.
- **비밀정보를 노트북에 두지 않는다.** 기본 경로인 Spark Connect는 카탈로그·S3 자격증명이
  **서버 측**에 있어 클라이언트가 `sc://localhost:15002`만 알면 된다 — **보안상 이 경로를 기본으로 쓴다.**
  pyiceberg 직접 접속이 필요하면 자격증명은 반드시 `os.environ` 참조로 읽고 **값을 셀에 쓰지 않는다**
  (코딩 철학 #4).
  > **자격증명은 `ICEBERG_S3_*`로 분리한다**(확정). 호스트에서 K8s 카탈로그에 붙을 때
  > 쓰는 키는 `ICEBERG_S3_ACCESS_KEY`·`ICEBERG_S3_SECRET_KEY`(= 클러스터 Secret `lakehouse-creds`의 값)이며,
  > 미설정 시 공용 `AWS_*`로 폴백한다(compose 단독 구성 호환). 엔드포인트(`ICEBERG_S3_ENDPOINT`)와
  > **자격증명은 한 쌍**이다 — 엔드포인트만 바꾸고 키를 공용으로 두면 아래 증상이 난다.
  >
  > | 단계 | 결과 | 이유 |
  > |---|---|---|
  > | `list_namespaces()` · `list_tables()` | ✅ 성공 | 카탈로그 **Postgres**만 조회 |
  > | `load_table()` | ❌ `ACCESS_DENIED during HeadObject` | `metadata.json`을 **S3에서** 읽는 순간 |
  >
  > 🔴 **부분 성공이라 오진하기 쉽다.** 키를 분리한 지금도 `ICEBERG_S3_*`를 비워두면 증상이 그대로
  > 재현된다 — 원인이 "설계 공백"에서 **"설정 누락"** 으로 바뀐 것뿐이다. 전파 체인은
  > [`../operations.md`](../operations.md) §1-2.
- **무거운 계산은 Spark에서 끝내고 pandas로는 집계 결과만 받는다.** `toPandas()`를 원천 테이블에
  걸지 않는다(대용량 테이블은 호스트 메모리를 넘긴다 — `chartevents`·`labevents`).
- **난수를 쓰면 seed를 고정**하고(`random_state=`), 표본 추출은 추출 조건을 셀에 남긴다.

### 학습 노트북 규칙 (ML)

탐색과 달리 **학습은 산출물이 남고 지표가 결론처럼 읽힌다.** 그래서 규칙이 넷 더 붙는다.
의존성은 `notebook`과 분리된 `[dependency-groups] ml`이고, 실행은
`uv run --group notebook --group ml`이다([`../../notebooks/README.md`](../../notebooks/README.md)).

- 🔴 **분할 단위는 행이 아니라 개체다.** 임상 데이터는 1환자가 여러 stay를 갖는다 —
  stay 단위로 나누면 같은 환자가 train/test에 걸쳐 모델이 **그 환자를 외우고**, 지표만 좋아진다.
  `StratifiedGroupKFold(groups=subject_id)`를 쓰고, **분할 후 겹치는 개체가 0인지 검산**한다.
- 🔴 **지표는 기준선과 함께 읽는다. 더미 모델을 항상 같이 돌린다** — 기준선이 없으면
  "좋다"를 판정할 눈금이 없다(`DummyClassifier` / `DummyRegressor`).
  - **분류**: 불균형에서 accuracy를 쓰지 않는다. 주지표는 **PR-AUC**이고 **유병률을 병기**한다
    (PR-AUC의 기준선이 유병률이라, 값만 떼면 같은 숫자가 다른 것을 뜻한다).
  - **회귀**: 타깃의 꼬리가 길면(로그정규 등) **로그 변환 후 학습·평가**하고, 어느 스케일의
    수치인지 지표 이름에 적는다. R²는 보조로만 쓴다 — 표본이 작으면 쉽게 음수가 되고,
    그 음수는 "평균보다 못하다"는 뜻이라 해석이 따로 필요하다.
- 🔴 **음성 대조를 둔다** — 타깃을 섞어 같은 파이프라인을 재학습하면 지표가 **더미 수준으로
  떨어져야** 한다. 안 떨어지면 누수이거나 버그다. 이것이 원칙 7("새로 건 게이트는 일부러
  위반시켜 본다")의 ML판이고, 좋은 지표라는 **성공 신호를 의심할 유일한 수단**이다.
  분류에서는 더미의 PR-AUC가 유병률과 어긋나는지도 함께 본다(어긋나면 지표 계산이 틀린 것이다).
- **표본 수를 행 수로 읽지 않는다.** 같은 개체의 행이 서로 거의 같으면 그 행들은 중복이고,
  행 수를 근거로 쓰면 신뢰구간이 실제보다 좁아 보인다(과신). 의심되면 **개체당 1행으로 줄여
  같은 평가를 반복**한다 — 성능이 그대로면 늘어난 행이 정보를 더하지 않았다는 실측 근거가 된다.
- **예측 시점(index time)을 고정하고 그 이전 데이터만 피처로 쓴다.** 시점 이후 정보가 한 칸이라도
  섞이면 누수다. 시점 이전에 결과가 발생한 행은 **attrition에서 제외**한다(사유를 적는다).
- **결측은 대체하되 표시자를 남긴다**(`SimpleImputer(add_indicator=True)`) — 채우기만 하면
  "측정하지 않았다"는 정보가 사라진다. §4의 *조용한 드롭*과 같은 계열의 실수다.

> ⚠️ **순위와 확률은 다른 축이다.** `class_weight="balanced"`는 예측확률을 통째로 위로 밀어
> PR-AUC·ROC-AUC(순위 지표)는 거의 그대로인데 보정은 무너진다. 확률을 해석하거나
> 임계값 규칙을 쓸 때는 **가중치 없는 모델을 따로 적합**시킨다. 임계값은 고정값 0.5도,
> 유병률 자체도 아닌 **예측확률의 분위수**로 잡는다(운용: 상위 N%만 경보).

**학습 산출물(모델·메트릭)은 저장소 밖** `$DATA_EXTRACT_DIR`(기본 `~/extracts`) 하위에 둔다.
모델은 **훈련 데이터의 함수**이기 때문이다 — 적합 산출물에 훈련 분포 통계가 박히고
(대체 중앙값·스케일·계수), 트리 계열은 **관측치 자체가 분할 임계값**이 된다.
⚠️ `scripts/worker_path_guard.py`는 **워커의 쓰기만** 보고 **Jupyter 커널은 그 가드 밖**이므로,
저장 셀이 직접 경로를 확인하고 에러를 내고 멈춘다(기계 강제가 아니라 노트북 내부 방어다).

### 셀 출력은 커밋되지 않는다

`.ipynb` 셀 출력에는 조회 결과가 **그대로 박제**되고, `gitleaks`는 크리덴셜 패턴을 잡지
원천 데이터를 잡지 못한다. 두 겹으로 막는다.

| 방어 | 위치 |
| --- | --- |
| `nbstripout` pre-commit 훅 (출력·실행횟수 제거) | `.pre-commit-config.yaml` |
| `**/.ipynb_checkpoints/` 무시 (Jupyter 스냅샷은 출력을 담는다) | `.gitignore` |

🔴 **훅을 `--no-verify`로 우회해 커밋하지 않는다.**

## 4. 수치·결론 규칙

분석의 산출물은 코드가 아니라 **주장**이다. 주장에는 근거가 붙어야 한다.

- **결론에 인용하는 수치는 gold 또는 dbt 모델을 경유한다.** 노트북의 임시 SQL로 낸 숫자를
  리포트에 그대로 옮기지 않는다 — 재현 경로가 없으면 검증도 반박도 불가능하다.
- **코호트는 attrition을 기록한다.** 전체 → 제외 조건별 감소 행 수 → 최종 N을 표로 남긴다.
  제외 사유가 없는 제외는 하지 않는다.
- **결측·이상치 처리를 명시한다.** 드롭했는지, 대치했는지, 대치했다면 방법과 근거를 쓴다.
  "조용한 드롭"은 결과를 바꾸면서 흔적을 남기지 않는 가장 흔한 오류다.
- **수치에는 산출 엔진을 병기한다.** 같은 SQL이 엔진에 따라 다른 값을 낸 사례가 실제로 있다 —
  `dbt.datediff`는 Spark가 경과시간 `ceil`, Trino는 경계 교차라 임계값 비교에서 값이 갈렸다
  ([`dbt.md`](dbt.md) 방언 흡수 규칙). **"도는 것"과 "같은 값"은 다르다.**
- **재식별을 시도하지 않는다.** 개별 레코드를 산출물에 노출하지 않고, 집계 셀이 지나치게 작으면
  (관례적으로 5 미만) 마스킹하거나 구간을 넓힌다([`../security.md`](../security.md) 재식별 금지).

## 5. 리포트 규칙

- **위치는 `docs/analyses/<NN>-<slug>.md`** — 디렉터리는 [`README.md`](../analyses/README.md)로
  이미 실재한다. ⚠️ **자리표시자를 지우지 마라** — git은 빈 디렉터리를 추적하지 않아
  그 파일이 없으면 이 경로를 가리키는 링크가 **클론에서만 죽는다**(작업 트리는 초록이다).
- **구성은 질문 → 데이터·코호트 → 방법 → 결과 → 해석 → 한계** 순으로 쓴다.
  **한계 섹션을 비우지 않는다** — 없으면 없다고 쓰지 말고, 무엇을 확인하지 못했는지 쓴다.
- 인용한 gold 모델·노트북을 **경로로 링크**한다(재현 경로 제공).
- 그림이 필요하면 차트를 만들고, 차트 설계는 런타임 스킬 `dataviz`를 따른다([`../skills.md`](../skills.md)).

## 6. 도구

| 용도 | 도구 | 비고 |
| --- | --- | --- |
| 대화형 탐색 | **Jupyter Lab**(호스트, `--group notebook`, 포트 **8889**) | Dagster와 venv 공유 — `dagster_project.common.*` import 가능 |
| SQL 엔진 | **Spark SQL**(Spark Connect `sc://localhost:15002`) | Trino는 재설계에서 제거 대상 — `--profile legacy-sql`로만 뜬다 |
| 지표·마트 정의 | **dbt**(gold 모델) | 스킬 `using-dbt-for-analytics-engineering` |
| 로컬 파일 즉석 분석 | **없음** — 위 SQL 엔진으로 간다 | 후보 **DuckDB**는 🔎 미채택 — 기각 사유·재검토 트리거는 [`../architectures/duckdb.md`](../architectures/duckdb.md) |
| 모델 학습 | **scikit-learn**(호스트, `--group ml`) | **Spark MLlib이 아니다** — 아래 참조 |
| 실험 추적 | **없음** — `metrics.json` 파일 + 리포트 | 후보 **MLflow**는 미채택. 재검토 트리거는 아래 |

**학습을 Spark에 올리지 않는 이유는 취향이 아니라 예산이다.** `spark.executor.instances ≤ 1`
경계를 Spark Connect가 상시 소비하고 Dagster 상주는 회수 다이얼이 듣지 않는다 —
executor를 늘리면 느려지는 게 아니라 **스케줄 자체가 실패**한다
([`../resource-sizing.md`](../resource-sizing.md) §C). 그래서 집계·피처는 Spark에서 끝내고
학습만 호스트로 내린다. 표 형식 데이터 규모에서는 이 분업이 성능상으로도 불리하지 않다.

**MLflow를 두지 않는 이유**는 상주 컴퓨트·스토리지 예산과 노출 축이 함께 늘기 때문이다.
재검토 트리거: 비교할 실험이 **손으로 세기 어려워지는 시점**(시점이 아니라 조건).

## 참고

- 코딩 철학(Rule of Three·추적 용이성): [`../philosophy.md`](../philosophy.md)
- 메달리온 레이어·tag 표기: [`dbt.md`](dbt.md)
- 테스트 계층·우선순위: [`../test.md`](../test.md)
- 보안·거버넌스(DUA·재식별 금지): [`../security.md`](../security.md)
- 데이터셋 원천·실버 피처: [`../dataset_schema.md`](../dataset_schema.md)
- 노트북 실행 방법: [`../../notebooks/README.md`](../../notebooks/README.md)
- dbt 메달리온 아키텍처(외부): https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview
- 관측연구 보고 지침 STROBE(코호트 보고 항목의 국제 표준): https://www.strobe-statement.org/
