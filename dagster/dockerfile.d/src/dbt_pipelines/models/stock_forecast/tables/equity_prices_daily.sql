{{ config(tags=['silver']) }}

-- 일별 시세 silver — 타입 확정과 관측 플래그만 붙인다.
--
-- 🔴 **중복 제거를 하지 않는다.** 초안 설계에는 "파티션 재실행 잔여 중복을
-- `row_number()`로 1행화"가 있었는데 뺐다. 적재가 파티션 교체라 유일성은 이미
-- 보장되고, 그런데도 dedup을 넣으면 **그 보장이 깨져도 테스트가 통과한다** —
-- 게이트가 자기가 검사할 대상을 미리 고쳐 버린다. `schema.yml`의
-- `unique_combination_of_columns`를 실제 게이트로 두고 덮지 않는다.
--
-- 🔴 `is_stale_echo`도 **판정이 아니라 관측**이다. 휴장일 요청에 직전 거래일
-- 값을 200으로 조용히 주는 실패 모드가 이 계열 원천에 실재하므로(frankfurter_fx
-- 실측) 행을 지우지 않고 표시만 한다 — 지우면 그 일이 있었다는 사실까지 사라진다.
--
-- 🔴 **그날 행이 있는 티커가 곧 그날의 유니버스다.** grouped daily가 거래된 종목
-- 전체를 주므로 별도 유니버스 테이블이 없고, 상장폐지 종목도 그날까지는 행이
-- 남아 생존 편향이 구조적으로 해소된다.

select
    ticker,
    trade_date,
    source_date,
    trade_count,
    bar_timestamp,
    ingested_at,
    cast(open as double) as open_price,
    cast(high as double) as high_price,
    cast(low as double) as low_price,
    cast(close as double) as close_price,
    cast(volume as double) as volume,
    cast(vwap as double) as vwap,
    -- 요청한 날짜와 원천이 돌려준 날짜가 갈리는지(관측 전용).
    -- ⚠️ 비교식은 **캐스트보다 뒤**에 둔다 — sqlfluff ST06이 캐스트를 단순 타깃으로,
    -- 비교를 계산으로 분류해 순서를 강제한다(실측으로 확인한 분류다).
    source_date <> trade_date as is_stale_echo
from {{ source('polygon_market', 'equity_ohlcv_daily') }}
