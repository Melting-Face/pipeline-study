{{ config(tags=['silver', 'label']) }}

-- 🔴 **미래 정보만 담는다. 이 테이블은 피처가 아니다.**
--
-- `feature_store_daily`와 분리한 것이 설계의 핵심이다 — 한 테이블에 두면
-- `select *` 한 번에 라벨이 딸려간다. 접두어로 가르는 것은 규율이고
-- 테이블을 나누는 것은 구조다. 태그에 `label`을 달아 선택에서도 가른다.
--
-- 🔴 `lead()`가 등장하는 **유일한 모델**이다. `feature_store_daily`에 이 함수가
-- 보이면 그 자체가 결함이다.
--
-- ⚠️ **horizon은 달력일이 아니라 그 종목이 거래된 날 수다.** 거래 정지 종목은
-- `lead(5)`가 5거래일 뒤가 아니라 "그 종목이 다섯 번째로 거래된 날"을 가리킨다.
-- 값이 틀린 것이 아니라 **보유 기간이 다르다** — 팔 수 없던 날은 보유가 이어지는
-- 것이 실제에 가깝다. 그래도 그 차이가 보이도록 `label_trade_date`를 노출하고,
-- `assert_label_uses_next_trading_day`가 그 날짜가 실제 거래일인지 검사한다.
--
-- ⚠️ 구간 끝에서는 미래가 아직 없어 `label_is_complete = false`다. **행을 지우지
-- 않는다** — 지우면 커버리지 손실이 조용해지고, 학습 시 필터링하는 쪽이
-- "얼마나 버렸는가"를 셀 수 있다.

with price_leads as (
    select
        ticker,
        trade_date,
        close_price,
        lead(close_price, 1) over (
            partition by ticker order by trade_date
        ) as close_h1,
        lead(trade_date, 1) over (
            partition by ticker order by trade_date
        ) as date_h1,
        lead(close_price, 5) over (
            partition by ticker order by trade_date
        ) as close_h5,
        lead(trade_date, 5) over (
            partition by ticker order by trade_date
        ) as date_h5,
        lead(close_price, 20) over (
            partition by ticker order by trade_date
        ) as close_h20,
        lead(trade_date, 20) over (
            partition by ticker order by trade_date
        ) as date_h20
    from {{ ref('equity_prices_daily') }}
),

horizon_1 as (
    select
        ticker,
        trade_date,
        date_h1 as label_trade_date,
        1 as horizon_days,
        close_h1 / close_price - 1 as label_forward_return,
        close_h1 is not null as label_is_complete
    from price_leads
),

horizon_5 as (
    select
        ticker,
        trade_date,
        date_h5 as label_trade_date,
        5 as horizon_days,
        close_h5 / close_price - 1 as label_forward_return,
        close_h5 is not null as label_is_complete
    from price_leads
),

horizon_20 as (
    select
        ticker,
        trade_date,
        date_h20 as label_trade_date,
        20 as horizon_days,
        close_h20 / close_price - 1 as label_forward_return,
        close_h20 is not null as label_is_complete
    from price_leads
)

select * from horizon_1
union all
select * from horizon_5
union all
select * from horizon_20
