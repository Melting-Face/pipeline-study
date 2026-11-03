"""NVD 페이지 순회·raw 회차 프로토콜·KEV 착지 단위 테스트 — `lakehouse/nvd.py`.

Dagster `tests/test_nvd_parse.py` §3(페이지 순회)·§4(raw 회차)·§5(KEV)의 이식이다.
**실인프라에 붙지 않는다** — HTTP는 `requests.get` 대역, S3는 boto3 클라이언트 대역이
쓰기·삭제 **순서**를 기록한다. 픽스처 값은 전부 **합성**이다.

이식하지 않는 Dagster 테스트: `test_land_requires_pool_limit_before_calling_api`(런타임
pool 한도 검사 — Airflow Pool은 차트가 선언), `test_nvd_raw_assets_declare_the_pool`
(dag-import pool 셀로 대체), `test_land_kev_skips_same_day_without_force`·
`test_land_kev_force_adds_run_on_same_day`(Q14로 의미 변경 → 아래 신규 3개로 대체).
"""

import gzip
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

import pytest
import requests
from botocore.exceptions import ClientError
from lakehouse.nvd import (
    NVD_MIN_INTERVAL_NO_KEY_S,
    NVD_MIN_INTERVAL_WITH_KEY_S,
    SOURCE_BASE,
    iter_window_pages,
    land_kev,
    land_window_pages,
    latest_committed_run,
    page_key,
    read_latest_kev,
    read_window_pages,
    run_segment,
    window_params,
)
from lakehouse.nvd_parse import cves_to_arrow
from lakehouse.s3raw import SIDECAR_SUFFIX

BUCKET = "warehouse"
WINDOW = "2026-10-30"
FETCHED_1 = datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)
FETCHED_2 = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
# FETCHED_1과 같은 UTC 날(dt=2026-10-31)의 나중 시각.
FETCHED_1_LATER = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
DT_KEY = f"raw/nvd/cves/dt={WINDOW}"
RUN_1 = f"{DT_KEY}/{run_segment(FETCHED_1)}"
RUN_2 = f"{DT_KEY}/{run_segment(FETCHED_2)}"
SECRET = "SECRET-KEY-123"  # noqa: S105 - 유출 검사용 합성 값


# ── 합성 픽스처 ─────────────────────────────────────────────────────────


def _cve(cve_id: str) -> dict[str, Any]:
    """`cves_to_arrow` 왕복에 필요한 최소 합성 CVE(값은 지어냈다)."""
    return {
        "id": cve_id,
        "lastModified": f"{WINDOW}T01:55:06.290",
        "published": "2026-10-01T00:00:00.000",
        "vulnStatus": "Analyzed",
    }


def _kev_entry(cve_id: str) -> dict[str, Any]:
    return {
        "cveID": cve_id,
        "dateAdded": "2026-10-01",
        "dueDate": "2026-10-22",
        "product": "Widget",
        "requiredAction": "Apply updates.",
        "shortDescription": "합성 설명",
        "vendorProject": "Example",
        "vulnerabilityName": "Example Widget Flaw",
    }


def _kev_payload(entries: list[dict[str, Any]], count: int | None = None) -> dict:
    return {
        "title": "합성 KEV",
        "catalogVersion": "2026.10.30",
        "dateReleased": "2026-10-30T17:59:11.4512Z",
        "count": len(entries) if count is None else count,
        "vulnerabilities": entries,
    }


def _cve_page(cves: list[dict], total: int, start: int = 0) -> dict[str, Any]:
    return {
        "resultsPerPage": len(cves),
        "startIndex": start,
        "totalResults": total,
        "format": "NVD_CVE",
        "version": "2.0",
        "timestamp": "2026-10-31T04:00:00.000",
        "vulnerabilities": [{"cve": cve} for cve in cves],
    }


# ── 3. 페이지 순회 (페이크 HTTP) ────────────────────────────────────────


class _FakeHttpResponse:
    def __init__(self, status: int, body: bytes, headers: dict | None = None) -> None:
        self.status_code = status
        self.ok = 200 <= status < 300
        self.content = body
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(str(self.status_code))


class _FakeHttp:
    """`requests.get` 대역. 응답을 순서대로 내주고 호출을 기록한다."""

    def __init__(self, responses: list[_FakeHttpResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "params": dict(params or {}), "headers": headers}
        )
        return self._responses.pop(0)


def _ok(payload: dict) -> _FakeHttpResponse:
    return _FakeHttpResponse(200, json.dumps(payload).encode("utf-8"))


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """페이싱·백오프 대기를 기록만 하고 실제로 자지 않는다."""
    recorded: list[float] = []
    monkeypatch.setattr("lakehouse.nvd.time.sleep", recorded.append)
    monkeypatch.setattr("lakehouse.http.time.sleep", recorded.append)
    return recorded


def _install_http(monkeypatch, responses: list[_FakeHttpResponse]) -> _FakeHttp:
    fake = _FakeHttp(responses)
    monkeypatch.setattr("lakehouse.http.requests.get", fake)
    return fake


WINDOW_PARAMS = window_params(
    WINDOW, start_param="lastModStartDate", end_param="lastModEndDate"
)


def test_window_params_cover_the_utc_day_without_overlap() -> None:
    assert {
        "lastModStartDate": f"{WINDOW}T00:00:00.000Z",
        "lastModEndDate": f"{WINDOW}T23:59:59.999Z",
    } == WINDOW_PARAMS


def _iter(api_key: str, page_size: int) -> list:
    return list(
        iter_window_pages(
            api_key,
            "/cves/2.0",
            window_params=WINDOW_PARAMS,
            items_key="vulnerabilities",
            page_size=page_size,
        )
    )


def test_iter_pages_advances_start_index_and_sends_header(monkeypatch, sleeps) -> None:
    http = _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("CVE-1"), _cve("CVE-2")], 3)),
            _ok(_cve_page([_cve("CVE-3")], 3, start=2)),
        ],
    )
    pages = _iter("k", page_size=2)

    assert [index for index, _, _ in pages] == [0, 1]
    assert [call["params"]["startIndex"] for call in http.calls] == ["0", "2"]
    assert all(call["params"]["resultsPerPage"] == "2" for call in http.calls)
    # 🔴 구간 시작·끝은 **매 요청 짝으로** 나간다(한쪽만이면 404).
    assert all(
        call["params"]["lastModStartDate"] == f"{WINDOW}T00:00:00.000Z"
        and call["params"]["lastModEndDate"] == f"{WINDOW}T23:59:59.999Z"
        for call in http.calls
    )
    assert all(call["headers"] == {"apiKey": "k"} for call in http.calls)
    # 🔴 키는 쿼리로 새지 않는다.
    assert all("apiKey" not in call["params"] for call in http.calls)
    assert sleeps == [NVD_MIN_INTERVAL_WITH_KEY_S] * 2
    assert sleeps == [0.7, 0.7]


def test_iter_pages_without_key_sends_no_header_and_paces_slower(
    monkeypatch, sleeps
) -> None:
    http = _install_http(monkeypatch, [_ok(_cve_page([], 0))])
    pages = _iter("", page_size=2000)

    assert len(pages) == 1  # 0건 구간도 1페이지(커밋 표지가 생긴다)
    assert http.calls[0]["headers"] is None
    assert sleeps == [NVD_MIN_INTERVAL_NO_KEY_S]
    assert sleeps == [6.5]


def test_iter_pages_fails_on_short_page_instead_of_truncating(
    monkeypatch, sleeps
) -> None:
    """🔴 원천이 신고한 수보다 적게 오면 **실패**다 — 조용히 잘라 담지 않는다."""
    _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("CVE-1"), _cve("CVE-2")], 3)),
            _ok(_cve_page([], 3, start=2)),
        ],
    )
    with pytest.raises(RuntimeError, match="빈 페이지"):
        _iter("", page_size=2)


def test_iter_pages_fails_when_total_moves_mid_scan(monkeypatch, sleeps) -> None:
    """🔴 순회 중 total이 바뀌면 `startIndex`가 한 건을 건너뛸 수 있다 — 실패시킨다."""
    _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("CVE-1"), _cve("CVE-2")], 4)),
            _ok(_cve_page([_cve("CVE-4")], 3, start=2)),
        ],
    )
    with pytest.raises(RuntimeError, match="totalResults"):
        _iter("", page_size=2)


def test_iter_pages_retries_429_through_helper(monkeypatch, sleeps) -> None:
    """429는 `http._get`이 Retry-After를 따라 재시도한다(재사용 확인)."""
    http = _install_http(
        monkeypatch,
        [
            _FakeHttpResponse(429, b"", {"Retry-After": "7"}),
            _ok(_cve_page([_cve("CVE-1")], 1)),
        ],
    )
    pages = _iter("k", page_size=2000)
    assert len(pages) == 1
    assert len(http.calls) == 2
    assert 7.0 in sleeps


# ── 4. raw 회차 프로토콜 (페이크 S3) ─────────────────────────────────────


class _FakePaginator:
    def __init__(self, s3: "_FakeS3") -> None:
        self._s3 = s3

    def paginate(self, Bucket: str, Prefix: str):  # noqa: N803
        keys = sorted(k for k in self._s3.objects if k.startswith(Prefix))
        # 페이지 경계를 일부러 둘로 쪼갠다 — 페이지네이션을 합치는지 본다
        half = len(keys) // 2
        yield {"Contents": [{"Key": k} for k in keys[:half]]}
        yield {"Contents": [{"Key": k} for k in keys[half:]]}


class _FakeS3:
    """boto3 S3 **클라이언트** 대역 — 쓰기·삭제 **순서**를 기록한다."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.ops: list[tuple[str, str]] = []

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.objects[Key] = Body
        self.ops.append(("put", Key))

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        self.objects.pop(Key, None)
        self.ops.append(("delete", Key))

    def get_paginator(self, name: str) -> _FakePaginator:
        assert name == "list_objects_v2"
        return _FakePaginator(self)


def _overwrites(s3: _FakeS3) -> list[str]:
    """두 번 이상 쓰였거나 지워진 키 — 「덮어쓰지 않는다」의 관측 셀."""
    puts = [key for op, key in s3.ops if op == "put"]
    deletes = [key for op, key in s3.ops if op == "delete"]
    return sorted({key for key in puts if puts.count(key) > 1} | set(deletes))


def _land(
    s3: _FakeS3,
    *,
    fetched_at: datetime = FETCHED_1,
    force: bool = False,
    page_size: int = 2,
    api_key: str = "k",
) -> dict[str, Any]:
    return land_window_pages(
        s3,
        api_key=api_key,
        feed="cves",
        path="/cves/2.0",
        start_param="lastModStartDate",
        end_param="lastModEndDate",
        items_key="vulnerabilities",
        item_key="cve",
        id_field="id",
        page_size=page_size,
        window_date=WINDOW,
        fetched_at=fetched_at,
        force=force,
    )


def _read(s3: _FakeS3) -> tuple[list[dict], datetime, str]:
    return read_window_pages(
        s3, feed="cves", window_date=WINDOW, items_key="vulnerabilities"
    )


def _two_pages(prefix: str = "CVE") -> list[_FakeHttpResponse]:
    return [
        _ok(_cve_page([_cve(f"{prefix}-1"), _cve(f"{prefix}-2")], 3)),
        _ok(_cve_page([_cve(f"{prefix}-3")], 3, start=2)),
    ]


def test_run_segment_roundtrips_to_utc() -> None:
    assert run_segment(FETCHED_1) == "fetched=20261031T040000Z"


def test_land_writes_data_before_sidecar_and_commits_page_zero_last(
    monkeypatch, sleeps
) -> None:
    """🔴 데이터 → 사이드카, 그리고 **page-0000(커밋 표지)이 맨 마지막**이다."""
    _install_http(monkeypatch, _two_pages())
    s3 = _FakeS3()
    result = _land(s3)

    p0, p1 = page_key(RUN_1, 0), page_key(RUN_1, 1)
    assert s3.ops == [
        ("put", p1),
        ("put", p1 + SIDECAR_SUFFIX),
        ("put", p0),
        ("put", p0 + SIDECAR_SUFFIX),
    ]
    assert result["rows"] == 3
    assert result["pages"] == 2
    assert result["total_results"] == 3
    # 원문 바이트 그대로 gzip — 풀면 받은 응답과 같다.
    assert gzip.decompress(s3.objects[p1]) == _two_pages()[1].content


def test_land_skips_committed_dt_without_calling_api(monkeypatch, sleeps) -> None:
    """raw는 수집 시점 스냅샷이다 — 커밋된 회차가 있으면 API를 부르지 않는다."""
    s3 = _FakeS3()
    _install_http(monkeypatch, _two_pages())
    _land(s3)
    http = _install_http(monkeypatch, _two_pages())
    ops_before = list(s3.ops)

    result = _land(s3, fetched_at=FETCHED_2)

    assert result["decision"] == "skipped_committed"
    assert http.calls == []
    assert s3.ops == ops_before


def test_force_adds_new_run_and_keeps_old_run(monkeypatch, sleeps) -> None:
    """🔴 force는 **새 회차를 더할 뿐** 옛 회차 객체를 지우거나 덮어쓰지 않는다."""
    s3 = _FakeS3()
    _install_http(monkeypatch, _two_pages("OLD"))
    _land(s3)
    old_objects = {k: v for k, v in s3.objects.items() if k.startswith(RUN_1)}
    _install_http(monkeypatch, _two_pages("NEW"))

    result = _land(s3, fetched_at=FETCHED_2, force=True)

    assert result["decision"] == "downloaded_new_run"
    assert result["previous_run"] == RUN_1
    # 옛 회차는 바이트까지 그대로다.
    assert {k: s3.objects[k] for k in old_objects} == old_objects
    assert _overwrites(s3) == []
    # bronze는 가장 최근 커밋 회차를 읽는다.
    pages, fetched_at, run_uri = _read(s3)
    assert fetched_at == FETCHED_2
    assert run_uri.endswith(RUN_2)
    assert pages[0]["vulnerabilities"][0]["cve"]["id"] == "NEW-1"


def test_force_failure_keeps_old_run_as_latest(monkeypatch, sleeps) -> None:
    """🔴 force가 도중에 실패하면 새 회차는 미커밋이고 **옛 회차가 그대로 최신**이다."""
    s3 = _FakeS3()
    _install_http(monkeypatch, _two_pages("OLD"))
    _land(s3)
    _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("NEW-1"), _cve("NEW-2")], 5)),
            _ok(_cve_page([_cve("NEW-3")], 5, start=2)),
            _ok(_cve_page([], 5, start=3)),
        ],
    )
    with pytest.raises(RuntimeError, match="빈 페이지"):
        _land(s3, fetched_at=FETCHED_2, force=True)

    assert page_key(RUN_2, 0) + SIDECAR_SUFFIX not in s3.objects
    pages, fetched_at, _ = _read(s3)
    assert fetched_at == FETCHED_1
    assert pages[0]["vulnerabilities"][0]["cve"]["id"] == "OLD-1"
    assert _overwrites(s3) == []


def test_read_ignores_uncommitted_newer_run(monkeypatch, sleeps) -> None:
    """미커밋 회차(표지 없음)는 더 최근이어도 읽지 않는다."""
    s3 = _FakeS3()
    _install_http(monkeypatch, _two_pages("OLD"))
    _land(s3)
    newer = json.dumps(_cve_page([_cve("HALF-1")], 1)).encode()
    s3.objects[page_key(RUN_2, 0)] = gzip.compress(newer)  # 사이드카 없음

    _, fetched_at, _ = _read(s3)
    assert fetched_at == FETCHED_1


def test_land_leaves_no_commit_when_scan_fails(monkeypatch, sleeps) -> None:
    """순회가 절단으로 실패하면 표지가 **없다**(미완결이 드러난다)."""
    _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("CVE-1"), _cve("CVE-2")], 5)),
            _ok(_cve_page([_cve("CVE-3")], 5, start=2)),
            _ok(_cve_page([], 5, start=3)),
        ],
    )
    s3 = _FakeS3()
    with pytest.raises(RuntimeError, match="빈 페이지"):
        _land(s3)
    assert page_key(RUN_1, 0) + SIDECAR_SUFFIX not in s3.objects

    with pytest.raises(RuntimeError, match="raw 미완결"):
        _read(s3)


def test_land_rejects_duplicate_ids_before_commit(monkeypatch, sleeps) -> None:
    """🔴 페이지 경계에서 같은 ID가 두 번이면 **표지를 쓰기 전에** 실패한다."""
    _install_http(
        monkeypatch,
        [
            _ok(_cve_page([_cve("CVE-1"), _cve("CVE-2")], 3)),
            _ok(_cve_page([_cve("CVE-2")], 3, start=2)),
        ],
    )
    s3 = _FakeS3()
    with pytest.raises(RuntimeError, match="두 번"):
        _land(s3)
    assert page_key(RUN_1, 0) + SIDECAR_SUFFIX not in s3.objects
    dt_uri = f"{SOURCE_BASE}/cves/dt={WINDOW}"
    assert latest_committed_run(s3, dt_uri, "page-0000.json.gz") is None


def test_land_refuses_to_reuse_run_path(monkeypatch, sleeps) -> None:
    """같은 회차 시각으로 다시 쓰려 하면 덮어쓰지 않고 실패한다."""
    s3 = _FakeS3()
    _install_http(monkeypatch, _two_pages())
    _land(s3)
    snapshot = dict(s3.objects)
    _install_http(monkeypatch, _two_pages("NEW"))

    with pytest.raises(RuntimeError, match="이미 있다"):
        _land(s3, force=True)
    assert s3.objects == snapshot


def test_read_window_pages_roundtrip(monkeypatch, sleeps) -> None:
    _install_http(monkeypatch, _two_pages())
    s3 = _FakeS3()
    _land(s3)

    pages, fetched_at, _ = _read(s3)
    arrow = cves_to_arrow(pages, WINDOW, fetched_at)
    assert arrow.column("cve_id").to_pylist() == ["CVE-1", "CVE-2", "CVE-3"]
    assert set(arrow.column("fetched_at").to_pylist()) == {FETCHED_1}


def test_read_window_pages_rejects_tampered_object(monkeypatch, sleeps) -> None:
    _install_http(monkeypatch, _two_pages())
    s3 = _FakeS3()
    _land(s3)
    s3.objects[page_key(RUN_1, 1)] = gzip.compress(b'{"totalResults": 3}')

    with pytest.raises(RuntimeError, match="무결성"):
        _read(s3)


# ── Q21. 키 유출 ─────────────────────────────────────────────────────────


def test_land_result_has_no_api_key(monkeypatch, sleeps) -> None:
    """반환 dict는 XCom·Asset event로 흘러간다 — 키가 없어야 한다."""
    _install_http(monkeypatch, _two_pages())
    result = _land(_FakeS3(), api_key=SECRET)
    assert SECRET not in json.dumps(result)


def test_land_logs_have_no_api_key(monkeypatch, sleeps, caplog) -> None:
    http = _install_http(monkeypatch, _two_pages())
    with caplog.at_level(logging.DEBUG):
        _land(_FakeS3(), api_key=SECRET)
    # 대조군 — 키가 실제로 헤더로 나갔고(경로 생존), 로그는 비어 있지 않다.
    assert all(call["headers"] == {"apiKey": SECRET} for call in http.calls)
    assert caplog.records
    assert SECRET not in caplog.text


def test_fetch_error_message_is_masked_and_keyless(monkeypatch, sleeps) -> None:
    """`requests` 예외 메시지에 쿼리 포함 URL이 실려도 새는 것이 없다."""

    def boom(url, **kwargs):
        message = f"connection refused: {url}?apiKey={SECRET}&startIndex=0"
        raise requests.ConnectionError(message)

    monkeypatch.setattr("lakehouse.http.requests.get", boom)
    with pytest.raises(RuntimeError) as info:
        _land(_FakeS3(), api_key=SECRET)
    assert "?" not in str(info.value)
    assert SECRET not in str(info.value)
    assert info.value.__cause__ is None


# ── 5. KEV ───────────────────────────────────────────────────────────────

KEV_ROOT = "raw/nvd/kev"
KEV_ROOT_URI = f"s3://{BUCKET}/{KEV_ROOT}"


def _kev(
    monkeypatch, s3: _FakeS3, payload: dict, *, fetched_at=FETCHED_1, force=False
) -> tuple[_FakeHttp, dict[str, Any]]:
    http = _install_http(monkeypatch, [_ok(payload)])
    return http, land_kev(s3, fetched_at=fetched_at, force=force)


def test_land_kev_sends_no_nvd_key_header(monkeypatch, sleeps) -> None:
    """🔴 KEV 요청에는 헤더가 없다 — NVD `apiKey`가 cisa.gov로 나가지 않는다."""
    s3 = _FakeS3()
    http, _ = _kev(monkeypatch, s3, _kev_payload([_kev_entry("CVE-2026-0001")]))
    assert len(http.calls) == 1
    assert http.calls[0]["headers"] is None
    assert "cisa.gov" in http.calls[0]["url"]
    run_key = f"{KEV_ROOT}/dt=2026-10-31/{run_segment(FETCHED_1)}"
    assert latest_committed_run(s3, KEV_ROOT_URI, "kev.json.gz") == run_key


def test_land_kev_rejects_count_mismatch_before_landing(monkeypatch, sleeps) -> None:
    s3 = _FakeS3()
    with pytest.raises(RuntimeError, match="잘린"):
        _kev(monkeypatch, s3, _kev_payload([_kev_entry("CVE-2026-0001")], count=2))
    assert s3.ops == []


def test_land_kev_rejects_duplicate_before_landing(monkeypatch, sleeps) -> None:
    s3 = _FakeS3()
    entries = [_kev_entry("CVE-2026-0001"), _kev_entry("CVE-2026-0001")]
    with pytest.raises(RuntimeError, match="중복"):
        _kev(monkeypatch, s3, _kev_payload(entries))
    assert s3.ops == []


def test_land_kev_skips_unchanged_content(monkeypatch, sleeps) -> None:
    """🔴 매시간 수집 — 항상 GET하되 내용이 같으면 쓰지 않는다."""
    s3 = _FakeS3()
    payload = _kev_payload([_kev_entry("A")])
    _kev(monkeypatch, s3, payload)
    ops_before = list(s3.ops)

    http, result = _kev(monkeypatch, s3, payload, fetched_at=FETCHED_1_LATER)

    assert result["decision"] == "skipped_unchanged"
    assert len(http.calls) == 1
    assert s3.ops == ops_before


def test_land_kev_writes_new_run_when_content_changed(monkeypatch, sleeps) -> None:
    s3 = _FakeS3()
    _kev(monkeypatch, s3, _kev_payload([_kev_entry("A")]))
    old = dict(s3.objects)

    _, result = _kev(
        monkeypatch, s3, _kev_payload([_kev_entry("B")]), fetched_at=FETCHED_1_LATER
    )

    assert result["decision"] == "downloaded_new_run"
    assert {k: s3.objects[k] for k in old} == old
    assert _overwrites(s3) == []
    latest = latest_committed_run(s3, KEV_ROOT_URI, "kev.json.gz")
    assert latest == f"{KEV_ROOT}/dt=2026-10-31/{run_segment(FETCHED_1_LATER)}"


def test_land_kev_force_writes_even_if_unchanged(monkeypatch, sleeps) -> None:
    s3 = _FakeS3()
    payload = _kev_payload([_kev_entry("A")])
    _kev(monkeypatch, s3, payload)

    _, result = _kev(monkeypatch, s3, payload, fetched_at=FETCHED_1_LATER, force=True)

    assert result["decision"] == "downloaded_new_run"
    assert result["previous_run"].endswith(run_segment(FETCHED_1))
    assert _overwrites(s3) == []


def test_kev_latest_run_spans_days(monkeypatch, sleeps) -> None:
    """최신 회차는 dt를 가로질러 고른다."""
    s3 = _FakeS3()
    _kev(monkeypatch, s3, _kev_payload([_kev_entry("A")]))
    _kev(monkeypatch, s3, _kev_payload([_kev_entry("B")]), fetched_at=FETCHED_2)
    latest = latest_committed_run(s3, KEV_ROOT_URI, "kev.json.gz")
    assert latest == f"{KEV_ROOT}/dt=2026-11-01/{run_segment(FETCHED_2)}"

    payload, fetched_at, _ = read_latest_kev(s3)
    assert fetched_at == FETCHED_2
    assert payload["vulnerabilities"][0]["cveID"] == "B"


def test_land_kev_refuses_to_reuse_run_path(monkeypatch, sleeps) -> None:
    """같은 회차 시각으로 force하면 덮어쓰지 않고 실패한다(`assert_new_run_prefix`)."""
    s3 = _FakeS3()
    _kev(monkeypatch, s3, _kev_payload([_kev_entry("A")]))
    snapshot = dict(s3.objects)
    with pytest.raises(RuntimeError, match="이미 있다"):
        _kev(monkeypatch, s3, _kev_payload([_kev_entry("B")]), force=True)
    assert s3.objects == snapshot


def test_read_latest_kev_requires_committed_run() -> None:
    with pytest.raises(RuntimeError, match="커밋된 KEV 회차가 없다"):
        read_latest_kev(_FakeS3())
