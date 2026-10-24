-- 1행 = (ticker, trade_date). 미국 종목의 달러 종가와 원화 환산 종가.
{{ config(tags=['gold']) }}

-- 🔴 **환율은 as-of다** — 그 거래일 당일 또는 이전의 가장 최근 고시값을 쓴다.
-- ECB 휴일(미국은 개장)에 같은 날짜 조인을 하면 행이 사라진다. 직전 값을 쓰되
-- `fx_date`·`fx_lag_days`로 **무엇을 썼는지 드러낸다**.
--
-- ⚠️ 거래일 이후 환율은 보지 않는다(`fx_date <= trade_date`). ECB 고시는 유럽 오후,
-- 미국 종가는 뉴욕 16:00이라 같은 날 고시는 종가보다 먼저 나온다 — 미래 참조가 아니다.
-- 대신 그 사이 몇 시간의 환율 변동은 오차로 남는다(급변일에 1% 안팎).
--
-- ⚠️ 환율 조인은 `left join`이다. 환율이 없는 거래일은 행을 지우지 않고
-- `usd_krw`가 null로 남아 `not_null` 테스트가 운다 — 커버리지 구멍을 조용히 줄이지 않는다.
--
-- 🔴 같은 fx_date가 둘이면 `row_number()`가 **임의로 하나를 고른다**(gold 유일성은
-- 그대로라 여기서는 안 보인다). 그 경우를 막는 것은 상류 `fx_usdkrw_daily`의
-- `unique(fx_date)`이고, `dbt build`는 그 실패로 이 모델을 건너뛴다(고정 입력 실측).
--
-- as-of는 행 단위가 아니라 **거래일 단위로 먼저 푼다** — 거래일은 수백 개,
-- 종목×거래일은 수백만 개라 윈도우를 작은 쪽에 건다.

with trade_dates as (
    select distinct cast(trade_date as date) as trade_day
    from {{ ref('equity_prices_daily') }}
),

fx_ranked as (
    select
        trade_dates.trade_day,
        fx.fx_date,
        fx.usd_krw,
        row_number() over (
            partition by trade_dates.trade_day
            order by fx.fx_date desc
        ) as fx_rank
    from trade_dates
    inner join {{ ref('fx_usdkrw_daily') }} as fx
        on trade_dates.trade_day >= fx.fx_date
),

fx_asof as (
    select
        trade_day,
        fx_date,
        usd_krw
    from fx_ranked
    where fx_rank = 1
)

select
    prices.ticker,
    prices.trade_date,
    prices.close_price as close_usd,
    fx_asof.usd_krw,
    fx_asof.fx_date,
    prices.close_price * fx_asof.usd_krw as close_krw,
    {{ elapsed('day', 'fx_asof.fx_date', 'cast(prices.trade_date as date)') }}
        as fx_lag_days
from {{ ref('equity_prices_daily') }} as prices
left join fx_asof
    on cast(prices.trade_date as date) = fx_asof.trade_day
