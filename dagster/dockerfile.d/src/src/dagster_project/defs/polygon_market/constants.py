"""Polygon(Massive) 시장 데이터셋 전용 상수."""

# Iceberg 네임스페이스 / Dagster 그룹
# (메달리온 레이어는 네임스페이스가 아닌 kind로 표기)
#
# 🔴 **이름을 원천 기관이 아니라 호출하는 서비스로 붙였다**(frankfurter_fx 선례).
# 서비스는 Polygon.io로 알려져 있으나 `polygon.io/pricing`이 `massive.com/pricing`으로
# **301 리다이렉트**된다(실측) — 리브랜딩·인수가 진행 중이다. API 호스트는 여전히
# `api.polygon.io`라 **우리가 실제로 호출하는 것**을 이름으로 쓴다.
# ⚠️ 호스트가 바뀌면 이 이름도 재검토 대상이다(네임스페이스는 나중에 바꾸기 비싸다).
NAMESPACE = "polygon_market"
GROUP_NAME = "polygon_market"

# 🔴 **무료 플랜(Stocks Basic)의 한계가 곧 이 데이터셋의 한계다.**
# 가격 페이지 원문: "$0 /month · All US Stocks Tickers · 5 API Calls / Minute ·
# 2 Years Historical Data · 100% Market Coverage · End of Day Data · **Individual use**"
#
# 🔴 "Individual use" — 개인 사용 한정이다. 원본 바이트를 재배포할 수 없고
#    (Iceberg 테이블·노트북 출력·리포트 어느 것도) 공개하는 것은 코드와 조회 시점뿐이다.
#    판정 정본은 `docs/security.md` §0, 절차는 `docs/setup/market-data-keys.md`.
#
# 접속 자체는 `common/polygon.py`의 `PolygonResource`가 소유한다
# (호스트·타임아웃·재시도).
# 여기에는 **경로와 파티션 계약**만 둔다.

# 🔴 grouped daily는 **1회 호출로 그날 거래된 전 미국 티커**를 준다(실측 12,626개).
# 일자 파티션과 1:1로 맞아 파티션당 요청이 1건이고, 그날의 유니버스가 응답 그 자체라
# **별도 유니버스 자산 없이 생존 편향이 구조적으로 해소**된다
# (티커별로 호출하는 방식과 갈리는 지점이다).
GROUPED_DAILY_PATH = "/v2/aggs/grouped/locale/us/market/stocks"

# 뉴스는 `published_utc`로 **날짜 구간 질의**가 된다. 그래서 RSS와 달리 롤링 윈도우
# 유실이 없고 **append가 아니라 일자 파티션 교체**로 갈 수 있다(멱등·백필 가능).
NEWS_PATH = "/v2/reference/news"

# 뉴스 페이지 크기. 🔴 응답에 `next_url`이 있으면 **그 하루가 이 수를 넘었다**는
# 뜻이다 — 페이징을 따르지 않으면 조용히 잘린다. 자산이 끝까지 따라간다.
NEWS_PAGE_LIMIT = 1000

# 🔴 **파티션 시작일은 리터럴로 고정한다.**
# 계산식(`today - N`)을 쓰면 정의를 로드할 때마다 파티션 집합의 왼쪽 끝이 잘려
# **어제 머티리얼라이즈한 파티션이 집합에서 사라진다**(에러 없이).
#
# 이 값의 근거는 프로브 실측이다 — `--source prices` ⑤가 이분 탐색으로 무료 플랜의
# 조회 경계를 `2024-09-25`(거부) ~ `2024-10-09`(허용) 사이로 좁혔다. 허용이 확인된
# 쪽을 쓴다. ⚠️ 벤더 문서의 "2 Years"를 그대로 옮긴 값이 아니다.
#
# 🔴 **이 경계는 롤링 윈도우다** — `frankfurter_fx`(1948년까지 열려 있음)와 축이 다르다.
# 시간이 지나면 이른 파티션이 권한 밖으로 밀려 403이 되고, 이미 적재된 것은 Iceberg에
# 남지만 **재적재는 영구히 불가능해진다.** 그래서 초기 구간 백필을 미루지 않는다.
# 범위를 옮길 때는 사람이 프로브를 다시 돌리고 이 상수를 고친다.
PARTITION_START_DATE = "2024-10-09"

# 파티션 타임존 — 🔴 **UTC이고, 이것이 스케줄 타임존까지 정한다.**
# `build_schedule_from_partitioned_job`은 시간 파티션 잡에 `execution_timezone`을
# 주면 CheckError로 죽으므로, 저장소의 "스케줄 타임존 명시" 규약은 여기서 지켜진다.
# KST로 두지 않는 이유는 **파티션 키가 그대로 API의 날짜 파라미터로 나가기 때문**이다
# — KST 하루와 원천의 달력일이 9시간 어긋나고 그 어긋남은 기록되지 않는다.
PARTITION_TIMEZONE = "UTC"

# 스케줄 발화 시각(파티션 타임존 기준). 09:00 UTC = 18:00 KST.
# 미 증시 정규장 마감은 20:00Z(EDT)/21:00Z(EST)이므로, **완결된 UTC 하루**가 끝난 뒤
# 9시간을 더 둔다 — 종가 확정·정정 반영에 여유를 주는 쪽이 싸다.
# ⚠️ 상류의 확정 시각은 문서가 다루지 않아 **미확인**이다. 근거 없는 시각에 바짝
# 붙이면 그 가정이 틀렸을 때 **빈 값이 아니라 미확정 값**이 들어온다.
SCHEDULE_HOUR_UTC = 9
