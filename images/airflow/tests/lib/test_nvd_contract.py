"""🔴 프로브 ↔ 파서 필드 계약 대조(NVD·KEV).

Dagster `tests/test_nvd_parse.py` §2를 옮겼다 — 파서 상수의 정본이
`lakehouse.nvd_parse`가 됐다. 프로브(`scripts/nvd_access_probe.py`)는 PEP 723
단독 실행 스크립트라 패키지를 import할 수 없어 두 벌이 존재하고, 갈라지면
**프로브는 통과하고 파서만 깨진다**.
그래서 AST로 값만 꺼내 대조한다(의도한 결합).
"""

import ast
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from lakehouse.nvd_parse import (
    KNOWN_CHANGE_FIELDS,
    KNOWN_CVE_FIELDS,
    KNOWN_KEV_ENTRY_FIELDS,
    REQUIRED_CHANGE_FIELDS,
    REQUIRED_CVE_FIELDS,
    REQUIRED_KEV_ENTRY_FIELDS,
    REQUIRED_KEV_TOP_FIELDS,
)

# tests/lib/ → tests/ → airflow/ → images/ → 저장소 루트 순으로 네 단계 올라간다.
PROBE_PATH = Path(__file__).resolve().parents[4] / "scripts" / "nvd_access_probe.py"


def _kev_entry(cve_id: str) -> dict[str, Any]:
    return {
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
        "knownRansomwareCampaignUse": "Unknown",
    }


def _kev_payload(entries: list[dict[str, Any]]) -> dict:
    return {
        "title": "합성 KEV",
        "catalogVersion": "2026.10.30",
        "dateReleased": "2026-10-30T17:59:11.4512Z",
        "count": len(entries),
        "vulnerabilities": entries,
    }


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
