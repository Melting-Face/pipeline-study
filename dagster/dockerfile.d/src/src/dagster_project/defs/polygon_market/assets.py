"""Polygon(Massive) 시세·뉴스 bronze 적재 에셋 (공개 HTTP API → Iceberg, 일자 파티션).

두 축 모두 **일자 파티션 교체**다(`replace_partition_in_iceberg`). append를 쓰지
않는 이유는 재실행·백필이 전제이기 때문이고, `replace`(=`drop_table`)를 쓰지 않는
이유는 스냅샷 계보가 끊기기 때문이다 — `frankfurter_fx`와 같은 판단이다.

🔴 **뉴스가 append가 아닌 것이 RSS 설계와 갈리는 지점이다.**
    RSS는 롤링 윈도우라 폴링 주기가 피드 깊이를 넘으면 기사가 **조용히 유실**되고,
    append 자산에는 LOOKBACK 개념이 없어 그 유실이 에러로 드러나지 않는다.
    Polygon 뉴스는 `published_utc`로 날짜 구간을 질의할 수 있어 그 축이 사라진다.

🔴 **요청한 날짜와 원천이 돌려준 날짜를 둘 다 담는다.**
    `frankfurter_fx` 실측: 휴장일 요청에 원천이 **직전 영업일 값을 HTTP 200으로
    조용히 돌려준다**. 상태코드로도 행 수로도 값의 범위로도 잡히지 않고 **날짜
    필드 하나만 다르다.** 그래서 `trade_date`(요청)와 `source_date`(응답 bar의 `t`)를
    나란히 두고 일치 여부를 메타데이터에 남긴다 — 판정하지 않고 **관측**만 한다.

🔴 **결측은 조용히 채우지 않는다.** 필수 필드가 없으면 KeyError로 드러낸다
    (`REQUIRED_BAR_FIELDS`·`REQUIRED_NEWS_FIELDS`). 가격을 None으로 채우면 silver의
    수익률이 NULL이 되고 모델은 그 종목을 조용히 건너뛴다 — 어디서도 에러가 나지 않는다.

주의: Dagster가 context를 클래스 identity로 검사하므로, 자산 모듈에서는
`from __future__ import annotations`(어노테이션 문자열화)를 사용하지 않는다.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pyarrow as pa
from dagster_iceberg.resource import IcebergTableResource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.helper import replace_partition_in_iceberg
from dagster_project.common.polygon import PolygonResource
from dagster_project.defs.polygon_market.constants import (
    GROUP_NAME,
    GROUPED_DAILY_PATH,
    NEWS_PAGE_LIMIT,
    NEWS_PATH,
    PARTITION_START_DATE,
    PARTITION_TIMEZONE,
)

# 일자 파티션. 시작일 리터럴 고정·타임존 UTC의 근거는 constants.py 주석 참조
# (둘 다 조용히 어긋나는 축이고, 시작일은 프로브 실측값이다).
DAILY_PARTITIONS = dg.DailyPartitionsDefinition(
    start_date=PARTITION_START_DATE,
    timezone=PARTITION_TIMEZONE,
)

# 뉴스 페이징 상한. 🔴 `while next_url`을 무한히 돌리지 않는다 — 원천이 커서를
# 잘못 주면 같은 페이지를 영원히 받으며 쿼터만 태운다. 상한에 닿으면 조용히
# 끊지 않고 **실패**시킨다(잘린 파티션을 정상으로 적재하는 것이 더 비싸다).
NEWS_MAX_PAGES = 20

# 🔴 **프로브와 짝을 이루는 상수다.**
# `scripts/stock_source_access_probe.py`의 `POLYGON_BAR_FIELDS`·`POLYGON_NEWS_FIELDS`와
# 같아야 한다. 프로브는 PEP 723 단독 실행이라 이 패키지를 import할 수 없어 상수가
# 두 벌 존재하는데, 두 벌이 조용히 갈라지면 **프로브는 통과하고 파서만 깨진다**.
# 그 갈라짐은 `tests/test_polygon_market_parse.py`의
# `test_probe_and_parser_share_the_field_contract`가 AST 대조로 막는다(의도한 결합).
REQUIRED_BAR_FIELDS = ("T", "o", "h", "l", "c", "v", "t")

# 뉴스의 필수는 둘뿐이다 — `id`(중복 제거 키)와 `published_utc`(이벤트타임 축).
# 🔴 `published_utc`를 수집 시각으로 대체하면 **Benzinga RSS와 같은 실패**가 된다
#    (실측: 10건 전부 동일한 피드 생성 시각). 제목·링크·퍼블리셔는 없어도 레코드가
#    성립하므로(건수 집계에는 기여한다) 빈 문자열로 둔다 — 행을 버리면 커버리지
#    손실이 조용해진다.
REQUIRED_NEWS_FIELDS = ("id", "published_utc")

# bronze 스키마 — grouped daily의 bar 하나가 한 행이다.
#
# 수치를 float64로 두는 이유: 원천이 JSON **수치**로 준다. 문자열로 담는 것이
# 원문 보존인 다른 데이터셋(usgs_water·mimic_iv)과 축이 다르다 — 저쪽은 원천이
# 문자열이라 문자열이 원문이고, 여기서는 문자열화가 오히려 변환이다(frankfurter와 동일).
#
# `volume`이 int64가 아닌 float64인 이유: 원천이 정수로 주지 않을 수 있고(분할·조정),
# 청크 간 타입 불일치가 Iceberg append에서 터지는 것보다 **넓게 받는 편이 싸다**.
OHLCV_SCHEMA = pa.schema(
    [
        # 우리가 요청한 날짜 = Dagster 파티션 키. 파티션 교체의 필터 대상이다.
        ("trade_date", pa.string()),
        # 응답 bar의 `t`(epoch ms)에서 유도한 날짜. trade_date와 다를 수 있다.
        ("source_date", pa.string()),
        ("ticker", pa.string()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        # 🔴 필수는 아니지만 **버리는 비용이 비대칭이라** 받아 둔다.
        # 무료 플랜의 과거 범위가 롤링 윈도우여서(프로브 실측), 나중에 컬럼을
        # 추가해 재적재하려 하면 **초기 파티션은 이미 권한 밖**이다. 없으면 null.
        ("vwap", pa.float64()),
        ("trade_count", pa.int64()),
        # 처리시간(수집 시각).
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)

# bronze 스키마 — 기사 하나가 한 행이다.
#
# 🔴 **본문(`description`)을 담지 않는다.** 저작물 저장 축을 피하고 메타데이터만
#    담는다(무료 플랜이 "Individual use"이고 재배포가 금지돼 있다).
NEWS_SCHEMA = pa.schema(
    [
        # 우리가 요청한 UTC 하루 = Dagster 파티션 키.
        ("published_date", pa.string()),
        # 원천이 주는 안정 ID. 중복 제거의 단일 축이라 해시를 따로 만들지 않는다.
        ("article_id", pa.string()),
        # 🔴 이벤트타임. 누수 방지(세션 배정) 설계 전체가 이 컬럼 위에 선다.
        ("published_at", pa.timestamp("us", tz="UTC")),
        ("title", pa.string()),
        ("article_url", pa.string()),
        ("publisher", pa.string()),
        # 종목별 피처의 조인 키. 배열로 두고 전개는 silver에서 한다
        # (bronze는 원천 형태를 보존한다).
        ("tickers", pa.list_(pa.string())),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


def _epoch_ms_to_date(epoch_ms: int) -> str:
    """Epoch 밀리초를 UTC 날짜 문자열로 바꾼다.

    grouped daily의 `t`는 그 거래일 **미 동부 자정**의 epoch ms다. UTC로는 같은 날
    04:00(EDT) 또는 05:00(EST)이라 UTC 날짜가 거래일과 일치한다.

    ⚠️ 이 일치는 **가정이 아니라 관측 대상**이다 — 원천이 `t`의 기준을 바꾸면
    `source_date`가 `trade_date`와 어긋나고, 그 어긋남이 메타데이터에 드러난다.
    여기서 보정하지 않는 이유가 그것이다.

    Args:
        epoch_ms: 응답 bar의 `t` 값.

    Returns:
        "YYYY-MM-DD" 형태의 UTC 날짜.
    """
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).date().isoformat()


def _grouped_to_arrow(
    payload: dict[str, Any],
    trade_date: str,
    ingested_at: datetime,
) -> pa.Table:
    """Grouped daily 응답을 bronze Arrow 테이블로 편다.

    응답 형태는 `{"status", "resultsCount", "results": [bar, ...]}` 이고 bar는
    `T`(티커)·`o`·`h`·`l`·`c`(OHLC)·`v`(거래량)·`t`(epoch ms)를 갖는다.

    외부 응답이므로 낙관적으로 읽지 않는다. 다만 `REQUIRED_BAR_FIELDS`는 **없으면
    레코드가 성립하지 않으므로** KeyError로 드러나게 둔다.

    🔴 **휴장일(`results` 부재·빈 배열)은 에러가 아니라 0행이다.** 미 증시는
    주말·공휴일에 닫으므로 빈 파티션이 정상이고, 여기서 예외를 올리면 백필이
    공휴일마다 멈춘다.

    Args:
        payload: grouped daily 응답 JSON.
        trade_date: 이번 파티션 키(= 요청한 날짜, "YYYY-MM-DD").
        ingested_at: 이번 수집의 처리시간(모든 행에 같은 값이 들어간다).

    Returns:
        OHLCV_SCHEMA를 따르는 Arrow 테이블(티커당 1행, 휴장일이면 0행).

    Raises:
        KeyError: bar에 `REQUIRED_BAR_FIELDS` 중 하나라도 없을 때.
    """
    rows = [
        {
            "trade_date": trade_date,
            "source_date": _epoch_ms_to_date(bar["t"]),
            "ticker": bar["T"],
            "open": float(bar["o"]),
            "high": float(bar["h"]),
            "low": float(bar["l"]),
            "close": float(bar["c"]),
            "volume": float(bar["v"]),
            # `.get()`으로 받는다 — 없어도 레코드가 성립한다(필수 축과 다르다).
            "vwap": None if bar.get("vw") is None else float(bar["vw"]),
            "trade_count": None if bar.get("n") is None else int(bar["n"]),
            "ingested_at": ingested_at,
        }
        # 티커로 정렬해 담는다 — 같은 파티션을 다시 받아도 행 순서가 흔들리지 않아
        # 스냅샷 간 비교가 쉬워진다(응답 순서에 기대지 않는다).
        for bar in sorted(payload.get("results") or [], key=lambda b: b["T"])
    ]
    return pa.Table.from_pylist(rows, schema=OHLCV_SCHEMA)


def _news_to_arrow(
    payload: dict[str, Any],
    published_date: str,
    ingested_at: datetime,
) -> pa.Table:
    """뉴스 응답을 bronze Arrow 테이블로 편다.

    응답 형태는 `{"status", "count", "results": [article, ...]}` 이고 article은
    `id`·`published_utc`·`title`·`article_url`·`publisher{name}`·`tickers[]`를 갖는다.

    🔴 **장 마감 이후 기사를 버리지 않는다.** 그 기사들이야말로 세션 배정 모델이
    필요한 이유이고, bronze에서 걸러내면 silver가 존재를 알 수 없어 누수 검사
    테스트의 모집단이 비고 **게이트가 0건으로 조용히 통과**한다.

    Args:
        payload: 뉴스 응답 JSON.
        published_date: 이번 파티션 키(= 요청한 UTC 하루, "YYYY-MM-DD").
        ingested_at: 이번 수집의 처리시간.

    Returns:
        NEWS_SCHEMA를 따르는 Arrow 테이블(기사당 1행, 없으면 0행).

    Raises:
        KeyError: article에 `REQUIRED_NEWS_FIELDS` 중 하나라도 없을 때.
    """
    rows = [
        {
            "published_date": published_date,
            "article_id": article["id"],
            "published_at": _parse_published_utc(article["published_utc"]),
            "title": article.get("title") or "",
            "article_url": article.get("article_url") or "",
            # publisher는 중첩 객체다. 없거나 name이 비어도 레코드는 성립한다.
            "publisher": (article.get("publisher") or {}).get("name") or "",
            "tickers": article.get("tickers") or [],
            "ingested_at": ingested_at,
        }
        for article in payload.get("results") or []
    ]
    # 발행시각 → ID 순으로 정렬해 순서를 고정한다(정렬 이유는 위 시세와 같다).
    rows.sort(key=lambda row: (row["published_at"], row["article_id"]))
    return pa.Table.from_pylist(rows, schema=NEWS_SCHEMA)


def _parse_published_utc(raw: str) -> datetime:
    """원천의 ISO8601 발행시각을 tz-aware UTC로 바꾼다.

    🔴 naive로 담으면 세션 마감 경계 비교가 조용히 어긋난다(저장은 UTC, 표시·스케줄은
    KST라는 저장소 정책과 직결된다). tz 표기가 없는 값이 오면 UTC로 간주하되,
    그 경우에도 tz-aware로 만들어 이후 비교가 naive/aware 혼합으로 터지지 않게 한다.

    `.replace("Z", "+00:00")`을 쓰는 이유: 타깃이 Python 3.10이고 `fromisoformat`이
    "Z" 접미사를 직접 받는 것은 3.11부터다.

    Args:
        raw: `published_utc` 원문(예: "2026-09-23T20:01:00Z").

    Returns:
        UTC로 정규화된 tz-aware datetime.
    """
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dg.asset(
    group_name=GROUP_NAME,
    partitions_def=DAILY_PARTITIONS,
    kinds={"python", "iceberg", "bronze"},
)
def equity_ohlcv_daily(
    context: AssetExecutionContext,
    polygon: PolygonResource,
    polygon_market_ohlcv_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """하루치 전 미국 티커 OHLCV를 bronze 테이블에 파티션 단위로 적재한다.

    같은 파티션을 여러 번 실행해도 **행 수가 늘지 않는다**(멱등). 원천이 하루를
    한 번의 호출로 주므로 파티션당 요청이 1건이고, 그날 거래된 종목 전체가 응답
    그 자체라 **별도 유니버스 자산이 필요 없다.**

    🔴 **휴장일은 0행이고 그것이 정상이다.** 그래도 교체를 건너뛰지 않는다 —
    이전 실행이 잘못된 값을 넣어 뒀다면 지워야 하기 때문이다(멱등의 일부).
    """
    trade_date = context.partition_key
    ingested_at = datetime.now(tz=timezone.utc)

    payload = polygon.fetch(f"{GROUPED_DAILY_PATH}/{trade_date}", {"adjusted": "true"})
    arrow = _grouped_to_arrow(payload, trade_date, ingested_at)

    # 🔴 원천이 에코한 날짜 집합. 휴장일 대체를 드러내는 유일한 관측점이다.
    source_dates = sorted(set(arrow.column("source_date").to_pylist()))
    results_count = payload.get("resultsCount")
    context.log.info(
        "OHLCV %s: %d rows (resultsCount=%s)", trade_date, arrow.num_rows, results_count
    )

    return replace_partition_in_iceberg(
        context,
        iceberg_table=polygon_market_ohlcv_table,
        arrow=arrow,
        partition_column="trade_date",
        partition_value=trade_date,
        extra_metadata={
            # 🔴 응답이 스스로 신고한 수와 우리가 담은 행 수를 나란히 둔다 —
            # 조용한 잘림(페이징 누락 등)은 이 둘이 갈릴 때만 드러난다.
            "results_count": results_count,
            "source_dates": ", ".join(source_dates) or "(없음)",
            # 🔴 판정하지 않고 값만 남긴다(data-quality **관측** 등급).
            "date_matches_request": dg.MetadataValue.bool(
                source_dates in ([trade_date], [])
            ),
            "is_trading_day": dg.MetadataValue.bool(arrow.num_rows > 0),
            "api_requests": 1,
        },
    )


@dg.asset(
    group_name=GROUP_NAME,
    partitions_def=DAILY_PARTITIONS,
    kinds={"python", "iceberg", "bronze"},
)
def news_articles(
    context: AssetExecutionContext,
    polygon: PolygonResource,
    polygon_market_news_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """하루치 뉴스 메타데이터를 bronze 테이블에 파티션 단위로 적재한다.

    🔴 **페이징을 끝까지 따라간다.** 원천이 하루에 `NEWS_PAGE_LIMIT`을 넘는 기사를
    주면 첫 페이지만 담는 것은 **조용한 유실**이다 — 행은 생기고 수만 모자라서
    어디서도 에러가 나지 않는다. 상한에 닿으면 끊지 않고 실패시킨다.

    🔴 **장 마감 이후 기사를 버리지 않는다.** 그 기사들이 세션 배정 모델이 필요한
    이유이고(실측: 최신 50건 중 45건이 20:00Z 이후), bronze에서 걸러내면 silver의
    누수 검사 모집단이 비어 **게이트가 0건으로 조용히 통과**한다.
    """
    published_date = context.partition_key
    ingested_at = datetime.now(tz=timezone.utc)
    start = date.fromisoformat(published_date)
    end = start + timedelta(days=1)

    # 정렬은 오름차순으로 고정한다 — 커서 페이징이 안정적이고, 전 페이지를 받으므로
    # 정렬 방향이 결과를 바꾸지 않는다(프로브는 페이지 편향을 피하려고 내림차순을 쓴다).
    payload = polygon.fetch(
        NEWS_PATH,
        {
            "published_utc.gte": f"{start.isoformat()}T00:00:00Z",
            "published_utc.lt": f"{end.isoformat()}T00:00:00Z",
            "limit": str(NEWS_PAGE_LIMIT),
            "order": "asc",
            "sort": "published_utc",
        },
    )
    results = list(payload.get("results") or [])
    pages = 1
    next_url = payload.get("next_url")
    while next_url:
        if pages >= NEWS_MAX_PAGES:
            message = (
                f"{published_date}: 뉴스 페이지가 상한 {NEWS_MAX_PAGES}을 넘었다 — "
                "커서가 끝나지 않는다. 잘린 파티션을 정상으로 적재하지 않는다."
            )
            raise RuntimeError(message)
        payload = polygon.fetch_next(next_url)
        results.extend(payload.get("results") or [])
        pages += 1
        next_url = payload.get("next_url")

    arrow = _news_to_arrow({"results": results}, published_date, ingested_at)

    stamps = arrow.column("published_at").to_pylist()
    # ⚠️ 20:00Z는 **EDT 기준 마감**이다(EST면 21:00Z). 정확한 세션 경계는 silver의
    # `trading_calendar`가 DST까지 반영해 정하고, 여기서는 **관측만** 한다 —
    # 이 수가 0이면 누수 검사의 모집단이 비었다는 신호다.
    after_close = sum(1 for stamp in stamps if stamp.hour >= 20)
    tagged = sum(1 for row in arrow.column("tickers").to_pylist() if row)
    context.log.info(
        "뉴스 %s: %d건 / %d페이지 (마감후 %d)",
        published_date,
        arrow.num_rows,
        pages,
        after_close,
    )

    return replace_partition_in_iceberg(
        context,
        iceberg_table=polygon_market_news_table,
        arrow=arrow,
        partition_column="published_date",
        partition_value=published_date,
        extra_metadata={
            "pages": pages,
            "api_requests": pages,
            # 🔴 관측 셀 — 누수 방지 설계가 겨냥하는 모집단의 크기다.
            "after_session_close_approx": after_close,
            "with_ticker_tags": tagged,
            "distinct_published_at": len({stamp.isoformat() for stamp in stamps}),
        },
    )
