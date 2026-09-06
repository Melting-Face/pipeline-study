"""USGS 수문(NWIS) 데이터셋 전용 상수."""

# Iceberg 네임스페이스 / Dagster 그룹
# (메달리온 레이어는 네임스페이스가 아닌 kind로 표기)
NAMESPACE = "usgs_water"
GROUP_NAME = "usgs_water"

# NWIS Water Services — 실시간 순간값(IV)과 관측소 메타(Site).
# 신규 OGC API(api.waterdata.usgs.gov)가 병존하나 **구버전 폐기 일정이 공식
# 문서에 없다**(2026-09-05 조사: 마이그레이션 문서에 지원 종료일·필수 이관
# 기한이 모두 부재). 현행 서비스를 쓰되 이관 신호를 주시한다.
IV_ENDPOINT = "https://waterservices.usgs.gov/nwis/iv/"
SITE_ENDPOINT = "https://waterservices.usgs.gov/nwis/site/"

# 수집 대상 — 좁게 시작해 실측 후 넓힌다.
# CA는 유량 한 종만으로 514 시계열이었다(2026-09-05 실측, 2시간 창 3,053 관측점).
TARGET_STATES = ("dc", "md")

# 유량(00060)·수위(00065)
PARAMETER_CODES = ("00060", "00065")

# 🔴 재조회 창 — **최신만 받으면 dedup 재료가 생기지 않는다.**
# 매 실행이 이 창을 다시 긁어 같은 (site_no, parameter_cd, date_time)이 중복
# 수집되고, 값이 수정되면 새 버전이 함께 들어온다. IV 응답은 관측점 100%가
# qualifier "P"(Provisional data subject to revision)이며, 90일이 지나도 P를
# 유지한다(2026-09-05 실측 — 3·20·90일 전 구간 전부 P 100%).
# ⇒ 상태 전이(P→A)는 IV에서 관측되지 않으므로, 이 창이 잡는 것은
#    **값 수정(revision)**이지 승인 전이가 아니다.
# 15분 주기에 PT2H 창이면 관측점당 최대 8중복이 쌓인다.
LOOKBACK = "PT2H"

# 한 요청당 site 상한 — 공식 문서 "You can specify up to 100 sites."
# 주(state) 단위 조회는 이 제한을 받지 않아 현재 경로에서는 쓰이지 않으나,
# 전국으로 넓힐 때 페이징 단위가 된다("No one user is allowed to download
# all of the data with a single call.").
MAX_SITES_PER_REQUEST = 100

# HTTP 호출 기본값.
# rate limit은 "5-10 req/s at a steady rate, 버스트 40-50 req/s"가 공식 문서에
# 명시돼 있다(waterservices.usgs.gov/docs/general/). 15분 주기 소량 호출이라
# 여유가 크지만, 시간당 한도는 문서가 다루지 않아 미확인이다.
HTTP_TIMEOUT_S = 30
HTTP_RETRIES = 3
