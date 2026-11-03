"""공개 API GET(429·5xx만 백오프 재시도) — Dagster `common/helper._request` 이식.

`mask_url`·`raise_masked`는 Dagster `common/fetch.py` 이식(로그·예외용 URL 마스킹).
"""

import time
from typing import Any, NoReturn

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
