"""NVD·KEV 파서·bronze 스키마 단위 테스트 — `lakehouse/nvd_parse.py`.

Dagster `tests/test_nvd_parse.py` §1(파서)·§6(Iceberg 파티션 교체)의 기대값을 그대로
옮겼다. **실인프라에 붙지 않는다** — Iceberg는 SQLite 카탈로그 + `tmp_path`를 쓴다.

🔴 픽스처 값은 전부 **합성**이다. 필드 **구조**는 프로브 계약을 따르고, 구조가 현실과
같은지는 프로브가 본다 — 이 파일은 `KNOWN_*` ↔ 픽스처를 잇는다.
"""

import json
from datetime import date, datetime, timezone
from typing import Any

import pytest
from lakehouse.iceberg import ensure_table, replace_partition
from lakehouse.nvd_parse import (
    KNOWN_CHANGE_FIELDS,
    KNOWN_CVE_FIELDS,
    KNOWN_KEV_ENTRY_FIELDS,
    NVD_CVE_HISTORY_SCHEMA,
    NVD_CVES_SCHEMA,
    NVD_KEV_SCHEMA,
    REQUIRED_CHANGE_FIELDS,
    REQUIRED_CVE_FIELDS,
    REQUIRED_KEV_ENTRY_FIELDS,
    REQUIRED_KEV_TOP_FIELDS,
    changes_to_arrow,
    count_outside_window,
    cves_to_arrow,
    kev_needs_load,
    kev_to_arrow,
    parse_timestamp,
    table_fetched_at,
)
from pyiceberg.catalog.sql import SqlCatalog

WINDOW = "2026-10-30"
FETCHED_1 = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
FETCHED_2 = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)


# ── 합성 픽스처 ─────────────────────────────────────────────────────────


def _cve(cve_id: str, last_modified: str = f"{WINDOW}T01:55:06.290") -> dict[str, Any]:
    """`KNOWN_CVE_FIELDS` 전체를 갖춘 합성 CVE(값은 지어냈다)."""
    return {
        "affected": [],
        "cisaActionDue": "2026-11-20",
        "cisaExploitAdd": "2026-10-30",
        "cisaRequiredAction": "Apply mitigations.",
        "cisaVulnerabilityName": "Example Product Flaw",
        "configurations": [],
        "cveTags": [],
        "descriptions": [{"lang": "en", "value": "합성 설명"}],
        "evaluatorComment": "",
        "evaluatorImpact": "",
        "evaluatorSolution": "",
        "id": cve_id,
        "lastModified": last_modified,
        "metrics": {},
        "published": "2026-10-01T00:00:00.000",
        "references": [],
        "sourceIdentifier": "cna@example.test",
        "vendorComments": [],
        "vulnStatus": "Analyzed",
        "weaknesses": [],
    }


def _change(change_id: str, cve_id: str = "CVE-2026-0001") -> dict[str, Any]:
    return {
        "created": f"{WINDOW}T12:00:00.000",
        "cveChangeId": change_id,
        "cveId": cve_id,
        "details": [{"action": "Added", "type": "CVSS V3.1"}],
        "eventName": "Initial Analysis",
        "sourceIdentifier": "nvd@example.test",
    }


def _kev_entry(cve_id: str, *, ransomware: bool = True) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "cveID": cve_id,
        "dateAdded": "2026-10-01",
        "dueDate": "2026-10-22",
        "product": "Widget",
        "requiredAction": "Apply updates.",
        "shortDescription": "합성 설명",
        "vendorProject": "Example",
        "vulnerabilityName": "Example Widget Flaw",
        "cwes": ["CWE-79"],
        "forensicTriage": "",
        "notes": "",
    }
    if ransomware:
        entry["knownRansomwareCampaignUse"] = "Unknown"
    return entry


def _kev_payload(entries: list[dict[str, Any]], count: int | None = None) -> dict:
    return {
        "title": "합성 KEV",
        "catalogVersion": "2026.10.30",
        "dateReleased": "2026-10-30T17:59:11.4512Z",
        "count": len(entries) if count is None else count,
        "vulnerabilities": entries,
    }


def _cve_page(cves: list[dict], total: int, start: int = 0) -> dict[str, Any]:
    return {
        "resultsPerPage": len(cves),
        "startIndex": start,
        "totalResults": total,
        "format": "NVD_CVE",
        "version": "2.0",
        "timestamp": "2026-10-31T04:00:00.000",
        "vulnerabilities": [{"cve": cve} for cve in cves],
    }


def _history_page(changes: list[dict], total: int) -> dict[str, Any]:
    return {
        "resultsPerPage": len(changes),
        "startIndex": 0,
        "totalResults": total,
        "format": "NVD_CVEHistory",
        "version": "2.0",
        "timestamp": "2026-10-31T04:00:00.000",
        "cveChanges": [{"change": change} for change in changes],
    }


# ── 1. 파서 ─────────────────────────────────────────────────────────────


def test_parse_timestamp_assumes_utc_for_offsetless() -> None:
    """🔴 오프셋 없는 원문은 UTC로 간주한다(가정 미확인) — naive로 남기지 않는다."""
    parsed = parse_timestamp("2023-11-07T01:55:06.290")
    assert parsed == datetime(2023, 11, 7, 1, 55, 6, 290000, tzinfo=timezone.utc)
    assert parsed.tzinfo is not None


def test_parse_timestamp_honours_offset_and_odd_fraction() -> None:
    """`Z`·오프셋은 따르고, 3·6자리가 아닌 소수부(3.10 fromisoformat 불가)도 받는다."""
    assert parse_timestamp("2026-10-30T17:59:11.4512Z") == datetime(
        2026, 10, 30, 17, 59, 11, 451200, tzinfo=timezone.utc
    )
    assert parse_timestamp("2026-10-30T09:00:00+09:00") == datetime(
        2026, 10, 30, 0, 0, tzinfo=timezone.utc
    )


def test_cves_to_arrow_maps_scalars_and_raw_json() -> None:
    pages = [_cve_page([_cve("CVE-2026-0002"), _cve("CVE-2026-0001")], 2)]
    arrow = cves_to_arrow(pages, WINDOW, FETCHED_1)

    assert arrow.schema == NVD_CVES_SCHEMA
    rows = arrow.to_pylist()
    # 정렬은 cve_id 순으로 고정된다(응답 순서에 기대지 않는다).
    assert [row["cve_id"] for row in rows] == ["CVE-2026-0001", "CVE-2026-0002"]
    assert rows[0]["window_date"] == date(2026, 10, 30)
    assert rows[0]["vuln_status"] == "Analyzed"
    assert rows[0]["fetched_at"] == FETCHED_1
    # 원본은 통째로 담긴다 — 펼치지 않은 필드도 버려지지 않는다.
    assert json.loads(rows[0]["raw_json"]) == _cve("CVE-2026-0001")


@pytest.mark.parametrize("field", REQUIRED_CVE_FIELDS)
def test_cves_to_arrow_raises_on_missing_required(field: str) -> None:
    """🔴 필수 필드 결측은 조용히 채우지 않고 KeyError로 드러낸다."""
    cve = _cve("CVE-2026-0001")
    del cve[field]
    with pytest.raises(KeyError):
        cves_to_arrow([_cve_page([cve], 1)], WINDOW, FETCHED_1)


def test_cves_to_arrow_rejects_duplicate_within_window() -> None:
    """한 구간에 같은 CVE가 두 번이면 페이지 이동이다 — bronze의 두 번째 벽."""
    page = _cve_page([_cve("CVE-2026-0001"), _cve("CVE-2026-0001")], 2)
    with pytest.raises(RuntimeError, match="두 번"):
        cves_to_arrow([page], WINDOW, FETCHED_1)


def test_changes_to_arrow_maps_scalars() -> None:
    pages = [_history_page([_change("b"), _change("a")], 2)]
    arrow = changes_to_arrow(pages, WINDOW, FETCHED_1)

    assert arrow.schema == NVD_CVE_HISTORY_SCHEMA
    rows = arrow.to_pylist()
    assert [row["cve_change_id"] for row in rows] == ["a", "b"]
    assert rows[0]["event_name"] == "Initial Analysis"
    assert rows[0]["created"] == datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    assert json.loads(rows[0]["raw_json"]) == _change("a")


@pytest.mark.parametrize("field", REQUIRED_CHANGE_FIELDS)
def test_changes_to_arrow_raises_on_missing_required(field: str) -> None:
    change = _change("a")
    del change[field]
    with pytest.raises(KeyError):
        changes_to_arrow([_history_page([change], 1)], WINDOW, FETCHED_1)


def test_changes_to_arrow_rejects_duplicate_change_id() -> None:
    page = _history_page([_change("a"), _change("a")], 2)
    with pytest.raises(RuntimeError, match="cveChangeId"):
        changes_to_arrow([page], WINDOW, FETCHED_1)


def test_kev_to_arrow_maps_and_keeps_optional_null() -> None:
    """선택 필드가 없으면 NULL이다 — 빈 문자열로 채우지 않는다."""
    payload = _kev_payload(
        [_kev_entry("CVE-2026-0002"), _kev_entry("CVE-2026-0001", ransomware=False)]
    )
    arrow = kev_to_arrow(payload, FETCHED_1)

    assert arrow.schema == NVD_KEV_SCHEMA
    rows = arrow.to_pylist()
    assert [row["cve_id"] for row in rows] == ["CVE-2026-0001", "CVE-2026-0002"]
    assert rows[0]["known_ransomware_campaign_use"] is None
    assert rows[1]["known_ransomware_campaign_use"] == "Unknown"
    assert rows[0]["date_added"] == date(2026, 10, 1)
    assert rows[0]["catalog_version"] == "2026.10.30"
    assert rows[0]["date_released"].tzinfo is not None
    assert rows[0]["fetched_at"] == FETCHED_1


def test_kev_to_arrow_rejects_count_mismatch() -> None:
    payload = _kev_payload([_kev_entry("CVE-2026-0001")], count=2)
    with pytest.raises(RuntimeError, match="잘렸다"):
        kev_to_arrow(payload, FETCHED_1)


def test_kev_to_arrow_rejects_duplicate_cve_id() -> None:
    payload = _kev_payload([_kev_entry("CVE-2026-0001"), _kev_entry("CVE-2026-0001")])
    with pytest.raises(RuntimeError, match="중복"):
        kev_to_arrow(payload, FETCHED_1)


@pytest.mark.parametrize("field", REQUIRED_KEV_ENTRY_FIELDS)
def test_kev_to_arrow_raises_on_missing_entry_field(field: str) -> None:
    entry = _kev_entry("CVE-2026-0001")
    del entry[field]
    with pytest.raises(KeyError):
        kev_to_arrow(_kev_payload([entry]), FETCHED_1)


@pytest.mark.parametrize("field", REQUIRED_KEV_TOP_FIELDS)
def test_kev_to_arrow_raises_on_missing_top_field(field: str) -> None:
    payload = _kev_payload([_kev_entry("CVE-2026-0001")])
    del payload[field]
    with pytest.raises(KeyError):
        kev_to_arrow(payload, FETCHED_1)


def test_count_outside_window_counts_only_outside() -> None:
    inside = datetime(2026, 10, 30, 23, 59, 59, 999000, tzinfo=timezone.utc)
    before = datetime(2026, 10, 29, 23, 59, 59, tzinfo=timezone.utc)
    after = datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc)
    assert count_outside_window([inside, before, after], WINDOW) == 2


def test_fixtures_match_the_known_field_contract() -> None:
    """픽스처 ↔ `KNOWN_*`. 계약만 고치고 픽스처를 두면 여기서 멈춘다."""
    assert set(_cve("CVE-2026-0001")) == set(KNOWN_CVE_FIELDS)
    assert set(_change("a")) == set(KNOWN_CHANGE_FIELDS)
    assert set(_kev_entry("CVE-2026-0001")) == set(KNOWN_KEV_ENTRY_FIELDS)


# ── 2. Iceberg 적재 모드 (SQLite 카탈로그) ───────────────────────────────

IDENTIFIER = "nvd_probe.table"


@pytest.fixture
def catalog(tmp_path):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    return SqlCatalog(
        "probe",
        uri=f"sqlite:///{tmp_path / 'catalog.db'}",
        warehouse=f"file://{warehouse}",
    )


def test_date_partition_filter_isolates_window(catalog) -> None:
    """`window_date`가 **date 타입**이어도 문자열 필터로 그 파티션만 교체된다."""

    def load(window: str, ids: list[str]) -> None:
        pages = [_cve_page([_cve(i) for i in ids], len(ids))]
        table = ensure_table(catalog, IDENTIFIER, NVD_CVES_SCHEMA)
        replace_partition(
            table, cves_to_arrow(pages, window, FETCHED_1), "window_date", window
        )

    load("2026-10-29", ["CVE-A", "CVE-B"])
    load(WINDOW, ["CVE-A", "CVE-C", "CVE-D"])
    load(WINDOW, ["CVE-A", "CVE-C", "CVE-D"])  # 재실행 — 멱등

    table = catalog.load_table(IDENTIFIER)
    assert table.scan().to_arrow().num_rows == 5
    on_window = table.scan(row_filter=f"window_date = '{WINDOW}'").to_arrow()
    assert on_window.num_rows == 3


# ── 3. KEV 재적재 판정 (Q14) ─────────────────────────────────────────────


def test_table_fetched_at_none_when_empty(catalog) -> None:
    """테이블이 비어 있으면 None — 첫 적재는 무조건 한다."""
    table = ensure_table(catalog, IDENTIFIER, NVD_KEV_SCHEMA)
    assert table_fetched_at(table) is None


def test_table_fetched_at_reads_single_value(catalog) -> None:
    """전체 교체 테이블이라 `fetched_at`은 한 값이다 — 한 행만 읽는다."""
    table = ensure_table(catalog, IDENTIFIER, NVD_KEV_SCHEMA)
    entries = [_kev_entry("CVE-2026-0001"), _kev_entry("CVE-2026-0002")]
    table.overwrite(kev_to_arrow(_kev_payload(entries), FETCHED_1))
    assert table_fetched_at(table) == FETCHED_1


def test_kev_needs_load() -> None:
    assert kev_needs_load(None, FETCHED_1) is True
    assert kev_needs_load(FETCHED_1, FETCHED_1) is False
    assert kev_needs_load(FETCHED_1, FETCHED_2) is True
