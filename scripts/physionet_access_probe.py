#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""PhysioNet 원천 접근 방식을 **실측으로 판정**한다(수집 자산의 선행 관문).

왜 이 스크립트인가:
    `defs/<dataset>/raw_assets.py`의 수집 경로는 세 가지 외부 사실 위에 서 있는데
    저장소 안에서는 **어느 것도 확인할 수 없다**.
        ① 파일 접근에 HTTP Basic이 통하는가 (아니면 세션 로그인인가)
        ② 버전 루트에 `SHA256SUMS.txt`가 있고 형식이 `<64hex>  <상대경로>`인가
        ③ 프로젝트 슬러그·버전이 코드의 상수와 맞는가
    셋 다 **추정**이라 코드를 믿고 3.3GB를 받기 전에 작은 파일로 먼저 묻는다.

🔴 **음성 대조(①)가 이 스크립트의 핵심이다.**
    인증을 걸고 200이 왔다는 사실만으로는 *인증이 통과했다*를 증명하지 못한다 —
    그 파일이 애초에 **공개**라면 크리덴셜이 틀려도 200이 온다. 그래서 먼저
    **인증 없이** 같은 URL을 때려 거부되는지 본다. 거부되지 않으면 이 프로브는
    "통과"가 아니라 **판정 불가**(exit 2)다.

🔴 **가장 비싼 실패 모드는 401이 아니라 「200 + 로그인 HTML」이다.**
    세션 기반 사이트는 미인증 요청에 로그인 페이지를 200으로 준다. 상태코드만
    보면 전부 통과하고, `chartevents.csv.gz`라는 이름의 HTML이 S3에 올라가
    몇 시간 뒤 적재 자산의 파싱에서 터진다. ③④가 그 축을 본다.

종료코드 (docs/test.md §5-1·§5-3 관문과 같은 의미):
    0  Basic 통과 — `common/physionet.py` 설계대로 진행
    1  Basic 거부 — 세션 로그인 분기가 필요(PhysioNetResource.session()만 바뀐다)
    2  판정 불가 — 크리덴셜 부재·네트워크 실패·**음성 대조 실패**
       ⚠️ `2`를 `1`로 읽지 마라. 못 붙은 것을 회귀로 읽으면 오진이고,
          통과로 읽으면 **관측 경로가 죽은 채 초록**이 된다.

실행 (의존성은 위 PEP 723 블록 — uv가 자동 provisioning):
    uv run scripts/physionet_access_probe.py                    # .env의 PHYSIONET_*
    uv run scripts/physionet_access_probe.py --project mimiciv --version 3.1

🔴 **디스크에 아무것도 쓰지 않는다.** 원천 데이터를 로컬에 남기지 않기 위해
    메모리에서 해시한 뒤 버린다(DUA — 원천 비공개).

스타일: 스크립트 컨벤션(docs/conventions/python.md)에 따라 절차형으로 쓴다.
    선언(상수)은 상단, 진입은 하단. 보조 함수로 쪼개지 않고 main에서 위→아래.
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

BASE_URL = "https://physionet.org/files"
SHA256SUMS_FILENAME = "SHA256SUMS.txt"
GZIP_MAGIC = b"\x1f\x8b"
SHA256_HEX_LEN = 64
TIMEOUT_S = 60

# 기본 프로브 대상 — **작은 파일**이어야 한다(전량을 메모리에 올려 해시한다).
# d_items는 ICU 항목 사전이라 이벤트 테이블보다 몇 자릿수 작다.
DEFAULT_PROJECT = "mimiciv"
DEFAULT_VERSION = "3.1"
DEFAULT_REL_PATH = "icu/d_items.csv.gz"

EXIT_OK = 0
EXIT_BASIC_REJECTED = 1
EXIT_UNDETERMINED = 2


def main() -> int:
    """프로브를 순서대로 실행하고 판정 종료코드를 돌려준다."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--rel-path", default=DEFAULT_REL_PATH)
    args = parser.parse_args()

    # ── 크리덴셜 로드 (.env 직접 파싱 — PEP 723 단독 실행 전제) ──────────
    username = os.environ.get("PHYSIONET_USERNAME", "")
    password = os.environ.get("PHYSIONET_PASSWORD", "")
    env_path = REPO_ROOT / ".env"
    if (not username or not password) and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            # 인라인 주석(`VALUE   # 설명`)을 벗긴다 — .env.example 형식이 그렇다.
            value = value.split("#", 1)[0].strip().strip("\"'")
            if key.strip() == "PHYSIONET_USERNAME" and not username:
                username = value
            elif key.strip() == "PHYSIONET_PASSWORD" and not password:
                password = value

    if not username or not password:
        print("✗ PHYSIONET_USERNAME/PASSWORD가 없다(.env 또는 환경변수).")
        print("  → 판정 불가. 값을 채운 뒤 다시 실행한다.")
        return EXIT_UNDETERMINED

    file_url = f"{BASE_URL}/{args.project}/{args.version}/{args.rel_path}"
    sums_url = f"{BASE_URL}/{args.project}/{args.version}/{SHA256SUMS_FILENAME}"
    print(f"대상: {file_url}")
    print(f"계정: {username[:2]}***  (값은 출력하지 않는다)\n")

    # ── ① 음성 대조: 인증 없이 거부되는가 ────────────────────────────────
    # 이것이 통과해야 ②의 200이 "인증이 통했다"를 의미한다.
    print("① 음성 대조 — 인증 없이 요청")
    try:
        anon = requests.get(file_url, stream=True, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        print(f"  ✗ 네트워크 실패: {type(exc).__name__} → 판정 불가")
        return EXIT_UNDETERMINED
    anon_type = anon.headers.get("Content-Type", "")
    anon_head = anon.raw.read(2) if anon.ok else b""
    anon.close()
    if anon.status_code in (401, 403):
        print(f"  ✓ {anon.status_code} — 보호된 파일이다")
    elif anon.ok and not anon_head.startswith(GZIP_MAGIC):
        print(f"  ✓ {anon.status_code}이나 gzip이 아니다({anon_type}) — 로그인 면")
    elif anon.ok:
        print(f"  ✗ {anon.status_code} + gzip — 이 파일은 **공개**다.")
        print("  → ②가 200이어도 인증을 증명 못한다. 보호된 파일로 프로브하라.")
        return EXIT_UNDETERMINED
    else:
        print(f"  ? {anon.status_code} — 예상 밖. 판정 불가")
        return EXIT_UNDETERMINED

    # ── ② Basic 인증 ────────────────────────────────────────────────────
    print("\n② HTTP Basic 인증")
    session = requests.Session()
    session.auth = (username, password)
    # 🔴 identity — `.csv.gz`에 Content-Encoding: gzip이 붙으면 urllib3가 한 번
    #    풀어버려 SHA256SUMS와의 바이트 동일성이 깨진다.
    session.headers.update({"Accept-Encoding": "identity"})
    try:
        response = session.get(file_url, stream=True, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        print(f"  ✗ 네트워크 실패: {type(exc).__name__} → 판정 불가")
        return EXIT_UNDETERMINED
    if response.status_code in (401, 403):
        print(f"  ✗ {response.status_code} — Basic이 거부됐다")
        print("  → 세션 로그인 분기가 필요하다(PhysioNetResource.session()만 바뀐다)")
        response.close()
        return EXIT_BASIC_REJECTED
    if not response.ok:
        print(f"  ✗ {response.status_code} → 판정 불가")
        response.close()
        return EXIT_UNDETERMINED
    print(f"  ✓ {response.status_code}")

    # ── ③ 최종 URL·Content-Type ─────────────────────────────────────────
    print("\n③ 리다이렉트·Content-Type")
    content_type = response.headers.get("Content-Type", "")
    print(f"  최종 URL : {response.url}")
    print(f"  타입     : {content_type or '(없음)'}")
    if "/login" in response.url or content_type.lower().startswith("text/html"):
        print("  ✗ 로그인 페이지로 보인다 — Basic이 실질 거부됐다")
        response.close()
        return EXIT_BASIC_REJECTED
    print("  ✓ 로그인 페이지가 아니다")

    # ── ④ 매직바이트 + 해시 (디스크에 쓰지 않는다) ──────────────────────
    print("\n④ 본문 검사")
    # 🔴 `response.iter_content()`를 쓰지 않는다 — requests 내부가
    #    `raw.stream(..., decode_content=True)`를 **강제**해서 여기서 설정한
    #    `raw.decode_content = False`를 덮어쓴다. 서버가 `.csv.gz`에
    #    `Content-Encoding: gzip`을 잘못 붙이면 본문이 한 번 풀려 **해시가
    #    SHA256SUMS와 영원히 어긋난다** — 프로브의 목적이 바로 그 대조라
    #    와이어 바이트를 그대로 봐야 한다. `raw`를 직접 읽는다.
    response.raw.decode_content = False
    hasher = hashlib.sha256()
    total = 0
    first = b""
    while True:
        chunk = response.raw.read(1024 * 1024)
        if not chunk:
            break
        if not first:
            first = chunk[:2]
        hasher.update(chunk)
        total += len(chunk)
    response.close()
    if not first.startswith(GZIP_MAGIC):
        print(f"  ✗ gzip 매직바이트 불일치(선두 {first!r})")
        return EXIT_BASIC_REJECTED
    digest = hasher.hexdigest()
    print(f"  ✓ gzip · {total:,} bytes · sha256={digest[:16]}…")

    # ── ⑤ SHA256SUMS 존재·형식·대조 ─────────────────────────────────────
    print("\n⑤ 무결성 정본(SHA256SUMS.txt)")
    try:
        sums_response = session.get(sums_url, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        print(f"  ? 네트워크 실패: {type(exc).__name__}")
        sums_response = None
    if sums_response is None or not sums_response.ok:
        code = sums_response.status_code if sums_response is not None else "N/A"
        print(f"  ⚠ 받지 못했다({code}) — 무결성 판정 축이 없다.")
        print("    수집 자산은 매 실행 재수신한다(downloaded_manifest_missing).")
        print("    → 코드는 이 상태를 견딘다. 다만 비싸다.")
        print(f"\n{'=' * 60}\n✓ Basic 통과 (무결성 정본은 미확인)")
        return EXIT_OK

    sums = {}
    for line in sums_response.text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        line_digest, _, path = stripped.partition(" ")
        path = path.strip().lstrip("*")
        if len(line_digest) == SHA256_HEX_LEN and path:
            sums[path] = line_digest.lower()
    print(f"  ✓ 항목 {len(sums)}개")

    expected = sums.get(args.rel_path)
    if expected is None:
        print(f"  ⚠ '{args.rel_path}'가 목록에 없다 — 경로 표기가 다를 수 있다.")
        sample = list(sums)[:3]
        print(f"    목록 예: {sample}")
        print("    → 코드의 상대경로 상수가 이 표기와 맞는지 확인하라.")
    elif expected == digest:
        print(f"  ✓ 해시 일치 — {expected[:16]}…")
    else:
        print(f"  ✗ 해시 불일치 — 기대 {expected[:16]}… / 실제 {digest[:16]}…")
        print("    → 전송 중 변형 가능성(Content-Encoding 자동 해제 등)")
        return EXIT_UNDETERMINED

    print(f"\n{'=' * 60}")
    print("✓ Basic 통과 — common/physionet.py 설계대로 진행 가능")
    print(f"  확정: project={args.project} version={args.version}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
