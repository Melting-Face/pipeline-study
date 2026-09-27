{{ config(tags=['silver']) }}

-- 거래일 달력 — 이 파이프라인의 **시간 축 정본**.
--
-- 🔴 외부 휴장일 달력을 의존성으로 들이지 않는다. 시세가 존재하는 날이 곧
-- 거래일이므로 달력을 **데이터에서 유도**한다. 새 의존성은 새 실패 모드이고,
-- 달력과 데이터가 어긋나면 어느 쪽이 맞는지 판정할 근거가 없어진다.
--
-- 🔴 `session_close_utc`는 원천의 `bar_timestamp`를 그대로 쓴다. "16:00
-- America/New_York을 UTC로"를 SQL에서 계산하려면 엔진별 방언
-- (`to_utc_timestamp` ↔ `with_timezone ... at time zone`)을 흡수하는 dispatch
-- 매크로와 sqlfluff 스텁 한 쌍이 필요한데, **원천이 이미 그 값을 준다**
-- (EDT 20:00Z / EST 21:00Z — DST가 반영돼 있다).
--
-- ⚠️ **`session_close_assumed`가 true인 이유.** 실측상 `bar_timestamp`는 16:00 ET에
-- 고정된 집계 창 표식이라 **조기 폐장일(13:00 ET)에도 같은 값**이 온다
-- (2025-11-28·2025-12-24 관측, 대조군 2025-12-23 동일). 따라서 이 컬럼은
-- "실제 마감"이 아니라 "16:00 ET 가정"이다. 뉴스 세션 배정이 이 값을 쓰므로,
-- 조기 폐장일 오후 기사는 **당일로 배정된다**(실제로는 마감 후일 수 있다).
-- 이 공백은 선언된 것이지 빠뜨린 것이 아니다.

with sessions as (
    select
        trade_date,
        min(bar_timestamp) as session_close_utc,
        -- 🔴 관측 셀 — 한 거래일 안에서 bar_timestamp가 갈리면 원천이 기준을
        -- 바꿨다는 뜻이다. schema.yml이 이 값을 1로 못 박는다.
        count(distinct bar_timestamp) as bar_timestamp_variants,
        count(*) as ticker_count
    from {{ source('polygon_market', 'equity_ohlcv_daily') }}
    group by trade_date
)

select
    trade_date,
    session_close_utc,
    bar_timestamp_variants,
    ticker_count,
    true as session_close_assumed,
    lag(trade_date) over (order by trade_date) as prev_trade_date,
    lead(trade_date) over (order by trade_date) as next_trade_date
from sessions
