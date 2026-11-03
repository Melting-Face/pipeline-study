"""파티션 적재 멱등성(`lakehouse.iceberg`).

Dagster `test_frankfurter_fx_partition_load.py` 이식. 실인프라에 붙지 않는다 —
pyiceberg SQLite 카탈로그 + `tmp_path` warehouse.
🔴 음성 대조를 함께 둔다. append면 행이 두 배가 되는 것을 보여야
「멱등」이 의미를 갖는다.
"""

from datetime import datetime, timezone

import pyarrow as pa
import pytest
from lakehouse.fx import FX_SCHEMA, rates_json_to_arrow
from lakehouse.iceberg import (
    S3Conf,
    append_rows,
    catalog_from_conns,
    catalog_uri,
    ensure_table,
    latest_snapshot_property,
    make_catalog,
    overwrite_table,
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


class _FakeConn:
    """Airflow Connection 대역(duck typing)."""

    def __init__(self, attrs):
        self.login = attrs.get("login")
        self.password = attrs.get("password")
        self.host = attrs.get("host")
        self.port = attrs.get("port")
        self.schema = attrs.get("schema")
        self.extra_dejson = attrs.get("extra", {})


def test_catalog_from_conns_maps_fields(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "lakehouse.iceberg.load_catalog",
        lambda name, **props: captured.update(name=name, **props) or object(),
    )
    catalog_from_conns(
        _FakeConn({"login": "u", "password": "p@s", "host": "h", "schema": "iceberg"}),
        _FakeConn(
            {
                "login": "ak",
                "password": "sk",
                "extra": {"endpoint_url": "http://s3:8333"},
            }
        ),
    )
    # 포트 미지정이면 5432, 비밀번호는 인코딩
    assert captured["uri"] == "postgresql+psycopg2://u:p%40s@h:5432/iceberg"
    assert captured["s3.endpoint"] == "http://s3:8333"
    assert captured["s3.access-key-id"] == "ak"
    assert captured["s3.secret-access-key"] == "sk"


def test_catalog_from_conns_rejects_empty_s3_endpoint():
    with pytest.raises(ValueError, match="endpoint"):
        catalog_from_conns(
            _FakeConn(
                {"login": "u", "password": "p", "host": "h", "schema": "iceberg"}
            ),
            _FakeConn({"login": "ak", "password": "sk"}),
        )


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


# ── 전체 overwrite (KEV 같은 스냅샷 피드) ─────────────────────────────────
# Dagster `test_kev_overwrite_is_idempotent_and_keeps_lineage`·
# `test_full_replace_check_catches_append_variant` 이식. 합성 2열 스키마.

FULL_IDENTIFIER = "nvd.nvd_kev"
FULL_SCHEMA = pa.schema([("cve_id", pa.string()), ("fetched_at", pa.string())])


def _full_arrow(rows):
    return pa.table(
        {
            "cve_id": [f"CVE-SYN-{i}" for i in range(rows)],
            "fetched_at": ["2026-01-01"] * rows,
        },
        schema=FULL_SCHEMA,
    )


def _append_variant(table, arrow):
    """변이체 — `overwrite_table`의 overwrite를 append로 바꾼 것."""
    table.append(arrow)


def _assert_full_replace(tmp_path, loader):
    """두 번 적재하면 두 번째 내용만 남고 스냅샷 루트가 하나인가."""
    catalog = SqlCatalog(
        "probe",
        uri=f"sqlite:///{tmp_path}/catalog.db",
        warehouse=f"file://{tmp_path}/warehouse",
    )
    table = ensure_table(catalog, FULL_IDENTIFIER, FULL_SCHEMA)
    loader(table, _full_arrow(3))
    loader(table, _full_arrow(2))
    table.refresh()
    assert table.scan().to_arrow().num_rows == 2
    snapshots = table.metadata.snapshots
    assert len(snapshots) > 1
    assert len([s for s in snapshots if s.parent_snapshot_id is None]) == 1


def test_overwrite_table_is_idempotent_and_keeps_lineage(tmp_path):
    _assert_full_replace(tmp_path, overwrite_table)


def test_full_replace_check_catches_append_variant(tmp_path):
    # 음성 대조 — 같은 판정이 append 변이체는 잡는다(둔감하지 않다)
    with pytest.raises(AssertionError):
        _assert_full_replace(tmp_path, _append_variant)


# ── append + 스냅샷 속성 (GeekNews 같은 누적 피드) ────────────────────────


@pytest.fixture
def append_table(tmp_path):
    catalog = SqlCatalog(
        "probe",
        uri=f"sqlite:///{tmp_path}/catalog.db",
        warehouse=f"file://{tmp_path}/warehouse",
    )
    return ensure_table(catalog, FULL_IDENTIFIER, FULL_SCHEMA)


def test_append_rows_accumulates(append_table):
    meta = append_rows(append_table, _full_arrow(2))
    assert meta["rows"] == 2
    assert isinstance(meta["snapshot_id"], int)
    append_rows(append_table, _full_arrow(2))
    append_table.refresh()
    assert append_table.scan().to_arrow().num_rows == 4


def test_latest_snapshot_property_walks_back(append_table):
    assert latest_snapshot_property(append_table, "k") is None
    append_rows(append_table, _full_arrow(1), {"k": "a"})
    assert latest_snapshot_property(append_table, "k") == "a"
    # 속성 없는 최신 스냅샷이 끼어도 과거로 거슬러 올라간다
    append_rows(append_table, _full_arrow(1))
    assert latest_snapshot_property(append_table, "k") == "a"
    append_rows(append_table, _full_arrow(1), {"k": "b"})
    assert latest_snapshot_property(append_table, "k") == "b"
