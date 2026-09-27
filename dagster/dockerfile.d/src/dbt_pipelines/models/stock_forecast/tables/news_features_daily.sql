{{ config(tags=['silver']) }}

-- 종목별·거래일별 뉴스 피처.
--
-- 배정된 거래일(`effective_trade_date`)로 집계한다 — 발행 달력일이 아니다.
-- 그 차이가 `news_session_assignment`의 존재 이유이고, 여기서 잘못 쓰면 앞 모델의
-- 방어가 통째로 무의미해진다.
--
-- 🔴 감성은 **원천이 준 값**이다. 우리가 NLP로 계산하지 않는다 — 계산했다면
-- 그 모델의 학습 시점이 또 하나의 누수 축이 된다.
--
-- ⚠️ 여기서 만드는 것은 **과거 정보만**이다. 라벨(미래 수익률)은 다른 모델에
-- 격리돼 있고 이 모델은 그것을 참조하지 않는다.

-- ⚠️ 별칭을 `assignment`로 두지 않는다 — sqlfluff sparksql 방언이 **예약어**로
-- 보고 RF04로 막는다(같은 이유로 테스트 파일에서도 `assigned`를 쓴다).
with ticker_rows as (
    select
        assigned.article_id,
        assigned.effective_trade_date,
        ticker_unnest.ticker_item as ticker
    from {{ ref('news_session_assignment') }} as assigned
        {{ unnest_array('assigned.tickers', 'ticker_unnest', 'ticker_item') }}
    where assigned.effective_trade_date is not null
),

sentiment_rows as (
    select
        assigned.article_id,
        assigned.effective_trade_date,
        insight_item.ticker,
        insight_item.sentiment
    from {{ ref('news_session_assignment') }} as assigned
        {{ unnest_array('assigned.insights', 'insight_unnest', 'insight_item') }}
    where assigned.effective_trade_date is not null
),

article_counts as (
    select
        ticker,
        effective_trade_date as trade_date,
        count(distinct article_id) as article_count
    from ticker_rows
    group by ticker, effective_trade_date
),

sentiment_counts as (
    select
        ticker,
        effective_trade_date as trade_date,
        count(distinct case when sentiment = 'positive' then article_id end)
            as positive_count,
        count(distinct case when sentiment = 'negative' then article_id end)
            as negative_count,
        count(distinct case when sentiment = 'neutral' then article_id end)
            as neutral_count
    from sentiment_rows
    group by ticker, effective_trade_date
)

select
    article_counts.ticker,
    article_counts.trade_date,
    article_counts.article_count,
    coalesce(sentiment_counts.positive_count, 0) as positive_count,
    coalesce(sentiment_counts.negative_count, 0) as negative_count,
    coalesce(sentiment_counts.neutral_count, 0) as neutral_count,
    -- 🔴 감성 순점수. 분모를 `article_count`가 아니라 **감성이 달린 기사 수**로
    -- 두면 태그 없는 기사가 중립으로 섞여 값이 희석된다. 분모를 명시해 둔다.
    coalesce(sentiment_counts.positive_count, 0)
    - coalesce(sentiment_counts.negative_count, 0) as sentiment_net,
    coalesce(sentiment_counts.positive_count, 0)
    + coalesce(sentiment_counts.negative_count, 0)
    + coalesce(sentiment_counts.neutral_count, 0) as sentiment_tagged_count
from article_counts
left join sentiment_counts
    on
        article_counts.ticker = sentiment_counts.ticker
        and article_counts.trade_date = sentiment_counts.trade_date
