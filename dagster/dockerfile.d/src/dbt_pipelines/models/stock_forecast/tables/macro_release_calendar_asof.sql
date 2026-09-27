{{ config(tags=['silver']) }}

-- 거래일별 "그날 발표가 있었는가" 지표.
--
-- bronze는 `as_of_date` 하루를 realtime으로 고정해 받으므로 각 파티션이 그날
-- 유효했던 릴리스 목록이다. 여기서는 거래일 축으로 옮겨 **이벤트 유무**만 낸다.
--
-- ⚠️ 이것은 **발표가 일어난 날의 기록**이지 앞으로의 예정표가 아니다(실측:
-- realtime을 하루로 고정하면 `date`가 전부 그 하루로 온다). "다음 CPI까지 며칠"
-- 같은 선행 피처는 여기서 만들 수 없다 — 만들려면 미래 일정을 받아야 하고,
-- 그것은 **그 시점에 알 수 있었는가**를 다시 따져야 하는 별개의 축이다.
--
-- 🔴 거래일에 붙일 때 `as_of_date`를 그대로 쓴다. 릴리스는 장중·장전에 나오므로
-- 발표일 당일의 피처로 쓰는 것이 맞다 — 뉴스처럼 마감 경계로 밀지 않는다.
-- ⚠️ 다만 **장 마감 후 발표되는 지표가 있다면** 이 가정이 깨진다. 릴리스 시각이
-- 원천에 없어(날짜만 온다) 여기서는 판정할 수 없다 — **미확인**으로 남긴다.

select
    calendar.trade_date,
    count(*) as release_count,
    count(distinct releases.release_id) as distinct_release_count
from {{ ref('trading_calendar') }} as calendar
inner join {{ source('fred_calendar', 'fred_release_dates') }} as releases
    on calendar.trade_date = releases.as_of_date
group by calendar.trade_date
