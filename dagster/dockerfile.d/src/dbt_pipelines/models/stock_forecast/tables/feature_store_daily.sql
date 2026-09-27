{{ config(tags=['silver']) }}

-- 🔴 **라벨 컬럼이 하나도 없다. 그것이 이 모델의 설계다.**
--
-- 피처와 라벨을 한 테이블에 두면 `select *` 한 번에 미래 정보가 딸려간다.
-- 접두어로 가르는 방법도 있지만 그것은 **규율**이고, 테이블을 나누는 것은
-- **구조**다. 학습 테이블(피처 ⨝ 라벨)은 gold로 남기고 여기서 만들지 않는다.
--
-- 🔴 **모든 윈도우가 과거만 본다.** `lag()`와 `rows between N preceding and
-- current row`만 쓰고 `lead()`는 쓰지 않는다 — `lead()`는 이 파일에 등장해서는
-- 안 되는 함수이고, 등장하면 그 자체가 결함이다(라벨 모델에만 있어야 한다).
--
-- ⚠️ 거래 정지 종목은 그 기간 행이 없으므로 `lag(n)`이 **달력 n일 전이 아니라
-- 그 종목이 마지막으로 거래된 n번째 날**을 가리킨다. 정지가 잦은 종목에서
-- 수익률 구간이 늘어난다 — 값이 틀린 것은 아니고 **의미가 다르다.**
-- 판정하지 않고 `prev_trade_date_1d`를 노출해 관측 가능하게 둔다.

with price_lags as (
    select
        ticker,
        trade_date,
        close_price,
        volume,
        vwap,
        lag(close_price, 1) over (
            partition by ticker order by trade_date
        ) as prev_close_1d,
        lag(close_price, 5) over (
            partition by ticker order by trade_date
        ) as prev_close_5d,
        lag(close_price, 20) over (
            partition by ticker order by trade_date
        ) as prev_close_20d,
        lag(trade_date, 1) over (
            partition by ticker order by trade_date
        ) as prev_trade_date_1d
    from {{ ref('equity_prices_daily') }}
),

price_returns as (
    select
        ticker,
        trade_date,
        close_price,
        volume,
        vwap,
        prev_trade_date_1d,
        close_price / prev_close_1d - 1 as return_1d,
        close_price / prev_close_5d - 1 as return_5d,
        close_price / prev_close_20d - 1 as return_20d
    from price_lags
),

price_features as (
    select
        ticker,
        trade_date,
        close_price,
        volume,
        vwap,
        prev_trade_date_1d,
        return_1d,
        return_5d,
        return_20d,
        stddev_samp(return_1d) over (
            partition by ticker order by trade_date
            rows between 19 preceding and current row
        ) as volatility_20d,
        avg(volume) over (
            partition by ticker order by trade_date
            rows between 19 preceding and current row
        ) as avg_volume_20d
    from price_returns
),

macro_wide as (
    select
        trade_date,
        max(case when series_id = 'CPIAUCSL' then observation_value end)
            as macro_cpi,
        max(case when series_id = 'DGS2' then observation_value end)
            as macro_dgs2,
        max(case when series_id = 'DGS10' then observation_value end)
            as macro_dgs10,
        max(case when series_id = 'FEDFUNDS' then observation_value end)
            as macro_fedfunds,
        max(case when series_id = 'GDPC1' then observation_value end)
            as macro_gdp,
        max(case when series_id = 'T10Y2Y' then observation_value end)
            as macro_term_spread,
        max(case when series_id = 'UNRATE' then observation_value end)
            as macro_unrate,
        max(case when series_id = 'VIXCLS' then observation_value end)
            as macro_vix
    from {{ ref('macro_features_daily') }}
    group by trade_date
)

select
    price_features.ticker,
    price_features.trade_date,
    price_features.close_price,
    price_features.volume,
    price_features.vwap,
    price_features.prev_trade_date_1d,
    price_features.return_1d,
    price_features.return_5d,
    price_features.return_20d,
    price_features.volatility_20d,
    price_features.avg_volume_20d,
    macro_wide.macro_cpi,
    macro_wide.macro_dgs2,
    macro_wide.macro_dgs10,
    macro_wide.macro_fedfunds,
    macro_wide.macro_gdp,
    macro_wide.macro_term_spread,
    macro_wide.macro_unrate,
    macro_wide.macro_vix,
    -- 뉴스가 없는 날은 0건이다(null과 구분한다 — 없는 것과 모르는 것은 다르다).
    coalesce(news.article_count, 0) as news_article_count,
    coalesce(news.sentiment_net, 0) as news_sentiment_net,
    coalesce(news.sentiment_tagged_count, 0) as news_sentiment_tagged_count,
    coalesce(releases.release_count, 0) as macro_release_count
from price_features
left join {{ ref('news_features_daily') }} as news
    on
        price_features.ticker = news.ticker
        and price_features.trade_date = news.trade_date
left join macro_wide
    on price_features.trade_date = macro_wide.trade_date
left join {{ ref('macro_release_calendar_asof') }} as releases
    on price_features.trade_date = releases.trade_date
