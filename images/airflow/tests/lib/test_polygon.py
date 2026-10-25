"""Polygon grouped daily 파서(`lakehouse.polygon`).

Dagster `test_polygon_market_parse.py`의 시세 부분 이식. 픽스처는 원본과 같다
(공식 문서 기반, 합성값). 뉴스 셀은 이관 범위 밖이라 Dagster에 남는다.
"""

from datetime import datetime, timezone

import pytest
import requests
from lakehouse.polygon import (
    KNOWN_BAR_FIELDS,
    OHLCV_SCHEMA,
    REQUIRED_BAR_FIELDS,
    fetch_grouped_daily,
)
from lakehouse.polygon import grouped_to_arrow as _grouped_to_arrow

INGESTED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
TRADE_DATE = "2026-09-23"

# grouped daily 응답. `t`는 그 거래일 **16:00 America/New_York**의 epoch ms다.
# 1790193600000 = 2026-09-23T20:00:00Z = 16:00 EDT (EST 구간이면 21:00Z).
# ⚠️ 초안은 "ET 자정"으로 적혀 있었는데 실측이 반증했다 — 값은 맞고 근거가 틀린
#    상태였다(자정이든 마감이든 UTC 날짜는 같아서 `source_date`가 안 깨졌다).
GROUPED_PAYLOAD = {
    "status": "OK",
    "adjusted": True,
    "queryCount": 2,
    "resultsCount": 2,
    "results": [
        {
            "T": "MSFT",
            "o": 510.5,
            "h": 515.0,
            "l": 508.25,
            "c": 512.75,
            "v": 18_400_000,
            "vw": 511.9,
            "t": 1790193600000,
            "n": 210_000,
        },
        {
            "T": "AAPL",
            "o": 250.0,
            "h": 253.4,
            "l": 249.1,
            "c": 252.8,
            "v": 44_100_000,
            "vw": 251.3,
            "t": 1790193600000,
            "n": 480_000,
        },
    ],
}


# ── grouped daily (시세) ────────────────────────────────────────────────


def test_grouped_maps_bars_into_rows() -> None:
    """bar 하나가 한 행이 되고 스키마가 고정된다."""
    table = _grouped_to_arrow(GROUPED_PAYLOAD, TRADE_DATE, INGESTED_AT)

    assert table.num_rows == 2
    assert table.schema == OHLCV_SCHEMA
    assert table.column("close").to_pylist() == [252.8, 512.75]
    assert table.column("volume").to_pylist() == [44_100_000.0, 18_400_000.0]


def test_grouped_keeps_vwap_and_trade_count() -> None:
    """VWAP(`vw`)과 거래 건수(`n`)를 버리지 않는다.

    🔴 이 두 컬럼은 **지금 담지 않으면 영구히 잃는다.** 무료 플랜의 과거 범위가
    롤링 윈도우라(프로브 실측) 나중에 컬럼을 추가해 재적재하려 할 때는 초기
    파티션이 이미 권한 밖으로 밀려나 있다. 필수 필드는 아니지만 **버리는 비용이
    비대칭**이라 받아 둔다.
    """
    table = _grouped_to_arrow(GROUPED_PAYLOAD, TRADE_DATE, INGESTED_AT)

    assert table.column("vwap").to_pylist() == [251.3, 511.9]
    assert table.column("trade_count").to_pylist() == [480_000, 210_000]


def test_grouped_tolerates_missing_vwap_and_trade_count() -> None:
    """`vw`·`n`이 없으면 null로 담고 행은 버리지 않는다.

    필수 필드(`REQUIRED_BAR_FIELDS`)와 축이 다르다 — 이 둘이 없어도 OHLCV
    레코드는 성립하므로 KeyError로 멈추지 않는다.
    """
    bar = {
        k: v for k, v in GROUPED_PAYLOAD["results"][0].items() if k not in ("vw", "n")
    }
    table = _grouped_to_arrow(
        {"status": "OK", "resultsCount": 1, "results": [bar]}, TRADE_DATE, INGESTED_AT
    )

    assert table.num_rows == 1
    assert table.column("vwap").to_pylist() == [None]
    assert table.column("trade_count").to_pylist() == [None]


def test_grouped_accepts_integer_prices() -> None:
    """가격·거래량이 정수로 와도 float로 담는다.

    🔴 **실측에서 실재한다** — 구조 덤프상 500개 bar 중 `o`·`h`·`l`·`c`·`v`가
    정수로 오는 것이 4~14건씩 있었다(JSON이 소수부 없는 수를 정수로 준다).
    캐스팅을 빼면 청크·파티션마다 Arrow 타입이 갈려 적재가 터진다.
    """
    bar = dict(GROUPED_PAYLOAD["results"][0])
    bar.update({"o": 510, "h": 515, "l": 508, "c": 512, "v": 18_400_000})
    table = _grouped_to_arrow(
        {"status": "OK", "resultsCount": 1, "results": [bar]}, TRADE_DATE, INGESTED_AT
    )

    assert table.schema == OHLCV_SCHEMA
    assert table.column("open").to_pylist() == [510.0]
    assert table.column("close").to_pylist() == [512.0]


def test_grouped_sorts_by_ticker() -> None:
    """행 순서를 티커로 고정한다.

    같은 파티션을 다시 받아도 순서가 흔들리지 않아야 스냅샷 간 비교가 쉽다
    (`frankfurter_fx`가 통화를 정렬해 담는 것과 같은 이유). 응답은 MSFT가
    먼저 왔지만 결과는 AAPL이 먼저여야 한다.
    """
    table = _grouped_to_arrow(GROUPED_PAYLOAD, TRADE_DATE, INGESTED_AT)

    assert table.column("ticker").to_pylist() == ["AAPL", "MSFT"]


def test_grouped_echoes_requested_and_source_date() -> None:
    """요청 날짜와 응답이 가리키는 날짜를 **나란히** 담는다.

    🔴 휴장일 요청에 직전 거래일 값을 200으로 조용히 주는 실패 모드가 실재한다
    (`frankfurter_fx` 실측). 상태코드·행 수·값 범위로는 안 잡히고 날짜 필드만
    다르므로, 두 값을 함께 두고 **판정하지 않고 관측**한다.
    """
    table = _grouped_to_arrow(GROUPED_PAYLOAD, TRADE_DATE, INGESTED_AT)

    assert table.column("trade_date").to_pylist() == [TRADE_DATE, TRADE_DATE]
    assert table.column("source_date").to_pylist() == ["2026-09-23", "2026-09-23"]


def test_grouped_keeps_bar_timestamp_not_just_the_date() -> None:
    """`t`를 날짜로 줄이지 않고 **타임스탬프 그대로** 담는다.

    🔴 이 컬럼 하나가 dispatch 매크로 하나를 없앤다. silver의 거래일 달력은
    "세션 마감 = 16:00 America/New_York"을 UTC로 바꿔야 하는데, 그 변환은
    엔진마다 문법이 갈려(`to_utc_timestamp` ↔ `with_timezone ... at time zone`)
    방언 매크로 + sqlfluff 스텁 한 쌍이 필요하다. 그런데 **원천이 이미 그 값을
    준다** — `t`는 EDT면 20:00Z, EST면 21:00Z로 와서 DST가 반영돼 있다.

    ⚠️ 이름을 `session_close_utc`로 하지 않는다. 실측상 `t`는 **16:00 ET에 고정된
    집계 창 표식**이고 조기 폐장일(13:00 ET)에도 같은 값이 온다 — 실제 마감이
    아니다. bronze는 원천이 준 것을 원천의 의미로 담고, "세션 마감"이라는 해석은
    가정을 함께 적어 silver가 붙인다.
    """
    table = _grouped_to_arrow(GROUPED_PAYLOAD, TRADE_DATE, INGESTED_AT)

    stamps = table.column("bar_timestamp").to_pylist()
    assert stamps == [
        datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc),
    ]
    assert all(stamp.tzinfo is not None for stamp in stamps)


def test_grouped_detects_stale_echo() -> None:
    """응답 날짜가 요청과 다르면 그 차이가 `source_date`에 그대로 남는다."""
    table = _grouped_to_arrow(GROUPED_PAYLOAD, "2026-09-26", INGESTED_AT)

    assert table.column("trade_date").to_pylist() == ["2026-09-26", "2026-09-26"]
    # 🔴 파서는 고치지도 지우지도 않는다 — 불일치를 드러내는 것이 일이다.
    assert table.column("source_date").to_pylist() == ["2026-09-23", "2026-09-23"]


def test_grouped_empty_results_is_zero_rows_not_error() -> None:
    """휴장일(resultsCount=0)은 에러가 아니라 0행이다.

    미 증시는 주말·공휴일에 닫으므로 빈 파티션이 **정상**이다. 여기서 예외를
    올리면 백필이 공휴일마다 멈춘다.
    """
    table = _grouped_to_arrow(
        {"status": "OK", "resultsCount": 0, "results": []}, TRADE_DATE, INGESTED_AT
    )

    assert table.num_rows == 0
    assert table.schema == OHLCV_SCHEMA


def test_grouped_missing_results_key_is_zero_rows() -> None:
    """`results` 키 자체가 없는 응답도 0행으로 다룬다(휴장일 응답 변형)."""
    payload = {"status": "OK", "resultsCount": 0}
    table = _grouped_to_arrow(payload, TRADE_DATE, INGESTED_AT)

    assert table.num_rows == 0


@pytest.mark.parametrize("field", REQUIRED_BAR_FIELDS)
def test_grouped_missing_required_field_raises(field: str) -> None:
    """필수 필드가 없으면 **KeyError로 드러낸다**.

    🔴 조용히 None으로 채우면 가격이 결측인 행이 bronze에 들어가고, silver의
    수익률 계산에서 NULL이 되며, 모델은 그 종목을 조용히 건너뛴다. 어디서도
    에러가 나지 않으므로 **여기서 멈춰야 한다**.
    """
    bar = {k: v for k, v in GROUPED_PAYLOAD["results"][0].items() if k != field}
    broken = {"status": "OK", "resultsCount": 1, "results": [bar]}

    with pytest.raises(KeyError):
        _grouped_to_arrow(broken, TRADE_DATE, INGESTED_AT)


def test_fixture_matches_the_known_field_contract() -> None:
    """픽스처의 bar 필드 집합이 **아는 전체 집합**과 정확히 같다.

    🔴 픽스처는 원천 약관(개인 사용 한정) 때문에 실측 값을 담을 수 없어 **합성**이다.
    현실과의 대조는 프로브가 하고(응답 ↔ `KNOWN_BAR_FIELDS`), 프로브 ↔ 상수는
    `test_polygon_contract.py`가, 남은 한 칸 **픽스처 ↔ 상수**는 이 셀이 잇는다.
    """
    assert set(GROUPED_PAYLOAD["results"][0]) == set(KNOWN_BAR_FIELDS)


# ── 계획 Review Focus 4·5 ───────────────────────────────────────────────


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.ok = status < 400
        self.headers = {}
        self.url = "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/2024-10-09?adjusted=true"

    def raise_for_status(self):
        message = f"{self.status_code} Client Error: Forbidden for url: {self.url}"
        raise requests.HTTPError(message)


def test_403_message_has_status_not_key(monkeypatch) -> None:
    """과거 경계 밖(403)은 원인을 담고, 키는 메시지에 없다(Bearer 헤더라 URL에 없다)."""
    monkeypatch.setattr("lakehouse.http.requests.get", lambda *a, **k: _Resp(403))
    with pytest.raises(requests.HTTPError) as exc:
        fetch_grouped_daily("SECRET-KEY-123", "2024-10-09")
    assert "403" in str(exc.value)
    assert "SECRET-KEY-123" not in str(exc.value)


def test_empty_api_key_rejected_before_call(monkeypatch) -> None:
    """Connection이 비면 호출하지 않는다(Review Focus 5)."""
    called = []
    monkeypatch.setattr("lakehouse.http.requests.get", lambda *a, **k: called.append(1))
    with pytest.raises(ValueError, match="키가 비었다"):
        fetch_grouped_daily("", "2024-10-09")
    assert called == []
