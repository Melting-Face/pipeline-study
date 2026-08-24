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
  > **자격증명은 `ICEBERG_S3_*`로 분리한다**(2026-08-19 확정). 호스트에서 K8s 카탈로그에 붙을 때
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
| 로컬 파일 즉석 분석 | **DuckDB** | 클러스터를 띄우기 아까운 소형 csv·parquet에 한정 |

## 참고

- 코딩 철학(Rule of Three·추적 용이성): [`../philosophy.md`](../philosophy.md)
- 메달리온 레이어·tag 표기: [`dbt.md`](dbt.md)
- 테스트 계층·우선순위: [`../test.md`](../test.md)
- 보안·거버넌스(DUA·재식별 금지): [`../security.md`](../security.md)
- 데이터셋 원천·실버 피처: [`../dataset_schema.md`](../dataset_schema.md)
- 노트북 실행 방법: [`../../notebooks/README.md`](../../notebooks/README.md)
- dbt 메달리온 아키텍처(외부): https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview
- 관측연구 보고 지침 STROBE(코호트 보고 항목의 국제 표준): https://www.strobe-statement.org/
