-- 🔴 배정 실패 검사 — 배정되지 않은 기사는 **마지막 세션 이후 발행분뿐**이어야 한다.
--
-- `news_session_assignment`은 조인 범위를 7일로 묶는다. 묶지 않으면 중간 결과가
-- 수천만 행이 되기 때문인데, 그 7일은 **가정**이다(미 증시가 그보다 오래 닫은
-- 적은 없지만 없다는 보장은 없다). 가정이 깨지면 기사가 조용히 NULL이 되고,
-- `news_features_daily`의 `where ... is not null`이 그 행을 **에러 없이 버린다**.
--
-- 그래서 NULL 자체를 금지하지 않고 **정당한 NULL과 아닌 것을 가른다.**
-- 정당한 NULL은 하나뿐이다 — 아직 시세가 적재되지 않아 배정할 세션이 없는
-- 구간(뉴스가 시세보다 앞서 적재된 경계). 그 밖의 NULL은 7일 가정이 깨진 것이다.
--
-- ⚠️ 이 테스트가 0건인 것은 "7일이 충분하다"가 아니라 **"지금까지는 충분했다"** 다.
-- 시장이 더 오래 닫히면 그때 이 테스트가 먼저 운다.

with last_session as (
    select max(session_close_utc) as last_close_utc
    from {{ ref('trading_calendar') }}
)

select
    assigned.article_id,
    assigned.published_at,
    last_session.last_close_utc
from {{ ref('news_session_assignment') }} as assigned
cross join last_session
where
    assigned.effective_trade_date is null
    -- 마지막 세션 마감보다 **앞서** 발행됐는데 배정이 없다 = 7일 가정 붕괴.
    and assigned.published_at < last_session.last_close_utc
