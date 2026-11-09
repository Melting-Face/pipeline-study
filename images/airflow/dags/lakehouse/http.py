"""공개 API GET(429·5xx만 백오프 재시도) — Dagster `common/helper._request` 이식.

`mask_url`·`raise_masked`는 Dagster `common/fetch.py` 이식(로그·예외용 URL 마스킹).
`post_json`은 Discord 웹훅 발신용이다(`alerts_*` DAG만 쓴다 — docs/risk.md §4 예외 ②).
"""

import contextlib
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any, NoReturn
from urllib.parse import urlsplit, urlunsplit

import requests


def _get(
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str] | None,
    timeout_s: int,
    retries: int,
) -> requests.Response:
    """GET 후 성공 응답을 반환한다. 429·5xx만 재시도하고 그 외 4xx는 즉시 실패한다.

    ⚠️ 실패 예외에는 쿼리스트링을 포함한 URL이 담긴다 — 크리덴셜은 **헤더로만** 보낸다
    (Polygon `Authorization: Bearer`). `Retry-After`가 오면 계산된 백오프보다 우선한다.
    """
    for attempt in range(retries + 1):
        response = requests.get(url, params=params, headers=headers, timeout=timeout_s)
        if response.ok:
            return response
        retryable = response.status_code == 429 or response.status_code >= 500
        if not retryable or attempt == retries:
            response.raise_for_status()
            # 3xx처럼 raise_for_status가 침묵하는 경우까지 닫는다(fail-closed)
            message = f"예상 밖 응답 {response.status_code}: {url}"
            raise RuntimeError(message)
        time.sleep(float(response.headers.get("Retry-After") or 2**attempt))
    message = f"재시도 소진: {url}"  # 도달 불가 — 타입 체커용
    raise RuntimeError(message)


def request_json(
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str] | None = None,
    timeout_s: int = 30,
    retries: int = 3,
) -> dict[str, Any]:
    """GET 후 JSON을 반환한다(재시도·실패 규칙은 `_get`)."""
    return _get(
        url, params=params, headers=headers, timeout_s=timeout_s, retries=retries
    ).json()


def request_bytes(
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str] | None = None,
    timeout_s: int = 30,
    retries: int = 3,
) -> bytes:
    """GET 후 응답 본문 바이트를 그대로 반환한다(raw 스냅샷 보존용)."""
    return _get(
        url, params=params, headers=headers, timeout_s=timeout_s, retries=retries
    ).content


# 429 응답이 요구하는 대기의 상한(초). 넘으면 기다리지 않고 실패한다.
# 짧게 끊어 다시 치면 다시 429를 받고, 반복하면 Discord가 일시 제한을 건다.
MAX_RETRY_AFTER_S = 60.0
# 재시도하지 않는 상태 — 반복하면 Discord가 무효 요청으로 세어 일시 제한을 건다
NO_RETRY_STATUS = frozenset({401, 403, 404})
SUCCESS_STATUS = 200
REJECTED_STATUS = 400


class PayloadRejectedError(RuntimeError):
    """수신 측이 payload를 400으로 거절했다(발신되지 않았다). 메시지는 마스킹 URL만."""


@contextlib.contextmanager
def _quiet_urllib3() -> Iterator[None]:
    """urllib3 연결 풀의 DEBUG 요청 줄(경로 포함)을 잠시 끈다.

    🔴 urllib3는 DEBUG에서 `"POST <경로>?<쿼리> HTTP/1.1" 200`을 남긴다.
    웹훅 토큰은 경로에 있으므로 로그 수준이 DEBUG면 그대로 샌다.
    호출 동안만 WARNING으로 올리고 되돌린다.
    """
    logger = logging.getLogger("urllib3.connectionpool")
    previous = logger.level
    logger.setLevel(max(previous, logging.WARNING))
    try:
        yield
    finally:
        logger.setLevel(previous)


def _with_wait(url: str) -> str:
    """`?wait=true`를 붙인다(성공을 메시지 생성 확인으로 받는다)."""
    parts = urlsplit(url)
    query = f"{parts.query}&wait=true" if parts.query else "wait=true"
    return urlunsplit(parts._replace(query=query))


def _retry_after(response: requests.Response) -> float | None:
    """429의 대기 초 — 본문 `retry_after`(float 초) 우선, 없으면 헤더."""
    with contextlib.suppress(ValueError, TypeError, AttributeError):
        body = response.json()
        if isinstance(body, dict) and body.get("retry_after") is not None:
            return float(body["retry_after"])
    with contextlib.suppress(ValueError, TypeError):
        header = response.headers.get("Retry-After")
        if header is not None:
            return float(header)
    return None


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    mask: Callable[[str], str],
    timeout_s: int = 10,
    retries: int = 3,
) -> int:
    """JSON POST(`?wait=true`). 반환: 200(응답 본문은 돌려주지 않는다).

    - 성공은 **200만**이다(`?wait=true`는 생성된 메시지와 200을 돌려준다).
      그 밖의 2xx(예: wait가 무시된 204)도 실패로 본다 — 생성 확인이 없다.
    - 리다이렉트를 따라가지 않는다(`allow_redirects=False`). 3xx는 재시도 없이
      실패한다 — 토큰이 든 요청을 다른 곳으로 다시 보내지 않는다.
    - 400: 재시도 없이 `PayloadRejectedError`(payload가 거절됐다 — 발신 안 됨).
    - 429: 본문 `retry_after`·`Retry-After`만큼 기다렸다 재시도한다.
      대기가 `MAX_RETRY_AFTER_S`를 넘거나 값이 없으면 기다리지 않고 실패한다.
    - 5xx: 지수 백오프(1·2·4…초)로 재시도한다.
    - 401·403·404: **재시도 없이 즉시 실패**한다. 그 밖의 비성공도 즉시 실패한다.
    - 요청 예외(ConnectionError·Timeout 등): 재시도하지 않는다. 요청이 이미
      닿았는지 알 수 없어 재시도가 중복 발신이 될 수 있다.

    🔴 실패 예외에는 `mask(url)`과 상태 코드·예외 **타입 이름**만 싣는다.
    requests 예외 문자열에는 전체 URL(토큰 포함)이 들어 있어 넣지 않는다.
    예외는 except 블록 **밖에서** `from None`으로 올린다. 블록 안에서 올리면
    `__cause__`는 끊겨도 `__context__`에 원 예외가 남는다.
    """
    target = _with_wait(url)
    failure = ""
    for attempt in range(retries + 1):
        failure = ""
        try:
            with _quiet_urllib3():
                response = requests.post(
                    target, json=payload, timeout=timeout_s, allow_redirects=False
                )
        except requests.RequestException as exc:
            failure = type(exc).__name__
        if failure:
            break
        status = response.status_code
        if status == SUCCESS_STATUS:
            return status
        failure = f"HTTP {status}"
        if status == REJECTED_STATUS:
            message = f"{mask(url)} POST 거절: {failure}"
            raise PayloadRejectedError(message) from None
        wait = _post_retry_wait(response, attempt)
        if wait is None or attempt == retries:
            break
        time.sleep(wait)
    message = f"{mask(url)} POST 실패: {failure}"
    raise RuntimeError(message) from None


def _post_retry_wait(response: requests.Response, attempt: int) -> float | None:
    """비성공 응답의 재시도 대기(초). 재시도하지 않으면 None."""
    status = response.status_code
    if status in NO_RETRY_STATUS:
        return None
    if status == 429:
        wait = _retry_after(response)
        if wait is None or wait > MAX_RETRY_AFTER_S:
            return None
        return max(0.0, wait)
    if status >= 500:
        return float(2**attempt)
    return None


def mask_url(url: str) -> str:
    """URL에서 자격증명과 쿼리스트링을 제거한다(로그·예외 메시지용).

    스킴·호스트·경로만 남겨 어느 요청이 실패했는지는 알아볼 수 있게 한다.
    """
    without_query = url.split("?", 1)[0]
    scheme, _, rest = without_query.partition("://")
    if not rest:
        return without_query
    _, _, host_and_path = rest.rpartition("@")  # user:pass@host 제거
    return f"{scheme}://{host_and_path}"


def raise_masked(url: str, exc: Exception) -> NoReturn:
    """외부 호출 예외를 자격증명 없는 메시지로 재포장해 올린다.

    🔴 **원 예외를 `from exc`로 매달지 않는다.** 체인을 남기면 원 메시지(전체
    URL 포함)가 트레이스백에 그대로 출력돼 마스킹이 무의미해진다. 잃는 것은
    스택 맥락이고 지키는 것은 크리덴셜이라 후자를 택한다.
    """
    message = f"{mask_url(url)} 요청 실패: {type(exc).__name__}"
    raise RuntimeError(message) from None
