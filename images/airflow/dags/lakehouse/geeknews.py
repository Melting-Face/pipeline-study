"""GeekNews(news.hada.io) Atom 피드 파서와 bronze 스키마.

- Airflow import 금지 — 순수 로직만 둔다(DAG는 배선).
- 원천 값(제목·링크·본문)은 로그·Asset event에 남기지 않는다.
- 본문·작성자는 적재하지 않는다(이용 조건 판정: docs/security/datasets.md).
  본문은 CVE 추출에만 쓰고 버린다.
- 입력은 외부 원천(FeedBurner 경유)이므로 DTD를 거부하고 크기 상한을 두며,
  이상 입력은 조용히 채우지 않고 실패한다.
"""

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree.ElementTree import (
    Element,
)

import pyarrow as pa
from defusedxml import ElementTree
from pyiceberg.table import Table

from lakehouse.iceberg import append_rows, latest_snapshot_property
from lakehouse.s3raw import gzip_sha256, put_gzip_with_sidecar

FEED_URL = "https://news.hada.io/rss/news"  # 302 → FeedBurner(requests 기본 추종)
USER_AGENT = (
    "pipeline-study-geeknews/1.0 (+https://github.com/Melting-Face/pipeline-study)"
)
RAW_BUCKET = "warehouse"
RAW_PREFIX = "raw/geeknews/feed"
RAW_FILENAME = "feed.xml.gz"
IDENTIFIER = "geeknews.geeknews_items"
FEED_DIGEST_PROPERTY = "geeknews.feed_sha256"  # bronze 스냅샷 요약 속성
RAW_KEY_PROPERTY = "geeknews.raw_key"
MAX_FEED_BYTES = 5 * 1024 * 1024
ATOM_NS = "{http://www.w3.org/2005/Atom}"
CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)
FETCHED_FORMAT = "%Y%m%dT%H%M%SZ"  # NVD 회차 표기와 같은 모양

GEEKNEWS_SCHEMA = pa.schema(
    [
        ("topic_id", pa.int64()),
        ("title", pa.string()),
        ("link", pa.string()),
        ("published", pa.timestamp("us", tz="UTC")),
        ("updated", pa.timestamp("us", tz="UTC")),
        ("cve_ids", pa.list_(pa.string())),
        ("fetched_at", pa.timestamp("us", tz="UTC")),
    ]
)


def raw_key(fetched_at: datetime) -> str:
    """수집 시각(aware)을 UTC로 환산한 raw 객체 키."""
    if fetched_at.tzinfo is None:
        message = "fetched_at은 tz-aware여야 함"
        raise ValueError(message)
    utc = fetched_at.astimezone(timezone.utc)
    fetched = utc.strftime(FETCHED_FORMAT)
    return f"{RAW_PREFIX}/dt={utc:%Y-%m-%d}/fetched={fetched}/{RAW_FILENAME}"


def strip_tracking_params(url: str) -> str:
    """`utm_` 접두 쿼리 키만 제거한다(나머지 순서·fragment 보존)."""
    parts = urlsplit(url)
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.startswith("utm_")
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def topic_id_from_entry_id(entry_id: str) -> int:
    """Entry `id`의 query `id` 정수. 없거나 정수가 아니면 ValueError."""
    values = parse_qs(urlsplit(entry_id).query).get("id", [])
    if len(values) != 1 or not values[0].isdigit():
        message = "topic id를 읽을 수 없음(entry id 형식 변경 의심)"
        raise ValueError(message)
    return int(values[0])


def extract_cve_ids(html: str | None) -> list[str]:
    """본문에서 CVE ID를 대문자·중복 제거·정렬해 반환한다."""
    if not html:
        return []
    return sorted({m.upper() for m in CVE_PATTERN.findall(html)})


def parse_atom_timestamp(text: str) -> datetime:
    """Atom 시각을 UTC aware로. 오프셋이 없으면 ValueError."""
    parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        message = "시각에 오프셋이 없음"
        raise ValueError(message)
    return parsed.astimezone(timezone.utc)


def _required_text(entry: Element, tag: str) -> str:
    text = entry.findtext(ATOM_NS + tag)
    if text is None or not text.strip():
        message = f"entry에 {tag}가 없음"
        raise ValueError(message)
    return text.strip()


def parse_feed(body: bytes, fetched_at: datetime) -> pa.Table:
    """Atom 피드 원문을 bronze 스키마 테이블로 파싱한다. 이상 입력은 ValueError."""
    # silver dedup이 fetched_at을 키로 쓰므로 호스트 로컬 시각으로 해석하지 않는다
    if fetched_at.tzinfo is None:
        message = "fetched_at은 tz-aware여야 함"
        raise ValueError(message)
    if len(body) > MAX_FEED_BYTES:
        message = f"피드가 상한({MAX_FEED_BYTES}바이트)을 넘음"
        raise ValueError(message)
    root = ElementTree.fromstring(body, forbid_dtd=True)
    if root.tag != ATOM_NS + "feed":
        message = "Atom feed 루트가 아님"
        raise ValueError(message)

    rows = []
    seen: set[int] = set()
    for entry in root.findall(ATOM_NS + "entry"):
        topic_id = topic_id_from_entry_id(_required_text(entry, "id"))
        if topic_id in seen:
            message = "피드 안에 topic 중복"
            raise ValueError(message)
        seen.add(topic_id)
        href = next(
            (
                link.get("href")
                for link in entry.findall(ATOM_NS + "link")
                if link.get("rel") == "alternate" and link.get("href")
            ),
            None,
        )
        if href is None:
            message = "entry에 alternate link가 없음"
            raise ValueError(message)
        rows.append(
            {
                "topic_id": topic_id,
                "title": _required_text(entry, "title"),
                "link": strip_tracking_params(href),
                "published": parse_atom_timestamp(_required_text(entry, "published")),
                "updated": parse_atom_timestamp(_required_text(entry, "updated")),
                "cve_ids": extract_cve_ids(entry.findtext(ATOM_NS + "content")),
                "fetched_at": fetched_at.astimezone(timezone.utc),
            }
        )
    if not rows:
        message = "entry 0건"
        raise ValueError(message)
    rows.sort(key=lambda row: row["topic_id"])
    return pa.Table.from_pylist(rows, schema=GEEKNEWS_SCHEMA)


def ingest_feed(
    s3: Any, table: Table, body: bytes, fetched_at: datetime
) -> dict[str, Any] | None:
    """피드를 검증하고, 직전 bronze 적재분과 다르면 raw → bronze append 한다.

    「직전」의 기준은 raw 사이드카가 아니라 **bronze 스냅샷 속성**이다. 사이드카를
    기준으로 하면 raw 쓰기 성공 → append 실패 뒤 다음 회차가 「같은 해시」로
    스킵해 그 스냅샷이 bronze에 영영 안 들어간다. 속성은 append와 원자적이라
    실패 후엔 다르게 보여 다시 적재한다(중복 raw 1벌 = 낭비이되 정확).

    Returns:
        직전과 같아 스킵하면 None. 아니면 관측 메타(원천 값 없음).
    """
    arrow = parse_feed(body, fetched_at)  # 검증 실패면 raw도 쓰지 않는다
    digest = gzip_sha256(body)
    if latest_snapshot_property(table, FEED_DIGEST_PROPERTY) == digest:
        return None
    key = raw_key(fetched_at)
    written = put_gzip_with_sidecar(s3, RAW_BUCKET, key, body)
    if written["sha256"] != digest:
        message = "gzip_sha256과 put_gzip_with_sidecar의 해시가 다름(압축 규칙 갈라짐)"
        raise RuntimeError(message)
    meta = append_rows(
        table, arrow, {FEED_DIGEST_PROPERTY: digest, RAW_KEY_PROPERTY: key}
    )
    return {
        "rows": arrow.num_rows,
        "rows_with_cve": sum(1 for ids in arrow["cve_ids"].to_pylist() if ids),
        "raw_key": key,
        "snapshot_id": meta["snapshot_id"],
        "feed_sha256_prefix": digest[:12],
    }
