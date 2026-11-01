"""NVD·CISA KEV 수집·적재 경로 단위 테스트 — `defs/nvd/` · `common/nvd.py`.

**실인프라에 붙지 않는다.** NVD·CISA에도 S3에도 접속하지 않는다 — HTTP는
`helper.requests.get`을, S3는 클라이언트 대역을, Iceberg는 SQLite 카탈로그 +
`tmp_path` warehouse를, Dagster 인스턴스는 `instance_for_test`(임시 SQLite)를 쓴다
(`docs/test.md` 격리 원칙, 모킹 라이브러리 미도입 선례).

이 파일이 지키는 축:

1. **파서** — 필수 필드 결측은 KeyError, 구간 안 중복은 실패, 오프셋 없는 시각은
   tz-aware UTC(가정 미확인 — `assets.py` 독스트링).
2. **필드 계약** — 파서의 `REQUIRED_*`·`KNOWN_*`가 프로브
   (`scripts/nvd_access_probe.py`)의 상수와 **같은 집합**인지 AST로 대조하고, 프로브가
   응답에만 있는 필드를 **판정 불가로 올리는지** 본다.
3. **페이지 순회** — `startIndex` 진행·`apiKey` 헤더·요청 간격, 그리고
   **절단·총계 변동이 실패로 드러나는지**(조용한 절단 금지).
4. **raw 회차 프로토콜** — 데이터 → 사이드카 순서, **page-0000 커밋이 마지막**, 커밋된
   dt 스킵, **force는 새 회차를 더할 뿐 옛 회차를 지우지 않는다**, 미커밋 회차는 읽지
   않는다, ID 중복·절단이면 커밋이 없다, pool 한도가 없으면 API를 부르지 않는다.
5. **KEV** — 착지 전 count·중복 거부, 요청에 NVD 키가 실리지 않음, 전체 overwrite가
   멱등이고 스냅샷 루트가 하나(drop 아님).

🔴 픽스처 값은 전부 **합성**이다. 필드 **구조**는 프로브 계약을 따르고, 구조가 현실과
같은지는 프로브가 본다(응답 ↔ `KNOWN_*`) — 이 파일은 `KNOWN_*` ↔ 픽스처를 잇는다.
"""

import ast
import gzip
import hashlib
import importlib.util
import io
import json
from collections.abc import Callable, Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from botocore.exceptions import ClientError
from dagster_project.common import helper, nvd
from dagster_project.common.fetch import SIDECAR_SUFFIX, put_gzip_with_sidecar
from dagster_project.common.helper import (
    ensure_table,
    overwrite_iceberg_table,
    replace_partition_in_iceberg,
)
from dagster_project.common.nvd import (
    NVD_API_POOL,
    NVD_MIN_INTERVAL_NO_KEY_S,
    NVD_MIN_INTERVAL_WITH_KEY_S,
    NvdResource,
    land_window_pages,
    latest_committed_run,
    page_key,
    parse_run_fetched_at,
    read_window_pages,
    run_segment,
    window_params,
)
from dagster_project.defs.nvd.assets import (
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
    _changes_to_arrow,
    _count_outside_window,
    _cves_to_arrow,
    _kev_to_arrow,
    _parse_timestamp,
)
from dagster_project.defs.nvd.raw_assets import (
    _land_kev,
    raw_nvd_cve_history,
    raw_nvd_cves,
    raw_nvd_kev,
)
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import EqualTo

import dagster as dg

WINDOW = "2026-10-30"
FETCHED_1 = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
FETCHED_2 = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
BUCKET = "warehouse"
SOURCE_BASE = f"s3://{BUCKET}/raw/nvd"
DT_KEY = f"raw/nvd/cves/dt={WINDOW}"
RUN_1 = f"{DT_KEY}/{run_segment(FETCHED_1)}"
RUN_2 = f"{DT_KEY}/{run_segment(FETCHED_2)}"
# tests/ → src/ → dockerfile.d/ → dagster/ → 저장소 루트 순으로 네 단계 올라간다.
PROBE_PATH = Path(__file__).resolve().parents[4] / "scripts" / "nvd_access_probe.py"


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
    parsed = _parse_timestamp("2023-11-07T01:55:06.290")
    assert parsed == datetime(2023, 11, 7, 1, 55, 6, 290000, tzinfo=timezone.utc)
    assert parsed.tzinfo is not None


def test_parse_timestamp_honours_offset_and_odd_fraction() -> None:
    """`Z`·오프셋은 따르고, 3·6자리가 아닌 소수부(3.10 fromisoformat 불가)도 받는다."""
    assert _parse_timestamp("2026-10-30T17:59:11.4512Z") == datetime(
        2026, 10, 30, 17, 59, 11, 451200, tzinfo=timezone.utc
    )
    assert _parse_timestamp("2026-10-30T09:00:00+09:00") == datetime(
        2026, 10, 30, 0, 0, tzinfo=timezone.utc
    )


def test_cves_to_arrow_maps_scalars_and_raw_json() -> None:
    pages = [_cve_page([_cve("CVE-2026-0002"), _cve("CVE-2026-0001")], 2)]
    arrow = _cves_to_arrow(pages, WINDOW, FETCHED_1)

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
        _cves_to_arrow([_cve_page([cve], 1)], WINDOW, FETCHED_1)


def test_cves_to_arrow_rejects_duplicate_within_window() -> None:
    """한 구간에 같은 CVE가 두 번이면 페이지 이동이다 — bronze의 두 번째 벽."""
    page = _cve_page([_cve("CVE-2026-0001"), _cve("CVE-2026-0001")], 2)
    with pytest.raises(RuntimeError, match="두 번"):
        _cves_to_arrow([page], WINDOW, FETCHED_1)


def test_changes_to_arrow_maps_scalars() -> None:
    pages = [_history_page([_change("b"), _change("a")], 2)]
    arrow = _changes_to_arrow(pages, WINDOW, FETCHED_1)

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
        _changes_to_arrow([_history_page([change], 1)], WINDOW, FETCHED_1)


def test_changes_to_arrow_rejects_duplicate_change_id() -> None:
    page = _history_page([_change("a"), _change("a")], 2)
    with pytest.raises(RuntimeError, match="cveChangeId"):
        _changes_to_arrow([page], WINDOW, FETCHED_1)


def test_kev_to_arrow_maps_and_keeps_optional_null() -> None:
    """선택 필드가 없으면 NULL이다 — 빈 문자열로 채우지 않는다."""
    payload = _kev_payload(
        [_kev_entry("CVE-2026-0002"), _kev_entry("CVE-2026-0001", ransomware=False)]
    )
    arrow = _kev_to_arrow(payload, FETCHED_1)

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
        _kev_to_arrow(payload, FETCHED_1)


def test_kev_to_arrow_rejects_duplicate_cve_id() -> None:
    payload = _kev_payload([_kev_entry("CVE-2026-0001"), _kev_entry("CVE-2026-0001")])
    with pytest.raises(RuntimeError, match="중복"):
        _kev_to_arrow(payload, FETCHED_1)


@pytest.mark.parametrize("field", REQUIRED_KEV_ENTRY_FIELDS)
def test_kev_to_arrow_raises_on_missing_entry_field(field: str) -> None:
    entry = _kev_entry("CVE-2026-0001")
    del entry[field]
    with pytest.raises(KeyError):
        _kev_to_arrow(_kev_payload([entry]), FETCHED_1)


@pytest.mark.parametrize("field", REQUIRED_KEV_TOP_FIELDS)
def test_kev_to_arrow_raises_on_missing_top_field(field: str) -> None:
    payload = _kev_payload([_kev_entry("CVE-2026-0001")])
    del payload[field]
    with pytest.raises(KeyError):
        _kev_to_arrow(payload, FETCHED_1)


def test_count_outside_window_counts_only_outside() -> None:
    inside = datetime(2026, 10, 30, 23, 59, 59, 999000, tzinfo=timezone.utc)
    before = datetime(2026, 10, 29, 23, 59, 59, tzinfo=timezone.utc)
    after = datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc)
    assert _count_outside_window([inside, before, after], WINDOW) == 2


def test_run_segment_roundtrips_to_utc() -> None:
    """회차 경로의 시각은 정렬 가능한 UTC 형식이고 그대로 복원된다."""
    assert run_segment(FETCHED_1) == "fetched=20261031T040000Z"
    assert parse_run_fetched_at(RUN_1) == FETCHED_1
    assert run_segment(FETCHED_1) < run_segment(FETCHED_2)


# ── 2. 필드 계약 ────────────────────────────────────────────────────────


def _probe_constants() -> dict[str, tuple[str, ...]]:
    """프로브의 모듈 스코프 문자열 튜플 상수를 AST로 읽는다(import하지 않는다).

    `KEV_ENTRY_KNOWN_FIELDS = (*KEV_ENTRY_FIELDS, ...)`처럼 **앞 상수를 펼친 형태**가
    있어 `ast.literal_eval`만으로는 못 읽는다 — `*이름`은 앞서 읽은 값으로 푼다.

    Returns:
        상수 이름 → 문자열 튜플.
    """
    assert PROBE_PATH.exists(), f"프로브를 못 찾았다: {PROBE_PATH}"
    tree = ast.parse(PROBE_PATH.read_text(encoding="utf-8"))
    found: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Tuple):
            continue
        values: list[str] = []
        for element in node.value.elts:
            if isinstance(element, ast.Starred) and isinstance(element.value, ast.Name):
                values.extend(found[element.value.id])
            elif isinstance(element, ast.Constant) and isinstance(element.value, str):
                values.append(element.value)
            else:
                break
        else:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = tuple(values)
    return found


def test_probe_and_parser_share_the_field_contract() -> None:
    """프로브와 파서가 **같은 필드 집합**을 본다.

    🔴 갈라지면 프로브는 통과하고 파서만 깨진다 — 프로브의 초록이 파서의 안전을
    보증하지 못하게 된다. 이 테스트를 깨보려면 어느 한쪽 상수에서 필드를 하나 지운다.
    """
    probe = _probe_constants()
    pairs = {
        "NVD_CVE_FIELDS": REQUIRED_CVE_FIELDS,
        "NVD_CVE_KNOWN_FIELDS": KNOWN_CVE_FIELDS,
        "NVD_CHANGE_FIELDS": REQUIRED_CHANGE_FIELDS,
        "NVD_CHANGE_KNOWN_FIELDS": KNOWN_CHANGE_FIELDS,
        "KEV_TOP_FIELDS": REQUIRED_KEV_TOP_FIELDS,
        "KEV_ENTRY_FIELDS": REQUIRED_KEV_ENTRY_FIELDS,
        "KEV_ENTRY_KNOWN_FIELDS": KNOWN_KEV_ENTRY_FIELDS,
    }
    for probe_name, parser_value in pairs.items():
        assert probe_name in probe, f"프로브에 {probe_name}이 없다 — 이름이 바뀌었나"
        assert set(probe[probe_name]) == set(parser_value), probe_name


def test_probe_reader_sees_starred_expansion() -> None:
    """대조군 — AST 리더가 `*` 펼침을 실제로 풀었는지 본다.

    이것이 없으면 위 대조가 "펼친 상수를 못 읽어 건너뛰었다"와 구분되지 않는다.
    """
    probe = _probe_constants()
    assert set(probe["KEV_ENTRY_FIELDS"]) < set(probe["KEV_ENTRY_KNOWN_FIELDS"])


def test_fixtures_match_the_known_field_contract() -> None:
    """픽스처 ↔ `KNOWN_*`. 계약만 고치고 픽스처를 두면 여기서 멈춘다."""
    assert set(_cve("CVE-2026-0001")) == set(KNOWN_CVE_FIELDS)
    assert set(_change("a")) == set(KNOWN_CHANGE_FIELDS)
    assert set(_kev_entry("CVE-2026-0001")) == set(KNOWN_KEV_ENTRY_FIELDS)


@pytest.fixture(scope="module")
def probe() -> Any:
    """프로브 스크립트를 모듈로 읽는다(`main()`은 부르지 않는다 — 네트워크 없음)."""
    spec = importlib.util.spec_from_file_location("nvd_access_probe", PROBE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_drift_flags_extra_field_only(probe) -> None:
    """🔴 응답에만 있는 필드는 신호(True), 아는데 없는 선택 필드는 신호가 아니다."""
    known = ("a", "b", "c")
    assert probe.report_field_drift({"a", "b", "c", "new"}, known) is True
    assert probe.report_field_drift({"a", "b"}, known) is False
    assert probe.report_field_drift({"a", "b", "c"}, known) is False


class _ProbeResponse:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _ProbeSession:
    def __init__(self, responses: list[_ProbeResponse]) -> None:
        self._responses = list(responses)

    def get(self, url, params=None, headers=None, timeout=None):
        return self._responses.pop(0)


def test_probe_kev_verdict_rises_on_unknown_field(probe) -> None:
    """프로브 판정이 실제로 올라간다 — 출력만 하고 초록으로 끝나지 않는다."""
    entry = {**_kev_entry("CVE-2026-0001"), "brandNewField": "x"}
    session = _ProbeSession(
        [_ProbeResponse(404), _ProbeResponse(200, _kev_payload([entry]))]
    )
    assert probe.probe_kev(session) == probe.EXIT_UNDETERMINED


def test_probe_kev_passes_on_known_fields(probe) -> None:
    """대조군 — 아는 필드뿐이면 통과한다(위 테스트가 늘 실패하는 것이 아님을 보인다)."""
    session = _ProbeSession(
        [
            _ProbeResponse(404),
            _ProbeResponse(200, _kev_payload([_kev_entry("CVE-2026-0001")])),
        ]
    )
    assert probe.probe_kev(session) == probe.EXIT_OK


# ── 3. 페이지 순회 (페이크 HTTP) ────────────────────────────────────────


class _FakeHttpResponse:
    def __init__(self, status: int, body: bytes, headers: dict | None = None) -> None:
        self.status_code = status
        self.ok = 200 <= status < 300
        self.content = body
        self.headers = headers or {}

    def json(self) -> Any:
        return json.loads(self.content)

    def raise_for_status(self) -> None:
        if not self.ok:
            message = f"{self.status_code}"
            raise RuntimeError(message)


class _FakeHttp:
    """`requests.get` 대역. 응답을 순서대로 내주고 호출을 기록한다."""

    def __init__(self, responses: list[_FakeHttpResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "params": dict(params or {}), "headers": headers}
        )
        return self._responses.pop(0)


def _ok(payload: dict) -> _FakeHttpResponse:
    return _FakeHttpResponse(200, json.dumps(payload).encode("utf-8"))


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """페이싱·백오프 대기를 기록만 하고 실제로 자지 않는다."""
    recorded: list[float] = []
    monkeypatch.setattr(nvd.time, "sleep", recorded.append)
    monkeypatch.setattr(helper.time, "sleep", recorded.append)
    return recorded


def _install_http(monkeypatch, responses: list[_FakeHttpResponse]) -> _FakeHttp:
    fake = _FakeHttp(responses)
    monkeypatch.setattr(helper.requests, "get", fake)
    return fake


WINDOW_PARAMS = window_params(
    WINDOW, start_param="lastModStartDate", end_param="lastModEndDate"
)


def test_window_params_cover_the_utc_day_without_overlap() -> None:
    params = window_params(
        WINDOW, start_param="lastModStartDate", end_param="lastModEndDate"
    )
    assert params == {
        "lastModStartDate": f"{WINDOW}T00:00:00.000Z",
        "lastModEndDate": f"{WINDOW}T23:59:59.999Z",
    }


def _iter(resource: NvdResource, page_size: int) -> list:
    return list(
        resource.iter_window_pages(
            "/cves/2.0",
            window_params=WINDOW_PARAMS,
            items_key="vulnerabilities",
            page_size=page_size,
        )
    )


def test_iter_pages_advances_start_index_and_sends_header(monkeypatch, sleeps) -> None:
    http = _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("CVE-1"), _cve("CVE-2")], 3)),
            _ok(_cve_page([_cve("CVE-3")], 3, start=2)),
        ],
    )
    pages = _iter(NvdResource(api_key="k"), page_size=2)

    assert [index for index, _, _ in pages] == [0, 1]
    assert [call["params"]["startIndex"] for call in http.calls] == ["0", "2"]
    assert all(call["params"]["resultsPerPage"] == "2" for call in http.calls)
    # 🔴 구간 시작·끝은 **매 요청 짝으로** 나간다(한쪽만이면 404).
    assert all(
        call["params"]["lastModStartDate"] == f"{WINDOW}T00:00:00.000Z"
        and call["params"]["lastModEndDate"] == f"{WINDOW}T23:59:59.999Z"
        for call in http.calls
    )
    assert all(call["headers"] == {"apiKey": "k"} for call in http.calls)
    # 🔴 키는 쿼리로 새지 않는다.
    assert all("apiKey" not in call["params"] for call in http.calls)
    assert sleeps == [NVD_MIN_INTERVAL_WITH_KEY_S] * 2


def test_iter_pages_without_key_sends_no_header_and_paces_slower(
    monkeypatch, sleeps
) -> None:
    http = _install_http(monkeypatch, [_ok(_cve_page([], 0))])
    pages = _iter(NvdResource(), page_size=2000)

    assert len(pages) == 1  # 0건 구간도 1페이지(커밋 표지가 생긴다)
    assert http.calls[0]["headers"] is None
    assert sleeps == [NVD_MIN_INTERVAL_NO_KEY_S]


def test_iter_pages_fails_on_short_page_instead_of_truncating(
    monkeypatch, sleeps
) -> None:
    """🔴 원천이 신고한 수보다 적게 오면 **실패**다 — 조용히 잘라 담지 않는다."""
    _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("CVE-1"), _cve("CVE-2")], 3)),
            _ok(_cve_page([], 3, start=2)),
        ],
    )
    with pytest.raises(RuntimeError, match="빈 페이지"):
        _iter(NvdResource(), page_size=2)


def test_iter_pages_fails_when_total_moves_mid_scan(monkeypatch, sleeps) -> None:
    """🔴 순회 중 total이 바뀌면 `startIndex`가 한 건을 건너뛸 수 있다 — 실패시킨다."""
    _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("CVE-1"), _cve("CVE-2")], 4)),
            _ok(_cve_page([_cve("CVE-4")], 3, start=2)),
        ],
    )
    with pytest.raises(RuntimeError, match="totalResults"):
        _iter(NvdResource(), page_size=2)


def test_iter_pages_retries_429_through_helper(monkeypatch, sleeps) -> None:
    """429는 `helper._request`가 Retry-After를 따라 재시도한다(재사용 확인)."""
    http = _install_http(
        monkeypatch,
        [
            _FakeHttpResponse(429, b"", {"Retry-After": "7"}),
            _ok(_cve_page([_cve("CVE-1")], 1)),
        ],
    )
    pages = _iter(NvdResource(api_key="k"), page_size=2000)
    assert len(pages) == 1
    assert len(http.calls) == 2
    assert 7.0 in sleeps


# ── 4. raw 회차 프로토콜 (페이크 S3 + 임시 인스턴스) ─────────────────────


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def read(self) -> bytes:
        return self._buf.read()


class _FakePaginator:
    def __init__(self, s3: "_FakeS3") -> None:
        self._s3 = s3

    def paginate(self, Bucket: str, Prefix: str):  # noqa: N803
        keys = sorted(k for k in self._s3.objects if k.startswith(Prefix))
        yield {"Contents": [{"Key": key} for key in keys]}


class _FakeS3:
    """`S3Resource` 대역 — 쓰기·삭제 **순서**를 기록한다."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.ops: list[tuple[str, str]] = []

    def get_client(self) -> "_FakeS3":
        return self

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.objects[Key] = Body
        self.ops.append(("put", Key))

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": _FakeBody(self.objects[Key])}

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        self.objects.pop(Key, None)
        self.ops.append(("delete", Key))

    def get_paginator(self, name: str) -> _FakePaginator:
        assert name == "list_objects_v2"
        return _FakePaginator(self)


def _overwrites(s3: _FakeS3) -> list[str]:
    """두 번 이상 쓰였거나 지워진 키 — 「덮어쓰지 않는다」의 관측 셀."""
    puts = [key for op, key in s3.ops if op == "put"]
    deletes = [key for op, key in s3.ops if op == "delete"]
    return sorted({key for key in puts if puts.count(key) > 1} | set(deletes))


@pytest.fixture
def instance() -> Iterator[dg.DagsterInstance]:
    """pool 한도 1이 걸린 임시 인스턴스(SQLite, 테스트가 끝나면 사라진다)."""
    with dg.instance_for_test() as inst:
        inst.event_log_storage.set_concurrency_slots(NVD_API_POOL, 1)
        yield inst


def _land(
    s3: _FakeS3,
    instance: dg.DagsterInstance,
    *,
    fetched_at: datetime = FETCHED_1,
    force: bool = False,
    page_size: int = 2,
) -> dg.MaterializeResult:
    return land_window_pages(
        dg.build_asset_context(partition_key=WINDOW, instance=instance),
        s3=s3,
        nvd=NvdResource(api_key="k"),
        source_base=SOURCE_BASE,
        feed="cves",
        path="/cves/2.0",
        start_param="lastModStartDate",
        end_param="lastModEndDate",
        items_key="vulnerabilities",
        item_key="cve",
        id_field="id",
        page_size=page_size,
        fetched_at=fetched_at,
        force=force,
    )


def _read(s3: _FakeS3) -> tuple[list[dict], datetime, str]:
    return read_window_pages(
        s3,
        source_base=SOURCE_BASE,
        feed="cves",
        window_date=WINDOW,
        items_key="vulnerabilities",
    )


def _two_pages(prefix: str = "CVE") -> list[_FakeHttpResponse]:
    return [
        _ok(_cve_page([_cve(f"{prefix}-1"), _cve(f"{prefix}-2")], 3)),
        _ok(_cve_page([_cve(f"{prefix}-3")], 3, start=2)),
    ]


def test_land_writes_data_before_sidecar_and_commits_page_zero_last(
    monkeypatch, sleeps, instance
) -> None:
    """🔴 데이터 → 사이드카, 그리고 **page-0000(커밋 표지)이 맨 마지막**이다."""
    _install_http(monkeypatch, _two_pages())
    s3 = _FakeS3()
    result = _land(s3, instance)

    p0, p1 = page_key(RUN_1, 0), page_key(RUN_1, 1)
    assert s3.ops == [
        ("put", p1),
        ("put", p1 + SIDECAR_SUFFIX),
        ("put", p0),
        ("put", p0 + SIDECAR_SUFFIX),
    ]
    assert result.metadata["rows"] == 3
    assert result.metadata["pages"] == 2
    assert result.metadata["total_results"] == 3
    # 원문 바이트 그대로 gzip — 풀면 받은 응답과 같다.
    assert gzip.decompress(s3.objects[p1]) == _two_pages()[1].content


def test_land_skips_committed_dt_without_calling_api(
    monkeypatch, sleeps, instance
) -> None:
    """raw는 수집 시점 스냅샷이다 — 커밋된 회차가 있으면 API를 부르지 않는다."""
    s3 = _FakeS3()
    _install_http(monkeypatch, _two_pages())
    _land(s3, instance)
    http = _install_http(monkeypatch, _two_pages())
    ops_before = list(s3.ops)

    result = _land(s3, instance, fetched_at=FETCHED_2)

    assert result.metadata["decision"] == "skipped_committed"
    assert http.calls == []
    assert s3.ops == ops_before


def test_force_adds_new_run_and_keeps_old_run(monkeypatch, sleeps, instance) -> None:
    """🔴 force는 **새 회차를 더할 뿐** 옛 회차 객체를 지우거나 덮어쓰지 않는다."""
    s3 = _FakeS3()
    _install_http(monkeypatch, _two_pages("OLD"))
    _land(s3, instance)
    old_objects = {k: v for k, v in s3.objects.items() if k.startswith(RUN_1)}
    _install_http(monkeypatch, _two_pages("NEW"))

    result = _land(s3, instance, fetched_at=FETCHED_2, force=True)

    assert result.metadata["decision"] == "downloaded_new_run"
    assert result.metadata["previous_run"] == RUN_1
    # 옛 회차는 바이트까지 그대로다.
    assert {k: s3.objects[k] for k in old_objects} == old_objects
    assert _overwrites(s3) == []
    # bronze는 가장 최근 커밋 회차를 읽는다.
    pages, fetched_at, run_uri = _read(s3)
    assert fetched_at == FETCHED_2
    assert run_uri.endswith(RUN_2)
    assert pages[0]["vulnerabilities"][0]["cve"]["id"] == "NEW-1"


def test_force_failure_keeps_old_run_as_latest(monkeypatch, sleeps, instance) -> None:
    """🔴 force가 도중에 실패하면 새 회차는 미커밋이고 **옛 회차가 그대로 최신**이다."""
    s3 = _FakeS3()
    _install_http(monkeypatch, _two_pages("OLD"))
    _land(s3, instance)
    _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("NEW-1"), _cve("NEW-2")], 5)),
            _ok(_cve_page([_cve("NEW-3")], 5, start=2)),
            _ok(_cve_page([], 5, start=3)),
        ],
    )
    with pytest.raises(RuntimeError, match="빈 페이지"):
        _land(s3, instance, fetched_at=FETCHED_2, force=True)

    assert page_key(RUN_2, 0) + SIDECAR_SUFFIX not in s3.objects
    pages, fetched_at, _ = _read(s3)
    assert fetched_at == FETCHED_1
    assert pages[0]["vulnerabilities"][0]["cve"]["id"] == "OLD-1"
    assert _overwrites(s3) == []


def test_read_ignores_uncommitted_newer_run(monkeypatch, sleeps, instance) -> None:
    """미커밋 회차(표지 없음)는 더 최근이어도 읽지 않는다."""
    s3 = _FakeS3()
    _install_http(monkeypatch, _two_pages("OLD"))
    _land(s3, instance)
    newer = json.dumps(_cve_page([_cve("HALF-1")], 1)).encode()
    s3.objects[page_key(RUN_2, 0)] = gzip.compress(newer)  # 사이드카 없음

    _, fetched_at, _ = _read(s3)
    assert fetched_at == FETCHED_1


def test_land_leaves_no_commit_when_scan_fails(monkeypatch, sleeps, instance) -> None:
    """순회가 절단으로 실패하면 표지가 **없다**(미완결이 드러난다)."""
    _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("CVE-1"), _cve("CVE-2")], 5)),
            _ok(_cve_page([_cve("CVE-3")], 5, start=2)),
            _ok(_cve_page([], 5, start=3)),
        ],
    )
    s3 = _FakeS3()
    with pytest.raises(RuntimeError, match="빈 페이지"):
        _land(s3, instance)
    assert page_key(RUN_1, 0) + SIDECAR_SUFFIX not in s3.objects

    with pytest.raises(RuntimeError, match="raw 미완결"):
        _read(s3)


def test_land_rejects_duplicate_ids_before_commit(
    monkeypatch, sleeps, instance
) -> None:
    """🔴 페이지 경계에서 같은 ID가 두 번이면 **표지를 쓰기 전에** 실패한다(I1)."""
    _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("CVE-1"), _cve("CVE-2")], 3)),
            _ok(_cve_page([_cve("CVE-2")], 3, start=2)),
        ],
    )
    s3 = _FakeS3()
    with pytest.raises(RuntimeError, match="두 번"):
        _land(s3, instance)
    assert page_key(RUN_1, 0) + SIDECAR_SUFFIX not in s3.objects
    dt_uri = f"{SOURCE_BASE}/cves/dt={WINDOW}"
    assert latest_committed_run(s3, dt_uri, "page-0000.json.gz") is None


def test_land_refuses_to_reuse_run_path(monkeypatch, sleeps, instance) -> None:
    """같은 회차 시각으로 다시 쓰려 하면 덮어쓰지 않고 실패한다."""
    s3 = _FakeS3()
    _install_http(monkeypatch, _two_pages())
    _land(s3, instance)
    snapshot = dict(s3.objects)
    _install_http(monkeypatch, _two_pages("NEW"))

    with pytest.raises(RuntimeError, match="이미 있다"):
        _land(s3, instance, force=True)
    assert s3.objects == snapshot


def test_land_requires_pool_limit_before_calling_api(monkeypatch, sleeps) -> None:
    """🔴 pool 한도가 없거나 1이 아니면 **API를 부르기 전에** 실패한다(I2)."""
    http = _install_http(monkeypatch, _two_pages())
    with dg.instance_for_test() as unlimited:
        with pytest.raises(RuntimeError, match="nvd_api"):
            _land(_FakeS3(), unlimited)
        unlimited.event_log_storage.set_concurrency_slots(NVD_API_POOL, 2)
        with pytest.raises(RuntimeError, match="nvd_api"):
            _land(_FakeS3(), unlimited)
    assert http.calls == []


def test_nvd_raw_assets_declare_the_pool() -> None:
    """정의 로드 축 — NVD를 부르는 raw 자산은 pool을 선언하고 KEV는 하지 않는다."""
    assert raw_nvd_cves.op.pool == NVD_API_POOL
    assert raw_nvd_cve_history.op.pool == NVD_API_POOL
    assert raw_nvd_kev.op.pool is None


def test_read_window_pages_roundtrip(monkeypatch, sleeps, instance) -> None:
    _install_http(monkeypatch, _two_pages())
    s3 = _FakeS3()
    _land(s3, instance)

    pages, fetched_at, _ = _read(s3)
    arrow = _cves_to_arrow(pages, WINDOW, fetched_at)
    assert arrow.column("cve_id").to_pylist() == ["CVE-1", "CVE-2", "CVE-3"]
    assert set(arrow.column("fetched_at").to_pylist()) == {FETCHED_1}


def test_read_window_pages_rejects_tampered_object(
    monkeypatch, sleeps, instance
) -> None:
    _install_http(monkeypatch, _two_pages())
    s3 = _FakeS3()
    _land(s3, instance)
    s3.objects[page_key(RUN_1, 1)] = gzip.compress(b'{"totalResults": 3}')

    with pytest.raises(RuntimeError, match="무결성"):
        _read(s3)


def test_sidecar_hash_is_content_not_time() -> None:
    """`mtime=0` — 같은 본문이면 같은 해시다(압축 시각을 세지 않는다)."""
    first, second = _FakeS3(), _FakeS3()
    put_gzip_with_sidecar(first, BUCKET, "k", b"same")
    put_gzip_with_sidecar(second, BUCKET, "k", b"same")
    assert first.objects["k"] == second.objects["k"]
    assert (
        first.objects["k" + SIDECAR_SUFFIX].decode()
        == hashlib.sha256(first.objects["k"]).hexdigest()
    )


# ── 5. KEV ───────────────────────────────────────────────────────────────

KEV_ROOT = "raw/nvd/kev"


def _land_kev_with(
    monkeypatch, s3: _FakeS3, payload: dict, *, fetched_at=FETCHED_1, force=False
) -> tuple[_FakeHttp, Callable[[], dg.MaterializeResult]]:
    http = _install_http(monkeypatch, [_ok(payload)])

    def run() -> dg.MaterializeResult:
        return _land_kev(
            dg.build_asset_context(), s3=s3, fetched_at=fetched_at, force=force
        )

    return http, run


def test_land_kev_sends_no_nvd_key_header(monkeypatch, sleeps) -> None:
    """🔴 KEV 요청에는 헤더가 없다 — NVD `apiKey`가 cisa.gov로 나가지 않는다."""
    s3 = _FakeS3()
    http, run = _land_kev_with(
        monkeypatch, s3, _kev_payload([_kev_entry("CVE-2026-0001")])
    )
    run()
    assert len(http.calls) == 1
    assert http.calls[0]["headers"] is None
    assert "cisa.gov" in http.calls[0]["url"]
    run_key = f"{KEV_ROOT}/dt=2026-10-31/{run_segment(FETCHED_1)}"
    assert latest_committed_run(s3, f"s3://{BUCKET}/{KEV_ROOT}", "kev.json.gz") == (
        run_key
    )


def test_land_kev_rejects_count_mismatch_before_landing(monkeypatch, sleeps) -> None:
    s3 = _FakeS3()
    _, run = _land_kev_with(
        monkeypatch, s3, _kev_payload([_kev_entry("CVE-2026-0001")], count=2)
    )
    with pytest.raises(RuntimeError, match="잘린"):
        run()
    assert s3.ops == []


def test_land_kev_rejects_duplicate_before_landing(monkeypatch, sleeps) -> None:
    s3 = _FakeS3()
    entries = [_kev_entry("CVE-2026-0001"), _kev_entry("CVE-2026-0001")]
    _, run = _land_kev_with(monkeypatch, s3, _kev_payload(entries))
    with pytest.raises(RuntimeError, match="중복"):
        run()
    assert s3.ops == []


KEV_ROOT_URI = f"s3://{BUCKET}/{KEV_ROOT}"
# FETCHED_1과 같은 UTC 날(dt=2026-10-31)의 나중 시각.
FETCHED_1_LATER = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)


def test_land_kev_skips_same_day_without_force(monkeypatch, sleeps) -> None:
    """대조군 — 같은 dt에 커밋된 회차가 있으면 force 없이는 API를 부르지 않는다."""
    s3 = _FakeS3()
    _, run = _land_kev_with(monkeypatch, s3, _kev_payload([_kev_entry("A")]))
    run()
    http, run = _land_kev_with(
        monkeypatch, s3, _kev_payload([_kev_entry("B")]), fetched_at=FETCHED_1_LATER
    )
    assert run().metadata["decision"] == "skipped_committed"
    assert http.calls == []


def test_land_kev_force_adds_run_on_same_day(monkeypatch, sleeps) -> None:
    """🔴 같은 dt에서 force는 **새 회차를 더하고** 옛 회차를 바이트까지 남긴다."""
    s3 = _FakeS3()
    _, run = _land_kev_with(monkeypatch, s3, _kev_payload([_kev_entry("A")]))
    run()
    old = dict(s3.objects)
    _, run = _land_kev_with(
        monkeypatch,
        s3,
        _kev_payload([_kev_entry("B")]),
        fetched_at=FETCHED_1_LATER,
        force=True,
    )
    assert run().metadata["decision"] == "downloaded_new_run"

    assert {k: s3.objects[k] for k in old} == old
    assert _overwrites(s3) == []
    latest = latest_committed_run(s3, KEV_ROOT_URI, "kev.json.gz")
    assert latest == f"{KEV_ROOT}/dt=2026-10-31/{run_segment(FETCHED_1_LATER)}"


def test_kev_latest_run_spans_days(monkeypatch, sleeps) -> None:
    """최신 회차는 dt를 가로질러 고른다(다른 날이면 force 없이도 새 회차다)."""
    s3 = _FakeS3()
    _, run = _land_kev_with(monkeypatch, s3, _kev_payload([_kev_entry("A")]))
    run()
    _, run = _land_kev_with(
        monkeypatch, s3, _kev_payload([_kev_entry("B")]), fetched_at=FETCHED_2
    )
    run()
    latest = latest_committed_run(s3, KEV_ROOT_URI, "kev.json.gz")
    assert latest == f"{KEV_ROOT}/dt=2026-11-01/{run_segment(FETCHED_2)}"


def test_land_kev_refuses_to_reuse_run_path(monkeypatch, sleeps) -> None:
    """같은 회차 시각으로 force하면 덮어쓰지 않고 실패한다(`assert_new_run_prefix`)."""
    s3 = _FakeS3()
    _, run = _land_kev_with(monkeypatch, s3, _kev_payload([_kev_entry("A")]))
    run()
    snapshot = dict(s3.objects)
    _, run = _land_kev_with(
        monkeypatch, s3, _kev_payload([_kev_entry("B")]), force=True
    )
    with pytest.raises(RuntimeError, match="이미 있다"):
        run()
    assert s3.objects == snapshot


# ── 6. Iceberg 적재 모드 (SQLite 카탈로그) ───────────────────────────────

IDENTIFIER = "nvd_probe.table"


class _FakeTableResource:
    name = "probe"
    schema_ = "nvd_probe"
    table = "table"


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    cat = SqlCatalog(
        "probe",
        uri=f"sqlite:///{tmp_path / 'catalog.db'}",
        warehouse=f"file://{warehouse}",
    )
    monkeypatch.setattr(
        "dagster_project.common.helper._load_iceberg_table",
        lambda _resource: (cat, IDENTIFIER),
    )
    return cat


def _kev_arrow(n: int) -> pa.Table:
    entries = [_kev_entry(f"CVE-2026-{i:04d}") for i in range(n)]
    return _kev_to_arrow(_kev_payload(entries), FETCHED_1)


def _append_variant(context, *, iceberg_table, arrow, extra_metadata=None):
    """변이체 — `overwrite_iceberg_table`의 overwrite를 append로 바꾼 것."""
    catalog, identifier = helper._load_iceberg_table(iceberg_table)
    ensure_table(catalog, identifier, arrow.schema).append(arrow)


def _assert_full_replace(catalog, loader) -> None:
    """두 번 적재하면 두 번째 내용만 남고 스냅샷 루트가 하나인가."""
    context = dg.build_asset_context()
    loader(context, iceberg_table=_FakeTableResource(), arrow=_kev_arrow(3))
    loader(context, iceberg_table=_FakeTableResource(), arrow=_kev_arrow(2))

    table = catalog.load_table(IDENTIFIER)
    assert table.scan().to_arrow().num_rows == 2
    snapshots = table.metadata.snapshots
    assert len(snapshots) > 1
    assert len([s for s in snapshots if s.parent_snapshot_id is None]) == 1


def test_kev_overwrite_is_idempotent_and_keeps_lineage(catalog) -> None:
    """🔴 전체 교체 두 번에 두 번째 내용만 남고, 스냅샷 루트는 하나(drop 아님)."""
    _assert_full_replace(catalog, overwrite_iceberg_table)


def test_full_replace_check_catches_append_variant(catalog) -> None:
    """음성 대조 — 같은 검사가 **append로 바꾼 변이체**는 잡는다(둔감하지 않다)."""
    with pytest.raises(AssertionError):
        _assert_full_replace(catalog, _append_variant)


def test_date_partition_filter_isolates_window(catalog) -> None:
    """`window_date`가 **date 타입**이어도 문자열 필터로 그 파티션만 교체된다."""

    def load(window: str, ids: list[str]) -> None:
        pages = [_cve_page([_cve(i) for i in ids], len(ids))]
        replace_partition_in_iceberg(
            dg.build_asset_context(partition_key=window),
            iceberg_table=_FakeTableResource(),
            arrow=_cves_to_arrow(pages, window, FETCHED_1),
            partition_column="window_date",
            partition_value=window,
        )

    load("2026-10-29", ["CVE-A", "CVE-B"])
    load(WINDOW, ["CVE-A", "CVE-C", "CVE-D"])
    load(WINDOW, ["CVE-A", "CVE-C", "CVE-D"])  # 재실행 — 멱등

    table = catalog.load_table(IDENTIFIER)
    assert table.scan().to_arrow().num_rows == 5
    on_window = table.scan(row_filter=EqualTo("window_date", WINDOW)).to_arrow()
    assert on_window.num_rows == 3
