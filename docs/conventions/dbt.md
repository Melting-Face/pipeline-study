# dbt 코딩 규칙

어댑터: **`dbt-spark`**(현행 — 기본 타깃 `spark_connect`, 2026-08-21 전환) ·
**`dbt-trino`**(값 대조용 존치 — `DBT_TARGET=dev`로 전환, [../redesign.md](../redesign.md) Phase 1).
trino는 **제거된 것이 아니다** — 엔진 간 값 차이(`dbt.datediff`·`dbt.dateadd` 등)를 잡는 유일한 대조 수단이다.
두 어댑터를 **동시 설치**해 타깃만 바꿔가며 대조한다(Iceberg/SeaweedFS 레이크하우스는 공통).
프로젝트: `dagster/dockerfile.d/src/dbt_pipelines/`.

## 포매팅 / 린팅

- SQL은 [`sqlfluff`](https://docs.sqlfluff.com/)로 lint·format한다. **pre-commit 훅으로 강제**된다.
- dialect는 **`sparksql`** 이다(dbt 기본 타깃이 `spark_connect`이므로).
- templater는 **`jinja`** 다(`dbt` 아님 — 아래 §templater 참고).
- 들여쓰기 스페이스 4칸, `max_line_length = 100`.

```bash
# 반드시 repo 루트에서 실행한다 (library_path가 CWD 기준이다)
sqlfluff lint dagster/dockerfile.d/src/dbt_pipelines/
sqlfluff fix  dagster/dockerfile.d/src/dbt_pipelines/
```

### 설정은 repo 루트 `pyproject.toml`에서 관리한다

sqlfluff 명세는 별도 `.sqlfluff` 파일 대신 **repo 루트 `pyproject.toml`의 `[tool.sqlfluff.*]`** 섹션에 둔다.
(sqlfluff는 `tool.sqlfluff`로 시작하는 nested 섹션을 공식 지원하며, 대상 파일에서 상위로
올라가며 pyproject를 병합 탐색하므로 루트 설정이 적용된다.)
제외 목록만 루트 `.sqlfluffignore`에 둔다(`initdb/`·`dbt_packages/`·`target/`).

### templater — `dbt`가 아니라 `jinja` + 스텁을 쓴다

**`templater = "dbt"`는 게이트로 쓸 수 없다.** dbt templater는 모델을 **실제로 컴파일**하려고
dbt 어댑터를 통해 Spark Connect에 접속한다. 즉 커밋이 클러스터·port-forward 가용성에 묶인다.
그래서 이 저장소의 SQL 22개 모델은 오랫동안 **설정만 있고 아무 검사도 받지 않는 상태**였다
(문서는 "미포함 사유: dbt 모델 부재"라고 적고 있었으나 모델은 이미 실재했다 — 2026-08-21 교정).

`jinja` templater는 오프라인·수초라 게이트로 쓸 수 있다. 대신 **dbt 런타임 객체를 모른다**:

| 모델이 쓰는 것 | jinja templater 단독 | 대응 |
| --- | --- | --- |
| `ref()`·`source()`·`config()` | ✅ 내장(`apply_dbt_builtins`) | 없음 |
| `{{ elapsed(...) }}`·`{{ unnest_array(...) }}` | ❌ `adapter.dispatch`를 모름 | `[tool.sqlfluff.templater.jinja.macros]` **인라인 스텁** |
| `{{ dbt.dateadd(...) }}` | ❌ `dbt` 네임스페이스 없음 | `library_path = "sqlfluff_libs"` → `sqlfluff_libs/dbt.py` **셰임** |

- `sqlfluff_libs/`에 **`__init__.py`를 두지 않는다** — 없으면 각 `.py`가 개별 모듈로 로드돼
  파일명 `dbt.py`가 곧 jinja 네임스페이스 `dbt`가 된다.
- `library_path`는 **CWD 기준**이다(config 파일 기준이 아니다 — sqlfluff 4.3.0 소스 실측).
  `mypy`와 같은 이유로 repo 루트에서 실행해야 한다.

**`macros/`에 새 dispatch 매크로를 추가하면 스텁도 함께 추가한다.** 빠뜨리면 조용히 통과하지 않고
`TMP: Undefined jinja template variable`로 **시끄럽게 깨진다**(의도한 결합).

**스텁 출력은 선언된 dialect(`sparksql`)에 맞춘다.** 의미론은 무관하지만 **길이는 무관하지 않다** —
치환 결과의 길이·모양이 `LT05`(줄길이)·`LT02`(들여쓰기) 판정에 그대로 들어간다.
그래서 원본 dispatch 구현을 옮기지 않고 **짧은 등가 호출**로 둔다.

### 이 게이트가 검사하지 않는 것 (계측 단위)

린트 대상은 **실제 컴파일 SQL이 아니라 스텁 치환 SQL**이다. 보증 범위는 **스타일·구문**
(대소문자·들여쓰기·줄길이·참조)까지이고, **매크로 dispatch가 엔진별로 같은 값을 내는지는 검사하지 않는다.**
그건 [test.md](../test.md) §5-1 `scripts/spark_connect_smoke.py`의 몫이다.
**`sqlfluff` 통과를 값 정합의 근거로 읽지 않는다**([philosophy.md](../philosophy.md) 원칙 7).

같은 이유로 **`dialect = "sparksql"` 파싱 통과도 값의 근거가 아니다** — 그건 "구문이 sparksql
파서에 맞았다"는 뜻이지 "Spark에서 같은 값이 나온다"는 뜻이 아니다. 두 축은 갈린다.

## 디렉토리 / 레이어링 (Medallion)

- **`models/` 하위 디렉토리는 서브프로젝트(데이터셋)명으로 묶는다** — Dagster 데이터셋 서브프로젝트(`<dataset>/`)와 1:1.
  메달리온 레이어명(`bronze`/`silver`/`gold`)을 **디렉토리명으로 쓰지 않는다.**
- **메달리온 레이어는 tag로 표기**한다(스키마 접두어·디렉토리명으로 인코딩하지 않는다).
  Dagster 쪽에서는 동일 레이어를 `kinds`로 표기한다([dagster.md](dagster.md)).

| 레이어                  | 의미                          | materialization 권장 |
| ----------------------- | ----------------------------- | -------------------- |
| `bronze` (staging)      | 원천 정제(타입·컬럼명 표준화) | `view`               |
| `silver` (intermediate) | 조인·비즈니스 로직            | `view` 또는 `table`  |
| `gold` (marts)          | 분석/소비용 집계              | `table`              |

> **gold 레이어의 실행 기준(언제 만드나·grain·네이밍·테스트)은 [`analysis.md`](analysis.md) §2가 정본**이다.
> 여기서는 레이어 표기(tag)와 디렉터리 규칙만 정한다. **현재 gold 모델은 0개**이며,
> 같은 조회가 3회 이상 반복되거나 리포트가 그 수치를 인용하는 시점에 silver에서 승격한다.

현재 구조(코드 기준):

```text
models/
├── eicu/              # 서브프로젝트(데이터셋)명 = dagster_project/defs/eicu
│   └── source.yml     # Dagster 적재분(dbt 미생성) source 선언 (실버 모델 미이식)
└── mimic_iv/          # = dagster_project/defs/mimic_iv
    ├── source.yml     # 적재 11테이블 source 선언
    └── tables/        # 실버 모델 22개 — config(tags=['silver'])
        ├── sofa.sql               # SOFA 6장기 점수
        ├── sepsis3.sql            # Sepsis-3 onset
        ├── suspicion_of_infection.sql
        └── ...                    # vitalsign · gcs · icustay_hourly 등 (mimic-code 포팅)
```

> - 새 모델은 해당 **데이터셋 디렉토리** 안에 추가하고, 레이어는 `+tags`(또는 모델 내
>   `config(tags=...)`)로 표기한다. `tables/`처럼 materialization 그룹용 하위 디렉토리는 가능하나,
>   메달리온 레이어명(`bronze`/`silver`/`gold`)을 디렉토리명으로 쓰지 않는다.
> - `mimic_iv/tables/`는 `dbt_project.yml`에서 `+materialized: table`(dbt 기본 view 재정의)로
>   물리 테이블로 구체화한다. 모델·피처 상세는 [`dataset_schema.md`](../dataset_schema.md).

## 네이밍

- 모델 파일·이름은 `snake_case`, 영어.
- 모델명에는 **데이터셋·엔티티**를 드러낸다(예: `stg_eicu__patient`, `eicu__patient_summary`).
  레이어는 파일명이 아니라 **tag**로 구분한다.
- 컬럼명은 `snake_case`.

## 모델 설정 (선언적)

- materialization·group 등은 **`dbt_project.yml`의 `+config`** 또는 모델 상단
  **`{{ config(...) }}`** 로 선언한다.
- Dagster 그룹도 dbt 쪽 config로 선언한다 (Dagster 서브클래싱 대신).

```yaml
# dbt_project.yml — 디렉토리는 데이터셋명, 레이어는 +tags로 표기
models:
  dbt_pipelines:
    eicu: # ← 서브프로젝트(데이터셋) 디렉토리
      +schema: eicu # 출력 스키마 = Iceberg 네임스페이스 (generate_schema_name로 접두어 없음)
      +meta:
        dagster:
          group: eicu # Dagster 에셋 그룹을 dbt config로 선언
```

> **출력 스키마**: 데이터셋 디렉토리에 `+schema: <namespace>`를 주면 커스텀 매크로
> `generate_schema_name`이 target schema(dev/prod) **접두어 없이** 그대로 적용한다
> (메달리온은 스키마가 아닌 tag/kind로 표기 — `macros/generate_schema_name.sql`).
> **소유**: 각 데이터셋 모델은 Dagster `defs/<dataset>/dbt_assets.py`의
> `@dbt_assets(select="fqn:<dataset>")`가 머티리얼라이즈한다([dagster.md](dagster.md)).

### `@dbt_assets` 셀렉터는 `fqn:` 을 쓴다 (`path:` 금지)

데이터셋 소유 셀렉터는 **`select="fqn:<dataset>"`** 로 쓴다(`manifest`만으로 해석).

```python
# defs/mimic_iv/dbt_assets.py
@dbt_assets(
    manifest=dbt_project.manifest_path,
    project=dbt_project,
    select="fqn:mimic_iv",   # models/mimic_iv/ 하위 모델 전체를 데이터셋 단위로 소유
)
def mimic_iv_dbt_models(context, dbt): ...
```

- **`path:models/<dataset>` 를 쓰지 말 것.** `path:` 셀렉터는 정의 빌드 시점의
  **cwd 기준 파일시스템 글롭**이라, Dagster가 프로젝트 밖 경로에서 정의를 로드하면
  `The selection criterion 'path:...' does not match any enabled nodes` 경고와 함께
  **모델이 하나도 수집되지 않는다**(모델이 0개일 땐 드러나지 않는 잠복 버그).
- `fqn:<dataset>` 은 manifest의 fqn(`dbt_pipelines.<dataset>.…`)만으로 매칭하므로
  cwd·파일시스템에 의존하지 않아 안전하다. `project=dbt_project` 는 런타임 `dbt build`
  의 작업 디렉토리를 프로젝트로 고정하기 위해 함께 넘긴다.

```sql
-- models/eicu/eicu__patient_summary.sql
-- 레이어는 디렉토리가 아니라 tag로 표기한다(gold).
{{ config(materialized='table', tags=['gold']) }}

select
    patient_id,
    count(*) as lab_count
from {{ ref('stg_eicu__patient') }}
group by patient_id
```

## ref / source

- 모델 간 참조는 항상 **`{{ ref('...') }}`**, 원천 참조는 **`{{ source('...') }}`** 를 쓴다.
  하드코딩된 테이블명을 금지한다 (lineage 보존).

### Dagster가 적재한(=dbt 미생성) 테이블은 `source()`로 참조한다

S3 → Iceberg 적재 테이블(`<dataset>/`)은 **dbt가 만들지 않으므로** dbt source로 선언하고
`{{ source(...) }}`로 참조한다. source 정의는 **데이터셋별 서브디렉토리**에 둔다
(`models/<dataset>/source.yml`). 현재: `models/eicu/source.yml`, `models/mimic_iv/source.yml`.

```yaml
# models/eicu/source.yml
version: 2
sources:
  - name: eicu
    schema: eicu # Iceberg 네임스페이스 = Trino 스키마 (eicu NAMESPACE)
    tables:
      - name: patient
        meta:
          dagster:
            asset_key: ["patient"] # ← Dagster 자산키와 1:1 매핑(lineage 연결)
```

```sql
-- models/eicu/stg_eicu__patient.sql   (레이어는 tag로: config(tags=['silver']))
select * from {{ source('eicu', 'patient') }}
```

- **`schema`는 Iceberg 네임스페이스(= `defs/<dataset>/constants.py`의 `NAMESPACE`)와 반드시 일치**해야
  한다. 둘은 단일 출처로 함께 바뀐다.
- **`meta.dagster.asset_key`** 로 dbt source를 기존 Dagster 자산키에 매핑한다. 미지정 시 dagster-dbt
  기본값은 `[source_name, table]`(2-세그먼트)이라 단일 세그먼트 자산키(`patient` 등)와 어긋나 lineage가
  끊긴다. (근거: `dagster_dbt` `default_asset_key_fn` — `meta.dagster.asset_key` 우선)
- Dagster **서브클래싱 없이 dbt 선언만으로** 연결한다(프로젝트 컨벤션).

## 테스트 (필수)

> 파이프라인 전체 테스트 전략(계층·우선순위·무엇에 테스트를 다는지)은 [`../test.md`](../test.md)가 단일 출처.
> 여기서는 dbt 스키마 테스트의 dbt 측 규약만 다룬다.

- 핵심 모델에는 스키마 테스트를 단다: `unique`, `not_null`, `relationships`, `accepted_values`.
- 추가 검증은 설치된 패키지 활용:
  - [`dbt_utils`](https://github.com/dbt-labs/dbt-utils) `1.3.3`
  - [`dbt_expectations`](https://github.com/metaplane/dbt-expectations) `0.10.10`

```yaml
# models/eicu/_eicu__models.yml
models:
  - name: eicu__patient_summary
    columns:
      - name: patient_id
        tests: [not_null, unique]
      - name: lab_count
        tests:
          - dbt_expectations.expect_column_values_to_be_between:
              min_value: 0
```

## dbt-spark 타깃 (Phase 1 이행)

- **`profiles.yml`에 실재하는 타깃은 5종**이다 — `dev` · `prod` · `spark_session` · `spark_connect` · `spark_thrift`.
  **`trino`라는 이름의 타깃은 없다**(trino로 가는 것은 `dev`·`prod`이며 **엔진명이 아니라 환경명**으로 적혀 있다).
  기본값이 `spark_connect`라 **아무것도 지정하지 않으면 Spark Connect**로 간다.
- 이 중 dbt-spark 계열은 셋이다: **`spark_session`**(호스트 로컬 Spark — 상시 서비스 없이 방언 검증용) /
  **`spark_connect`**(클러스터 Spark Connect 서버 — 컴퓨트=K8s) / **`spark_thrift`**(대피로, 아래).
  앞의 둘은 차이가 `spark.remote` 하나뿐이다.
- ⚠️ **`spark_thrift`는 지금 쓸 수 없다** — 클라이언트 의존성 `pyhive`·`thrift`·`thrift_sasl`이
  **미설치**라 타깃만 바꾸면 접속 시점에 죽는다(`connections.py`가 ImportError를 삼켜 `hive=None`으로 둔다).
  **선언만 있는 대피로**로 읽는다([../architectures/spark.md](../architectures/spark.md)).
- **Iceberg 설정은 `server_side_parameters`에 둔다.** dbt-spark는 이 값들을 **세션 생성 시
  `builder.config()`로 적용**하므로 카탈로그·S3 설정이 정상 반영되고, `{{ env_var(...) }}`를 쓸 수 있어
  **비밀정보를 참조로 유지**할 수 있다(하드코딩 금지 원칙 충족).
- **S3 키는 `server_side_parameters`에도 적지 않는다** — AWS 표준 env(`AWS_ACCESS_KEY_ID`/`SECRET`)로
  S3FileIO의 기본 자격증명 체인이 집어간다.
- ⚠️ **`source.yml`에 `database:`를 쓰지 않는다.** dbt-spark는 relation에 database 설정을 금지해
  `Cannot set database in spark!`로 죽는다. 카탈로그는 타깃이 정한다(trino=프로파일 `database`,
  spark=`spark.sql.defaultCatalog`).

### `+file_format: iceberg`는 필수다 (2026-08-22 추가)

`dbt_project.yml`의 `mimic_iv.tables`에 **`+file_format: iceberg` 한 줄**을 추가했다.
dbt-spark의 기본 `file_format`은 iceberg가 아니며, **없으면 아래 셋이 동시에 무너진다.**

| 빠뜨렸을 때 | 결과 |
| --- | --- |
| **매 실행 DROP + CREATE** | Iceberg **스냅샷 히스토리 소멸**, 테이블 UUID 변경 |
| 같은 원인 | DROP과 CREATE 사이에 **테이블이 존재하지 않는 창**이 생긴다(`table.sql:19,27-30`) |
| **컬럼 `description` 244건이 통째로 무시** | `adapters.sql:365`가 `file_format in ['delta','hudi','iceberg']`일 때만 `ALTER TABLE`로 코멘트를 반영한다 |

**셋째가 가장 위험하다 — 에러도 경고도 나지 않는다.** `dbt run`은 성공하고, `dbt docs`도
생성되며, 다만 **컬럼 설명이 비어 있을 뿐**이다. 244건을 적어 넣고도 하나도 반영되지 않는 상태가
조용히 유지된다([philosophy.md](../philosophy.md) 원칙 7 — "통과"가 *검사했다*인지 확인한다).

- ⚠️ **244는 `description` 항목 수**이지 파일 줄 수가 아니다(`schema.yml`은 626줄).
- **dbt-trino는 이 config를 무시**하므로 **Trino 경로에는 무해**하다 — 값 대조를 깨지 않는다.

### dbt-spark 어댑터가 ANSI 모드를 강제로 끈다 (2026-08-22 발견)

`dbt/adapters/spark/connections.py:189-191`이 세션 생성 시 ANSI 관련 설정을 **프로파일 값보다
나중에 덮어쓴다.** 즉 `server_side_parameters`에 ANSI를 켜 두어도 **적용되지 않는다.**

**결과 — 실패해야 할 연산이 실패하지 않는다.**

| 상황 | Trino(ANSI) | Spark(ANSI off) |
| --- | --- | --- |
| 0으로 나누기 | 에러 | **`NULL`** |
| 정수 오버플로 | 에러 | **wrap-around** |
| 캐스트 실패 | 에러 | **`NULL`** |

**행 수는 같고 지표만 `NULL`이 된다.** 그래서 `not_null`이 없는 한 **카운트 기반 테스트로는
절대 안 잡힌다** — 두 엔진이 같은 행 수를 내놓으므로 대조표에서도 정상으로 보인다.

- **실제 노출 지점**: `urine_output_rate.sql:97,101,105`의 `/ wd.weight` — **분모 가드가 없다.**
  체중이 0이거나 `NULL`인 환자에서 Trino는 죽고 Spark는 조용히 `NULL`을 낸다.
- 📌 **대응은 어댑터 설정이 아니라 SQL 쪽이다.** 어댑터가 덮어쓰므로 설정으로는 못 막는다.
  **분모 가드(`nullif`)를 모델에 명시**하는 것이 유일하게 두 엔진에서 같은 값을 보장한다.

### ⚠️ `spark_connect`는 어댑터 계약이 아니라 내부 동작에 얹혀 있다

✅ 엔드투엔드로 **동작한다**. 그러나 dbt-spark가 공식 지원하는 method는
`SparkConnectionMethod` 기준 **thrift / http / odbc / session 4개뿐**이고 **connect는 없다.**
도는 이유는 `session.py`가 `builder.config()` → `getOrCreate()`를 타서 pyspark 빌더가
`spark.remote`를 보고 위임하는 **내부 동작** 때문이다.

- ⇒ **계약이 아니라 구현에 의존**하므로 minor 업그레이드가 **에러 없이** 깨뜨릴 수 있다.
  그래서 `pyproject.toml`이 상한을 **`dbt-spark<1.12`·`pyspark<3.6`** 으로 묶는다.
- **상한을 올리기 직전에 `scripts/spark_connect_smoke.py`를 통과시킨다**([../test.md](../test.md) §5-1).
  단 그 스모크가 무엇을 보지 **못하는지**는 test.md의 **B9**를 함께 읽는다.

### 방언 차이는 크로스 어댑터 매크로로 흡수한다 (규칙)

- 어댑터마다 문법이 갈리는 날짜·시간 연산은 **엔진 리터럴을 직접 쓰지 않는다.**
  대신 **의미론이 같은지 확인한 뒤**(아래 §의미론) 내장 매크로 또는 프로젝트 dispatch 매크로를 쓴다.

  ```sql
  -- Good — 엔진 리터럴을 모델에 박지 않는다
  {{ elapsed('hour', 'a', 'b') }}

  -- Bad — Trino 전용 리터럴 (Spark는 따옴표 없는 INTERVAL 1 HOUR)
  ih.endtime - INTERVAL '1' HOUR
  ```

  **"리터럴을 안 쓴다"와 "같은 값이 나온다"는 다른 축이다.** 리터럴 제거는 **컴파일**을
  양쪽에서 통과시킬 뿐이고, 값 정합은 매크로마다 따로 확인해야 한다.

- 이 매크로는 **dbt-core 전역 프로젝트**에 있어 `dbt_utils` 설치와 무관하게 `dbt.` 접두어로 호출된다
  (`dbt_utils.dateadd`는 구 경로 — 신규 코드는 `dbt.`를 쓴다).

#### 내장 매크로를 쓰기 전에 **의미론이 같은지** 확인한다

**"돌아가는 것"이 아니라 "같은 값이 나오는 것"이 이행의 목표다.** 내장 크로스 어댑터 매크로는
어댑터별 구현이 **다른 의미**일 수 있고, 그러면 조용히 결과가 갈린다.

- 실측 반례(2026-08-19, 설치본 소스 확인) — 🔴 **`dbt.datediff`는 쓰지 않는다**:

  | 구현 | `'hour'` 의미 |
  | --- | --- |
  | Trino 네이티브 `date_diff('hour', a, b)` | **경계 교차 횟수**(Joda field difference) |
  | `trino__datediff(..., 'hour')` | `day*24 + hour(b) - hour(a)` → 네이티브와 동일 |
  | `spark__datediff(..., 'hour')` | `ceil((unix(b)-unix(a))/3600)` → **경과시간 올림** |

  `11:00 → 12:59`가 경계교차는 **1**, ceil은 **2**다. `ventilation`의 `>= 14`,
  `urine_output_rate`의 `<= 5`처럼 **임계값 비교**에 쓰이므로 값이 갈리면 silver 피처가 달라진다.

- **오분류 교정(2026-08-22) — `dbt.dateadd`도 의미론이 같지 않다**:

  이 문서는 오랫동안 `dbt.dateadd`를 *"어댑터 간 의미론이 같다"* 쪽으로 분류하고 **권장**해 왔다.
  **틀렸다.** 설치본 소스 확인 결과 두 어댑터의 구현이 다음과 같이 갈린다.

  | 구현 | `dateadd('hour', n, ts)` 전개 |
  | --- | --- |
  | `trino__dateadd` | `date_add('hour', n, ts)` — **네이티브 타임스탬프 연산** |
  | `spark__dateadd` | `to_timestamp(to_unix_timestamp(ts) + cast(n*3600 as int))` — **epoch 초 왕복** |

  Spark 구현이 epoch **초**를 경유하므로 결과가 두 갈래로 갈린다.

  | 갈리는 축 | 내용 |
  | --- | --- |
  | **초 이하 절삭** | `to_unix_timestamp`가 초 단위라 **밀리·마이크로초가 버려진다**. Trino는 보존한다 |
  | **타임존 의존** | `to_unix_timestamp`/`to_timestamp`가 **`spark.sql.session.timeZone`을 탄다**. 이 값은 **현재 미설정**이라 JVM 기본값에 좌우된다 — 즉 **서버가 어디서 뜨느냐에 따라 값이 바뀔 수 있다** |

  **`dbt.datediff`와 같은 축의 함정인데 이쪽만 통과했다.** 왜 놓쳤는지 한 줄로 남긴다 —
  **`datediff`는 "차이를 어떻게 세나"라는 질문이 이름에 드러나 의심을 받았지만, `dateadd`는
  "더하기"라 의미가 자명해 보였다.** 실제로 갈린 지점은 덧셈이 아니라 **덧셈을 하려고 거쳐 간
  중간 표현(epoch 초)** 이었다. ⇒ **의심의 트리거를 "연산이 애매한가"가 아니라
  "구현이 중간 표현을 경유하는가"로 옮긴다.** 후자는 소스를 열어야만 보인다.

  📌 🔴 **신규 코드에서 `dbt.dateadd`를 쓰지 않는다.** 흡수 방향은 `elapsed`와 같이
  `macros/cross_engine.sql`의 dispatch 매크로다.

- **판단 기준**

  | 상황 | 쓸 것 |
  | --- | --- |
  | 어댑터 간 의미론이 **확인상** 같다 | **dbt 내장** — 단 **설치본 소스를 열어 확인한 것만** |
  | 의미론이 갈린다 (`dbt.datediff`·**`dbt.dateadd`**) | **프로젝트 dispatch 매크로** — Trino 네이티브를 **정본**으로 두고 Spark에 같은 수식을 재현 |
  | 내장 매크로가 아예 없다(배열 펼치기 등) | **프로젝트 dispatch 매크로** |

  🔴 **"내장이니 안전하다"는 근거가 아니다.** dbt 내장 크로스 데이터베이스 매크로는 **문법의
  이식성**을 보장하지 **값의 동일성**을 보장하지 않는다. 새 내장 매크로를 도입할 때는
  **`<adapter>__<macro>` 구현 두 개를 나란히 놓고 읽는다** — 이 저장소는 같은 자리에서 **두 번** 틀렸다.

- 프로젝트 dispatch 매크로는 **`macros/cross_engine.sql`** 한 곳에 모은다. `adapter.dispatch`를 쓰고,
  **`default__` 구현에 `raise_compiler_error`를 둬** 새 어댑터에서 **조용히 틀리지 않고 즉시 실패**하게 한다.

  | 매크로 | Trino | Spark |
  | --- | --- | --- |
  | `elapsed(part, from, to)` | `date_diff(...)` 네이티브 | 경계교차 수식 재현(`hour`·`minute`만) |
  | `unnest_array(arr, alias, col)` | `cross join unnest(...)` | `lateral view explode(...)` |

- ⚠️ **`dbt compile` 통과를 이행 완료로 읽지 않는다.** compile은 구문이 파서에 맞았다는 뜻이지
  두 엔진이 같은 값을 낸다는 뜻이 아니다. 값 정합은 실행으로만 확인된다.

  📌 이행 현황과 그 근거 수치는 **저장소 밖**에 있다 — `$OBSIDIAN_VAULT/status/observations.md`
  §dbt 방언 이행 현황. 여기 두면 **아무도 손대지 않아도 저절로 낡는다**.

## Trino / Iceberg 주의사항

> Trino는 재설계로 **제거 예정**이다([../architectures/trino.md](../architectures/trino.md)). 아래는 현행 기준.

- `profiles.yml`의 `database`(= `iceberg`)는 Trino 카탈로그명과 일치해야 한다.
  ([architectures/overview.md](../architectures/overview.md) 참고)
- 기본 target은 `dev`(schema `dev`). prod 배포 시 `--target prod`.
- Iceberg 테이블 속성(파티셔닝 등)이 필요하면 `+table_properties` / `+partitioned_by`를
  config로 선언한다 (dbt-trino 문서 참고).

## 실행

```bash
dbt deps              # packages.yml 설치
dbt run               # 모델 빌드 (기본 target: dev)
dbt test              # 테스트
dbt run --target prod # prod 빌드
```

> Dagster를 통해 실행할 때는 `dbt_all_job` / 스케줄로 트리거된다 ([dagster.md](dagster.md)).

## 참고

- dbt-trino: https://github.com/starburstdata/dbt-trino
- dbt 모델 설정: https://docs.getdbt.com/docs/build/models
- dbt 테스트: https://docs.getdbt.com/docs/build/data-tests
- sqlfluff: https://docs.sqlfluff.com/
- dbt_utils: https://github.com/dbt-labs/dbt-utils
- dbt_expectations: https://github.com/metaplane/dbt-expectations
