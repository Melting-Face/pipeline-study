{{ config(tags=['silver']) }}

-- 뉴스 중복 제거 — `article_id` 기준 **최초 수집본**을 남긴다.
--
-- 🔴 여기서는 중복 제거를 **하고**, `equity_prices_daily`에서는 **안 한다.**
-- 축이 다르기 때문이다.
--   · 시세: 중복이 생길 수 있는 경로가 파티션 **안**뿐인데 그건 교체 적재가
--     이미 막는다. dedup을 넣으면 그 보장이 깨져도 테스트가 통과한다.
--   · 뉴스: 원천이 `published_utc`를 **정정**하면 같은 기사가 다른 UTC 하루로
--     옮겨가 **파티션을 가로질러** 두 벌 남는다. 교체 적재는 파티션 안만 보므로
--     이 축을 막지 못한다 — 여기서 막는 것이 유일한 지점이다.
--
-- 최초 수집본을 남기는 이유: 나중 수집본은 원천이 값을 고친 뒤의 것이다.
-- "우리가 그때 받은 것"이 PIT의 기준이므로 먼저 받은 쪽을 채택한다.
--
-- 🔴 `QUALIFY`를 쓰지 않는다 — Trino가 지원하지 않는다(방언 교정 대상 밖으로
-- 두려면 두 엔진 공통 문법만 쓴다). `row_number()` + 서브쿼리로 같은 일을 한다.

with ranked as (
    select
        article_id,
        published_date,
        published_at,
        title,
        article_url,
        publisher,
        tickers,
        insights,
        ingested_at,
        row_number() over (
            partition by article_id
            order by ingested_at asc, published_date asc
        ) as ingest_rank
    from {{ source('polygon_market', 'news_articles') }}
)

select
    article_id,
    published_date,
    published_at,
    title,
    article_url,
    publisher,
    tickers,
    insights,
    ingested_at
from ranked
where ingest_rank = 1
