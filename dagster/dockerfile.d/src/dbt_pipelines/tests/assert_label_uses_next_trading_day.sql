-- 🔴 라벨 검사 — 라벨이 가리키는 날은 **실제 거래일**이고 기준일보다 뒤여야 한다.
--
-- 흔한 오류는 `trade_date + interval N day`로 라벨 날짜를 만드는 것이다. 그러면
-- 주말·휴장일을 가리키게 되고, 그 날짜로 가격을 찾으면 조인이 실패해 행이
-- 조용히 사라지거나(내부 조인) null이 된다 — **에러가 나지 않는다.**
--
-- `labels_forward_return`은 `lead()`로 그 종목이 실제 거래된 날을 집으므로 이
-- 오류가 구조적으로 막히지만, **막혔다는 것을 확인하는 것은 별개**다. 구현이
-- 바뀌어도 이 불변식은 남는다.
--
-- ⚠️ 미완성 라벨(`label_is_complete = false`)은 `label_trade_date`가 null이므로
-- 검사 대상이 아니다 — 구간 끝에서 정상적으로 일어난다.

select
    labels.ticker,
    labels.trade_date,
    labels.horizon_days,
    labels.label_trade_date
from {{ ref('labels_forward_return') }} as labels
left join {{ ref('trading_calendar') }} as calendar
    on labels.label_trade_date = calendar.trade_date
where
    labels.label_trade_date is not null
    and (
        calendar.trade_date is null
        or labels.label_trade_date <= labels.trade_date
    )
