-- 1행 = (ticker, trade_date). 익일(h=1) 등락 방향 분류의 학습 테이블.
{{ config(tags=['gold']) }}

-- 피처(`feature_store_daily`)와 라벨(`labels_forward_return`)을 조인하는 **유일한 곳**이다.
-- silver에서 둘을 나눈 이유가 "select * 한 번에 미래가 딸려간다"였으므로, 이 테이블을
-- 읽는 쪽은 `label_` 접두 컬럼을 피처 목록에서 **명시적으로 뺀다**(노트북 04).
--
-- 🔴 `lead()`를 쓰지 않는다 — 미래는 라벨 모델에서 `ref`로만 들어온다.
--
-- ⚠️ **유니버스 축소를 여기서 한다.** Polygon은 미국 전 종목이라 저유동·페니 종목이
-- 행 수 대부분을 차지하고, 그 종목들의 일간 수익률은 호가 단위 잡음에 가깝다.
-- pandas로 원천을 받아 거르지 않는다(analysis.md §3 — 무거운 계산은 엔진에서).
-- 하한은 아래 named 값이며, 걸러진 행 수는 리포트 attrition에 남긴다.
--
-- ⚠️ 수익률이 정확히 0인 날은 `label_up = false`다(하락과 같은 칸). 비율은 노트북에서 센다.
--
-- ⚠️ `label_trade_date`를 남기는 이유는 **purge**다 — 시간 분할에서 학습 행의 라벨 날짜가
-- 평가 구간 시작 이후면 그 행은 평가 구간의 가격을 본 것이다.

{% set min_close_price = 5 %}
{% set min_avg_volume_20d = 1000000 %}

with labels_h1 as (
    select
        ticker,
        trade_date,
        label_trade_date,
        label_forward_return
    from {{ ref('labels_forward_return') }}
    where
        horizon_days = 1
        and label_is_complete
)

select
    features.ticker,
    features.trade_date,
    features.close_price,
    features.volume,
    features.vwap,
    features.return_1d,
    features.return_5d,
    features.return_20d,
    features.volatility_20d,
    features.avg_volume_20d,
    features.macro_cpi,
    features.macro_dgs2,
    features.macro_dgs10,
    features.macro_fedfunds,
    features.macro_gdp,
    features.macro_term_spread,
    features.macro_unrate,
    features.macro_vix,
    features.news_article_count,
    features.news_sentiment_net,
    features.news_sentiment_tagged_count,
    features.macro_release_count,
    labels_h1.label_trade_date,
    labels_h1.label_forward_return,
    labels_h1.label_forward_return > 0 as label_up
from {{ ref('feature_store_daily') }} as features
inner join labels_h1
    on
        features.ticker = labels_h1.ticker
        and features.trade_date = labels_h1.trade_date
where
    features.close_price >= {{ min_close_price }}
    and features.avg_volume_20d >= {{ min_avg_volume_20d }}
