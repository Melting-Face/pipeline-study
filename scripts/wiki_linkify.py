#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""`wiki/` 원본의 마크다운 링크를 GitHub 위키 형태로 변환한다.

사용법:
    python3 scripts/wiki_linkify.py <디렉터리>

왜 변환이 필요한가:
    같은 링크가 **두 곳에서 다르게 해석된다**.

    - 저장소: `[텍스트](other-page.md)` — `.md`가 실파일을 가리켜야
      `doc_lint --links`가 대상 존재를 검사할 수 있다.
    - 위키: 페이지 URL에 확장자가 없어 `.md`가 붙으면 404다.

    그래서 원본은 `.md`를 붙여 쓰고(검사 가능), 미러 단계에서 접미어만 뗀다.
    반대로 하면(원본에서 확장자 생략) 링크 검사기가 대상을 못 찾아
    **검사가 통째로 죽는다** — 통제를 잃는 쪽이 아니라 유지하는 쪽을 골랐다.

이 스크립트가 다루지 않는 것(빠뜨린 것과 구분하기 위해 명시한다):
    - 절대 URL(`http://`·`https://`·`mailto:`) — 저장소 밖을 가리키므로 그대로 둔다.
    - 순수 앵커(`#절-이름`) — 같은 페이지 안이라 변환 대상이 아니다.
    - 참조형 링크(`[x]: url`)와 HTML `<a href>` — 인라인 링크만 본다.
      위키 원본에서는 이 두 형태를 쓰지 않기로 했다(규약).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 인라인 마크다운 링크 중 **상대 경로이면서 `.md`로 끝나는 것**만 잡는다.
#   (?!...) — 절대 URL과 순수 앵커를 선행 제외한다.
#   그룹 1 = 확장자를 뗀 경로, 그룹 2 = 선택적 앵커(`#절-이름`).
LINK_PATTERN = re.compile(
    r"\]\((?!https?://|mailto:|#)([^)\s]+)\.md(#[^)\s]*)?\)",
)


def main() -> int:
    """대상 디렉터리의 `*.md`에서 `.md` 링크 접미어를 제거한다."""
    if len(sys.argv) != 2:
        print("사용법: wiki_linkify.py <디렉터리>", file=sys.stderr)
        return 2

    target_dir = Path(sys.argv[1])
    if not target_dir.is_dir():
        print(f"디렉터리가 아니다: {target_dir}", file=sys.stderr)
        return 2

    pages = sorted(target_dir.glob("*.md"))
    if not pages:
        # 🔴 빈 결과를 성공으로 읽지 않는다 — 복사 단계가 조용히 실패하면
        #    여기서 0건이 나오고 미러는 "정상 종료"한다. 그 형태를 막는다.
        print(f"변환 대상이 없다: {target_dir}/*.md", file=sys.stderr)
        return 1

    total_links = 0
    for page in pages:
        body = page.read_text(encoding="utf-8")
        converted, count = LINK_PATTERN.subn(
            lambda m: f"]({m.group(1)}{m.group(2) or ''})",
            body,
        )
        if count:
            page.write_text(converted, encoding="utf-8")
        total_links += count
        print(f"  {page.name}: 링크 {count}건 변환")

    print(f"위키 링크 변환 완료 — 페이지 {len(pages)}개 / 링크 {total_links}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
