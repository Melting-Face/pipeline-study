{{ config(tags=['silver']) }}

-- 경제지표 관측치 정제 — vintage 구간을 **그대로 보존**한다.
--
-- 🔴 같은 `observation_date`가 여러 행으로 남는 것이 정상이다. 지표는 발표 뒤
-- 개정되고 각 행은 "그 값이 유효했던 구간"을 갖는다(실측: UNRATE 2025-11-01은
-- 2026-01-08까지 4.6, 그 뒤 4.5 / CPIAUCSL은 관측일 956건 중 119건 개정).
-- 여기서 하나로 접으면 as-of 조인이 불가능해지고, 접힌 뒤에는 복원되지 않는다.
--
-- 🔴 결측 `"."` 를 여기서 null로 바꾸되 **원문도 함께 남긴다.** bronze가 원문을
-- 보존하는 것과 별개로, silver를 읽는 쪽이 "발표 안 됨"과 "캐스팅 실패"를
-- 구분할 수 있어야 한다 — 숫자 컬럼만 두면 둘이 같은 null이 된다.

select
    series_id,
    observation_date,
    realtime_start,
    realtime_end,
    ingested_at,
    value as value_raw,
    case when value = '.' then null else cast(value as double) end
        as observation_value
from {{ source('fred_calendar', 'fred_series_observations') }}
