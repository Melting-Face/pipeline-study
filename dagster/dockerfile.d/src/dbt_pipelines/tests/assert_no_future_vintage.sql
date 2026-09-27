-- 🔴 PIT 누수 검사 — 거래일에 붙은 지표는 **그날 유효했던 vintage**여야 한다.
--
-- `macro_features_daily`의 조인이 이미 `realtime_start <= trade_date <=
-- realtime_end`를 걸지만, 조인 조건이 곧 검사는 아니다 — 조건을 고치면 검사도
-- 함께 사라진다. 불변식을 **조인 밖에** 두어야 조인이 틀렸을 때 운다.
--
-- 함께 보는 축 하나 더: `observation_date`가 `trade_date`보다 미래면 그 시점에
-- 존재하지 않던 기간의 값이다(원천이 예측치를 넣는 경우).
--
-- 이 테스트를 깨보려면 `macro_features_daily`의 조인에서
-- `pit.realtime_start <= calendar.trade_date` 를 지우면 된다 — 그러면 개정 전
-- 날짜에 개정 후 값이 붙고, 다른 어떤 테스트도 그것을 잡지 못한다.

select
    trade_date,
    series_id,
    observation_date,
    realtime_start,
    realtime_end
from {{ ref('macro_features_daily') }}
where
    realtime_start > trade_date
    or realtime_end < trade_date
    or observation_date > trade_date
