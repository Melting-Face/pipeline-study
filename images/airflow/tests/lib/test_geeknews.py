"""GeekNews Atom 피드 파서·bronze 스키마(`lakehouse.geeknews`).

피드 fixture는 합성이다(entry 모양만 실제 피드를 따르고 값은 지어냈다).
네트워크에 붙지 않는다.
"""

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import defusedxml
import pytest
import yaml
from botocore.exceptions import ClientError
from lakehouse.assets import GEEKNEWS_ITEMS_URI
from lakehouse.geeknews import (
    FEED_DIGEST_PROPERTY,
    GEEKNEWS_SCHEMA,
    IDENTIFIER,
    MAX_FEED_BYTES,
    RAW_BUCKET,
    USER_AGENT,
    extract_cve_ids,
    ingest_feed,
    parse_atom_timestamp,
    parse_feed,
    raw_key,
    strip_tracking_params,
    topic_id_from_entry_id,
)
from lakehouse.iceberg import ensure_table, latest_snapshot_property
from lakehouse.s3raw import gzip_sha256, read_verified_gzip
from pyiceberg.catalog.sql import SqlCatalog

FEED_XML = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns='http://www.w3.org/2005/Atom'>
  <title>GeekNews</title>
  <entry>
    <title><![CDATA[합성 글 하나]]></title>
    <link rel='alternate' type='text/html' href='https://news.hada.io/topic?id=36530&amp;utm_source=geeknews&amp;utm_medium=rss&amp;utm_campaign=news' />
    <id>https://news.hada.io/topic?id=36530</id>
    <updated>2026-11-04T00:02:40+09:00</updated>
    <published>2026-11-04T00:02:40+09:00</published>
    <author><name>someone</name><uri>https://news.hada.io/user/someone</uri></author>
    <content type='html' xml:lang='ko'><![CDATA[<p>cve-2026-12345 와 CVE-2026-12345, CVE-2025-0001, CVE-26-1</p>]]></content>
  </entry>
  <entry>
    <title><![CDATA[합성 글 둘]]></title>
    <link rel='alternate' type='text/html' href='https://news.hada.io/topic?id=36531&amp;utm_source=geeknews' />
    <id>https://news.hada.io/topic?id=36531</id>
    <updated>2026-11-03T10:00:00Z</updated>
    <published>2026-11-03T09:00:00+09:00</published>
  </entry>
</feed>
""".encode()  # noqa: E501 - 실제 피드 모양의 긴 줄을 그대로 둔다

FETCHED_AT = datetime(2026, 11, 4, 1, 2, 3, tzinfo=timezone.utc)

_ENTRY = (
    "<entry><title>t</title><link rel='alternate' href='https://x.example/a'/>"
    "<id>https://news.hada.io/topic?id={id}</id>"
    "<updated>2026-11-03T10:00:00Z</updated><published>2026-11-03T10:00:00Z</published></entry>"
)


def _feed(entries: str) -> bytes:
    return f"<feed xmlns='http://www.w3.org/2005/Atom'>{entries}</feed>".encode()


def test_parse_feed_rows_and_schema():
    table = parse_feed(FEED_XML, FETCHED_AT)
    assert table.schema.equals(GEEKNEWS_SCHEMA)
    assert table.num_rows == 2
    assert table.column("topic_id").to_pylist() == [36530, 36531]


def test_link_without_rel_counts_as_alternate():
    # RFC 4287 §4.2.7.2: rel이 없으면 alternate
    body = _feed(_ENTRY.replace(" rel='alternate'", "").format(id=1))
    assert parse_feed(body, FETCHED_AT).column("link").to_pylist() == [
        "https://x.example/a"
    ]


def test_identifier_contract_with_asset_uri_and_dbt_source():
    assert "iceberg://" + IDENTIFIER.replace(".", "/") == GEEKNEWS_ITEMS_URI
    source_yml = (
        Path(__file__).resolve().parents[4]
        / "dagster/dockerfile.d/src/dbt_pipelines/models/geeknews/source.yml"
    )
    source = yaml.safe_load(source_yml.read_text())["sources"][0]
    assert f"{source['schema']}.{source['tables'][0]['name']}" == IDENTIFIER


def test_parse_feed_has_no_body_or_author():
    table = parse_feed(FEED_XML, FETCHED_AT)
    assert set(table.column_names) == {
        "topic_id",
        "title",
        "link",
        "published",
        "updated",
        "cve_ids",
        "fetched_at",
    }


def test_strip_tracking_params():
    assert (
        strip_tracking_params(
            "https://news.hada.io/topic?id=36530&utm_source=geeknews&utm_medium=rss&utm_campaign=news"
        )
        == "https://news.hada.io/topic?id=36530"
    )
    assert (
        strip_tracking_params("https://example.com/a?x=1&utm_source=y#f")
        == "https://example.com/a?x=1#f"
    )
    assert strip_tracking_params("https://example.com/a") == "https://example.com/a"


def test_topic_id_from_entry_id():
    assert topic_id_from_entry_id("https://news.hada.io/topic?id=36530") == 36530
    with pytest.raises(ValueError, match="topic"):
        topic_id_from_entry_id("https://news.hada.io/topic")
    with pytest.raises(ValueError, match="topic"):
        topic_id_from_entry_id("https://news.hada.io/topic?id=abc")


def test_extract_cve_ids_normalizes():
    html = "<p>cve-2026-12345 와 CVE-2026-12345, CVE-2025-0001, CVE-26-1</p>"
    assert extract_cve_ids(html) == ["CVE-2025-0001", "CVE-2026-12345"]
    assert extract_cve_ids(None) == []
    assert extract_cve_ids("CVE-2026-123") == []


def test_cve_ids_in_parsed_table():
    table = parse_feed(FEED_XML, FETCHED_AT)
    assert table.column("cve_ids").to_pylist() == [
        ["CVE-2025-0001", "CVE-2026-12345"],
        [],
    ]


def test_link_has_no_tracking_params_in_table():
    table = parse_feed(FEED_XML, FETCHED_AT)
    assert table.column("link").to_pylist() == [
        "https://news.hada.io/topic?id=36530",
        "https://news.hada.io/topic?id=36531",
    ]


def test_timestamps_are_utc():
    assert parse_atom_timestamp("2026-11-04T00:02:40+09:00") == datetime(
        2026, 11, 3, 15, 2, 40, tzinfo=timezone.utc
    )
    assert parse_atom_timestamp("2026-11-03T10:00:00Z") == datetime(
        2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc
    )
    with pytest.raises(ValueError, match="오프셋"):
        parse_atom_timestamp("2026-11-04T00:02:40")


def test_parse_feed_rejects_dtd():
    body = b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "x">]>' + _feed(
        _ENTRY.format(id=1)
    )
    with pytest.raises(defusedxml.DTDForbidden):
        parse_feed(body, FETCHED_AT)


def test_parse_feed_rejects_non_atom_root():
    with pytest.raises(ValueError, match="Atom"):
        parse_feed(b"<html><body/></html>", FETCHED_AT)


def test_parse_feed_rejects_empty_feed():
    with pytest.raises(ValueError, match="0건"):
        parse_feed(_feed(""), FETCHED_AT)


def test_parse_feed_rejects_duplicate_topic():
    with pytest.raises(ValueError, match="중복"):
        parse_feed(_feed(_ENTRY.format(id=7) + _ENTRY.format(id=7)), FETCHED_AT)


def test_parse_feed_rejects_oversize():
    with pytest.raises(ValueError, match="상한"):
        parse_feed(b" " * (MAX_FEED_BYTES + 1), FETCHED_AT)


def test_raw_key_layout():
    expected = "raw/geeknews/feed/dt=2026-11-04/fetched=20261104T010203Z/feed.xml.gz"
    assert raw_key(FETCHED_AT) == expected
    kst = datetime.fromisoformat("2026-11-04 10:02:03+09:00")
    assert raw_key(kst) == expected
    with pytest.raises(ValueError, match="tz"):
        raw_key(FETCHED_AT.replace(tzinfo=None))


def test_user_agent_is_identifiable():
    assert "pipeline-study" in USER_AGENT
    for token in ("GPTBot", "ClaudeBot", "CCBot"):
        assert token not in USER_AGENT


def test_parse_feed_rejects_naive_fetched_at():
    # silver dedup이 fetched_at을 키로 쓰므로 호스트 로컬 시각으로 해석하지 않는다
    with pytest.raises(ValueError, match="tz"):
        parse_feed(FEED_XML, FETCHED_AT.replace(tzinfo=None))


# ── ingest_feed: raw → bronze append, 직전 해시 판정 ──────────────────────


class FakeS3:
    """boto3 S3 클라이언트 대역 — 쓰기 순서를 기록한다."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.ops: list[tuple[str, str]] = []

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.objects[Key] = Body
        self.ops.append(("put", Key))

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


class FailingAppendTable:
    """append가 항상 실패하는 테이블 래퍼(나머지는 위임)."""

    def __init__(self, table) -> None:
        self._table = table

    def append(self, *args, **kwargs):
        message = "append 실패(합성)"
        raise RuntimeError(message)

    def __getattr__(self, name):
        return getattr(self._table, name)


@pytest.fixture
def bronze(tmp_path):
    catalog = SqlCatalog(
        "probe",
        uri=f"sqlite:///{tmp_path}/catalog.db",
        warehouse=f"file://{tmp_path}/warehouse",
    )
    return ensure_table(catalog, IDENTIFIER, GEEKNEWS_SCHEMA)


def test_ingest_writes_raw_then_appends(bronze):
    s3 = FakeS3()
    key = raw_key(FETCHED_AT)
    result = ingest_feed(s3, bronze, FEED_XML, FETCHED_AT)
    assert result["rows"] == 2
    assert result["rows_with_cve"] == 1
    assert result["raw_key"] == key
    assert result["feed_sha256_prefix"] == gzip_sha256(FEED_XML)[:12]
    assert s3.ops == [("put", key), ("put", key + ".sha256")]
    assert read_verified_gzip(s3, RAW_BUCKET, key) == FEED_XML
    bronze.refresh()
    assert bronze.scan().to_arrow().num_rows == 2
    digest = latest_snapshot_property(bronze, FEED_DIGEST_PROPERTY)
    assert digest == gzip_sha256(FEED_XML)
    # 원천 값(제목·링크·CVE 목록)은 반환에 없다
    assert "합성" not in str(result)
    assert "CVE-" not in str(result)


def test_ingest_skips_unchanged_feed(bronze):
    s3 = FakeS3()
    ingest_feed(s3, bronze, FEED_XML, FETCHED_AT)
    ops_before = list(s3.ops)
    later = FETCHED_AT + timedelta(hours=1)
    assert ingest_feed(s3, bronze, FEED_XML, later) is None
    assert s3.ops == ops_before
    bronze.refresh()
    assert bronze.scan().to_arrow().num_rows == 2


def test_ingest_relands_after_failed_append(bronze):
    s3 = FakeS3()
    with pytest.raises(RuntimeError, match="append 실패"):
        ingest_feed(s3, FailingAppendTable(bronze), FEED_XML, FETCHED_AT)
    assert len(s3.objects) == 2  # raw는 이미 썼다
    bronze.refresh()
    assert bronze.scan().to_arrow().num_rows == 0
    # 사이드카 기준이었다면 여기서 「같은 해시」로 스킵해 영영 못 들어간다
    later = FETCHED_AT + timedelta(hours=1)
    result = ingest_feed(s3, bronze, FEED_XML, later)
    assert result is not None
    assert result["raw_key"] == raw_key(later)
    assert len(s3.objects) == 4
    bronze.refresh()
    assert bronze.scan().to_arrow().num_rows == 2


def test_ingest_invalid_feed_writes_nothing(bronze):
    s3 = FakeS3()
    with pytest.raises(ValueError, match="Atom"):
        ingest_feed(s3, bronze, b"<html/>", FETCHED_AT)
    assert s3.ops == []


def test_cve_ids_roundtrip_through_iceberg(bronze):
    ingest_feed(FakeS3(), bronze, FEED_XML, FETCHED_AT)
    bronze.refresh()
    scanned = bronze.scan().to_arrow().sort_by("topic_id")
    assert scanned.column("cve_ids").to_pylist() == [
        ["CVE-2025-0001", "CVE-2026-12345"],
        [],
    ]
