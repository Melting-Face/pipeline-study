"""Polygon(Massive) 시장 데이터 API 접속 리소스.

계약을 *"경로와 쿼리를 주면 JSON을 돌려준다"* 로 두어, 인증 방식이 바뀌어도
이 파일 안에서 끝나게 한다(`common/physionet.py`와 같은 판단).
"""

from typing import Any

import dagster as dg
from dagster_project.common.helper import fetch_json


class PolygonResource(dg.ConfigurableResource):
    """Polygon 시장 데이터 API 접속.

    🔴 **인증을 `Authorization: Bearer` 헤더로 한다.** `?apiKey=` 쿼리 파라미터도
    지원하지만 쓰지 않는다 — `helper._request`가 *"크리덴셜을 쿼리 파라미터로 받는
    API에는 이 함수를 그대로 쓰지 않는다"* 를 금지로 적어 뒀고(예외 메시지에
    전체 URL이 실려 키가 이벤트 로그에 박힌다), **헤더를 쓰면 그 축이 아예 사라진다.**
    그래서 이 리소스에는 마스킹 래퍼가 없다 — 없어도 되는 것이지 빠뜨린 것이 아니다.
    (FRED는 `?api_key=`뿐이라 같은 판단이 통하지 않는다 — 대비되는 축이다.)

    ⚠️ **무료 플랜은 분당 호출 수가 제한된다.** `helper._request`가 429를
    `Retry-After` 우선으로 재시도하므로 한도를 넘으면 느려지지만 죽지는 않는다.
    다만 파티션 백필은 **파티션당 요청 1건**이라 동시 실행 수가 곧 순간 요청 수다 —
    대량 백필 전에 `max_concurrent_runs`를 함께 본다(docs/resource-sizing.md).

    ⚠️ **조회 가능한 과거 범위가 롤링 윈도우다**(프로브 실측). 파티션 시작일이
    고정 리터럴이므로 시간이 지나면 **가장 이른 파티션이 권한 밖으로 밀려나** 403이
    된다 — 이미 적재된 것은 Iceberg에 남지만 **재적재는 불가능해진다.** 그래서
    초기 구간은 미루지 않고 받는다. 경계 재확인은
    `scripts/stock_source_access_probe.py --source prices`가 한다.

    Attributes:
        api_key: Polygon API 키.
        base_url: API 루트. 기본값은 공식 호스트.
        timeout_s: HTTP 타임아웃(초).
        retries: 429·5xx 재시도 횟수.
    """

    api_key: str
    # 🔴 호스트는 `api.polygon.io`를 유지한다. 웹 사이트(`polygon.io`)는
    # `massive.com`으로 리다이렉트되지만(실측) API 호스트는 그대로다.
    base_url: str = "https://api.polygon.io"
    timeout_s: int = 30
    retries: int = 3

    def fetch(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """경로와 쿼리로 JSON을 받는다.

        Args:
            path: `/v2/...` 형태의 절대 경로.
            params: 쿼리 파라미터. 🔴 **키를 여기 넣지 않는다** — 헤더로 간다.

        Returns:
            파싱된 응답 JSON.
        """
        return fetch_json(
            f"{self.base_url}{path}",
            params,
            timeout_s=self.timeout_s,
            retries=self.retries,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    def fetch_next(self, next_url: str) -> dict[str, Any]:
        """응답의 `next_url`로 다음 페이지를 받는다.

        🔴 **원천이 준 URL을 그대로 쓴다.** 커서가 그 안에 들어 있어 쿼리를
        재조립하면 페이지가 조용히 어긋난다(같은 페이지를 반복하거나 건너뛴다).

        🔴 **`base_url` 밖을 가리키면 거부한다.** 응답 본문이 정하는 주소로
        인증 헤더를 실어 보내는 경로이므로, 원천이 바뀌거나 중간에서 손대면
        **우리 키가 다른 호스트로 나간다.** 접두어 검사가 그 축을 닫는다.

        Args:
            next_url: 응답의 `next_url` 값(절대 URL).

        Returns:
            파싱된 다음 페이지 JSON.

        Raises:
            RuntimeError: `next_url`이 `base_url` 밖을 가리킬 때.
        """
        if not next_url.startswith(f"{self.base_url}/"):
            # 🔴 값을 그대로 찍지 않는다 — 커서에 무엇이 실렸는지 알 수 없다.
            message = f"next_url이 {self.base_url} 밖을 가리킨다"
            raise RuntimeError(message)
        return fetch_json(
            next_url,
            {},
            timeout_s=self.timeout_s,
            retries=self.retries,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
