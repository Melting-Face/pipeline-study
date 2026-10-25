"""공개 API GET(429·5xx만 백오프 재시도) — Dagster `common/helper._request` 이식."""

import time
from typing import Any

import requests


def request_json(
    url: str,
    *,
    params: dict[str, str],
    headers: dict[str, str] | None = None,
    timeout_s: int = 30,
    retries: int = 3,
) -> dict[str, Any]:
    """GET 후 JSON을 반환한다. 429·5xx만 재시도하고 그 외 4xx는 즉시 실패한다.

    ⚠️ 실패 예외에는 쿼리스트링을 포함한 URL이 담긴다 — 크리덴셜은 **헤더로만** 보낸다
    (Polygon `Authorization: Bearer`). `Retry-After`가 오면 계산된 백오프보다 우선한다.
    """
    for attempt in range(retries + 1):
        response = requests.get(url, params=params, headers=headers, timeout=timeout_s)
        if response.ok:
            return response.json()
        retryable = response.status_code == 429 or response.status_code >= 500
        if not retryable or attempt == retries:
            response.raise_for_status()
            # 3xx처럼 raise_for_status가 침묵하는 경우까지 닫는다(fail-closed)
            message = f"예상 밖 응답 {response.status_code}: {url}"
            raise RuntimeError(message)
        time.sleep(float(response.headers.get("Retry-After") or 2**attempt))
    message = f"재시도 소진: {url}"  # 도달 불가 — 타입 체커용
    raise RuntimeError(message)
