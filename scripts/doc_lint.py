#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""마크다운 문서의 **가독성 규약**을 기계로 검사한다.

사용법:
    uv run scripts/doc_lint.py                    # 저장소 전체(기본 대상)
    uv run scripts/doc_lint.py docs/setup.md      # 특정 파일
    uv run scripts/doc_lint.py --summary          # 파일별 위반 수만(진척 측정용)

왜 이 스크립트인가:
    `README.md`·`docs/`·`CLAUDE.md`는 **AI와 사람이 함께 읽는 문서**인데
    2026-08-23 실측에서 둘 다 막히는 상태였다 — `conventions/agents.md`의
    🔴가 238개(2,488줄이니 10줄마다 하나), `docs/README.md`의 가장 긴 줄이
    3,704자(목차 문서인데 한 셀이 A4 두 장).

    🔴 **수치가 없으면 "짧게 쓰자"는 다음 문서에서 조용히 깨진다.** 규약을
    문서에만 적어 두는 것과 기계가 재는 것은 다른 축이다(철학 원칙 7).

계측 단위(무엇을 세는가):
    - `line-length`  : **위반한 줄 수**
    - `table-cell`   : **위반한 셀 수**(한 줄에 여러 셀이면 여러 건)
    - `emphasis`     : **문서당 1건**(개수 초과 여부이지 🔴 총수가 아니다)
    - `file-length`  : **문서당 1건**
    - `nested-paren` : **위반한 줄 수**

    ⇒ 총 위반 수는 "고쳐야 할 곳의 수"이지 "문제가 있는 문서 수"가 아니다.

검사에서 빼는 것(빠뜨린 것과 구분하기 위해 명시한다):
    - 펜스 코드 블록(``` … ```) 안 — 명령·설정 원문은 줄여 쓸 수 없다.
    - URL만 있는 줄 — 링크는 접을 수 없다.
    - 프론트매터(--- … ---) 안.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

# --- 규약 상한 (정본은 docs/conventions/general.md §문서 작성 규약) ---
MAX_LINE_LENGTH = 120
MAX_TABLE_CELL_LENGTH = 200
MAX_EMPHASIS_MARKERS = 5
MAX_FILE_LINES = 500

EMPHASIS_MARKER = "🔴"

# 기본 검사 대상 — 사람과 AI가 함께 읽는 문서만. 벤더·생성물은 제외한다.
DEFAULT_TARGETS = ("CLAUDE.md", "README.md", "docs", "notebooks/README.md")
EXCLUDE_PARTS = ("dbt_packages", "target", ".venv", "node_modules")

FENCE_RE = re.compile(r"^\s*(```|~~~)")
URL_ONLY_RE = re.compile(r"^\s*[-*>|\s]*\[?[^]]*\]?\(?https?://\S+\)?\s*[|]?\s*$")

# 괄호 중첩 검사는 **프로즈**를 재야 한다. 마크다운 문법이 만드는 괄호는 먼저 지운다.
#   🔴 안 지우면 `...다([`a.md`](a.md))` 한 줄이 중첩으로 잡힌다 — 링크 문법이
#      바깥 괄호 안에 들어간 것뿐인데 "한 문장에 두 생각"으로 오판된다.
#      오탐이 있는 게이트는 읽는 사람이 통째로 무시하게 되므로 규칙보다 먼저 고친다.
CODE_SPAN_RE = re.compile(r"`[^`]*`")
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)")


def width(text: str) -> int:
    """문자열의 **표시 폭**(터미널 열 수)을 센다.

    🔴 계측 단위 함정 — 같은 줄이 셋 다 다른 값을 낸다:
        - `len(s)`          : **문자 수**. 한글 1  → 표시 폭의 약 절반으로 과소평가
        - `awk length($0)`  : **바이트 수**. 한글 3 → 약 1.5배로 과대평가
        - 이 함수           : **표시 폭**. 한글 2  → 가독성이 실제로 걸리는 단위

    2026-08-24 실측: `docs/README.md`가 `len()` 기준으로 0건이었는데
    120열을 넘는 줄이 12개 있었다. **값은 맞고 단위가 틀렸다** — 그래서
    검산을 통과하며 남았다(philosophy.md §계측 단위).
    """
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text
    )


def main() -> int:
    """대상 문서를 훑어 규약 위반을 출력하고, 위반이 있으면 종료코드 1을 낸다."""
    parser = argparse.ArgumentParser(description="마크다운 가독성 규약 검사")
    parser.add_argument(
        "paths", nargs="*", help="검사할 파일/디렉터리 (기본: 저장소 문서 전체)"
    )
    parser.add_argument("--summary", action="store_true", help="파일별 위반 수만 출력")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    targets = (
        [Path(p) for p in args.paths]
        if args.paths
        else [repo_root / t for t in DEFAULT_TARGETS]
    )

    # 대상 파일 수집 — 디렉터리는 재귀, 제외 경로는 걸러낸다.
    files: list[Path] = []
    for target in targets:
        path = target if target.is_absolute() else Path.cwd() / target
        if path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
        elif path.is_file():
            files.append(path)
    files = [f for f in files if not any(part in f.parts for part in EXCLUDE_PARTS)]

    if not files:
        print("검사 대상 없음", file=sys.stderr)
        return 2

    total = 0
    per_file: list[tuple[Path, int]] = []

    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path
        findings: list[str] = []

        # 문서 길이 — 문서당 1건
        if len(lines) > MAX_FILE_LINES:
            findings.append(
                f"{rel}: file-length {len(lines)}줄 "
                f"(상한 {MAX_FILE_LINES}) — 주제별 분할 대상"
            )

        # 강조 개수 — 문서당 1건. 개수 자체가 아니라 초과 여부를 센다.
        marker_count = sum(line.count(EMPHASIS_MARKER) for line in lines)
        if marker_count > MAX_EMPHASIS_MARKERS:
            findings.append(
                f"{rel}: emphasis {EMPHASIS_MARKER} {marker_count}개 "
                f"(상한 {MAX_EMPHASIS_MARKERS}) — 강조는 희소해야 작동한다"
            )

        in_fence = False
        in_frontmatter = False
        for lineno, line in enumerate(lines, start=1):
            # 프론트매터는 1행의 --- 로만 연다(본문 구분선과 구별).
            if lineno == 1 and line.strip() == "---":
                in_frontmatter = True
                continue
            if in_frontmatter:
                if line.strip() == "---":
                    in_frontmatter = False
                continue
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence or URL_ONLY_RE.match(line):
                continue

            line_width = width(line)
            if line_width > MAX_LINE_LENGTH:
                findings.append(
                    f"{rel}:{lineno}: line-length {line_width}열 "
                    f"(상한 {MAX_LINE_LENGTH})"
                )

            # 표 셀 — 구분행(| --- |)은 건너뛴다.
            stripped = line.strip()
            if stripped.startswith("|") and not re.fullmatch(r"[|\s:-]+", stripped):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                findings.extend(
                    f"{rel}:{lineno}: table-cell {width(cell)}열 "
                    f"(상한 {MAX_TABLE_CELL_LENGTH}) — 서술은 본문으로 내린다"
                    for cell in cells
                    if width(cell) > MAX_TABLE_CELL_LENGTH
                )

            # 괄호 중첩 — 코드 스팬과 링크 문법을 지운 뒤 깊이 2를 넘는지 본다.
            plain = MD_LINK_RE.sub("", CODE_SPAN_RE.sub("", line))
            depth = 0
            for char in plain:
                if char == "(":
                    depth += 1
                    if depth > 1:
                        findings.append(
                            f"{rel}:{lineno}: nested-paren — 한 문장에 한 생각"
                        )
                        break
                elif char == ")":
                    depth = max(0, depth - 1)

        per_file.append((rel, len(findings)))
        total += len(findings)
        if findings and not args.summary:
            for finding in findings:
                print(finding)

    if args.summary:
        for rel, count in sorted(per_file, key=lambda item: -item[1]):
            if count:
                print(f"{count:5d}  {rel}")

    print(f"\n위반 {total}건 / 문서 {len(files)}개", file=sys.stderr)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
