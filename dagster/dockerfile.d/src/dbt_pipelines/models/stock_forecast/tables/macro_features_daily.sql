{{ config(tags=['silver']) }}

-- 🔴 **as-of 조인 — 이 파이프라인의 두 번째 누수 급소.**
--
-- 거래일마다, 시리즈마다 **그날 알 수 있었던 값**을 고른다:
--
--     realtime_start <= trade_date <= realtime_end   (그 vintage가 유효했던 구간)
--     observation_date <= trade_date                 (미래 기간의 값을 쓰지 않는다)
--     그중 observation_date가 가장 최신인 것
--
-- 개정 전 날짜에 개정 후 값을 붙이면 **존재하지 않던 정보**를 쓰는 것이다.
-- 실측 예: UNRATE 2025-11-01은 2026-01-08까지 4.6이었다. 2025-12-20의 피처에
-- 4.5를 쓰면 3주 뒤에야 나올 값을 미리 본 것이고, 어디서도 에러가 나지 않는다.
--
-- 🔴 **날짜 차이를 계산하지 않는다.** `vintage_lag_days` 같은 컬럼이 유용하지만
-- `dbt.datediff`는 이 저장소가 금지한다(Spark는 경과시간 ceil, Trino는 경계
-- 교차라 값이 갈린다). 새 dispatch 매크로를 만들면 sqlfluff 스텁까지 한 쌍이
-- 늘어난다. 그래서 **두 날짜를 그대로 노출**하고 차이는 쓰는 쪽이 낸다 —
-- `bar_timestamp`로 매크로 하나를 없앤 것과 같은 판단이다.
--
-- ⚠️ 날짜가 전부 'YYYY-MM-DD' 문자열이라 비교가 사전순 = 날짜순이다.
-- 열린 구간의 '9999-12-31'도 이 순서에서 올바르게 가장 큰 값이 된다.

with candidates as (
    select
        calendar.trade_date,
        pit.series_id,
        pit.observation_date,
        pit.observation_value,
        pit.realtime_start,
        pit.realtime_end,
        row_number() over (
            partition by calendar.trade_date, pit.series_id
            order by pit.observation_date desc, pit.realtime_start desc
        ) as recency_rank
    from {{ ref('trading_calendar') }} as calendar
    inner join {{ ref('macro_observations_pit') }} as pit
        on
            -- ⚠️ 조건의 좌우를 `calendar` 먼저로 쓴다 — sqlfluff ST09가
            -- "먼저 참조한 테이블을 앞에"를 강제한다.
            calendar.trade_date >= pit.realtime_start
            and calendar.trade_date <= pit.realtime_end
            -- 미래 기간의 관측치를 배제한다(원천이 예측치를 넣어도 막힌다).
            and calendar.trade_date >= pit.observation_date
            and pit.observation_value is not null
)

select
    trade_date,
    series_id,
    observation_date,
    observation_value,
    realtime_start,
    realtime_end
from candidates
where recency_rank = 1
