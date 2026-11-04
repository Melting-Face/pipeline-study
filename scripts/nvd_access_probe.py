#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""NVD CVE·Change History API와 CISA KEV 피드의 접근·형식·경계를 **실측으로 판정**한다.

왜 이 스크립트인가:
    Airflow `lakehouse/nvd.py`의 수집 경로는 저장소 밖의 외부 사실 위에 선다.
        ① 엔드포인트·응답 필드가 파서의 계약과 맞는가
        ② 페이지 최대 크기와 `lastMod*`/`change*` 조회 구간의 최대 길이
           (상수가 여기 걸린다)
        ③ 키를 `apiKey` **헤더**로 받는가, 변조한 키를 실제로 거부하는가
        ④ 일별 `lastModified` 구간의 합집합이 전체 CVE와 같은가 (파티션 백필의 전제)
        ⑤ 가장 이른 `lastModified` (PARTITION_START_DATE의 근거)
    NVD 개발자 문서는 JS 렌더링이라 1차 본문을 기계로 읽지 못했다. 그래서 문서 대신
    **API에 직접 묻는다** — 응답 동작은 문서보다 강한 근거다.

🔴 **음성 대조가 이 스크립트의 핵심이다. 200은 성공이 아니다.**
    "121일 구간은 거부된다"를 보려면 **120일 구간이 먼저 통과**해야 한다. 양성 대조가
    실패한 채 음성만 보면 날짜 형식 오류를 구간 제한으로 오독한다.

종료코드:
    0  통과 — 계획대로 자산을 배선한다
    1  명확한 거부 — 엔드포인트 부재·키 거절
    2  판정 불가 — 키 부재·네트워크 실패·**음성 대조 실패**
       ⚠️ `2`를 `0`으로도 `1`로도 읽지 마라. `--source all`은 **최악 코드**를 돌려준다.

실행 (의존성은 위 PEP 723 블록 — uv가 자동 provisioning):
    uv run scripts/nvd_access_probe.py --source all
    uv run scripts/nvd_access_probe.py --source cves --scan-lastmod   # ⑤ 전구간 스캔
    uv run scripts/nvd_access_probe.py --source history --scan-change  # ④ 전구간 스캔

🔴 **디스크에 아무것도 쓰지 않는다.** NVD 이용 조건이 미확인이라 응답은 메모리에서
    판정하고 버린다. 🔴 **크리덴셜을 출력하지 않는다** — 키는 헤더로만 보낸다.

스타일: 스크립트 컨벤션(docs/conventions/python.md)에 따라 절차형으로 쓴다.
    선례(`stock_source_access_probe.py`)처럼 원천별 `probe_*`로 나뉘지만
    **각 함수 안은 위→아래 절차형**이고, 공통 조각(.env 로드·요청 래퍼·페이싱)만
    Rule of Three로 추출했다.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

TIMEOUT_S = 60

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_UNDETERMINED = 2

# ── NVD ─────────────────────────────────────────────────────────────────
NVD_CVES_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_HISTORY_URL = "https://services.nvd.nist.gov/rest/json/cvehistory/2.0"

# 재는 대상(문서 미확인 — 기억 기반 기대값). 프로브가 **실제 동작**으로 판정한다.
NVD_CVES_MAX_PAGE = 2000
NVD_HISTORY_MAX_PAGE = 5000
NVD_MAX_RANGE_DAYS = 120

# 롤링 30초 창의 요청 상한(기대값). 키 없음 5 / 키 있음 50.
# 🔴 페이싱이 없으면 403/429가 **판정 재료를 지운다** — "구간이 너무 길다"와
#    "너무 빨리 물었다"가 같은 거부 모양이라 ② 판정이 오염된다.
NVD_WINDOW_S = 30
NVD_CALLS_NO_KEY = 5
NVD_CALLS_WITH_KEY = 50
_NVD_CALL_TIMES: list[float] = []

# 🔴 **파서와 짝을 이루는 상수다**(`lakehouse/nvd_parse.py`의 `REQUIRED_*`·`KNOWN_*`).
# 이 스크립트는 PEP 723 단독 실행이라 그 패키지를 import할 수 없어 두 벌이 존재하고,
# 갈라지면 **프로브는 통과하고 파서만 깨진다**. AST 대조 테스트
# (`images/airflow/tests/lib/test_nvd_contract.py`)가 그 갈라짐을 막는다.
NVD_CVE_FIELDS = ("id", "lastModified", "published", "vulnStatus")
NVD_CVE_KNOWN_FIELDS = (
    "affected",
    "cisaActionDue",
    "cisaExploitAdd",
    "cisaRequiredAction",
    "cisaVulnerabilityName",
    "configurations",
    "cveTags",
    "descriptions",
    "evaluatorComment",
    "evaluatorImpact",
    "evaluatorSolution",
    "id",
    "lastModified",
    "metrics",
    "published",
    "references",
    "sourceIdentifier",
    "vendorComments",
    "vulnStatus",
    "weaknesses",
)
NVD_CHANGE_FIELDS = ("created", "cveChangeId", "cveId", "eventName")
NVD_CHANGE_KNOWN_FIELDS = (
    "created",
    "cveChangeId",
    "cveId",
    "details",
    "eventName",
    "sourceIdentifier",
)

# 전구간 스캔의 시작점. NVD 이전 CVE 체계의 시작(1999)보다 앞에서 출발해
# 「가장 이른 수정일」이 탐색 범위 밖으로 잘리지 않게 한다.
NVD_SCAN_FROM = datetime(1999, 1, 1, tzinfo=timezone.utc)
NVD_SCAN_RETRIES = 3

# ── CISA KEV ────────────────────────────────────────────────────────────
KEV_FEEDS_BASE = "https://www.cisa.gov/sites/default/files/feeds"
KEV_URL = f"{KEV_FEEDS_BASE}/known_exploited_vulnerabilities.json"
# 🔴 스키마의 최상위 required에는 `title`이 없지만 실제 피드에는 있다(researcher A등급).
#    필수 판정은 스키마의 required를 따르고 `title`은 아는 필드로만 둔다.
KEV_TOP_FIELDS = ("catalogVersion", "count", "dateReleased", "vulnerabilities")
KEV_ENTRY_FIELDS = (
    "cveID",
    "dateAdded",
    "dueDate",
    "product",
    "requiredAction",
    "shortDescription",
    "vendorProject",
    "vulnerabilityName",
)
KEV_ENTRY_KNOWN_FIELDS = (
    *KEV_ENTRY_FIELDS,
    "cwes",
    "forensicTriage",
    "knownRansomwareCampaignUse",
    "notes",
)

DEFAULT_USER_AGENT = "dagster-study-probe/0.1"


def load_env_value(key: str) -> str:
    """환경변수에서, 없으면 저장소 루트 `.env`에서 값을 읽는다.

    PEP 723 단독 실행이 전제라 python-dotenv를 쓰지 않고 직접 파싱한다.

    Args:
        key: 찾을 환경변수 이름.

    Returns:
        찾은 값. 없으면 빈 문자열.
    """
    value = os.environ.get(key, "")
    if value:
        return value
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, raw = stripped.partition("=")
        if name.strip() != key:
            continue
        return raw.split("#", 1)[0].strip().strip("\"'")
    return ""


def pace_nvd(api_key: str) -> None:
    """NVD 롤링 30초 창의 요청 상한에 맞춰 필요한 만큼 대기한다.

    🔴 NVD 요청 **직전에** 부른다. 키 유무로 상한이 10배 갈린다.

    Args:
        api_key: 키(빈 문자열이면 키 없음 상한을 쓴다).
    """
    limit = NVD_CALLS_WITH_KEY if api_key else NVD_CALLS_NO_KEY
    now = time.monotonic()
    _NVD_CALL_TIMES[:] = [t for t in _NVD_CALL_TIMES if now - t < NVD_WINDOW_S]
    if len(_NVD_CALL_TIMES) >= limit:
        wait = NVD_WINDOW_S - (now - _NVD_CALL_TIMES[0]) + 1
        time.sleep(wait)
        now = time.monotonic()
        _NVD_CALL_TIMES[:] = [t for t in _NVD_CALL_TIMES if now - t < NVD_WINDOW_S]
    _NVD_CALL_TIMES.append(time.monotonic())


def get(
    session: requests.Session,
    url: str,
    params: dict[str, str] | None = None,
    api_key: str = "",
) -> tuple[requests.Response | None, str]:
    """요청을 보내고 (응답, 오류설명)을 돌려준다.

    프로브는 **실패도 관측 대상**이라 예외를 올리지 않고 튜플로 돌려준다.
    키는 `apiKey` 헤더로만 보내므로 URL·예외 메시지에 실리지 않는다.

    Args:
        session: 재사용할 requests 세션.
        url: 요청 URL.
        params: 쿼리 파라미터.
        api_key: 비어 있지 않으면 `apiKey` 헤더로 붙인다.

    Returns:
        (응답, ""). 실패면 (None, 사람이 읽는 오류 설명).
    """
    headers = {"apiKey": api_key} if api_key else None
    try:
        response = session.get(url, params=params, headers=headers, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        return None, f"{type(exc).__name__} @ {url}"
    return response, ""


def nvd_ts(moment: datetime) -> str:
    """NVD 날짜 파라미터 형식(ISO-8601, 밀리초, UTC 오프셋 표기)으로 바꾼다."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def report_field_drift(observed: set[str], known: tuple[str, ...]) -> bool:
    """관측한 필드 합집합을 아는 집합과 대조해 양방향 드리프트를 출력한다.

    🔴 **레코드 하나가 아니라 페이지 전체의 합집합**을 받는다. CVE는 선택 필드가
    많아(cisa*·evaluator*) 한 건만 보면 "사라진 필드"가 거짓 양성으로 쏟아진다.

    🔴 **응답에만 있는 필드는 판정을 올린다**(True 반환 → 호출부가 판정 불가로).
    출력만 하면 경고가 초록 종료코드에 묻혀 계약 갱신이 미뤄진다 — 실제로 `affected`가
    그렇게 묻혔다. 반대로 **이 페이지에 없는 아는 필드**는 선택 필드일 수 있어 판정에
    넣지 않는다(거짓 양성).

    Args:
        observed: 페이지 안 모든 레코드 필드의 합집합.
        known: 우리가 아는 필드 이름 튜플.

    Returns:
        응답에만 있는 필드가 하나라도 있으면 True.
    """
    extra = sorted(observed - set(known))
    gone = sorted(set(known) - observed)
    if extra:
        print(
            f"  ⚠ 응답에만 있는 필드 {extra} — 계약(프로브·파서)을 갱신한다 → 판정 불가"
        )
    if gone:
        print(f"  · 아는데 이 페이지에 없는 필드 {gone} — 선택 필드면 정상")
    if not extra and not gone:
        print("  ✓ 필드 집합이 파서의 계약과 일치한다")
    return bool(extra)


def probe_cves(session: requests.Session, api_key: str, scan_lastmod: bool) -> int:
    """CVE API의 엔드포인트·필드·페이지 크기·구간 제한·키 검증을 판정한다.

    Args:
        session: 요청 세션.
        api_key: NVD 키(빈 문자열이면 키 축은 판정 불가).
        scan_lastmod: 참이면 전구간을 120일 창으로 훑어 ④⑤를 잰다.

    Returns:
        종료코드(0 통과 / 1 거부 / 2 판정 불가).
    """
    print(f"\n{'=' * 64}\n[CVE] {NVD_CVES_URL}\n{'=' * 64}")
    verdict = EXIT_OK

    # ── ① 엔드포인트·전체 건수·필드 (페이지 최대 크기로) ─────────────────
    print(f"① 본 요청 — resultsPerPage={NVD_CVES_MAX_PAGE}")
    pace_nvd(api_key)
    response, err = get(
        session, NVD_CVES_URL, {"resultsPerPage": str(NVD_CVES_MAX_PAGE)}, api_key
    )
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.status_code == 404:
        print("  ✗ 404 — 엔드포인트가 없다(경로가 바뀌었는지 확인)")
        return EXIT_REJECTED
    if not response.ok:
        print(f"  ✗ {response.status_code} → 판정 불가")
        return EXIT_UNDETERMINED
    payload: dict[str, Any] = response.json()
    total = int(payload.get("totalResults", 0))
    page = payload.get("vulnerabilities") or []
    print(f"  ✓ 최상위 필드 {sorted(payload)}")
    print(
        f"  totalResults={total:,} · resultsPerPage={payload.get('resultsPerPage')}"
        f" · 받은 건수={len(page)} · version={payload.get('version')}"
    )
    if not page:
        print("  ✗ vulnerabilities가 비었다 → 판정 불가")
        return EXIT_UNDETERMINED
    cves = [item.get("cve", {}) for item in page]
    missing = sorted({f for cve in cves for f in NVD_CVE_FIELDS if f not in cve})
    if missing:
        print(f"  ✗ 필수 필드 누락 {missing} → 파서 상수를 고쳐야 한다. 판정 불가")
        return EXIT_UNDETERMINED
    if report_field_drift({f for cve in cves for f in cve}, NVD_CVE_KNOWN_FIELDS):
        verdict = max(verdict, EXIT_UNDETERMINED)
    print(f"  예: id={cves[0]['id']} lastModified={cves[0]['lastModified']}")
    statuses = sorted({cve["vulnStatus"] for cve in cves})
    print(f"  vulnStatus 종류(이 페이지) {statuses}")

    # ── ② 🔴 음성 대조: 페이지 최대 크기 + 1 ───────────────────────────────
    print(f"\n② 음성 대조 — resultsPerPage={NVD_CVES_MAX_PAGE + 1}")
    pace_nvd(api_key)
    response, err = get(
        session, NVD_CVES_URL, {"resultsPerPage": str(NVD_CVES_MAX_PAGE + 1)}, api_key
    )
    if response is None:
        print(f"  ? 네트워크 실패: {err} → 이 축은 미확인")
        verdict = max(verdict, EXIT_UNDETERMINED)
    elif response.ok:
        echoed = response.json().get("resultsPerPage")
        print(f"  · 200 — 거부하지 않고 resultsPerPage={echoed}로 응답")
        print("    → 상한이 조용히 잘리는 방식이다. 페이지 크기는 응답 값을 믿는다")
    else:
        print(f"  ✓ {response.status_code} — 상한 {NVD_CVES_MAX_PAGE}이 강제된다")

    # ── ③ 구간 제한: 양성(120일) → 음성(121일) → 짝 누락 ───────────────────
    end = datetime.now(tz=timezone.utc).replace(microsecond=0)
    print(f"\n③ 양성 대조 — lastMod 구간 {NVD_MAX_RANGE_DAYS}일")
    pace_nvd(api_key)
    response, err = get(
        session,
        NVD_CVES_URL,
        {
            "lastModStartDate": nvd_ts(end - timedelta(days=NVD_MAX_RANGE_DAYS)),
            "lastModEndDate": nvd_ts(end),
            "resultsPerPage": "1",
        },
        api_key,
    )
    if response is None or not response.ok:
        code = response.status_code if response is not None else err
        print(f"  ✗ {code} — 120일이 통과하지 않는다. 날짜 형식이 틀렸을 수 있다")
        print("    → 음성 대조를 해석할 수 없다. 판정 불가")
        return EXIT_UNDETERMINED
    print(f"  ✓ 200 · totalResults={response.json().get('totalResults'):,}")

    print(f"\n③-2 음성 대조 — lastMod 구간 {NVD_MAX_RANGE_DAYS + 1}일")
    pace_nvd(api_key)
    response, err = get(
        session,
        NVD_CVES_URL,
        {
            "lastModStartDate": nvd_ts(end - timedelta(days=NVD_MAX_RANGE_DAYS + 1)),
            "lastModEndDate": nvd_ts(end),
            "resultsPerPage": "1",
        },
        api_key,
    )
    if response is None:
        print(f"  ? 네트워크 실패: {err} → 이 축은 미확인")
        verdict = max(verdict, EXIT_UNDETERMINED)
    elif response.ok:
        print("  ⚠ 200 — 121일도 통과했다. 구간 상한이 120일이 아니다(더 길거나 없음)")
    else:
        print(f"  ✓ {response.status_code} — 상한 {NVD_MAX_RANGE_DAYS}일이 강제된다")

    print("\n③-3 음성 대조 — lastModStartDate만 (짝 누락)")
    pace_nvd(api_key)
    response, err = get(
        session,
        NVD_CVES_URL,
        {"lastModStartDate": nvd_ts(end - timedelta(days=1)), "resultsPerPage": "1"},
        api_key,
    )
    if response is None:
        print(f"  ? 네트워크 실패: {err} → 이 축은 미확인")
        verdict = max(verdict, EXIT_UNDETERMINED)
    elif response.ok:
        print("  ⚠ 200 — 짝 없이도 받는다. 자산은 그래도 항상 둘 다 보낸다")
    else:
        print(f"  ✓ {response.status_code} — 시작·끝을 함께 보내야 한다")

    # ── ④ 키 검증: 유효 키(헤더) → 변조 키 ────────────────────────────────
    print("\n④ 키 — apiKey 헤더")
    if not api_key:
        print("  ? NVD_API_KEY가 없다 → 이 축은 판정 불가")
        print("    https://nvd.nist.gov/developers/request-an-api-key 에서 발급")
        verdict = max(verdict, EXIT_UNDETERMINED)
    else:
        print(f"  키: {api_key[:4]}***  (값은 출력하지 않는다)")
        response, err = get(session, NVD_CVES_URL, {"resultsPerPage": "1"}, api_key)
        if response is None or not response.ok:
            code = response.status_code if response is not None else err
            print(f"  ✗ {code} — 유효 키가 거부됐다")
            return EXIT_REJECTED
        print("  ✓ 200 — 헤더 키를 받는다")
        tampered = api_key[:-1] + ("0" if api_key[-1] != "0" else "1")
        pace_nvd(api_key)
        response, err = get(session, NVD_CVES_URL, {"resultsPerPage": "1"}, tampered)
        if response is None:
            print(f"  ? 네트워크 실패: {err} → 이 축은 미확인")
            verdict = max(verdict, EXIT_UNDETERMINED)
        elif response.ok:
            print("  ✗ 200 — 변조한 키가 통과했다. 키 검증이 미증명")
            verdict = max(verdict, EXIT_UNDETERMINED)
        else:
            print(f"  ✓ {response.status_code} — 키를 실제로 검증한다")

    # ── ⑤ 🔴 전구간 스캔: 파티션 합집합 = 전체인가, 가장 이른 수정일 ──────
    if not scan_lastmod:
        print("\n⑤ 전구간 스캔 — 건너뜀(--scan-lastmod로 켠다)")
        return verdict
    print(f"\n⑤ 전구간 스캔 — {NVD_SCAN_FROM.date()}부터 {NVD_MAX_RANGE_DAYS}일 창")
    # 🔴 창의 끝은 다음 창 시작 1초 전이다. 경계를 겹치면 경계 시각의 CVE가
    #    두 번 세져 합계가 전체를 넘고, 그 초과가 「중복」이 아니라 「신규」로 읽힌다.
    scan_end = datetime.now(tz=timezone.utc).replace(microsecond=0)
    cursor = NVD_SCAN_FROM
    summed = 0
    earliest_window = ""
    windows = 0
    while cursor < scan_end:
        upper = min(cursor + timedelta(days=NVD_MAX_RANGE_DAYS), scan_end)
        # 🔴 창 하나의 일시 실패(연결 끊김·5xx·한도 초과 403)가 스캔 전체를 지우지
        #    않게 재시도한다 — 실측에서 2002년 창 하나의 ConnectionError로 중단됐다.
        #    404는 재시도하지 않는다(파라미터 거부라 다시 물어도 같다).
        for attempt in range(NVD_SCAN_RETRIES):
            pace_nvd(api_key)
            response, err = get(
                session,
                NVD_CVES_URL,
                {
                    "lastModStartDate": nvd_ts(cursor),
                    "lastModEndDate": nvd_ts(upper - timedelta(seconds=1)),
                    "resultsPerPage": "1",
                },
                api_key,
            )
            if response is not None and (response.ok or response.status_code == 404):
                break
            code = response.status_code if response is not None else err
            print(f"  · {cursor.date()} 창 {code} — 재시도 {attempt + 1}")
            time.sleep(2**attempt * 5)
        windows += 1
        if response is None or not response.ok:
            code = response.status_code if response is not None else err
            print(f"  ✗ {cursor.date()} 창 {code} → 스캔 중단, 판정 불가")
            return EXIT_UNDETERMINED
        count = int(response.json().get("totalResults", 0))
        if count and not earliest_window:
            earliest_window = f"{cursor.date()} ~ {upper.date()}"
            first = response.json()["vulnerabilities"][0]["cve"]["lastModified"]
            print(f"  · 첫 비지 않은 창 {earliest_window} ({count:,}건, 예 {first})")
        summed += count
        cursor = upper
    print(f"  창 {windows}개 · 합계 {summed:,} · 전체(①) {total:,}")
    gap = total - summed
    print(f"  차이 {gap:+,} — 스캔 중 신규 수정분만큼 어긋날 수 있다(양수 소량은 정상)")
    if abs(gap) > max(100, total // 1000):
        print("  ⚠ 차이가 크다 — 파티션 합집합 ≠ 전체. 백필 전제(Q13)를 재검토한다")
        verdict = max(verdict, EXIT_UNDETERMINED)
    else:
        print("  ✓ 파티션 합집합 ≈ 전체 — 파티션 백필로 전체 이력을 덮는다")
    print(f"  → 가장 이른 수정일은 창 {earliest_window} 안에 있다")
    print("    (계측 단위: 창 해상도 120일. 정확한 날짜는 그 창 안을 좁혀 잰다)")
    return verdict


def probe_history(session: requests.Session, api_key: str, scan_change: bool) -> int:
    """Change History API의 엔드포인트·필드·페이지 크기·구간 제한을 판정한다.

    Args:
        session: 요청 세션.
        api_key: NVD 키.
        scan_change: 참이면 전구간을 120일 창으로 훑어 총량·가장 이른 변경을 잰다.

    Returns:
        종료코드(0 통과 / 1 거부 / 2 판정 불가).
    """
    print(f"\n{'=' * 64}\n[History] {NVD_HISTORY_URL}\n{'=' * 64}")
    verdict = EXIT_OK
    end = datetime.now(tz=timezone.utc).replace(microsecond=0)

    # ── ① 본 요청: 최근 1일, 페이지 최대 크기 ─────────────────────────────
    print(f"① 본 요청 — 최근 1일 · resultsPerPage={NVD_HISTORY_MAX_PAGE}")
    pace_nvd(api_key)
    response, err = get(
        session,
        NVD_HISTORY_URL,
        {
            "changeStartDate": nvd_ts(end - timedelta(days=1)),
            "changeEndDate": nvd_ts(end),
            "resultsPerPage": str(NVD_HISTORY_MAX_PAGE),
        },
        api_key,
    )
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.status_code == 404:
        print("  ✗ 404 — 엔드포인트가 없거나 파라미터가 틀렸다")
        return EXIT_REJECTED
    if not response.ok:
        print(f"  ✗ {response.status_code} → 판정 불가")
        return EXIT_UNDETERMINED
    payload: dict[str, Any] = response.json()
    changes = [item.get("change", {}) for item in payload.get("cveChanges") or []]
    print(f"  ✓ 최상위 필드 {sorted(payload)}")
    print(
        f"  totalResults={payload.get('totalResults'):,}"
        f" · resultsPerPage={payload.get('resultsPerPage')} · 받은 건수={len(changes)}"
    )
    if not changes:
        print("  ✗ 최근 1일 변경이 0건 → 형식을 판정할 수 없다. 판정 불가")
        return EXIT_UNDETERMINED
    missing = sorted({f for c in changes for f in NVD_CHANGE_FIELDS if f not in c})
    if missing:
        print(f"  ✗ 필수 필드 누락 {missing} → 파서 상수를 고쳐야 한다. 판정 불가")
        return EXIT_UNDETERMINED
    if report_field_drift({f for c in changes for f in c}, NVD_CHANGE_KNOWN_FIELDS):
        verdict = max(verdict, EXIT_UNDETERMINED)
    events = sorted({c["eventName"] for c in changes})
    print(f"  eventName 종류(이 페이지) {events}")

    # ── ② 🔴 음성 대조: 페이지 최대 크기 + 1 ───────────────────────────────
    print(f"\n② 음성 대조 — resultsPerPage={NVD_HISTORY_MAX_PAGE + 1}")
    pace_nvd(api_key)
    response, err = get(
        session,
        NVD_HISTORY_URL,
        {
            "changeStartDate": nvd_ts(end - timedelta(days=1)),
            "changeEndDate": nvd_ts(end),
            "resultsPerPage": str(NVD_HISTORY_MAX_PAGE + 1),
        },
        api_key,
    )
    if response is None:
        print(f"  ? 네트워크 실패: {err} → 이 축은 미확인")
        verdict = max(verdict, EXIT_UNDETERMINED)
    elif response.ok:
        echoed = response.json().get("resultsPerPage")
        print(f"  · 200 — 거부하지 않고 resultsPerPage={echoed}로 응답")
    else:
        print(f"  ✓ {response.status_code} — 상한 {NVD_HISTORY_MAX_PAGE}이 강제된다")

    # ── ③ 구간 제한: 양성(120일) → 음성(121일) ───────────────────────────
    for days, expect_ok in (
        (NVD_MAX_RANGE_DAYS, True),
        (NVD_MAX_RANGE_DAYS + 1, False),
    ):
        label = "양성" if expect_ok else "음성"
        print(f"\n③ {label} 대조 — change 구간 {days}일")
        pace_nvd(api_key)
        response, err = get(
            session,
            NVD_HISTORY_URL,
            {
                "changeStartDate": nvd_ts(end - timedelta(days=days)),
                "changeEndDate": nvd_ts(end),
                "resultsPerPage": "1",
            },
            api_key,
        )
        if response is None:
            print(f"  ? 네트워크 실패: {err} → 이 축은 미확인")
            verdict = max(verdict, EXIT_UNDETERMINED)
        elif expect_ok and not response.ok:
            print(f"  ✗ {response.status_code} — 120일이 통과하지 않는다 → 판정 불가")
            return EXIT_UNDETERMINED
        elif expect_ok:
            print(f"  ✓ 200 · totalResults={response.json().get('totalResults'):,}")
        elif response.ok:
            print("  ⚠ 200 — 121일도 통과했다. 구간 상한이 120일이 아니다")
        else:
            print(f"  ✓ {response.status_code} — 120일 초과는 거부된다")

    # ── ④ 🔴 전구간 스캔: 총량·가장 이른 변경 (History 파티션 시작일의 근거) ─
    if not scan_change:
        print("\n④ 전구간 스캔 — 건너뜀(--scan-change로 켠다)")
        return verdict
    print("\n④-1 구간 없이 전체 건수")
    pace_nvd(api_key)
    response, err = get(session, NVD_HISTORY_URL, {"resultsPerPage": "1"}, api_key)
    if response is None or not response.ok:
        code = response.status_code if response is not None else err
        print(f"  ? {code} — 전체 건수 미확인(합계 대조 없이 스캔만 한다)")
        total = -1
    else:
        total = int(response.json().get("totalResults", 0))
        print(f"  ✓ totalResults={total:,}")
    print(f"\n④-2 전구간 스캔 — {NVD_SCAN_FROM.date()}부터 {NVD_MAX_RANGE_DAYS}일 창")
    # 🔴 창의 끝은 다음 창 시작 **1밀리초** 전이다(`.999Z`). 이 형식이 받아들여지는지가
    #    자산의 일별 구간 끝(`T23:59:59.999Z`)과 같은 축이라 여기서 함께 검증된다.
    scan_end = datetime.now(tz=timezone.utc).replace(microsecond=0)
    cursor = NVD_SCAN_FROM
    summed = 0
    earliest_window = ""
    windows = 0
    while cursor < scan_end:
        upper = min(cursor + timedelta(days=NVD_MAX_RANGE_DAYS), scan_end)
        window_end = (upper - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.999Z")
        for attempt in range(NVD_SCAN_RETRIES):
            pace_nvd(api_key)
            response, err = get(
                session,
                NVD_HISTORY_URL,
                {
                    "changeStartDate": nvd_ts(cursor),
                    "changeEndDate": window_end,
                    "resultsPerPage": "1",
                },
                api_key,
            )
            if response is not None and (response.ok or response.status_code == 404):
                break
            code = response.status_code if response is not None else err
            print(f"  · {cursor.date()} 창 {code} — 재시도 {attempt + 1}", flush=True)
            time.sleep(2**attempt * 5)
        windows += 1
        if response is None or not response.ok:
            code = response.status_code if response is not None else err
            print(f"  ✗ {cursor.date()} 창 {code} → 스캔 중단, 판정 불가")
            return EXIT_UNDETERMINED
        count = int(response.json().get("totalResults", 0))
        if count and not earliest_window:
            earliest_window = f"{cursor.date()} ~ {upper.date()}"
            first = response.json()["cveChanges"][0]["change"]["created"]
            print(f"  · 첫 비지 않은 창 {earliest_window} ({count:,}건, 예 {first})")
        print(f"  · {cursor.date()} 창 {count:,}건", flush=True)
        summed += count
        cursor = upper
    print(f"  창 {windows}개 · 합계 {summed:,} · 전체 {total:,}")
    if total >= 0:
        gap = total - summed
        print(f"  차이 {gap:+,} — 스캔 중 신규 변경분만큼 어긋날 수 있다")
        if abs(gap) > max(100, total // 1000):
            print("  ⚠ 차이가 크다 — 창 합집합 ≠ 전체. 경계 누락을 의심한다")
            verdict = max(verdict, EXIT_UNDETERMINED)
        else:
            print("  ✓ 창 합집합 ≈ 전체 — .999Z 경계로 누락·중복이 없다")
    print(f"  → 가장 이른 변경은 창 {earliest_window} 안에 있다(해상도 120일)")
    return verdict


def probe_kev(session: requests.Session) -> int:
    """CISA KEV 피드의 접근·필수 필드·건수 정합을 판정한다.

    Args:
        session: 요청 세션.

    Returns:
        종료코드(0 통과 / 1 거부 / 2 판정 불가).
    """
    print(f"\n{'=' * 64}\n[KEV] {KEV_URL}\n{'=' * 64}")

    # ── ① 음성 대조: 없는 경로가 404인가 (200을 주는 CDN이면 본 요청도 못 믿는다) ─
    print("① 음성 대조 — 없는 파일명")
    response, err = get(session, KEV_URL.replace(".json", "_no_such_file.json"))
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.ok:
        print(f"  ✗ {response.status_code} — 없는 경로도 200이다. 본 요청 불신")
        return EXIT_UNDETERMINED
    print(f"  ✓ {response.status_code} — 없는 경로는 거부된다")

    # ── ② 본 요청 ───────────────────────────────────────────────────────
    print("\n② 본 요청")
    response, err = get(session, KEV_URL)
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if not response.ok:
        print(f"  ✗ {response.status_code} → 판정 불가")
        return EXIT_UNDETERMINED
    payload: dict[str, Any] = response.json()
    missing_top = [f for f in KEV_TOP_FIELDS if f not in payload]
    if missing_top:
        print(f"  ✗ 최상위 필수 누락 {missing_top} → 판정 불가")
        return EXIT_UNDETERMINED
    entries = payload["vulnerabilities"]
    print(f"  ✓ 최상위 필드 {sorted(payload)}")
    print(
        f"  catalogVersion={payload['catalogVersion']}"
        f" · dateReleased={payload['dateReleased']}"
        f" · count={payload['count']} · 실제 항목 {len(entries)}"
    )
    # 🔴 count와 실제 항목 수가 다르면 잘린 응답이다 — 행 수 검증의 기준값이 흔들린다.
    if int(payload["count"]) != len(entries):
        print("  ✗ count ≠ 항목 수 — 잘렸거나 count 의미가 다르다. 판정 불가")
        return EXIT_UNDETERMINED
    missing = sorted({f for e in entries for f in KEV_ENTRY_FIELDS if f not in e})
    if missing:
        print(f"  ✗ 항목 필수 필드 누락 {missing} → 판정 불가")
        return EXIT_UNDETERMINED
    verdict = EXIT_OK
    if report_field_drift({f for e in entries for f in e}, KEV_ENTRY_KNOWN_FIELDS):
        verdict = EXIT_UNDETERMINED
    duplicated = len(entries) - len({e["cveID"] for e in entries})
    print(f"  cveID 중복 {duplicated}건 (0이어야 cveID를 키로 쓴다)")
    if duplicated:
        return EXIT_UNDETERMINED
    added = sorted(e["dateAdded"] for e in entries)
    print(f"  dateAdded 범위 {added[0]} ~ {added[-1]}")
    if verdict != EXIT_OK:
        print(f"\n{'=' * 64}\n? [KEV] 판정 불가 — 필드 계약 갱신 필요")
        return verdict
    print(f"\n{'=' * 64}\n✓ [KEV] 통과")
    return EXIT_OK


def main() -> int:
    """인자를 읽어 원천별 프로브를 실행하고 **최악 종료코드**를 돌려준다."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", choices=["cves", "history", "kev", "all"], default="all"
    )
    parser.add_argument("--scan-lastmod", action="store_true")
    parser.add_argument("--scan-change", action="store_true")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    api_key = load_env_value("NVD_API_KEY")
    print(f"NVD 키: {'있음' if api_key else '없음 — 30초당 5회로 페이싱'}")

    started = time.monotonic()
    results: dict[str, int] = {}
    if args.source in ("cves", "all"):
        results["cves"] = probe_cves(session, api_key, args.scan_lastmod)
    if args.source in ("history", "all"):
        results["history"] = probe_history(session, api_key, args.scan_change)
    if args.source in ("kev", "all"):
        results["kev"] = probe_kev(session)

    print(f"\n{'=' * 64}\n판정표 (소요 {time.monotonic() - started:.0f}s)\n{'=' * 64}")
    label = {EXIT_OK: "통과", EXIT_REJECTED: "거부", EXIT_UNDETERMINED: "판정 불가"}
    for name, code in results.items():
        print(f"  {name:8s} {code}  {label[code]}")
    worst = max(results.values())
    print(f"\n종료코드 {worst} ({label[worst]})")
    if worst == EXIT_UNDETERMINED:
        print("⚠️ 판정 불가는 실패가 아니라 **미확인**이다. 위 출력에서 어느 축이")
        print("   안 닫혔는지 보고 그것만 푼다.")
    return worst


if __name__ == "__main__":
    sys.exit(main())
