"""NVD(CVE·Change History)·CISA KEV 데이터셋 전용 상수.

접속(호스트·타임아웃·재시도·페이싱·페이지 순회)은 `common/nvd.py`의 `NvdResource`가
소유한다. 여기에는 **경로·S3 레이아웃·파티션 계약**만 둔다.

근거 수치(페이지 상한·구간 상한·레이트리밋)는 `scripts/nvd_access_probe.py`의 실측이다.
⚠️ NVD 개발자 문서는 JS 렌더링이라 1차 본문을 기계로 읽지 못했다 — 문서가 아니라
**API 응답 동작**이 근거다. 바뀌면 프로브를 다시 돌리고 이 파일을 고친다.
"""

# Iceberg 네임스페이스 / Dagster 그룹
# (메달리온 레이어는 네임스페이스가 아닌 kind로 표기)
# KEV는 CISA 피드지만 CVE와 한 분석 축(취약점)이라 같은 네임스페이스에 둔다.
NAMESPACE = "nvd"
GROUP_NAME = "nvd"

# raw 랜딩 루트. 피드별 하위 경로 `<feed>/dt=YYYY-MM-DD/...`는
# `common/nvd.py`가 조립한다.
SOURCE_BASE = "s3://warehouse/raw/nvd"

# 피드 이름 = S3 하위 경로 이름. 🔴 **바꾸면 이미 받은 raw를 bronze가 못 찾는다**
# (에러가 아니라 「raw 미완결」로 드러난다). 레이아웃 계약이다.
CVES_FEED = "cves"
HISTORY_FEED = "cve_history"
KEV_FEED = "kev"

# ── NVD CVE API 2.0 ───────────────────────────────────────────────────
# 경로는 `NvdResource.base_url`(…/rest/json) 기준.
CVES_PATH = "/cves/2.0"
# 응답에서 레코드 배열이 담기는 키 — 각 원소는 `{"cve": {...}}`.
CVES_ITEMS_KEY = "vulnerabilities"
CVES_ITEM_KEY = "cve"
# 🔴 페이지 상한 2000(초과 시 404 — 프로브 실측). 상한에 맞춰 요청 수를 최소화한다.
CVES_PAGE_SIZE = 2000
# 구간 파라미터 — 🔴 **항상 짝으로 보낸다**(한쪽만 보내면 404, 실측).
CVES_START_PARAM = "lastModStartDate"
CVES_END_PARAM = "lastModEndDate"

# ── NVD CVE Change History API 2.0 ────────────────────────────────────
HISTORY_PATH = "/cvehistory/2.0"
HISTORY_ITEMS_KEY = "cveChanges"
HISTORY_ITEM_KEY = "change"
# 🔴 페이지 상한 5000(초과 시 404 — 프로브 실측). CVE와 다르다.
HISTORY_PAGE_SIZE = 5000
HISTORY_START_PARAM = "changeStartDate"
HISTORY_END_PARAM = "changeEndDate"

# ── CISA KEV ──────────────────────────────────────────────────────────
# 키 없음·CC0 1.0. 🔴 NVD 키를 이 호스트로 보내지 않도록 `NvdResource`를 거치지 않는다.
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
KEV_FILE_NAME = "kev.json.gz"
KEV_TIMEOUT_S = 60
KEV_RETRIES = 3

# ── 파티션 ────────────────────────────────────────────────────────────
# 🔴 **파티션 시작일은 리터럴로 고정한다**(계산식 금지 — 정의 로드마다 왼쪽 끝이 밀려
#    어제 머티리얼라이즈한 파티션이 집합에서 사라진다.
#    docs/conventions/dagster.md §파티션 ①).
#
# CVE: 프로브 `--scan-lastmod`가 120일 창으로 전구간을 훑어 **가장 이른 `lastModified`가
# 2023-08-23 ~ 2023-12-21 창 안**에 있음을 보였다(창 85개 합계 = 전체 건수).
# 정확한 날짜는 좁히지 않았으므로 **창 시작으로 보수적으로** 고정한다 — 앞쪽 빈 파티션
# 몇 개는 0행으로 끝날 뿐이지만, 늦게 잡으면 가장 이른 수정분이 **조용히** 빠진다.
CVES_PARTITION_START_DATE = "2023-08-23"

# History: 프로브 `--scan-change`(2026-10-31)가 120일 창 85개로 전구간을 훑어
# 창 합계 2,433,235 ≈ 구간 없는 전체 2,433,239(차이 +4 — 스캔 중 신규분)를 보였고,
# **가장 이른 변경 이벤트가 1999-01-01 ~ 1999-05-01 창 안**에 있었다
# (예 `created=1999-01-01T05:00:00.000`). 창 시작으로 고정한다.
# 🔴 **CVE와 시작일이 26년 다른 것이 정상이다.** CVE 피드의 `lastModified`는 재수정될
#    때마다 앞으로 끌려와 과거가 비지만, History는 변경이 일어난 시각을 **보존**한다.
#    두 상수를 "맞추면" History 앞 24년이 에러 없이 수집 범위 밖이 된다.
# ⚠️ 이 구간 대부분은 이벤트가 드문 날이라 백필의 대부분이 0~수 건 파티션이다 —
#    요청 수는 일수에 비례하므로 백필 전에 "요청 간격 x 일수"를 계산한다
#    (키 유무로 간격이 약 10배 다르다 — common/nvd.py).
HISTORY_PARTITION_START_DATE = "1999-01-01"

# 파티션 타임존 — 🔴 **UTC이고, 이것이 스케줄 타임존까지 정한다.**
# 파티션 키가 그대로 API의 날짜 구간(`...T00:00:00.000Z`)으로 나가므로 KST로 두면
# 키의 의미와 원천의 구간이 9시간 어긋난다(polygon_market과 같은 판단).
PARTITION_TIMEZONE = "UTC"

# 일별 파티션 스케줄 발화 시각(파티션 타임존 기준). 19:00 UTC ≈ 04:00 KST.
# 완결된 UTC 하루가 끝나고 19시간 뒤다 — 늦출수록 그날 수정된 CVE가 다시 수정돼
# 다른 날로 옮겨갈 기회가 늘어나지만(raw는 수집 시점 스냅샷이다), 그 이동은
# Change History가 덮는다. 상류의 집계 지연은 **미확인**이라 여유를 둔다.
SCHEDULE_HOUR_UTC = 19

# KEV 스케줄(KST). 05:00 — NVD 일별 잡 뒤에 둔다(순서 의존은 없다).
KEV_SCHEDULE_CRON = "0 5 * * *"
