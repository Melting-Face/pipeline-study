"""외부 원천 파일 → S3 raw/ 수집 헬퍼 (원천 획득 단계 · 오케스트레이터 중립).

`helper.py`와 가르는 축은 **도착지**다 — `helper.py`는 「원천 → *Iceberg*」이고
이 모듈은 「외부 HTTP(인증) → *S3*」다. 파이프라인의 한 계층 앞에 있어서
이 모듈의 산출물이 곧 `helper.py`의 입력(`SOURCE_BASE/....csv.gz`)이 된다.

⚠️ **이것을 "다섯 번째 적재 경로"라고 부르지 않는다.** `overview.md`의 A~D는
전부 Iceberg 테이블로 끝나는 *같은 단계의 변종*이고, 이 모듈은 S3에서 멈추며
테이블을 만들지 않는 **앞 단계**다. 같은 표에 넣으면 "적재 경로"가 두 뜻이 된다.

🔴 **크리덴셜이 붙는 첫 경로다.** `helper._request`의 독스트링이 예고한 지점이
여기다 — *"키가 필요한 원천을 붙일 때 예외 재포장·마스킹을 함께 넣는다"*.
`requests`의 예외 메시지는 **쿼리스트링을 포함한 전체 URL**을 담으므로, 이
모듈은 외부 호출 예외를 **그대로 올리지 않는다**(`raise_masked`).

🔴 **가장 비싼 실패 모드는 401이 아니라 「200 + 로그인 HTML」이다.** 인증이
풀린 채 받으면 `chartevents.csv.gz`라는 이름의 **HTML 페이지**가 S3에 올라가고,
실패는 다운로드가 아니라 몇 시간 뒤 적재 태스크의 pyarrow 파싱에서 터진다
(원인 추적이 비싸다). 그래서 `assert_gzip_body`가 **런타임 경로에** 있다.

🔴 **강제 재수신(`force`)은 런타임 파라미터이지 환경변수가 아니다.** 원본은 이
플래그를 Dagster `dg.Config`(run config)로 받았고, 그 선택의 근거는 그대로
유효하다 — env 플래그는 전역이고 끈적여서 누가 켜둔 채 잊으면 **매 실행 전량을
다시 받는다**(원천은 남의 서버이고 3.3GB급이다). 오케스트레이터 중립 트리에서는
설정 클래스를 두지 않고 `force: bool` 인자로 받으며, 그 값을 어디서 읽을지는
호출부(DAG)가 정한다 — Airflow라면 `params`/`dag_run.conf`이며 **그 실행에만
산다**. 🔴 env에서 읽는 배선을 만들지 않는다.
"""

import hashlib
import io
import logging
from datetime import datetime, timezone
from typing import Any, NoReturn

import requests
from boto3.s3.transfer import TransferConfig
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from pipeline_project.common.helper import parse_s3_uri

# 멀티파트 파라미터.
#
# 🔴 **chunksize는 워커 메모리와 강결합이다.** 태스크는 오케스트레이터가 띄운
#    프로세스 안에서 돌고, 그 컨테이너의 `limits.memory`와 동시 실행 수가 상한을
#    정한다. 기존 업로드 스크립트는 64MB를 쓰지만 그것은 **호스트에서** 도는
#    일회성 스크립트다. 여기서는 32MB로 내린다.
# 🔴 **use_threads=False** — 메모리를 청크 1개분으로 묶는다. 다운로드가 병목이라
#    실질 손해가 없고, 동시에 업로드 스트림 읽기가 순차임이 자명해져 해시
#    계산(통과하며 누적)의 전제가 구현 세부에 기대지 않는다.
MULTIPART_THRESHOLD = 8 * 1024 * 1024
MULTIPART_CHUNKSIZE = 32 * 1024 * 1024

# SHA256SUMS.txt 한 줄: "<64자리 hex>  <상대경로>"
SHA256_HEX_LEN = 64

# gzip 매직바이트. csv.gz가 아닌 것(로그인 HTML 등)을 받으면 여기서 걸린다.
GZIP_MAGIC = b"\x1f\x8b"

# 사이드카 접미어 — 데이터 객체 옆에 해시를 텍스트 한 줄로 둔다.
SIDECAR_SUFFIX = ".sha256"

# `decide_download` 반환값. 불리언이 아니라 **열거 문자열**인 이유는 "스킵"이
# 여러 갈래이기 때문이다(원칙 7 — 부정 결과는 갈래를 구분해야 유효하다).
DECISION_SKIPPED_VERIFIED = "skipped_verified"
DECISION_FORCED = "downloaded_forced"
DECISION_ABSENT = "downloaded_absent"
DECISION_OBJECT_MISSING = "downloaded_object_missing"
DECISION_SIDECAR_MISSING = "downloaded_sidecar_missing"
DECISION_DIGEST_MISMATCH = "downloaded_digest_mismatch"
DECISION_MANIFEST_MISSING = "downloaded_manifest_missing"

# S3 객체 부재를 뜻하는 에러 코드(구현체마다 다르게 온다).
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def parse_sha256sums(text: str) -> dict[str, str]:
    """SHA256SUMS.txt 본문을 {상대경로: 해시}로 파싱한다.

    빈 줄·주석·형식이 어긋난 줄은 건너뛴다. 대상 경로가 결과에 없으면 그것은
    호출부에서 **판정 근거 없음**으로 드러난다(조용한 성공으로 만들지 않는다).

    Args:
        text: SHA256SUMS.txt 전문.

    Returns:
        상대경로 → 소문자 hex 해시 매핑.
    """
    sums: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        digest, _, path = stripped.partition(" ")
        path = path.strip().lstrip("*")  # 바이너리 모드 표기(*) 제거
        if len(digest) != SHA256_HEX_LEN or not path:
            continue
        sums[path] = digest.lower()
    return sums


def validate_relative_path(rel_path: str) -> str:
    """원천 상대경로를 검증한다(traversal 차단).

    🔴 이 값은 **URL과 S3 키를 동시에** 만든다. `..`가 섞이면 `raw/` 밖의
    객체를 덮어쓸 수 있고, SeaweedFS에 버저닝이 없어 그 덮어쓰기는 **부분
    비가역**이다. 현재 호출부가 전부 리터럴 상수라 가능성은 낮지만, 가능성이
    낮은 것과 막혀 있는 것은 다른 축이다.

    Args:
        rel_path: 데이터셋 루트 기준 상대경로.

    Returns:
        정규화된 상대경로.

    Raises:
        ValueError: 절대경로이거나 상위 참조를 포함할 때.
    """
    cleaned = rel_path.strip()
    if not cleaned:
        message = "상대경로가 비었다"
        raise ValueError(message)
    if cleaned.startswith(("/", "\\")):
        message = f"절대경로는 허용하지 않는다: {rel_path}"
        raise ValueError(message)
    if ".." in cleaned.split("/"):
        message = f"상위 참조(..)는 허용하지 않는다: {rel_path}"
        raise ValueError(message)
    return cleaned


def assert_gzip_body(content_type: str | None, first_bytes: bytes, url: str) -> None:
    """응답 본문이 실제로 gzip인지 확인한다(fail-closed).

    🔴 **인증 실패가 401로 오지 않을 수 있다.** 세션 기반 사이트는 미인증
    요청에 **200 + 로그인 HTML**을 주는 일이 흔하고, 그러면 상태코드만 보는
    코드는 전부 통과시킨다. 저장소에서 가장 비싼 실패 모드라 런타임 경로에
    가드를 둔다.

    Args:
        content_type: 응답 `Content-Type` 헤더.
        first_bytes: 본문 선두 바이트(최소 2바이트).
        url: 실패 메시지에 쓸 URL(마스킹해서 넣는다).

    Raises:
        RuntimeError: HTML이거나 gzip 매직바이트가 아닐 때.
    """
    if content_type and content_type.lower().startswith("text/html"):
        message = (
            f"{mask_url(url)} — gzip이 아니라 HTML이 왔다(로그인 페이지 가능성). "
            "크리덴셜·접근 권한을 확인한다."
        )
        raise RuntimeError(message)
    if not first_bytes.startswith(GZIP_MAGIC):
        message = (
            f"{mask_url(url)} — gzip 매직바이트 불일치"
            f"(선두 {first_bytes[:2]!r}). 원천이 기대한 파일이 아니다."
        )
        raise RuntimeError(message)


def decide_download(
    *,
    expected_sha256: str | None,
    sidecar_sha256: str | None,
    object_exists: bool,
    force: bool,
) -> str:
    """수신 여부를 판정한다(순수 함수 — 부수효과 없음).

    순수 함수로 떼어낸 이유는 **이 판정이 전량 재다운로드를 좌우**하는데
    실인프라 없이 전수 테스트할 수 있어야 하기 때문이다.

    Args:
        expected_sha256: 상류 manifest가 고시한 해시(없으면 None).
        sidecar_sha256: S3 사이드카에 기록된 과거 실행의 해시(없으면 None).
        object_exists: 데이터 객체가 S3에 있는지.
        force: 강제 재수신 여부.

    Returns:
        `DECISION_*` 상수 중 하나. `skipped_`로 시작하면 수신하지 않는다.
    """
    if force:
        return DECISION_FORCED
    if not object_exists:
        # 🔴 사이드카만 있고 객체가 없는 상태를 **절대 스킵하지 않는다.**
        #    쓰기 순서가 데이터 → 사이드카라 정상 경로에서는 생기지 않지만,
        #    사람이 객체만 지우면 생긴다. 갈래를 따로 세어 로그에 남긴다.
        return DECISION_OBJECT_MISSING if sidecar_sha256 else DECISION_ABSENT
    if sidecar_sha256 is None:
        return DECISION_SIDECAR_MISSING
    if expected_sha256 is None:
        # 상류 해시를 못 받았으면 대조할 기준이 없다. 「기준 없음」을
        # 「일치」로 읽지 않는다(fail-closed).
        return DECISION_MANIFEST_MISSING
    if sidecar_sha256 == expected_sha256:
        return DECISION_SKIPPED_VERIFIED
    return DECISION_DIGEST_MISMATCH


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


class IntegrityError(RuntimeError):
    """무결성 대조 실패 — 메타데이터를 속성으로 함께 들고 올라간다.

    원본은 실패 메타데이터를 `dg.Failure(description=, metadata=)`에 실었다.
    표준 예외에는 그 그릇이 없으므로 **메시지에 요지를, 속성에 전문**을 둔다 —
    호출부(DAG)가 `except IntegrityError as exc: exc.metadata`로 그대로
    관측 경로에 올릴 수 있다. 값은 같고 그릇만 바뀐다.

    Attributes:
        metadata: 실패 시점의 관측 메타데이터.
    """

    def __init__(self, message: str, metadata: dict[str, Any]) -> None:
        """메시지와 관측 메타데이터를 함께 들고 올린다.

        Args:
            message: 사람이 읽을 실패 요지(해시 앞 12자만 — 전문은 metadata에).
            metadata: 실패 시점의 관측 메타데이터.
        """
        super().__init__(message)
        self.metadata = metadata


class _HashingReader(io.RawIOBase):
    """업로드 스트림을 통과시키며 sha256을 누적하는 래퍼.

    파일을 두 번 읽지 않기 위한 것이다 — 3.3GB를 올린 뒤 해시를 위해 다시
    내려받으면 대역폭이 두 배가 된다.

    `prefix`는 매직바이트 검사를 위해 **미리 읽어버린 선두 바이트**다. 검사
    때문에 스트림이 소비되므로 그 바이트를 앞에 되돌려 붙인다 — 안 하면
    업로드된 객체의 선두 2바이트가 잘려 **해시가 영원히 불일치**한다.

    ⚠️ 클래스를 쓴다. 금지 대상은 *파이프라인 정의(에셋·태스크)의 클래스화*이고,
    file-like 어댑터는 상태(해시·누적 바이트)를 들고 `read()` 계약을 만족해야
    해서 함수로는 표현되지 않는다.
    """

    def __init__(self, raw: Any, prefix: bytes = b"") -> None:
        super().__init__()
        self._raw = raw
        # 아직 호출부로 내보내지 않은 선두 바이트. 해시는 생성자에서 이미
        # 반영했으므로 여기서 다시 넣지 않는다(이중 해시 금지).
        self._buffer = prefix
        self._hasher = hashlib.sha256()
        self._bytes_read = 0
        if prefix:
            self._hasher.update(prefix)
            self._bytes_read += len(prefix)

    def readable(self) -> bool:
        return True

    def _pull(self, size: int | None) -> bytes:
        """원본에서 읽어 해시에 반영한다(내보내기와 분리)."""
        chunk = self._raw.read() if size is None else self._raw.read(size)
        if chunk:
            self._hasher.update(chunk)
            self._bytes_read += len(chunk)
        return chunk

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            out = self._buffer + self._pull(None)
            self._buffer = b""
            return out
        if self._buffer:
            if len(self._buffer) >= size:
                out = self._buffer[:size]
                self._buffer = self._buffer[size:]
                return out
            # 버퍼가 모자라면 부족분만 원본에서 채운다 — 짧은 read를 내보내면
            # boto3가 청크를 잘게 쪼개 요청 수가 늘어난다.
            out = self._buffer + self._pull(size - len(self._buffer))
            self._buffer = b""
            return out
        return self._pull(size)

    @property
    def hexdigest(self) -> str:
        return self._hasher.hexdigest()

    @property
    def bytes_read(self) -> int:
        return self._bytes_read


def _head_object(s3_client: BaseClient, bucket: str, key: str) -> dict[str, Any] | None:
    """S3 객체 메타데이터를 조회한다. 없으면 None(에러 아님)."""
    try:
        return s3_client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _NOT_FOUND_CODES:
            return None
        raise


def read_sidecar(s3_client: BaseClient, bucket: str, key: str) -> str | None:
    """사이드카 객체에서 과거 실행이 기록한 해시를 읽는다. 없으면 None."""
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key + SIDECAR_SUFFIX)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _NOT_FOUND_CODES:
            return None
        raise
    return body["Body"].read().decode("utf-8").strip() or None


def write_sidecar(s3_client: BaseClient, bucket: str, key: str, digest: str) -> None:
    """사이드카 객체에 해시를 기록한다.

    🔴 **반드시 데이터 객체를 올린 *뒤에* 쓴다.** 역순이면 중간에 죽었을 때
    깨진(혹은 없는) 객체를 영영 스킵한다 — 조용하고 비가역이다. 이 순서면
    최악이라도 다음 실행이 재수신한다(낭비이되 정확).
    """
    s3_client.put_object(
        Bucket=bucket,
        Key=key + SIDECAR_SUFFIX,
        Body=digest.encode("utf-8"),
        ContentType="text/plain",
    )


def _skip_metadata(
    *,
    decision: str,
    target_uri: str,
    expected_sha256: str | None,
    sidecar: str | None,
    head: dict[str, Any] | None,
    source_url: str,
    checked_at: str,
) -> dict[str, Any]:
    """스킵 판정의 관측 메타데이터를 만든다.

    🔴 **스킵할 때도 판정 근거를 남긴다.** 그냥 건너뛰면 "검사하고 건너뛴 것"과
    "아무것도 안 한 것"이 **같은 모양**이 된다.
    """
    return {
        "decision": decision,
        # 🔴 과거 실행의 기록을 믿었을 뿐, 이 실행은 3.3GB를 재해시하지
        #    않았다. "검증됨"으로 읽히지 않도록 축을 분리해 적는다.
        "verification": "recorded",
        "target": target_uri,
        "expected_sha256": expected_sha256 or "미제공",
        "sidecar_sha256": sidecar or "없음",
        # 스킵도 **객체를 실제로 조회했다**는 증거. 없으면 "검사했다"와
        # "건너뛰었다"가 구분되지 않는다.
        "object_size_bytes": (head or {}).get("ContentLength"),
        "object_last_modified": str((head or {}).get("LastModified")),
        "bytes_transferred": 0,
        "source_url": mask_url(source_url),
        "checked_at": checked_at,
    }


def download_to_s3(
    logger: logging.Logger,
    *,
    s3_client: BaseClient,
    session: requests.Session,
    source_url: str,
    target_uri: str,
    expected_sha256: str | None = None,
    force: bool = False,
    timeout_s: int = 60,
) -> dict[str, Any]:
    """외부 파일을 스트리밍으로 받아 S3에 놓는다(멱등 스킵 포함).

    메모리에 전량 적재하지 않는다 — `stream=True` 응답을 boto3 멀티파트
    업로드에 그대로 흘려보내고 그 과정에서 sha256을 함께 계산한다.

    Args:
        logger: 판정·진행 로그를 남길 로거.
        s3_client: boto3 S3 클라이언트.
        session: 인증이 걸린 requests 세션(`PhysioNetClient.session()`).
        source_url: 원천 파일 URL.
        target_uri: 도착지 s3:// URI.
        expected_sha256: 원천이 고시한 기대 해시.
        force: True면 판정을 건너뛰고 무조건 재수신.
        timeout_s: HTTP 타임아웃(초).

    Returns:
        판정·수집 결과 메타데이터. `verification`이 이 실행이 바이트를 직접
        해시했는지(`computed`) 과거 기록을 믿었을 뿐인지(`recorded`)를 가른다.

    Raises:
        IntegrityError: 받은 바이트의 해시가 기대 해시와 다를 때.
    """
    bucket, key = parse_s3_uri(target_uri)
    checked_at = datetime.now(tz=timezone.utc).isoformat()

    head = _head_object(s3_client, bucket, key)
    sidecar = read_sidecar(s3_client, bucket, key)
    decision = decide_download(
        expected_sha256=expected_sha256,
        sidecar_sha256=sidecar,
        object_exists=head is not None,
        force=force,
    )

    if decision == DECISION_SKIPPED_VERIFIED:
        logger.info("%s: %s (verification=recorded) — 재수신 생략", key, decision)
        return _skip_metadata(
            decision=decision,
            target_uri=target_uri,
            expected_sha256=expected_sha256,
            sidecar=sidecar,
            head=head,
            source_url=source_url,
            checked_at=checked_at,
        )

    # ── 수집 ────────────────────────────────────────────────────────────
    logger.info("%s: %s — %s 수신 시작", key, decision, mask_url(source_url))
    try:
        response = session.get(source_url, stream=True, timeout=timeout_s)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise_masked(source_url, exc)

    # 🔴 decode_content=False — csv.gz를 **원본 바이트 그대로** 올린다.
    #    urllib3가 Content-Encoding을 보고 자동 해제하면 S3에는 `.csv.gz`라는
    #    이름의 평문 CSV가 올라가고, SHA256SUMS와의 바이트 동일성도 깨진다.
    response.raw.decode_content = False

    try:
        # 매직바이트 검사를 위해 선두를 미리 읽는다. 읽어버린 바이트는
        # _HashingReader의 prefix로 되돌려 붙인다(안 붙이면 객체 선두가 잘린다).
        head_bytes = response.raw.read(len(GZIP_MAGIC))
        assert_gzip_body(response.headers.get("Content-Type"), head_bytes, source_url)

        reader = _HashingReader(response.raw, prefix=head_bytes)
        s3_client.upload_fileobj(
            reader,
            bucket,
            key,
            Config=TransferConfig(
                multipart_threshold=MULTIPART_THRESHOLD,
                multipart_chunksize=MULTIPART_CHUNKSIZE,
                use_threads=False,
            ),
        )
    except requests.RequestException as exc:
        raise_masked(source_url, exc)
    finally:
        response.close()

    digest = reader.hexdigest

    # ── 무결성 대조 ──────────────────────────────────────────────────────
    # 🔴 불일치면 **방금 올린 객체를 지우고** 실패시킨다. 남겨두면 다음 실행이
    #    그 깨진 객체를 "있음"으로 세고, 사이드카가 없으니 재수신은 하겠지만
    #    그 사이 적재 태스크가 먼저 읽으면 조용히 잘못된 값이 흐른다.
    if expected_sha256 and digest != expected_sha256:
        s3_client.delete_object(Bucket=bucket, Key=key)
        message = (
            f"{key} 무결성 불일치로 적재 취소 — "
            f"기대 {expected_sha256[:12]}… / 실제 {digest[:12]}…"
        )
        raise IntegrityError(
            message,
            {
                "decision": decision,
                "target": target_uri,
                "expected_sha256": expected_sha256,
                "actual_sha256": digest,
                "bytes_transferred": reader.bytes_read,
                "source_url": mask_url(source_url),
                "checked_at": checked_at,
            },
        )

    write_sidecar(s3_client, bucket, key, digest)

    logger.info(
        "%s: %s (verification=computed) — %d bytes", key, decision, reader.bytes_read
    )
    return {
        "decision": decision,
        # 이 실행은 흘러간 바이트를 직접 해시했다.
        "verification": "computed",
        "target": target_uri,
        "sha256": digest,
        "expected_sha256": expected_sha256 or "미제공",
        "verified_against_manifest": bool(expected_sha256),
        "bytes_transferred": reader.bytes_read,
        "sidecar_uri": f"{target_uri}{SIDECAR_SUFFIX}",
        "source_url": mask_url(source_url),
        "checked_at": checked_at,
    }
