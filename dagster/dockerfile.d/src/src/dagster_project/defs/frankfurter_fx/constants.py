"""Frankfurter 환율 데이터셋 전용 상수."""

# Iceberg 네임스페이스 / Dagster 그룹
# (메달리온 레이어는 네임스페이스가 아닌 kind로 표기)
#
# 🔴 **이름을 원천 기관이 아니라 호출하는 서비스로 붙였다.** 초안은 `ecb_fx`
# 였는데, 공식 문서가 "By default, rates are blended across all providers"와
# "84 central banks"를 말해 **v1 응답이 ECB 참조환율 그 자체라고 단정할 근거가
# 없다**(그 문장이 v2를 설명하는 것인지도 문서가 가르지 않는다 — **미확인**).
# 실측 응답이 `base: EUR`·29통화라 ECB 목록과 모양은 맞지만, **모양이 맞는 것과
# 확인된 것은 다른 축**이다. 틀린 라벨은 검산을 통과한 채 남고 네임스페이스는
# 나중에 바꾸기 비싸므로, 확인된 사실(=우리가 무엇을 호출하는가)로 이름을 짓는다.
NAMESPACE = "frankfurter_fx"
GROUP_NAME = "frankfurter_fx"

# Frankfurter — 환율을 **무인증**으로 제공하는 공개 API.
# 공식 문서 원문: "It requires no API key." /
# "There are no quotas. Requests are rate-limited to prevent abuse,
#  but there are no monthly or daily caps."
# 데이터 범위는 "84 central banks, covering 201 currencies back to 1948".
#
# 🔴 **출처 표기 의무** — 상류에 ECB가 있고 ECB 이용조건은 자유 이용을 허용하되
# *"When such information is distributed or reproduced, it must appear accurately
# and the ECB must be cited as the source."* 로 **must**를 쓴다(요청이 아니다).
# 값을 외부로 내보내는 산출물(리포트·위키·블로그)에는 출처를 표기한다.
# 판정 정본은 `docs/security.md` §0.
#
# 🔴 원천 선택 경위 — 처음 대상은 currencyapi.net이었으나 **무료 플랜이
# historical을 막는다**(공식 문서 2곳이 일치: `documentation/history/`의
# "Available on: Essential, StartUp, Professional"과 `pricing/`의
# "No Historical Rates"). 일자 파티션은 과거 조회가 전제라 원천을 바꿨다.
# 무료 플랜은 현재 환율만·500 req/월·base USD 고정이다.
#
# v1(경로형 `/{date}`)을 쓴다. v2(`/v2/rates?date=`)가 신설됐으나 공식 FAQ가
# "No. The v1 API will continue to work."로 존속을 명시하고, v2는 응답 구조가
# 다르다(단일 객체 → 통화별 배열). v2 전환은 별도 검증 과제로 남긴다 — **미확인**.
RATES_BASE = "https://api.frankfurter.dev/v1"

# 🔴 **파티션 시작일은 리터럴로 고정한다.**
# `date.today() - timedelta(days=30)` 같은 계산식을 쓰면 정의를 로드할 때마다
# 파티션 집합이 하루씩 밀려 **어제 머티리얼라이즈한 파티션이 집합에서 사라진다**
# (에러 없이 왼쪽 끝이 잘려나간다). 범위를 옮길 때는 사람이 이 상수를 고친다.
#
# ⚠️ 고정이라는 것은 **범위가 30일로 유지되지 않는다**는 뜻이기도 하다 —
# 집합은 하루에 하나씩 늘어난다. "최근 30일"은 착수 시점의 폭이지 불변식이 아니다.
# (마지막 파티션은 **완결된 UTC 하루**라 오늘이 아니라 어제까지다.)
PARTITION_START_DATE = "2026-08-09"

# 파티션 타임존 — 🔴 **UTC이고, 이것이 스케줄 타임존까지 정한다.**
# `build_schedule_from_partitioned_job`은 시간 파티션 잡에 `execution_timezone`을
# 주면 CheckError로 죽으므로, 저장소의 "스케줄 타임존 명시" 규약은 여기서 지켜진다.
# KST로 두지 않는 이유는 **파티션 키가 그대로 API의 날짜 파라미터로 나가기 때문**이다
# — KST 하루와 원천의 달력일이 9시간 어긋나고 그 어긋남은 기록되지 않는다.
PARTITION_TIMEZONE = "UTC"

# 스케줄 발화 시각(파티션 타임존 기준). 01:00 UTC = 10:00 KST.
# 파티션이 **완결된 UTC 하루**라 그 하루가 끝난 뒤에 전일분을 받는다.
# ⚠️ 상류 고시 시각은 원천 문서가 다루지 않아 **미확인**이다 — 그래서 고시
# 시각에 바짝 붙이지 않고 하루가 완전히 지난 뒤로 여유를 둔다. 근거 없는
# 시각에 맞추면 그 가정이 틀렸을 때 **빈 값이 아니라 전날 값**이 들어온다.
SCHEDULE_HOUR_UTC = 1

# 응답의 `base` 기본값. 실응답 실측으로 확인했고(`"base":"EUR"`),
# `base` 파라미터로 바꿀 수 있으나 bronze는 원천 기본값을 그대로 담는다.
DEFAULT_BASE_CURRENCY = "EUR"

# HTTP 호출 기본값. 정량 rate limit은 문서가 다루지 않아 **미확인**이다
# ("rate-limited to prevent abuse"라는 정성 서술만 있다). 하루 1회 호출이라
# 여유가 크지만, 백필은 파티션당 1요청이므로 30일 백필이 30요청이다.
HTTP_TIMEOUT_S = 30
HTTP_RETRIES = 3
