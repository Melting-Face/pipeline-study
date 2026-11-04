"""raw 스냅샷의 gzip + sha256 사이드카 규약.

옛 Dagster 공통 코드(사이드카 쓰기·검증 읽기, URI 파서, 키 나열)의 이식이다
(NVD 전용분은 삭제됨). Dagster의 `S3Resource.get_client()`는 두지 않는다 —
boto3 **클라이언트**를 직접 받는다.
"""

import gzip
import hashlib
from typing import Any

from botocore.exceptions import ClientError

SIDECAR_SUFFIX = ".sha256"

_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """s3://bucket/key → (bucket, key)."""
    if not uri.startswith("s3://"):
        message = f"s3:// URI가 아님: {uri}"
        raise ValueError(message)
    bucket, _, key = uri[len("s3://") :].partition("/")
    return bucket, key


def list_keys(s3: Any, bucket: str, prefix_key: str) -> list[str]:
    """접두 아래 모든 객체 키를 나열한다(페이지네이션 포함)."""
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix_key):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def read_sidecar(s3: Any, bucket: str, key: str) -> str | None:
    """사이드카 객체에서 과거 실행이 기록한 해시를 읽는다. 없으면 None."""
    try:
        body = s3.get_object(Bucket=bucket, Key=key + SIDECAR_SUFFIX)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _MISSING_CODES:
            return None
        raise
    return body["Body"].read().decode("utf-8").strip() or None


def gzip_sha256(body: bytes) -> str:
    """`put_gzip_with_sidecar`가 쓸 사이드카 해시를 **업로드 없이** 계산한다.

    `mtime=0`으로 압축한다 — gzip 헤더에 시각이 박히면 같은 본문도 해시가 매번
    달라져, 해시가 "내용"이 아니라 "압축한 시각"을 세게 된다.
    """
    return hashlib.sha256(gzip.compress(body, mtime=0)).hexdigest()


def put_gzip_with_sidecar(
    s3: Any, bucket: str, key: str, body: bytes
) -> dict[str, Any]:
    """바이트를 gzip해 S3에 올리고, **그 뒤에** sha256 사이드카를 쓴다.

    API 응답(메모리에 이미 있는 수 MB 이하의 JSON 바이트)을 압축해 한 번에 올린다.
    `mtime=0`으로 압축한다 — 같은 본문이면 같은 해시여야 해시가 "내용"을 센다.

    Args:
        s3: boto3 S3 클라이언트.
        bucket: 버킷.
        key: 데이터 객체 키(사이드카는 `key + SIDECAR_SUFFIX`).
        body: 압축 전 원문 바이트.

    Returns:
        `sha256`·`raw_bytes`·`gzip_bytes`.
    """
    compressed = gzip.compress(body, mtime=0)
    digest = hashlib.sha256(compressed).hexdigest()
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=compressed,
        ContentType="application/gzip",
    )
    # 🔴 데이터 → 사이드카 순서. 역순이면 중간에 죽었을 때 깨진(혹은 없는) 객체를
    # 영영 스킵한다 — 조용하고 비가역이다. 이 순서면 최악이라도 다음 실행이
    # 재수신한다(낭비이되 정확).
    s3.put_object(
        Bucket=bucket,
        Key=key + SIDECAR_SUFFIX,
        Body=digest.encode("utf-8"),
        ContentType="text/plain",
    )
    return {"sha256": digest, "raw_bytes": len(body), "gzip_bytes": len(compressed)}


def read_verified_gzip(s3: Any, bucket: str, key: str) -> bytes:
    """사이드카로 무결성을 대조한 뒤 gzip 객체를 풀어 돌려준다.

    🔴 **사이드카가 없으면 읽지 않는다.** 사이드카는 "쓰기가 끝났다"는 커밋 표지라
    (데이터 → 사이드카 순서), 없다는 것은 쓰는 중에 죽었거나 아직 안 쓴 것이다.
    그 객체를 읽어 적재하면 잘린 스냅샷이 정상으로 들어간다.

    Raises:
        RuntimeError: 사이드카가 없거나 해시가 다를 때.
    """
    expected = read_sidecar(s3, bucket, key)
    if expected is None:
        message = f"s3://{bucket}/{key}: 사이드카 없음 — 미완결 객체는 읽지 않는다"
        raise RuntimeError(message)
    compressed = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    digest = hashlib.sha256(compressed).hexdigest()
    if digest != expected:
        message = (
            f"s3://{bucket}/{key}: 무결성 불일치 — "
            f"사이드카 {expected[:12]}… / 실제 {digest[:12]}…"
        )
        raise RuntimeError(message)
    return gzip.decompress(compressed)
