"""Frankfurter 환율 응답 파서 단위 테스트.

`_rates_json_to_arrow`는 **입력이 dict이고 출력이 Arrow 테이블인 순수 함수**라
인프라 없이 검증된다(`test_usgs_water_parse.py`와 같은 축).

🔴 **픽스처의 환율 값은 합성값이다.** 구조·필드명·타입·날짜 시프트는 실응답에서
가져왔지만 **수치 자체는 원천 데이터라 저장소에 담지 않는다**(공개 저장소이고,
데이터셋 판정은 `docs/security.md` §0이 갖는다). 검증 대상은 값이 아니라
**요청 날짜와 응답 날짜가 갈린다는 구조**이므로 합성값으로도 교훈이 보존된다.
실응답의 최상위 필드는 `amount`·`base`·`date`·`rates` 넷이다.
"""

from datetime import datetime, timezone

import pyarrow as pa
import pytest
from dagster_project.defs.frankfurter_fx.assets import FX_SCHEMA, _rates_json_to_arrow

INGESTED_AT = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)

# 영업일(화요일) 응답 — 요청한 날짜가 그대로 에코된다. 값은 합성이다.
WEEKDAY_PAYLOAD = {
    "amount": 1.0,
    "base": "EUR",
    "date": "2026-09-01",
    "rates": {"KRW": 1500.0, "USD": 1.10, "JPY": 180.0, "GBP": 0.80},
}

# 주말(토요일) 응답 — **요청 날짜가 아니라 직전 금요일이 에코된다.**
# 이 시프트는 실측으로 확인된 원천 동작이다(HTTP 200 · 통화 수는 평일과 동일).
# 값을 평일과 다르게 둔 것도 의도다 — 값이 달라도 **정상 범위라 값으로는 못
# 가른다**는 것이 이 함정의 핵심이라, 픽스처가 그 성질을 재현해야 한다.
WEEKEND_PAYLOAD = {
    "amount": 1.0,
    "base": "EUR",
    "date": "2026-09-04",
    "rates": {"KRW": 1450.0, "USD": 1.15, "JPY": 175.0, "GBP": 0.82},
}


def test_rates_flatten_to_one_row_per_currency() -> None:
    """rates 객체가 통화당 1행으로 펴진다."""
    table = _rates_json_to_arrow(WEEKDAY_PAYLOAD, "2026-09-01", INGESTED_AT)
    assert table.num_rows == 4
    assert table.schema == FX_SCHEMA


def test_rows_are_sorted_by_quote_currency() -> None:
    """행 순서가 dict 순서가 아니라 통화 코드 정렬을 따른다."""
    table = _rates_json_to_arrow(WEEKDAY_PAYLOAD, "2026-09-01", INGESTED_AT)
    assert table.column("quote_currency").to_pylist() == ["GBP", "JPY", "KRW", "USD"]


def test_rate_is_float_not_string() -> None:
    """환율은 원천이 수치로 주므로 float64로 담는다(문자열화는 변환이다)."""
    table = _rates_json_to_arrow(WEEKDAY_PAYLOAD, "2026-09-01", INGESTED_AT)
    assert table.schema.field("rate").type == pa.float64()
    rates = dict(
        zip(
            table.column("quote_currency").to_pylist(),
            table.column("rate").to_pylist(),
            strict=True,
        )
    )
    assert rates["KRW"] == pytest.approx(1500.0)


def test_requested_and_source_date_both_kept() -> None:
    """요청한 날짜와 응답이 에코한 날짜를 **둘 다** 담는다."""
    table = _rates_json_to_arrow(WEEKDAY_PAYLOAD, "2026-09-01", INGESTED_AT)
    assert set(table.column("rate_date").to_pylist()) == {"2026-09-01"}
    assert set(table.column("source_date").to_pylist()) == {"2026-09-01"}


def test_source_date_may_differ_from_partition_key() -> None:
    """🔴 주말 대체를 담아낸다 — 이 갈림이 보이지 않으면 컬럼을 둔 의미가 없다.

    가정이 아니라 **실측**이다. 토요일 날짜로 요청하면 원천은 HTTP 200에
    평일과 같은 통화 수로 응답하고 `date` 필드만 직전 금요일로 시프트한다.
    상태코드·행 수·값 범위 어느 것으로도 잡히지 않으므로, 이 두 컬럼의
    불일치가 **유일한 관측점**이다.
    """
    table = _rates_json_to_arrow(WEEKEND_PAYLOAD, "2026-09-05", INGESTED_AT)

    assert set(table.column("rate_date").to_pylist()) == {"2026-09-05"}
    assert set(table.column("source_date").to_pylist()) == {"2026-09-04"}
    # 통화 수가 평일과 같다는 것이 이 함정의 핵심이다 — 수로는 못 가른다.
    assert (
        table.num_rows
        == _rates_json_to_arrow(WEEKDAY_PAYLOAD, "2026-09-01", INGESTED_AT).num_rows
    )


def test_base_currency_comes_from_payload() -> None:
    """base는 상수가 아니라 응답에서 읽는다(파라미터로 바뀔 수 있다)."""
    table = _rates_json_to_arrow(
        {**WEEKDAY_PAYLOAD, "base": "USD"}, "2026-09-01", INGESTED_AT
    )
    assert set(table.column("base_currency").to_pylist()) == {"USD"}


def test_same_ingested_at_on_every_row() -> None:
    """한 번의 수집은 모든 행에 같은 처리시간을 남긴다."""
    table = _rates_json_to_arrow(WEEKDAY_PAYLOAD, "2026-09-01", INGESTED_AT)
    assert set(table.column("ingested_at").to_pylist()) == {INGESTED_AT}


def test_empty_rates_keeps_schema() -> None:
    """rates가 비어도 0행 테이블의 스키마는 유지된다."""
    table = _rates_json_to_arrow(
        {"amount": 1.0, "base": "EUR", "date": "2026-09-05", "rates": {}},
        "2026-09-05",
        INGESTED_AT,
    )
    assert table.num_rows == 0
    assert table.schema == FX_SCHEMA


def test_missing_date_raises() -> None:
    """date가 없으면 조용히 넘어가지 않는다 — 요청↔응답 대조 축이 무너진다."""
    with pytest.raises(KeyError):
        _rates_json_to_arrow(
            {"amount": 1.0, "base": "EUR", "rates": {"USD": 1.1}},
            "2026-09-01",
            INGESTED_AT,
        )


def test_missing_base_raises() -> None:
    """base가 없으면 환율의 기준이 사라지므로 드러나게 둔다."""
    with pytest.raises(KeyError):
        _rates_json_to_arrow(
            {"amount": 1.0, "date": "2026-09-01", "rates": {"USD": 1.1}},
            "2026-09-01",
            INGESTED_AT,
        )
