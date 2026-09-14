"""원천 획득 경로 테스트 — `common/fetch.py` · `common/physionet.py`.

**실인프라에 붙지 않는다.** physionet.org에도 S3에도 접속하지 않고, HTTP 세션과
S3 클라이언트를 손수 만든 대역으로 갈아끼운다(`docs/test.md` 격리 원칙).
새 모킹 라이브러리를 도입하지 않는 것은 저장소 선례를 따른 것이다.

이 파일이 지키는 축은 셋이다.

1. **멱등 판정**(`decide_download`) — 전량 재수신을 좌우하는 분기라 전수로 센다.
   특히 *"사이드카는 있는데 객체가 없다"*가 스킵으로 새지 않는지 본다.
2. **200 + 로그인 HTML 방어**(`assert_gzip_body`) — 이 저장소에서 가장 비싼
   실패 모드다. 인증이 풀린 채 받으면 `.csv.gz`라는 이름의 HTML이 S3에 올라가고
   실패는 몇 시간 뒤 적재 자산의 파싱에서 터진다.
3. **해시 정확성**(`_HashingReader`) — 매직바이트 검사로 미리 읽은 선두
   바이트를 되돌려 붙이는 경로가 있어, 여기가 틀리면 해시만 조용히 어긋나
   **매번 재수신**하거나 **무결성 대조가 거짓 실패**한다.

🔴 데이터는 전부 **합성 바이트**다 — 원천 데이터를 저장소에 담지 않는다.
"""

import gzip
import hashlib
import io

import pytest
from botocore.exceptions import ClientError
from dagster_project.common.fetch import (
    DECISION_ABSENT,
    DECISION_DIGEST_MISMATCH,
    DECISION_FORCED,
    DECISION_MANIFEST_MISSING,
    DECISION_OBJECT_MISSING,
    DECISION_SIDECAR_MISSING,
    DECISION_SKIPPED_VERIFIED,
    SIDECAR_SUFFIX,
    _HashingReader,
    assert_gzip_body,
    decide_download,
    download_to_s3,
    mask_url,
    parse_sha256sums,
    validate_relative_path,
)

import dagster as dg

# 합성 원천 — 실제 MIMIC/eICU 데이터가 아니다.
PAYLOAD = gzip.compress(b"subject_id,value\n1,10\n2,20\n")
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()

BUCKET = "warehouse"
KEY = "raw/eicu/patient.csv.gz"
TARGET_URI = f"s3://{BUCKET}/{KEY}"
SOURCE_URL = "https://physionet.org/files/eicu-crd/2.0/patient.csv.gz"


# ── 대역 ────────────────────────────────────────────────────────────────


class _FakeRaw:
    """`response.raw` 대역.

    `io.BytesIO`를 직접 쓰지 않는 이유: BytesIO 인스턴스에는 `__dict__`가 없어
    `decode_content` 속성을 붙일 수 없다(실코드가 그 속성을 설정한다).
    """

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)
        self.decode_content = True

    def read(self, size: int = -1) -> bytes:
        return self._buf.read() if size is None or size < 0 else self._buf.read(size)


class _FakeResponse:
    def __init__(self, data: bytes, content_type: str = "application/gzip") -> None:
        self.raw = _FakeRaw(data)
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, data: bytes, content_type: str = "application/gzip") -> None:
        self._data = data
        self._content_type = content_type
        self.get_calls = 0

    def get(self, url: str, stream: bool = False, timeout: int = 0) -> _FakeResponse:
        self.get_calls += 1
        return _FakeResponse(self._data, self._content_type)

    def close(self) -> None:
        return None


class _FakeS3:
    """`S3Resource` 대역 — 실코드가 쓰는 클라이언트 메서드만 갖는다.

    `chunk_reads`를 켜면 `upload_fileobj`가 **청크 단위**로 읽는다. boto3의
    멀티파트 업로더가 그렇게 읽으므로, 한 번에 다 읽는 경로만 테스트하면
    prefix 되돌리기 버그를 놓친다.
    """

    def __init__(self, objects: dict[str, bytes] | None = None, *, chunk_reads=False):
        self.objects: dict[str, bytes] = dict(objects or {})
        self.uploaded: list[tuple[str, bytes]] = []
        self.deleted: list[str] = []
        self.put_calls: list[tuple[str, bytes]] = []
        self._chunk_reads = chunk_reads

    def get_client(self) -> "_FakeS3":
        return self

    def head_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key]), "LastModified": "T0"}

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.objects[Key] = Body
        self.put_calls.append((Key, Body))

    def upload_fileobj(self, fileobj, Bucket, Key, Config=None):  # noqa: N803
        if self._chunk_reads:
            chunks = []
            while True:
                chunk = fileobj.read(7)  # 일부러 매직바이트(2)와 어긋나는 폭
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
        else:
            data = fileobj.read()
        self.objects[Key] = data
        self.uploaded.append((Key, data))

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        self.objects.pop(Key, None)
        self.deleted.append(Key)


# ── manifest 파싱 ───────────────────────────────────────────────────────


def test_parse_sha256sums_reads_entries():
    text = f"{'a' * 64}  icu/icustays.csv.gz\n{'b' * 64}  hosp/patients.csv.gz\n"
    assert parse_sha256sums(text) == {
        "icu/icustays.csv.gz": "a" * 64,
        "hosp/patients.csv.gz": "b" * 64,
    }


def test_parse_sha256sums_skips_noise():
    """빈 줄·주석·형식 오류는 버리고, 바이너리 표기(*)는 벗긴다."""
    text = f"\n# comment\ntooshort  bad.csv.gz\n{'c' * 64} *patient.csv.gz\n"
    assert parse_sha256sums(text) == {"patient.csv.gz": "c" * 64}


def test_parse_sha256sums_empty_on_html():
    """로그인 HTML이 200으로 와도 항목 0개가 된다(조용한 오인식 방지)."""
    assert parse_sha256sums("<!DOCTYPE html><html><body>Login</body></html>") == {}


# ── 경로 검증 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../etc/passwd", "icu/../../x.gz", "/abs/path.gz", ""])
def test_validate_relative_path_rejects_traversal(bad):
    """🔴 이 값이 URL과 S3 키를 **동시에** 만든다 — raw/ 밖 쓰기를 막는다."""
    with pytest.raises(ValueError, match=r"허용하지 않는다|비었다"):
        validate_relative_path(bad)


def test_validate_relative_path_allows_nested():
    assert validate_relative_path("icu/chartevents.csv.gz") == "icu/chartevents.csv.gz"


# ── 본문 가드 (가장 비싼 실패 모드) ──────────────────────────────────────


def test_assert_gzip_body_rejects_login_html():
    with pytest.raises(RuntimeError, match="HTML"):
        assert_gzip_body("text/html; charset=utf-8", b"<!", SOURCE_URL)


def test_assert_gzip_body_rejects_non_gzip_magic():
    """Content-Type이 그럴듯해도 바이트가 gzip이 아니면 막는다."""
    with pytest.raises(RuntimeError, match="매직바이트"):
        assert_gzip_body("application/octet-stream", b"<h", SOURCE_URL)


def test_assert_gzip_body_accepts_gzip():
    assert_gzip_body("application/gzip", PAYLOAD[:2], SOURCE_URL)


# ── 멱등 판정 (전수) ────────────────────────────────────────────────────


def test_decide_skips_only_when_verified():
    assert (
        decide_download(
            expected_sha256="x", sidecar_sha256="x", object_exists=True, force=False
        )
        == DECISION_SKIPPED_VERIFIED
    )


@pytest.mark.parametrize(
    ("expected", "sidecar", "exists", "force", "want"),
    [
        ("x", "x", True, True, DECISION_FORCED),
        ("x", None, False, False, DECISION_ABSENT),
        # 🔴 사이드카만 있고 객체가 없다 — 절대 스킵이면 안 된다.
        ("x", "x", False, False, DECISION_OBJECT_MISSING),
        ("x", None, True, False, DECISION_SIDECAR_MISSING),
        ("x", "y", True, False, DECISION_DIGEST_MISMATCH),
        # 기대 해시가 없으면 「기준 없음」이지 「일치」가 아니다.
        (None, "x", True, False, DECISION_MANIFEST_MISSING),
    ],
)
def test_decide_download_enumerates(expected, sidecar, exists, force, want):
    assert (
        decide_download(
            expected_sha256=expected,
            sidecar_sha256=sidecar,
            object_exists=exists,
            force=force,
        )
        == want
    )


def test_decide_download_never_skips_except_verified():
    """음성 대조 — 스킵은 오직 한 조합에서만 나온다."""
    skips = [
        decide_download(expected_sha256=e, sidecar_sha256=s, object_exists=o, force=f)
        for e in (None, "x")
        for s in (None, "x", "y")
        for o in (True, False)
        for f in (True, False)
    ]
    assert skips.count(DECISION_SKIPPED_VERIFIED) == 1


# ── 해시 리더 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("read_size", [-1, 1, 2, 3, 7, 4096])
def test_hashing_reader_matches_plain_hash(read_size):
    """prefix를 되돌려 붙여도 바이트·해시가 원본과 같아야 한다.

    🔴 읽기 폭을 바꿔 가며 본다. 매직바이트(2바이트)와 어긋나는 폭에서
    prefix 경계 버그가 드러난다 — 그 버그는 에러가 아니라 **해시만 틀린다**.
    """
    prefix, rest = PAYLOAD[:2], PAYLOAD[2:]
    reader = _HashingReader(_FakeRaw(rest), prefix=prefix)
    out = []
    while True:
        chunk = reader.read(read_size)
        if not chunk:
            break
        out.append(chunk)
        if read_size < 0:
            break
    assert b"".join(out) == PAYLOAD
    assert reader.hexdigest == PAYLOAD_SHA
    assert reader.bytes_read == len(PAYLOAD)


# ── 수집 경로 (통합) ────────────────────────────────────────────────────


@pytest.mark.parametrize("chunked", [False, True])
def test_download_writes_object_and_sidecar(chunked):
    s3 = _FakeS3(chunk_reads=chunked)
    session = _FakeSession(PAYLOAD)
    result = download_to_s3(
        dg.build_asset_context(),
        s3=s3,
        session=session,
        source_url=SOURCE_URL,
        target_uri=TARGET_URI,
        expected_sha256=PAYLOAD_SHA,
    )
    # 바이트가 온전히 올라갔는가 — 선두 2바이트가 잘리지 않았는가
    assert s3.objects[KEY] == PAYLOAD
    assert s3.objects[KEY + SIDECAR_SUFFIX] == PAYLOAD_SHA.encode()
    assert result.metadata["verification"] == "computed"
    assert result.metadata["sha256"] == PAYLOAD_SHA
    assert result.metadata["bytes_transferred"] == len(PAYLOAD)


def test_download_skips_when_sidecar_matches():
    """🔴 음성 대조 — 스킵이면 업로드가 **불리지 않아야** 한다.

    메타데이터만 보면 "스킵됨"이 테스트 둔감인지 실제 생략인지 갈리지 않는다.
    """
    s3 = _FakeS3({KEY: PAYLOAD, KEY + SIDECAR_SUFFIX: PAYLOAD_SHA.encode()})
    session = _FakeSession(PAYLOAD)
    result = download_to_s3(
        dg.build_asset_context(),
        s3=s3,
        session=session,
        source_url=SOURCE_URL,
        target_uri=TARGET_URI,
        expected_sha256=PAYLOAD_SHA,
    )
    assert result.metadata["decision"] == DECISION_SKIPPED_VERIFIED
    assert result.metadata["bytes_transferred"] == 0
    # 검사했다는 증거가 남는가(건너뛴 것과 구분)
    assert result.metadata["object_size_bytes"] == len(PAYLOAD)
    assert result.metadata["verification"] == "recorded"
    assert s3.uploaded == []
    assert session.get_calls == 0


def test_download_force_ignores_sidecar():
    s3 = _FakeS3({KEY: PAYLOAD, KEY + SIDECAR_SUFFIX: PAYLOAD_SHA.encode()})
    result = download_to_s3(
        dg.build_asset_context(),
        s3=s3,
        session=_FakeSession(PAYLOAD),
        source_url=SOURCE_URL,
        target_uri=TARGET_URI,
        expected_sha256=PAYLOAD_SHA,
        force=True,
    )
    assert result.metadata["decision"] == DECISION_FORCED
    assert len(s3.uploaded) == 1


def test_download_rejects_login_html_before_upload():
    """인증이 풀린 응답은 S3에 **닿기 전에** 막힌다."""
    s3 = _FakeS3()
    with pytest.raises(RuntimeError, match="HTML"):
        download_to_s3(
            dg.build_asset_context(),
            s3=s3,
            session=_FakeSession(b"<!DOCTYPE html><html>login</html>", "text/html"),
            source_url=SOURCE_URL,
            target_uri=TARGET_URI,
            expected_sha256=PAYLOAD_SHA,
        )
    assert s3.uploaded == []
    assert KEY not in s3.objects


def test_download_deletes_object_on_digest_mismatch():
    """무결성 불일치면 방금 올린 객체를 지우고 실패한다(부분 성공 금지)."""
    s3 = _FakeS3()
    with pytest.raises(RuntimeError, match="무결성 불일치"):
        download_to_s3(
            dg.build_asset_context(),
            s3=s3,
            session=_FakeSession(PAYLOAD),
            source_url=SOURCE_URL,
            target_uri=TARGET_URI,
            expected_sha256="f" * 64,
        )
    assert KEY in s3.deleted
    assert KEY not in s3.objects
    # 사이드카도 남지 않아야 한다 — 남으면 다음 실행이 깨진 상태를 신뢰한다
    assert KEY + SIDECAR_SUFFIX not in s3.objects


def test_download_without_manifest_still_transfers():
    """기대 해시가 없으면 매번 받는다(조용한 스킵보다 비싼 재수신)."""
    s3 = _FakeS3({KEY: PAYLOAD, KEY + SIDECAR_SUFFIX: PAYLOAD_SHA.encode()})
    result = download_to_s3(
        dg.build_asset_context(),
        s3=s3,
        session=_FakeSession(PAYLOAD),
        source_url=SOURCE_URL,
        target_uri=TARGET_URI,
        expected_sha256=None,
    )
    assert result.metadata["decision"] == DECISION_MANIFEST_MISSING
    assert result.metadata["verified_against_manifest"] is False
    assert len(s3.uploaded) == 1


# ── 크리덴셜 마스킹 (회귀) ──────────────────────────────────────────────


def test_mask_url_strips_inline_credentials_and_query():
    masked = mask_url("https://user:secret@physionet.org/files/x.gz?token=abc")
    assert "secret" not in masked
    assert "token" not in masked
    assert masked == "https://physionet.org/files/x.gz"
