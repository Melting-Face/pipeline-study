"""FRED(ALFRED) 접속 리소스.

계약을 *"경로와 쿼리를 주면 JSON을 돌려준다"* 로 두어, 인증 방식이 바뀌어도
이 파일 안에서 끝나게 한다(`common/physionet.py`·`common/polygon.py`와 같은 판단).
"""

from typing import Any

import dagster as dg
from dagster_project.common.fetch import raise_masked
from dagster_project.common.helper import fetch_json

FRED_BASE = "https://api.stlouisfed.org/fred"


class FredResource(dg.ConfigurableResource):
    """FRED/ALFRED 접속.

    🔴 **키를 쿼리 파라미터로 보낼 수밖에 없다.** FRED는 `?api_key=`만 지원하고
    헤더 인증이 없다. `helper._request`가 *"크리덴셜을 쿼리 파라미터로 받는 API에는
    이 함수를 그대로 쓰지 않는다"* 를 금지로 적어 둔 바로 그 경우다 — `requests`의
    예외 메시지에 **쿼리스트링을 포함한 전체 URL**이 담겨, 4xx 한 번에 키가 Dagster
    이벤트 로그에 평문으로 박힌다.

    그래서 이 리소스가 예외를 재포장한다. `raise_masked`는 쿼리스트링을 통째로
    벗기고 **`from None`으로 체인을 끊는다** — `from exc`를 쓰면 원 URL이 체인에
    남아 마스킹이 무의미해진다.

    ⚠️ `PolygonResource`에는 이 래퍼가 없는데, 누락이 아니라 **필요가 없어서**다
    (그쪽은 `Authorization: Bearer` 헤더라 축이 닫혀 있다). 두 파일을 나란히 두면
    "왜 한쪽만 감쌌나"가 보이도록 양쪽에 근거를 적었다.

    Attributes:
        api_key: FRED API 키.
        base_url: API 루트. 기본값은 공식 경로.
        timeout_s: HTTP 타임아웃(초).
        retries: 429·5xx 재시도 횟수.
    """

    api_key: str
    base_url: str = FRED_BASE
    timeout_s: int = 30
    retries: int = 3

    def fetch(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """경로와 쿼리로 JSON을 받는다.

        Args:
            path: `releases/dates` 같은 상대 경로(앞 슬래시 없음).
            params: 쿼리 파라미터. `api_key`·`file_type`은 여기서 붙인다.

        Returns:
            파싱된 응답 JSON.

        Raises:
            RuntimeError: 요청이 실패했을 때(URL은 마스킹된다).
        """
        url = f"{self.base_url}/{path}"
        merged = {**params, "api_key": self.api_key, "file_type": "json"}
        try:
            return fetch_json(
                url, merged, timeout_s=self.timeout_s, retries=self.retries
            )
        except Exception as exc:
            # 넓게 잡는다 — requests 예외든 RuntimeError든 **URL이 새는 것**이
            # 문제이므로 종류를 가리지 않고 전부 마스킹해 다시 올린다.
            raise_masked(url, exc)
