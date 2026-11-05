"""공개 API GET 헬퍼(`lakehouse.http`).

`requests.get`을 대체해 네트워크에 붙지 않는다. 429 재시도는 Dagster
`test_iter_pages_retries_429_through_helper` 이식이다.
"""

from typing import Any

import pytest
import requests
from lakehouse.http import mask_url, raise_masked, request_bytes, request_json


class _Resp:
    def __init__(
        self, status: int, content: bytes = b"", headers: dict | None = None
    ) -> None:
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self.ok = status < 400

    def json(self) -> Any:
        return {"ok": True}

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr("lakehouse.http.time.sleep", recorded.append)
    return recorded


def _install(monkeypatch, responses: list[_Resp]) -> list[dict]:
    calls: list[dict] = []
    queue = list(responses)

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return queue.pop(0)

    monkeypatch.setattr("lakehouse.http.requests.get", fake_get)
    return calls


def test_request_bytes_returns_raw_content(monkeypatch) -> None:
    _install(monkeypatch, [_Resp(200, b"\x00raw\xff")])
    assert request_bytes("https://h/x", params={}) == b"\x00raw\xff"


def test_request_bytes_retries_429_with_retry_after(monkeypatch, sleeps) -> None:
    calls = _install(
        monkeypatch, [_Resp(429, b"", {"Retry-After": "7"}), _Resp(200, b"ok")]
    )
    assert request_bytes("https://h/x", params={}) == b"ok"
    assert len(calls) == 2
    assert 7.0 in sleeps


def test_request_json_unchanged(monkeypatch, sleeps) -> None:
    calls = _install(monkeypatch, [_Resp(503), _Resp(200)])
    result = request_json("https://h/x", params={"a": "1"}, headers={"k": "v"})
    assert result == {"ok": True}
    assert calls[0]["params"] == {"a": "1"}
    assert calls[0]["headers"] == {"k": "v"}
    assert sleeps == [1.0]


def test_mask_url_strips_query_and_userinfo() -> None:
    assert mask_url("https://u:p@h/x?apiKey=s") == "https://h/x"


def test_raise_masked_has_no_chain_and_no_query() -> None:
    secret_url = "https://h/x?apiKey=secret"  # noqa: S105 - 합성 값
    with pytest.raises(RuntimeError) as exc:
        raise_masked(secret_url, ValueError(secret_url))
    assert exc.value.__cause__ is None
    assert "secret" not in str(exc.value)
    assert "apiKey" not in str(exc.value)
