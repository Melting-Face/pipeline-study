"""GitHub 트렌드(근사)를 Search API로 수집해 S3(SeaweedFS)에 raw JSON으로 남기는 DAG.

⚠️ 「트렌드」는 근사다 — github.com/trending 의 "기간 내 받은 별"이 아니라
"최근 LOOKBACK_DAYS 일 안에 생성된 저장소를 별 수로 정렬"한 결과다
(Trending 페이지는 공식 API가 없다).
"""

import json
import logging
from datetime import timedelta

import pendulum
import requests
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.sdk import Asset, Variable, dag, get_current_context, task

logger = logging.getLogger(__name__)

# 타임존 정책: 스케줄·파티션 날짜는 KST, 수집 시각 기록은 UTC.
KST = pendulum.timezone("Asia/Seoul")

SEARCH_URL = "https://api.github.com/search/repositories"
LOOKBACK_DAYS = 7
PER_PAGE = 100  # Search API 페이지 상한 — 1회 호출로 끝낸다
REQUEST_TIMEOUT_SEC = 30

# 저장 위치 — 기존 warehouse 버킷(스택 A 소유)을 쓴다.
# 새 버킷은 terraform apply 가 필요하다.
# Connection 은 env AIRFLOW_CONN_SEAWEEDFS_S3 (Secret airflow-conn-seaweedfs)
S3_CONN_ID = "seaweedfs_s3"
BUCKET = "warehouse"
PREFIX = "raw/github_trending"
RAW_ASSET = Asset(f"s3://{BUCKET}/{PREFIX}")

# 원천 응답에서 남길 필드(그 밖은 버린다)
KEEP_FIELDS = (
    "full_name",
    "html_url",
    "description",
    "language",
    "stargazers_count",
    "forks_count",
    "created_at",
    "topics",
)


@dag(
    dag_id="github_trending",
    start_date=pendulum.datetime(2026, 10, 1, tz="Asia/Seoul"),
    schedule="0 9 * * *",  # 매일 09:00 KST
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "retry_exponential_backoff": True,
    },
    tags=["study", "github"],
)
def github_trending() -> None:
    """GitHub Search API → s3://warehouse/raw/github_trending/dt=<날짜>/repos.json."""

    @task
    def fetch_trending() -> dict:
        # 기준 날짜는 실행 시각이 아니라 run 의 run_after(KST)
        # — 재실행해도 같은 파티션을 본다
        # run_after 는 stdlib datetime 일 수 있어 pendulum 으로 감싼 뒤 변환한다
        run_after = get_current_context()["dag_run"].run_after
        run_date = pendulum.instance(run_after).in_timezone(KST).date()
        since = run_date - timedelta(days=LOOKBACK_DAYS)
        query = f"created:>={since.isoformat()}"

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # 토큰은 선택 — 일 1회 호출이라 비인증 한도로 충분하다. 있으면 한도만 올라간다
        token = Variable.get("github_token", default=None)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = requests.get(
            SEARCH_URL,
            params={"q": query, "sort": "stars", "order": "desc", "per_page": PER_PAGE},
            headers=headers,
            timeout=REQUEST_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        body = resp.json()

        items = [{k: repo.get(k) for k in KEEP_FIELDS} for repo in body["items"]]
        # 0건은 "트렌드 없음"이 아니라 쿼리·원천 이상이다
        # — 빈 파일로 성공 처리하지 않는다
        if not items:
            msg = f"Search API 결과 0건 (query={query!r})"
            raise ValueError(msg)

        logger.info(
            "수집 %d건 (total_count=%s, query=%r)",
            len(items),
            body.get("total_count"),
            query,
        )
        return {
            "dt": run_date.isoformat(),
            "query": query,
            "collected_at": pendulum.now("UTC").isoformat(),
            "total_count": body.get("total_count"),
            "incomplete_results": body.get("incomplete_results"),
            "items": items,
        }

    @task(outlets=[RAW_ASSET])
    def write_raw(payload: dict) -> str:
        # dt= 파티션을 덮어쓴다(replace=True) — 같은 run 재실행은 멱등
        key = f"{PREFIX}/dt={payload['dt']}/repos.json"
        S3Hook(aws_conn_id=S3_CONN_ID).load_string(
            json.dumps(payload, ensure_ascii=False),
            key=key,
            bucket_name=BUCKET,
            replace=True,
        )
        uri = f"s3://{BUCKET}/{key}"
        logger.info("저장 %s (%d건)", uri, len(payload["items"]))
        return uri

    write_raw(fetch_trending())


github_trending()
