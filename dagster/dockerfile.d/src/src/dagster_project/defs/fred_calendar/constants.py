"""FRED(ALFRED) 경제지표 데이터셋 전용 상수."""

# Iceberg 네임스페이스 / Dagster 그룹
# 🔴 이름을 원천 기관(세인트루이스 연준)이 아니라 **호출하는 서비스**로 붙인다
# (frankfurter_fx·polygon_market과 같은 규칙).
NAMESPACE = "fred_calendar"
GROUP_NAME = "fred_calendar"

# 접속은 `common/fred.py`의 `FredResource`가 소유한다(호스트·타임아웃·재시도·마스킹).
# 여기에는 **경로와 파티션 계약**만 둔다.
OBSERVATIONS_PATH = "series/observations"
RELEASES_DATES_PATH = "releases/dates"

# 🔴 **추적할 시리즈 — 리터럴로 고정한다.**
# 환경변수로 두지 않는 이유는 `PARTITION_START_DATE`와 같다: 조용히 변하면
# 파티션 집합이 바뀌고, 사라진 시리즈의 과거 파티션이 집합에서 빠진다.
#
# 일별(개정 거의 없음)과 월·분기별(개정 잦음)을 섞었다 — vintage 축이 실제로
# 값을 하는 것은 후자다(실측: DGS10은 974건 중 개정 0, CPIAUCSL은 44건 중 35건 개정).
SERIES_IDS = (
    "CPIAUCSL",  # 소비자물가지수 (월, 개정 잦음)
    "DGS2",  # 2년 국채 금리 (일)
    "DGS10",  # 10년 국채 금리 (일)
    "FEDFUNDS",  # 연방기금금리 (월)
    "GDPC1",  # 실질 GDP (분기, 속보→잠정→확정)
    "T10Y2Y",  # 장단기 금리차 (일) — 침체 선행지표로 널리 쓰인다
    "UNRATE",  # 실업률 (월, 개정 잦음)
    "VIXCLS",  # VIX 변동성 지수 (일)
)

# 🔴 **vintage 조회의 시작일. `polygon_market`의 파티션 시작일과 맞춘다.**
# silver의 as-of 조인이 `realtime_start <= trade_date`로 값을 고르는데, 시세보다
# 늦게 시작하면 초기 거래일에 붙일 지표가 없다.
#
# ⚠️ **이 날짜가 과거 값의 `realtime_start`를 클리핑한다.** 원천은 요청 구간과
# 겹치는 vintage를 돌려주되 구간을 요청 범위로 잘라서 준다 — 2024-10-09 이전부터
# 유효하던 값은 `realtime_start=2024-10-09`로 온다. 우리 분석 창(거래일 ≥ 이 날짜)
# 안에서는 그것이 **정확히 맞는 값**이지만, 이 데이터로 그 이전 시점의 vintage를
# 복원할 수는 없다. 창을 넓히려면 이 상수를 내리고 전량 재적재한다.
REALTIME_START = "2024-10-09"

# 열린 구간의 종료 표기. 원천이 "현재까지 유효"를 이 값으로 준다.
REALTIME_OPEN_END = "9999-12-31"

# 릴리스 일정 파티션(일자)의 시작일. 관측치와 달리 **하루 단위 스냅샷**이라
# 파티션 수가 곧 일수다.
PARTITION_START_DATE = REALTIME_START

# 파티션 타임존 — 🔴 UTC이고 이것이 스케줄 타임존까지 정한다.
# `build_schedule_from_partitioned_job`에 `execution_timezone`을 주면 CheckError로
# 죽으므로, "스케줄 타임존 명시" 규약은 여기서 지켜진다. 파티션 키가 그대로 API의
# 날짜 파라미터로 나가므로 KST면 원천 달력일과 어긋난다.
PARTITION_TIMEZONE = "UTC"

# 스케줄 발화 시각(UTC). 06:00 UTC = 15:00 KST.
# 미 지표는 대개 08:30 ET(=12:30/13:30 UTC)에 발표되므로 **완결된 하루** 뒤에 받는다.
SCHEDULE_HOUR_UTC = 6

# 릴리스 일정 페이지 크기. 🔴 응답의 `count`가 이 값을 넘으면 페이징이 필요하다 —
# 파서가 `count`와 실제 행 수를 대조해 **조용한 잘림을 실패로 만든다**.
RELEASES_PAGE_LIMIT = 1000
