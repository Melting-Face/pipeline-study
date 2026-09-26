"""Polygon(Massive) 시세·뉴스 응답 파서 단위 테스트.

`_grouped_to_arrow`·`_news_to_arrow`는 **입력이 dict이고 출력이 Arrow 테이블인
순수 함수**라 인프라 없이 검증된다(`docs/test.md`의 격리 원칙).

🔴 **이 파일의 픽스처는 실측이 아니라 공식 문서 기반이다.**
    `usgs_water`·`frankfurter_fx` 픽스처가 "실측 응답을 축소한 것"인 것과
    **축이 다르다** — 작성 시점에 API 키가 없었다. 문서와 실제가 어긋나면
    이 테스트는 **초록인 채 파이프라인만 틀린다**(계획서가 경고한
    「그럴듯한 값」). 그래서 두 겹으로 막는다:

    ① 파서가 **필수 필드 결측을 KeyError로 드러낸다**(조용히 채우지 않는다).
    ② `test_probe_and_parser_share_the_field_contract`가
       `scripts/stock_source_access_probe.py`의 필드 상수와 파서의 필드 상수가
       **같은지 기계로 대조한다**. 프로브는 PEP 723 단독 실행이라 패키지를
       import할 수 없어 상수가 두 벌 존재하는데, 두 벌이 조용히 갈라지면
       프로브는 통과하고 파서만 깨진다. 그 갈라짐을 이 테스트가 막는다.

    🔴 **키를 받으면 프로브를 돌려 실제 필드 목록을 확인하고, 이 픽스처를
       실측 응답으로 교체한다.** 그 전까지 이 테스트의 초록은
       "파서가 이 모양의 입력을 올바로 다룬다"이지 "원천이 이 모양이다"가 아니다.
"""

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dagster_project.defs.polygon_market.assets import (
    NEWS_SCHEMA,
    OHLCV_SCHEMA,
    REQUIRED_BAR_FIELDS,
    REQUIRED_NEWS_FIELDS,
    _grouped_to_arrow,
    _news_to_arrow,
)

INGESTED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
TRADE_DATE = "2026-09-23"
PUBLISHED_DATE = "2026-09-23"

# grouped daily 응답. `t`는 그 거래일의 epoch ms(ET 자정 기준)다.
# 1790136000000 = 2026-09-23T04:00:00Z = 2026-09-23 00:00 EDT.
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
            "t": 1790136000000,
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
            "t": 1790136000000,
            "n": 480_000,
        },
    ],
}

NEWS_PAYLOAD = {
    "status": "OK",
    "count": 2,
    "results": [
        {
            "id": "abc123",
            "publisher": {"name": "The Motley Fool", "homepage_url": "https://x"},
            "title": "Apple announces something",
            "article_url": "https://example.test/a",
            "published_utc": "2026-09-23T13:30:00Z",
            "tickers": ["AAPL", "MSFT"],
        },
        {
            "id": "def456",
            "publisher": {"name": "Benzinga"},
            "title": "After-hours filing",
            "article_url": "https://example.test/b",
            # 🔴 20:01Z = 16:01 ET — **장 마감 직후**. 세션 배정 모델이 존재해야
            #    하는 이유이고, SEC EDGAR 실측에서도 같은 시간대가 관측됐다.
            "published_utc": "2026-09-23T20:01:00Z",
            "tickers": [],
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


# ── news (뉴스) ─────────────────────────────────────────────────────────


def test_news_maps_articles_into_rows() -> None:
    """기사 하나가 한 행이 되고 스키마가 고정된다."""
    table = _news_to_arrow(NEWS_PAYLOAD, PUBLISHED_DATE, INGESTED_AT)

    assert table.num_rows == 2
    assert table.schema == NEWS_SCHEMA
    assert table.column("article_id").to_pylist() == ["abc123", "def456"]
    assert table.column("publisher").to_pylist() == ["The Motley Fool", "Benzinga"]


def test_news_parses_published_at_as_tz_aware_utc() -> None:
    """발행시각을 tz-aware UTC로 담는다.

    🔴 이 컬럼이 누수 방지 설계 전체의 기준축이다. naive로 담으면 세션 마감
    경계 비교가 조용히 9시간(KST) 어긋난다.
    """
    table = _news_to_arrow(NEWS_PAYLOAD, PUBLISHED_DATE, INGESTED_AT)

    stamps = table.column("published_at").to_pylist()
    assert stamps[0] == datetime(2026, 9, 23, 13, 30, tzinfo=timezone.utc)
    assert stamps[1] == datetime(2026, 9, 23, 20, 1, tzinfo=timezone.utc)
    assert all(s.tzinfo is not None for s in stamps)


def test_news_keeps_after_close_articles() -> None:
    """장 마감(20:00Z=16:00 ET) 이후 기사를 버리지 않는다.

    🔴 이 기사들이야말로 세션 배정 모델이 필요한 이유다. bronze에서 걸러내면
    silver가 그 존재를 알 수 없고, 누수 검사 테스트의 모집단이 비어
    **게이트가 0건으로 조용히 통과**한다.
    """
    table = _news_to_arrow(NEWS_PAYLOAD, PUBLISHED_DATE, INGESTED_AT)

    after_close = [s for s in table.column("published_at").to_pylist() if s.hour >= 20]
    assert len(after_close) == 1


def test_news_missing_tickers_becomes_empty_list() -> None:
    """`tickers`가 없으면 빈 배열로 담는다(행을 버리지 않는다).

    티커 태그가 없는 기사는 종목별 피처에 기여하지 않지만, 있었다는 사실은
    남겨야 커버리지를 관측할 수 있다.
    """
    payload = {
        "results": [
            {k: v for k, v in NEWS_PAYLOAD["results"][0].items() if k != "tickers"}
        ]
    }
    table = _news_to_arrow(payload, PUBLISHED_DATE, INGESTED_AT)

    assert table.column("tickers").to_pylist() == [[]]


def test_news_missing_publisher_becomes_empty_string() -> None:
    """`publisher`는 없어도 레코드가 성립하므로 빈 문자열로 둔다."""
    payload = {
        "results": [
            {k: v for k, v in NEWS_PAYLOAD["results"][0].items() if k != "publisher"}
        ]
    }
    table = _news_to_arrow(payload, PUBLISHED_DATE, INGESTED_AT)

    assert table.column("publisher").to_pylist() == [""]


def test_news_empty_results_is_zero_rows() -> None:
    """기사가 없는 날은 0행이다(에러가 아니다)."""
    table = _news_to_arrow({"status": "OK", "results": []}, PUBLISHED_DATE, INGESTED_AT)

    assert table.num_rows == 0
    assert table.schema == NEWS_SCHEMA


@pytest.mark.parametrize("field", REQUIRED_NEWS_FIELDS)
def test_news_missing_required_field_raises(field: str) -> None:
    """필수 필드(`id`·`published_utc`)가 없으면 KeyError로 드러낸다.

    🔴 `published_utc`를 수집 시각으로 대체하면 **Benzinga RSS와 같은 실패**가
    된다(실측: 10건 전부 동일한 피드 생성 시각). 이벤트타임을 추측으로 채우면
    체계적 편향이 에러 없이 들어간다.
    """
    payload = {
        "results": [{k: v for k, v in NEWS_PAYLOAD["results"][0].items() if k != field}]
    }

    with pytest.raises(KeyError):
        _news_to_arrow(payload, PUBLISHED_DATE, INGESTED_AT)


# ── 🔴 프로브 ↔ 파서 필드 계약 대조 ─────────────────────────────────────


def _probe_tuple(name: str) -> tuple[str, ...]:
    """프로브 스크립트에서 모듈 스코프 문자열 튜플 상수를 읽는다.

    프로브는 PEP 723 단독 실행 스크립트라 패키지를 import할 수 없고, 반대로
    테스트도 프로브를 import하면 의존성(`requests`)을 끌어온다. 그래서 실행하지
    않고 **AST로 값만** 꺼낸다.

    Args:
        name: 찾을 모듈 스코프 상수 이름.

    Returns:
        상수에 할당된 문자열 튜플.

    Raises:
        AssertionError: 상수를 찾지 못했을 때(이름이 바뀌면 여기서 멈춘다).
    """
    # tests/ → src/ → dockerfile.d/ → dagster/ → 저장소 루트 순으로 네 단계 올라간다.
    probe_path = (
        Path(__file__).resolve().parents[4] / "scripts" / "stock_source_access_probe.py"
    )
    assert probe_path.exists(), f"프로브를 못 찾았다: {probe_path}"
    tree = ast.parse(probe_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name in targets:
            return tuple(ast.literal_eval(node.value))
    message = f"프로브에 {name} 상수가 없다 — 이름이 바뀌었는지 확인한다"
    raise AssertionError(message)


def test_probe_and_parser_share_the_field_contract() -> None:
    """프로브와 파서가 **같은 필수 필드 집합**을 본다.

    🔴 상수가 두 벌 존재하는 것은 프로브가 단독 실행이라 어쩔 수 없다. 문제는
    두 벌이 조용히 갈라질 때다 — 그러면 **프로브는 통과하고 파서만 깨진다**.
    프로브의 초록이 파서의 안전을 보증하지 못하게 되는 것이고, 그건 관측 경로가
    죽은 채 초록인 상태와 같다. 그래서 갈라짐 자체를 실패로 만든다.

    이 테스트를 깨보려면 어느 한쪽 상수에서 필드를 하나 지우면 된다.
    """
    assert _probe_tuple("POLYGON_BAR_FIELDS") == REQUIRED_BAR_FIELDS
    assert _probe_tuple("POLYGON_NEWS_FIELDS") == REQUIRED_NEWS_FIELDS
