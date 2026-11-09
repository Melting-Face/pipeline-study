"""GeekNews·CVE 다이제스트 — 매일 08:30 KST에 새 항목을 본인 Discord 웹훅으로 보낸다.

🔴 외부 발신이다 — docs/risk.md §4 예외 ②가 정본이다. 로직은 `lakehouse/alerts.py`
(통제 배선 파일)에 있고 이 파일은 배선만 한다.
- 후보는 `geeknews.geeknews__cve_links` **전 행**이다. 관심목록 일치 행을 앞에
  정렬한다. 관심목록은 env `ALERTS_WATCHLIST_PATH`의 파일이다.
  - 🔴 배포(차트)에서는 이 env가 **항상 설정**돼 Secret 마운트 파일을 가리킨다.
    Secret이 비었거나 형식이 틀리면 `load_watchlist`가 ValueError를 내고
    **이 DAG도 실패한다**(fail-closed). 「목록 없이 돈다」가 아니다.
  - env 자체가 없을 때(차트 밖 로컬 실행)만 정렬을 건너뛰고 돈다(건수를 로그에 남긴다).
- 기록 테이블 `alerts.discord_sends`에 이 kind의 시드 표지가 없으면 baseline
  시드만 하고 보내지 않는다. 일일 상한은 두 DAG 합산이다(`lakehouse.alerts`).
- 짝 없는 pending(발신 여부를 모르는 항목)이 남아 있으면 보내지 않고 실패한다.
- Connection `discord_webhook`은 이 DAG와 `alerts_kev`만 쓴다.
  실패 콜백 알림에 쓰지 않는다.
- 발신 태스크는 Pool `discord_webhook`(slots 1)에 넣는다. 두 DAG의 기록 조회 →
  발신 → 기록이 겹치면 상한·중복 판정이 서로를 못 본다.
- 재시도 0 — 401·403·404를 반복하면 Discord가 제한을 건다. 5xx·429 재시도는
  `post_json` 안에서 한다.
- 원천 값(제목·링크)·웹훅 URL은 로그·XCom에 남기지 않는다 — 건수와 키 해시 접두만.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import pendulum
from airflow.sdk import Connection, CronTriggerTimetable, dag, task

log = logging.getLogger(__name__)

# 글이 언급한 CVE 전 행 + digest의 KEV 벤더/제품·CPE(관심목록 정렬용).
# 🔴 description은 고르지 않는다(gold에도 없다)
DIGEST_SQL = """
select
    l.topic_id,
    l.title,
    l.link,
    l.cve_id,
    l.cvss_score,
    l.cvss_severity,
    l.is_kev,
    d.kev_vendor_project,
    d.kev_product,
    d.cpe_vendor_products
from geeknews.geeknews__cve_links as l
left join nvd.nvd__cve_digest as d
    on l.cve_id = d.cve_id
"""


@dag(
    schedule=CronTriggerTimetable("30 8 * * *", timezone="Asia/Seoul"),
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    default_args={"retries": 0},
    tags=["alerts", "discord"],
)
def alerts_digest() -> None:
    """GeekNews·CVE 다이제스트 발신 DAG."""

    @task(pool="discord_webhook")
    def send_alerts_digest() -> dict[str, Any]:
        """Gold를 읽어 기록에 없는 항목을 상한까지 보내고 기록한다."""
        import os
        from functools import partial

        from lakehouse.alerts import (
            KIND_DIGEST,
            SEND_LOG_IDENTIFIER,
            SEND_LOG_SCHEMA,
            build_candidates,
            digest_candidate,
            load_watchlist,
            mask_webhook,
            run_send,
            validate_webhook_url,
        )
        from lakehouse.http import post_json
        from lakehouse.iceberg import catalog_from_conns, ensure_table
        from lakehouse.trino import query_rows

        # 🔴 fail-closed — 비었거나 모양이 다르면 보내지 않는다
        webhook = Connection.get("discord_webhook").password or ""
        if not webhook:
            message = "Connection discord_webhook의 password(웹훅 URL)가 비었다"
            raise ValueError(message)
        validate_webhook_url(webhook)

        watchlist_path = os.environ.get("ALERTS_WATCHLIST_PATH", "")
        watchlist = load_watchlist(watchlist_path) if watchlist_path else None

        rows = query_rows(DIGEST_SQL)
        candidates, counts = build_candidates(
            rows, digest_candidate, watchlist, watchlist_only=False
        )
        if watchlist is None:
            log.info("관심목록 없음 — 정렬 생략, 후보 %d건", len(candidates))
        log.info(
            "gold %d행 · 후보 %d건 · 관심목록 일치 %d · 검증 탈락 %d",
            counts["rows"],
            len(candidates),
            counts["matched"],
            counts["rejected"],
        )

        catalog = catalog_from_conns(
            Connection.get("iceberg_catalog"), Connection.get("seaweedfs_s3")
        )
        table = ensure_table(catalog, SEND_LOG_IDENTIFIER, SEND_LOG_SCHEMA)
        result = run_send(
            KIND_DIGEST,
            candidates,
            table=table,
            post=partial(post_json, webhook, mask=mask_webhook),
            now=lambda: datetime.now(tz=timezone.utc),
        )
        result.update(
            gold_rows=counts["rows"],
            invalid_rows=counts["rejected"],
            watchlist_matched=counts["matched"],
            watchlist_loaded=watchlist is not None,
        )
        log.info("발신 결과(건수만): %s", result)
        return result

    send_alerts_digest()


alerts_digest()
