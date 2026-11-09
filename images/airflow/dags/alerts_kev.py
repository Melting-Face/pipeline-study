"""관심 제품 신규 KEV — 알림 gold가 갱신될 때 본인 Discord 웹훅으로 보낸다.

🔴 외부 발신이다 — docs/risk.md §4 예외 ②가 정본이다. 로직은 `lakehouse/alerts.py`
(통제 배선 파일)에 있고 이 파일은 배선만 한다.
- 스케줄은 `alerts_dbt`가 내는 gold Asset이다(`ALERTS_GOLD_URI`).
- 후보는 `nvd.nvd__cve_digest`에서 `is_kev`이면서 관심목록과 일치하는 행이다.
  관심목록(env `ALERTS_WATCHLIST_PATH`)이 없거나 비면 **실패**한다 — 목록 없이
  KEV 전체를 보내지 않는다.
- 시드 표지에 관심목록 정규화 해시가 들어간다. 표지가 없으면(처음 켤 때·
  관심목록이 바뀐 뒤) 기록에 없는 일치 행을 baseline으로 시드하고 보내지 않는다
  — 목록에 제품을 더한 순간 그 제품의 기존 KEV가 한꺼번에 나가지 않게 한다.
  해시는 표지 키에만 있고 목록 원문은 기록·로그에 남지 않는다.
- 일일 상한은 `alerts_digest`와 합산이다. 짝 없는 pending이 있으면 실패한다.
- Connection `discord_webhook`은 이 DAG와 `alerts_digest`만 쓴다.
  실패 콜백 알림에 쓰지 않는다.
- Pool·재시도 0·로그 규칙은 `alerts_digest.py`와 같다.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import pendulum
from airflow.sdk import Asset, Connection, dag, task
from lakehouse.assets import ALERTS_GOLD_URI

log = logging.getLogger(__name__)

# 🔴 description은 고르지 않는다(gold에도 없다)
KEV_SQL = """
select
    cve_id,
    cvss_score,
    cvss_severity,
    is_kev,
    kev_vendor_project,
    kev_product,
    cpe_vendor_products
from nvd.nvd__cve_digest
where is_kev
"""


@dag(
    schedule=[Asset(ALERTS_GOLD_URI)],
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    default_args={"retries": 0},
    tags=["alerts", "discord"],
)
def alerts_kev() -> None:
    """관심 제품 신규 KEV 발신 DAG."""

    @task(pool="discord_webhook")
    def send_alerts_kev() -> dict[str, Any]:
        """관심목록과 일치하는 KEV 중 기록에 없는 것을 상한까지 보내고 기록한다."""
        import os
        from functools import partial

        from lakehouse.alerts import (
            KIND_KEV,
            SEND_LOG_IDENTIFIER,
            SEND_LOG_SCHEMA,
            build_candidates,
            kev_candidate,
            load_watchlist,
            mask_webhook,
            run_send,
            seed_key,
            validate_webhook_url,
            watchlist_digest,
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
        if not watchlist_path:
            message = "env ALERTS_WATCHLIST_PATH가 비었다 — KEV 발신은 관심목록 필수"
            raise ValueError(message)
        watchlist = load_watchlist(watchlist_path)

        rows = query_rows(KEV_SQL)
        candidates, counts = build_candidates(
            rows, kev_candidate, watchlist, watchlist_only=True
        )
        log.info(
            "KEV %d행 · 관심목록 일치 %d · 후보 %d건 · 검증 탈락 %d",
            counts["rows"],
            counts["matched"],
            len(candidates),
            counts["rejected"],
        )

        catalog = catalog_from_conns(
            Connection.get("iceberg_catalog"), Connection.get("seaweedfs_s3")
        )
        table = ensure_table(catalog, SEND_LOG_IDENTIFIER, SEND_LOG_SCHEMA)
        result = run_send(
            KIND_KEV,
            candidates,
            table=table,
            post=partial(post_json, webhook, mask=mask_webhook),
            now=lambda: datetime.now(tz=timezone.utc),
            # 관심목록이 바뀌면 표지가 새 키가 되어 다시 시드한다(해시만 — 원문 없음)
            seed=seed_key(KIND_KEV, watchlist_digest(watchlist)),
        )
        result.update(
            gold_rows=counts["rows"],
            invalid_rows=counts["rejected"],
            watchlist_matched=counts["matched"],
        )
        log.info("발신 결과(건수만): %s", result)
        return result

    send_alerts_kev()


alerts_kev()
