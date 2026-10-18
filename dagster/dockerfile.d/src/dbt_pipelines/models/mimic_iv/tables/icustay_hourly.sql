{{ config(tags=['silver']) }}

-- ICU 재실 구간을 정시(clock-hour) 격자로 펼쳐 한 시간마다 한 행을 생성한다.
-- 시각 기준은 첫 심박수 측정 24시간 전부터 퇴실까지이며, hr=0은 입실 시각(정시 올림),
-- 음수 hr은 입실 전(입실 전 검사값 확보용), 양수 hr은 입실 후 경과 시간을 뜻한다.
-- stay_id와 (endtime - 1 hour, endtime] 구간으로 다른 테이블과 조인하는 시간 격자.
-- 공식 mimic-code concepts/demographics/icustay_hourly.sql 포팅 (BigQuery → Trino).

with all_hours as (
    select
        it.stay_id,

        -- intime_hr을 정시 단위로 올림(ceil)한 기준 시각
        case
            when date_trunc('hour', it.intime_hr) = it.intime_hr
                then it.intime_hr
            else {{ dbt.dateadd('hour', 1, "date_trunc('hour', it.intime_hr)") }}
        end as endtime,

        -- 입실 24시간 전(-24)부터 퇴실까지 시간 오프셋 정수 배열
        sequence(
            -24,
            cast({{ elapsed('hour', 'it.intime_hr', 'it.outtime_hr') }} as integer)
        ) as hrs
    from {{ ref('icustay_times') }} as it
)

select
    all_hours.stay_id,
    cast(t.hr_unnested as integer) as hr,
    {{ dbt.dateadd('hour', 't.hr_unnested', 'all_hours.endtime') }} as endtime
from all_hours
{{ unnest_array('all_hours.hrs', 't', 'hr_unnested') }}
