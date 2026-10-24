# 원화 종가 파이프라인의 Airflow 이관 — 설계

> 구현 계획은 이 설계를 입력으로 별도 작성한다. 선행: PR #265(`us_equity_krw` dbt·환율 시작일),
> 관련 설계: [Airflow on ArgoCD](2026-10-11-airflow-argocd-design.md)(범위가 「UI 기동까지」였고
> 데이터셋 이관을 이후 단계로 미뤘다 — 이 문서가 그 첫 단계다).

## Context

- **현재**: 미국 종목 원화 종가는 Dagster 정의(환율 `fx_rates_daily`·시세 `equity_ohlcv_daily` 자산 →
  dbt `us_equity_krw`)로 만들어진다. Dagster는 클러스터에 배포하지 않으므로(D6) 정기 실행 주체가 없고,
  과거 구간은 호스트에서 일회성 스크립트로 백필했다. Airflow는 클러스터에서 상주하지만 DAG는 실험용 2개뿐이고
  **dbt·Iceberg 적재·API 키 경로가 없다**.
- **목적(사용자 결정)**: 이 파이프라인을 Airflow로 옮겨 **매일 자동 실행**하고, Airflow UI에서
  **① DAG 실행 성공 여부 ② 원화 종가의 최신 거래일**을 본다. 이것은 **Dagster를 대체하는 첫걸음**이다 —
  옮긴 부분의 Dagster 정의는 이관 후 지운다.
- **범위(사용자 결정)**: 원화 종가에 필요한 것만 — 환율 수집, Polygon 시세 수집, dbt 빌드
  (`equity_prices_daily` → `fx_usdkrw_daily` → `us_equity_krw__close_daily`). 뉴스·FRED·주가예측은 Dagster에 남는다.
- **설계 방침(사용자 위임)**: 기술 선택은 설계자에게 맡기되 **Dagster 구조와 유사하게** 한다. 이 문서의 결정은
  모두 그 위임 아래의 **설계자 판단**이며, 사용자가 문서를 보며 논의한다.

## 목표 · 비목표 · 반목표

- **목표**: Airflow만으로 수집 → Iceberg 적재 → dbt 빌드가 완결된다 / UI에서 성공 여부와 최신 거래일이 보인다 /
  이후 데이터셋 이관이 **따라 할 본보기**가 된다.
- **비목표**: 뉴스·FRED·주가예측 이관 / 원격 로깅 / 국내 환율 원천 / Dagster 전체 철거.
- 🔴 **반목표**:
  - **테이블 이름·스키마·값이 Dagster 시절과 달라지는 것** — 남은 Dagster 모델이 같은 시세 테이블을 읽는다.
  - 시크릿이 이미지·DAG 코드에 들어가는 것.
  - 거래일인데 0행을 적재하고 「성공」으로 끝나는 것.
  - Polygon 무료 한도(분당 5회)를 넘겨 실패가 배경 소음이 되는 것.

## Dagster ↔ Airflow 대응

설계 방침이 「Dagster와 유사하게」이므로 개념을 1:1로 옮긴다.

| Dagster | Airflow 3 | 이 설계 |
|---|---|---|
| 자산(`@dg.asset`) | **Asset** + outlet으로 갱신하는 태스크 | bronze 테이블 1개 = Asset 1개 = DAG 1개 |
| 일자 파티션(UTC) | logical date(UTC) | 파티션 키 = 전일 UTC 하루(timetable은 빌더 몫) |
| 파티션 잡 → 파생 스케줄 | cron 스케줄 + `catchup=False` | 발화 시각을 Dagster와 같게(§4) |
| 백필 | **Airflow 3 백필** | 파티션 = DAG run 1개. 일회성 스크립트를 대체 |
| `MaterializeResult(metadata=...)` | **Asset event `extra`** + XCom | 행 수·`source_dates`·`date_matches_request` |
| 리소스 | **Connection**(`AIRFLOW_CONN_*`) + 공용 헬퍼 | 키·엔드포인트는 Connection, 로직은 헬퍼 |
| `@dbt_assets(select=...)` | **Cosmos `DbtTaskGroup`** | dbt 모델 = 태스크, 모델 출력 = Asset |
| 자산 계보 | **Asset 기반 스케줄링** | dbt DAG는 두 bronze Asset이 갱신되면 돈다 |
| `common/` | DAG 번들 안의 공용 패키지 | 위치·이름은 빌더 몫 |

## 결정 요약

| # | 결정 | 누가 |
|---|---|---|
| F1 | **DAG 3개**: `fx_rates_daily` · `equity_ohlcv_daily` · `us_equity_krw_dbt` | 설계자 |
| F2 | 적재는 **pyiceberg 파티션 교체**(`overwrite(filter)`)를 그대로 이식 | 설계자 |
| F3 | 공용 코드는 Dagster 패키지를 import하지 않고 **복사·이식**, 당분간 **두 벌 공존** | 설계자 |
| F4 | dbt는 **Cosmos + 별도 venv의 dbt-trino** | 설계자 |
| F5 | dbt DAG는 **Asset 스케줄**(두 bronze Asset 모두 갱신 시) | 설계자 |
| F6 | Connection: `polygon_api` · `iceberg_catalog` · `seaweedfs_s3`(기존) · `trino_default` | 설계자 |
| F6a | 🔴 Iceberg·S3 자격은 **기존 값 재사용**, 접속은 클러스터 내부 이름 | **사용자** |
| F7 | Polygon 호출은 **Pool `polygon_api`(slot 1)** + 태스크 내 간격 | 설계자 |
| F8 | 최신 거래일을 **Trino `max(trade_date)`로 조회해 Asset event·반환값**에 남김 | 설계자 |
| F9 | 실행기는 **Celery(기본)**, 메모리는 실측 후 조정 | 설계자 |
| F10 | **`equity_prices_daily`는 Airflow가 소유**, Dagster `stock_forecast`는 `exclude` | 설계자 |
| F11 | **raw S3 단계 없음** — 태스크 하나에서 fetch → parse → 적재 | 설계자 |

### 결정 근거

- **F1** — Dagster의 잡 3개와 같다. 원천마다 실패·재시도·한도가 달라 묶지 않는다(Dagster가 시세·뉴스를 가른
  이유와 같다).
- **F2** — 현재 테이블이 이 방식으로 만들어졌다. 값·계보가 Dagster 시절과 같아야 한다(반목표).
  `drop_table`이 없어 스냅샷 계보가 이어진다.
- **F3** — Dagster를 지우는 것이 목적이라 공유 import는 철거를 막는다. ⚠️ **중복은 일시적이지 않다** —
  `_request`·`replace_partition_in_iceberg`·Polygon 클라이언트는 Dagster에 남는 뉴스·FRED도 쓴다.
  남은 데이터셋을 다 옮길 때까지 두 벌이 공존하고, 갈라짐은 **같은 pytest 셀을 양쪽에 유지**해 막는다.
  이 공존 규칙을 본보기 규약에 적는다.
- **F4** — Dagster `@dbt_assets`와 같은 「모델 = 실행 단위」. dbt와 Airflow의 의존성 충돌을 venv 분리로
  피한다(`dbt_executable_path`).
- **F5** — Dagster의 「19:00 KST 고정 cron + 상류보다 뒤」보다 정확하다. 시각 가정이 깨져도 낡은 입력으로
  빌드하지 않는다.
- **F6** — 기존 패턴(`AIRFLOW_CONN_<ID>` ← Secret ← `k8s-secrets.sh`)을 따른다. 이미지·Git에 값이 없다.
- **F6a(사용자 결정)** — 카탈로그 롤 `iceberg`(`default/catalog-pg-app`)·S3 키(`default/lakehouse-creds`)와
  **같은 값**으로 `airflow` 네임스페이스에 Connection Secret을 만든다. Secret은 네임스페이스 단위라 복제가
  필요하다. 접속은 `catalog-postgres-rw.default.svc:5432`·`seaweedfs.default.svc:8333`·`trino.default.svc:8080`
  이라 **포트포워드가 필요 없다**.
  - 얻는 것: 별도 롤의 CNPG·GRANT·회전 작업과 비가역 게이트를 피한다.
  - 대가: **회전 묶음이 하나 늘어난다** — CNPG 원본·`default` Secret·`airflow` Secret·호스트 `.env`를 한 벌로
    바꾼다. 한쪽만 바뀐 상태는 「접속은 되는데 쓰기에서 실패」로 드러난다.
  - 메타 DB에 `iceberg` 롤 재사용을 금지한 A4와는 용도(메타 vs 데이터 쓰기)가 달라 충돌하지 않는다.
- **F7** — 분당 5회. 백필이 동시 run을 띄워도 순간 요청 수가 1을 넘지 않게 429를 재시도가 아니라
  **구조로** 막는다. 호스트 백필에서는 재시도가 붙은 간격에서 429가 주기적으로 났다.
- **F8** — 사용자 성공 기준 ②. 별도 대시보드 없이 UI의 Asset·태스크 화면에서 보인다. 값은 날짜·행 수만
  남긴다(Polygon 재배포 금지).
- **F9** — 하루치 시세는 1Gi 안에 들 것으로 본다(**미측정**). 부족하면 해당 태스크만 `KubernetesExecutor`로.
- **F10** — 원천(시세)을 쓰는 쪽이 첫 silver도 만든다. 두 오케스트레이터가 같은 테이블을 각자 `replace`하는
  이중 소유를 막는다. 남은 Dagster 모델(피처·라벨)은 그것을 **외부 상류**로 읽는다. 파일을 옮기면 fqn이
  바뀌어 양쪽 셀렉터가 다 흔들리므로 옮기지 않는다.
- **F11** — XCom에 응답 본문을 싣지 않으면서 재실행이 항상 원천을 다시 묻게 한다(미완 응답을 재실행으로
  고칠 수 있다). 원천 바이트를 새로 보존하지 않아 거버넌스 표를 바꿀 일이 없다.

## 설계

### 1. 데이터 흐름

```
[fx_rates_daily DAG]       Frankfurter ──fetch──▶ parse ──▶ Iceberg frankfurter_fx.fx_rates_daily   (Asset A)
[equity_ohlcv_daily DAG]   Polygon     ──fetch──▶ parse ──▶ Iceberg polygon_market.equity_ohlcv_daily (Asset B)
                                                                         │
[us_equity_krw_dbt DAG]  schedule=[A, B] ──▶ Cosmos: equity_prices_daily → fx_usdkrw_daily → us_equity_krw__close_daily (+테스트)
                                         ──▶ latest_trade_date (Trino max) ──▶ Asset event extra
```

- 테이블 이름·네임스페이스·스키마·파티션 컬럼(`rate_date`·`trade_date`)은 **현재와 같다**.
- 각 수집 DAG는 **태스크 하나**(fetch → parse → 적재)다(F11). 응답은 태스크 안에서만 다루고 XCom에는
  관측 메타(행 수 등)만 남긴다. 재실행은 항상 원천을 다시 부른다 — 한도는 Pool(F7)이 지킨다.
- `equity_prices_daily`는 Airflow dbt DAG가 빌드하고 Dagster는 그것을 읽기만 한다(F10).

### 2. 공용 라이브러리 (DAG 번들 안 — Dagster `common/`과 당분간 공존, F3)

Dagster `common/`에서 이 범위에 필요한 것만 이식한다.

- HTTP: `_request`(429·5xx 백오프)·Polygon 클라이언트(Bearer 헤더, `fetch_next` 호스트 검사)
- 파싱: `_rates_json_to_arrow`·`_grouped_to_arrow`와 각 스키마(`FX_SCHEMA` 등)
  — **스키마 상수는 한 글자도 바꾸지 않는다**
- 적재: `replace_partition_in_iceberg`의 로직(필터 문자열화·작은따옴표 fail-closed 포함)
- 기존 pytest(`test_frankfurter_fx_partition_load.py` 등)를 함께 옮겨 **같은 셀이 Airflow 쪽 코드에서 통과**하게 한다

### 3. 이미지

`images/airflow/`에 추가한다(현재는 공식 이미지 그대로).

| 추가 | 용도 |
|---|---|
| `pyiceberg[sql-postgres,pyarrow]` | 적재(F2). 버전은 Dagster 쪽과 같게 고정 |
| `astronomer-cosmos` | dbt 렌더(F4) |
| 별도 venv의 `dbt-core`·`dbt-trino` | dbt 실행(F4). 버전은 현재 저장소와 같게 |
| dbt 프로젝트(`dbt_pipelines/`) 복사 + `dbt deps` | Cosmos 입력. 저장소 경로가 바뀌지 않게 이미지 빌드에서 복사 |

- 이미지 태그 bump·arm64·「태그 불변」 규약은 [`docker.md`](../../conventions/docker.md) §3 그대로.
- **DAG import 테스트 셀을 DAG마다 추가**한다(같은 문서의 규약).

### 4. 스케줄 (UTC, Dagster와 같은 시각)

| DAG | 스케줄 | 근거 |
|---|---|---|
| `fx_rates_daily` | `0 1 * * *`(10:00 KST) — 전일 UTC 하루 | Dagster `SCHEDULE_HOUR_UTC = 1` |
| `equity_ohlcv_daily` | `0 9 * * *`(18:00 KST) — 전일 UTC 하루 | Dagster `SCHEDULE_HOUR_UTC = 9` |
| `us_equity_krw_dbt` | Asset 스케줄 `[A, B]` | F5 |

- 저장소 타임존 정책(「표시·스케줄은 KST」)과 갈리는 이유는 Dagster 때와 같다 — **파티션 키가 그대로 API 날짜
  파라미터로 나가므로 UTC 하루**여야 한다(`frankfurter_fx/constants.py` 주석). 문서에 예외로 남긴다.
- 기본 일시정지(`is_paused_upon_creation=True`) — Dagster의 `STOPPED`와 같은 이유(켜는 것은 사람).

### 5. 관측 (사용자 성공 기준)

- ① **성공 여부**: DAG run 상태. 실패 원인이 UI에 남도록 실패 메시지에 원인 사슬의 맨 안쪽을 쓴다.
- ② **최신 거래일**: F8. 값은 날짜와 행 수만(원천 값 금지 — Polygon 재배포 금지).
- Dagster 메타와 같은 관측: `results_count`(원천 신고 수) vs 적재 행 수, `date_matches_request`, `is_trading_day`.
- 🔴 **거래일 0행 차단**: 미국 거래일인데 0행이면 태스크를 실패시킨다(반목표). 휴장일 0행은 정상이다 —
  휴장 판정 기준은 빌더 몫(아래).

### 6. 이관 절차(컷오버)

1. Airflow 구현 + 이미지 발행 + Connection Secret 생성(`k8s-secrets.sh` 확장).
2. **값 대조**: 이미 적재된 과거 하루(예: 거래일 1·휴장일 1)를 Airflow DAG로 다시 돌려, 행 수·키 집합이
   Dagster 시절 적재분과 같은지 본다(파티션 교체라 멱등 — 다시 돌려도 안전).
3. DAG unpause → 하루 이상 정기 실행 성공 확인(성공 기준 ①②).
4. Dagster에서 삭제: `fx_rates_daily`·`equity_ohlcv_daily` 자산·잡·스케줄, `defs/us_equity_krw/`
   (PR #265 머지 전제), 해당 리소스 바인딩. `stock_forecast`의 `@dbt_assets`에 `equity_prices_daily`
   **exclude**를 더한다(F10). 공용 헬퍼는 뉴스·FRED가 쓰므로 **지우지 않는다**(F3).
   `stock_forecast`의 source(`equity_ohlcv_daily`)는 남은 Dagster 모델이 읽으므로 **source 선언은 유지**하고
   `meta.dagster.asset_key`만 정리한다(소유자가 Airflow로 바뀌었음을 주석으로).
5. 문서: `docs/architectures/airflow.md` 신설 여부는 별도(기존 설계가 「이관 마지막 단계 몫」으로 미뤘다).
   이 이관의 규약(본보기)은 `docs/conventions/`에 남긴다.

## 빌더에게 남긴 것

- 휴장일 판정 방법(Polygon 응답 0행 + 요일·미국 휴일 달력 중 무엇으로 「거래일 0행」을 가를지)
- Cosmos 설정 세부(LoadMode, 테스트 실행 방식, profiles 매핑 — Trino 무인증 매핑이 Cosmos에 없으면
  profiles.yml 파일 사용)
- 공용 라이브러리의 위치·이름
- 「전일 UTC 하루」를 파티션 키로 얻는 timetable 방식(Airflow 3 cron 기본 timetable은
  `data_interval_start`가 전일이 아니다)
- Pool 외에 태스크 내 간격(12초 이상)을 어디에 둘지

## 미확인 (구현 전 확인)

- Cosmos가 Airflow 3.2와 이 저장소의 dbt 버전에서 Asset 방출(모델 = Asset)을 지원하는지
- pyiceberg·pyarrow를 공식 Airflow 이미지에 넣었을 때 의존성 충돌 여부
- 워커 1Gi에서 하루치 시세 적재의 실제 메모리
- Airflow 3 백필이 Pool·`max_active_runs`를 지키는지(F7의 전제)
