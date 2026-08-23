# 테스트 규칙 (testing)

이 문서는 pipeline-study 파이프라인의 **테스트 전략·규칙의 단일 출처**다.
계층별로 *무엇을 · 어디서 · 어떻게* 테스트하는지 정하고, 각 계층의 세부 규약은
[`conventions/dbt.md`](conventions/dbt.md)·[`conventions/python.md`](conventions/python.md)·[`conventions/dagster.md`](conventions/dagster.md)와 교차링크한다.

버전 컨텍스트: `dagster==1.12.12` · `pytest`(dev 의존성) · **`dbt-spark>=1.11,<1.12`(현행 기본)** ·
`dbt-trino>=1.8,<2.0`(값 대조용 존치) — dbt-core 1.8+ = 단위 테스트 지원 계열.
🔴 dbt 기본 타깃은 **`spark_connect`** 다. 아래 명령의 `--target dev`는 **trino 경로**를 뜻한다
([conventions/dbt.md](conventions/dbt.md) §dbt-spark 타깃).

## 현황 (2026-08 기준)

> **인프라는 있으나 테스트는 거의 미작성**이다. 아래 규칙은 채워 나갈 목표(TODO)를 겸한다.

| 계층 | 인프라 | 실제 테스트 | 비고 |
| --- | --- | --- | --- |
| dbt 스키마 테스트 | ✅ 규약([dbt.md](conventions/dbt.md#테스트-필수))·패키지(`dbt_utils`·`dbt_expectations`) | ❌ `schema.yml`에 `description`만, `data_tests` 없음 | **최우선 보강 대상** |
| dbt 단위 테스트 | ✅ dbt 1.8+ | ❌ 없음 | `unit_tests:` 미사용 |
| dbt singular 테스트 | ✅ `dbt_pipelines/tests/`(`.gitkeep`) | ❌ 없음 | — |
| Dagster 에셋 pytest | ✅ `src/tests/`(`__init__.py`)·`pytest` | ❌ 없음 | 뼈대만 |
| 통합·스모크 | ✅ `dg check`·`dbt build` | ⚠️ 수동 | CI 게이트 미구성. 🔴 **`dbt build`는 22모델에 대해 한 번도 돌지 않았다**(§5-1 B9) |
| Iceberg 유지보수 잡 | ✅ `iceberg_maintenance_job` | ❌ 없음 | 🔴 **구조적 커버리지 공백 2건**(§5-2) |
| Iceberg changelog 판독 | ✅ `scripts/iceberg_changelog_probe.py` | ⚠️ 수동 1회(**2026-08-23 실측**, 종료 코드 `0`) | 실인프라 수동 관문(§5-3). 진단표는 관문이 아니다 |
| 분석 재현성 | ✅ `notebooks/`·`nbconvert` | ⚠️ 수동 1회(2026-08-19, 스타터 노트북 전 셀 실행) | 리포트(`docs/analyses/`)는 아직 없음 |

## 테스트 계층 (우선순위 순)

전역 규칙(**효율·비용·리스크·정확성**)에 따라 **비용 대비 회귀 방어 효과가 큰 순서**로 나열한다.
위에서부터 채운다.

| 순위 | 계층 | 우선도 | 이유 (효율·비용·리스크·정확성) |
| --- | --- | --- | --- |
| 1 | **dbt 스키마 테스트** | ★★★★★ | 선언 한 줄로 데이터 무결성 회귀를 즉시 포착. 비용 최저, 규약·패키지 이미 존재 |
| 2 | **통합·스모크**(`dg check`·`dbt build`) | ★★★★★ | 정의 로드·빌드 깨짐을 배포 전 차단하는 안전망. CI 게이트의 뼈대 |
| 3 | **dbt 단위 테스트** | ★★★★☆ | SOFA·Sepsis-3 등 복잡 SQL 분기 로직의 정확성 검증. 목킹 셋업 비용 있음 |
| 4 | **Dagster 에셋 pytest** | ★★★☆☆ | 적재 헬퍼·자산 로직 검증. 외부 리소스(S3·dbt) mock 필요로 비용 중간 |
| 5 | **dbt singular 테스트** | ★★☆☆☆ | 스키마 테스트로 표현 못 하는 교차 테이블 불변식만 선별 사용 |
| 6 | **분석 재현성** | ★★☆☆☆ | 파이프라인이 아니라 **결론**을 방어한다. 실인프라가 필요해 비싸고 느리지만, 다른 어떤 계층도 "이 수치가 재현되는가"를 묻지 않는다 |

> **실인프라에 붙는 계층은 셋이고, 셋 다 상시 CI가 아니라 수동 관문이다** — §5-1(Spark Connect
> 어댑터 스모크, *의존성 상한 인상 직전*) · **§5-3**(Iceberg changelog 판독, *changelog 경로를 건드리기
> 직전*) · §6(분석 재현성, *분석 산출물 공유 직전*). 나머지는 실인프라 미접속이 원칙이다(격리·재현).
>
> 🔴 **예외가 둘에서 셋으로 늘었다(2026-08-23).** "분석 재현성만 실인프라에 붙는다"는 초기 서술은
> 이미 §5-1로 깨졌고 이번에 §5-3이 더해졌다 — **예외는 계층이 아니라 *관문 시점*으로 구분**한다.
> 셋 다 "무엇을 하기 직전에 통과시키는가"가 다르고, 그것이 이 예외들을 정당화하는 유일한 축이다.

---

## 1. dbt 스키마(데이터) 테스트 — ★★★★★

> 상세 규약·패키지는 [`conventions/dbt.md#테스트-필수`](conventions/dbt.md#테스트-필수). 여기서는 **무엇에 다는지**를 정한다.

- **모든 모델의 grain 컬럼**(예: `stay_id`·`subject_id`·`hadm_id`·`charttime`)에 `not_null`을 단다.
- **grain을 이루는 키/키 조합**에 `unique`(단일)·`dbt_utils.unique_combination_of_columns`(복합)를 단다.
  - 예: `sepsis3`는 `stay_id` 1건/재실 → `unique`. `sofa`는 `(stay_id, hr)` 조합 → 복합 유니크.
- **원천/상위 모델 참조 키**에 `relationships`를 달아 lineage 무결성을 보장한다
  (예: 개념 모델의 `stay_id` → `icustay_times.stay_id`).
- **유한 범주·점수 컬럼**에 `accepted_values`/범위 테스트를 단다.
  - SOFA 장기 점수(`*_24hours`)는 `0~4`, 총점 `sofa_24hours`는 `0~24`,
    플래그(`sepsis3`·`suspected_infection`·`positive_culture`)는 `[0, 1]`.

```yaml
# models/mimic_iv/tables/schema.yml — grain·범위 테스트 예
models:
  - name: sofa
    columns:
      - name: stay_id
        data_tests: [not_null]
      - name: sofa_24hours
        data_tests:
          - not_null
          - dbt_expectations.expect_column_values_to_be_between:
              min_value: 0
              max_value: 24
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [stay_id, hr]
```

> **키워드 주의**: dbt 1.8+는 `tests:` → **`data_tests:`** 로 이름이 바뀌었다(구 키는 deprecated). 신규는 `data_tests:`로 쓴다.

## 2. dbt 단위 테스트 (unit tests) — ★★★★☆

> 입력을 **목(mock)** 으로 고정하고 **기대 출력**을 검증한다. 데이터가 아니라 **SQL 변환 로직**을 테스트한다.
> 작성은 스킬 [`adding-dbt-unit-test`]을 활용한다.

- **대상**: 조건 분기·집계·윈도우가 복잡해 회귀 위험이 큰 모델.
  이 레포에선 **SOFA 점수 산출(`sofa`)·Sepsis-3 판정(`sepsis3`)·환기 상태 분류(`ventilation`)**
  같은 규칙 기반 파생 모델이 1순위다.
- **위치**: 대상 모델과 같은 `models/<dataset>/**/` 아래 `.yml`의 `unit_tests:` 블록.
- **grain 최소 표본**만 목킹한다(경계값·null·범위 밖 값 위주). 전체 컬럼을 채우지 않는다.

```yaml
# 예: SOFA 총점이 6개 장기 합인지 최소 표본으로 검증
unit_tests:
  - name: test_sofa_total_is_sum_of_organs
    model: sofa
    given:
      - input: ref('icustay_hourly')
        rows: [{stay_id: 1, hr: 0, endtime: "2020-01-01 00:00:00"}]
      # ... 각 장기 입력 ref를 경계값으로 목킹 ...
    expect:
      rows: [{stay_id: 1, hr: 0, sofa_24hours: 6}]
```

> 어댑터(`dbt-trino`)의 단위 테스트 지원 여부를 **도입 전 1개 모델로 확인**한다(미지원 시 3번 singular로 대체).

## 3. dbt singular 테스트 — ★★☆☆☆

- 스키마/단위 테스트로 표현하기 어려운 **교차 테이블 불변식**만 `dbt_pipelines/tests/*.sql`에 둔다.
  (테스트 SQL은 **위반 행을 반환**하면 실패 — 통과 시 0행.)
- 예: `sepsis3.suspected_infection_time`이 항상 `sofa` 관측 구간 안에 드는지 등 조인 기반 규칙.
- 남용하지 않는다 — 단일 컬럼 규칙은 1번(스키마 테스트)으로 충분하다.

## 4. Dagster 에셋 pytest — ★★★☆☆

> 상세는 [`conventions/python.md#테스트`](conventions/python.md#테스트). 테스트는 `src/tests/` 하위, `pytest`로 작성한다.

- **적재 헬퍼**(`common/helper.py`의 `read_csv_gz_table`·`load_heavy_csv_gz_to_iceberg`)의 **순수 변환 로직**을
  작은 인메모리 표본으로 검증한다(파싱·스키마·행 수).
- **`@asset` 함수**는 `materialize`로 검증하되 **외부 리소스(S3·dbt·Trino)는 mock/stub**으로 주입한다
  (실제 SeaweedFS·Trino에 붙지 않는다 — 단위 테스트는 격리·재현이 원칙).
- Dagster 정의 무결성(자산키·의존성·리소스 매핑)은 4번보다 5번 스모크(`dg check`)로 싸게 잡는다.

```python
# src/tests/test_assets.py — 리소스 mock + materialize 예 (권장 패턴)
from unittest.mock import MagicMock

import pyarrow as pa
from dagster import materialize

from dagster_project.defs.eicu.assets import patient


def test_patient_loads_table() -> None:
    s3 = MagicMock()  # S3Resource stub — 실제 SeaweedFS 미접속
    # helper가 읽을 표본을 반환하도록 구성...
    result = materialize([patient], resources={"s3": s3, "io_manager_eicu": MagicMock()})
    assert result.success
```

> 자산 정의 모듈은 `from __future__ import annotations` 금지 규칙([dagster.md](conventions/dagster.md#자산-모듈에서는-from-__future__-import-annotations-금지))이 테스트 임포트에도 그대로 적용된다.

## 5. 통합·스모크 — ★★★★★

배포·CI의 **최소 게이트**. 개별 로직이 아니라 **파이프라인이 로드·빌드되는지**를 싸게 검증한다.

| 명령 | 검증 내용 |
| --- | --- |
| `dg check` | Dagster 정의(자산·리소스·잡·스케줄) 로드·타입 정합성 |
| `dbt build --target dev` | dbt 모델 컴파일 + run + 스키마/단위/singular 테스트 일괄 실행 |
| `ruff check` · `sqlfluff lint` | 정적 검사 — **pre-commit 훅으로 자동 실행**(`.pre-commit-config.yaml`) |
| `mypy` | 타입 정합성 — **훅 미포함, 수동 실행**(의존성 환경 필요) |

> 🔴 **"정적 검사"를 한 덩어리로 읽지 않는다.** 2026-08-21 이전 이 표는 세 도구를 함께 적어
> 셋 다 커밋 시 도는 것처럼 보였으나, 실제로는 `sqlfluff`·`mypy` **둘 다 훅에 없었다**.
> 지금 `sqlfluff`는 들어갔고 **`mypy`는 여전히 수동**이다 — 목록에 있다는 것과 실행된다는 것은 다르다.

> `dbt build`는 `run`+`test`를 합쳐 실행하므로 **1~3번 dbt 테스트가 이 명령에 자동 포함**된다.
> Dagster 경유 실행 시엔 `dbt.cli(["build"])`가 `dbt_assets`에서 동일하게 테스트를 태운다([dagster.md](conventions/dagster.md#dbt-통합-pythonic-dbt_assets)).

### 5-1. Spark Connect 어댑터 스모크 — 수동 관문

```shell
kubectl port-forward svc/spark-connect 15002:15002   # 별도 터미널 (실인프라 필요)
uv run scripts/spark_connect_smoke.py
```

**무엇을 방어하나.** dbt-spark는 Spark Connect를 **공식 지원하지 않는다** —
`SparkConnectionMethod`는 thrift/http/odbc/session 4개뿐이고 connect가 없다. 그런데도 도는 것은
`session.py`가 `builder.config()` → `getOrCreate()`를 타서 pyspark classic 빌더가 `spark.remote`를
RemoteSparkSession으로 위임하는 **내부 동작**에 얹히기 때문이다([architectures/spark.md](architectures/spark.md)).
**계약이 아니라 구현에 의존**하므로 `dbt-spark`·`pyspark` 업그레이드가 **에러 없이** 깨뜨릴 수 있다.
그래서 `pyproject.toml`이 상한을 minor로 묶어 두고(`dbt-spark<1.12`·`pyspark<3.6`),
**상한을 올리기 직전에** 이 스모크를 관문으로 통과시킨다.

검증 경로: 접속 → `create table`(iceberg) → 스키마 테스트 → **`merge into` 실발행 확인** →
`docs generate`(카탈로그 메타데이터) → 전용 네임스페이스 `iceberg.smoke` 자동 정리.

🔴 **자기 정정 — 그 "자동 정리"는 카탈로그만 지우고 있었다 (2026-08-23).** `DROP TABLE`에 `PURGE`가
빠져 있어 카탈로그에서 `smoke`는 사라지는데 `s3://warehouse/smoke/`에는 parquet·metadata가
**85개(275.9 KiB)** 남아 있었다. 🔴 **이 스크립트는 "의존성 상한 인상 직전 수동 관문"이라 돌 때마다
누적된다** — 85개는 한 번의 잔여물이 아니라 **여러 번 돈 결과**다. 📌 **관문일수록 자기 잔여물에
민감해야 한다**: 관문은 반복 실행이 전제이고, 흔적이 warehouse에 남으면 관문이 스스로 §5-2 ⓑ의
**"지목할 대상이 없는 고아"** 를 생산한다(테이블 단위 프로시저의 스캔 범위 밖이라 유지보수 잡을 돌려도
사라지지 않는다). `DROP TABLE ... PURGE`로 고쳤고 재실행 후 같은 prefix가 **0개**임을 S3에서 확인했다.
발견 경위와 "어느 층에서 참인지"의 교훈은
§5-3 [정리 검증](#정리-검증--스크립트의-삭제함-출력을-믿지-않는다)에 있다 — **한쪽만 읽고 닫지 않는다.**

| 종료 코드 | 의미 |
| --- | --- |
| `0` | 전 항목 통과 — 상한을 올려도 된다 |
| `1` | **회귀** — Connect 경로가 깨졌다 |
| `2` | **판정 불가** — 포트 미개방·venv 부재 등 사전 조건 미충족 |

🔴 **`1`과 `2`를 나눈 이유**가 이 게이트의 핵심이다. 포트가 닫혀 못 붙은 것을 실패로 읽으면
회귀가 아닌데 회귀로 오진하고, 통과로 읽으면 **관측 경로가 죽은 채 통과**가 된다(철학 원칙 7).
같은 이유로 2회차 build는 종료 코드가 아니라 **`merge into`가 실제로 발행됐는지**를 본다 —
incremental이 조용히 full-refresh로 떨어져도 종료 코드는 `0`이다.

> **같은 원칙의 다른 구현**: 여기서는 *결과 코드*로 "실패 vs 판정 불가"를 가른다면,
> [`conventions/agents.md`](conventions/agents.md#권한-매트릭스-실측) §권한 매트릭스는 *증거 출처*로
> "자기보고 vs 런타임"을 가른다(**도구 유무는 물어보지 말고 쓰게 시킨다**).
> 둘 다 "부정 결과는 관측 경로 생존을 함께 확인해야 유효하다"([philosophy.md](philosophy.md) 원칙 7)의
> 구현이고, **다른 층에서 같은 실수를 막는다** — 한쪽만 알면 다른 쪽에서 똑같이 속는다.

> **격리 원칙의 의도된 예외**(§6과 같은 성격): 실인프라에 붙어야만 의미가 있어 **CI 상시 게이트가
> 아니다**. 다만 §6이 *분석 산출물 공유 직전*의 관문이라면, 이쪽은 **의존성 상한 인상 직전**의 관문이다.
> 게이트 자체를 **일부러 위반시켜 확인**했다 — 포트를 닫고 돌려 `2`, 열고 돌려 `0`을 받았고,
> 그 과정에서 스크립트의 dbt 플래그 위치 오류(전역 자리에 둔 `--profiles-dir`)를 **거짓 회귀**로
> 잡아냈다. 게이트를 만들고 통과만 보면 이 오류는 "Connect가 깨졌다"로 오독됐을 것이다.

#### 🔴 B9 — 이 스모크는 **실 프로젝트 경로를 검증하지 않는다** (2026-08-22)

이 게이트의 관측 범위에 대한 한계다. **`exit 0`이 보증하는 대상이 생각보다 좁다.**

| | 스모크 픽스처 | `models/mimic_iv/tables/` 22모델 |
| --- | --- | --- |
| `file_format` | **명시**(`scripts/spark_connect_smoke.py:76`·`:87`에 `file_format='iceberg'`) | **미명시였다** — `dbt_project.yml`에 `+file_format: iceberg`를 넣기 전까지 |

🔴 **즉 유일한 관측 수단이 실 모델과 다른 코드 경로를 본다.** dbt-spark는 `file_format`에 따라
DDL 분기가 갈리므로(`ALTER TABLE` 발행 여부·DROP+CREATE 여부 —
[conventions/dbt.md](conventions/dbt.md) §`+file_format: iceberg`는 필수다), 픽스처가 통과해도
**실 모델 경로의 결함에는 아무 보증을 주지 못한다.**

- 실제로 `+file_format` 누락 결함은 **이 스모크를 `0`으로 통과시킨 채** 존재하고 있었다.
- 📌 **교훈은 "스모크를 늘려라"가 아니라 "스모크가 *무엇의* 대리표본인지 적어라"** 다.
  픽스처는 **어댑터 접속 경로**의 대리표본이지 **프로젝트 모델 설정**의 대리표본이 아니다.
  두 역할을 한 스크립트에 기대면 통과 신호가 실제보다 넓게 읽힌다([philosophy.md](philosophy.md) 원칙 7).
- ⚠️ **현재 상태**: `+file_format: iceberg`는 추가됐지만, 그 설정이 실제로 의도대로 도는지는
  **22모델을 `dbt build`로 돌려야 확인된다** — 아직 돌린 적이 없다(원천 데이터 미확보).

### 5-2. 🔴 `iceberg_maintenance_job`의 커버리지 공백 2건 (2026-08-22 실측)

유지보수 잡([architectures/spark.md](architectures/spark.md) §안전 순서)에 **테스트가 없을 뿐 아니라,
잡 구조 자체가 특정 상황을 영영 처리하지 못한다.** 테스트를 추가하기 전에 이 둘을 먼저 안다.

#### ⓐ 첫 op이 실패하면 뒤 op이 전부 중단된다

op 의존성이 `optimize_iceberg_files → expire snapshots → remove_orphan_files` 순서라,
🔴 **첫 op이 원천 테이블 부재로 실패하면 나머지가 `Not executing`으로 전부 건너뛰어진다.**

- ⇒ 원천 테이블이 아직 없는 지금 같은 상태에서 **orphan 정리가 영영 돌지 않는다.**
- **순서 강제(안전)와 실패 전파(가용성)가 같은 배선에 묶여 있다.** 순서는 지켜야 하지만,
  "앞 단계가 할 일이 없어서 실패한 것"과 "앞 단계가 깨진 것"은 다르다 — 전자는 뒤를 막을 이유가 없다.

#### ⓑ 어떤 테이블에도 속하지 않는 객체를 지울 경로가 **잡에 없다**

`remove_orphan_files`는 **인자로 받은 테이블의 location 하위만** 스캔한다.

- ⇒ 🔴 **카탈로그에서 이미 지워진 테이블의 잔여 객체는 인자로 지목할 대상이 없어 스캔 범위 밖이다.**
- 과거 사례(**대상 해소 — 2026-08-23**): 체크섬 결함으로 손상된 `smoke`/`smoke_seed`를 카탈로그에서
  드롭했으나 **객체 53개가 orphan으로 남아 있었다**
  ([architectures/spark.md](architectures/spark.md) §SeaweedFS 체크섬 결함).
  §5-1의 스모크에 `PURGE`를 붙이고 **사람이 일괄 삭제**해 `s3://warehouse/smoke/`는 **0개**가 됐다
  (prefix 자체 소멸). 🔴 **잡이 지운 것이 아니다** — 위 문장(*"잡의 스캔 범위 밖"*)은 **여전히 참**이고
  사라진 것은 **대상**이지 **공백**이 아니다. 이 구분을 흐리면 커버리지가 닫힌 줄로 읽힌다.
- 🔴 **현행 실례 (2026-08-23 `warehouse` 버킷 전수 관측)**: `s3://warehouse/chk/` **6개(15.0 KiB)**.
  네임스페이스 `chk`는 카탈로그에 **있는데 테이블이 0개**이므로(테이블은 `poc.sample`·
  `poc.sample_flink`·`poc.sample_stream` 3개뿐) 이 객체들은 **인자로 지목할 대상이 없다** —
  ⓑ가 말하는 고아의 **현행 예시**다. 🔴 **출처는 `미확인`**: 어느 작업이 만들었는지 관측하지 않았다.
- ⚠️ **`s3://warehouse/flink-checkpoints/` 2개는 고아가 아니다** — **0.0 KiB 디렉터리 마커**다.
  `flink cancel` 시 `DELETE_ON_CANCELLATION`이 실제 체크포인트를 지운 뒤 남은 빈 마커이고
  **용량을 먹지 않는다.** 크기 0이 구분 기준이며, 위 고아와 **같은 목록에 세지 않는다**.
- 📌 **"orphan 정리 잡이 있다"와 "orphan이 정리된다"는 다르다.** 잡의 이름이 커버리지를
  실제보다 넓게 들리게 한다 — **테이블 단위 프로시저이지 warehouse 단위 청소기가 아니다.**

#### 🔴 부정 결과 판정의 함정 — "에러 0건"을 통과로 읽지 않는다

`remove_orphan_files` 실행에서 `No FileSystem for scheme "s3"`가 **0건**이었다.
**이것을 `spark.hadoop.fs.s3*` 배선의 통과 근거로 읽으면 안 된다.**

- 프로시저가 **테이블 해석 단계에서 먼저 죽어 Hadoop FS 나열에 도달조차 못 했다.**
- ⇒ 해당 에러가 날 **코드 경로가 실행되지 않았다.** 판정은 `통과`가 아니라 **`미검증`** 이다.
- 이것이 §5-1이 종료코드 `1`(회귀)과 `2`(판정 불가)를 나눈 것과 **정확히 같은 구분**이다.
  다만 여기서는 그 구분을 **잡이 자동으로 해주지 않으므로 사람이 해야 한다** —
  부정 결과는 **관측 경로가 살아 있었음을 함께 확인**해야 유효하다([philosophy.md](philosophy.md) 원칙 7).

### 5-3. Iceberg changelog 판독 관문 + 소스 적격성 진단 — 수동 관문 (2026-08-23 실측)

```shell
kubectl scale deploy/spark-connect --replicas=1
kubectl port-forward svc/spark-connect 15002:15002   # 별도 터미널 (실인프라 필요)
uv run scripts/iceberg_changelog_probe.py
```

**무엇을 방어하나.** Phase 3 스트리밍과 증분 조회가 전부 **Iceberg Spark 프로시저
`create_changelog_view`** 위에 서는데([architectures/spark.md](architectures/spark.md)
§changelog는 4.1이 주지 않는다), 이 프로시저의 **인자 계약·계보 판정·런타임 jar 버전**은
`dbt compile`이나 `dg check`가 전혀 보지 않는 층이다. 그래서 changelog 경로를 건드리기 직전의
관문으로 쓴다 — §5-1이 *의존성 상한 인상 직전*이라면 이쪽은 **changelog 경로 변경 직전**이다.

**두 가지를 한 번에 한다(섞어 읽지 않는다).**

| 부분 | 성격 | 종료 코드에 영향 |
| --- | --- | --- |
| **관문**(프로브) | 전용 네임스페이스 `chglog`에 알려진 변경 3건을 만들고 기대 변경분과 대조 | ✅ 있다 |
| **진단**(기존 테이블) | 카탈로그의 실 테이블이 changelog·스트림 소스로 쓸 수 있는지 판정 | ❌ **없다**(읽기 전용 관측) |

🔴 **진단표를 게이트로 읽지 않는다.** 소스를 *고르기 위한* 관측이고, 표가 `불가`를 찍어도
종료 코드는 `0`이다. 두 역할을 한 스크립트에 담았으므로 통과 신호의 범위를 여기서 못 박는다
(§5-1 B9의 "스모크가 *무엇의* 대리표본인지 적어라"와 같은 이유).

#### 종료 코드

| 종료 코드 | 의미 |
| --- | --- |
| `0` | **관문 통과** — changelog가 기대한 변경분을 낸다 |
| `1` | **회귀** — 프로시저 인자 계약 변경 · 계보 판정 변경 · Iceberg 런타임 jar 불일치 중 하나 |
| `2` | **판정 불가** — venv 부재·Spark Connect 미도달 등 사전 조건 미충족 |

🔴 **`2`를 `1`로 읽으면 안 되는 이유**는 §5-1과 정확히 같다. `spark-connect`를 `--replicas=0`으로
내려 둔 상태(=이 저장소의 **정상 상태**, 회수 규율)에서 돌리면 항상 도달 불가가 나는데, 이걸
회귀로 읽으면 **회귀가 아닌 것을 회귀로 오진**하고 통과로 읽으면 **관측 경로가 죽은 채 통과**가 된다.
🔴 이 관문은 **회수 규율과 구조적으로 충돌하므로 `2`가 기본 결과다** — 돌릴 때만 올리고
끝나면 다시 내린다(2026-08-23 검증에서도 `replicas=1` → 검증 → **즉시 `0`으로 회수**했고,
회수 후 노드 실사용이 [resource-sizing.md](resource-sizing.md)의 "회수 후 실측" **2250m(28%) /
3140Mi(14%)** 로 복귀함을 확인했다).

#### 🔴 게이트 자체를 일부러 위반시켜 확인했다 (2026-08-23)

세 경로를 **각각 만들어** 돌렸다. 통과만 보고 닫지 않는다([philosophy.md](philosophy.md) 원칙 7).

| 경로 | 위반 방법 | 관측된 종료 코드 |
| --- | --- | --- |
| 정상 | 그대로 실행 | **`0`** |
| 사전 조건 미충족 | `SPARK_REMOTE=sc://localhost:1`(죽은 포트) | **`2`** |
| 회귀 | 기대값 상수를 일부러 틀린 값으로 패치 | **`1`** — `got={'INSERT': 2} want={'INSERT': 99}` |

📌 회귀 경로에서 **틀린 쪽이 기대값이었다는 점**이 중요하다. 실측값 `{'INSERT': 2}`는 그대로였고
기대값만 흔들었으므로, 이 `1`은 **비교 로직이 실제로 살아 있음**을 보인다 — "실패가 났다"가 아니라
"실패를 **탐지하는 경로**가 돈다"의 증거다.

#### 실측 결과 — `create_changelog_view` 의미론 (Spark **3.5.9** · Iceberg **1.11.0**)

프로브 테이블에 **append 3행 → append 2행 → update 1행**을 만든 뒤 창을 잡았다.

| 창 / 옵션 | 결과 | 기대 |
| --- | --- | --- |
| append 창 `s1→s2` | `{INSERT: 2}` | 일치 |
| 갱신 창 `s2→s3`, `identifier_columns` **없음** | `{DELETE: 1, INSERT: 1}` | 일치 |
| 갱신 창 `s2→s3`, `identifier_columns => array('id')` | `{UPDATE_BEFORE: 1, UPDATE_AFTER: 1}` | 일치 |

🔴 **`UPDATE`는 `append`가 아니라 `overwrite` 스냅샷을 남긴다**(프로브의 ops = `append, append,
overwrite`). 이것이 Flink 스트리밍 읽기 제약이 실제로 물리는 지점이다
([architectures/flink.md](architectures/flink.md) §급소).

#### 🔴 창은 `committed_at`이 아니라 `parent_id`로 잡는다

스크립트가 `.history`의 계보를 직접 걸어 창을 잡는 이유는 실측에서 나왔다 —
**`.snapshots`를 `committed_at`으로 정렬한 인접 두 행이 부모-자식이라는 보장이 없다.**
`poc.sample`의 인접 두 행(6→7)에 창을 잡자 `IllegalArgumentException: Starting snapshot
(exclusive) … is not a parent ancestor of end snapshot`으로 죽었다.
원인과 파급은 [architectures/spark.md](architectures/spark.md) §`createOrReplace`는 계보를 끊는다.

#### 🔴 이 관문이 보증하지 않는 것

- **Flink 스트리밍 읽기는 별개 축이다.** Spark `create_changelog_view`는 overwrite 스냅샷 테이블에서도
  뷰 생성에 성공하지만(`poc.sample`에서 실측), **Flink 스트리밍 읽기는 append만** 본다.
  ⇒ 이 게이트의 `0`은 **Flink 축에 아무 보증을 주지 않으며**, 그 축은 **Flink 잡으로만 닫힌다**
  (2026-08-23 현재 `미검증`).
- **진단표의 `flink_stream` 열은 소스 *후보*를 좁힐 뿐** 스트리밍이 실제로 도는지를 말하지 않는다.
- **값 정합이 아니라 변경 종류·건수까지**다. 22모델의 값 정합은 여전히 미검증이다(§5-1 B9).

#### 정리 검증 — 스크립트의 "삭제함" 출력을 믿지 않는다

프로브는 전용 네임스페이스 `chglog`에 만들고 종료 시 지운다. 🔴 **삭제 여부는 스크립트 출력이 아니라
카탈로그 Postgres에서 직접 확인**했다(2026-08-23) — `iceberg_tables`에 `poc.sample`·`poc.sample_flink`
둘뿐이었고 `iceberg_namespace_properties`에 `chglog`가 없었다. 자기보고가 아니라 **다른 층의 관측**으로
닫는다([conventions/agents.md](conventions/agents.md) §권한 매트릭스와 같은 구분).

#### 🔴 위 문단의 자기 정정 — 카탈로그 축에서만 참이었다 (2026-08-23)

위 문단을 지우지 않고 두는 이유는 **그 관측 자체가 거짓이 아니었기 때문**이다. 카탈로그 조회 결과는
실제로 0건이었다. 불완전했던 것은 거기서 "정리됐다"로 닫은 **결론의 범위**다 — 같은 시각
`s3://warehouse/chglog/`에는 parquet·metadata **64개(212.7 KiB)** 가 그대로 남아 있었다.
`DROP TABLE`은 **카탈로그 항목만** 지우고 스토리지는 건드리지 않는다(`PURGE` 누락).

🔴 **아이러니가 이 절의 진짜 교훈이다.** 제목대로 스크립트의 자기보고를 의심해 **다른 층에서 확인**한
방법론은 옳았다. 그런데 **고른 층이 하나뿐이었고, 그 층에서만 참이었다.** ⇒ *"자기보고 대신 다른 층에서
본다"* 는 맞지만, **"어느 층에서 참인지"** 를 함께 적지 않으면 문장은 실제보다 넓게 읽힌다
(계측 단위 — [philosophy.md](philosophy.md) 원칙 7).

🔴 **이런 오류는 검산을 통과하면서 틀린다.** 의심스러워 카탈로그를 몇 번 다시 조회해도 계속 0건이라
**재확인할수록 확신만 커지고**, 틀린 축(스토리지)은 그동안 한 번도 조회되지 않는다. 값이 틀린 게 아니라
**세는 대상이 어긋난 정답**이라 검산으로는 걸리지 않는다.

**지금 상태 — 두 축 모두 정리된다.** 프로브에 `DROP TABLE ... PURGE`를 붙여 **카탈로그와 스토리지
양쪽**이 정리된다. 검증도 카탈로그가 아니라 **실행 후 S3 prefix 재조회**로 했다(2026-08-23) —
남아 있던 고아가 삭제되고 재실행 후 `s3://warehouse/chglog/`가 **0개**, 버킷 prefix 목록에서
**prefix 자체가 사라졌다**. 🔴 "0개"와 "prefix가 없다"를 함께 본 이유도 같다 — 한 축의 빈 응답만으로는
*지워졌다*와 *애초에 안 봤다*가 구분되지 않는다.

📌 이 잔류물은 §5-2 ⓑ가 말한 **"카탈로그에서 이미 지워진 테이블의 잔여 객체"** 와 같은 계열이다 —
관문 스크립트가 스스로 그것을 만들고 있었다. 같은 결함이
[§5-1 Spark Connect 어댑터 스모크](#5-1-spark-connect-어댑터-스모크--수동-관문)에도 있었고
(`s3://warehouse/smoke/`에 **85개(275.9 KiB)**) 함께 고쳤다. 그쪽은 상한 인상 때마다 도는 관문이라
**누적**됐다는 점이 다르다.

#### ⚠️ 접속 경로 드리프트 — 선언된 경로가 실제로는 쓰이지 않았다 (미결)

이번 실행은 `.env`의 **`SPARK_REMOTE=sc://localhost:15002`(port-forward 폴백)** 로 했다.

- `.env.example`이 정본으로 제시하는 것은 **gRPC TLS Ingress**
  (`sc://spark-grpc.localtest.me:8443/;use_ssl=true`)이고, 그 경로가 요구하는 CA 파일
  `~/.lakehouse-ca.crt`는 **존재하지 않았다**. Ingress 리소스 자체는 살아 있다
  (`spark-connect-grpc`, HOSTS `spark-grpc.localtest.me`).
- ⇒ **"선언돼 있다"와 "쓰이고 있다"가 갈렸다.** 폴백이 정상 경로처럼 굳어지면 TLS 경로는
  검증 없이 낡아간다. 🔴 **이번에 고치지 않았고 `미결`로 남긴다** — 교정 대상이 이 문서 밖
  (`.env.example`·CA 배포 절차)이다.

---

## 6. 분석 재현성 검증 — ★★☆☆☆

> 규칙 정본은 [`conventions/analysis.md`](conventions/analysis.md). 여기서는 **무엇을 검증하는지**를 정한다.
> 1~5번이 "파이프라인이 옳게 도는가"를 묻는다면, 이 계층은 **"내린 결론이 재현되는가"** 를 묻는다.

- **노트북은 실행 가능해야 한다.** 위→아래 1회 실행으로 끝까지 도는지 `nbconvert`로 확인한다.
  중간에 죽는 노트북은 고치거나 지운다(탐색이 끝난 노트북은 지우는 게 기본이다).

```shell
kubectl port-forward svc/spark-connect 15002:15002   # 별도 터미널 (실인프라 필요)

cd dagster/dockerfile.d/src
uv run --group notebook jupyter nbconvert --to notebook --execute \
    --output /tmp/_verify.ipynb ../../../notebooks/00-lakehouse-connect.ipynb
```

- 🔴 **실행 산출물은 즉시 지운다.** `--output` 사본과 `.ipynb_checkpoints/`에는 조회 결과가 그대로
  박제된다(DUA — [`security.md`](security.md)). 검증 직후 삭제하고 저장소로 들이지 않는다.
- **리포트의 인용 수치는 재현 경로를 갖는다.** `docs/analyses/`의 각 수치가 gold 또는 dbt 모델을
  경유하는지 확인한다. 노트북 임시 SQL로 낸 숫자는 리포트에 실리지 않는다(analysis.md §4).
- **gold 모델 자체의 무결성은 1번(스키마 테스트)이 맡는다** — grain 유니크·범위·`relationships`.
  이 계층에 중복해서 두지 않는다.

> **격리 원칙의 의도된 예외**: 1~4번은 실인프라에 붙지 않는 것이 원칙이지만, 이 계층은
> **실제로 붙어야만 의미가 있다**(접속·권한·데이터 존재가 검증 대상이다). 그래서 CI 상시 게이트가
> 아니라 **분석 산출물을 커밋·공유하기 직전의 수동 관문**으로 쓴다.
>
> 실제로 이 검증이 잡은 사례: 호스트에서 pyiceberg로 붙을 때 `list_tables()`는 성공하고
> `load_table()`만 `ACCESS_DENIED`로 죽는 **부분 성공**(카탈로그는 Postgres, `metadata.json`은 S3).
> 컴파일·로드 검증으로는 절대 드러나지 않는 층이다([`operations.md`](operations.md) §1-2).

## 위치·네이밍 규칙

| 테스트 유형 | 위치 | 네이밍 |
| --- | --- | --- |
| dbt 스키마/단위 테스트 | 모델과 같은 `models/<dataset>/**/schema.yml`(또는 `_<dataset>__models.yml`) | dbt 표준 키(`data_tests:`·`unit_tests:`) |
| dbt singular 테스트 | `dbt_pipelines/tests/*.sql` | 검증 대상이 드러나게 (`assert_<불변식>.sql`) |
| Dagster pytest | `dagster/dockerfile.d/src/tests/` | `test_*.py` · 함수 `test_*` |

- 테스트 코드는 ruff `per-file-ignores`로 `ANN`·`D`·`S101`(assert)·`ARG` 면제
  (설정은 루트 `pyproject.toml`의 `"**/tests/**"`, 상세 [python.md](conventions/python.md#테스트)).

## 실행 명령

```bash
# dbt (스키마·단위·singular 테스트 일괄) — src/dbt_pipelines 에서
dbt build --target dev      # run + test
dbt test                    # 테스트만

# Dagster 에셋 pytest — src/ 에서
pytest                      # tests/ 하위 수집

# 정의 스모크 (정적 로드 검증)
dg check

# 정적 검사 (pre-commit이 커밋 시 자동 실행 — repo 루트에서)
ruff check . && sqlfluff lint dagster/dockerfile.d/src/dbt_pipelines/

# 타입 정합성 (훅에 없다 — 수동)
uv run --project dagster/dockerfile.d/src --with mypy mypy dagster/dockerfile.d/src/src

# 수동 관문 (실인프라 필요 — 상시 CI 아님)
uv run scripts/spark_connect_smoke.py       # §5-1 의존성 상한 인상 직전
uv run scripts/iceberg_changelog_probe.py   # §5-3 changelog 경로 변경 직전
```

## 무엇을 테스트하고, 무엇을 안 하나

- **테스트한다**: 파생 로직(SOFA·Sepsis-3 등)의 정확성, grain 무결성(유니크·not_null),
  값 범위·범주, lineage 참조 무결성, 정의 로드·빌드.
- **테스트하지 않는다**: 외부 시스템 자체(SeaweedFS·Trino·Postgres의 동작), 원천 데이터의 임상적 타당성
  (데이터셋 제공자 책임), 서드파티 라이브러리 내부. **단위 테스트에서 실인프라 접속 금지**(격리·재현).

## 누가 쓰고, 누가 채점하나

테스트는 **전담 워커(`tester`)를 두지 않는다** — 행위가 이미 3축에 분해돼 있다
(정본 [`conventions/agents.md`](conventions/agents.md#전문-워커-3종-세트의-경계-중첩-금지)).

| 행위 | 워커 | 축 |
| --- | --- | --- |
| 테스트를 **쓴다** | `data-engineer` | 구현 |
| 통과했는데 **값이 이상하다** | `data-verifier` | 실측 |
| **커버리지·게이트**를 감사하고, 작성된 테스트를 **사후 채점**한다 | `data-qa` | 체계 |

🔴 **사후 채점은 생략하지 않는다** — 판정자가 쓰지 않으므로 구현자가 자기 코드의 테스트를 쓴다.
작성자와 채점자를 가르는 유일한 지점이고, 채점의 핵심 물음은 **"이 테스트를 일부러 위반시키면
실패하는가"** 다(통과 확인만으로는 [철학 원칙 7](philosophy.md)의 *실행됐다*뿐인 통과와 구분되지 않는다).

## PDCA

- **Plan**: 계층을 우선순위(★)대로 채운다 — 1) 핵심 grain에 스키마 테스트 → 2) CI에 `dg check`+`dbt build` 게이트 → 3) SOFA·Sepsis-3 단위 테스트.
- **Do**: `schema.yml`에 `data_tests` 추가부터(비용 최저·효과 최대), 이후 상위 계층으로.
- **Check**: `dbt build --target dev`·`pytest`·`dg check`가 로컬·CI에서 녹색인지.
- **Act**: 회귀가 난 지점에 테스트를 **추가**해 재발을 막는다(테스트는 버그 재현부터).

## 참고

- dbt 데이터 테스트(Data tests): https://docs.getdbt.com/docs/build/data-tests
- dbt 단위 테스트(Unit tests): https://docs.getdbt.com/docs/build/unit-tests
- dbt_utils: https://github.com/dbt-labs/dbt-utils
- dbt_expectations: https://github.com/metaplane/dbt-expectations
- Dagster 자산 테스트(Testing assets): https://docs.dagster.io/guides/test/unit-testing-assets-and-ops
- pytest: https://docs.pytest.org/
