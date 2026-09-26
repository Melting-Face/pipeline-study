#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""주식예측 원천의 접근·형식·경계를 **실측으로 판정**한다(적재의 선행 관문).

왜 이 스크립트인가:
    `defs/polygon_market`·`defs/fred_calendar`의 수집 경로는 저장소 안에서
    확인할 수 없는 외부 사실 위에 선다.
        ① 무료 플랜이 **어디까지의 과거**를 주는가 (파티션 시작일이 여기 걸린다)
        ② 휴장일에 빈 응답을 주는가, **직전 거래일 값을 조용히 주는가**
        ③ 뉴스가 기사마다 **다른 발행시각**을 주는가 (누수 방지의 기준축)
        ④ FRED가 ALFRED vintage를 **실제로 다른 값**으로 주는가
    코드를 믿고 자산을 배선하기 전에 먼저 묻는다. 접근성 미확인 상태에서
    적재 코드를 쓰면 그 오류는 파싱 실패가 아니라 **「그럴듯한 값」**으로 나타난다.

🔴 **음성 대조가 이 스크립트의 핵심이다. 200은 성공이 아니다.**
    각 원천마다 **실패해야 하는 요청**을 먼저 보내고, 그것이 실패하지 않으면
    이 프로브는 "통과"가 아니라 **판정 불가**(exit 2)다.

🔴 **③은 실측으로 원천 하나를 이미 탈락시켰다.**
    Benzinga 공개 RSS는 접근이 되고(200·유효 XML·404 음성대조 통과·10건)
    형식도 맞았지만, `pubDate`가 **10건 전부 동일**했다(피드 생성 시각).
    이벤트타임 축이 없으면 뉴스를 거래 세션에 배정할 수 없고, 수집 시각으로
    대신하면 **체계적 편향이 에러 없이** 들어간다. Stooq는 JS 브라우저 검증
    챌린지로 막혔다(우회하지 않는다). 그래서 원천이 Polygon.io로 바뀌었다.

종료코드:
    0  통과 — 계획대로 자산을 배선한다
    1  명확한 거부 — 키 거절·엔트리 권한 부재. 플랜/키를 고친다
    2  판정 불가 — 키 부재·네트워크 실패·**음성 대조 실패**
       ⚠️ `2`를 `0`으로도 `1`로도 읽지 마라. 통과로 읽으면 관측 경로가
          죽은 채 초록이 되고, 거부로 읽으면 멀쩡한 원천을 버린다.
       `--source all`은 **최악 코드**를 돌려준다.

실행 (의존성은 위 PEP 723 블록 — uv가 자동 provisioning):
    uv run scripts/stock_source_access_probe.py --source all
    uv run scripts/stock_source_access_probe.py --source prices

🔴 **디스크에 아무것도 쓰지 않는다.** 원천 약관이 재배포를 제한하므로 응답은
    메모리에서 판정하고 버린다. 🔴 **크리덴셜을 출력하지 않는다.**
    Polygon은 `Authorization: Bearer`를 지원하므로 키가 URL에 실리지 않지만,
    FRED는 `?api_key=`뿐이라 진단 출력이 전부 `mask_url()`을 지난다.

스타일: 스크립트 컨벤션(docs/conventions/python.md)에 따라 절차형으로 쓴다.
    축이 셋이라 `probe_*` 셋으로 나뉘지만 **각 함수 안은 위→아래 절차형**이며
    선례(`physionet_access_probe.py`)의 `main()` 한 벌과 같은 모양이다.
    공통 조각(.env 로드·요청 래퍼)만 Rule of Three로 추출했다.
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

TIMEOUT_S = 30

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_UNDETERMINED = 2

# ── Polygon.io ──────────────────────────────────────────────────────────
POLYGON_BASE = "https://api.polygon.io"

# 🔴 grouped daily는 **1회 호출로 그날 거래된 전 미국 티커**를 준다.
# 일자 파티션과 1:1로 맞고(파티션당 요청 1건), 그날의 유니버스가 응답 그 자체라
# 별도 유니버스 자산 없이 **생존 편향이 구조적으로 해소**된다.
POLYGON_GROUPED_PATH = "/v2/aggs/grouped/locale/us/market/stocks"
POLYGON_NEWS_PATH = "/v2/reference/news"

# 🔴 **파서와 짝을 이루는 상수다.**
# `defs/polygon_market/assets.py`의 `REQUIRED_BAR_FIELDS`·`REQUIRED_NEWS_FIELDS`와
# 같아야 한다. 이 스크립트는 PEP 723 단독 실행이라 그 패키지를 import할 수 없어
# 상수가 두 벌 존재하는데, 두 벌이 조용히 갈라지면 **프로브는 통과하고 파서만 깨진다**.
# 그 갈라짐은 `tests/test_polygon_market_parse.py`의
# `test_probe_and_parser_share_the_field_contract`가 AST 대조로 막는다(의도한 결합).
# 🔴 여기를 고치면 저쪽도 고친다 — 안 고치면 조용히 통과하지 않고 테스트가 멈춘다.

# grouped daily 응답의 필수 필드. T=티커, o/h/l/c=시가·고가·저가·종가, v=거래량,
# t=epoch ms. 하나라도 없으면 스키마 상수를 고쳐야 하므로 판정 불가로 떨군다.
POLYGON_BAR_FIELDS = ("T", "o", "h", "l", "c", "v", "t")

# 뉴스의 필수는 둘뿐이다 — id(중복 제거 키)와 published_utc(이벤트타임 축).
# 제목·링크·퍼블리셔는 없어도 레코드가 성립하므로 필수에 넣지 않는다.
POLYGON_NEWS_FIELDS = ("id", "published_utc")

# 🔴 무료 플랜의 과거 경계를 **재는** 대상. 문서의 "2년"을 믿지 않고 직접 묻는다 —
# 이 값이 `PARTITION_START_DATE`를 정하고, 틀리면 백필이 조용히 빈 파티션을 만든다.
POLYGON_LOOKBACK_PROBE_YEARS = (1, 2, 3, 5)

# ── FRED ────────────────────────────────────────────────────────────────
FRED_BASE = "https://api.stlouisfed.org/fred"

# 🔴 vintage 대조용 시리즈 — **개정이 큰 것**이어야 한다.
# GDPC1(실질 GDP)은 속보치→잠정치→확정치로 바뀌므로, 옛 vintage와 오늘 vintage가
# **같은 observation_date에 다른 값**을 줘야 정상이다. 같으면 ALFRED 축이
# 실재하지 않는 것이고, 그러면 PIT(as-of) 설계의 근거가 사라진다.
FRED_VINTAGE_SERIES = "GDPC1"
FRED_VINTAGE_OLD_DATE = "2015-06-01"

# UA는 반드시 보낸다 — 기본 `python-requests/x.y`는 차단 대상이 되기 쉽다.
# (SEC EDGAR 계열로 폴백할 경우 **연락처 포함 UA가 정책 요구사항**이다.
#  실측: UA에 연락처가 없으면 403, 있으면 200 + application/atom+xml.)
DEFAULT_USER_AGENT = "dagster-study-probe/0.1"


def load_env_value(key: str) -> str:
    """환경변수에서, 없으면 저장소 루트 `.env`에서 값을 읽는다.

    PEP 723 단독 실행이 전제라 python-dotenv를 쓰지 않고 직접 파싱한다.
    `.env.example` 형식을 따라 인라인 주석(`VALUE  # 설명`)과 따옴표를 벗긴다.

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


def mask_url(url: str) -> str:
    """URL에서 쿼리스트링과 자격정보를 벗긴다.

    🔴 FRED는 키를 `?api_key=`로만 받는다. 진단 출력에 URL을 그대로 찍으면
    키가 터미널·로그·스크린샷에 남는다. 출력 경로는 전부 이 함수를 지난다.

    Args:
        url: 원본 URL.

    Returns:
        scheme://host/path 형태의 마스킹된 URL.
    """
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname or ''}{parts.path}"


def get(
    session: requests.Session,
    url: str,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[requests.Response | None, str]:
    """요청을 보내고 (응답, 오류설명)을 돌려준다.

    예외를 올리지 않고 튜플로 돌려주는 이유: 프로브는 **실패도 관측 대상**이라
    각 단계가 자기 판정을 내려야 한다. 🔴 예외 메시지에 전체 URL이 실리므로
    설명에는 마스킹한 URL과 예외 **타입**만 담는다.

    Args:
        session: 재사용할 requests 세션(UA가 붙어 있다).
        url: 요청 URL.
        params: 쿼리 파라미터.
        headers: 이 요청에만 붙일 헤더(Polygon의 Bearer 토큰).

    Returns:
        (응답, ""). 실패면 (None, 사람이 읽는 오류 설명).
    """
    try:
        response = session.get(url, params=params, headers=headers, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        return None, f"{type(exc).__name__} @ {mask_url(url)}"
    return response, ""


def recent_weekday(back_days: int = 5) -> date:
    """최근의 평일 하나를 돌려준다(거래일 추정용).

    🔴 이것은 **거래일 달력이 아니다** — 공휴일을 모른다. 프로브는 "평일인데
    응답이 비었다"를 그 자체로 실패로 읽지 않고 며칠을 훑어 하나라도 데이터가
    있는 날을 찾는다. 달력의 정본은 적재된 가격 테이블이다(silver에서 유도).

    Args:
        back_days: 오늘로부터 며칠 전부터 볼 것인가.

    Returns:
        주말이 아닌 가장 가까운 과거 날짜.
    """
    day = datetime.now(tz=timezone.utc).date() - timedelta(days=back_days)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def probe_prices(session: requests.Session, api_key: str) -> int:
    """Polygon grouped daily의 접근·형식·**휴장일 동작·과거 경계**를 판정한다.

    Args:
        session: 요청 세션.
        api_key: Polygon API 키.

    Returns:
        종료코드(0 통과 / 1 거부 / 2 판정 불가).
    """
    print(f"\n{'=' * 64}\n[시세] {POLYGON_BASE}{POLYGON_GROUPED_PATH}\n{'=' * 64}")
    auth = {"Authorization": f"Bearer {api_key}"}

    probe_day = recent_weekday()
    url = f"{POLYGON_BASE}{POLYGON_GROUPED_PATH}/{probe_day.isoformat()}"

    # ── ① 음성 대조: 키 없이 거부되는가 ──────────────────────────────────
    print("① 음성 대조 — 키 없이 요청")
    response, err = get(session, url)
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.ok:
        print(f"  ✗ {response.status_code} — 키 없이도 응답한다.")
        print("    → 이 경로는 키를 검증하지 않는다. 우리 키의 유효성이 미증명.")
        return EXIT_UNDETERMINED
    print(f"  ✓ {response.status_code} — 키가 필요하다")

    # ── ② 음성 대조: 변조한 키가 거부되는가 ──────────────────────────────
    # 🔴 "키 없음"과 "키 틀림"은 다른 축이다. 없을 때만 막고 틀린 것은 통과시키는
    #    구현이 실재하므로, 마지막 글자를 바꿔 실제로 **검증**하는지 본다.
    print("\n② 음성 대조 — 마지막 글자를 변조한 키")
    tampered = api_key[:-1] + ("0" if api_key[-1] != "0" else "1")
    response, err = get(session, url, headers={"Authorization": f"Bearer {tampered}"})
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.ok:
        print(f"  ✗ {response.status_code} — 변조한 키가 통과했다. 유효성 미증명")
        return EXIT_UNDETERMINED
    print(f"  ✓ {response.status_code} — 키를 실제로 검증한다")

    # ── ③ 본 요청 ───────────────────────────────────────────────────────
    print(f"\n③ 본 요청 — {probe_day.isoformat()}")
    payload: dict[str, Any] = {}
    for _ in range(5):
        response, err = get(session, url, {"adjusted": "true"}, auth)
        if response is None:
            print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
            return EXIT_UNDETERMINED
        if response.status_code in (401, 403):
            print(f"  ✗ {response.status_code} — 키 거절 또는 엔타이틀먼트 부재")
            print("    → 플랜이 이 엔드포인트를 포함하는지 확인한다")
            return EXIT_REJECTED
        if response.status_code == 429:
            print("  ✗ 429 — 무료 플랜 rate limit(5 req/min). 잠시 뒤 다시 돌린다")
            return EXIT_UNDETERMINED
        if not response.ok:
            print(f"  ✗ {response.status_code} → 판정 불가")
            return EXIT_UNDETERMINED
        payload = response.json()
        if payload.get("resultsCount"):
            break
        # 평일이어도 공휴일이면 0건이다. 하루씩 앞으로 물러난다.
        probe_day -= timedelta(days=1)
        while probe_day.weekday() >= 5:
            probe_day -= timedelta(days=1)
        url = f"{POLYGON_BASE}{POLYGON_GROUPED_PATH}/{probe_day.isoformat()}"
        print(f"  · 0건 — {probe_day.isoformat()}로 물러난다(공휴일 가능성)")

    results = payload.get("results") or []
    if not results:
        print("  ✗ 5거래일을 훑어도 0건 → 판정 불가")
        return EXIT_UNDETERMINED
    bar = results[0]
    missing = [f for f in POLYGON_BAR_FIELDS if f not in bar]
    if missing:
        print(f"  ✗ 기대 필드 누락 {missing} · 실제 {sorted(bar)} → 판정 불가")
        return EXIT_UNDETERMINED
    print(f"  ✓ {probe_day.isoformat()} · 티커 {len(results):,}개 · 필드 {sorted(bar)}")
    print(f"    예: {bar['T']} o={bar['o']} h={bar['h']} l={bar['l']} c={bar['c']}")

    # ── ④ 🔴 음성 대조: 휴장일에 직전 거래일 값을 조용히 주는가 ──────────
    # frankfurter_fx가 실측으로 발견한 실패 모드다. 상태코드·행 수·값 범위
    # 어느 것으로도 안 잡히고 **날짜 필드 하나만 다르다**.
    print("\n④ 음성 대조 — 휴장일(직전 일요일)")
    today = datetime.now(tz=timezone.utc).date()
    sunday = today - timedelta(days=(today.weekday() + 1) % 7 or 7)
    closed_url = f"{POLYGON_BASE}{POLYGON_GROUPED_PATH}/{sunday.isoformat()}"
    closed, err = get(session, closed_url, {"adjusted": "true"}, auth)
    if closed is None or not closed.ok:
        code = closed.status_code if closed is not None else err
        print(f"  ? 응답 {code} — 이 축은 **미확인**으로 남는다")
    else:
        closed_payload = closed.json()
        count = closed_payload.get("resultsCount", 0)
        print(f"  요청 {sunday.isoformat()} · resultsCount={count}")
        if count:
            print("  🔴 **휴장일에 데이터가 왔다** — 직전 거래일 대체 가능성.")
            print("    → `source_date` 에코 컬럼이 **필수**임이 확정됐다.")
        else:
            print("  ✓ 0건 — 휴장일에 대체값을 주지 않는다")

    # ── ⑤ 🔴 과거 경계 측정 — 이 값이 PARTITION_START_DATE를 정한다 ──────
    print("\n⑤ 과거 경계 (파티션 시작일의 근거)")
    oldest_ok: date | None = None
    for years in POLYGON_LOOKBACK_PROBE_YEARS:
        past = recent_weekday(back_days=365 * years + 5)
        past_url = f"{POLYGON_BASE}{POLYGON_GROUPED_PATH}/{past.isoformat()}"
        past_response, err = get(session, past_url, {"adjusted": "true"}, auth)
        label = f"  {years}년 전 {past.isoformat()}:"
        if past_response is None:
            print(f"{label} 네트워크 실패({err})")
            continue
        if past_response.status_code in (401, 403):
            print(f"{label} {past_response.status_code} 권한 없음")
            continue
        if not past_response.ok:
            print(f"{label} {past_response.status_code}")
            continue
        count = past_response.json().get("resultsCount", 0)
        print(f"{label} {count:,}건")
        if count:
            oldest_ok = past
    if oldest_ok is None:
        print("  ⚠ 과거 경계를 재지 못했다 — **미확인**. 시작일을 보수적으로 잡는다")
    else:
        print(f"  → 확인된 최고령 조회 가능일: **{oldest_ok.isoformat()}**")
        print("    PARTITION_START_DATE를 이 날짜 **이후**로 잡는다(리터럴 고정)")

    print(f"\n{'=' * 64}\n✓ [시세] 통과")
    return EXIT_OK


def probe_news(session: requests.Session, api_key: str) -> int:
    """Polygon 뉴스의 접근·**이벤트타임 분산**·티커 태그를 판정한다.

    Args:
        session: 요청 세션.
        api_key: Polygon API 키.

    Returns:
        종료코드(0 통과 / 1 거부 / 2 판정 불가).
    """
    print(f"\n{'=' * 64}\n[뉴스] {POLYGON_BASE}{POLYGON_NEWS_PATH}\n{'=' * 64}")
    auth = {"Authorization": f"Bearer {api_key}"}
    url = f"{POLYGON_BASE}{POLYGON_NEWS_PATH}"

    # ── ① 음성 대조: 키 없이 거부되는가 ──────────────────────────────────
    print("① 음성 대조 — 키 없이 요청")
    response, err = get(session, url, {"limit": "1"})
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.ok:
        print(f"  ✗ {response.status_code} — 키 없이도 응답한다. 유효성 미증명")
        return EXIT_UNDETERMINED
    print(f"  ✓ {response.status_code} — 키가 필요하다")

    # ── ② 본 요청: 특정 하루로 질의되는가 ────────────────────────────────
    # 🔴 이것이 RSS와 갈리는 지점이다. 날짜로 질의되면 롤링 윈도우 유실이 없고
    #    **append가 아니라 일자 파티션 교체**로 갈 수 있다(멱등·백필 가능).
    probe_day = recent_weekday()
    print(f"\n② 본 요청 — published_utc {probe_day.isoformat()} 하루")
    response, err = get(
        session,
        url,
        {
            "published_utc.gte": f"{probe_day.isoformat()}T00:00:00Z",
            "published_utc.lt": f"{(probe_day + timedelta(days=1)).isoformat()}"
            "T00:00:00Z",
            "limit": "50",
            "order": "asc",
            "sort": "published_utc",
        },
        auth,
    )
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.status_code in (401, 403):
        print(f"  ✗ {response.status_code} — 키 거절 또는 엔타이틀먼트 부재")
        return EXIT_REJECTED
    if response.status_code == 429:
        print("  ✗ 429 — rate limit(5 req/min). 잠시 뒤 다시 돌린다")
        return EXIT_UNDETERMINED
    if not response.ok:
        print(f"  ✗ {response.status_code} → 판정 불가")
        return EXIT_UNDETERMINED
    payload: dict[str, Any] = response.json()
    articles = payload.get("results") or []
    if not articles:
        print("  ✗ 0건 — 날짜 질의가 듣지 않거나 그날 기사가 없다. 판정 불가")
        return EXIT_UNDETERMINED
    paging = "있음" if payload.get("next_url") else "없음"
    print(f"  ✓ {len(articles)}건 · 페이징 {paging}")
    print(f"    필드: {sorted(articles[0])}")
    # 🔴 파서의 REQUIRED_NEWS_FIELDS와 같은 집합이다(위 상수 주석 참조).
    #    여기서 누락이 잡히면 파서는 KeyError로 죽는다 — 먼저 드러낸다.
    missing = [f for f in POLYGON_NEWS_FIELDS if f not in articles[0]]
    if missing:
        print(f"  ✗ 필수 필드 누락 {missing} → 파서 상수를 고쳐야 한다. 판정 불가")
        return EXIT_UNDETERMINED

    # ── ③ 🔴 음성 대조: 발행시각이 기사마다 다른가 ───────────────────────
    # 🔴 Benzinga RSS를 탈락시킨 바로 그 축이다. 이벤트타임이 없으면 누수 방지
    #    설계 전체가 성립하지 않으므로 여기서 반드시 가른다.
    print("\n③ 음성 대조 — 발행시각의 분산 (Benzinga가 탈락한 축)")
    stamps: list[str] = []
    missing_stamp = 0
    for article in articles:
        stamp = article.get("published_utc")
        if stamp:
            stamps.append(stamp)
        else:
            missing_stamp += 1
    if missing_stamp:
        print(f"  ⚠ published_utc 결측 {missing_stamp}건 — 파서가 견뎌야 한다")
    if not stamps:
        print("  ✗ 발행시각이 0건 → 이벤트타임 축이 없다. 원천을 교체한다")
        return EXIT_REJECTED
    unique = sorted(set(stamps))
    print(f"  고유 시각 {len(unique)}종 / 기사 {len(stamps)}건")
    if len(unique) == 1:
        print("  ✗ 모든 기사의 발행시각이 동일하다 — 이벤트타임 축이 아니다.")
        print("    → Benzinga RSS와 같은 실패. 원천을 교체한다.")
        return EXIT_REJECTED
    print(f"  ✓ 최초 {unique[0]} · 최종 {unique[-1]}")

    # 🔴 세션 경계를 넘는 기사가 실재하는지 — 누수 방지 설계의 존재 이유.
    after_close = [s for s in stamps if s[11:16] >= "20:00"]
    print(f"    20:00Z(=16:00 ET) 이후 발행 {len(after_close)}건")
    if after_close:
        print("    → 장 마감 후 기사가 실재한다. 세션 배정 모델이 반드시 필요하다")

    # ── ④ 티커 태그 ─────────────────────────────────────────────────────
    print("\n④ 티커 태그 (조인 키)")
    tagged = [a for a in articles if a.get("tickers")]
    print(f"  태그 있는 기사 {len(tagged)}/{len(articles)}건")
    if not tagged:
        print("  ✗ 티커 태그가 없다 — 종목별 피처를 만들 수 없다. 판정 불가")
        return EXIT_UNDETERMINED
    sample_tickers = tagged[0]["tickers"][:5]
    print(f"  ✓ 예: {sample_tickers}")

    print(f"\n{'=' * 64}\n✓ [뉴스] 통과 — 날짜 질의가 되므로 **일자 파티션**으로 간다")
    return EXIT_OK


def probe_fred(session: requests.Session) -> int:
    """FRED 키 유효성과 **ALFRED vintage 축의 실재**를 판정한다.

    Args:
        session: 요청 세션.

    Returns:
        종료코드(0 통과 / 1 거부 / 2 판정 불가).
    """
    print(f"\n{'=' * 64}\n[FRED] {FRED_BASE}\n{'=' * 64}")

    api_key = load_env_value("FRED_API_KEY")
    if not api_key:
        print("✗ FRED_API_KEY가 없다(.env 또는 환경변수).")
        print("  → 판정 불가. https://fredaccount.stlouisfed.org/apikeys 에서 발급")
        return EXIT_UNDETERMINED
    print(f"키: {api_key[:4]}***  (값은 출력하지 않는다)")

    # ── ① 음성 대조: 키 없이 거부되는가 ──────────────────────────────────
    print("\n① 음성 대조 — 키 없이 요청")
    response, err = get(
        session, f"{FRED_BASE}/releases/dates", {"file_type": "json", "limit": "1"}
    )
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.ok:
        print(f"  ✗ {response.status_code} — 키 없이도 응답한다. 유효성 미증명")
        return EXIT_UNDETERMINED
    print(f"  ✓ {response.status_code} — 키가 필요하다")

    # ── ② 음성 대조: 변조한 키가 거부되는가 ──────────────────────────────
    print("\n② 음성 대조 — 마지막 글자를 변조한 키")
    tampered = api_key[:-1] + ("0" if api_key[-1] != "0" else "1")
    response, err = get(
        session,
        f"{FRED_BASE}/releases/dates",
        {"file_type": "json", "limit": "1", "api_key": tampered},
    )
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.ok:
        print(f"  ✗ {response.status_code} — 변조한 키가 통과했다. 유효성 미증명")
        return EXIT_UNDETERMINED
    print(f"  ✓ {response.status_code} — 키를 실제로 검증한다")

    # ── ③ 본 요청: 릴리스 캘린더 ────────────────────────────────────────
    print("\n③ 본 요청 — releases/dates")
    response, err = get(
        session,
        f"{FRED_BASE}/releases/dates",
        {"file_type": "json", "limit": "5", "api_key": api_key},
    )
    if response is None:
        print(f"  ✗ 네트워크 실패: {err} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.status_code in (400, 401, 403):
        print(f"  ✗ {response.status_code} — 키가 거부됐다. 키를 다시 확인한다")
        return EXIT_REJECTED
    if not response.ok:
        print(f"  ✗ {response.status_code} → 판정 불가")
        return EXIT_UNDETERMINED
    payload: dict[str, Any] = response.json()
    dates = payload.get("release_dates") or []
    if not dates:
        print("  ✗ release_dates가 비었다 → 판정 불가")
        return EXIT_UNDETERMINED
    sample = dates[0]
    if "release_id" not in sample or "date" not in sample:
        print(f"  ✗ 기대 필드 부재. 실제 키: {sorted(sample)} → 판정 불가")
        return EXIT_UNDETERMINED
    print(f"  ✓ {len(dates)}건 · 필드 {sorted(sample)}")
    # 페이징 필요 여부 — 적재 자산이 limit을 넘기면 조용히 잘린다.
    print(f"  count={payload.get('count')} limit={payload.get('limit')}")

    # ── ④ 🔴 vintage 축의 실재 — 이게 실패하면 PIT 설계의 근거가 사라진다 ─
    print(f"\n④ vintage 대조 — {FRED_VINTAGE_SERIES}")
    obs_params = {
        "file_type": "json",
        "series_id": FRED_VINTAGE_SERIES,
        "api_key": api_key,
    }
    old_response, err = get(
        session,
        f"{FRED_BASE}/series/observations",
        {
            **obs_params,
            "realtime_start": FRED_VINTAGE_OLD_DATE,
            "realtime_end": FRED_VINTAGE_OLD_DATE,
        },
    )
    new_response, err2 = get(session, f"{FRED_BASE}/series/observations", obs_params)
    if old_response is None or new_response is None:
        print(f"  ✗ 네트워크 실패: {err or err2} → 판정 불가")
        return EXIT_UNDETERMINED
    if not old_response.ok or not new_response.ok:
        print(f"  ✗ {old_response.status_code}/{new_response.status_code} → 판정 불가")
        return EXIT_UNDETERMINED

    old_by_date = {
        o["date"]: o["value"] for o in old_response.json().get("observations", [])
    }
    new_by_date = {
        o["date"]: o["value"] for o in new_response.json().get("observations", [])
    }
    if not old_by_date:
        print(f"  ✗ {FRED_VINTAGE_OLD_DATE} vintage가 비었다 → 판정 불가")
        return EXIT_UNDETERMINED
    shared = sorted(set(old_by_date) & set(new_by_date))
    revised = [d for d in shared if old_by_date[d] != new_by_date[d]]
    print(f"  공통 관측일 {len(shared)}건 · 값이 달라진 것 {len(revised)}건")
    if not revised:
        print("  ✗ 옛 vintage와 현재 vintage가 **완전히 같다**.")
        print("    → realtime_start가 응답을 바꾸지 않는다는 뜻이고, 그러면")
        print("      계획 §3의 PIT(as-of) 설계 전체가 근거를 잃는다. 판정 불가.")
        return EXIT_UNDETERMINED
    example = revised[0]
    print(f"  ✓ 예: {example}")
    print(f"    과거 {old_by_date[example]} → 현재 {new_by_date[example]}")
    print("    → vintage 축이 실재한다. vintage_date 파티션 설계가 성립한다.")

    print(f"\n{'=' * 64}\n✓ [FRED] 통과")
    return EXIT_OK


def main() -> int:
    """인자를 읽어 축별 프로브를 실행하고 **최악 종료코드**를 돌려준다."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", choices=["prices", "news", "fred", "all"], default="all"
    )
    parser.add_argument("--user-agent", default="")
    args = parser.parse_args()

    # UA 우선순위: --user-agent > .env의 NEWS_RSS_USER_AGENT > 기본값.
    # 🔴 SEC EDGAR 폴백 경로는 **연락처 포함 UA가 정책 요구사항**이라(없으면 403)
    #    환경변수로 둔다 — 개인 연락처이므로 코드에 박지 않는다.
    user_agent = (
        args.user_agent or load_env_value("NEWS_RSS_USER_AGENT") or DEFAULT_USER_AGENT
    )
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    print(f"User-Agent: {user_agent}")

    results: dict[str, int] = {}
    if args.source in ("prices", "news", "all"):
        polygon_key = load_env_value("POLYGON_API_KEY")
        if not polygon_key:
            print("✗ POLYGON_API_KEY가 없다(.env 또는 환경변수).")
            print("  → 판정 불가. https://polygon.io/dashboard/api-keys 에서 발급")
            if args.source in ("prices", "all"):
                results["prices"] = EXIT_UNDETERMINED
            if args.source in ("news", "all"):
                results["news"] = EXIT_UNDETERMINED
        else:
            print(f"Polygon 키: {polygon_key[:4]}***  (값은 출력하지 않는다)")
            if args.source in ("prices", "all"):
                results["prices"] = probe_prices(session, polygon_key)
            if args.source in ("news", "all"):
                results["news"] = probe_news(session, polygon_key)
    if args.source in ("fred", "all"):
        results["fred"] = probe_fred(session)

    print(f"\n{'=' * 64}\n판정표\n{'=' * 64}")
    label = {EXIT_OK: "통과", EXIT_REJECTED: "거부", EXIT_UNDETERMINED: "판정 불가"}
    for name, code in results.items():
        print(f"  {name:6s} {code}  {label[code]}")
    worst = max(results.values())
    print(f"\n종료코드 {worst} ({label[worst]})")
    if worst == EXIT_UNDETERMINED:
        print("⚠️ 판정 불가는 실패가 아니라 **미확인**이다. 통과로도 거부로도")
        print("   읽지 말고, 위 출력에서 어느 축이 안 닫혔는지 보고 그것만 푼다.")
    return worst


if __name__ == "__main__":
    sys.exit(main())
