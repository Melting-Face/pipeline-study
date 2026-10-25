"""Polygon(Massive) 뉴스 bronze 적재 에셋 (공개 HTTP API → Iceberg, 일자 파티션).

🔴 **시세(`equity_ohlcv_daily`)는 여기 없다** — 적재 소유자가 Airflow DAG
    `equity_ohlcv_daily`다(`images/airflow/dags/lakehouse/polygon.py`). 시세 파서·필드
    계약·스키마 리터럴 대조도 그쪽 테스트(`images/airflow/tests/lib/`)가 갖는다.

**일자 파티션 교체**다(`replace_partition_in_iceberg`). append를 쓰지
않는 이유는 재실행·백필이 전제이기 때문이고, `replace`(=`drop_table`)를 쓰지 않는
이유는 스냅샷 계보가 끊기기 때문이다 — `frankfurter_fx`와 같은 판단이다.

🔴 **뉴스가 append가 아닌 것이 RSS 설계와 갈리는 지점이다.**
    RSS는 롤링 윈도우라 폴링 주기가 피드 깊이를 넘으면 기사가 **조용히 유실**되고,
    append 자산에는 LOOKBACK 개념이 없어 그 유실이 에러로 드러나지 않는다.
    Polygon 뉴스는 `published_utc`로 날짜 구간을 질의할 수 있어 그 축이 사라진다.

🔴 **결측은 조용히 채우지 않는다.** 필수 필드가 없으면 KeyError로 드러낸다
    (`REQUIRED_NEWS_FIELDS`). 발행시각을 수집 시각으로 채우면 세션 배정이 체계적으로
    치우치고, 어디서도 에러가 나지 않는다.

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
# `scripts/stock_source_access_probe.py`의 `POLYGON_NEWS_FIELDS`와
# 같아야 한다. 프로브는 PEP 723 단독 실행이라 이 패키지를 import할 수 없어 상수가
# 두 벌 존재하는데, 두 벌이 조용히 갈라지면 **프로브는 통과하고 파서만 깨진다**.
# 그 갈라짐은 `tests/test_polygon_market_parse.py`의
# `test_probe_and_parser_share_the_field_contract`가 AST 대조로 막는다(의도한 결합).
# (시세 bar 쪽 짝 `POLYGON_BAR_*`는 Airflow `lakehouse/polygon.py`와 대조한다.)
# 뉴스의 필수는 둘뿐이다 — `id`(중복 제거 키)와 `published_utc`(이벤트타임 축).
# 🔴 `published_utc`를 수집 시각으로 대체하면 **Benzinga RSS와 같은 실패**가 된다
#    (실측: 10건 전부 동일한 피드 생성 시각). 제목·링크·퍼블리셔는 없어도 레코드가
#    성립하므로(건수 집계에는 기여한다) 빈 문자열로 둔다 — 행을 버리면 커버리지
#    손실이 조용해진다.
REQUIRED_NEWS_FIELDS = ("id", "published_utc")

# 🔴 **실측 응답의 전체 필드 집합이다**(값이 아니라 구조라서 재배포 축과 무관하다).
# 테스트 픽스처는 원천 약관상 **실측 값을 담을 수 없어 합성**인데, 그러면 구조가
# 현실과 어긋나도 테스트는 초록이다. 그 축을 사람 눈이 아니라 프로브가 본다:
#   - 여기 있는데 응답에 없다 → 원천이 필드를 뺐다(파서가 깨질 수 있다).
#   - 응답에 있는데 여기 없다 → **우리가 버리고 있는 새 필드**다. 조회 범위가
#     롤링 윈도우라 담을지 판단을 미루면 그만큼 영구 손실이다.
# 필수(`REQUIRED_*`)와 축이 다르다 — 이쪽은 "아는 전부", 저쪽은 "없으면 죽는 것".
KNOWN_NEWS_FIELDS = (
    "article_url",
    "author",
    "description",
    "id",
    "image_url",
    "insights",
    "keywords",
    "published_utc",
    "publisher",
    "tickers",
    "title",
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
        # 🔴 **원천이 준 티커별 감성이다 — 우리가 계산한 것이 아니다.**
        # 계획의 "감성 분석 없음"은 NLP를 돌리지 않는다는 뜻이지 받은 것을
        # 버린다는 뜻이 아니다. 뉴스도 조회 범위가 롤링 윈도우일 수 있어
        # (시세는 실측으로 확인됨) 지금 안 담으면 영구히 잃는다.
        #
        # `sentiment_reasoning`은 **담지 않는다** — 산문이라 기사 본문을
        # 적재하지 않는다는 원칙과 같은 축이고, 저장량도 크다.
        (
            "insights",
            pa.list_(pa.struct([("ticker", pa.string()), ("sentiment", pa.string())])),
        ),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


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
            # 🔴 `sentiment_reasoning`을 **의도적으로 떨군다**(스키마에도 없다).
            "insights": [
                {
                    "ticker": insight.get("ticker") or "",
                    "sentiment": insight.get("sentiment") or "",
                }
                for insight in article.get("insights") or []
            ],
            "ingested_at": ingested_at,
        }
        for article in payload.get("results") or []
    ]
    # 발행시각 → ID 순으로 정렬해 순서를 고정한다 — 같은 파티션을 다시 받아도 행 순서가
    # 흔들리지 않아 스냅샷 간 비교가 쉬워진다(응답 순서에 기대지 않는다).
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
