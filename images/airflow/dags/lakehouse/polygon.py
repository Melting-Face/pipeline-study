"""Polygon grouped daily 수집·파서.

Dagster `common/polygon.py`·`defs/polygon_market/assets.py` 이식.
🔴 인증은 `Authorization: Bearer` 헤더다. 쿼리 파라미터로 보내면 실패 예외의
URL에 키가 실린다.
🔴 `OHLCV_SCHEMA`는 Dagster 쪽과 **같아야 한다**(`test_parity.py`가 대조).
"""

from datetime import datetime, timezone
from typing import Any

import pyarrow as pa

from lakehouse.http import request_json

BASE_URL = "https://api.polygon.io"
GROUPED_DAILY_PATH = "/v2/aggs/grouped/locale/us/market/stocks"

# 없으면 레코드가 성립하지 않는 필드(KeyError로 드러낸다)
REQUIRED_BAR_FIELDS = ("T", "o", "h", "l", "c", "v", "t")

OHLCV_SCHEMA = pa.schema(
    [
        ("trade_date", pa.string()),
        # 응답 bar의 `t`(epoch ms)에서 유도한 UTC 날짜. trade_date와 다르면 원천의 대체
        # 응답이다.
        ("source_date", pa.string()),
        # `t`는 16:00 America/New_York 집계 창 표식이다(조기 폐장일에도 같다 — 실제
        # 마감이 아니다)
        ("bar_timestamp", pa.timestamp("us", tz="UTC")),
        ("ticker", pa.string()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("vwap", pa.float64()),
        ("trade_count", pa.int64()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


def fetch_grouped_daily(
    api_key: str, trade_date: str, *, timeout_s: int = 30, retries: int = 3
) -> dict[str, Any]:
    """하루치 전 미국 티커 OHLCV를 받는다. 빈 키면 호출하지 않고 실패한다."""
    if not api_key:
        message = "Polygon API 키가 비었다 — Connection `polygon_api`를 확인하라"
        raise ValueError(message)
    return request_json(
        f"{BASE_URL}{GROUPED_DAILY_PATH}/{trade_date}",
        params={"adjusted": "true"},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout_s=timeout_s,
        retries=retries,
    )


def _epoch_ms_to_date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).date().isoformat()


def grouped_to_arrow(
    payload: dict[str, Any], trade_date: str, ingested_at: datetime
) -> pa.Table:
    """bar를 티커당 1행으로 편다.

    휴장일(`results` 부재·빈 배열)은 에러가 아니라 0행이다.
    """
    rows = [
        {
            "trade_date": trade_date,
            "source_date": _epoch_ms_to_date(bar["t"]),
            "bar_timestamp": datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc),
            "ticker": bar["T"],
            "open": float(bar["o"]),
            "high": float(bar["h"]),
            "low": float(bar["l"]),
            "close": float(bar["c"]),
            "volume": float(bar["v"]),
            "vwap": None if bar.get("vw") is None else float(bar["vw"]),
            "trade_count": None if bar.get("n") is None else int(bar["n"]),
            "ingested_at": ingested_at,
        }
        for bar in sorted(payload.get("results") or [], key=lambda b: b["T"])
    ]
    return pa.Table.from_pylist(rows, schema=OHLCV_SCHEMA)
