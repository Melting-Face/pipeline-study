#!/usr/bin/env bash
# images/airflow/tests/dag-import.test.sh
#
# 커스텀 Airflow 이미지를 빌드하고, 이미지 안에서 DAG가 "import 오류 0건"이며
# DAG(`hello`·`github_trending`)가 기대한 모양인지 본다. 클러스터·레지스트리를 건드리지 않는다.
#
# 🔴 왜 `airflow dags list`가 아니라 DagBag 직접 호출인가:
#   Airflow 3의 `dags list`/`list-import-errors`는 메타데이터 DB(초기화 필요)를
#   읽는다. 일회용 컨테이너에서는 DB가 없어 이 경로가 깨지기 쉽다. DagBag은
#   DAG 폴더를 파싱만 하므로 DB 없이 같은 의미("import 오류 0 + hello 존재")를
#   가장 덜 취약하게 검증한다.
#
# 🔴 보지 못하는 것: DagBag 기본 safe_mode는 내용에 "airflow"와 "dag" 문자열이 둘 다 없는
#   파일을 파싱하지 않고 건너뛴다(오류로도 세지 않는다). 음성 대조용으로 깨진 파일을 넣을 때
#   두 문자열을 함께 넣지 않으면 "오류 0건"이 나와 게이트가 죽은 것처럼 보인다.
#
# 의존 도구: 컨테이너 CLI(기본 podman — 로컬 arm64 호스트. CI 러너는 CONTAINER_CLI=docker), jq
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${CONTEXT_DIR}/../.." && pwd)"
VALUES="${REPO_ROOT}/gitops/charts/airflow/values.yaml"
IMAGE="pipeline-study-airflow:test"
CLI="${CONTAINER_CLI:-podman}"

# 배포(차트)와 같은 executor 목록 — 단일 출처는 values.yaml 이다(테스트에 다시 적지 않는다).
# 줄이 정확히 1개가 아니면 판정 불가로 실패한다(엉뚱한 목록으로 통과하지 않게).
executor_lines="$(grep -E '^ +executor: "[^"]+"$' "${VALUES}" || true)"
if [ "$(printf '%s' "${executor_lines}" | grep -c . || true)" -ne 1 ]; then
    echo "FAIL [executor-source] ${VALUES} 에서 executor 줄을 정확히 1개 찾지 못했다"
    exit 1
fi
EXECUTORS="$(printf '%s' "${executor_lines}" | sed -E 's/^ +executor: "([^"]+)"$/\1/')"

echo "== 빌드: ${IMAGE} (linux/arm64, ${CLI}) =="
# dbt 프로젝트는 이 컨텍스트 밖에 있어 **이름 붙은 추가 컨텍스트**로 넘긴다(컨텍스트를 저장소 루트로
# 넓히면 .env 등이 컨텍스트에 실린다). CI build-push-action 의 build-contexts 와 같은 이름 `dbt`.
DBT_CONTEXT="${REPO_ROOT}/dagster/dockerfile.d/src/dbt_pipelines"
if ! "${CLI}" build --platform linux/arm64 --build-context dbt="${DBT_CONTEXT}" -t "${IMAGE}" "${CONTEXT_DIR}"; then
    echo "FAIL [build] 이미지 빌드 실패 — Dockerfile 확인"
    exit 1
fi

# 컨테이너 안에서 실행할 검증 코드: 오류 수와 DAG id 목록을 JSON으로 출력한다.
CHECK_PY='
import json
from airflow.models.dagbag import DagBag

bag = DagBag(dag_folder="/opt/airflow/dags", include_examples=False)
dags = {
    dag_id: {
        "tasks": {t.task_id: t.executor for t in d.tasks},
        "downstream": {t.task_id: sorted(t.downstream_task_ids) for t in d.tasks},
        "outlets": {t.task_id: sorted(a.uri for a in t.outlets) for t in d.tasks},
        # Task SDK timetable 에는 summary 가 없다. cron 의 해석 tz 는 DAG 가 아니라 timetable 의 것이다
        "cron": getattr(d.timetable, "expression", None) or getattr(d.timetable, "_expression", None),
        "cron_tz": str(getattr(d.timetable, "timezone", None) or getattr(d.timetable, "_timezone", None)),
        "timetable": type(d.timetable).__name__,
        "paused": d.is_paused_upon_creation,
        "catchup": d.catchup,
        "max_active_runs": d.max_active_runs,
        "max_active_tasks": d.max_active_tasks,
        "tis": {t.task_id: t.max_active_tis_per_dag for t in d.tasks},
        # Cosmos dbt 태스크가 실행 때 dbt deps 를 다시 받는지(None = Cosmos 태스크 아님)
        "install_deps": {t.task_id: getattr(t, "install_deps", None) for t in d.tasks},
        # Cosmos 테스트 태스크의 dbt indirect selection 모드(None = 미지정 = dbt 기본 eager)
        "indirect": {t.task_id: getattr(t, "indirect_selection", None) for t in d.tasks},
        # Asset 스케줄이면 조건의 종류(AssetAll=AND·AssetAny=OR)와 대상 URI
        "asset_cond": type(getattr(d.timetable, "asset_condition", None)).__name__,
        "asset_uris": sorted(
            a.uri for a in getattr(getattr(d.timetable, "asset_condition", None), "objects", [])
        ),
    }
    for dag_id, d in bag.dags.items()
}
print(json.dumps({"errors": bag.import_errors, "dag_ids": sorted(bag.dag_ids), "dags": dags}))
'

# values 의 executor 목록을 주입한다. 없으면 태스크의 executor="KubernetesExecutor"가
# UnknownExecutorException으로 import 오류가 된다. 첫 번째가 기본 executor다.
if ! result="$("${CLI}" run --rm -e AIRFLOW__CORE__EXECUTOR="${EXECUTORS}" "${IMAGE}" python -c "${CHECK_PY}" | tail -n 1)"; then
    echo "FAIL [run] 컨테이너에서 DagBag 검사 실행 실패"
    exit 1
fi
echo "검사 결과: ${result}"

rc=0

if [[ "$(echo "${result}" | jq -c '.errors')" == "{}" ]]; then
    echo "PASS [import-errors] DAG import 오류 0건"
else
    echo "FAIL [import-errors] DAG import 오류가 있다"
    rc=1
fi

if echo "${result}" | jq -e '.dag_ids | index("hello")' >/dev/null; then
    echo "PASS [dag-id] DAG id 'hello' 존재"
else
    echo "FAIL [dag-id] DAG id 'hello'가 없다"
    rc=1
fi

# 미지정 태스크는 null(→ 배포 기본 Celery), k8s 태스크만 명시 executor를 가진다.
if echo "${result}" | jq -e '.dags.hello.tasks == {"say_hello": null, "say_hello_k8s": "KubernetesExecutor"}' >/dev/null; then
    echo "PASS [executors] say_hello=기본(Celery), say_hello_k8s=KubernetesExecutor"
else
    echo "FAIL [executors] 태스크별 executor가 기대와 다르다"
    rc=1
fi

# 기본(Celery) 경로가 끝난 뒤 K8s 경로가 돈다 — 의존이 빠지면 두 경로가 병렬로 돌아도 테스트가 통과했었다
if echo "${result}" | jq -e '.dags.hello.downstream == {"say_hello": ["say_hello_k8s"], "say_hello_k8s": []}' >/dev/null; then
    echo "PASS [dependency] say_hello >> say_hello_k8s"
else
    echo "FAIL [dependency] 태스크 의존이 기대와 다르다"
    rc=1
fi

# --- github_trending ---------------------------------------------------------
if echo "${result}" | jq -e '.dag_ids | index("github_trending")' >/dev/null; then
    echo "PASS [gt-dag-id] DAG id 'github_trending' 존재"
else
    echo "FAIL [gt-dag-id] DAG id 'github_trending'가 없다"
    rc=1
fi

# 두 태스크 모두 기본(Celery) — executor 를 지정하지 않는다
if echo "${result}" | jq -e '.dags.github_trending.tasks == {"fetch_trending": null, "write_raw": null}' >/dev/null; then
    echo "PASS [gt-tasks] fetch_trending·write_raw, 기본 executor"
else
    echo "FAIL [gt-tasks] 태스크 집합·executor가 기대와 다르다"
    rc=1
fi

# 수집이 끝나야 저장한다
if echo "${result}" | jq -e '.dags.github_trending.downstream == {"fetch_trending": ["write_raw"], "write_raw": []}' >/dev/null; then
    echo "PASS [gt-dependency] fetch_trending >> write_raw"
else
    echo "FAIL [gt-dependency] 태스크 의존이 기대와 다르다"
    rc=1
fi

# 매일 09:00 이고 해석 기준이 KST 여야 한다 — cron 만 맞고 tz 가 UTC 면 18:00 KST 에 돈다
if echo "${result}" | jq -e '.dags.github_trending.cron == "0 9 * * *" and .dags.github_trending.cron_tz == "Asia/Seoul"' >/dev/null; then
    echo "PASS [gt-schedule] 0 9 * * * (Asia/Seoul)"
else
    echo "FAIL [gt-schedule] 스케줄·타임존이 기대와 다르다"
    rc=1
fi

# 저장 태스크만 Asset 을 낸다(하류 DAG 의 트리거 대상)
if echo "${result}" | jq -e '.dags.github_trending.outlets.write_raw == ["s3://warehouse/raw/github_trending"]' >/dev/null; then
    echo "PASS [gt-outlet] write_raw → s3://warehouse/raw/github_trending"
else
    echo "FAIL [gt-outlet] write_raw 의 Asset outlet이 기대와 다르다"
    rc=1
fi

# --- fx_rates_daily (환율 bronze) ------------------------------------------------------
# 🔴 cron 은 UTC 01:00, 데이터 구간 timetable 이어야 파티션 키(data_interval_start)가 「전일 UTC 하루」다
if echo "${result}" | jq -e '.dags.fx_rates_daily | .tasks == {"load_fx_rates": null}
        and .cron == "0 1 * * *" and .cron_tz == "UTC" and .timetable == "CronDataIntervalTimetable"
        and .paused == true and .catchup == false' >/dev/null; then
    echo "PASS [fx-dag] fx_rates_daily: 태스크 1·0 1 * * * UTC·데이터 구간·일시정지·catchup 없음"
else
    echo "FAIL [fx-dag] fx_rates_daily 모양이 기대와 다르다"
    rc=1
fi

if echo "${result}" | jq -e '.dags.fx_rates_daily.outlets.load_fx_rates == ["iceberg://frankfurter_fx/fx_rates_daily"]' >/dev/null; then
    echo "PASS [fx-outlet] load_fx_rates → iceberg://frankfurter_fx/fx_rates_daily"
else
    echo "FAIL [fx-outlet] load_fx_rates 의 Asset outlet이 기대와 다르다"
    rc=1
fi

# --- equity_ohlcv_daily (Polygon 시세 bronze) -------------------------------------------
# 🔴 무료 한도(분당 5회) — run 1개·태스크 동시 1개로 순간 요청을 1로 묶는다(Pool 대신, 계획 조정)
if echo "${result}" | jq -e '.dags.equity_ohlcv_daily | .tasks == {"load_equity_ohlcv": null}
        and .cron == "0 9 * * *" and .cron_tz == "UTC" and .timetable == "CronDataIntervalTimetable"
        and .paused == true and .catchup == false and .max_active_runs == 1
        and .tis.load_equity_ohlcv == 1' >/dev/null; then
    echo "PASS [ohlcv-dag] equity_ohlcv_daily: 0 9 * * * UTC·일시정지·run 1·태스크 동시 1"
else
    echo "FAIL [ohlcv-dag] equity_ohlcv_daily 모양이 기대와 다르다"
    rc=1
fi

if echo "${result}" | jq -e '.dags.equity_ohlcv_daily.outlets.load_equity_ohlcv == ["iceberg://polygon_market/equity_ohlcv_daily"]' >/dev/null; then
    echo "PASS [ohlcv-outlet] load_equity_ohlcv → iceberg://polygon_market/equity_ohlcv_daily"
else
    echo "FAIL [ohlcv-outlet] load_equity_ohlcv 의 Asset outlet이 기대와 다르다"
    rc=1
fi

# --- us_equity_krw_dbt (원화 종가 dbt) ---------------------------------------------------
# 🔴 두 bronze Asset 의 AND — 한쪽만 갱신된 날(환율은 주말에도 갱신)에 낡은 입력으로 빌드하지 않는다
if echo "${result}" | jq -e '.dags.us_equity_krw_dbt | .asset_cond == "AssetAll"
        and .asset_uris == ["iceberg://frankfurter_fx/fx_rates_daily", "iceberg://polygon_market/equity_ohlcv_daily"]
        and .paused == true' >/dev/null; then
    echo "PASS [krw-schedule] us_equity_krw_dbt: 환율 AND 시세 Asset 스케줄·일시정지"
else
    echo "FAIL [krw-schedule] us_equity_krw_dbt 스케줄이 기대와 다르다"
    rc=1
fi

# Cosmos 그룹은 모델 4개만 — stock_forecast 의 다른 모델(피처·라벨·뉴스)이 끌려오면 실패.
# 🔴 trading_calendar 포함: equity_prices_daily 의 relationships 테스트가 참조한다 — 빼면 달력이 낡아
#    새 거래일마다 테스트가 실패한다(컷오버 실행에서 255,207행 FAIL 실측)
if echo "${result}" | jq -e '.dags.us_equity_krw_dbt.tasks | keys as $k
        | (["trading_calendar", "equity_prices_daily", "fx_usdkrw_daily", "us_equity_krw__close_daily"]
           | all(. as $m | $k | any(contains($m))))
        and ($k | any(test("feature_store|labels_forward|news_|macro_")) | not)' >/dev/null; then
    echo "PASS [krw-models] Cosmos 그룹 = 모델 4개(다른 stock_forecast 모델 없음)"
else
    echo "FAIL [krw-models] Cosmos 그룹의 모델 집합이 기대와 다르다"
    rc=1
fi

# 🔴 두 모델에 걸친 테스트(relationships 시세→달력)는 전용 태스크가 돌린다 — cautious 는 부모가 **모두**
#    선택된 테스트만 고르는데 Cosmos 는 모델마다 `--select <모델>` 이라 이 테스트가 어느 태스크에도 안
#    잡힌다(dbt ls 실측 0건). 두 모델 run 뒤·원화 종가 run 앞에 둔다(낡은 달력 255,207행 FAIL 을 잡은 테스트)
if echo "${result}" | jq -e '.dags.us_equity_krw_dbt as $d
        | ($d.downstream["dbt.trading_calendar.run"] | index("dbt_cross_model_tests"))
        and ($d.downstream["dbt.equity_prices_daily.run"] | index("dbt_cross_model_tests"))
        and ($d.downstream["dbt_cross_model_tests"] | index("dbt.us_equity_krw__close_daily.run"))
        and $d.install_deps["dbt_cross_model_tests"] == false' >/dev/null; then
    echo "PASS [krw-cross-tests] [달력·시세 run] >> dbt_cross_model_tests >> 원화 종가 run"
else
    echo "FAIL [krw-cross-tests] 모델 간 테스트 태스크 배선이 기대와 다르다"
    rc=1
fi

# 🔴 테스트는 cautious — eager(dbt 기본)면 `--select trading_calendar` 가 달력을 참조하는 **다른 모델의**
#    테스트(뉴스·라벨·거시 등, Airflow 밖)까지 골라 TABLE_NOT_FOUND 로 실패한다(배포 후 실행 실측 10건)
if echo "${result}" | jq -e '.dags.us_equity_krw_dbt.indirect | to_entries
        | map(select(.key | endswith(".test"))) | length > 0 and all(.value == "cautious")' >/dev/null; then
    echo "PASS [krw-cautious] Cosmos 테스트 태스크 indirect selection = cautious"
else
    echo "FAIL [krw-cautious] Cosmos 테스트 태스크가 cautious 가 아니다"
    rc=1
fi

# 🔴 태스크 동시 1 — Celery 워커(1Gi)에서 dbt 태스크 여럿이 겹쳐 OOMKilled 됐다(컷오버 실측).
#    태스크 프로세스(Airflow·Cosmos 로드)가 태스크마다 떠서 dbt 자체(~130MB)보다 크다
if echo "${result}" | jq -e '.dags.us_equity_krw_dbt.max_active_tasks == 1' >/dev/null; then
    echo "PASS [krw-serial] us_equity_krw_dbt 태스크 동시 1(max_active_tasks)"
else
    echo "FAIL [krw-serial] us_equity_krw_dbt max_active_tasks 가 1 이 아니다"
    rc=1
fi

# 🔴 이미지에 구운 dbt_packages 를 쓴다 — Cosmos 기본값(install_dbt_deps=True)은 태스크마다 dbt deps 로
#    hub.getdbt.com 에 나가 런타임이 외부에 묶인다(최종 리뷰 I2)
if echo "${result}" | jq -e '.dags.us_equity_krw_dbt.install_deps | to_entries
        | map(select(.key | startswith("dbt."))) | length > 0 and all(.value == false)' >/dev/null; then
    echo "PASS [krw-no-deps] Cosmos dbt 태스크가 실행 시 dbt deps 를 받지 않는다"
else
    echo "FAIL [krw-no-deps] Cosmos dbt 태스크의 install_deps 가 false 가 아니다"
    rc=1
fi

if echo "${result}" | jq -e '.dags.us_equity_krw_dbt.outlets.record_latest_trade_date == ["iceberg://us_equity_krw/us_equity_krw__close_daily"]' >/dev/null; then
    echo "PASS [krw-outlet] record_latest_trade_date → iceberg://us_equity_krw/us_equity_krw__close_daily"
else
    echo "FAIL [krw-outlet] record_latest_trade_date 의 Asset outlet이 기대와 다르다"
    rc=1
fi

# --- 이미지 내용(원화 종가 이관 — pyiceberg·cosmos·dbt venv·dbt 프로젝트) -------------
# 🔴 lint CI 초록은 빌드 보증이 아니다 — 이미지 안에서 실제로 import·실행해 본다.
if "${CLI}" run --rm "${IMAGE}" python -c "import pyiceberg, cosmos, trino, holidays, lakehouse.iceberg" >/dev/null 2>&1; then
    echo "PASS [img-imports] pyiceberg·cosmos·trino·holidays·lakehouse import"
else
    echo "FAIL [img-imports] 이미지 안 import 실패"
    rc=1
fi

# 🔴 이미지 entrypoint 는 bash·python 이 아닌 첫 인자를 airflow 하위 명령으로 해석한다 — bash -c 로 감싼다
# 출력은 변수로 먼저 받는다 — `| grep -q` 는 첫 일치에서 끊어 앞 명령이 SIGPIPE 를 받고, pipefail 이
# 그것을 실패로 읽는다(값이 맞는데 FAIL 이 났다, 실측)
DBT_VERSION_OUT="$("${CLI}" run --rm "${IMAGE}" bash -c "/opt/dbt-venv/bin/dbt --version" 2>&1 || true)"
if printf '%s\n' "${DBT_VERSION_OUT}" | grep -E "trino: +1\.10\.6" >/dev/null; then
    echo "PASS [img-dbt] /opt/dbt-venv 의 dbt-trino 1.10.6"
else
    echo "FAIL [img-dbt] dbt venv 또는 dbt-trino 버전이 기대와 다르다"
    rc=1
fi

if "${CLI}" run --rm "${IMAGE}" bash -c "test -s /opt/airflow/dbt/dbt_pipelines/target/manifest.json"; then
    echo "PASS [img-manifest] dbt manifest 가 이미지에 구워져 있다"
else
    echo "FAIL [img-manifest] /opt/airflow/dbt/dbt_pipelines/target/manifest.json 이 없다"
    rc=1
fi

# DAG 파일이 공용 패키지를 import 할 수 있는가 — Dockerfile 의 PYTHONPATH 가 받친다(없으면 FAIL, 실측)
PROBE="${SCRIPT_DIR}/probe_lakehouse_import.py"
if "${CLI}" run --rm -v "${PROBE}:/tmp/probe.py:ro" "${IMAGE}" python /tmp/probe.py 2>/dev/null \
    | tail -n 1 | jq -e '.ok and .errors == 0' >/dev/null; then
    echo "PASS [img-dag-import-lib] DAG 파일에서 import lakehouse 가능"
else
    echo "FAIL [img-dag-import-lib] DAG 파일에서 lakehouse import 실패(PYTHONPATH 확인)"
    rc=1
fi

exit "${rc}"
