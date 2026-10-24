"""Frankfurter 환율 bronze 스키마·파서(Dagster `defs/frankfurter_fx/assets.py` 이식).

🔴 `FX_SCHEMA`는 Dagster 쪽과 **같아야 한다**(같은 테이블에 쓴다).
`test_parity.py`가 대조한다.
"""

from datetime import datetime
from typing import Any

import pyarrow as pa

RATES_BASE = "https://api.frankfurter.dev/v1"

FX_SCHEMA = pa.schema(
    [
        # 우리가 요청한 날짜 = 파티션 키. 파티션 교체의 필터 대상이다.
        ("rate_date", pa.string()),
        # 원천이 응답에 에코한 날짜. 주말·휴일 요청이면 rate_date와 다르다.
        ("source_date", pa.string()),
        ("base_currency", pa.string()),
        ("quote_currency", pa.string()),
        ("rate", pa.float64()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


def rates_json_to_arrow(
    payload: dict[str, Any], rate_date: str, ingested_at: datetime
) -> pa.Table:
    """응답을 통화당 1행으로 편다.

    `date`·`base`가 없으면 KeyError로 드러낸다(조용히 채우지 않는다).
    """
    source_date = payload["date"]
    base_currency = payload["base"]
    rows = [
        {
            "rate_date": rate_date,
            "source_date": source_date,
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "rate": float(rate),
            "ingested_at": ingested_at,
        }
        # 정렬해 담는다 — 재수집해도 행 순서가 흔들리지 않는다
        for quote_currency, rate in sorted((payload.get("rates") or {}).items())
    ]
    return pa.Table.from_pylist(rows, schema=FX_SCHEMA)
