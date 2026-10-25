{{ config(tags=['silver']) }}

-- 1행 = 원천 고시일(fx_date). EUR 기준 두 환율로 교차 계산한 USD/KRW.
--
-- 🔴 **날짜 기준은 요청일(rate_date)이 아니라 원천이 답한 날짜(source_date)다.**
-- 주말·휴일 요청에 원천은 직전 영업일 값을 200으로 준다. rate_date로 두면
-- 토요일에 금요일 값이 "토요일 환율"로 들어가 고시가 없던 날이 있던 날처럼 보인다.
--
-- ⚠️ `distinct`는 그 **에코만** 접는다(같은 source_date·같은 값의 반복 요청).
-- 같은 source_date에 값이 둘이면 접히지 않고 아래 조인이 2행을 내
-- `unique(fx_date)`가 실패한다 — 원천 개정을 조용히 고르지 않고 드러낸다.
--
-- ⚠️ 교차 = (KRW per EUR) / (USD per EUR). 두 값은 같은 고시 시각의 값이라
-- 시각 차이는 이 나눗셈에서 생기지 않는다(시각 차이는 미국 종가와의 사이에 있다).

with rates as (
    select distinct
        source_date,
        quote_currency,
        rate
    from {{ source('frankfurter_fx', 'fx_rates_daily') }}
    where
        base_currency = 'EUR'
        and quote_currency in ('USD', 'KRW')
),

usd as (
    select
        source_date,
        rate as eur_usd
    from rates
    where quote_currency = 'USD'
),

krw as (
    select
        source_date,
        rate as eur_krw
    from rates
    where quote_currency = 'KRW'
)

select
    usd.eur_usd,
    krw.eur_krw,
    cast(usd.source_date as date) as fx_date,
    krw.eur_krw / usd.eur_usd as usd_krw
from usd
inner join krw
    on usd.source_date = krw.source_date
