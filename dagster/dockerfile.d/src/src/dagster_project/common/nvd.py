"""NVD CVE·Change History API 접속 리소스와 raw 스냅샷 레이아웃 프로토콜.

두 부분이 한 파일에 있는 이유는 **쓰는 쪽과 읽는 쪽이 같은 계약을 봐야** 하기 때문이다.
raw 자산은 `land_window_pages`로 쓰고 bronze 자산은 `read_window_pages`로 읽는다 —
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

비-force 실행은 해당 dt에 커밋된 회차가 하나라도 있으면 API를 부르지 않고 스킵한다.
"""

import json
import math
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from dagster_aws.s3 import S3Resource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.fetch import (
    SIDECAR_SUFFIX,
    put_gzip_with_sidecar,
    read_verified_gzip,
)
from dagster_project.common.helper import fetch_bytes, parse_s3_uri

# 요청 간 최소 간격(초). 🔴 **429를 재시도에만 맡기지 않는다.**
# 프로브 실측: 키 없이 30초당 5회로 페이싱해도 429가 났다(85요청 중 6회).
# `helper._request`가 429를 Retry-After/지수 백오프로 재시도하지만, 그것은 한도를
# 넘은 **뒤의** 회복이고 예산을 태운다. 간격은 롤링 30초 창의 상한(키 없음 5 /
# 키 있음 50 — 프로브의 기대값)보다 여유 있게 잡는다: 30/5=6 → 6.5, 30/50=0.6 → 0.7.
# ⚠️ 키 있음 축은 **키 부재로 미검증**이다 — 키를 받으면 프로브 ④로 확인한다.
# ⚠️ 이 간격은 **프로세스 하나 안에서만** 지켜진다. 여러 run이 동시에 NVD를 부르지
#    않게 하는 것은 아래 pool(`NVD_API_POOL`)이 맡는다.
NVD_MIN_INTERVAL_NO_KEY_S = 6.5
NVD_MIN_INTERVAL_WITH_KEY_S = 0.7

# 🔴 **NVD API를 부르는 step은 인스턴스 전체에서 동시에 1개만 돈다** — Dagster pool로
# 강제한다(raw 자산의 `pool=`). 한도 값은 **인스턴스 DB**에 산다:
#     dagster instance concurrency set nvd_api 1
# (`DAGSTER_HOME`=`dagster/dockerfile.d/src`의 `dagster.yaml`이 가리키는 Postgres).
# `dagster.yaml`의 `concurrency.pools.default_limit`로 선언하지 않은 이유:
# Dagster 1.12는 `concurrency` 블록이 있으면
# `run_coordinator.config.max_concurrent_runs`를 충돌로 거부하고
# (instance/config.py `validate_concurrency_config`), 대체 위치인
# `concurrency.runs.max_concurrent_runs`는 env 참조를 받지 않는다(타입 `int`).
# 한도를 빠뜨리면 pool은 **무제한으로 조용히** 돈다 — 그래서 `assert_pool_limited`가
# API를 부르기 전에 한도를 읽어 1이 아니면 실패시킨다(fail-closed).
NVD_API_POOL = "nvd_api"
NVD_API_POOL_LIMIT = 1

# 페이지 키 자릿수. 하루 구간이 이것을 넘는 페이지를 낼 일은 없지만
# (하루 ≈ 1페이지 실측), 넘으면 키 정렬이 깨지므로 fail-closed로 막는다.
PAGE_INDEX_DIGITS = 4
MAX_PAGE_INDEX = 10**PAGE_INDEX_DIGITS - 1

# 회차 경로의 수집 시각 형식(UTC). 사전순 = 시간순이라 최신 회차를 `max`로 고른다.
FETCHED_PREFIX = "fetched="
FETCHED_FORMAT = "%Y%m%dT%H%M%SZ"

# 일자 구간 회차의 커밋 표지 파일(이 파일의 사이드카가 표지다).
WINDOW_COMMIT_FILE = "page-0000.json.gz"


class NvdResource(dg.ConfigurableResource):
    """NVD REST API 2.0 접속(CVE·Change History).

    🔴 **인증은 `apiKey` HTTP 헤더다.** 쿼리 파라미터가 아니라서 `helper._request`가
    경고한 "예외 메시지에 URL이 실려 키가 로그에 박히는" 축이 없다 — 그래서 마스킹
    래퍼가 없다(`common/polygon.py`와 같은 판단). ⚠️ 헤더로 받는다는 사실 자체는
    **키 부재로 미검증**이다(프로브 ④가 판정한다).

    🔴 **키는 비어 있어도 된다**(키 없는 공개 한도로 돈다). 단 `dg.EnvVar`는 변수가
    *미설정*이면 run을 실패시키므로 `.env`에 `NVD_API_KEY=` 줄은 있어야 한다.

    ⚠️ **CISA KEV는 이 리소스를 거치지 않는다** — 다른 호스트로 NVD 키 헤더를 실어
    보내지 않기 위해서다(`defs/nvd/raw_assets.py`가 `fetch_bytes`로 직접 받는다).

    Attributes:
        api_key: NVD API 키. 빈 문자열이면 헤더를 보내지 않는다.
        base_url: API 루트(`…/rest/json`).
        timeout_s: HTTP 타임아웃(초).
        retries: 429·5xx 재시도 횟수. 지수 백오프(1+2+4+8+16=31초)가 롤링 30초 창
            하나를 넘기도록 5로 둔다.
    """

    api_key: str = ""
    base_url: str = "https://services.nvd.nist.gov/rest/json"
    timeout_s: int = 60
    retries: int = 5

    def min_interval_s(self) -> float:
        """키 유무에 따른 요청 간 최소 간격(초)."""
        return (
            NVD_MIN_INTERVAL_WITH_KEY_S if self.api_key else NVD_MIN_INTERVAL_NO_KEY_S
        )

    def fetch_page(self, path: str, params: dict[str, str]) -> bytes:
        """한 페이지를 받아 **원문 바이트**로 돌려준다(요청 전에 페이싱한다).

        Args:
            path: `/cves/2.0` 형태의 경로.
            params: 쿼리 파라미터. 🔴 **키를 여기 넣지 않는다** — 헤더로 간다.

        Returns:
            응답 본문 바이트.
        """
        time.sleep(self.min_interval_s())
        headers = {"apiKey": self.api_key} if self.api_key else None
        return fetch_bytes(
            f"{self.base_url}{path}",
            params,
            timeout_s=self.timeout_s,
            retries=self.retries,
            headers=headers,
        )

    def iter_window_pages(
        self,
        path: str,
        *,
        window_params: dict[str, str],
        items_key: str,
        page_size: int,
    ) -> Iterator[tuple[int, bytes, dict[str, Any]]]:
        """구간 조회를 `startIndex`로 끝까지 순회하며 페이지를 하나씩 내준다.

        🔴 **받은 합계 ≠ `totalResults`면 실패한다**(조용한 절단 금지 —
        fred_calendar의 `_assert_not_truncated`와 같은 축). 잘린 응답은 행이 생기고
        수만 모자라서 어디서도 에러가 나지 않는다. 가드는 마지막 페이지를 내준
        **뒤에** 걸리므로, 호출부는 제너레이터가 끝까지 돈 다음에만 커밋해야 한다
        (`land_window_pages`가 그렇게 한다).

        🔴 **순회 도중 `totalResults`가 바뀌면 실패한다.** 그 사이 구간 안의 CVE가
        다시 수정돼 밖으로 나가면 뒤 레코드가 앞으로 당겨져 `startIndex` 기준으로
        **한 건을 건너뛴다**. 합계 대조만으로는 이것을 못 잡을 수 있다
        (나간 만큼 total도 줄어든다).

        Args:
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
            body = self.fetch_page(path, params)
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


def assert_pool_limited(instance: dg.DagsterInstance, pool: str, limit: int) -> None:
    """Dagster pool의 한도가 정확히 `limit`인지 확인한다(아니면 API 호출 전에 실패).

    🔴 한도가 설정되지 않은 pool은 Dagster가 **무제한으로** 돌린다 — 에러도 경고도 없다.
    `pool=`을 선언해 둔 것만으로는 강제가 걸렸다고 읽을 수 없어, 실행 경로에서 한도를
    직접 읽는다(설정 방법은 `NVD_API_POOL` 주석).

    Args:
        instance: 실행 중인 Dagster 인스턴스.
        pool: pool 이름.
        limit: 요구하는 동시 실행 한도.

    Raises:
        RuntimeError: 저장소가 pool을 지원하지 않거나 한도가 `limit`이 아닐 때.
    """
    storage = instance.event_log_storage
    if not storage.supports_global_concurrency_limits:
        message = (
            f"pool `{pool}`: 이 인스턴스 저장소는 pool 한도를 지원하지 않는다 — "
            "동시성 테이블이 없을 수 있다(`dagster instance migrate`로 생성한다)"
        )
        raise RuntimeError(message)
    limits = {item.name: item.limit for item in storage.get_pool_limits()}
    if limits.get(pool) != limit:
        message = (
            f"pool `{pool}` 한도가 {limit}이 아니다(현재 {limits.get(pool)}) — "
            f"`dagster instance concurrency set {pool} {limit}` 후 실행한다."
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
        window_date: 파티션 키(`YYYY-MM-DD`).
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
    """파티션 하나의 raw 접두 URI(`…/<feed>/dt=YYYY-MM-DD`). 회차는 이 아래에 생긴다."""
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


def _list_keys(s3: S3Resource, bucket: str, prefix_key: str) -> list[str]:
    """접두 아래 모든 객체 키를 나열한다(페이지네이션 포함)."""
    keys: list[str] = []
    paginator = s3.get_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix_key):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def latest_committed_run(
    s3: S3Resource, prefix_uri: str, commit_file: str
) -> str | None:
    """접두 아래 커밋된 회차 중 **가장 최근** 회차의 키(접두)를 돌려준다.

    커밋 = `<회차>/<commit_file>` 의 사이드카가 있다. 접두는 dt 하나(일자 피드)일 수도,
    피드 루트(KEV — `dt=…/fetched=…`)일 수도 있다 — 둘 다 사전순 = 시간순이다.

    Args:
        s3: dagster-aws S3Resource.
        prefix_uri: 탐색 접두 URI.
        commit_file: 커밋 표지 파일명(`page-0000.json.gz`·`kev.json.gz`).

    Returns:
        회차 키(`…/fetched=…`, 버킷 제외). 커밋된 회차가 없으면 None.
    """
    bucket, prefix_key = parse_s3_uri(prefix_uri)
    tail = f"/{commit_file}{SIDECAR_SUFFIX}"
    runs = [
        key[: -len(tail)]
        for key in _list_keys(s3, bucket, f"{prefix_key}/")
        if key.endswith(tail)
        and key[: -len(tail)].rsplit("/", 1)[-1].startswith(FETCHED_PREFIX)
    ]
    return max(runs) if runs else None


def assert_new_run_prefix(s3: S3Resource, bucket: str, run_key: str) -> None:
    """새 회차 경로가 비어 있는지 확인한다(덮어쓰기 금지 — fail-closed).

    같은 초에 두 번 수집하면 회차 경로가 겹친다. 그때 덮어쓰면 커밋된 스냅샷이
    파괴되므로, 이어 쓰지 않고 실패시킨다(잠시 뒤 다시 돌리면 다른 회차가 된다).

    Raises:
        RuntimeError: 회차 경로에 객체가 이미 있을 때.
    """
    if _list_keys(s3, bucket, f"{run_key}/"):
        message = f"s3://{bucket}/{run_key}: 회차 경로가 이미 있다 — 덮어쓰지 않는다"
        raise RuntimeError(message)


def land_window_pages(
    context: AssetExecutionContext,
    *,
    s3: S3Resource,
    nvd: NvdResource,
    source_base: str,
    feed: str,
    path: str,
    start_param: str,
    end_param: str,
    items_key: str,
    item_key: str,
    id_field: str,
    page_size: int,
    fetched_at: datetime,
    force: bool,
) -> dg.MaterializeResult:
    """UTC 하루 구간을 끝까지 받아 **새 회차**로 S3 raw에 놓는다(raw 자산의 공통 본문).

    CVE·History 두 자산이 쓴다(2회). Rule of Three 이전이지만 추출한 이유는 이것이
    **읽는 쪽(`read_window_pages`)과 짝을 이루는 프로토콜**이라서다 — 커밋 순서가
    자산마다 따로 적히면 한쪽만 바뀌는 순간 bronze가 미완결 회차를 읽는다.

    🔴 **레코드 ID 중복은 커밋 전에 거른다**(KEV의 count 대조와 같은 원칙). 순회 중
    레코드가 이동하면 페이지 경계에서 같은 ID가 두 번 나오는데, 그 회차를 커밋하면
    비-force 실행이 영영 스킵하는 **깨진 최신 스냅샷**이 된다.

    Args:
        context: 에셋 실행 컨텍스트(파티션 키 = 구간 날짜).
        s3: dagster-aws S3Resource.
        nvd: NVD 접속 리소스.
        source_base: raw 루트 URI.
        feed: 피드 이름(S3 하위 경로).
        path: API 경로.
        start_param: 구간 시작 파라미터 이름.
        end_param: 구간 끝 파라미터 이름.
        items_key: 레코드 배열 키(`vulnerabilities`).
        item_key: 배열 원소 안 레코드 키(`cve`).
        id_field: 레코드 ID 필드(`id`·`cveChangeId`).
        page_size: 요청 페이지 크기.
        fetched_at: 이번 수집 회차의 시각(UTC) — 회차 경로가 된다.
        force: True면 커밋된 회차가 있어도 새 회차를 추가로 받는다.

    Returns:
        판정·수집 결과를 담은 MaterializeResult.

    Raises:
        RuntimeError: pool 한도 미설정·회차 경로 충돌·절단·ID 중복.
    """
    window_date = context.partition_key
    dt_uri = window_prefix(source_base, feed, window_date)
    latest = latest_committed_run(s3, dt_uri, WINDOW_COMMIT_FILE)
    if latest and not force:
        # 🔴 스킵도 판정 근거를 남긴다 — "검사하고 건너뛴 것"과 "아무것도 안 한 것"이
        #    같은 모양이 되지 않게(fetch.download_to_s3와 같은 규칙).
        context.log.info("%s: 커밋된 회차가 있다 — 재수집 생략", latest)
        return dg.MaterializeResult(
            metadata={
                "decision": "skipped_committed",
                "latest_run": latest,
                "api_requests": 0,
            }
        )

    assert_pool_limited(context.instance, NVD_API_POOL, NVD_API_POOL_LIMIT)
    bucket, dt_key = parse_s3_uri(dt_uri)
    run_key = f"{dt_key}/{run_segment(fetched_at)}"
    assert_new_run_prefix(s3, bucket, run_key)

    params = window_params(window_date, start_param=start_param, end_param=end_param)
    first_body: bytes | None = None
    total_results = 0
    pages = 0
    ids: list[str] = []
    gzip_bytes = 0
    for index, body, payload in nvd.iter_window_pages(
        path, window_params=params, items_key=items_key, page_size=page_size
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

    context.log.info(
        "%s: %d건 / %d페이지 수집·커밋 (totalResults=%d)",
        run_key,
        len(ids),
        pages,
        total_results,
    )
    return dg.MaterializeResult(
        metadata={
            "decision": "downloaded_new_run" if latest else "downloaded",
            "target": f"s3://{bucket}/{run_key}",
            # force로 새 회차를 더했으면 직전 최신 회차(지우지 않고 남아 있다).
            "previous_run": latest or "없음",
            "window_start": params[start_param],
            "window_end": params[end_param],
            "total_results": total_results,
            "rows": len(ids),
            "pages": pages,
            # 🔴 단위: **성공한 페이지 요청 수**다. `helper._request`의 429·5xx
            #    재시도는 여기 안 잡힌다(그 수는 관측 경로가 없다 — 미측정).
            "api_requests": pages,
            "page_size": page_size,
            "gzip_bytes": gzip_bytes,
            "commit_sha256": written["sha256"],
        }
    )


def read_window_pages(
    s3: S3Resource,
    *,
    source_base: str,
    feed: str,
    window_date: str,
    items_key: str,
) -> tuple[list[dict[str, Any]], datetime, str]:
    """dt의 **가장 최근 커밋된 회차**를 읽어 페이지 JSON 목록을 돌려준다.

    읽을 페이지 수는 **page-0000의 `totalResults`가 정한다** — 디렉터리를 나열해
    있는 만큼 읽지 않는다(회차 안에는 그 회차가 쓴 페이지만 있지만, 계약을 나열에
    기대지 않는다).

    Args:
        s3: dagster-aws S3Resource.
        source_base: raw 루트 URI.
        feed: 피드 이름.
        window_date: 파티션 키.
        items_key: 레코드 배열 키.

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
            "raw 자산을 먼저 머티리얼라이즈한다"
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
