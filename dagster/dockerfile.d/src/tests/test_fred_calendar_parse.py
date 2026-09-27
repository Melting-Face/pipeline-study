"""FRED(ALFRED) 응답 파서 단위 테스트.

`_observations_to_arrow`·`_release_dates_to_arrow`는 **입력이 dict이고 출력이
Arrow 테이블인 순수 함수**라 인프라 없이 검증된다(`docs/test.md`의 격리 원칙).

🔴 **픽스처의 필드 집합·타입은 실측 구조를 따르고 값은 지어냈다.**
    `polygon_market`과 같은 판단이다 — 구조가 현실과 어긋나는 축은 프로브의
    계약 검사가 보고, 픽스처↔계약은 아래 `test_fixtures_match...`가 잇는다.

🔴 **이 데이터셋의 존재 이유는 vintage다.** 지표는 발표 후 개정되므로 "오늘 아는
    값"을 과거 날짜에 붙이면 **존재하지 않던 정보**를 쓰는 것이 된다. 실측에서
    UNRATE 2025-11-01은 2026-01-08까지 4.6이었다가 2026-01-09부터 4.5가 됐다.
    파서가 `realtime_start`·`realtime_end`를 버리면 그 구분이 사라지고,
    사라진 뒤에는 **어떤 테스트로도 복원할 수 없다.**
"""

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dagster_project.defs.fred_calendar.assets import (
    KNOWN_OBSERVATION_FIELDS,
    KNOWN_RELEASE_FIELDS,
    OBSERVATION_SCHEMA,
    RELEASE_SCHEMA,
    REQUIRED_OBSERVATION_FIELDS,
    REQUIRED_RELEASE_FIELDS,
    _observations_to_arrow,
    _release_dates_to_arrow,
)

INGESTED_AT = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
SERIES_ID = "UNRATE"
AS_OF_DATE = "2026-09-21"

# 실측 구조를 따른 합성 픽스처. 같은 관측일이 realtime 구간을 달리해 두 번 나오는
# 것이 이 원천의 핵심 형태다(개정). 값은 지어냈다.
OBSERVATION_PAYLOAD = {
    "realtime_start": "2024-10-09",
    "realtime_end": "9999-12-31",
    "count": 3,
    "limit": 100000,
    "offset": 0,
    "observations": [
        {
            "realtime_start": "2024-10-09",
            "realtime_end": "9999-12-31",
            "date": "2025-10-01",
            "value": "4.4",
        },
        # 🔴 같은 관측일의 **옛 vintage** — 이 행이 없으면 그 시점에 무엇을
        #    알았는지 복원할 수 없다.
        {
            "realtime_start": "2025-12-16",
            "realtime_end": "2026-01-08",
            "date": "2025-11-01",
            "value": "4.6",
        },
        {
            "realtime_start": "2026-01-09",
            "realtime_end": "9999-12-31",
            "date": "2025-11-01",
            "value": "4.5",
        },
    ],
}

RELEASE_PAYLOAD = {
    "realtime_start": AS_OF_DATE,
    "realtime_end": AS_OF_DATE,
    "count": 2,
    "limit": 1000,
    "offset": 0,
    "release_dates": [
        {
            "release_id": 10,
            "release_name": "Example Price Index",
            "release_last_updated": "2026-09-21 08:30:01-05",
            "date": AS_OF_DATE,
        },
        {
            "release_id": 53,
            "release_name": "Example Output Report",
            "release_last_updated": "2026-09-21 07:34:09-05",
            "date": AS_OF_DATE,
        },
    ],
}


# ── observations ────────────────────────────────────────────────────────


def test_observations_map_rows_and_keep_both_vintages() -> None:
    """개정된 관측일이 vintage마다 한 행씩 남는다.

    🔴 이 파이프라인에서 가장 중요한 단언이다. 같은 `observation_date`가 두 행이
    되는 것이 정상이고, 하나로 접으면 PIT(as-of) 조인이 불가능해진다.
    """
    table = _observations_to_arrow(OBSERVATION_PAYLOAD, SERIES_ID, INGESTED_AT)

    assert table.num_rows == 3
    assert table.schema == OBSERVATION_SCHEMA
    november = [
        (start, value)
        for date, start, value in zip(
            table.column("observation_date").to_pylist(),
            table.column("realtime_start").to_pylist(),
            table.column("value").to_pylist(),
            strict=True,
        )
        if date == "2025-11-01"
    ]
    assert november == [("2025-12-16", "4.6"), ("2026-01-09", "4.5")]


def test_observations_stamp_the_series_partition_key() -> None:
    """모든 행에 파티션 키(`series_id`)가 들어간다.

    응답 본문에는 series_id가 없다 — 요청 파라미터로만 존재하므로 파서가 넣는다.
    """
    table = _observations_to_arrow(OBSERVATION_PAYLOAD, SERIES_ID, INGESTED_AT)

    assert set(table.column("series_id").to_pylist()) == {SERIES_ID}


def test_observations_keep_missing_marker_verbatim() -> None:
    """FRED의 결측 표기 `"."` 를 원문 그대로 담는다.

    🔴 여기서 null로 바꾸면 **"발표되지 않았다"와 "값이 0이다"와 "파싱에 실패했다"**
    가 한 칸에 섞인다. 해석은 silver가 하고 bronze는 원천을 보존한다.
    """
    payload = {
        "observations": [
            {
                "realtime_start": "2024-10-09",
                "realtime_end": "9999-12-31",
                "date": "2025-05-26",
                "value": ".",
            }
        ]
    }
    table = _observations_to_arrow(payload, "DGS10", INGESTED_AT)

    assert table.column("value").to_pylist() == ["."]


def test_observations_sort_by_date_then_vintage() -> None:
    """관측일 → vintage 순으로 정렬해 순서를 고정한다.

    같은 파티션을 다시 받아도 행 순서가 흔들리지 않아야 스냅샷 비교가 쉽다.
    """
    table = _observations_to_arrow(OBSERVATION_PAYLOAD, SERIES_ID, INGESTED_AT)

    pairs = list(
        zip(
            table.column("observation_date").to_pylist(),
            table.column("realtime_start").to_pylist(),
            strict=True,
        )
    )
    assert pairs == sorted(pairs)


def test_observations_empty_is_zero_rows() -> None:
    """관측치가 없으면 0행이다(에러가 아니다)."""
    table = _observations_to_arrow({"observations": []}, SERIES_ID, INGESTED_AT)

    assert table.num_rows == 0
    assert table.schema == OBSERVATION_SCHEMA


def test_observations_detect_silent_truncation() -> None:
    """응답이 신고한 `count`보다 행이 적으면 **실패**시킨다.

    🔴 페이징을 안 따라가서 잘린 것을 정상으로 적재하면 행은 생기고 수만 모자라
    어디서도 에러가 나지 않는다. 기본 `limit`이 커서 지금은 안 걸리지만,
    시리즈가 길어지거나 원천이 기본값을 바꾸면 조용히 잘린다.
    """
    payload = dict(OBSERVATION_PAYLOAD)
    payload["count"] = 9999

    with pytest.raises(RuntimeError, match="잘렸다"):
        _observations_to_arrow(payload, SERIES_ID, INGESTED_AT)


@pytest.mark.parametrize("field", REQUIRED_OBSERVATION_FIELDS)
def test_observations_missing_required_field_raises(field: str) -> None:
    """필수 필드가 없으면 KeyError로 드러낸다.

    🔴 `realtime_start`가 빠지면 **vintage 축이 사라진다.** 그 상태로 적재하면
    as-of 조인이 아무 값이나 집게 되고, 결과는 그럴듯하며 틀리다.
    """
    observation = {
        k: v for k, v in OBSERVATION_PAYLOAD["observations"][1].items() if k != field
    }

    with pytest.raises(KeyError):
        _observations_to_arrow({"observations": [observation]}, SERIES_ID, INGESTED_AT)


# ── release dates ───────────────────────────────────────────────────────


def test_release_dates_map_rows() -> None:
    """릴리스 한 건이 한 행이 되고 파티션 키가 들어간다."""
    table = _release_dates_to_arrow(RELEASE_PAYLOAD, AS_OF_DATE, INGESTED_AT)

    assert table.num_rows == 2
    assert table.schema == RELEASE_SCHEMA
    assert set(table.column("as_of_date").to_pylist()) == {AS_OF_DATE}
    assert table.column("release_id").to_pylist() == [10, 53]


def test_release_dates_tolerate_missing_last_updated() -> None:
    """`release_last_updated`가 없어도 행을 버리지 않는다(필수 축과 다르다)."""
    entry = {
        k: v
        for k, v in RELEASE_PAYLOAD["release_dates"][0].items()
        if k != "release_last_updated"
    }
    table = _release_dates_to_arrow({"release_dates": [entry]}, AS_OF_DATE, INGESTED_AT)

    assert table.column("release_last_updated").to_pylist() == [""]


def test_release_dates_empty_is_zero_rows() -> None:
    """발표가 없는 날은 0행이다 — 주말·공휴일에 정상적으로 일어난다."""
    table = _release_dates_to_arrow({"release_dates": []}, AS_OF_DATE, INGESTED_AT)

    assert table.num_rows == 0
    assert table.schema == RELEASE_SCHEMA


@pytest.mark.parametrize("field", REQUIRED_RELEASE_FIELDS)
def test_release_dates_missing_required_field_raises(field: str) -> None:
    """필수 필드가 없으면 KeyError로 드러낸다."""
    entry = {k: v for k, v in RELEASE_PAYLOAD["release_dates"][0].items() if k != field}

    with pytest.raises(KeyError):
        _release_dates_to_arrow({"release_dates": [entry]}, AS_OF_DATE, INGESTED_AT)


# ── 계약 대조 ───────────────────────────────────────────────────────────


def _probe_tuple(name: str) -> tuple[str, ...]:
    """프로브 스크립트에서 모듈 스코프 문자열 튜플 상수를 AST로 읽는다.

    Args:
        name: 찾을 상수 이름.

    Returns:
        상수에 할당된 문자열 튜플.

    Raises:
        AssertionError: 상수를 찾지 못했을 때.
    """
    probe_path = (
        Path(__file__).resolve().parents[4] / "scripts" / "stock_source_access_probe.py"
    )
    assert probe_path.exists(), f"프로브를 못 찾았다: {probe_path}"
    for node in ast.parse(probe_path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    message = f"프로브에 {name} 상수가 없다 — 이름이 바뀌었는지 확인한다"
    raise AssertionError(message)


def test_fixtures_match_the_known_field_contract() -> None:
    """픽스처의 필드 집합이 아는 전체 집합과 정확히 같다."""
    assert set(OBSERVATION_PAYLOAD["observations"][0]) == set(KNOWN_OBSERVATION_FIELDS)
    assert set(RELEASE_PAYLOAD["release_dates"][0]) == set(KNOWN_RELEASE_FIELDS)


def test_probe_and_parser_share_the_field_contract() -> None:
    """프로브와 파서가 같은 필드 계약을 본다.

    🔴 상수가 두 벌인 이유와 그 위험은 `test_polygon_market_parse.py`의 같은
    테스트에 적어 뒀다 — 갈라지면 **프로브는 통과하고 파서만 깨진다.**
    """
    assert _probe_tuple("FRED_OBSERVATION_FIELDS") == REQUIRED_OBSERVATION_FIELDS
    assert _probe_tuple("FRED_RELEASE_FIELDS") == REQUIRED_RELEASE_FIELDS
    assert _probe_tuple("FRED_OBSERVATION_KNOWN_FIELDS") == KNOWN_OBSERVATION_FIELDS
    assert _probe_tuple("FRED_RELEASE_KNOWN_FIELDS") == KNOWN_RELEASE_FIELDS
