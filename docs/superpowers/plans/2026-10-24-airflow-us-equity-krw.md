# 원화 종가 파이프라인 Airflow 이관 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 환율·Polygon 시세 수집과 원화 종가 dbt 빌드를 Airflow DAG 3개로 옮기고, 검증 뒤 해당 Dagster 정의를
지운다.

**Architecture:** 수집 DAG 2개가 각자 bronze Iceberg 테이블 1개를 파티션 교체로 적재하고 Asset을 갱신한다.
dbt DAG는 두 Asset에 걸린 Asset 스케줄로 돌며 Cosmos로 모델 3개를 빌드한 뒤 최신 거래일을 Asset event에 남긴다.
수집·적재 로직은 Airflow를 import하지 않는 순수 패키지 `lakehouse`로 두어 호스트 venv에서 단위 테스트한다.

**Tech Stack:** Airflow 3.2.2(`apache/airflow:3.2.2`), pyiceberg 0.11.1, pyarrow, astronomer-cosmos, dbt-core
1.10.23 ·
dbt-trino 1.10.6(별도 venv), trino 클라이언트, `holidays`(NYSE 달력), k8s(kind) + ArgoCD.

**Spec:** [원화 종가 Airflow 이관 설계](../specs/2026-10-24-airflow-us-equity-krw-design.md)

## Global Constraints

- 선행: **PR #265 머지**(`us_equity_krw` dbt·`file_format` 제거·환율 시작일을 시세와 일치). 착수 전 `origin/main`
  기준 worktree.
- 테이블 이름·네임스페이스·스키마·파티션 컬럼은 **현재와 같다**: `frankfurter_fx.fx_rates_daily`(`rate_date`),
  `polygon_market.equity_ohlcv_daily`(`trade_date`). 스키마 상수는 Dagster 쪽과 **바이트 단위로 같아야** 한다.
- 파티션 키 = **전일 UTC 하루**. 스케줄 `0 1 * * *`(환율)·`0 9 * * *`(시세), 타임존 UTC.
- 카탈로그 이름은 `iceberg`(전 엔진 공통). S3 쓰기는 `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`(차트 env에
  이미 있음).
- 자격은 **기존 값 재사용**(F6a): 카탈로그 롤 `iceberg`(`default/catalog-pg-app`), S3(`default/lakehouse-creds`).
  접속은 `catalog-postgres-rw.default.svc:5432` · `seaweedfs.default.svc:8333` · `trino.default.svc:8080`.
- 시크릿은 이미지·Git에 두지 않는다. Connection은 `AIRFLOW_CONN_<ID>` ← Secret ← `scripts/k8s-secrets.sh`.
- 새 DAG는 **`is_paused_upon_creation=True`**, `catchup=False`.
- 원천 값(시세·환율)을 로그·XCom·Asset event에 남기지 않는다 — 날짜·행 수·불리언만(Polygon 재배포 금지).
- 이미지 태그는 `gitops/charts/airflow/values.yaml`의 `# bump-image-tag` 한 줄, 덮어쓰기 금지, linux/arm64.
- 비가역(Secret 생성·클러스터 반영·Dagster 정의 삭제 머지 전 데이터 확인)은 reviewer 체크리스트 + 사용자 승인.

## 설계 대비 조정(계획 단계 결정)

- **F7 Pool → `max_active_tis_per_dag=1` + `max_active_runs=1`**: Airflow Pool은 차트·Git으로 선언할 수 없고 수동
  생성이
  필요하다. Polygon을 쓰는 DAG가 하나뿐인 지금은 태스크 동시성 1로 같은 효과를 선언적으로 얻는다. **두 번째 Polygon
  DAG
  (뉴스 이관)가 생길 때 Pool로 바꾼다** — 그 시점이 재검토 트리거다.
- **이미지 빌드 컨텍스트를 저장소 루트로** 넓힌다(dbt 프로젝트가 `images/airflow` 밖에 있다). Dockerfile 위치는
  그대로.
- 휴장일 판정은 **`holidays.financial_holidays("NYSE")` + 주말**이다(설계가 빌더에게 남긴 항목).

## Review Focus

1. **Asset 스케줄의 AND 의미** — 환율은 주말에도 갱신되고 시세는 휴장일에도 0행으로 갱신된다. 두 Asset 중 하나만
갱신된
   날에는 dbt DAG가 돌지 않아야 한다(Task 6 import 테스트에서 schedule이 두 Asset의 AND인지 확인).
2. **휴장일 0행 vs 거래일 0행** — 성금요일·추수감사절은 통과, 평범한 화요일 0행은 실패(Task 4 테스트).
3. **재실행 멱등** — 같은 파티션을 두 번 적재하면 행 수가 그대로이고 스냅샷 계보가 한 루트(Task 1 테스트).
4. **Polygon 403(과거 경계 밖)** — 무료 플랜 경계 밖 날짜 백필은 실패가 정상이다. 실패 메시지가 원인(403)을 그대로
   담아야 한다(Task 4 테스트: 403 응답 → 예외 메시지에 `403` 포함, 키 미포함).
5. **Connection 누락·빈 값** — Secret이 없거나 값이 비면 빈 키로 호출하지 않고 즉시 실패한다(Task 1 테스트:
   `S3Conf`·`fetch_grouped_daily`가 빈 값에 `ValueError`).

---

### Task 1: 공용 패키지 `lakehouse`(수집·파싱·적재, Airflow 비의존)

**Files:**
- Create: `images/airflow/dags/lakehouse/__init__.py`, `http.py`, `fx.py`, `polygon.py`, `iceberg.py`
- Create: `images/airflow/dags/.airflowignore`(내용 `lakehouse/` — DagBag이 패키지를 DAG 파일로 파싱하지 않게)
- Test: `images/airflow/tests/lib/test_fx.py`, `test_polygon.py`, `test_iceberg.py`, `test_parity.py`
- 원본: Dagster `common/helper.py`(`_request`·`replace_partition_in_iceberg`), `common/polygon.py`,
  `defs/frankfurter_fx/assets.py`(`FX_SCHEMA`·`_rates_json_to_arrow`),
  `defs/polygon_market/assets.py`(`OHLCV_SCHEMA`·`_grouped_to_arrow`)

**Interfaces:**
- Produces:
  - `http.request_json(url: str, *, params: dict, headers: dict | None = None, timeout_s: int = 30, retries: int =
    3) -> dict`
    (429·5xx만 백오프 재시도, 실패 예외 메시지에 쿼리스트링 미포함)
  - `polygon.fetch_grouped_daily(api_key: str, trade_date: str) -> dict`(Bearer 헤더, `adjusted=true`)
  - `fx.FX_SCHEMA`, `fx.rates_json_to_arrow(payload: dict, rate_date: str, ingested_at: datetime) -> pa.Table`
  - `polygon.OHLCV_SCHEMA`, `polygon.grouped_to_arrow(payload: dict, trade_date: str, ingested_at: datetime) ->
    pa.Table`
  - `iceberg.load_table(catalog_uri: str, s3: S3Conf, identifier: str) -> Table`, `S3Conf(endpoint, access_key,
    secret_key)`
    (`S3Conf`는 빈 문자열 필드에 `ValueError`, `fetch_grouped_daily`는 빈 `api_key`에 `ValueError` — Review Focus 5)
  - `iceberg.replace_partition(table: Table, arrow: pa.Table, column: str, value: str) -> dict`
    (반환: `{"rows": int, "snapshot_id": int}`; 작은따옴표 포함 값은 `ValueError`)

- [ ] **Step 1: 테스트 이식** — Dagster `tests/test_frankfurter_fx_parse.py`·`test_polygon_market_parse.py`·
  `test_frankfurter_fx_partition_load.py`의 셀을 `lakehouse` 함수로 바꿔 옮긴다(SQLite 카탈로그 + 임시 warehouse,
  음성 대조 포함).
- [ ] **Step 2: 패리티 테스트 작성** — `test_parity.py`:
  ```python
  def test_fx_schema_matches_dagster():
      from dagster_project.defs.frankfurter_fx.assets import FX_SCHEMA as DAGSTER
      assert fx.FX_SCHEMA.equals(DAGSTER, check_metadata=True)

  def test_ohlcv_schema_matches_dagster():
      from dagster_project.defs.polygon_market.assets import OHLCV_SCHEMA as DAGSTER
      assert polygon.OHLCV_SCHEMA.equals(DAGSTER, check_metadata=True)
  ```
  (F3의 「두 벌 공존」 갈라짐 방지 장치. Dagster 쪽 정의를 지우는 Task 8에서 상수 리터럴 비교로 바꾼다.)
- [ ] **Step 3: 실패 확인** — `cd dagster/dockerfile.d/src && PYTHONPATH=../../../images/airflow/dags uv run pytest
      ../../../images/airflow/tests/lib -q`
  → `ModuleNotFoundError: lakehouse`
- [ ] **Step 4: 구현** — 원본 로직을 옮기되 Dagster
      타입(`AssetExecutionContext`·`IcebergTableResource`·`MaterializeResult`)을 쓰지 않는다.
  `lakehouse` 안에서 `airflow`를 import하지 않는다.
- [ ] **Step 5: 통과 확인** — 같은 명령 → 전부 PASS. Dagster 쪽 기존 pytest도 그대로 PASS(`uv run pytest -q`).
- [ ] **Step 6: Commit** — `feat(airflow): 수집·적재 공용 패키지 lakehouse를 이식한다`

### Task 2: 이미지(의존성·dbt venv·dbt 프로젝트)

**Files:**
- Modify: `images/airflow/Dockerfile`, `images/airflow/tests/dag-import.test.sh`(빌드 컨텍스트 = 저장소 루트),
  `.github/workflows/airflow-image.yml`(paths·context)
- Create: `images/airflow/requirements.txt`

**Interfaces:**
- Produces: 이미지 안 경로 — DAG `/opt/airflow/dags/`, dbt 프로젝트 `/opt/airflow/dbt/dbt_pipelines/`(manifest
  포함),
  dbt 실행 파일 `/opt/dbt-venv/bin/dbt`.

- [ ] **Step 1: requirements 고정** — `pyiceberg[sql-postgres,pyarrow]==0.11.1`, `astronomer-cosmos==<Airflow 3.2
      지원 최신 고정>`,
  `trino==<고정>`, `holidays==<고정>`. 버전은 설치 전 PyPI 메타로 Airflow 3.2 호환을 확인하고 정확히 고정한다.
- [ ] **Step 2: Dockerfile** — `pip install -r requirements.txt`(constraints: 공식 이미지 constraints 파일 사용),
  `python -m venv /opt/dbt-venv && /opt/dbt-venv/bin/pip install dbt-core==1.10.23 dbt-trino==1.10.6`,
  `COPY dagster/dockerfile.d/src/dbt_pipelines /opt/airflow/dbt/dbt_pipelines`, 빌드 단계에서 `dbt deps` + `dbt
  parse`
  (manifest 생성, 엔진 접속 없음 — `TRINO_HOST` 더미).
- [ ] **Step 3: 테스트 스크립트에 셀 추가** — 이미지 안에서
  `python -c "import pyiceberg, cosmos, trino, holidays"` 성공, `/opt/dbt-venv/bin/dbt --version`에 `trino: 1.10.6`,
  `/opt/airflow/dbt/dbt_pipelines/target/manifest.json` 존재.
- [ ] **Step 4: 로컬 빌드 검증** — `bash images/airflow/tests/dag-import.test.sh` → 기존 셀 + 새 셀 전부 PASS
  (CI 초록은 빌드 보증이 아니다 — 로컬 podman 빌드로 확인).
- [ ] **Step 5: Commit** — `build(airflow): pyiceberg·cosmos·dbt venv를 이미지에 더한다`

### Task 3: DAG `fx_rates_daily`

**Files:**
- Create: `images/airflow/dags/fx_rates_daily.py`
- Modify: `images/airflow/tests/dag-import.test.sh`(DAG 셀)

**Interfaces:**
- Consumes: `lakehouse.http.request_json`, `lakehouse.fx.*`, `lakehouse.iceberg.*`
- Produces: Asset `FX_ASSET = Asset("iceberg://frankfurter_fx/fx_rates_daily")`(Task 6이 import)

- [ ] **Step 1: import 셀 추가(실패)** — dag_id `fx_rates_daily`, 태스크 1개 `load_fx_rates`, outlets에 위 URI,
  timetable cron `0 1 * * *`·tz `UTC`, `is_paused_upon_creation` True.
- [ ] **Step 2: 실패 확인** — 테스트 스크립트 → `fx_rates_daily` 없음으로 FAIL.
- [ ] **Step 3: 구현** — `CronDataIntervalTimetable("0 1 * * *", timezone="UTC")`, 파티션 키 =
  `data_interval_start`의 UTC 날짜. Connection `iceberg_catalog`(postgres)·`seaweedfs_s3`(aws)를
  `BaseHook.get_connection`으로
  읽어 `S3Conf`·카탈로그 URI를 만든다(Connection이 없으면 즉시 실패). `outlet_events[FX_ASSET].extra`에
  `{"partition": ..., "rows": ..., "source_date": ..., "date_matches_request": ...}`. `retries=2`.
- [ ] **Step 4: 통과 확인** — 테스트 스크립트 PASS.
- [ ] **Step 5: Commit** — `feat(airflow): 환율 수집 DAG fx_rates_daily를 추가한다`

### Task 4: DAG `equity_ohlcv_daily`

**Files:**
- Create: `images/airflow/dags/equity_ohlcv_daily.py`, `images/airflow/dags/lakehouse/calendar.py`
- Test: `images/airflow/tests/lib/test_calendar.py`, `test_polygon.py`(403 셀 추가)
- Modify: `images/airflow/tests/dag-import.test.sh`

**Interfaces:**
- Produces: `calendar.is_us_trading_day(day: date) -> bool`, Asset `OHLCV_ASSET =
  Asset("iceberg://polygon_market/equity_ohlcv_daily")`

- [ ] **Step 1: 테스트(실패)** —
  ```python
  def test_trading_days():
      assert not is_us_trading_day(date(2025, 4, 18))   # 성금요일
      assert not is_us_trading_day(date(2025, 11, 27))  # 추수감사절
      assert not is_us_trading_day(date(2025, 4, 19))   # 토요일
      assert is_us_trading_day(date(2025, 4, 21))       # 부활절 월요일(미국 개장)

  def test_403_message_has_status_not_key(): ...  # 403 응답 → 예외 메시지에 "403" 포함, api_key 문자열 미포함
  ```
  import 셀: dag_id `equity_ohlcv_daily`, cron `0 9 * * *` UTC, `max_active_runs` 1, 태스크 `max_active_tis_per_dag`
  1.
- [ ] **Step 2: 실패 확인** — pytest·테스트 스크립트 FAIL.
- [ ] **Step 3: 구현** — `holidays.financial_holidays("NYSE")` + 주말. 태스크는 fetch → parse → 적재 후
  `time.sleep(12.5)`(분당 5회 상한 보장). **거래일인데 0행이면 실패**, 휴장일 0행은 성공. Connection
  `polygon_api`(password = 키).
  `extra`: `partition`·`rows`·`results_count`·`source_dates`·`is_trading_day`.
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: Commit** — `feat(airflow): Polygon 시세 수집 DAG equity_ohlcv_daily를 추가한다`

### Task 5: Connection Secret·차트 env

**Files:**
- Modify: `scripts/k8s-secrets.sh`(Secret 2종 + 불일치 검사), `gitops/charts/airflow/values.yaml`(`secret:`·`env:`),
  `.env.example`(주석만)

**Interfaces:**
- Produces: env `AIRFLOW_CONN_POLYGON_API`(Secret `airflow-conn-polygon`), `AIRFLOW_CONN_ICEBERG_CATALOG`(Secret
  `airflow-conn-iceberg`),
  `TRINO_HOST=trino.default.svc`·`TRINO_PORT=8080`(차트 env 리터럴)

- [ ] **Step 1: 스크립트** — 기존 `airflow-conn-seaweedfs` 블록과 같은 모양: 있으면 값 일치만 검사(불일치면 실패),
      없으면 생성.
  `airflow-conn-iceberg`는 `default/catalog-pg-app`의 `username`·`password`로 `postgres` Connection JSON
  (host `catalog-postgres-rw.default.svc`, port 5432, schema `iceberg`). `airflow-conn-polygon`은 `.env`의
  `POLYGON_API_KEY`.
- [ ] **Step 2: 정적 확인** — `bash -n scripts/k8s-secrets.sh`, `shellcheck`, 차트 `helm template`에 env 3종 렌더.
- [ ] **Step 3: 🔴 게이트** — reviewer 보안 체크리스트 → 사용자 승인 → `scripts/k8s-secrets.sh` 실행 →
  `kubectl -n airflow get secret airflow-conn-polygon airflow-conn-iceberg`(값 미출력) 존재 확인.
- [ ] **Step 4: Commit** — `feat(airflow): Polygon·Iceberg Connection Secret 배선을 더한다`

### Task 6: DAG `us_equity_krw_dbt`

**Files:**
- Create: `images/airflow/dags/us_equity_krw_dbt.py`
- Modify: `images/airflow/tests/dag-import.test.sh`

**Interfaces:**
- Consumes: `FX_ASSET`, `OHLCV_ASSET`(Task 3·4), 이미지 경로(Task 2), env `TRINO_HOST`·`TRINO_PORT`(Task 5)
- Produces: Asset `KRW_ASSET = Asset("iceberg://us_equity_krw/us_equity_krw__close_daily")`

- [ ] **Step 1: import 셀(실패)** — dag_id `us_equity_krw_dbt`; schedule이 `FX_ASSET & OHLCV_ASSET`(AND);
  Cosmos 그룹에 모델 3개(`equity_prices_daily`·`fx_usdkrw_daily`·`us_equity_krw__close_daily`)의 run·test 태스크가
  있고
  `stock_forecast`의 다른 모델은 **없다**; 마지막 태스크 `record_latest_trade_date`의 outlet이 `KRW_ASSET`.
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — Cosmos 그룹은 다음 설정값으로 만든다(Task 2의 이미지 경로).
  ```python
  DbtTaskGroup(
      project_config=ProjectConfig("/opt/airflow/dbt/dbt_pipelines", manifest_path=MANIFEST),
      profile_config=ProfileConfig(profiles_yml_filepath=PROFILES, profile_name="dbt_pipelines", target_name="dev"),
      execution_config=ExecutionConfig(dbt_executable_path="/opt/dbt-venv/bin/dbt"),
      render_config=RenderConfig(
          load_method=LoadMode.DBT_MANIFEST,
          select=["fqn:us_equity_krw", "equity_prices_daily"],
      ),
  )
  ```
  `record_latest_trade_date`는 Trino(`trino.dbapi`, user `airflow`)로 `select max(trade_date), count(*)` 후
  `extra`와 반환값에 남긴다.
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: Commit** — `feat(airflow): 원화 종가 dbt DAG us_equity_krw_dbt를 추가한다`

### Task 7: 규약 문서 + PR-A(배포)

**Files:**
- Create: `docs/conventions/airflow.md`(본보기: DAG = bronze 테이블 1개, `lakehouse` 공존·패리티 테스트 규칙,
  Connection 패턴,
  UTC 파티션 예외, Pool 재검토 트리거)
- Modify: `docs/dataset_schema/us-equity-krw.md`(운영 절: 소유자 Airflow), `docs/conventions/timezone.md`(예외 1줄),
  `CLAUDE.md`(Airflow 규약 링크 1줄), `gitops/charts/airflow/values.yaml`(`# bump-image-tag` 다음 버전)

- [ ] **Step 1: 문서 작성** + `pre-commit run --files <변경 파일>` 전부 Passed.
- [ ] **Step 2: Commit + PR** — PR-A 본문에 「DAG는 일시정지 상태로 배포, 컷오버는 PR-B」 명시.
- [ ] **Step 3: 머지 후** — CI가 이미지를 발행했는지(GHCR 태그 존재), ArgoCD `airflow` 앱 Synced/Healthy, UI에 DAG
      3개(paused).

### Task 8: 컷오버 + PR-B(Dagster 정의 삭제)

**Files:**
- Delete/Modify: Dagster `defs/frankfurter_fx/`(자산·상수), `defs/polygon_market/assets.py`의
  `equity_ohlcv_daily`만,
  `defs/us_equity_krw/`, `defs/automation.py`(해당 잡·스케줄), `defs/resources.py`(해당 테이블 바인딩),
  `defs/stock_forecast/dbt_assets.py`(`exclude="equity_prices_daily"`), `models/*/source.yml`의 해당
  `meta.dagster.asset_key`
- Modify: `images/airflow/tests/lib/test_parity.py`(Dagster import → 스키마 리터럴 비교로)

- [ ] **Step 1: 값 대조(읽기 + 멱등 재적재)** — 대조 전 Trino로 대상 파티션의 `count(*)`와 키 집합 해시를 기록
  (환율 1일, 시세 거래일 1일 + 휴장일 1일). Airflow 백필로 그 날짜를 다시 돌린 뒤 같은 값인지 확인.
- [ ] **Step 2: unpause** — 3 DAG를 켜고 다음 정기 실행 1회가 모두 success, `record_latest_trade_date`의 extra에
      전일 거래일.
  F9 확인: 그 실행 동안 Celery worker의 메모리 최대치(`kubectl top pod -n airflow` 주기 관측)가 limit 1Gi 아래인지
  기록.
- [ ] **Step 3: Dagster 삭제** — 위 파일 변경 후 `uv run pytest -q` PASS, 정의 로드(인스턴스 없이)에서 자산 수가
      줄고 고아 0,
  `stock_forecast` 그룹에 `equity_prices_daily`가 없다.
- [ ] **Step 4: Commit + PR-B** — `refactor(dagster)!: 원화 종가 경로를 Airflow로 넘기고 Dagster 정의를 지운다`
  (BREAKING: Dagster에서 환율·시세 자산 제거).
