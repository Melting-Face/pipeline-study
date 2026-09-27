-- 🔴 누수 검사 — 배정된 거래일의 세션 마감이 기사 발행보다 **뒤**여야 한다.
--
-- 이 불변식이 깨지면 장 마감 뒤에야 알 수 있었던 정보로 그날의 수익률을
-- 설명하게 된다. 스키마 테스트로는 표현할 수 없다(두 테이블의 값 비교라
-- 교차 테이블 불변식이고, `docs/test.md` §5가 singular test의 용도로 정한 것이 이것이다).
--
-- 🔴 **NULL 배정은 여기서 보지 않는다** — 그것은 다른 실패 모드이고
-- `assert_news_assignment_is_complete.sql`이 맡는다. 한 테스트가 두 축을 보면
-- 실패했을 때 어느 쪽인지 알 수 없다.
--
-- 이 테스트를 깨보려면 `news_session_assignment`의 조인 조건에서 부등호를
-- `>=`가 아니라 `<`로 뒤집으면 된다.

select
    assigned.article_id,
    assigned.published_at,
    assigned.effective_trade_date,
    calendar.session_close_utc
from {{ ref('news_session_assignment') }} as assigned
inner join {{ ref('trading_calendar') }} as calendar
    on assigned.effective_trade_date = calendar.trade_date
where calendar.session_close_utc <= assigned.published_at
