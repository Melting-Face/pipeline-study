"""NVD CVE·Change History·CISA KEV 필드 계약, bronze 스키마, 파서 (Airflow 비의존).

Dagster `defs/nvd/assets.py`에서 이식했다. raw 스냅샷(`raw/nvd/…`)을 읽은 페이지 JSON을
bronze Arrow 테이블로 편다 — API를 다시 부르지 않는다.

**bronze = 핵심 스칼라 + 원본 JSON 문자열(`raw_json`)이다.** 중첩 구조(metrics·
configurations·details 등)의 펼침은 dbt(silver)가 한다 — bronze에서 펼치면 스키마가
원천 변경마다 깨지고, 펼치지 않은 필드는 **조용히 버려진다**.

적재 모드(호출하는 쪽 DAG가 고른다):
- `nvd_cves`·`nvd_cve_history`: 일자 파티션 교체(`window_date`). 재실행 멱등이고, 같은
  CVE가 다른 날 다시 수정되면 **그 날 파티션에 또 한 행**이 생긴다 — 버전 이력이 행으로
  누적되는 것이 의도다(최신 1행으로 접는 것은 silver).
- `nvd_kev`: 테이블 전체 교체. 🔴 `drop_table` 후 재생성은 스냅샷 계보를 끊는다.

🔴 **결측은 조용히 채우지 않는다.** 필수 필드가 없으면
KeyError로 드러낸다(`REQUIRED_*`).

🔴 **NVD 시각 문자열에는 오프셋이 없다**(예 `2023-11-07T01:55:06.290`).
**UTC로 해석하되 그 가정은 미확인이다** — API 파라미터는 `Z`(UTC)로 받으므로
응답도 UTC일 개연성이 높지만, 응답 쪽을 명시한 1차 문서를 읽지 못했다(JS 렌더링).
틀리면 모든 시각이 **같은 크기로** 밀려 에러 없이 들어간다. 확인 수단은 파티션
구간과 `last_modified`의 대조다 — `count_outside_window`가 구간 밖 건수를 센다
(판정 없이 관측만).
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pyarrow as pa
from pyiceberg.table import Table

# NVD 응답 피드 키 — 페이지 JSON에서 항목 목록과 항목 본체를 꺼내는 이름.
CVES_ITEMS_KEY = "vulnerabilities"
CVES_ITEM_KEY = "cve"
HISTORY_ITEMS_KEY = "cveChanges"
HISTORY_ITEM_KEY = "change"

# ── 필드 계약 ─────────────────────────────────────────────────────────
# 🔴 **프로브와 짝을 이루는 상수다.** `scripts/nvd_access_probe.py`의
# `NVD_CVE_FIELDS`·`NVD_CVE_KNOWN_FIELDS`·`NVD_CHANGE_FIELDS`·
# `NVD_CHANGE_KNOWN_FIELDS`·`KEV_TOP_FIELDS`·`KEV_ENTRY_FIELDS`·
# `KEV_ENTRY_KNOWN_FIELDS`와 **같은 집합**이어야 한다.
# 프로브는 PEP 723 단독 실행이라 이 패키지를 import할 수 없어 두 벌이 존재하고, 갈라지면
# **프로브는 통과하고 파서만 깨진다** — `tests/lib/test_nvd_contract.py`의
# `test_probe_and_parser_share_the_field_contract`가 AST 대조로 막는다(의도한 결합).
#
# 필수(`REQUIRED_*`) = 없으면 행이 성립하지 않는 것(KeyError). 아는 전부(`KNOWN_*`) =
# 응답에 새 필드가 생겼는지 프로브가 대조하는 기준. bronze는 `raw_json`에 원본을 통째로
# 담으므로 **모르는 필드도 버려지지 않는다** — KNOWN은 "담을지"가 아니라
# "펼칠지" 판단용이다.
REQUIRED_CVE_FIELDS = ("id", "lastModified", "published", "vulnStatus")
KNOWN_CVE_FIELDS = (
    "affected",
    "cisaActionDue",
    "cisaExploitAdd",
    "cisaRequiredAction",
    "cisaVulnerabilityName",
    "configurations",
    "cveTags",
    "descriptions",
    "evaluatorComment",
    "evaluatorImpact",
    "evaluatorSolution",
    "id",
    "lastModified",
    "metrics",
    "published",
    "references",
    "sourceIdentifier",
    "vendorComments",
    "vulnStatus",
    "weaknesses",
)
REQUIRED_CHANGE_FIELDS = ("created", "cveChangeId", "cveId", "eventName")
KNOWN_CHANGE_FIELDS = (
    "created",
    "cveChangeId",
    "cveId",
    "details",
    "eventName",
    "sourceIdentifier",
)
# KEV 최상위 필수는 스키마의 required를 따른다
# (`title`은 실제 피드에 있으나 required가 아니다).
REQUIRED_KEV_TOP_FIELDS = ("catalogVersion", "count", "dateReleased", "vulnerabilities")
REQUIRED_KEV_ENTRY_FIELDS = (
    "cveID",
    "dateAdded",
    "dueDate",
    "product",
    "requiredAction",
    "shortDescription",
    "vendorProject",
    "vulnerabilityName",
)
# 선택 필드(`knownRansomwareCampaignUse`·`forensicTriage`·`notes`·`cwes`)는
# 없을 수 있다.
KNOWN_KEV_ENTRY_FIELDS = (
    *REQUIRED_KEV_ENTRY_FIELDS,
    "cwes",
    "forensicTriage",
    "knownRansomwareCampaignUse",
    "notes",
)

# ── bronze 스키마 ─────────────────────────────────────────────────────
# 🔴 이미 적재된 테이블의 모양이다 — 바꾸면 `tests/lib/test_parity.py` 리터럴이 막는다.
_TS = pa.timestamp("us", tz="UTC")

NVD_CVES_SCHEMA = pa.schema(
    [
        # 조회한 UTC 하루 = 파티션 키. 교체 필터의 축이다.
        ("window_date", pa.date32()),
        ("cve_id", pa.string()),
        # 🔴 오프셋 없는 원문을 UTC로 해석했다(가정 미확인 — 모듈 독스트링).
        ("published", _TS),
        ("last_modified", _TS),
        ("vuln_status", pa.string()),
        # `vulnerabilities[].cve` 객체 원문(재직렬화 — 키 순서는 보존, 공백은 다르다).
        ("raw_json", pa.string()),
        # raw 수집 회차의 시각(경로 `fetched=`) — 재복사에도 바뀌지 않는다.
        ("fetched_at", _TS),
    ]
)

NVD_CVE_HISTORY_SCHEMA = pa.schema(
    [
        ("window_date", pa.date32()),
        ("cve_change_id", pa.string()),
        ("cve_id", pa.string()),
        ("event_name", pa.string()),
        ("created", _TS),
        # `cveChanges[].change` 객체 원문.
        ("raw_json", pa.string()),
        ("fetched_at", _TS),
    ]
)

NVD_KEV_SCHEMA = pa.schema(
    [
        ("catalog_version", pa.string()),
        ("date_released", _TS),
        ("cve_id", pa.string()),
        ("date_added", pa.date32()),
        ("due_date", pa.date32()),
        # 선택 필드 — 없으면 NULL(빈 문자열로 채우지 않는다: "모름"과 "빈 값"이 섞인다).
        ("known_ransomware_campaign_use", pa.string()),
        # `vulnerabilities[]` 항목 원문.
        ("raw_json", pa.string()),
        ("fetched_at", _TS),
    ]
)

# 소수부 자릿수 정규화용 — Python 3.10 `fromisoformat`은 3·6자리만 받는데 KEV
# `dateReleased`는 다른 자릿수로 올 수 있다.
_FRACTION = re.compile(r"\.(\d+)")


def parse_timestamp(raw: str) -> datetime:
    """원천 시각 문자열을 tz-aware UTC로 바꾼다.

    🔴 **오프셋이 없으면 UTC로 간주한다 — 이 가정은 미확인이다**(모듈 독스트링).
    `Z`·오프셋이 있으면 그것을 따른다. naive로 담지 않는 이유는 이후 비교가
    naive/aware 혼합으로 터지거나, 더 나쁘게는 조용히 로컬 시각으로 읽히기 때문이다.

    Args:
        raw: ISO-8601 시각(예 `2023-11-07T01:55:06.290`, `2025-01-02T03:04:05.1234Z`).

    Returns:
        UTC로 정규화된 tz-aware datetime.
    """
    text = raw.replace("Z", "+00:00")
    text = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _dumps(obj: Any) -> str:
    """원본 JSON 객체를 문자열로 담는다(비ASCII 보존·공백 없음)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def cves_to_arrow(
    pages: list[dict[str, Any]], window_date: str, fetched_at: datetime
) -> pa.Table:
    """CVE 구간 페이지들을 bronze Arrow 테이블로 편다.

    Args:
        pages: raw에서 읽은 페이지 JSON 목록.
        window_date: 파티션 키(`YYYY-MM-DD`).
        fetched_at: raw 수집 회차의 시각(경로 `fetched=`).

    Returns:
        NVD_CVES_SCHEMA를 따르는 테이블(CVE당 1행, 없으면 0행).

    Raises:
        KeyError: CVE에 `REQUIRED_CVE_FIELDS` 중 하나라도 없을 때.
        RuntimeError: 같은 구간에 같은 CVE가 두 번 나올 때(페이지 이동 의심).
    """
    day = date.fromisoformat(window_date)
    rows = []
    for payload in pages:
        for item in payload[CVES_ITEMS_KEY]:
            cve = item[CVES_ITEM_KEY]
            rows.append(
                {
                    "window_date": day,
                    "cve_id": cve["id"],
                    "published": parse_timestamp(cve["published"]),
                    "last_modified": parse_timestamp(cve["lastModified"]),
                    "vuln_status": cve["vulnStatus"],
                    "raw_json": _dumps(cve),
                    "fetched_at": fetched_at.astimezone(timezone.utc),
                }
            )
    # 🔴 한 구간 안에서 CVE는 한 번만 나온다(`lastModified`는 하나다). 두 번이면 순회 중
    #    레코드가 이동해 페이지 경계에서 겹친 것이다 — 합계는 맞아도 한 건이 빠졌다.
    #    raw 커밋 전에 같은 가드가 먼저 걸린다 — 여기는 두 번째 벽.
    ids = [row["cve_id"] for row in rows]
    if len(ids) != len(set(ids)):
        message = f"cves dt={window_date}: 같은 CVE가 두 번 나왔다 — 페이지 이동 의심"
        raise RuntimeError(message)
    rows.sort(key=lambda row: row["cve_id"])
    return pa.Table.from_pylist(rows, schema=NVD_CVES_SCHEMA)


def changes_to_arrow(
    pages: list[dict[str, Any]], window_date: str, fetched_at: datetime
) -> pa.Table:
    """Change History 구간 페이지들을 bronze Arrow 테이블로 편다.

    Args:
        pages: raw에서 읽은 페이지 JSON 목록.
        window_date: 파티션 키.
        fetched_at: raw 수집 회차의 시각.

    Returns:
        NVD_CVE_HISTORY_SCHEMA를 따르는 테이블(변경 이벤트당 1행).

    Raises:
        KeyError: 변경에 `REQUIRED_CHANGE_FIELDS` 중 하나라도 없을 때.
        RuntimeError: 같은 `cveChangeId`가 두 번 나올 때.
    """
    day = date.fromisoformat(window_date)
    rows = []
    for payload in pages:
        for item in payload[HISTORY_ITEMS_KEY]:
            change = item[HISTORY_ITEM_KEY]
            rows.append(
                {
                    "window_date": day,
                    "cve_change_id": change["cveChangeId"],
                    "cve_id": change["cveId"],
                    "event_name": change["eventName"],
                    "created": parse_timestamp(change["created"]),
                    "raw_json": _dumps(change),
                    "fetched_at": fetched_at.astimezone(timezone.utc),
                }
            )
    ids = [row["cve_change_id"] for row in rows]
    if len(ids) != len(set(ids)):
        message = f"cve_history dt={window_date}: 같은 cveChangeId가 두 번 나왔다"
        raise RuntimeError(message)
    rows.sort(key=lambda row: (row["created"], row["cve_change_id"]))
    return pa.Table.from_pylist(rows, schema=NVD_CVE_HISTORY_SCHEMA)


def kev_to_arrow(payload: dict[str, Any], fetched_at: datetime) -> pa.Table:
    """KEV 카탈로그 전체를 bronze Arrow 테이블로 편다.

    Args:
        payload: KEV JSON 전체.
        fetched_at: raw 수집 회차의 시각.

    Returns:
        NVD_KEV_SCHEMA를 따르는 테이블(항목당 1행).

    Raises:
        KeyError: 최상위·항목 필수 필드가 없을 때.
        RuntimeError: `count` ≠ 항목 수, 또는 `cveID` 중복.
    """
    missing_top = [f for f in REQUIRED_KEV_TOP_FIELDS if f not in payload]
    if missing_top:
        raise KeyError(missing_top[0])
    entries = payload["vulnerabilities"]
    if int(payload["count"]) != len(entries):
        message = f"KEV count={payload['count']}인데 항목 {len(entries)}건 — 잘렸다"
        raise RuntimeError(message)
    catalog_version = str(payload["catalogVersion"])
    date_released = parse_timestamp(payload["dateReleased"])
    rows = []
    for entry in entries:
        missing = [f for f in REQUIRED_KEV_ENTRY_FIELDS if f not in entry]
        if missing:
            raise KeyError(missing[0])
        rows.append(
            {
                "catalog_version": catalog_version,
                "date_released": date_released,
                "cve_id": entry["cveID"],
                "date_added": date.fromisoformat(entry["dateAdded"]),
                "due_date": date.fromisoformat(entry["dueDate"]),
                "known_ransomware_campaign_use": entry.get(
                    "knownRansomwareCampaignUse"
                ),
                "raw_json": _dumps(entry),
                "fetched_at": fetched_at.astimezone(timezone.utc),
            }
        )
    # 🔴 `cveID`가 키다 — 중복이면 키로 쓸 수 없고 silver 조인이 행을 불린다.
    ids = [row["cve_id"] for row in rows]
    if len(ids) != len(set(ids)):
        message = "KEV cveID 중복 — 키로 쓸 수 없다"
        raise RuntimeError(message)
    rows.sort(key=lambda row: row["cve_id"])
    return pa.Table.from_pylist(rows, schema=NVD_KEV_SCHEMA)


def count_outside_window(stamps: list[datetime], window_date: str) -> int:
    """UTC 하루 구간 밖에 놓인 시각의 수(관측 셀 — 판정하지 않는다).

    0이 아니면 ⓐ 시각의 UTC 가정이 틀렸거나 ⓑ 원천의 구간 해석이 우리와 다르다는 신호다.
    """
    start = datetime.fromisoformat(window_date).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return sum(1 for stamp in stamps if not start <= stamp < end)


def table_fetched_at(table: Table) -> datetime | None:
    """KEV 테이블에 적재된 raw 회차 시각(`fetched_at`). 비어 있으면 None.

    전체 교체 테이블이라 모든 행의 `fetched_at`이 같다 — 한 행만 읽는다.
    """
    arrow = table.scan(selected_fields=("fetched_at",), limit=1).to_arrow()
    if arrow.num_rows == 0:
        return None
    return arrow.column("fetched_at")[0].as_py()


def kev_needs_load(table_fetched_at: datetime | None, run_fetched_at: datetime) -> bool:
    """최신 raw 회차가 테이블에 아직 안 올라갔으면 True(첫 적재이거나 회차가 다르다)."""
    return table_fetched_at is None or table_fetched_at != run_fetched_at
