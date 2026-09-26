"""Polygon(Massive) 시장 데이터셋 전용 상수."""

# Iceberg 네임스페이스 / Dagster 그룹
# (메달리온 레이어는 네임스페이스가 아닌 kind로 표기)
#
# 🔴 **이름을 원천 기관이 아니라 호출하는 서비스로 붙였다**(frankfurter_fx 선례).
# 서비스는 Polygon.io로 알려져 있으나 `polygon.io/pricing`이 `massive.com/pricing`으로
# **301 리다이렉트**된다(2026-09-25 실측) — 리브랜딩·인수가 진행 중이다.
# API 호스트는 여전히 `api.polygon.io`라 **우리가 실제로 호출하는 것**을 이름으로 쓴다.
# ⚠️ 호스트가 바뀌면 이 이름도 재검토 대상이다(네임스페이스는 나중에 바꾸기 비싸다).
NAMESPACE = "polygon_market"
GROUP_NAME = "polygon_market"

# 🔴 **무료 플랜(Stocks Basic)의 한계가 곧 이 데이터셋의 한계다.**
# 가격 페이지 원문(2026-09-25 실측): "Stocks Basic · Great for trying our APIs ·
# $0 /month · All US Stocks Tickers · 5 API Calls / Minute · 2 Years Historical Data ·
# 100% Market Coverage · End of Day Data · Reference Data · Corporate Actions ·
# Technical Indicators · Minute Aggregates · **Individual use**"
#
# 🔴 "Individual use" — 개인 사용 한정이다. 원본 바이트를 재배포할 수 없고
#    (Iceberg 테이블·노트북 출력·리포트 어느 것도) 공개하는 것은 코드와 pull 시점뿐이다.
#    판정 정본은 `docs/security.md` §0.
#
# ⚠️ **뉴스는 이 목록에 없다** — 가격 페이지에 `News`라는 단어가 0회 등장한다.
#    무료 플랜 포함 여부는 **미확인**이고, `scripts/stock_source_access_probe.py
#    --source news`가 401/403으로 가른다. 거부되면 SEC EDGAR Atom으로 간다
#    (무료·무키·연락처 UA 필요, 이미 실측 통과).
POLYGON_BASE = "https://api.polygon.io"

# 🔴 grouped daily는 **1회 호출로 그날 거래된 전 미국 티커**를 준다.
# 일자 파티션과 1:1로 맞아 파티션당 요청이 1건이고, 그날의 유니버스가 응답 그 자체라
# **별도 유니버스 자산 없이 생존 편향이 구조적으로 해소**된다
# (티커별로 호출하는 방식과 갈리는 지점이다).
GROUPED_DAILY_PATH = "/v2/aggs/grouped/locale/us/market/stocks"

# 뉴스는 `published_utc`로 **날짜 구간 질의**가 된다. 그래서 RSS와 달리
# 롤링 윈도우 유실이 없고 **append가 아니라 일자 파티션 교체**로 갈 수 있다(멱등·백필).
NEWS_PATH = "/v2/reference/news"

# 🔴 **인증은 `Authorization: Bearer` 헤더로 한다.**
# `?apiKey=` 쿼리 파라미터도 지원하지만 쓰지 않는다 — `common/helper.py:226-230`이
# *"크리덴셜을 쿼리 파라미터로 받는 API에 `fetch_json`을 그대로 쓰지 않는다"* 를
# 금지로 적어 뒀고(예외 메시지에 전체 URL이 실린다), 헤더를 쓰면 그 축이 아예 사라진다.
# (FRED는 `?api_key=`뿐이라 `common/fred.py`가 마스킹 래퍼를 둔다 — 대비되는 축이다.)
AUTH_HEADER = "Authorization"

# HTTP 호출 기본값. 🔴 무료 플랜은 **5 req/min**이라 백필이 분당 5파티션으로 묶인다.
# 429를 재시도로 흡수하되, `Retry-After`가 오면 그것을 우선한다(helper._request).
HTTP_TIMEOUT_S = 30
HTTP_RETRIES = 3
