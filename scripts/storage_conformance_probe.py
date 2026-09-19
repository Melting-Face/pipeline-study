#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.36,<1.44",
#     "pyiceberg[pyarrow,sql-sqlite]>=0.11,<0.12",
# ]
# ///
"""S3 호환 오브젝트 스토리지 합격 스위트 (교체 후보 판정용 실측 프로브).

**왜 이 스크립트인가.** SeaweedFS 교체 후보(RustFS·Ceph RGW·Garage)의 공식 문서가
`aws-chunked`·`x-amz-trailer`·`STREAMING-UNSIGNED-PAYLOAD-TRAILER`에 대해 **전부
침묵**한다(1차 문서 조사 0회 매칭). 문서로는 후보를 가릴 수 없으므로 **실측이
유일한 판정 수단**이다. 같은 스크립트를 대조군(SeaweedFS)과 후보에 **같은 인자로**
겨눠 결과를 나란히 놓는 것이 이 파일의 존재 이유다.

**대조군은 빨개야 한다.** 현행 SeaweedFS가 P1에서 통과하면 SeaweedFS가 고쳐진 것이
아니라 **프로브가 고장난 것**이다. 그래서 P1은 저장소 전역에 깔린 우회책
(`AWS_REQUEST_CHECKSUM_CALCULATION=when_required`)을 **클라이언트 설정으로 명시
덮어써** SDK 기본 경로(`when_supported`)를 강제로 탄다. 그리고 같은 실행에서
`when_required` 대조 케이스도 돌려 **둘이 갈리는지**를 함께 본다.

🔴 **「덮어쓰기가 먹혔는가」는 결과가 아니라 전선(wire)에서 확인한다.**
   botocore는 스트리밍 입력이라도 **엔드포인트가 https일 때만** 트레일러
   체크섬을 쓴다(`httpchecksum.resolve_request_checksum_algorithm` — 실측:
   botocore 1.43). http 엔드포인트에서는 체크섬이 **헤더로** 붙고 `aws-chunked`
   프레이밍이 **아예 생기지 않는다** ⇒ 이때의 초록은 "SeaweedFS가 통과했다"가
   아니라 **"그 축을 안 쟀다"** 이다. 이 스크립트는 PUT 요청 헤더를 직접 관측해
   프레이밍이 `trailer`가 아니면 P1을 PASS가 아니라 **`미측정`** 으로 낸다.

## 프로브 5종

- P1 SDK 기본 체크섬(aws-chunked + 트레일러) PUT → 바이트 무결성
- P2 boto3 **기본 설정** PUT/GET 왕복(단일 + 멀티파트)
- P3 pyiceberg 테이블 write→read + FileIO 대용량 멀티파트
- P4 warehouse 직접 나열(FS 재귀 나열 ↔ ListObjectsV2 대조)
- P5 path-style · ListObjectsV2 페이지네이션 · 멀티파트 abort

## 🔴 미검사 축 (빠뜨린 것이 아니라 일부러 안 한 것)

1. **Java / AWS SDK v2 경로 전체.** SeaweedFS 손상은 **Java에서도** 났다
   (`iceberg-aws-bundle` ≥ 1.11.0 = SDK v2 ≥ 2.30.0 — docs/conventions/k8s/checksum.md).
   이 스크립트는 **Python(boto3/botocore·pyarrow)만** 본다. 후보가 여기서
   전부 초록이어도 **Spark·Flink의 S3FileIO 축은 판정되지 않았다.**
2. **Hadoop `s3a://` 클라이언트(JVM).** P4가 재는 것은 `s3a`가 아니라
   pyarrow `S3FileSystem`(aws-sdk-cpp)의 **재귀 나열**이다. 같은 "디렉터리
   나열"이라는 말 아래 구현이 다르므로 P4 초록을 `remove_orphan_files`
   (Spark, JVM) 안전의 근거로 읽지 않는다.
3. **barman-cloud의 실제 WAL 아카이빙 왕복.** CNPG + barman 플러그인이 필요해
   범위 밖이다. P2는 **boto3 기본 PUT 수준의 근사**이며, P2가 재현되면 현재
   원인 미규명인 `PutObject InternalError`까지 설명될 **가능성**이 있을 뿐
   등가가 아니다. P2가 초록이어도 barman 축은 여전히 `미확인`이다.
4. **성능·내구성·동시성.** 이 스위트는 **정합성**만 본다.

## 종료 코드 — 「통과/실패」가 아니라 「관측 경로가 살아 있었는가」

    0  선택한 프로브가 **전부 실행됐다**(결과가 FAIL이어도 0. FAIL은 정보다)
    2  하나 이상이 **미측정**(접속 불가·의존성 부재·예기치 않은 예외·
       또는 P1처럼 **겨냥한 축이 전선에서 성립하지 않음**)
    3  사전 조건 미충족으로 **아무것도 못 돌렸다**(자격증명·엔드포인트·안전 가드)

    🔴 `1`은 쓰지 않는다 — FAIL을 종료코드로 내보내면 "못 돈 것"과 뭉쳐진다.
    판정 갈래도 같은 원칙으로 가른다:
      - 스토어가 응답한 오류(`ClientError`)  → **FAIL**(측정됐다. 정보다)
      - 접속 실패·모듈 부재·예기치 않은 예외 → **미측정**(정보가 아니다)

## 안전

- 쓰기는 **전용 프로브 버킷 + 실행마다 새로 만드는 프리픽스**에만 한다.
  가드는 **두 겹**이고 서로를 대체하지 않는다:
  ① **allowlist** — 버킷명·프리픽스에 `probe` 마커를 **강제**한다. 실 버킷
     (`warehouse`·`pg-backup`·`dagster-logs`)이 **구조적으로** 배제되므로
     인벤토리가 늘어도 이 파일을 따라 갱신할 필요가 없다.
  ② **denylist** — 실 버킷 이름을 **하드 거부**한다(이중 방어).
- 버킷 삭제·기존 객체 삭제는 하지 않는다. 정리는 **이번 실행이 만든 키만**
  지우며, **정리 결과는 판정과 분리해 별도로 출력**한다(정리 실패가 결과를
  가리지 않게).
- 🔴 **삭제 범위는 서버 응답이 아니라 자기가 만든 문자열로 정한다.**
  `ListObjectsV2`가 `Prefix`를 지키는지가 **이 스위트의 검사 대상**이라
  (P5가 계약을, P4가 나열 정합을 잰다) 정리에서 그 응답을 무조건 신뢰하면
  순환이다. 나열 결과는 `run_prefix`로 **클라이언트에서 재검증**하고,
  걸러진 건수가 0이 아니면 **비준수의 추가 관측점으로 출력**한다.

## 예외 출력 규칙

임의 예외를 잡는 자리(`except Exception`)에서는 **`type(exc).__name__`만**
싣고 **`str(exc)`는 싣지 않는다**. 예외 메시지의 내용은 서드파티가 정하는데
P3·P4는 자격증명을 딕셔너리 값·키워드 인자로 넘기고(`SqlCatalog`·
`PyArrowFileIO`·`S3FileSystem`), pydantic `ValidationError`처럼 **입력값을
메시지에 싣는** 구현이 있어 비노출이 **보장되지 않는다**. 실유출이 관측된
것은 아니지만(「보장되지 않음」이지 「유출됨」이 아니다) 같은 파일에 안전한
패턴이 이미 있으므로 갈리는 쪽이 **드리프트**다. 스토어가 응답한
`ClientError`는 예외 — 에러 **코드**만 꺼내 쓴다(입력값이 아니다).

`FlexibleChecksumError`·`AwsChunkedWrapperError`도 예외다 — P1·P2는 이 둘의
**전문(`{exc}`)을 싣는다**. 근거는 범주다: botocore `httpchecksum.py`를 직접
열어 보면 두 예외의 `error_msg`는 **SDK 내부 상태**(기대/실제 체크섬 digest·
알고리즘명·스트림 seek 상태)로만 조립되고 **자격증명·사용자 입력값을 에코하는
경로가 없다**. 입력값을 그대로 메시지에 싣는 pydantic `ValidationError` 계열과
같이 취급하면 P1이 겨냥한 축 그 자체(어느 체크섬이 어떻게 어긋났는가)가
타입명 한 줄로 뭉개진다.
🔴 **잔여위험**: `error_msg`는 botocore **내부 구현이지 공개 계약이 아니다** —
상위 버전에서 조립 재료가 바뀌어도 아무 신호가 없다. 재확인 트리거는 시점이
아니라 **조건**이다: 위 PEP 723 `boto3` **상한(`<1.44`)을 올리기 전에** 이
전제를 다시 확인한다(올린 뒤가 아니다).

## 실행 (의존성은 위 PEP 723 — uv가 자동 provisioning)

    uv run scripts/storage_conformance_probe.py --help
    uv run scripts/storage_conformance_probe.py            # .env의 좌표로
    uv run scripts/storage_conformance_probe.py \
        --endpoint https://s3.example.invalid --only p1,p2

자격증명·엔드포인트는 **환경변수에서만** 읽는다(하드코딩 없음):
`ICEBERG_S3_ENDPOINT` / `ICEBERG_S3_ACCESS_KEY` / `ICEBERG_S3_SECRET_KEY`,
미설정 시 `S3_ENDPOINT_URL` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` 폴백.
🔴 키 값은 출력·에러 메시지에 **싣지 않는다**(전선 헤더도 화이트리스트로만 본다).

스타일: 스크립트 컨벤션(docs/conventions/python.md)에 따라 절차형으로 쓴다.
    선언은 상단·진입은 하단, 클래스 없이 하나의 main()에서 위→아래로 실행한다.
    3회 이상 반복되는 것만 함수로 뽑는다(Rule of Three).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import secrets
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── 안전 가드 ────────────────────────────────────────────────────────────
# 프로브 전용 기본 좌표. 실데이터 버킷과 **이름이 겹칠 수 없게** 둔다.
DEFAULT_BUCKET = "conformance-probe"
DEFAULT_PREFIX = "conformance-probe"
# 🔴 1차 방어 — **allowlist**. 버킷명에도 마커를 강제한다.
#   denylist 단독은 **인벤토리가 늘 때마다 이 상수를 갱신해야 하는데 그 트리거가
#   없다**. 실제로 한 번 어긋났다: 실 버킷은 `warehouse`/`pg-backup`/
#   `dagster-logs` 3개인데(정본 scripts/k8s-poc-storage.sh) 목록은 백업 버킷을
#   빠뜨리고 버킷이 아닌 `raw`(=s3://warehouse/raw 경로)를 넣고 있었다.
#   마커 강제는 실 버킷 3종을 **구조적으로** 배제하므로 인벤토리를 따라다니지
#   않는다 — 앞으로 버킷이 늘어도 이 파일은 그대로다.
REQUIRED_BUCKET_MARKER = "probe"
# 2차 방어 — denylist. allowlist를 **대체하지 않는다**(두 겹을 함께 둔다).
#   마커를 포함하면서 실데이터이기도 한 이름이 생기는 경우를 위한 이중 방어다.
FORBIDDEN_BUCKETS = frozenset({"warehouse", "pg-backup", "dagster-logs"})
# 프리픽스에 반드시 들어가야 하는 토큰 — 구조적 오폭 방지.
REQUIRED_PREFIX_MARKER = "probe"

# ── 페이로드 크기 ────────────────────────────────────────────────────────
SMALL_PAYLOAD_BYTES = 64 * 1024
# S3 규격상 마지막이 아닌 파트는 5 MiB 이상이어야 한다.
MULTIPART_MIN_PART_BYTES = 5 * 1024 * 1024
DEFAULT_MULTIPART_MB = 12
DEFAULT_LARGE_MB = 16
PAGINATION_OBJECTS = 5
PAGINATION_PAGE_SIZE = 2

# ── 전선(wire) 관측 ──────────────────────────────────────────────────────
# 🔴 블랙리스트가 아니라 **화이트리스트**다.
#   Authorization 헤더에 액세스 키 ID가 실리므로 "빼는" 방식은 안 쓴다.
WIRE_HEADER_ALLOWLIST = (
    "content-encoding",
    "transfer-encoding",
    "x-amz-trailer",
    "x-amz-sdk-checksum-algorithm",
    "x-amz-checksum-crc32",
    "x-amz-content-sha256",
    "x-amz-decoded-content-length",
)
FRAMING_TRAILER = "trailer"
FRAMING_HEADER = "header"
FRAMING_NONE = "none"

# ── 판정 어휘 ────────────────────────────────────────────────────────────
# 식별자에 PASS/FAIL을 쓰지 않는 이유는 ruff S105(하드코딩 비밀 추정)가
# 이름에 든 "pass"를 비밀번호로 오탐하기 때문이다. **출력 문자열은 그대로**다.
VERDICT_OK = "PASS"
VERDICT_NG = "FAIL"
VERDICT_UNMEASURED = "미측정"

EXIT_ALL_MEASURED = 0
EXIT_SOME_UNMEASURED = 2
EXIT_PRECONDITION = 3

PROBE_IDS = ("p1", "p2", "p3", "p4", "p5")

# 판정 누적 — 절차형 스크립트라 모듈 스코프에 둔다(출력 순서 = 실행 순서).
RESULTS: dict[str, str] = {}
SUMMARY: list[tuple[str, str, str]] = []
EXTRA: dict[str, str] = {}


def record(
    probe: str,
    title: str,
    verdict: str,
    counts: str,
    expectation: str,
    basis: str,
    observed: str,
) -> None:
    """프로브 1건의 판정을 즉시 출력하고 요약 표에 적는다.

    출력에는 판정만이 아니라 **무엇을 세는가(계측 단위)** 와 **기대값 + 그
    기대값의 근거**를 함께 싣는다. 숫자만 남으면 나중에 그 숫자가 무엇을
    셌는지 복원할 수 없다(docs/philosophy.md §계측 단위).
    """
    RESULTS[probe] = verdict
    SUMMARY.append((probe, title, verdict))
    print(f"── {probe.upper()} {title}")
    print(f"   무엇을 세는가 : {counts}")
    print(f"   기대값        : {expectation}")
    print(f"   기대 근거     : {basis}")
    print(f"   관측          : {observed}")
    print(f"   판정          : {verdict}")
    print()


def make_s3_client(
    boto3_mod: Any,
    config_cls: Any,
    endpoint: str,
    region: str,
    creds: tuple[str, str],
    checksum_mode: str | None,
) -> Any:
    """path-style S3 클라이언트를 만든다.

    `checksum_mode`가 None이면 **아무 것도 지정하지 않는다** — 즉 환경변수
    (`AWS_REQUEST_CHECKSUM_CALCULATION`)와 SDK 기본값이 그대로 결정한다.
    값을 주면 그 값이 환경변수를 **이긴다**(botocore 설정 우선순위:
    클라이언트 Config > 환경변수 > 공유 설정파일).
    """
    config_kwargs: dict[str, Any] = {
        # SeaweedFS를 비롯한 자체 호스팅 스토어는 가상호스트 주소를 못 쓴다.
        "s3": {"addressing_style": "path"},
        "signature_version": "s3v4",
        "retries": {"max_attempts": 2, "mode": "standard"},
    }
    if checksum_mode is not None:
        config_kwargs["request_checksum_calculation"] = checksum_mode
        config_kwargs["response_checksum_validation"] = checksum_mode
    access_key, secret_key = creds
    return boto3_mod.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=config_cls(**config_kwargs),
    )


def put_get_roundtrip(
    client: Any,
    bucket: str,
    key: str,
    payload: bytes,
) -> tuple[bool, str]:
    """PUT한 바이트와 GET한 바이트가 같은지 본다 — (동일 여부, 관측 설명).

    🔴 PUT이 200이라는 사실은 무결성의 근거가 아니다. SeaweedFS 손상 사례는
    **쓰기가 성공 응답을 받고** 이후 읽기에서 드러났다.
    """
    client.put_object(Bucket=bucket, Key=key, Body=payload)
    got = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    same = got == payload
    detail = (
        f"원본 {len(payload)}B / 회수 {len(got)}B / "
        f"sha256 {'일치' if same else '불일치'}"
    )
    if not same:
        # 프레이밍 바이트가 섞였는지 보이게 선두만 hex로 남긴다.
        # (프로브가 직접 만든 난수 페이로드라 민감정보가 아니다)
        detail += f" / 회수 선두 24B(hex) {got[:24].hex()}"
    return same, detail


def list_keys(client: Any, bucket: str, prefix: str) -> list[str]:
    """프리픽스 아래 키를 페이지네이션으로 전부 모은다(중복 제거 없이)."""
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


# 아래 main()이 긴 것은 의도다 — scripts/는 절차형(단일 main·함수화 최소)이고
# ruff C901도 `scripts/**`에 면제돼 있다(pyproject.toml per-file-ignores).
def main() -> int:
    """사전 조건을 확인하고 P1~P5를 순서대로 실행한 뒤 종료코드를 낸다."""
    # ── 1) 인자 ──────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(
        description="S3 호환 스토리지 합격 스위트(P1~P5) — 실측 판정용",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help=(
            "S3 엔드포인트(scheme 포함). 미지정 시 ICEBERG_S3_ENDPOINT → "
            "S3_ENDPOINT_URL 순으로 환경에서 읽는다"
        ),
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("S3_PROBE_BUCKET", DEFAULT_BUCKET),
        help=(
            f"프로브 전용 버킷('{REQUIRED_BUCKET_MARKER}'를 포함해야 한다. "
            "실데이터 버킷은 거부된다)"
        ),
    )
    parser.add_argument(
        "--prefix",
        default=os.environ.get("S3_PROBE_PREFIX", DEFAULT_PREFIX),
        help=f"프로브 전용 프리픽스('{REQUIRED_PREFIX_MARKER}'를 포함해야 한다)",
    )
    parser.add_argument(
        "--only",
        default=",".join(PROBE_IDS),
        help="실행할 프로브 쉼표 목록(예: p1,p2)",
    )
    parser.add_argument(
        "--multipart-mb",
        type=int,
        default=DEFAULT_MULTIPART_MB,
        help="P2 멀티파트 페이로드 크기(MiB)",
    )
    parser.add_argument(
        "--large-mb",
        type=int,
        default=DEFAULT_LARGE_MB,
        help="P3 FileIO 대용량 페이로드 크기(MiB)",
    )
    parser.add_argument(
        "--no-create-bucket",
        action="store_true",
        help="프로브 버킷이 없어도 만들지 않는다(없으면 사전 조건 미충족)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="정리하지 않고 이번 실행이 만든 객체를 남긴다(사후 조사용)",
    )
    args = parser.parse_args()

    selected = [p.strip().lower() for p in args.only.split(",") if p.strip()]
    unknown = [p for p in selected if p not in PROBE_IDS]
    if unknown:
        print(f"❌ 알 수 없는 프로브: {', '.join(unknown)}")
        return EXIT_PRECONDITION

    print("=" * 72)
    print("S3 호환 스토리지 합격 스위트 — 관측 경로 생존이 종료코드다")
    print("=" * 72)
    print()

    # ── 2) 지연 import (--help는 의존성 없이도 떠야 한다) ────────────────
    try:
        import boto3
        from boto3.s3.transfer import TransferConfig
        from botocore.config import Config
        from botocore.exceptions import (
            AwsChunkedWrapperError,
            BotoCoreError,
            ClientError,
            EndpointConnectionError,
            FlexibleChecksumError,
        )
    except ModuleNotFoundError as exc:
        print(f"❌ 사전 조건 미충족 — boto3 부재({exc.name}). 전 프로브 미측정")
        print("   uv run scripts/storage_conformance_probe.py 로 실행한다")
        return EXIT_PRECONDITION
    # 🔴 **모듈은 있는데 심볼이 없는** 경우는 위 분기에 걸리지 않는다.
    #   `from botocore.exceptions import AwsChunkedWrapperError`가 실패하면
    #   맨 `ImportError`가 나는데 `ModuleNotFoundError`는 그 **하위**라
    #   최상위까지 뚫려 트레이스백 + 종료코드 1이 된다 — 이 파일이 "1은 쓰지
    #   않는다"고 선언한 계약을 깨는 경로다(실측: 파이썬 3.13).
    #   개연성은 낮지 않다 — PEP 723 하한(`boto3>=1.36`) 쪽 botocore에 위
    #   체크섬 예외 심볼이 없으면 그대로 이 경로다.
    #   `exc.name`은 심볼이 아니라 **모듈명**('botocore.exceptions')이라
    #   "boto3 부재"로 출력하면 오히려 오진을 부른다 ⇒ 타입명만 싣고
    #   무엇을 찾다 실패했는지는 **이 파일의 리터럴**로 말한다(§예외 출력 규칙).
    except ImportError as exc:
        print(f"❌ 사전 조건 미충족 — botocore 심볼 부재: {type(exc).__name__}")
        print("   필요: AwsChunkedWrapperError·BotoCoreError·ClientError·")
        print("         EndpointConnectionError·FlexibleChecksumError")
        print("   설치된 botocore가 PEP 723 하한 쪽이면 상한을 맞춰 다시 받는다")
        return EXIT_PRECONDITION

    # ── 3) .env 로드 (PEP 723 단독 실행 전제 — 기존 env가 이긴다) ────────
    env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, _, env_val = line.partition("=")
            env_key = env_key.strip()
            env_val = env_val.split(" #", 1)[0].strip().strip("'\"")
            if env_key:
                os.environ.setdefault(env_key, env_val)

    # ── 4) 좌표·자격증명 확정 (값은 절대 출력하지 않는다) ────────────────
    endpoint = (
        args.endpoint
        or os.environ.get("ICEBERG_S3_ENDPOINT")
        or os.environ.get("S3_ENDPOINT_URL")
    )
    if not endpoint:
        print("❌ 사전 조건 미충족 — 엔드포인트 미지정")
        print("   --endpoint 또는 ICEBERG_S3_ENDPOINT/S3_ENDPOINT_URL 필요")
        return EXIT_PRECONDITION

    access_key = os.environ.get("ICEBERG_S3_ACCESS_KEY") or os.environ.get(
        "AWS_ACCESS_KEY_ID"
    )
    secret_key = os.environ.get("ICEBERG_S3_SECRET_KEY") or os.environ.get(
        "AWS_SECRET_ACCESS_KEY"
    )
    if not access_key or not secret_key:
        print("❌ 사전 조건 미충족 — 자격증명 부재")
        print("   ICEBERG_S3_ACCESS_KEY/_SECRET_KEY 또는 AWS_* 를 설정한다")
        return EXIT_PRECONDITION
    creds = (access_key, secret_key)
    region = (
        os.environ.get("AWS_DEFAULT_REGION")
        or os.environ.get("AWS_REGION")
        or "us-east-1"
    )

    parsed_endpoint = urlparse(endpoint)
    scheme = parsed_endpoint.scheme or "http"
    netloc = parsed_endpoint.netloc or parsed_endpoint.path

    # ── 5) 안전 가드 ─────────────────────────────────────────────────────
    bucket = args.bucket.strip().strip("/")
    prefix_root = args.prefix.strip().strip("/")
    if not bucket:
        print("❌ 거부 — 버킷명이 비었다")
        return EXIT_PRECONDITION
    # 1차 — allowlist(마커 강제). 실 버킷 인벤토리를 몰라도 배제된다.
    if REQUIRED_BUCKET_MARKER not in bucket:
        print(f"❌ 거부 — 버킷명에 '{REQUIRED_BUCKET_MARKER}'가 없다: '{bucket}'")
        print("   프로브는 마커가 든 전용 버킷에만 쓴다(실 버킷 구조적 배제)")
        return EXIT_PRECONDITION
    # 2차 — denylist(이중 방어). 1차를 통과해도 실데이터 이름이면 거부한다.
    if bucket in FORBIDDEN_BUCKETS:
        print(f"❌ 거부 — '{bucket}'은 실데이터 버킷이다. 프로브 버킷을 쓴다")
        return EXIT_PRECONDITION
    if REQUIRED_PREFIX_MARKER not in prefix_root:
        print(
            f"❌ 거부 — 프리픽스에 '{REQUIRED_PREFIX_MARKER}'가 없다: '{prefix_root}'"
        )
        print("   구조적 오폭 방지 가드다(실데이터 경로에 쓰지 않기 위해)")
        return EXIT_PRECONDITION

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_prefix = f"{prefix_root}/{stamp}-{secrets.token_hex(3)}"

    print(f"▶ 엔드포인트   : {endpoint}  (scheme={scheme})")
    print(f"▶ 리전         : {region}")
    print("▶ 자격증명     : 설정됨(값은 출력하지 않는다)")
    print(f"▶ 쓰기 범위    : s3://{bucket}/{run_prefix}/  ← 이 아래에만 쓴다")
    print(f"▶ 실행 프로브  : {', '.join(selected)}")
    env_mode = os.environ.get("AWS_REQUEST_CHECKSUM_CALCULATION", "(미설정)")
    print(f"▶ 환경 체크섬  : AWS_REQUEST_CHECKSUM_CALCULATION={env_mode}")
    print()

    # ── 6) 버킷 확보 (삭제는 하지 않는다) ────────────────────────────────
    admin = make_s3_client(boto3, Config, endpoint, region, creds, None)
    try:
        admin.head_bucket(Bucket=bucket)
        print(f"▶ 버킷 '{bucket}' 존재 확인")
    except EndpointConnectionError as exc:
        print(f"❌ 사전 조건 미충족 — 엔드포인트 접속 실패: {type(exc).__name__}")
        print("   전 프로브 미측정(못 돈 것은 정보가 아니다)")
        return EXIT_PRECONDITION
    except ClientError:
        if args.no_create_bucket:
            print(f"❌ 사전 조건 미충족 — 버킷 '{bucket}' 부재(--no-create-bucket)")
            return EXIT_PRECONDITION
        try:
            admin.create_bucket(Bucket=bucket)
            print(f"▶ 버킷 '{bucket}' 생성함(프로브 전용)")
        except (ClientError, BotoCoreError) as exc2:
            print(f"❌ 사전 조건 미충족 — 버킷 생성 실패: {type(exc2).__name__}")
            return EXIT_PRECONDITION
    # 🔴 캐치올 — 위 `EndpointConnectionError`는 접속 실패의 **한 형제**일 뿐이다.
    #   botocore 1.43 실측 계층:
    #     BotoCoreError
    #      ├─ ConnectionError ─┬─ EndpointConnectionError  ← 위에서 잡는 것
    #      │                   ├─ SSLError                 ← 형제
    #      │                   └─ ConnectTimeoutError       ← 형제
    #      └─ HTTPClientError ── ReadTimeoutError            ← 형제
    #   하위가 아니라 **형제**라 이 셋은 위 분기를 통과해 최상위까지 뚫리고,
    #   그러면 트레이스백 + **종료코드 1**이 난다 — 이 파일이 모듈 독스트링에서
    #   "`1`은 쓰지 않는다"고 선언한 계약을 코드가 깨는 자리다.
    #   개연성이 낮지 않은 이유: P1은 https 엔드포인트를 요구하는데(트레일러
    #   체크섬이 https에서만 붙는다) 이 저장소의 로컬 TLS는 **로컬 CA 체인**이라
    #   신뢰 스토어에 CA가 없으면 **첫 head_bucket에서 SSLError**다 — 대조군
    #   실측을 시작하는 바로 그 순간이다.
    #   `ClientError`는 이 튜플에 넣지 않는다 — **BotoCoreError의 하위가 아니고**
    #   (실측) 위에서 이미 버킷 생성 경로로 분기해 여기 도달할 수 없다.
    except BotoCoreError as exc:
        print(f"❌ 사전 조건 미충족 — 엔드포인트 접속 실패: {type(exc).__name__}")
        print("   전 프로브 미측정(못 돈 것은 정보가 아니다)")
        print("   TLS면 CA 신뢰(로컬 CA 체인)를, 타임아웃이면 좌표를 확인한다")
        return EXIT_PRECONDITION
    print()

    # 전선 관측 버퍼 — PUT 요청 헤더를 화이트리스트로만 담는다.
    wire_log: list[dict[str, str]] = []

    def capture_wire(request: Any, **_kwargs: Any) -> None:
        """before-send 훅 — 체크섬 관련 헤더만 골라 담는다(키는 담지 않는다)."""
        headers = {k.lower(): v for k, v in dict(request.headers).items()}
        picked = {
            name: str(headers[name])
            for name in WIRE_HEADER_ALLOWLIST
            if name in headers
        }
        picked["__method"] = str(request.method)
        picked["__path"] = urlparse(str(request.url)).path
        wire_log.append(picked)

    # ── 7) P1 — SDK 기본 체크섬 강제 ─────────────────────────────────────
    p1_payload = secrets.token_bytes(SMALL_PAYLOAD_BYTES)
    p1_expectation = "현행 SeaweedFS에서 FAIL"
    p1_basis = (
        "SeaweedFS가 aws-chunked 프레이밍을 못 풀어 객체에 그대로 저장된다 — "
        "PASS면 스토어가 고쳐진 것이 아니라 프로브를 의심한다"
    )
    if "p1" in selected:
        try:
            wire_log.clear()
            forced = make_s3_client(
                boto3, Config, endpoint, region, creds, "when_supported"
            )
            forced.meta.events.register("before-send.s3", capture_wire)
            key_forced = f"{run_prefix}/p1/when_supported.bin"
            same_forced, detail_forced = put_get_roundtrip(
                forced, bucket, key_forced, p1_payload
            )

            # 🔴 덮어쓰기가 먹혔는지는 결과가 아니라 전선에서 판정한다.
            put_wires = [
                w
                for w in wire_log
                if w.get("__method") == "PUT" and w.get("__path", "").endswith(".bin")
            ]
            wire = put_wires[-1] if put_wires else {}
            enc = wire.get("content-encoding", "")
            if "aws-chunked" in enc and "x-amz-trailer" in wire:
                framing = FRAMING_TRAILER
            elif "x-amz-checksum-crc32" in wire or (
                "x-amz-sdk-checksum-algorithm" in wire
            ):
                framing = FRAMING_HEADER
            else:
                framing = FRAMING_NONE

            # 대조 — 저장소 전역 우회책(when_required)을 켠 같은 왕복.
            relaxed = make_s3_client(
                boto3, Config, endpoint, region, creds, "when_required"
            )
            key_relaxed = f"{run_prefix}/p1/when_required.bin"
            same_relaxed, detail_relaxed = put_get_roundtrip(
                relaxed, bucket, key_relaxed, p1_payload
            )

            wire_desc = ", ".join(
                f"{k}={v}" for k, v in sorted(wire.items()) if not k.startswith("__")
            )
            observed = (
                f"프레이밍={framing} [{wire_desc or '체크섬 헤더 없음'}] / "
                f"when_supported: {detail_forced} / "
                f"when_required: {detail_relaxed} / "
                f"두 모드 결과 {'갈림' if same_forced != same_relaxed else '동일'}"
            )

            if framing != FRAMING_TRAILER:
                # 축이 전선에서 성립하지 않았다 — 초록이 아니라 미측정이다.
                p1_verdict = VERDICT_UNMEASURED
                observed += (
                    " ⚠️ aws-chunked 트레일러가 전선에 없다. botocore는 "
                    "https 엔드포인트에서만 트레일러를 쓴다(1.43 실측) ⇒ "
                    "이 실행은 P1 축을 재지 못했다. https 엔드포인트로 다시 겨눈다"
                )
            else:
                p1_verdict = VERDICT_OK if same_forced else VERDICT_NG
                if same_forced and same_relaxed:
                    observed += " ⚠️ 두 모드가 갈리지 않았다 — 덮어쓰기 실효를 의심한다"
            EXTRA["p1_framing"] = framing
            EXTRA["p1_when_required"] = VERDICT_OK if same_relaxed else VERDICT_NG
        except EndpointConnectionError as exc:
            p1_verdict = VERDICT_UNMEASURED
            observed = f"접속 실패 — {type(exc).__name__}"
        # 🔴 이것은 미측정이 아니라 **측정된 실패**다. GET 응답의 체크섬이
        #   안 맞았다는 것은 곧 저장된 바이트가 쓴 바이트와 다르다는 뜻이고,
        #   그게 바로 P1이 겨냥한 축이다. Exception으로 뭉치면 찾던 실패가
        #   "못 돌았다"로 둔갑한다.
        except (FlexibleChecksumError, AwsChunkedWrapperError) as exc:
            p1_verdict = VERDICT_NG
            observed = f"무결성 검증 실패 — {type(exc).__name__}: {exc}"
        except ClientError as exc:
            p1_verdict = VERDICT_NG
            observed = f"스토어가 오류로 응답 — {exc.response['Error'].get('Code')}"
        # 예기치 않은 예외는 FAIL이 아니라 미측정이다(정보가 아니다).
        except Exception as exc:
            p1_verdict = VERDICT_UNMEASURED
            observed = f"예기치 않은 예외 — {type(exc).__name__}"
        record(
            "p1",
            "SDK 기본 체크섬(aws-chunked+트레일러) PUT → 바이트 무결성",
            p1_verdict,
            "PUT한 바이트와 GET한 바이트의 일치 여부 — 1건 "
            "(+ 전선 프레이밍 관측 1건, when_required 대조 1건)",
            p1_expectation,
            p1_basis,
            observed,
        )

    # ── 8) P2 — boto3 기본 설정 왕복(단일 + 멀티파트) ────────────────────
    if "p2" in selected:
        try:
            plain = make_s3_client(boto3, Config, endpoint, region, creds, None)
            effective = getattr(
                plain.meta.config, "request_checksum_calculation", "(미상)"
            )
            key_single = f"{run_prefix}/p2/single.bin"
            same_single, detail_single = put_get_roundtrip(
                plain, bucket, key_single, secrets.token_bytes(SMALL_PAYLOAD_BYTES)
            )

            mp_payload = secrets.token_bytes(args.multipart_mb * 1024 * 1024)
            mp_digest = hashlib.sha256(mp_payload).hexdigest()
            key_mp = f"{run_prefix}/p2/multipart.bin"
            transfer = TransferConfig(
                multipart_threshold=MULTIPART_MIN_PART_BYTES,
                multipart_chunksize=MULTIPART_MIN_PART_BYTES,
                max_concurrency=2,
            )
            plain.upload_fileobj(
                io.BytesIO(mp_payload), bucket, key_mp, Config=transfer
            )
            sink = io.BytesIO()
            plain.download_fileobj(bucket, key_mp, sink, Config=transfer)
            same_mp = hashlib.sha256(sink.getvalue()).hexdigest() == mp_digest

            p2_verdict = VERDICT_OK if (same_single and same_mp) else VERDICT_NG
            observed = (
                f"유효 체크섬 모드={effective} / 단일: {detail_single} / "
                f"멀티파트 {args.multipart_mb}MiB "
                f"({MULTIPART_MIN_PART_BYTES // (1024 * 1024)}MiB 파트): "
                f"sha256 {'일치' if same_mp else '불일치'}"
            )
        except EndpointConnectionError as exc:
            p2_verdict = VERDICT_UNMEASURED
            observed = f"접속 실패 — {type(exc).__name__}"
        # P1과 같은 이유로 무결성 위반은 FAIL이다(미측정 아님).
        except (FlexibleChecksumError, AwsChunkedWrapperError) as exc:
            p2_verdict = VERDICT_NG
            observed = f"무결성 검증 실패 — {type(exc).__name__}: {exc}"
        except ClientError as exc:
            p2_verdict = VERDICT_NG
            observed = f"스토어가 오류로 응답 — {exc.response['Error'].get('Code')}"
        except Exception as exc:
            p2_verdict = VERDICT_UNMEASURED
            observed = f"예기치 않은 예외 — {type(exc).__name__}"
        record(
            "p2",
            "boto3 기본 설정 PUT/GET 왕복(단일 + 멀티파트)",
            p2_verdict,
            "왕복 바이트 일치 — 단일 1건 + 멀티파트 1건 = 2건",
            "미상 (기대값을 적을 수 없다는 사실 자체가 관측 대상)",
            "barman-cloud(boto3)의 CNPG 백업이 PutObject InternalError로 "
            "실패 중인데 원인이 미규명이다 — 재현되면 같은 원인일 가능성이 "
            "생기고, 재현되지 않으면 barman 축은 별개로 남는다(등가 아님)",
            observed,
        )

    # ── 9) P3 — pyiceberg 테이블 왕복 + FileIO 대용량 ────────────────────
    tmp_dir: str | None = None
    if "p3" in selected:
        try:
            import pyarrow as pa
            from pyiceberg.catalog.sql import SqlCatalog
            from pyiceberg.io.pyarrow import PyArrowFileIO

            iceberg_props = {
                "s3.endpoint": endpoint,
                "s3.access-key-id": access_key,
                "s3.secret-access-key": secret_key,
                "s3.region": region,
            }
            tmp_dir = tempfile.mkdtemp(prefix="storage-conformance-")
            catalog = SqlCatalog(
                "probe",
                uri=f"sqlite:///{Path(tmp_dir) / 'catalog.db'}",
                warehouse=f"s3://{bucket}/{run_prefix}/iceberg",
                **iceberg_props,
            )
            catalog.create_namespace("probe_ns")
            arrow_tbl = pa.table({"id": [1, 2, 3, 4], "label": ["a", "b", "c", "d"]})
            table = catalog.create_table("probe_ns.roundtrip", schema=arrow_tbl.schema)
            table.append(arrow_tbl)
            back = table.scan().to_arrow()
            rows_ok = back.num_rows == arrow_tbl.num_rows
            labels_ok = sorted(back.column("label").to_pylist()) == [
                "a",
                "b",
                "c",
                "d",
            ]

            # FileIO 대용량 — 멀티파트 업로드 경로를 탄다.
            large_payload = secrets.token_bytes(args.large_mb * 1024 * 1024)
            large_digest = hashlib.sha256(large_payload).hexdigest()
            file_io = PyArrowFileIO(properties=iceberg_props)
            large_loc = f"s3://{bucket}/{run_prefix}/p3/large.bin"
            with file_io.new_output(large_loc).create(overwrite=True) as stream:
                stream.write(large_payload)
            with file_io.new_input(large_loc).open() as stream:
                read_back = stream.read()
            large_ok = hashlib.sha256(read_back).hexdigest() == large_digest

            p3_verdict = (
                VERDICT_OK if (rows_ok and labels_ok and large_ok) else VERDICT_NG
            )
            observed = (
                f"테이블 왕복 {back.num_rows}/{arrow_tbl.num_rows}행, "
                f"값 {'일치' if labels_ok else '불일치'} / "
                f"FileIO {args.large_mb}MiB sha256 "
                f"{'일치' if large_ok else '불일치'}"
            )
        except ModuleNotFoundError as exc:
            p3_verdict = VERDICT_UNMEASURED
            observed = f"의존성 부재 — {exc.name}"
        except EndpointConnectionError as exc:
            p3_verdict = VERDICT_UNMEASURED
            observed = f"접속 실패 — {type(exc).__name__}"
        except ClientError as exc:
            p3_verdict = VERDICT_NG
            observed = f"스토어가 오류로 응답 — {exc.response['Error'].get('Code')}"
        except Exception as exc:
            p3_verdict = VERDICT_UNMEASURED
            # 예외 본문(`{exc}`)은 싣지 않는다 — str()을 서드파티가 정하는데
            # 이 경로는 자격증명을 인자로 넘긴다(§예외 출력 규칙).
            observed = f"예기치 않은 예외 — {type(exc).__name__}"
        record(
            "p3",
            "pyiceberg 테이블 write→read + FileIO 대용량 멀티파트",
            p3_verdict,
            "테이블 왕복 행 수(4행 기대) 1건 + 대용량 바이트 sha256 일치 1건",
            "PASS",
            "정본 적재 경로다(metadata.json·parquet 쓰기 → 읽기). 이 경로의 "
            "손상이 2026-08-18 pyiceberg JSON 파싱 실패로 처음 드러났다 — "
            "FAIL이면 후보를 정본 경로에 못 올린다",
            observed,
        )

    # ── 10) P4 — warehouse 직접 나열(FS 재귀 ↔ ListObjectsV2) ────────────
    if "p4" in selected:
        try:
            from pyarrow.fs import FileSelector, FileType, S3FileSystem

            lister = make_s3_client(boto3, Config, endpoint, region, creds, None)
            api_keys = set(list_keys(lister, bucket, f"{run_prefix}/"))

            fs = S3FileSystem(
                access_key=access_key,
                secret_key=secret_key,
                region=region,
                endpoint_override=netloc,
                scheme=scheme,
                allow_bucket_creation=False,
                allow_bucket_deletion=False,
            )
            infos = fs.get_file_info(
                FileSelector(f"{bucket}/{run_prefix}", recursive=True)
            )
            fs_keys = {
                info.path.removeprefix(f"{bucket}/")
                for info in infos
                if info.type == FileType.File
            }

            delim = lister.list_objects_v2(
                Bucket=bucket, Prefix=f"{run_prefix}/", Delimiter="/"
            )
            common = [c["Prefix"] for c in delim.get("CommonPrefixes", [])]

            only_api = sorted(api_keys - fs_keys)
            only_fs = sorted(fs_keys - api_keys)
            sets_match = not only_api and not only_fs
            p4_verdict = VERDICT_OK if (sets_match and bool(common)) else VERDICT_NG
            observed = (
                f"ListObjectsV2 {len(api_keys)}키 ↔ FS 재귀 {len(fs_keys)}파일 "
                f"({'일치' if sets_match else '불일치'}) / "
                f"Delimiter='/' CommonPrefixes {len(common)}건"
            )
            if only_api:
                observed += f" / API에만 {len(only_api)}건(예: {only_api[0]})"
            if only_fs:
                observed += f" / FS에만 {len(only_fs)}건(예: {only_fs[0]})"
        except ModuleNotFoundError as exc:
            p4_verdict = VERDICT_UNMEASURED
            observed = f"의존성 부재 — {exc.name}"
        except EndpointConnectionError as exc:
            p4_verdict = VERDICT_UNMEASURED
            observed = f"접속 실패 — {type(exc).__name__}"
        except ClientError as exc:
            p4_verdict = VERDICT_NG
            observed = f"스토어가 오류로 응답 — {exc.response['Error'].get('Code')}"
        except Exception as exc:
            p4_verdict = VERDICT_UNMEASURED
            # 예외 본문(`{exc}`)은 싣지 않는다 — str()을 서드파티가 정하는데
            # 이 경로는 자격증명을 인자로 넘긴다(§예외 출력 규칙).
            observed = f"예기치 않은 예외 — {type(exc).__name__}"
        record(
            "p4",
            "warehouse 직접 나열 — FS 재귀 나열 ↔ ListObjectsV2 대조",
            p4_verdict,
            "같은 프리픽스를 두 경로로 세어 나온 두 수의 일치 — 1건 "
            "(+ 디렉터리성 CommonPrefixes 존재 1건)",
            "PASS",
            "remove_orphan_files는 카탈로그가 모르는 파일을 찾느라 FS 나열을 "
            "탄다 — 두 수가 갈리면 정리가 살아 있는 파일을 orphan으로 보거나 "
            "쓰레기를 못 본다. 🔴 단 여기서 재는 것은 pyarrow(aws-sdk-cpp)이며 "
            "JVM s3a가 아니다(미검사 축 2)",
            observed,
        )

    # ── 11) P5 — path-style · 페이지네이션 · 멀티파트 abort ──────────────
    if "p5" in selected:
        try:
            wire_log.clear()
            probe5 = make_s3_client(boto3, Config, endpoint, region, creds, None)
            probe5.meta.events.register("before-send.s3", capture_wire)

            page_prefix = f"{run_prefix}/p5/page/"
            for idx in range(PAGINATION_OBJECTS):
                probe5.put_object(
                    Bucket=bucket, Key=f"{page_prefix}{idx:03d}.bin", Body=b"x"
                )

            pages = 0
            seen: list[str] = []
            paginator = probe5.get_paginator("list_objects_v2")
            for page in paginator.paginate(
                Bucket=bucket,
                Prefix=page_prefix,
                PaginationConfig={"PageSize": PAGINATION_PAGE_SIZE},
            ):
                pages += 1
                seen.extend(obj["Key"] for obj in page.get("Contents", []))
            unique = len(set(seen))
            pagination_ok = (
                unique == PAGINATION_OBJECTS
                and len(seen) == PAGINATION_OBJECTS
                and pages > 1
            )

            # path-style: 요청 경로가 /<bucket>/... 로 시작해야 한다.
            paths = [w.get("__path", "") for w in wire_log]
            path_style_ok = bool(paths) and all(
                p.startswith((f"/{bucket}/", f"/{bucket}?")) for p in paths if p
            )

            # 멀티파트 abort — 완료하지 않고 버린 업로드가 정리되는가.
            abort_key = f"{run_prefix}/p5/aborted.bin"
            created = probe5.create_multipart_upload(Bucket=bucket, Key=abort_key)
            upload_id = created["UploadId"]
            probe5.upload_part(
                Bucket=bucket,
                Key=abort_key,
                PartNumber=1,
                UploadId=upload_id,
                Body=secrets.token_bytes(MULTIPART_MIN_PART_BYTES),
            )
            probe5.abort_multipart_upload(
                Bucket=bucket, Key=abort_key, UploadId=upload_id
            )
            listed = probe5.list_multipart_uploads(Bucket=bucket, Prefix=abort_key)
            lingering = [
                u for u in listed.get("Uploads", []) if u.get("UploadId") == upload_id
            ]
            try:
                probe5.head_object(Bucket=bucket, Key=abort_key)
                object_absent = False
            except ClientError:
                object_absent = True
            abort_ok = not lingering and object_absent

            p5_verdict = (
                VERDICT_OK
                if (pagination_ok and path_style_ok and abort_ok)
                else VERDICT_NG
            )
            observed = (
                f"페이지네이션: 고유 키 {unique}/{PAGINATION_OBJECTS}, "
                f"총 {len(seen)}건, 페이지 {pages}회 "
                f"({'OK' if pagination_ok else 'NG'}) / "
                f"path-style 요청 {len(paths)}건 "
                f"({'OK' if path_style_ok else 'NG'}) / "
                f"abort 후 잔존 업로드 {len(lingering)}건, "
                f"객체 {'없음' if object_absent else '남음'} "
                f"({'OK' if abort_ok else 'NG'})"
            )
        except EndpointConnectionError as exc:
            p5_verdict = VERDICT_UNMEASURED
            observed = f"접속 실패 — {type(exc).__name__}"
        except ClientError as exc:
            p5_verdict = VERDICT_NG
            observed = f"스토어가 오류로 응답 — {exc.response['Error'].get('Code')}"
        except Exception as exc:
            p5_verdict = VERDICT_UNMEASURED
            # 예외 본문(`{exc}`)은 싣지 않는다 — str()을 서드파티가 정하는데
            # 이 경로는 자격증명을 인자로 넘긴다(§예외 출력 규칙).
            observed = f"예기치 않은 예외 — {type(exc).__name__}"
        record(
            "p5",
            "path-style · ListObjectsV2 페이지네이션 · 멀티파트 abort",
            p5_verdict,
            f"페이지네이션이 돌려준 고유 키 수({PAGINATION_OBJECTS} 기대) 1건 "
            "+ path-style 요청 경로 1건 + abort 후 잔존 업로드 수(0 기대) 1건",
            "PASS",
            "S3 API 계약이라 구현이 지켜야 한다 — 조용히 갈리는 축이므로 "
            "후보 간 변별점이 된다(중복 키·무한 페이지·abort 미지원)",
            observed,
        )

    # ── 12) 정리 — 판정과 분리해 별도로 출력한다 ─────────────────────────
    print("─" * 72)
    if args.keep:
        cleanup_status = "SKIPPED"
        print(f"정리(cleanup): 생략(--keep) — s3://{bucket}/{run_prefix}/ 잔존")
    else:
        deleted = 0
        failed = 0
        try:
            cleaner = make_s3_client(boto3, Config, endpoint, region, creds, None)
            # 🔴 삭제 범위는 **서버 응답이 아니라 자기가 만든 문자열**로 정한다.
            #   `ListObjectsV2`가 `Prefix`를 지키는지가 바로 이 스위트의 검사
            #   대상이다(P5가 그 계약을, P4가 나열 정합을 잰다). "모른다"고
            #   전제하고 재면서 정리에서만 무조건 신뢰하면 순환이다 — 대상이
            #   신생 구현(RustFS·Garage)이라 전제가 깨질 개연성도 낮지 않다.
            scope = f"{run_prefix}/"
            out_of_scope = 0
            # 이번 실행이 만든 미완료 멀티파트도 함께 거둔다(best effort).
            try:
                pending = cleaner.list_multipart_uploads(Bucket=bucket, Prefix=scope)
                for upload in pending.get("Uploads", []):
                    if not upload["Key"].startswith(scope):
                        out_of_scope += 1
                        continue
                    cleaner.abort_multipart_upload(
                        Bucket=bucket,
                        Key=upload["Key"],
                        UploadId=upload["UploadId"],
                    )
            except (ClientError, BotoCoreError):
                failed += 1
            listed = list_keys(cleaner, bucket, scope)
            targets = [k for k in listed if k.startswith(scope)]
            out_of_scope += len(listed) - len(targets)
            # 걸러진 건수가 0이 아니면 그 자체가 **스토어 비준수의 추가
            # 관측점**이다(Prefix 계약 위반). 버리지 말고 정보로 남긴다.
            if out_of_scope:
                print(
                    f"   🔴 Prefix 계약 위반 관측 — 범위 밖 응답 {out_of_scope}건을 "
                    f"삭제 대상에서 제외했다(요청 Prefix='{scope}')"
                )
                print(
                    "      스토어가 ListObjectsV2/ListMultipartUploads의 Prefix를 "
                    "지키지 않았다는 뜻이다 — P4·P5 판정과 함께 읽는다"
                )
            for start in range(0, len(targets), 1000):
                batch = targets[start : start + 1000]
                resp = cleaner.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": k} for k in batch]},
                )
                deleted += len(resp.get("Deleted", []))
                failed += len(resp.get("Errors", []))
            # 잔존 집계도 같은 기준으로 센다 — 범위 밖 응답을 세면 "내 객체가
            # 남았다"로 오독된다(계측 단위를 맞춘다).
            left = list_keys(cleaner, bucket, scope)
            remaining = len([k for k in left if k.startswith(scope)])
            cleanup_status = "OK" if (remaining == 0 and failed == 0) else "PARTIAL"
            print(
                f"정리(cleanup): 대상 {len(targets)}건 / 삭제 {deleted}건 / "
                f"실패 {failed}건 / 잔존 {remaining}건 → {cleanup_status}"
            )
            if remaining:
                print(f"   🔴 수동 정리 필요: s3://{bucket}/{run_prefix}/")
        except Exception as exc:
            cleanup_status = VERDICT_UNMEASURED
            print(f"정리(cleanup): 실패 — {type(exc).__name__}")
            print(f"   🔴 수동 정리 필요: s3://{bucket}/{run_prefix}/")
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("   ※ 정리 결과는 종료코드에 반영하지 않는다 — 종료코드는 관측 경로")
    print("      생존만 센다. 정리 실패는 위 한 줄로 따로 읽는다")
    print()

    # ── 13) 요약 표 + 머신 파싱 한 줄 ────────────────────────────────────
    print("=" * 72)
    print(f"{'프로브':<6} {'판정':<8} 대상")
    print("-" * 72)
    for probe, title, verdict in SUMMARY:
        print(f"{probe.upper():<6} {verdict:<8} {title}")
    skipped = [p for p in PROBE_IDS if p not in selected]
    for probe in skipped:
        print(f"{probe.upper():<6} {'(미선택)':<8} --only에서 제외됨")
    print("=" * 72)

    parts = [
        f"{probe}={RESULTS.get(probe, VERDICT_UNMEASURED)}"
        for probe in PROBE_IDS
        if probe in selected
    ]
    parts.extend(f"{key}={value}" for key, value in sorted(EXTRA.items()))
    parts.append(f"cleanup={cleanup_status}")
    print("RESULT " + " ".join(parts))

    unmeasured = [
        probe
        for probe in selected
        if RESULTS.get(probe, VERDICT_UNMEASURED) == VERDICT_UNMEASURED
    ]
    if len(unmeasured) == len(selected):
        print(f"EXIT {EXIT_PRECONDITION} — 전 프로브 미측정(판정 불가)")
        return EXIT_PRECONDITION
    if unmeasured:
        print(
            f"EXIT {EXIT_SOME_UNMEASURED} — 미측정 {len(unmeasured)}건: "
            f"{', '.join(unmeasured)}"
        )
        return EXIT_SOME_UNMEASURED
    print(f"EXIT {EXIT_ALL_MEASURED} — 선택한 프로브가 전부 실행됨(FAIL도 정보다)")
    return EXIT_ALL_MEASURED


if __name__ == "__main__":
    sys.exit(main())
