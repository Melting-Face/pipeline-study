{{ config(tags=['silver']) }}

-- 🔴 **이 파이프라인에서 가장 중요한 모델.**
--
-- 기사를 "발행된 달력일"이 아니라 **그 정보를 거래에 쓸 수 있었던 첫 거래일**에
-- 배정한다. 규칙 한 줄:
--
--     effective_trade_date = min{ d : d.session_close_utc > published_at }
--
-- 마감 **전** 기사는 그날, 마감 후·주말·휴장일 기사는 **다음 거래일**이다.
--
-- 왜 필요한가: 실측상 어느 평일의 최신 기사 50건 중 **45건이 20:00Z(=16:00 ET)
-- 이후** 발행이었다. 그것을 발행 달력일에 배정하면 장 마감 뒤에야 알 수 있었던
-- 정보로 그날의 수익률을 설명하게 된다 — 백테스트 성능만 좋아지고, **어디서도
-- 에러가 나지 않는다.**
--
-- 🔴 조인 범위를 7일로 묶는다. 묶지 않으면 기사 하나가 이후 **모든** 거래일과
-- 짝지어져 중간 결과가 수천만 행이 된다. 미 증시가 7일 넘게 닫힌 적은 없지만
-- 그것은 **가정**이므로, 초과 시 조용히 버리지 않고 `left join`으로 NULL을 남기고
-- `assert_news_assignment_is_complete.sql`이 그 NULL을 검사한다.
--
-- ⚠️ 마지막 거래일 이후에 발행된 기사는 배정할 세션이 아직 없어 NULL이 정상이다
-- (시세보다 뉴스가 먼저 적재된 구간). 위 singular test가 그 경계를 구분한다.

with bounded as (
    select
        news.article_id,
        news.published_at,
        news.tickers,
        news.insights,
        calendar.trade_date,
        calendar.session_close_utc
    from {{ ref('news_articles_dedup') }} as news
    left join {{ ref('trading_calendar') }} as calendar
        on
            news.published_at < calendar.session_close_utc
            and calendar.trade_date >= cast(news.published_at as date)
            and calendar.trade_date
            <= {{ dbt.dateadd('day', 7, 'cast(news.published_at as date)') }}
),

assigned as (
    select
        article_id,
        published_at,
        min(trade_date) as effective_trade_date
    from bounded
    group by article_id, published_at
)

select
    news.article_id,
    news.published_at,
    assigned.effective_trade_date,
    news.tickers,
    news.insights,
    -- 🔴 관측 셀 — 배정된 날이 발행 달력일과 다른 기사 수가 곧 이 모델이 막고
    -- 있는 누수의 크기다. 0이면 모델이 일을 안 하고 있거나 모집단이 비었다.
    assigned.effective_trade_date <> cast(news.published_at as date)
        as shifted_to_next_session,
    assigned.effective_trade_date is null as is_unassigned
from {{ ref('news_articles_dedup') }} as news
inner join assigned
    on news.article_id = assigned.article_id
