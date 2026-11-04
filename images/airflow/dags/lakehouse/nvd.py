"""NVD CVE·Change History API 순회와 raw 스냅샷 회차 프로토콜, CISA KEV 착지.

옛 Dagster NVD 모듈(NVD 리소스·창 순회·KEV 착지·읽기부, 이관 후 삭제됨)의 이식이다.
Airflow를 import하지 않는다 — Connection 조회·S3 클라이언트 생성은 DAG가 하고
값만 넘긴다.

두 부분이 한 파일에 있는 이유는 **쓰는 쪽과 읽는 쪽이 같은 계약을 봐야** 하기 때문이다.
raw 착지는 `land_window_pages`로 쓰고 bronze 적재는 `read_window_pages`로 읽는다 —
회차 경로·커밋 표지·절단 가드가 두 벌로 갈라지면 **쓰기는 성공하고 읽기만 엉뚱한
페이지를 집는다**(에러 없이).

레이아웃(수집 회차마다 경로가 갈린다):
    `<source_base>/<feed>/dt=YYYY-MM-DD/fetched=YYYYMMDDTHHMMSSZ/page-NNNN.json.gz`
    (+ `.sha256` 사이드카)

🔴 **어떤 경로도 기존 객체를 지우거나 덮어쓰지 않는다.** raw는 수집 시점의 스냅샷이고
같은 과거 파티션을 나중에 다시 받으면 내용이 다르다(그 사이 다시 수정된 CVE는
`lastModified`가 다른 날로 옮겨간다). 그래서 `force` 재수집은 옛 스냅샷을 대체하지 않고
**새 회차(`fetched=`)를 옆에 추가**한다. 새 회차 경로에 객체가 이미 있으면 실패한다.

🔴 **커밋 표지는 회차 안 page-0000의 사이드카다.** page-0000 본문은 메모리에 들고 있다가
나머지 페이지와 중복 가드를 모두 통과한 뒤 **마지막에** 쓴다. 표지가 없는 회차(중간에
죽은 것)는 읽지 않으므로, force가 도중에 실패하면 **직전 커밋 회차가 그대로 최신**이다.
bronze는 dt마다 **가장 최근에 커밋된 회차**를 읽는다.

🔴 **NVD 키는 `apiKey` 헤더로만 간다.** 쿼리에 넣으면 `http._get`의 실패 메시지(전체 URL
포함)에 키가 박힌다. 그래도 `requests` 예외는 `raise_masked`로 재포장해 쿼리·원인 체인을
버린다. 반환 dict·로그에도 키를 싣지 않는다(XCom·Asset event로 흘러가는 값이다).
"""

import json
import logging
import math
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import requests

from lakehouse.http import raise_masked, request_bytes
from lakehouse.s3raw import (
    SIDECAR_SUFFIX,
    gzip_sha256,
    list_keys,
    parse_s3_uri,
    put_gzip_with_sidecar,
    read_sidecar,
    read_verified_gzip,
)

log = logging.getLogger(__name__)

# ── 접속 ──────────────────────────────────────────────────────────────
NVD_BASE_URL = "https://services.nvd.nist.gov/rest/json"
NVD_TIMEOUT_S = 60
# 429·5xx 재시도 횟수. 지수 백오프(1+2+4+8+16=31초)가 롤링 30초 창 하나를
# 넘기도록 5로 둔다.
NVD_RETRIES = 5

# 요청 간 최소 간격(초). 🔴 **429를 재시도에만 맡기지 않는다.**
# 프로브 실측: 키 없이 30초당 5회로 페이싱해도 429가 났다(85요청 중 6회).
# `http._get`이 429를 Retry-After/지수 백오프로 재시도하지만, 그것은 한도를
# 넘은 **뒤의** 회복이고 예산을 태운다. 간격은 롤링 30초 창의 상한(키 없음 5 /
# 키 있음 50 — 프로브의 기대값)보다 여유 있게 잡는다: 30/5=6 → 6.5, 30/50=0.6 → 0.7.
# ⚠️ 키 있음 축은 **키 부재로 미검증**이다 — 키를 받으면 프로브 ④로 확인한다.
# ⚠️ 이 간격은 **프로세스 하나 안에서만** 지켜진다. 여러 태스크가 동시에 NVD를 부르지
#    않게 하는 것은 Airflow Pool `nvd_api`(slots=1 — 차트에서 선언형으로 만든다)가
#    맡는다.
NVD_MIN_INTERVAL_NO_KEY_S = 6.5
NVD_MIN_INTERVAL_WITH_KEY_S = 0.7

# 페이지 키 자릿수. 하루 구간이 이것을 넘는 페이지를 낼 일은 없지만
# (하루 ≈ 1페이지 실측), 넘으면 키 정렬이 깨지므로 fail-closed로 막는다.
PAGE_INDEX_DIGITS = 4
MAX_PAGE_INDEX = 10**PAGE_INDEX_DIGITS - 1

# 회차 경로의 수집 시각 형식(UTC). 사전순 = 시간순이라 최신 회차를 `max`로 고른다.
FETCHED_PREFIX = "fetched="
FETCHED_FORMAT = "%Y%m%dT%H%M%SZ"

# 일자 구간 회차의 커밋 표지 파일(이 파일의 사이드카가 표지다).
WINDOW_COMMIT_FILE = "page-0000.json.gz"

# ── raw 레이아웃 ──────────────────────────────────────────────────────
# raw 랜딩 루트. 피드별 하위 경로는 `<feed>/dt=YYYY-MM-DD/...`.
SOURCE_BASE = "s3://warehouse/raw/nvd"

# 피드 이름 = S3 하위 경로 이름. 🔴 **바꾸면 이미 받은 raw를 bronze가 못 찾는다**
# (에러가 아니라 「raw 미완결」로 드러난다). 레이아웃 계약이다.
CVES_FEED = "cves"
HISTORY_FEED = "cve_history"
KEV_FEED = "kev"

# ── NVD CVE API 2.0 ───────────────────────────────────────────────────
CVES_PATH = "/cves/2.0"
# 🔴 페이지 상한 2000(초과 시 404 — 프로브 실측). 상한에 맞춰 요청 수를 최소화한다.
CVES_PAGE_SIZE = 2000
# 구간 파라미터 — 🔴 **항상 짝으로 보낸다**(한쪽만 보내면 404, 실측).
CVES_START_PARAM = "lastModStartDate"
CVES_END_PARAM = "lastModEndDate"

# ── NVD CVE Change History API 2.0 ────────────────────────────────────
HISTORY_PATH = "/cvehistory/2.0"
# 🔴 페이지 상한 5000(초과 시 404 — 프로브 실측). CVE와 다르다.
HISTORY_PAGE_SIZE = 5000
HISTORY_START_PARAM = "changeStartDate"
HISTORY_END_PARAM = "changeEndDate"

# ── CISA KEV ──────────────────────────────────────────────────────────
# 키 없음·CC0 1.0. 🔴 NVD 키를 이 호스트로 보내지 않도록 `fetch_page`를 거치지 않는다.
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
KEV_FILE_NAME = "kev.json.gz"
KEV_TIMEOUT_S = 60
KEV_RETRIES = 3


def min_interval_s(api_key: str) -> float:
    """키 유무에 따른 요청 간 최소 간격(초)."""
    return NVD_MIN_INTERVAL_WITH_KEY_S if api_key else NVD_MIN_INTERVAL_NO_KEY_S


def fetch_page(
    api_key: str,
    path: str,
    params: dict[str, str],
    *,
    base_url: str = NVD_BASE_URL,
) -> bytes:
    """한 페이지를 받아 **원문 바이트**로 돌려준다(요청 전에 페이싱한다).

    Args:
        api_key: NVD API 키. 빈 문자열이면 헤더를 보내지 않는다.
        path: `/cves/2.0` 형태의 경로.
        params: 쿼리 파라미터. 🔴 **키를 여기 넣지 않는다** — 헤더로 간다.
        base_url: API 루트(`…/rest/json`).

    Returns:
        응답 본문 바이트.

    Raises:
        RuntimeError: 요청 실패(쿼리·원인 체인을 버린 메시지).
    """
    time.sleep(min_interval_s(api_key))
    url = f"{base_url}{path}"
    try:
        return request_bytes(
            url,
            params=params,
            headers={"apiKey": api_key} if api_key else None,
            timeout_s=NVD_TIMEOUT_S,
            retries=NVD_RETRIES,
        )
    except requests.RequestException as exc:
        raise_masked(url, exc)


def iter_window_pages(
    api_key: str,
    path: str,
    *,
    window_params: dict[str, str],
    items_key: str,
    page_size: int,
) -> Iterator[tuple[int, bytes, dict[str, Any]]]:
    """구간 조회를 `startIndex`로 끝까지 순회하며 페이지를 하나씩 내준다.

    🔴 **받은 합계 ≠ `totalResults`면 실패한다**(조용한 절단 금지). 잘린 응답은 행이
    생기고 수만 모자라서 어디서도 에러가 나지 않는다. 가드는 마지막 페이지를 내준
    **뒤에** 걸리므로, 호출부는 제너레이터가 끝까지 돈 다음에만 커밋해야 한다
    (`land_window_pages`가 그렇게 한다).

    🔴 **순회 도중 `totalResults`가 바뀌면 실패한다.** 그 사이 구간 안의 CVE가
    다시 수정돼 밖으로 나가면 뒤 레코드가 앞으로 당겨져 `startIndex` 기준으로
    **한 건을 건너뛴다**. 합계 대조만으로는 이것을 못 잡을 수 있다
    (나간 만큼 total도 줄어든다).

    Args:
        api_key: NVD API 키(헤더로만 전달).
        path: API 경로.
        window_params: 구간 시작·끝 파라미터(항상 짝으로).
        items_key: 레코드 배열 키(`vulnerabilities`·`cveChanges`).
        page_size: 요청 페이지 크기(원천 상한).

    Yields:
        (페이지 번호, 원문 바이트, 파싱된 JSON).

    Raises:
        RuntimeError: 절단·총계 변동·진행 없음·페이지 상한 초과.
        KeyError: 응답에 `totalResults`가 없을 때.
    """
    total: int | None = None
    received = 0
    index = 0
    max_pages = 1
    while True:
        params = {
            **window_params,
            "resultsPerPage": str(page_size),
            "startIndex": str(received),
        }
        body = fetch_page(api_key, path, params)
        payload = json.loads(body)
        page_total = int(payload["totalResults"])
        if total is None:
            total = page_total
            max_pages = max(1, math.ceil(total / page_size))
        elif page_total != total:
            message = (
                f"{path}: 순회 도중 totalResults가 {total} → {page_total}로 "
                "바뀌었다 — 구간 안 레코드가 이동했다. 잠시 뒤 다시 받는다."
            )
            raise RuntimeError(message)
        items = payload.get(items_key)
        if items is None:
            message = f"{path}: 응답에 `{items_key}`가 없다"
            raise RuntimeError(message)
        received += len(items)
        yield index, body, payload
        index += 1
        if received >= total:
            break
        if not items:
            message = f"{path}: 빈 페이지 — {received}/{total}에서 진행이 멈췄다"
            raise RuntimeError(message)
        if index >= max_pages:
            message = (
                f"{path}: 예상 페이지 수 {max_pages}를 넘었다({received}/{total}) "
                "— 페이지가 짧게 온다. 잘린 파티션을 정상으로 두지 않는다."
            )
            raise RuntimeError(message)
    if received != total:
        message = (
            f"{path}: 응답이 {total}건을 신고했는데 {received}건을 받았다 — "
            "절단 또는 중복이다."
        )
        raise RuntimeError(message)


def window_params(
    window_date: str, *, start_param: str, end_param: str
) -> dict[str, str]:
    """UTC 하루를 NVD 구간 파라미터 짝으로 바꾼다.

    `D T00:00:00.000Z ~ D T23:59:59.999Z` — 끝을 다음 날 0시로 두면 경계 시각의
    레코드가 이웃 파티션과 **두 번** 세진다. `.999Z` 형식이 받아들여지고 창 합집합이
    전체와 같다는 것은 프로브 History 전구간 스캔이 같은 형식으로 확인했다.

    Args:
        window_date: 창 날짜(`YYYY-MM-DD`).
        start_param: 시작 파라미터 이름.
        end_param: 끝 파라미터 이름.

    Returns:
        `{start_param: ..., end_param: ...}`.
    """
    return {
        start_param: f"{window_date}T00:00:00.000Z",
        end_param: f"{window_date}T23:59:59.999Z",
    }


def window_prefix(source_base: str, feed: str, window_date: str) -> str:
    """창 하나의 raw 접두 URI(`…/<feed>/dt=YYYY-MM-DD`). 회차는 이 아래에 생긴다."""
    return f"{source_base}/{feed}/dt={window_date}"


def run_segment(fetched_at: datetime) -> str:
    """수집 시각을 회차 경로 조각(`fetched=YYYYMMDDTHHMMSSZ`)으로 바꾼다."""
    return FETCHED_PREFIX + fetched_at.astimezone(timezone.utc).strftime(FETCHED_FORMAT)


def parse_run_fetched_at(run_key: str) -> datetime:
    """회차 키에서 수집 시각(tz-aware UTC)을 복원한다.

    🔴 bronze의 `fetched_at`은 **경로의 이 값**이다 — S3 `LastModified`와 달리 객체를
    다시 복사해도 바뀌지 않고, 스토리지 시계에 기대지 않는다.

    Raises:
        ValueError: 키에 `fetched=` 조각이 없을 때.
    """
    for part in run_key.split("/"):
        if part.startswith(FETCHED_PREFIX):
            stamp = part[len(FETCHED_PREFIX) :]
            return datetime.strptime(stamp, FETCHED_FORMAT).replace(tzinfo=timezone.utc)
    message = f"회차 키에 `{FETCHED_PREFIX}`가 없다: {run_key}"
    raise ValueError(message)


def page_key(prefix_key: str, index: int) -> str:
    """페이지 번호를 객체 키로 바꾼다(`…/page-NNNN.json.gz`).

    Raises:
        ValueError: 자릿수를 넘는 번호(키 정렬이 깨진다).
    """
    if not 0 <= index <= MAX_PAGE_INDEX:
        message = f"페이지 번호 범위 밖: {index}"
        raise ValueError(message)
    return f"{prefix_key}/page-{index:0{PAGE_INDEX_DIGITS}d}.json.gz"


def latest_committed_run(s3: Any, prefix_uri: str, commit_file: str) -> str | None:
    """접두 아래 커밋된 회차 중 **가장 최근** 회차의 키(접두)를 돌려준다.

    커밋 = `<회차>/<commit_file>` 의 사이드카가 있다. 접두는 dt 하나(일자 피드)일 수도,
    피드 루트(KEV — `dt=…/fetched=…`)일 수도 있다 — 둘 다 사전순 = 시간순이다.

    Args:
        s3: boto3 S3 클라이언트.
        prefix_uri: 탐색 접두 URI.
        commit_file: 커밋 표지 파일명(`page-0000.json.gz`·`kev.json.gz`).

    Returns:
        회차 키(`…/fetched=…`, 버킷 제외). 커밋된 회차가 없으면 None.
    """
    bucket, prefix_key = parse_s3_uri(prefix_uri)
    tail = f"/{commit_file}{SIDECAR_SUFFIX}"
    runs = [
        key[: -len(tail)]
        for key in list_keys(s3, bucket, f"{prefix_key}/")
        if key.endswith(tail)
        and key[: -len(tail)].rsplit("/", 1)[-1].startswith(FETCHED_PREFIX)
    ]
    return max(runs) if runs else None


def assert_new_run_prefix(s3: Any, bucket: str, run_key: str) -> None:
    """새 회차 경로가 비어 있는지 확인한다(덮어쓰기 금지 — fail-closed).

    같은 초에 두 번 수집하면 회차 경로가 겹친다. 그때 덮어쓰면 커밋된 스냅샷이
    파괴되므로, 이어 쓰지 않고 실패시킨다(잠시 뒤 다시 돌리면 다른 회차가 된다).

    Raises:
        RuntimeError: 회차 경로에 객체가 이미 있을 때.
    """
    if list_keys(s3, bucket, f"{run_key}/"):
        message = f"s3://{bucket}/{run_key}: 회차 경로가 이미 있다 — 덮어쓰지 않는다"
        raise RuntimeError(message)


def land_window_pages(
    s3: Any,
    *,
    api_key: str,
    feed: str,
    path: str,
    start_param: str,
    end_param: str,
    items_key: str,
    item_key: str,
    id_field: str,
    page_size: int,
    window_date: str,
    fetched_at: datetime,
    force: bool,
    source_base: str = SOURCE_BASE,
) -> dict[str, Any]:
    """UTC 하루 구간을 끝까지 받아 **새 회차**로 S3 raw에 놓는다(raw 착지 본문).

    CVE·History 두 피드가 쓴다. 추출한 이유는 이것이 **읽는 쪽(`read_window_pages`)과
    짝을 이루는 프로토콜**이라서다 — 커밋 순서가 따로 적히면 한쪽만 바뀌는 순간
    bronze가 미완결 회차를 읽는다.

    🔴 **레코드 ID 중복은 커밋 전에 거른다**. 순회 중 레코드가 이동하면 페이지 경계에서
    같은 ID가 두 번 나오는데, 그 회차를 커밋하면 비-force 실행이 영영 스킵하는
    **깨진 최신 스냅샷**이 된다.

    Args:
        s3: boto3 S3 클라이언트.
        api_key: NVD API 키(헤더로만 전달, 반환값에 싣지 않는다).
        feed: 피드 이름(S3 하위 경로).
        path: API 경로.
        start_param: 구간 시작 파라미터 이름.
        end_param: 구간 끝 파라미터 이름.
        items_key: 레코드 배열 키(`vulnerabilities`).
        item_key: 배열 원소 안 레코드 키(`cve`).
        id_field: 레코드 ID 필드(`id`·`cveChangeId`).
        page_size: 요청 페이지 크기.
        window_date: 구간 날짜(`YYYY-MM-DD`).
        fetched_at: 이번 수집 회차의 시각(UTC) — 회차 경로가 된다.
        force: True면 커밋된 회차가 있어도 새 회차를 추가로 받는다.
        source_base: raw 루트 URI.

    Returns:
        판정·수집 결과(날짜·행 수·해시·경로만 — 원천 값과 키는 없다).

    Raises:
        RuntimeError: 회차 경로 충돌·절단·ID 중복.
    """
    dt_uri = window_prefix(source_base, feed, window_date)
    latest = latest_committed_run(s3, dt_uri, WINDOW_COMMIT_FILE)
    if latest and not force:
        # 🔴 스킵도 판정 근거를 남긴다 — "검사하고 건너뛴 것"과 "아무것도 안 한 것"이
        #    같은 모양이 되지 않게.
        log.info("%s: 커밋된 회차가 있다 — 재수집 생략", latest)
        return {
            "decision": "skipped_committed",
            "latest_run": latest,
            "api_requests": 0,
        }

    bucket, dt_key = parse_s3_uri(dt_uri)
    run_key = f"{dt_key}/{run_segment(fetched_at)}"
    assert_new_run_prefix(s3, bucket, run_key)

    params = window_params(window_date, start_param=start_param, end_param=end_param)
    first_body: bytes | None = None
    total_results = 0
    pages = 0
    ids: list[str] = []
    gzip_bytes = 0
    for index, body, payload in iter_window_pages(
        api_key,
        path,
        window_params=params,
        items_key=items_key,
        page_size=page_size,
    ):
        pages += 1
        ids.extend(item[item_key][id_field] for item in payload[items_key])
        if index == 0:
            first_body = body
            total_results = int(payload["totalResults"])
            continue
        gzip_bytes += put_gzip_with_sidecar(s3, bucket, page_key(run_key, index), body)[
            "gzip_bytes"
        ]

    if len(ids) != len(set(ids)):
        message = (
            f"{run_key}: 같은 `{id_field}`가 두 번 나왔다 — 페이지 이동 의심. "
            "회차를 커밋하지 않는다."
        )
        raise RuntimeError(message)
    # 제너레이터가 끝까지 돌았다 = 절단 가드를 통과했다. 이제야 커밋한다.
    if first_body is None:  # 도달 불가(첫 페이지는 항상 나온다) — 타입 체커용
        message = f"{run_key}: 첫 페이지를 받지 못했다"
        raise RuntimeError(message)
    written = put_gzip_with_sidecar(s3, bucket, page_key(run_key, 0), first_body)
    gzip_bytes += written["gzip_bytes"]

    log.info(
        "%s: %d건 / %d페이지 수집·커밋 (totalResults=%d)",
        run_key,
        len(ids),
        pages,
        total_results,
    )
    return {
        "decision": "downloaded_new_run" if latest else "downloaded",
        "target": f"s3://{bucket}/{run_key}",
        # force로 새 회차를 더했으면 직전 최신 회차(지우지 않고 남아 있다).
        "previous_run": latest or "없음",
        "window_start": params[start_param],
        "window_end": params[end_param],
        "total_results": total_results,
        "rows": len(ids),
        "pages": pages,
        # 🔴 단위: **성공한 페이지 요청 수**다. `http._get`의 429·5xx
        #    재시도는 여기 안 잡힌다(그 수는 관측 경로가 없다 — 미측정).
        "api_requests": pages,
        "page_size": page_size,
        "gzip_bytes": gzip_bytes,
        "commit_sha256": written["sha256"],
    }


def read_window_pages(
    s3: Any,
    *,
    feed: str,
    window_date: str,
    items_key: str,
    source_base: str = SOURCE_BASE,
) -> tuple[list[dict[str, Any]], datetime, str]:
    """dt의 **가장 최근 커밋된 회차**를 읽어 페이지 JSON 목록을 돌려준다.

    읽을 페이지 수는 **page-0000의 `totalResults`가 정한다** — 디렉터리를 나열해
    있는 만큼 읽지 않는다(회차 안에는 그 회차가 쓴 페이지만 있지만, 계약을 나열에
    기대지 않는다).

    Args:
        s3: boto3 S3 클라이언트.
        feed: 피드 이름.
        window_date: 구간 날짜.
        items_key: 레코드 배열 키.
        source_base: raw 루트 URI.

    Returns:
        (페이지 JSON 목록, 회차 수집 시각, 회차 URI).

    Raises:
        RuntimeError: 커밋된 회차 없음·무결성 불일치·총계 불일치·절단.
    """
    dt_uri = window_prefix(source_base, feed, window_date)
    run_key = latest_committed_run(s3, dt_uri, WINDOW_COMMIT_FILE)
    if run_key is None:
        message = (
            f"{feed} dt={window_date}: 커밋된 raw 회차가 없다(raw 미완결) — "
            "raw 착지 태스크를 먼저 실행한다"
        )
        raise RuntimeError(message)
    bucket, _ = parse_s3_uri(dt_uri)
    first = json.loads(read_verified_gzip(s3, bucket, page_key(run_key, 0)))
    total = int(first["totalResults"])
    pages: list[dict[str, Any]] = [first]
    received = len(first[items_key])
    index = 1
    while received < total:
        payload = json.loads(read_verified_gzip(s3, bucket, page_key(run_key, index)))
        if int(payload["totalResults"]) != total:
            message = (
                f"{run_key} page {index}: totalResults가 page-0000과 다르다 — "
                "다른 수집의 페이지가 섞였다."
            )
            raise RuntimeError(message)
        if not payload[items_key]:
            message = f"{run_key} page {index}: 빈 페이지"
            raise RuntimeError(message)
        received += len(payload[items_key])
        pages.append(payload)
        index += 1
    if received != total:
        message = f"{run_key}: totalResults {total}건인데 {received}건을 읽었다"
        raise RuntimeError(message)
    return pages, parse_run_fetched_at(run_key), f"s3://{bucket}/{run_key}"


def land_kev(
    s3: Any,
    *,
    fetched_at: datetime,
    force: bool,
    source_base: str = SOURCE_BASE,
) -> dict[str, Any]:
    """KEV 카탈로그 전체를 `dt=<수집일>/fetched=<수집시각>/kev.json.gz`로 놓는다.

    🔴 **NVD 키를 쓰지 않는다** — KEV는 다른 호스트(cisa.gov)이고 키가 없다.
    `fetch_page`를 거치면 NVD `apiKey` 헤더가 남의 호스트로 나간다.

    🔴 **완결성은 착지 전에 본다**(`count == len(vulnerabilities)`·`cveID` 유일).
    잘리거나 중복된 응답을 스냅샷으로 커밋하면 그것이 최신 회차가 된다.
    필드 계약 전체는 bronze 파서(`nvd_parse.kev_to_arrow`)가 본다.

    🔴 **항상 GET하고, 내용이 같으면 쓰지 않는다**(매시간 수집). 새 본문의 gzip(mtime=0)
    sha256이 피드 전체의 최신 커밋 회차 사이드카와 같고 `force`가 아니면
    `skipped_unchanged`다 — 같은 날 안의 재호출도, 날짜를 넘긴 호출도 같은 규칙이다.
    (Dagster의 「같은 dt면 API 미호출 스킵」은 매시간 수집과 양립하지 않아 대체했다.)

    Args:
        s3: boto3 S3 클라이언트.
        fetched_at: 이번 수집 회차의 시각(UTC).
        force: True면 내용이 같아도 새 회차를 추가로 놓는다.
        source_base: raw 루트 URI.

    Returns:
        판정·수집 결과.

    Raises:
        RuntimeError: 절단·중복·회차 경로 충돌.
    """
    body = request_bytes(
        KEV_URL, params={}, timeout_s=KEV_TIMEOUT_S, retries=KEV_RETRIES
    )
    payload = json.loads(body)
    entries = payload["vulnerabilities"]
    if int(payload["count"]) != len(entries):
        message = (
            f"KEV count={payload['count']}인데 항목 {len(entries)}건 — 잘린 응답이다. "
            "스냅샷으로 커밋하지 않는다."
        )
        raise RuntimeError(message)
    ids = [entry["cveID"] for entry in entries]
    if len(ids) != len(set(ids)):
        message = "KEV cveID 중복 — 스냅샷으로 커밋하지 않는다."
        raise RuntimeError(message)

    feed_uri = f"{source_base}/{KEV_FEED}"
    bucket, feed_key = parse_s3_uri(feed_uri)
    latest = latest_committed_run(s3, feed_uri, KEV_FILE_NAME)
    if latest and not force:
        previous = read_sidecar(s3, bucket, f"{latest}/{KEV_FILE_NAME}")
        if previous == gzip_sha256(body):
            log.info("%s: 내용이 같다 — 새 회차를 쓰지 않는다", latest)
            return {
                "decision": "skipped_unchanged",
                "latest_run": latest,
                "api_requests": 1,
            }

    run_date = fetched_at.astimezone(timezone.utc).date().isoformat()
    run_key = f"{feed_key}/dt={run_date}/{run_segment(fetched_at)}"
    assert_new_run_prefix(s3, bucket, run_key)
    written = put_gzip_with_sidecar(s3, bucket, f"{run_key}/{KEV_FILE_NAME}", body)
    log.info("%s: KEV %d건 수집·커밋", run_key, len(entries))
    return {
        "decision": "downloaded_new_run" if latest else "downloaded",
        "target": f"s3://{bucket}/{run_key}/{KEV_FILE_NAME}",
        "previous_run": latest or "없음",
        "catalog_version": str(payload.get("catalogVersion")),
        "rows": len(entries),
        "total_results": int(payload["count"]),
        "pages": 1,
        "api_requests": 1,
        "gzip_bytes": written["gzip_bytes"],
        "commit_sha256": written["sha256"],
    }


def read_latest_kev(
    s3: Any, *, source_base: str = SOURCE_BASE
) -> tuple[dict[str, Any], datetime, str]:
    """가장 최근에 커밋된 KEV 스냅샷을 무결성 대조 후 읽는다.

    Returns:
        (KEV JSON 전체, 회차 수집 시각, 객체 URI).

    Raises:
        RuntimeError: 커밋된 회차가 없거나 무결성이 맞지 않을 때.
    """
    feed_uri = f"{source_base}/{KEV_FEED}"
    run_key = latest_committed_run(s3, feed_uri, KEV_FILE_NAME)
    if run_key is None:
        message = (
            f"{feed_uri}: 커밋된 KEV 회차가 없다 — raw 착지 태스크를 먼저 실행한다"
        )
        raise RuntimeError(message)
    bucket, _ = parse_s3_uri(feed_uri)
    key = f"{run_key}/{KEV_FILE_NAME}"
    payload = json.loads(read_verified_gzip(s3, bucket, key))
    return payload, parse_run_fetched_at(run_key), f"s3://{bucket}/{key}"
