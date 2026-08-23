# Dagster 코딩 규칙

버전: `dagster==1.12.12` 계열 / `dagster-dg-cli` 기반 프로젝트 구조.

## 핵심 원칙: 에셋은 함수 + 데코레이터로 정의한다

> **클래스 기반 정의나 커스터마이징을 위한 불필요한 서브클래싱을 지양한다.**

- 에셋은 **함수 + 데코레이터**로 정의한다: `@asset`, `@multi_asset`, `@dbt_assets`.
- 커스터마이징이 필요하면 **선언적 설정**(데코레이터 인자, 메타데이터, dbt config)을 우선한다.
- 이유: 가독성 · 테스트 용이성 · 낮은 결합도. 함수형 정의가 Dagster 권장 패턴이며 보일러플레이트가 적다.

```python
# 권장: 함수 + 데코레이터
from dagster import asset


@asset(group_name="bronze")
def raw_events() -> None:
    # 원천 이벤트를 적재한다.
    ...
```

```python
# 지양: 커스터마이징을 위한 서브클래싱
class MyDbtTranslator(DagsterDbtTranslator):   # ← 가급적 사용하지 않는다
    def get_group_name(self, ...):
        ...
```

→ group 같은 설정은 서브클래스(`DagsterDbtTranslator`) 대신 **dbt config**로 선언한다:
`dbt_project.yml`의 `+meta.dagster.group`(또는 모델 `meta.dagster.group`). (아래 예시 참고)

### 리소스도 같다 — 공식 통합이 있으면 커스텀 리소스를 만들지 않는다

`dg.ConfigurableResource`를 **직접 상속**하기 전에 `dagster-<technology>` 통합에 이미 있는지 먼저 본다.
공식 리소스는 Dagster가 유지보수하고(`_is_dagster_maintained`), UI 표시·설정 스키마·수명주기 훅
(`setup_for_execution`)이 표준을 따른다.

- **사례(2026-08-19)**: Iceberg 유지보수용 Spark 접속을 커스텀 `SparkConnectResource`로 만들었다가
  **`dagster-pyspark`의 `LazyPySparkResource`로 교체**했다. Spark Connect 접속은 커스텀 코드가 아니라
  **`spark_config={"spark.remote": "sc://..."}`** 한 줄이면 된다 — 내부 `builder.config(k, v)`가
  이 키를 받아 `pyspark.sql.connect` 세션을 만든다(실측). 커스텀 모듈(`common/spark.py`)은 삭제했다.
- **`Lazy~` 변형이 있으면 그쪽을 본다** — `PySparkResource`는 리소스 초기화에서 세션을 **즉시** 만들어,
  그 리소스를 쓰지 않는 run까지 백엔드 가용성에 묶는다. `LazyPySparkResource`는 `spark_session`
  **접근 시점**에 만든다.
- 커스텀 리소스는 **통합에 없거나 의미가 다를 때만** 만든다(예: `TrinoResource` — 공식 Trino 리소스가
  없어 만든 경량 dbapi 래퍼).
  🔴 **`TrinoResource`는 더 이상 유지보수 프로시저 실행자가 아니다** — Iceberg 유지보수는 **Spark
  프로시저로 이관**됐고(`defs/maintenance.py`가 `dagster_pyspark`의 `LazyPySparkResource`를 쓴다),
  이 접속은 `defs/resources.py`에 **dbt-spark 이행 중 방언 값 대조용으로만** 남는다
  ([architectures/trino.md](../architectures/trino.md) · [operations.md](../operations.md) §Iceberg 유지보수).

## 자산 모듈에서는 `from __future__ import annotations` 금지

> Dagster는 `@asset`/op의 `context` 파라미터를 **클래스 identity**로 검사한다.
> future annotations를 켜면 어노테이션이 **문자열**이 되어 검사가 실패한다.

- 증상: `DagsterInvalidDefinitionError: Cannot annotate context parameter …`
- 규칙: **자산/op 정의가 있는 모듈**에서는 `from __future__ import annotations`를 쓰지 않는다.
  `context`는 임포트한 실제 클래스로 표기한다: `context: AssetExecutionContext` (또는 생략).
- 공통 helper 등 **자산이 아닌** 모듈은 future annotations를 써도 무방하다.
  같은 맥락으로 `TC` 룰도 Dagster introspection과 충돌해 켜지 않는다([python.md](python.md)).

## 각 에셋은 명시적으로 분리 정의한다

> **팩토리로 동적 생성하지 않고, 각 에셋을 `@asset` 함수로 명시적으로 정의한다.**

- 이유: 탐색성(에셋 이름 grep/IDE 점프) · per-asset 커스터마이징 용이 · 자기문서화.
- 공통 처리 로직은 일반 함수로 분리해 재사용하되(DRY), 에셋 정의 자체는 각각 명시한다.
- 에셋은 **데이터셋별 서브프로젝트** `dagster_project/defs/<dataset>/assets.py`로 분리 관리한다.

```python
# 권장: 명시적 에셋 + 공통 로직 재사용
from dagster_project.common.helper import load_csv_gz_to_iceberg


@asset(group_name=GROUP_NAME, kinds={"python", "iceberg"})
def mimiciv_hosp_patients(context) -> MaterializeResult:
    return load_csv_gz_to_iceberg(
        context, identifier=f"{NAMESPACE}.patients",
        source_glob=f"{SOURCE_BASE}/hosp/patients.csv.gz",
    )
```

```python
# 지양: 팩토리 + 목록 루프로 에셋 동적 생성 (탐색성 저하)
bronze_assets = [build_csv_to_iceberg_asset(...) for ... in TABLES]   # ← 사용하지 않는다
```

## 머티리얼라이즈 메타데이터를 남긴다

> **적재/변환 에셋은 관측 가능한 메타데이터(행 수·미리보기 등)를 남긴다.**
> Dagster UI에서 결과를 눈으로 확인하고 회귀를 조기에 잡기 위해서다.

이 레포는 적재 경로가 둘이라 메타데이터를 붙이는 방법도 둘이다([../architectures/overview.md](../architectures/overview.md#두-가지-적재-경로)).

- **일반 경로**(`pa.Table` 반환 → IO 매니저가 write): 반환 타입을 유지한 채
  `context.add_output_metadata(...)`로 메타데이터를 부착한다.

  ```python
  @dg.asset(group_name=GROUP_NAME, io_manager_key=IO_MANAGER_KEY, kinds={"python", "iceberg", "bronze"})
  def patient(context: dg.AssetExecutionContext, s3: S3Resource) -> pa.Table:
      """EICU patient 원본을 bronze Iceberg 테이블로 적재한다."""
      table = read_csv_gz_table(s3, f"{SOURCE_BASE}/patient.csv.gz")
      context.add_output_metadata({
          "row_count": dg.MetadataValue.int(table.num_rows),
          "columns": dg.MetadataValue.int(table.num_columns),
      })
      return table
  ```

- **대용량 경로**(IO 매니저 미사용): `MaterializeResult(metadata=...)`로 반환한다.
  공통 헬퍼 `load_heavy_csv_gz_to_iceberg`가 이미 `table`·`source_uri`·`rows`·`mode`를 담아 반환한다.

**권장 키**: `row_count`(int) 위주. 필요 시 `preview`(`MetadataValue.md`, `head` 마크다운),
`source_uri`, `mode` 등. 자주 쓰는 키는 통일해 대시보드에서 비교 가능하게 한다.

## 프로젝트 구조

```text
src/dagster_project/
├── definitions.py          # load_defs(dagster_project.defs) → 단일 Definitions (모듈 스코프 1개)
├── common/                 # 공통 재사용 라이브러리 (데이터셋 무관, defs/ 밖)
│   ├── constants.py        # 공통 상수/기본값 (S3 파라미터 포함)
│   ├── helper.py           # read_csv_gz_table(일반) · load_heavy_csv_gz_to_iceberg(대용량)
│   ├── dbt.py              # 공유 DbtProject · build_dbt_resource (단일 dbt 프로젝트)
│   └── trino.py            # TrinoResource (dbt-spark 이행 중 방언 값 대조용 — 유지보수는 Spark로 이관)
└── defs/                   # load_defs가 재귀 자동발견하는 정의 루트
    ├── resources.py        # @dg.definitions: s3 · dbt · trino · io_manager_* · 테이블 바인딩
    ├── automation.py       # dbt_all_job · dbt_all_schedule (모듈 스코프 객체)
    ├── maintenance.py      # iceberg_maintenance_job: 컴팩션→스냅샷 만료→orphan 정리 3단계(주간 스케줄, 순서 강제)
    ├── mimic_iv/           # 데이터셋 서브프로젝트 (정의만)
    │   ├── constants.py    # NAMESPACE · GROUP_NAME · SOURCE_BASE
    │   ├── assets.py       # 명시적 @asset (bronze 적재)
    │   └── dbt_assets.py   # @dbt_assets(select="fqn:mimic_iv", project=dbt_project)
    └── eicu/
        ├── constants.py
        ├── assets.py
        └── dbt_assets.py   # @dbt_assets(select="fqn:eicu", project=dbt_project)
```

- **정의는 모두 `defs/` 하위**에 두고 `load_defs`가 재귀 자동발견해 단일 `Definitions`로 합친다.
  - `@asset`·`@dbt_assets`·잡·스케줄 등 **모듈 스코프 정의 객체**는 자동 수집된다.
  - **리소스는 `@dg.definitions`** 로 감싼 함수가 `Definitions(resources=...)`를 반환하면 수집·merge된다.
  - ⚠️ **`@dg.definitions`는 `@asset`이 있는 모듈에 같이 두지 않는다.** 한 모듈에 `@dg.definitions`가 있으면
    **그 함수의 반환값이 모듈의 정의 전체를 대체**해, 같은 파일의 모듈 스코프 `@asset`이 **조용히 수집되지 않는다**
    (에러도 경고도 없다 — 2026-08-18 실측: `defs/poc/assets.py`가 이 형태라 `poc_spark_ingest`가 UI에 뜬 적이 없었다).
    리소스 등록은 `defs/resources.py`(공유) 또는 `defs/<dataset>/resources.py`(서브프로젝트 전용)처럼
    **자산이 없는 모듈**에 둔다.
    - 자동발견 누락은 조용해서 놓치기 쉽다 → 정의 추가 후 **`dg check defs`** 또는
      `load_defs(...).resolve_asset_graph().get_all_asset_keys()`로 **자산 수를 확인**한다.
- **공통 로직은 `common/`**(defs 밖)에 두고 데이터셋 모듈이 import해 재사용한다(DRY).
- 코드 로케이션 모듈(`definitions.py`)은 **모듈 스코프에 `Definitions` 1개(`defs`)** 만 둔다(autodiscovery 제약).

```python
# definitions.py — defs/ 를 자동발견해 단일 Definitions로 합친다
from dagster import load_defs

import dagster_project.defs

defs = load_defs(dagster_project.defs)
```

```python
# defs/resources.py — 리소스는 @dg.definitions로 제공
@dg.definitions
def resources() -> dg.Definitions:
    return dg.Definitions(resources={"s3": ..., "dbt": ..., "io_manager_eicu": ...})
```

## dbt 통합 (pythonic `@dbt_assets`)

단일 dbt 프로젝트(`dbt_pipelines`)를 데이터셋 서브프로젝트가 **`@dbt_assets`로 분할 소유**한다.
공유 `DbtProject`·리소스는 `common/dbt.py`에 두고, 각 `defs/<dataset>/dbt_assets.py`가 `select`로
자기 모델만 소유한다(컴포넌트/`defs.yaml` 미사용).

```python
# defs/eicu/dbt_assets.py
from dagster_dbt import DbtCliResource, dbt_assets

from dagster_project.common.dbt import dbt_project


@dbt_assets(manifest=dbt_project.manifest_path, project=dbt_project, select="fqn:eicu")
def eicu_dbt_models(context, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()
```

- **셀렉터는 `select="fqn:<dataset>"`**(+ `project=dbt_project`)를 쓴다. `path:models/<dataset>`는
  정의 로드 시 cwd 기준 파일시스템 글롭이라 모델이 수집되지 않는 잠복 버그가 있다. 상세 [`dbt.md`](dbt.md).
- **group은 서브클래싱 없이 dbt config로 선언**: `dbt_project.yml`의 `+meta.dagster.group`
  (기본 `DagsterDbtTranslator`가 읽는다).
- **schema는 접두어 없이** `generate_schema_name` 매크로로 그대로 사용(데이터셋 = Iceberg 네임스페이스).
- **manifest**: dev는 `DbtProject.prepare_if_dev()`가 생성, 비-dev(`dg check`·프로덕션)는 이미지 빌드 시
  `dbt parse`로 사전생성. 상세 [`dbt.md`](dbt.md) · [`../architectures/overview.md`](../architectures/overview.md).

## 새 데이터셋 서브프로젝트 추가 체크리스트

Iceberg 네임스페이스는 **데이터셋 서브프로젝트 단위**로 만든다(예: `eicu`, `mimic_iv`).
새 데이터셋을 추가할 때 아래를 순서대로 채워 자산·리소스·lineage 누락을 막는다.
(경로는 `src/dagster_project/` 기준 — 실제 wiring은 `defs/` 하위를 `load_defs`가 수집한다.)

1. **`defs/<dataset>/constants.py`** — `NAMESPACE`·`GROUP_NAME`·`SOURCE_BASE` 정의.
   네임스페이스에 `bronze_` 같은 레이어 접두어를 넣지 않는다(`NAMESPACE = "<dataset>"`).
2. **`defs/<dataset>/assets.py`** — 테이블별 **명시적 `@asset`**(팩토리 금지). 일반=IO 매니저 /
   대용량=`load_heavy_csv_gz_to_iceberg`. 메타데이터를 남긴다(위 규약).
3. **`defs/<dataset>/dbt_assets.py`** — `@dbt_assets(select="fqn:<dataset>", project=dbt_project)`로 dbt 모델 소유.
4. **IO 매니저 리소스 등록** — `defs/resources.py`에 `io_manager_<dataset>`(namespace=`<dataset>`)를
   추가한다. 대용량 테이블이 있으면 해당 `IcebergTableResource`도 함께 등록한다.
   Iceberg 카탈로그 설정(`IcebergCatalogConfig`)은 별도 빌더 없이 **각 리소스에 인라인**한다
   (한 파일에서 전체 설정을 파악 — 적은 파일로 파악).
5. **dbt source 매핑** — `models/<dataset>/source.yml`에 Dagster 적재 테이블을 source로 선언하고
   `meta.dagster.asset_key`로 자산키와 매핑한다(lineage 연결). 상세 [dbt.md](dbt.md).
6. **dbt group 선언** — `dbt_project.yml`의 `+meta.dagster.group`(또는 모델 `meta.dagster.group`)로
   그룹을 지정한다(서브클래싱 대신 config).
7. **문서 동기화** — 데이터셋이 늘면 [../architectures/overview.md](../architectures/overview.md)의 서브프로젝트 표를 갱신한다.

> `load_defs(dagster_project.defs)`가 `defs/` 하위 모듈 스코프 정의를 자동 수집하므로,
> 새 서브프로젝트는 `defs/` 아래 두기만 하면 별도 등록 없이 합쳐진다. **리소스 키**(4번)만
> 자산의 `io_manager_key`와 일치시키면 된다.

## 잡 / 스케줄

- 잡은 `define_asset_job` + `AssetSelection`으로 선언적으로 구성한다.
- 그룹 단위 선택을 활용한다.

```python
dbt_all_job = define_asset_job(
    "dbt_all_job",
    selection=AssetSelection.groups("dbt_ingest"),
)

dbt_all_schedule = ScheduleDefinition(
    name="dbt_all_schedule",
    job=dbt_all_job,
    cron_schedule="0 * * * *",         # 매시 정각
    execution_timezone="Asia/Seoul",   # cron을 KST로 해석 (타임존 규칙)
)
```

- **스케줄은 `execution_timezone`을 명시**한다(미지정 시 daemon 시스템 TZ 의존). 상세 [timezone.md](timezone.md).

## 그룹 / 네이밍

- 에셋 그룹명은 `snake_case`. 적재 자산은 **데이터셋 단위로 그룹화**한다(예: `eicu`, `mimiciv`).
- **메달리온 레이어는 그룹·네임스페이스 접두어가 아니라 `kinds`로 표기**한다
  (예: `kinds={"python", "iceberg", "bronze"}`). dbt 쪽에서는 동일 레이어를 tag로 관리한다.
  → 네임스페이스/스키마에는 `bronze_` 같은 레이어 접두어를 넣지 않는다(`NAMESPACE = "eicu"`).
- 잡·스케줄 이름은 역할이 드러나게 (`dbt_all_job`, `dbt_all_schedule`).

## 실행

```bash
dg dev        # 로컬 ad-hoc 개발 (webserver+daemon+code 일체형, http://localhost:3000)
```

> 컨테이너 스택(`compose.yml`)은 `dg dev` 대신 **`dagster-webserver` + `dagster-daemon`** 으로
> 분리해 기동한다(운영 토폴로지). 상세 [`../architectures/overview.md`](../architectures/overview.md#dagster-프로세스-분리-webserver--daemon).

## 참고

- Dagster 에셋: https://docs.dagster.io/guides/build/assets
- 컴포넌트(`dg`): https://docs.dagster.io/guides/build/components
- dagster-dbt: https://docs.dagster.io/integrations/libraries/dbt
- `dagster.yaml`: https://docs.dagster.io/deployment/oss/dagster-yaml
