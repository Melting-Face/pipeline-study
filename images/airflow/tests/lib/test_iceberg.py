"""파티션 적재 멱등성(`lakehouse.iceberg`).

Dagster `test_frankfurter_fx_partition_load.py` 이식. 실인프라에 붙지 않는다 —
pyiceberg SQLite 카탈로그 + `tmp_path` warehouse.
🔴 음성 대조를 함께 둔다. append면 행이 두 배가 되는 것을 보여야
「멱등」이 의미를 갖는다.
"""

from datetime import datetime, timezone

import pytest
from lakehouse.fx import FX_SCHEMA, rates_json_to_arrow
from lakehouse.iceberg import (
    S3Conf,
    catalog_uri,
    ensure_table,
    make_catalog,
    replace_partition,
)
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import EqualTo

INGESTED_AT = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)
IDENTIFIER = "frankfurter_fx.fx_rates_daily"

# 🔴 합성값이다 — 원천 데이터를 저장소에 담지 않는다.
TUESDAY = {
    "amount": 1.0,
    "base": "EUR",
    "date": "2026-09-01",
    "rates": {"KRW": 1500.0, "USD": 1.10},
}
SATURDAY = {
    "amount": 1.0,
    "base": "EUR",
    "date": "2026-09-04",
    "rates": {"KRW": 1450.0, "USD": 1.15},
}


@pytest.fixture
def table(tmp_path):
    catalog = SqlCatalog(
        "probe",
        uri=f"sqlite:///{tmp_path}/catalog.db",
        warehouse=f"file://{tmp_path}/warehouse",
    )
    return ensure_table(catalog, IDENTIFIER, FX_SCHEMA)


def _count(table, partition):
    return table.scan(row_filter=EqualTo("rate_date", partition)).to_arrow().num_rows


def test_replace_is_idempotent(table):
    arrow = rates_json_to_arrow(TUESDAY, "2026-09-01", INGESTED_AT)
    replace_partition(table, arrow, "rate_date", "2026-09-01")
    meta = replace_partition(table, arrow, "rate_date", "2026-09-01")
    assert _count(table, "2026-09-01") == 2
    assert meta["rows"] == 2


def test_append_doubles_negative_control(table):
    arrow = rates_json_to_arrow(TUESDAY, "2026-09-01", INGESTED_AT)
    table.append(arrow)
    table.append(arrow)
    assert _count(table, "2026-09-01") == 4


def test_replace_isolates_partitions(table):
    replace_partition(
        table,
        rates_json_to_arrow(TUESDAY, "2026-09-01", INGESTED_AT),
        "rate_date",
        "2026-09-01",
    )
    replace_partition(
        table,
        rates_json_to_arrow(SATURDAY, "2026-09-05", INGESTED_AT),
        "rate_date",
        "2026-09-05",
    )
    replace_partition(
        table,
        rates_json_to_arrow(TUESDAY, "2026-09-01", INGESTED_AT),
        "rate_date",
        "2026-09-01",
    )
    assert _count(table, "2026-09-01") == 2
    assert _count(table, "2026-09-05") == 2


def test_lineage_has_single_root(table):
    arrow = rates_json_to_arrow(TUESDAY, "2026-09-01", INGESTED_AT)
    replace_partition(table, arrow, "rate_date", "2026-09-01")
    replace_partition(table, arrow, "rate_date", "2026-09-01")
    table.refresh()
    roots = [s for s in table.snapshots() if s.parent_snapshot_id is None]
    assert len(roots) == 1


def test_quote_in_partition_value_fails_closed(table):
    arrow = rates_json_to_arrow(TUESDAY, "2026-09-01", INGESTED_AT)
    with pytest.raises(ValueError, match="작은따옴표"):
        replace_partition(table, arrow, "rate_date", "2026-09-01' or '1'='1")


def test_s3conf_rejects_empty_values():
    # Review Focus 5 — Connection이 비면 빈 키로 접속하지 않는다
    with pytest.raises(ValueError, match="access_key"):
        S3Conf("http://seaweedfs.default.svc:8333", "", "dummy")


def test_make_catalog_properties(monkeypatch):
    captured = {}

    def fake_load_catalog(name, **props):
        captured.update(name=name, **props)
        return object()

    monkeypatch.setattr("lakehouse.iceberg.load_catalog", fake_load_catalog)
    make_catalog(
        "postgresql+psycopg2://u:p@h:5432/iceberg", S3Conf("http://s3:8333", "ak", "sk")
    )
    assert captured["name"] == "iceberg"  # 🔴 전 엔진 공통 카탈로그 이름
    assert captured["type"] == "sql"
    assert captured["warehouse"] == "s3://warehouse"
    assert captured["s3.path-style-access"] == "true"
    assert captured["s3.endpoint"] == "http://s3:8333"


def test_catalog_uri_quotes_password():
    # 비밀번호의 @ : / 가 그대로 들어가면 URI가 다른 호스트로 해석된다
    uri = catalog_uri(
        "iceberg", "p@ss:w/rd", "catalog-postgres-rw.default.svc", 5432, "iceberg"
    )
    assert uri == (
        "postgresql+psycopg2://iceberg:p%40ss%3Aw%2Frd"
        "@catalog-postgres-rw.default.svc:5432/iceberg"
    )


def test_catalog_uri_rejects_empty_password():
    with pytest.raises(ValueError, match="iceberg_catalog"):
        catalog_uri("iceberg", "", "h", 5432, "iceberg")
