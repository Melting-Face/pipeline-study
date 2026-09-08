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

- **사례**: Iceberg 유지보수용 Spark 접속을 커스텀 `SparkConnectResource`로 만들었다가
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

이 레포는 적재 경로가 넷이라 메타데이터를 붙이는 방법도 갈린다([../architectures/overview.md](../architectures/overview.md#네-가지-적재-경로)).

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
    (에러도 경고도 없다 — 실측: `defs/poc/assets.py`가 이 형태라 `poc_spark_ingest`가 UI에 뜬 적이 없었다).
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
   원천이 S3가 아니라 외부 API면 `SOURCE_BASE` 대신 엔드포인트·수집 파라미터·HTTP 기본값을 둔다.
2. **`defs/<dataset>/assets.py`** — 테이블별 **명시적 `@asset`**(팩토리 금지). 일반=IO 매니저 /
   대용량=`load_heavy_csv_gz_to_iceberg`. 메타데이터를 남긴다(위 규약).
   원천이 **일자별로 나뉘면 `partitions_def`를 건다**(아래 §파티션 — 시작일 리터럴·타임존·멱등).
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

**bronze에서 멈추는 데이터셋은 3·5·6을 건너뛴다** — dbt 모델을 두지 않으면 소유할 것도
매핑할 것도 없다. 다만 **건너뛴 것은 선언한다**(빠뜨린 것과 구분되도록 데이터셋 문서에 적는다).
IO 매니저를 쓰지 않는 적재 경로(대용량 청크·API append·파티션 교체)는 4번에서
`io_manager_<dataset>` 대신 대상 테이블용 `IcebergTableResource`만 등록한다.

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
  단 **파티션 잡은 이 인자를 받지 않는다** — 아래 §파티션.

## 파티션

원천이 **하루치씩 나뉘는 것**(일자별 API 조회 등)이면 자산에 `partitions_def`를 건다.
파티션은 백필·재실행·부분 실패 복구의 단위가 되고, UI에서 어느 날짜가 비었는지가 보인다.

```python
DAILY_PARTITIONS = dg.DailyPartitionsDefinition(
    start_date=PARTITION_START_DATE,   # 리터럴 상수 — 아래 ①
    timezone="UTC",                    # 스케줄 타임존도 여기서 정해진다 — 아래 ②
)

@dg.asset(group_name=GROUP_NAME, partitions_def=DAILY_PARTITIONS, kinds={...})
def fx_rates_daily(context: dg.AssetExecutionContext, ...) -> dg.MaterializeResult:
    rate_date = context.partition_key      # "YYYY-MM-DD"
```

**① 시작일은 리터럴로 고정한다.** `date.today() - timedelta(days=30)` 같은 계산식을 쓰면
정의를 로드할 때마다 파티션 집합이 하루씩 밀려, **어제 머티리얼라이즈한 파티션이 집합에서
사라진다.** 이력은 남지만 자산 그래프가 그것을 더는 자기 파티션으로 보지 않는다.

**② 파티션 잡의 스케줄 타임존은 스케줄이 아니라 파티션 정의가 정한다.**
`build_schedule_from_partitioned_job`에 `execution_timezone`(또는 `cron_schedule`)을 주면
시간 파티션 잡에서는 `check.failed`로 **죽는다**. 발화 시각은 `hour_of_day`·`minute_of_hour`로
민다. 위 §잡/스케줄의 "`execution_timezone` 명시" 규약은 **파티션 정의의 `timezone=`이
대신 만족**시킨다(목적인 "daemon 시스템 TZ 의존 금지"는 그대로 지켜진다).

- **파티션 키가 외부 시스템에 그대로 전달되면 타임존은 그 시스템에 맞춘다.**
  키를 API의 날짜 파라미터로 보내는데 파티션을 KST로 잡으면 키의 의미(KST 하루)와
  값의 의미(원천의 달력일)가 어긋난다. 저장은 UTC라는 [timezone.md](timezone.md) 규칙과도 같은 방향이다.

**③ 파티션 자산에 `append`를 쓰면 재실행이 행을 늘린다.** 파티션은 재실행·백필이 전제라
멱등해야 한다. 그렇다고 `replace`(=`drop_table` 후 재생성)를 쓰면 스냅샷 계보가 끊긴다.
⇒ 파티션 범위만 지우고 다시 넣는 `replace_partition_in_iceberg`(`common/helper.py`)를 쓴다.
내부적으로 `Table.overwrite(df, overwrite_filter=...)`이며 `drop_table`이 없어 계보가 이어진다.

🔴 **다만 Flink 스트리밍 소스로 읽는 테이블에는 쓰지 않는다** — 계보는 이어지지만
delete/overwrite 스냅샷이 생기고 `IncrementalAppendScan`은 그것을 다루지 못한다.
**계보 보존과 append-only는 다른 축**이다.

**④ 요청한 파티션 키와 원천이 돌려준 값은 다를 수 있다.** 휴장일·결측일에 직전 값을
조용히 돌려주는 원천이 있고, 그때 **행 수도 값도 정상이라 검산을 통과한다.**
원천이 날짜를 에코하면 그것을 별도 컬럼으로 함께 저장하고 일치 여부를 메타데이터에 남긴다
(판정하지 않고 **관측**만 — [data-quality.md](data-quality.md)). 에코 필드가 없으면 그 축은
`미확인`으로 명시하고, 있는 것처럼 컬럼을 만들지 않는다.

**확인 방법** — 같은 파티션을 **두 번** 머티리얼라이즈해 행 수가 그대로인지 본다.
"돌았다"는 "맞다"가 아니다([philosophy.md](../philosophy.md) 원칙 7).

## 그룹 / 네이밍

- 에셋 그룹명은 `snake_case`. 적재 자산은 **데이터셋 단위로 그룹화**한다(예: `eicu`, `mimiciv`).
- **메달리온 레이어는 그룹·네임스페이스 접두어가 아니라 `kinds`로 표기**한다
  (예: `kinds={"python", "iceberg", "bronze"}`). dbt 쪽에서는 동일 레이어를 tag로 관리한다.
  → 네임스페이스/스키마에는 `bronze_` 같은 레이어 접두어를 넣지 않는다(`NAMESPACE = "eicu"`).
- 잡·스케줄 이름은 역할이 드러나게 (`dbt_all_job`, `dbt_all_schedule`).

## 실행

```bash
./scripts/k8s-dagster.sh                    # 정본 — 이미지·수렴 (http://dagster.localtest.me:8080)
dg dev                                      # 개발 루프 대안 (일체형, http://localhost:3000)
```

> `dg dev`는 webserver·daemon·code server가 **한 프로세스**라 개발 중 재로드가 빠르지만,
> 메타 DB(CNPG)와 S3에 **port-forward가 전제**다. 정본 토폴로지는 아래 §K8s in-cluster 배포.

## K8s in-cluster 배포

Dagster는 **kind 클러스터 안**에서 돈다(구 "호스트 유지" 규약 폐기 — [k8s.md](k8s.md) §8).
선언은 `k8s/dagster/` 3파일이고 **적용은 `terraform/lakehouse-platform/`**(`manifests.tf`)이다.
`scripts/k8s-dagster.sh`가 맡는 것은 **이미지 빌드·push와 ConfigMap, 그리고 수렴 대기**다.
🔴 이 3파일을 `kubectl apply`로 다시 넣지 않는다 — 서버사이드 apply의 필드 소유권이 `kubectl`로
넘어가면 Terraform이 drift를 감지하고도 덮지 못한다.

### 토폴로지

| 파드 | 역할 | SA 토큰 | probe |
| --- | --- | --- | --- |
| `dagster-webserver` | UI·GraphQL (Ingress `dagster.localtest.me`) | **미마운트** | startup/readiness/liveness — `httpGet /server_info` |
| `dagster-daemon` | 스케줄·센서·런큐 + **run 실행** | 마운트(Spark 제출용) | startup/liveness — `dagster-daemon liveness-check` |

- run launcher는 **`DefaultRunLauncher`** — run은 daemon 파드 안의 서브프로세스로 돈다.
  그래서 daemon의 `limits.memory`가 곧 run의 상한이고, `max_concurrent_runs`와 **강결합**이다.
- 코드 서버는 **임베디드**(전용 gRPC 파드 없음). 대가로 UI의 "Terminate run"이 크로스 파드로 닿지 않는다 —
  compose(별개 컨테이너)에도 있던 결함이라 in-cluster의 신규 회귀는 아니다.
- 둘 다 `strategy: Recreate`다. 🔴 **daemon이 2개 겹치면 하트비트 중복·스케줄 이중 발화**가 나므로
  RollingUpdate의 교체 창을 허용하지 않는다.
- `run_monitoring`은 켜지 않는다 — `DefaultRunLauncher`가 `supports_check_run_worker_health`를
  지원하지 않는다. **부재가 결정이고**, 그 대가로 daemon 재시작 시 진행 중 run이 고아가 된다.

### env 매핑 — `POSTGRES_*`와 `ICEBERG_CATALOG_*`는 다른 DB다

in-cluster에서 이 둘이 **처음으로 갈린다**. 앞은 메타 DB(`dagster`), 뒤는 Iceberg 카탈로그(`iceberg`)이고
**같은 CNPG 서버의 다른 DB·다른 롤·다른 시크릿**이다.

`common/constants.py`가 `ICEBERG_CATALOG_USER` 미지정 시 `POSTGRES_USER`로 **폴백**하므로,
빠뜨리면 `dagster` 계정으로 `iceberg` DB에 붙는다 — **접속은 성공하고 테이블 접근에서 거부**된다.
부분 성공이라 오진하기 쉽다. ⇒ in-cluster에서 `ICEBERG_CATALOG_*`는 선택이 아니라 **필수**다.

값의 정본은 `k8s/dagster/dagster-deploy.yaml`의 ConfigMap이고, 호스트 실행분은 `.env`다.
in-cluster는 port-forward 주소 대신 서비스 DNS를 쓴다 —
`catalog-postgres-rw:5432` · `seaweedfs:8333` · `sc://spark-connect:15002`(평문 gRPC).

### 이미지

- 하나의 이미지를 compose와 K8s가 공유한다(`dagster/dockerfile.d/`). 태그는 **구체 버전 고정**이고
  올릴 때는 매니페스트의 `image:`를 **같은 커밋에서** 올린다(`k8s-dagster.sh`가 적용 전에 대조한다).
- 베이스는 **Python 3.12**다. `pyspark[connect]`가 `numpy<2`를 요구하는데 numpy 1.26.x에는
  cp313 휠이 없어 3.13에서는 소스 빌드로 떨어지고 `-slim`에 컴파일러가 없어 빌드가 실패한다.
- `pip install -e .`이지 `-e ".[dev]"`가 아니다 — 상세는 [docker.md](docker.md) §2.
- `.dockerignore` 패턴은 **빌드 컨텍스트 루트 기준**이라 `src/` 접두어가 필요하다.
  🔴 빠뜨리면 조용히 통과한다 — 실제로 `src/.venv`가 통째로 이미지에 들어갔다.
  효과 확인은 **이미지 크기**로 한다(`podman images`).

### 관측

- step 로그는 **`S3ComputeLogManager`** 로 SeaweedFS `dagster-logs` 버킷에 올린다.
  기본값(Local)이면 webserver와 daemon이 디스크를 공유하지 않아 **UI에서 영영 안 보인다**.
- 메트릭 엔드포인트는 두지 않는다(Dagster OSS에 `/metrics`가 없고 수집기도 없다).
- `telemetry`는 껐다 — DUA 환경에서 무엇이 나가는지 확인하지 않은 외부 발신은 통제로 볼 수 없다.

## 참고

- Dagster 에셋: https://docs.dagster.io/guides/build/assets
- 컴포넌트(`dg`): https://docs.dagster.io/guides/build/components
- dagster-dbt: https://docs.dagster.io/integrations/libraries/dbt
- `dagster.yaml`: https://docs.dagster.io/deployment/oss/dagster-yaml
