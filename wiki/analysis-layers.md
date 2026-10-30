# 분석을 세 층에 나눠 두기 — 결론의 수치는 모델을 지나온다

> 스터디 장(방법론). 적재·변환한 데이터로 질문에 답할 때 무엇을 어디에 두는지 정리한 것.
> 결론부터 쓰면 — **노트북에서 본 숫자는 결론이 아니다. 결론에 쓰는 숫자는 gold 모델을 지나와야 다시 낼 수 있다.**

## 개념

- **메달리온**은 데이터를 정제 단계별로 bronze(원천 정제) → silver(조인·로직) → gold(지표·코호트)로 나누는 방식이다.
  외부 설명: [dbt — How we structure our dbt projects](https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview)
- 이 저장소는 그 위에 **분석 3층**을 둔다. 층마다 성격과 검증이 다르다.

| 층 | 위치 | 성격 | 검증 |
| --- | --- | --- | --- |
| gold 모델 | `dbt_pipelines/models/<dataset>/` (`tags=['gold']`) | 합의된 지표·코호트를 재현 가능한 테이블로 고정 | dbt 스키마 테스트 필수 |
| 노트북 | `notebooks/` | 탐색·가설 확인·일회성 조회 | 없음, 결론의 근거로 쓰지 않는다 |
| 리포트 | `docs/analyses/<NN>-<slug>.md` | 질문 → 방법 → 수치 → 해석·한계 | 인용 수치가 모델 경유인지 |

규칙 정본은 [docs/conventions/analysis.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/analysis.md) §1이다.

## 이 저장소에서의 쓰임

- 레이어는 디렉터리나 스키마 접두어가 아니라 **tag**로 표기한다. 모델 파일 상단의 config가 레이어를 정한다.
  규칙은 [docs/conventions/dbt.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/dbt.md)
  「디렉토리 / 레이어링 (Medallion)」에 있다.

```sql
{{ config(tags=['gold']) }}
```

- gold 태그가 붙은 모델의 예:
    - [stock_forecast__direction_training_h1.sql](https://github.com/Melting-Face/pipeline-study/blob/main/dagster/dockerfile.d/src/dbt_pipelines/models/stock_forecast/tables/stock_forecast__direction_training_h1.sql)
    - [us_equity_krw__close_daily.sql](https://github.com/Melting-Face/pipeline-study/blob/main/dagster/dockerfile.d/src/dbt_pipelines/models/us_equity_krw/tables/us_equity_krw__close_daily.sql)
- 노트북은 [notebooks/](https://github.com/Melting-Face/pipeline-study/tree/main/notebooks)에 있고
  쓰는 법은 [notebooks/README.md](https://github.com/Melting-Face/pipeline-study/blob/main/notebooks/README.md)에 있다.
- 리포트 자리는 [docs/analyses/README.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/analyses/README.md)가 지킨다.
- 흐름은 이렇다.
    1. 노트북에서 분포와 가설을 본다.
    2. 같은 조회를 3회 이상 하거나 리포트가 인용하면 gold 모델로 올린다(Rule of Three).
    3. 노트북에서 검증한 로직을 모델로 옮긴 뒤 노트북을 지운다. 정의의 단일 출처는 `defs/`·`models/`다.
    4. 리포트는 gold 모델의 수치만 인용하고 산출 엔진을 함께 적는다.

## 실습

이 장의 실습은 클러스터 없이 돈다. dbt가 모델을 읽어 들이는지(파싱)까지만 보고, 엔진에는 접속하지 않는다.

**띄우기 — 의존성과 dbt 패키지**

출처: [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §1 「저장소 준비」

```shell
(cd dagster/dockerfile.d/src && uv sync --group dev)

(cd dagster/dockerfile.d/src \
  && uv run dbt deps  --project-dir dbt_pipelines \
  && uv run dbt parse --project-dir dbt_pipelines --profiles-dir dbt_pipelines)
```

**확인 1 — 패키지 설치**

```text
# 관측: uv run dbt deps --project-dir dbt_pipelines
<시각>  Running with dbt=1.10.23
...
<시각>  Installing dbt-labs/dbt_utils
<시각>  Installed from version 1.3.3
...
<시각>  Installing metaplane/dbt_expectations
<시각>  Installed from version 0.10.10
...
<시각>  Installing godatadriven/dbt_date
<시각>  Installed from version 0.19.0
```

- 성공 기준: `packages.yml`의 패키지와 그 의존성마다 `Installed from version`이 보이고 에러 없이 끝난다.
  `Updated version available` 안내는 실패가 아니다.

**확인 2 — 모델 파싱**

```text
# 관측: uv run dbt parse --project-dir dbt_pipelines --profiles-dir dbt_pipelines
<시각>  Running with dbt=1.10.23
...
<시각>  Registered adapter: trino=1.10.6
<시각>  Unable to do partial parsing because saved manifest not found. Starting full parse.
<시각>  [WARNING]: Configuration paths exist in your dbt_project.yml file which do not apply to any resources.
There are 1 unused configuration paths:
- models.dbt_pipelines.eicu
<시각>  Performance info: dbt_pipelines/target/perf_info.json
```

- 성공 기준: `Registered adapter: trino`가 보이고 `Performance info`까지 에러 없이 끝난다.
- 파싱 통과는 "모델 파일을 읽었다"까지다. 엔진에서 같은 값이 나오는지는 보지 않는다.
- 위 `[WARNING]`은 관측된 그대로 실었다. 원인은 이 장에서 확인하지 않았다.

**내리기** — 상주 프로세스가 없어 내릴 것이 없다. 생성물(`.venv`·`dbt_packages`·`target`)은 git이 무시한다.

## 고장 대응

| 증상 | 먼저 볼 것 | 처방 |
| --- | --- | --- |
| 노트북 셀 출력이 커밋에 섞일 것 같다 | `nbstripout` 훅 설치 여부 | `pre-commit install --install-hooks`(setup.md §1). 훅을 `--no-verify`로 우회하지 않는다 |
| 같은 SQL이 엔진마다 다른 값을 낸다 | 매크로가 dbt 내장(`dbt.datediff` 등)인가 | 프로젝트 dispatch 매크로로 흡수한다 — [cross-engine-sql.md](cross-engine-sql.md) |

출처: [docs/conventions/analysis.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/analysis.md)
§3 「셀 출력은 커밋되지 않는다」, §4. 설치 명령은 docs/setup.md §1.

## 함정

### 「도는 것」과 「같은 값」은 다르다

출처: docs/conventions/analysis.md §4.
`dbt.datediff`는 Spark가 경과시간 `ceil`, Trino는 경계 교차라 임계값 비교에서 값이 갈렸다.
그래서 수치에는 산출 엔진을 병기한다.

### 조용한 드롭은 흔적을 남기지 않는다

출처: docs/conventions/analysis.md §4.
결측·이상치를 드롭했는지 대치했는지 적지 않으면 결과가 바뀌어도 아무도 모른다.
코호트는 제외 조건별 행 수 감소(attrition)를 표로 남긴다.

### 자리표시자를 지우면 링크가 클론에서만 죽는다

출처: docs/conventions/analysis.md §5.
git은 빈 디렉터리를 추적하지 않는다. `docs/analyses/README.md`를 지우면 그 경로로 가는 링크가
작업 트리에서는 살아 있고 클론에서만 깨진다.
