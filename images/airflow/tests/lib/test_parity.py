"""bronze 스키마 고정(설계 F3 — 이미 적재된 테이블과의 갈라짐 방지).

이관 중에는 Dagster 정의와 대조했다(두 오케스트레이터가 같은 테이블에 썼다).
컷오버로 Dagster 정의가 삭제됐으므로(계획 Task 8) **스키마 리터럴과 비교**한다.

🔴 리터럴은 **이미 Iceberg에 적재된 테이블의 모양**이다. 코드 쪽 스키마만 바꾸면
파티션 교체가 기존 스냅샷과 다른 모양으로 쓰거나 append가 타입 불일치로 터진다 —
바꾸려면 이 리터럴과 테이블 마이그레이션을 **함께** 결정한다.
비교는 필드명·순서·타입(단위·tz 포함)·nullable·메타데이터 전부다.
"""

import pyarrow as pa
from lakehouse import fx, polygon

# frankfurter_fx.fx_rates_daily
FX_SCHEMA_PINNED = pa.schema(
    [
        ("rate_date", pa.string()),
        ("source_date", pa.string()),
        ("base_currency", pa.string()),
        ("quote_currency", pa.string()),
        ("rate", pa.float64()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)

# polygon_market.equity_ohlcv_daily
OHLCV_SCHEMA_PINNED = pa.schema(
    [
        ("trade_date", pa.string()),
        ("source_date", pa.string()),
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


def _assert_pinned(actual: pa.Schema, pinned: pa.Schema) -> None:
    """스키마가 리터럴과 같다. 실패 시 어느 필드가 갈렸는지 보이게 한다."""
    assert actual.names == pinned.names, (
        f"필드명·순서: {actual.names} != {pinned.names}"
    )
    diff = [
        (a.name, str(a.type), a.nullable, str(p.type), p.nullable)
        for a, p in zip(actual, pinned, strict=True)
        if not a.equals(p, check_metadata=True)
    ]
    assert diff == [], (
        f"갈린 필드(이름, 실제 타입·nullable, 고정 타입·nullable): {diff}"
    )
    assert actual.equals(pinned, check_metadata=True)


def test_fx_schema_is_pinned():
    _assert_pinned(fx.FX_SCHEMA, FX_SCHEMA_PINNED)


def test_ohlcv_schema_is_pinned():
    _assert_pinned(polygon.OHLCV_SCHEMA, OHLCV_SCHEMA_PINNED)


def test_timestamps_are_tz_aware_utc():
    """🔴 tz는 타입 비교에 포함되지만, 의도(저장은 UTC)를 독립 셀로도 박는다."""
    for schema in (fx.FX_SCHEMA, polygon.OHLCV_SCHEMA):
        for field in schema:
            if pa.types.is_timestamp(field.type):
                assert field.type.tz == "UTC", field.name
                assert field.type.unit == "us", field.name
