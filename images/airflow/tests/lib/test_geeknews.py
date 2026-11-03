"""GeekNews Atom 피드 파서·bronze 스키마(`lakehouse.geeknews`).

피드 fixture는 합성이다(entry 모양만 실제 피드를 따르고 값은 지어냈다).
네트워크에 붙지 않는다.
"""

from datetime import datetime, timezone

import defusedxml
import pytest
from lakehouse.geeknews import (
    GEEKNEWS_SCHEMA,
    MAX_FEED_BYTES,
    USER_AGENT,
    extract_cve_ids,
    parse_atom_timestamp,
    parse_feed,
    raw_key,
    strip_tracking_params,
    topic_id_from_entry_id,
)

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
