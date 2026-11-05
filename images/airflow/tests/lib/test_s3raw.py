"""raw 사이드카 규약(`lakehouse.s3raw`).

Dagster `test_nvd_parse.py`의 사이드카 셀 이식. 실인프라에 붙지 않는다 —
boto3 클라이언트 대역(`_FakeS3`)이 쓰기·삭제 **순서**를 기록한다.
"""

import hashlib
import io

import pytest
from botocore.exceptions import ClientError
from lakehouse.s3raw import (
    SIDECAR_SUFFIX,
    gzip_sha256,
    list_keys,
    parse_s3_uri,
    put_gzip_with_sidecar,
    read_sidecar,
    read_verified_gzip,
)

BUCKET = "warehouse"


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
    """boto3 S3 **클라이언트** 대역 — 쓰기·삭제 순서를 기록한다."""

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


def test_put_gzip_writes_data_then_sidecar() -> None:
    s3 = _FakeS3()
    put_gzip_with_sidecar(s3, BUCKET, "k", b"body")
    assert s3.ops == [("put", "k"), ("put", "k" + SIDECAR_SUFFIX)]


def test_sidecar_hash_is_content_not_time() -> None:
    """`mtime=0` — 같은 본문이면 같은 해시다(압축 시각을 세지 않는다)."""
    first, second = _FakeS3(), _FakeS3()
    put_gzip_with_sidecar(first, BUCKET, "k", b"same")
    put_gzip_with_sidecar(second, BUCKET, "k", b"same")
    assert first.objects["k"] == second.objects["k"]
    assert (
        first.objects["k" + SIDECAR_SUFFIX].decode()
        == hashlib.sha256(first.objects["k"]).hexdigest()
    )


def test_gzip_sha256_matches_sidecar() -> None:
    s3 = _FakeS3()
    result = put_gzip_with_sidecar(s3, BUCKET, "k", b"body")
    assert gzip_sha256(b"body") == result["sha256"]
    assert gzip_sha256(b"body") == s3.objects["k" + SIDECAR_SUFFIX].decode()
    assert result["raw_bytes"] == 4
    assert result["gzip_bytes"] == len(s3.objects["k"])


def test_read_verified_gzip_roundtrip() -> None:
    s3 = _FakeS3()
    put_gzip_with_sidecar(s3, BUCKET, "k", b"payload")
    assert read_verified_gzip(s3, BUCKET, "k") == b"payload"


def test_read_verified_gzip_rejects_tampered() -> None:
    s3 = _FakeS3()
    put_gzip_with_sidecar(s3, BUCKET, "k", b"payload")
    s3.objects["k"] = s3.objects["k"] + b"x"
    with pytest.raises(RuntimeError, match="무결성"):
        read_verified_gzip(s3, BUCKET, "k")


def test_read_verified_gzip_refuses_missing_sidecar() -> None:
    s3 = _FakeS3()
    put_gzip_with_sidecar(s3, BUCKET, "k", b"payload")
    del s3.objects["k" + SIDECAR_SUFFIX]
    with pytest.raises(RuntimeError, match="사이드카 없음"):
        read_verified_gzip(s3, BUCKET, "k")


def test_read_sidecar_none_when_absent() -> None:
    assert read_sidecar(_FakeS3(), BUCKET, "k") is None


def test_list_keys_returns_prefix_only() -> None:
    s3 = _FakeS3()
    for key in ("a/1", "a/2", "a/3", "b/1"):
        s3.objects[key] = b""
    assert list_keys(s3, BUCKET, "a/") == ["a/1", "a/2", "a/3"]


def test_parse_s3_uri() -> None:
    assert parse_s3_uri("s3://warehouse/raw/nvd") == ("warehouse", "raw/nvd")
    with pytest.raises(ValueError, match="s3://"):
        parse_s3_uri("https://warehouse/raw")
