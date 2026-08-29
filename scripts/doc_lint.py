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
    uv run scripts/doc_lint.py --tense            # 시제 어휘 진단(넓은 패턴 포함)

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
    - `tense-word`   : **매치 수**(한 줄에 두 어휘가 있으면 2건)
      🔴 기본 검사가 세는 것은 **잔여 0 등급뿐**이다. `--tense`는 넓은 패턴까지
         켜므로 **두 명령의 수치를 같은 단위로 비교하지 않는다.**

    ⇒ 총 위반 수는 "고쳐야 할 곳의 수"이지 "문제가 있는 문서 수"가 아니다.

검사에서 빼는 것(빠뜨린 것과 구분하기 위해 명시한다):
    - 펜스 코드 블록(``` … ```) 안 — 명령·설정 원문은 줄여 쓸 수 없다.
    - URL만 있는 줄 — 링크는 접을 수 없다.
    - 프론트매터(--- … ---) 안.
"""

from __future__ import annotations

import argparse
import os
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

# 관측·결정 일자는 문서에 두지 않는다(`docs/doc-sync.md` §실무 규칙 7 — 축 2).
#   수치와 전말은 볼트로, 열린 항목은 Issue로 가고 문서에는 인과와 처방만 남는다.
#   🔴 **출처 발행일은 예외다** — 낡지 않는 사실이고 `conventions/publishing.md` §3이
#      *"제목·링크·버전/날짜를 남긴다"* 로 요구한다. 그래서 판정 전에 링크의 **URL을
#      떼고 표시 텍스트만** 본다(인용 주소 안의 날짜를 위반으로 세지 않기 위함).
#   🔴 신설 시점에는 **기본 검사에 넣지 않고 `--dates`로만** 돌렸다. 기존 위반이
#      300건대라 기본에 넣으면 전부 빨간불이 되고, 고칠 수 없는 위반이 쌓이면 도구가
#      통째로 무시된다(URL 줄·표 행을 빼는 것과 같은 이유).
#      ⇒ 정리로 **0건**이 된 뒤 기본 검사에 편입했다.
#         `--dates`는 이 축만 따로 볼 때 쓴다.
#      🔴 **유입을 막는 게이트가 없으면 잔여를 0으로 만들어도 다시 는다** —
#         실제로 정리 중에 main 진행분으로 5건이 새로 들어왔다. 편입이 그 답이다.
OBSERVATION_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")

# 예외 — 출처 인용 문서와, 독자·통제가 다른 위키 산출물.
DATE_EXEMPT_FILES = ("docs/references.md",)
DATE_EXEMPT_DIRS = ("wiki",)

# 줄 단위 예외 — 외부 출처의 발행일·EOL·릴리스 날짜.
#   🔴 파일 단위 예외로는 이 축을 못 뺀다. 규칙 문서 한복판에 인용이 한 줄 끼어 있고
#      `conventions/publishing.md` §3이 *"제목·링크·버전/날짜"* 를 **요구**하므로,
#      그 줄의 날짜를 지우면 **다른 규칙을 어긴다.** 두 규칙이 실제로 충돌한 자리다.
#   🔴 **Rule of Three로 도입했다** — ① Spark Operator GA ② Promtail EOL(2곳).
#      한 건일 때는 문구를 고쳐 피했고(`k8s.md`), 두 번째에서 그 우회가 인용 규칙과
#      충돌해 더 못 미뤘다. **쓰이지 않을 예외 문법을 미리 만들지 않는다.**
#   ⚠️ 이 마커는 **면제이지 검증이 아니다** — 붙이면 그 줄은 아무도 안 본다.
#      관측 일자에 붙이면 조용히 통과하므로, **외부 출처에만** 쓴다.
DATE_OPT_OUT = "<!-- date-ok -->"

# 진행 상태를 말하는 **시제 어휘**도 문서에 두지 않는다(같은 §실무 규칙 7 — 축 2).
#   날짜 축과 **막는 것이 다르다** — 날짜는 *"언제 봤는가"*, 시제는 날짜 없이
#   저절로 낡는 *"아직 ~없다"* 를 잡는다. 후자는 `OBSERVATION_DATE_RE`의 관측 범위
#   밖이라, 그 검사가 0건이어도 이 축은 열린 채 남아 있었다.
#   🔴 **두 등급으로 가른다 — 기준은 「엄격함」이 아니라 「현재 잔여」다.**
#      날짜 축이 잔여 300건대로 배운 그대로다: 고칠 수 없는 위반이 쌓이면 도구가
#      통째로 무시된다. ⇒ **지금 잔여 0인 패턴만 기본 검사**에 넣어 유입 게이트로
#      바로 쓰고, 잔여가 있는 넓은 패턴은 `--tense` 진단 모드로만 돌린다.
#      잔여가 정리로 0이 되면 그때 기본으로 올린다(날짜 축이 밟은 경로).
#   🔴 파일·디렉터리 예외는 두지 않는다 — `DATE_EXEMPT_*`를 물려받지 않는다.
#      `wiki/`를 날짜 축에서 뺀 이유는 독자·통제가 다르다는 것인데, 시제 축은
#      **낡는 문장이 저장소 밖으로 나가는 것**을 막으므로 위키야말로 대상이다
#      (미러는 단방향이라 저장소 쪽에서 못 막으면 막을 곳이 없다).
#      잔여도 위키 포함 0건이라 넣는 비용이 없다.

# 잔여 0건 — 즉시 기본 검사에 편입한다. 상태 표지가 명시적이라 오탐이 낮다.
#   🔴 **`\b`를 쓰지 않는다 — 한글 앞뒤에서 서지 않는다.** 파이썬 `re`의 `\b`는
#      유니코드 워드 문자 기준이라 한글도 워드 문자다. 그래서 `\bTODO\b`는
#      `TODO를`·`TBD로`·`TODO다`를 **못 잡는데**, 한국어 문서에서는 그쪽이
#      오히려 자연스러운 표기다. 잔여가 0이라 증상이 없고 **미래 유입만 조용히
#      새는** 죽은 규칙이 된다(원칙 7 — "잡는다"와 "잡을 수 있다"는 다른 축).
#      ⇒ 경계를 **ASCII 문자로만** 세운다. 영문 연접(`TODOS`·`FIXMEs`)은 계속 제외다.
#      ⚠️ **`_`도 경계가 아니다 — 의도된 넓어짐이다.** `TODO_LIST` 같은 식별자가 잡힌다.
#         좁히지 마라: `_`를 경계에 넣으면 `TODO_`로 시작하는 **진짜 상태 표기가
#         조용히 샌다.** 게이트에서는 **시끄러운 오탐이 조용한 누락보다 낫다** —
#         오탐은 `<!-- tense-ok -->` 한 줄로 끝나지만 누락은 아무 증상이 없다.
#   🔴 **숫자는 경계에서 빼지 않는다 — 의도한 비대칭이다.** `TODO2`·`3TODO`는
#      잡히는 편이 맞다(TODO의 변형이지 다른 낱말이 아니다). 같은 파일의 날짜 축은
#      **반대로** 가야 한다(숫자 인접은 거기서 오탐이다). 두 축이 달라 보인다고
#      **"통일"하지 마라** — 경계 규칙은 축마다 무엇이 오탐인지에 따라 갈린다.
TENSE_STRICT_RES = (
    re.compile(r"(?<![A-Za-z])(TODO|TBD|FIXME)(?![A-Za-z])"),
    # 🔴 **한글 어휘에는 경계(`\b`)를 두르지 마라 — 두르면 조사·어미에서 샌다.**
    #    한국어는 낱말 뒤에 조사·어미가 그대로 이어 붙는데 `\b`는 그 자리에서
    #    서지 않는다: `\b아직까지\b`는 `아직까지 안 했다`는 잡고
    #    **`아직까지는`·`아직까지의`·`지금까지였다`를 놓친다**(실측 확인).
    #    ⇒ 여기서 `\b`가 없는 것은 **빠뜨린 것이 아니라 결정**이다. 오탐을 줄이려고
    #       나중에 두르면 위 형태들이 **에러 없이 조용히** 검사에서 빠진다.
    #    🔴 **날짜 축(`OBSERVATION_DATE_RE`)은 이 함정을 아직 밟고 있다** — 거기 `\b`는
    #       조사를 *처리하는* 것이 아니라 조사에서 *깨진다*(`2026-08-29에` 미검출).
    #       ⇒ **`--dates` 0건을 "낡는 날짜가 없다"로 읽지 마라.** 조사가 바로 붙은
    #          형태는 관측 범위 밖이다. 교정은 잔여 정리와 **한 커밋에** 묶어야 한다
    #          (따로 하면 그 사이 훅이 모든 문서 커밋을 막는다). 별도 과제다.
    re.compile(r"아직까지|지금까지"),
    re.compile(r"예정이다|할 예정|될 예정"),
)

# 잔여 있음 — `--tense`로만 돈다. 넓은 만큼 규칙 문서의 **자기 인용**과 부딪힌다
#   (아래 면제 마커). 🔴 **잔여 건수를 여기 적지 않는다** — 정리가 진행되면 저절로
#   거짓이 되고, 낡은 수치가 규칙 옆에 있으면 규칙까지 신뢰를 잃는다(축 2 그 자체).
#   지금 몇 건인지는 `--tense`를 돌려서 본다. 그것이 이 플래그의 용도다.
TENSE_DIAGNOSTIC_RES = (
    re.compile(r"아직[^.。\n]{0,20}?(없다|없고|없으|않다|않고|않으|못한다|못했|안 )"),
    re.compile(r"미해소"),
    re.compile(r"이번에"),
)

# 줄 단위 예외 — 규칙 문서가 **자기 규칙을 설명하려고 위반 예시를 인용**하는 자리.
#   `DATE_OPT_OUT`과 같은 구조의 규칙 충돌이다: 그 줄에서 시제 어휘를 지우면
#   "무엇이 위반인지"를 못 적어 **다른 규칙을 어긴다**(`general.md`의 상태 어휘 표,
#   `doc-sync.md`의 인용문이 실제 그 자리다).
#   ⚠️ 이 마커는 **면제이지 검증이 아니다** — 붙이면 그 줄은 아무도 안 본다.
#      진짜 상태 서술에 붙이면 조용히 통과하므로, **인용·예시에만** 쓴다.
TENSE_OPT_OUT = "<!-- tense-ok -->"

# 기본 검사 대상 — 사람과 AI가 함께 읽는 문서만. 벤더·생성물은 제외한다.
#   🔴 `wiki/`는 **저장소 밖으로 나가는 원본**이라 반드시 여기 있어야 한다.
#      GitHub 위키는 별도 저장소(`<repo>.wiki.git`)라 pre-commit 훅이 안 돈다.
#      위키를 다루는 방식(`wiki/` 원본 + CI 단방향 미러)의 **전제**가
#      "커밋 게이트를 상속한다"인데, 대상이 **명시 열거**라
#      여기 없으면 그 상속은 허구가 된다.
DEFAULT_TARGETS = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "docs",
    "notebooks/README.md",
    "wiki",
)

# 링크 검사는 **저장소 전역 1회**로 돈다.
#   🔴 분업하면 경계에 사각이 생긴다 — 2026-08-24 실측에서 두 관측자가 각자
#      `.claude/agents/**`(221건)와 `docs/**`를 검사해 **둘 다 0건**을 보고했는데
#      합집합에는 깨진 링크가 2건 있었다. 값이 아니라 **모집단**이 문제였다.
LINK_SCAN_DIRS = (
    "docs",
    ".claude/agents",
    ".claude/commands",
    ".codex/agents",
    "notebooks",
    # 🔴 `wiki/`의 링크는 **두 곳에서 다른 형태로 해석된다** — 저장소에서는 `.md`가
    #    붙은 실파일이고, 미러 단계에서 접미어가 제거돼 위키 페이지가 된다.
    #    여기서 검사하는 것은 **저장소 쪽 형태**뿐이다: `.md` 대상이 실재하는가.
    #    미러 후 위키에서 실제로 링크가 걸리는지는 이 검사기의 관측 범위 **밖**이다
    #    (그 축은 `.github/workflows/wiki.yml` 실행 후 사람이 본다).
    "wiki",
)
LINK_SCAN_FILES = ("README.md", "AGENTS.md", "CLAUDE.md")

# `.claude/skills/`는 **외부에서 설치한 벤더 콘텐츠**라 링크 검사에서 뺀다.
#   우리가 고칠 수 없고, 코드 예시의 제네릭 `[T](x: T)`가 링크로 오인된다.
#   🔴 **제외는 「검사 안 함」이지 「안전함」이 아니다** — 그 디렉터리에는
#      lock 밖 스킬이 실재하고 출처·검토를 거치지 않은 것이 섞여 있다.
#      그 축은 별도 통제(`skill_gate_guard.py`)가 본다.
EXCLUDE_PARTS = ("dbt_packages", "target", ".venv", "node_modules")

FENCE_RE = re.compile(r"^\s*(```|~~~)")
URL_ONLY_RE = re.compile(r"^\s*[-*>|\s]*\[?[^]]*\]?\(?https?://\S+\)?\s*[|]?\s*$")

# 괄호 중첩 검사는 **프로즈**를 재야 한다. 마크다운 문법이 만드는 괄호는 먼저 지운다.
#   🔴 안 지우면 `...다([`a.md`](a.md))` 한 줄이 중첩으로 잡힌다 — 링크 문법이
#      바깥 괄호 안에 들어간 것뿐인데 "한 문장에 두 생각"으로 오판된다.
#      오탐이 있는 게이트는 읽는 사람이 통째로 무시하게 되므로 규칙보다 먼저 고친다.
CODE_SPAN_RE = re.compile(r"`[^`]*`")
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\([^)]*\)")

# 링크는 **표시 텍스트만** 남기고 접는다.
#   독자가 보는 것은 `[텍스트](url)`이 아니라 `텍스트`다.
#   🔴 URL을 길이에 세면 긴 주소 하나가 줄을 위반으로 만드는데,
#      그건 줄바꿈으로 고쳐지지 않는다. `docs/security.md`는 GitHub
#      Security Policy로 렌더돼 **절대 URL이 의도된 선택**이라 더욱 그렇다.
#      고칠 수 없는 것을 위반이라 부르면 도구가 통째로 무시된다.
LINK_TEXT_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


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


def heading_slug(text: str) -> str:
    """제목을 GitHub 앵커 슬러그로 바꾼다.

    🔴 **구두점이 제거되면 그 자리의 공백은 합쳐지지 않고 각각 하이픈이 된다.**
    `4-3. 서비스 RBAC·최소권한 (2.5 · 2.6)` → `…rbac최소권한-25--26`
    (`·` 양옆 공백이 `--`가 된다). 이 규칙을 틀리면 **정상 링크를 위반으로
    잡아 "고치다가" 실제로 깨뜨린다.**
    """
    text = re.sub(r"[`*\[\]()]", "", text).strip().lower()
    text = "".join(c for c in text if c.isalnum() or c.isspace() or c in "-_")
    return re.sub(r"\s", "-", text)


def check_links(repo_root: Path) -> list[str]:
    """저장소 전역의 상대 링크와 앵커가 실재하는지 본다."""
    targets: list[Path] = [repo_root / f for f in LINK_SCAN_FILES]
    for d in LINK_SCAN_DIRS:
        targets.extend(sorted((repo_root / d).rglob("*.md")))
    docs = [
        f
        for f in targets
        if f.is_file() and not any(p in f.parts for p in EXCLUDE_PARTS)
    ]

    slugs: dict[Path, set[str]] = {}
    for f in docs:
        body = f.read_text(encoding="utf-8")
        slugs[f] = {
            heading_slug(m.group(2))
            for m in re.finditer(r"^(#{1,6})\s+(.*)$", body, re.MULTILINE)
        }

    findings: list[str] = []
    for f in docs:
        rel = f.relative_to(repo_root)
        # 🔴 펜스 코드 블록은 제외한다 — 코드의 제네릭·슬라이스가 링크로 오인된다.
        prose_lines, in_fence = [], False
        for line in f.read_text(encoding="utf-8").splitlines():
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if not in_fence:
                prose_lines.append(line)
        prose = "\n".join(prose_lines)
        link_re = r"\]\((?!https?:|mailto:)([^)#]*)(?:#([^)]+))?\)"
        for m in re.finditer(link_re, prose):
            path_part, anchor = m.group(1), m.group(2)
            target = (f.parent / path_part).resolve() if path_part else f.resolve()
            if path_part and not target.exists():
                findings.append(f"{rel}: dead-link {path_part} — 대상 파일이 없다")
                continue
            if anchor and target in slugs and anchor not in slugs[target]:
                findings.append(
                    f"{rel}: dead-anchor {path_part}#{anchor} — 그런 절이 없다"
                )
    return findings


def check_dates(files: list[Path], repo_root: Path) -> list[str]:
    """문서 본문에 **관측·결정 일자**가 남아 있는지 본다.

    잡는 것은 *"이 자리에 날짜가 있다"* 까지다. 그것이 관측 일자인지 출처 발행일인지
    기계는 가르지 못하므로 **예외를 파일 단위로** 둔다(`DATE_EXEMPT_*`).
    🔴 그래서 이 검사가 **0건이어도 「낡는 문장이 없다」는 뜻이 아니다** —
    날짜 없이 쓴 실측 수치(*"피크 84%"*)는 이 축의 관측 범위 **밖**이다.
    """
    findings: list[str] = []
    for path in files:
        rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path
        if rel.as_posix() in DATE_EXEMPT_FILES:
            continue
        if any(part in DATE_EXEMPT_DIRS for part in rel.parts):
            continue

        in_fence = False
        in_frontmatter = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
            if in_fence or DATE_OPT_OUT in line:
                continue
            # 링크는 표시 텍스트만 남긴다 — 인용 URL 안의 날짜는 출처 표기다.
            prose = LINK_TEXT_RE.sub(r"\1", line)
            findings.extend(
                f"{rel}:{lineno}: observation-date {m.group(0)} "
                f"— 일자는 볼트/Issue로 (doc-sync §실무 규칙 7 축 2)"
                for m in OBSERVATION_DATE_RE.finditer(prose)
            )
    return findings


def check_tense(
    files: list[Path], repo_root: Path, strict_only: bool = True
) -> list[str]:
    """문서 본문에 **진행 상태를 말하는 시제 어휘**가 남아 있는지 본다.

    `strict_only`가 참이면 **잔여 0인 패턴만**(기본 검사·유입 게이트),
    거짓이면 진단 패턴까지 함께 본다(`--tense`). 🔴 두 등급이 **한 함수·한 루프**를
    쓰는 것이 요점이다 — 스캔 규율을 복제하면 두 경로의 모집단이 갈린다.

    스캔 규율은 `check_dates`와 같다(프론트매터·펜스 제외, 링크는 표시 텍스트만).
    잡는 것은 *"이 자리에 상태 어휘가 있다"* 까지다. 그것이 실제 상태 서술인지
    규칙을 설명하는 인용인지 기계는 가르지 못하므로 **예외를 줄 단위로** 둔다.
    🔴 그래서 이 검사가 **0건이어도 「낡는 문장이 없다」는 뜻이 아니다** —
    어휘를 쓰지 않고 쓴 상태 서술(*"현재 3종만 등재"*)은 관측 범위 **밖**이다.
    """
    patterns = (
        TENSE_STRICT_RES if strict_only else TENSE_STRICT_RES + TENSE_DIAGNOSTIC_RES
    )
    findings: list[str] = []
    for path in files:
        rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path

        in_fence = False
        in_frontmatter = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
            if in_fence or TENSE_OPT_OUT in line:
                continue
            # 링크는 표시 텍스트만 남긴다 — URL 안의 `TODO` 같은 경로는 문장이 아니다.
            prose = LINK_TEXT_RE.sub(r"\1", line)
            findings.extend(
                f"{rel}:{lineno}: tense-word {m.group(0)} "
                f"— 상태는 볼트/Issue로 (doc-sync §실무 규칙 7 축 2)"
                for pattern in patterns
                for m in pattern.finditer(prose)
            )
    return findings


def check_vault_refs(repo_root: Path) -> list[str] | None:
    """`docs/`가 가리키는 볼트 파일·절이 실재하는지 본다.

    볼트는 **저장소 밖**이라 `check_links`가 보지 못한다. 상태 서술을 볼트로 옮긴
    뒤 `docs/`에는 볼트를 가리키는 참조만 남으므로, 이 축을 안 보면
    **규칙 문서가 없는 곳을 가리키는 상태**가 조용히 생긴다.

    🔴 **판정 방향을 뒤집는다 — 산문에서 §이름을 잘라내지 않는다.**
    `§운영 정책 미설정 항목.` 처럼 절 이름 뒤에 마침표·조사·괄호가 붙는데
    **어디까지가 이름인지 알려주는 경계가 없다**. 잘라내려 하면 꼬리가 딸려와
    실재하는 절을 "없다"고 판정한다(실제로 그렇게 오탐이 났다).
    그래서 **볼트의 제목 집합을 먼저 읽고, 인용문이 그 제목으로 시작하는지**를 본다.

    `$OBSIDIAN_VAULT`가 없으면 `None`을 돌려 **건너뛴다** — 개인 환경 의존성을
    게이트의 전제로 만들지 않는다. 🔴 호출부는 이 `None`을 **`0건`이 아니라
    「검사 안 함」으로** 출력해야 한다(안 본 것과 통과한 것은 다르다).
    """
    vault_env = os.environ.get("OBSIDIAN_VAULT", "")
    vault = Path(vault_env).expanduser() if vault_env else Path.home() / "obsidian"
    if not vault.is_dir():
        return None

    # 볼트 파일별 제목 집합 — 번호 접두(`## 5. 문서 · 도구 체계`)는 떼고 담는다.
    # 절은 **이름으로도 번호로도** 불린다(`§자원 실측 피크` · `§5에 있다`).
    # 번호만 쓰는 참조가 실제로 3건 있어, 이름 집합만 보면 정상 참조가 위반이 된다.
    headings: dict[Path, set[str]] = {}
    numbers: dict[Path, set[str]] = {}
    for f in sorted(vault.rglob("*.md")):
        titles, nums = set(), set()
        for m in re.finditer(
            r"^#{1,6}\s+(.*)$", f.read_text(encoding="utf-8"), re.MULTILINE
        ):
            title = m.group(1).strip()
            titles.add(title)
            if num_m := re.match(r"(\d+(?:[-.]\d+)*)\.\s*(.*)", title):
                nums.add(num_m.group(1))
                titles.add(num_m.group(2))
        headings[f], numbers[f] = titles, nums

    findings: list[str] = []
    ref_re = re.compile(r"\$OBSIDIAN_VAULT/([\w./-]+\.md)`?\s*(?:§\s*(.*))?")
    for doc in sorted((repo_root / "docs").rglob("*.md")):
        rel = doc.relative_to(repo_root)
        for num, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for m in ref_re.finditer(line):
                target = vault / m.group(1)
                if not target.is_file():
                    findings.append(f"{rel}:{num}: dead-vault {m.group(1)} — 파일 없음")
                    continue
                tail = (m.group(2) or "").strip()
                if not tail:
                    continue
                # 번호 참조는 뒤에 숫자가 안 이어질 때만 맞다(`§5`가 `§55`를 먹지 않게).
                by_num = any(
                    re.match(rf"{re.escape(n)}(?!\d)", tail) for n in numbers[target]
                )
                if by_num or any(tail.startswith(t) for t in headings[target] if t):
                    continue
                findings.append(
                    f"{rel}:{num}: dead-vault-section {m.group(1)} §{tail[:30]}"
                )
    return findings


def main() -> int:
    """대상 문서를 훑어 규약 위반을 출력하고, 위반이 있으면 종료코드 1을 낸다."""
    parser = argparse.ArgumentParser(description="마크다운 가독성 규약 검사")
    parser.add_argument(
        "paths", nargs="*", help="검사할 파일/디렉터리 (기본: 저장소 문서 전체)"
    )
    parser.add_argument("--summary", action="store_true", help="파일별 위반 수만 출력")
    parser.add_argument(
        "--links", action="store_true", help="저장소 전역 링크·앵커만 검사"
    )
    parser.add_argument(
        "--dates", action="store_true", help="관측·결정 일자 표기만 검사"
    )
    # 🔴 기본 검사는 **잔여 0 등급만** 본다. 이 플래그는 잔여가 있는 넓은 패턴까지
    #    켜는 **진단 모드**다 — 위반 수를 재는 용도이지 커밋 게이트가 아니다.
    parser.add_argument(
        "--tense", action="store_true", help="시제 어휘만 검사(진단 패턴 포함)"
    )
    args = parser.parse_args()

    if args.links:
        root = Path(__file__).resolve().parent.parent
        link_findings = check_links(root)
        for finding in link_findings:
            print(finding)
        print(f"\n링크 위반 {len(link_findings)}건", file=sys.stderr)

        vault_findings = check_vault_refs(root)
        if vault_findings is None:
            # 🔴 "0건"으로 적지 않는다 — 안 본 것과 통과한 것은 다른 상태다.
            print("볼트 참조: 검사 안 함 ($OBSIDIAN_VAULT 없음)", file=sys.stderr)
            return 1 if link_findings else 0
        for finding in vault_findings:
            print(finding)
        print(f"볼트 참조 위반 {len(vault_findings)}건", file=sys.stderr)
        return 1 if link_findings or vault_findings else 0

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

    if args.dates:
        date_findings = check_dates(files, repo_root)
        if not args.summary:
            for finding in date_findings:
                print(finding)
        else:
            counts: dict[str, int] = {}
            for finding in date_findings:
                counts[finding.split(":")[0]] = counts.get(finding.split(":")[0], 0) + 1
            for rel, count in sorted(counts.items(), key=lambda item: -item[1]):
                print(f"{count:5d}  {rel}")
        print(
            f"\n일자 표기 {len(date_findings)}건 / 문서 {len(files)}개", file=sys.stderr
        )
        return 1 if date_findings else 0

    if args.tense:
        # 진단 모드라 두 등급을 함께 본다(`strict_only=False`).
        tense_findings = check_tense(files, repo_root, strict_only=False)
        if not args.summary:
            for finding in tense_findings:
                print(finding)
        else:
            tense_counts: dict[str, int] = {}
            for finding in tense_findings:
                key = finding.split(":")[0]
                tense_counts[key] = tense_counts.get(key, 0) + 1
            for rel, count in sorted(tense_counts.items(), key=lambda item: -item[1]):
                print(f"{count:5d}  {rel}")
        print(
            f"\n시제 어휘 {len(tense_findings)}건 / 문서 {len(files)}개",
            file=sys.stderr,
        )
        return 1 if tense_findings else 0

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
        # 🔴 **펜스 안은 세지 않는다** — 위 docstring §검사에서 빼는 것이 선언한
        #    그대로다(선언만 있고 구현이 없어 펜스 안을 함께 세고 있었다).
        #    펜스 안은 워커 지시문에 복사되는 **단서 원문**·설정 예시라 이 문서에서
        #    줄일 수 없고, 고칠 수 없는 것을 위반이라 부르면 도구가 통째로 무시된다
        #    (URL 줄을 빼는 것과 같은 이유).
        # 🔴 아래 본 루프와 **별도 패스**다 — 본 루프는 줄 단위 위반을 `lineno`와 함께
        #    내지만 이 규칙은 문서당 1건이라 집계가 먼저 끝나야 한다. 펜스 판정은
        #    같은 `FENCE_RE`를 쓰므로 두 패스가 갈리지 않는다.
        # 관측 일자 — 줄 단위. 별도 함수를 재사용해 `--dates`와 **같은 판정**을 쓴다.
        #   🔴 여기서 로직을 복제하면 두 경로의 모집단이 갈린다
        #      (이 저장소가 반복해 데인 함정).
        findings.extend(check_dates([path], repo_root))
        # 시제 어휘 — 같은 이유로 함수를 재사용한다. **기본 검사는 잔여 0 등급만**
        #   본다(`strict_only`). 넓은 진단 패턴을 여기 넣으면 잔여가 즉시 빨간불이
        #   되고, 그 순간 이 도구는 통째로 무시되기 시작한다.
        findings.extend(check_tense([path], repo_root))

        marker_count = 0
        counting_fence = False
        for line in lines:
            if FENCE_RE.match(line):
                counting_fence = not counting_fence
                continue
            if not counting_fence:
                marker_count += line.count(EMPHASIS_MARKER)
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

            stripped = line.strip()
            is_table_row = stripped.startswith("|")

            # 줄 길이 — 🔴 표 행은 제외한다.
            #   마크다운 표 행은 **줄바꿈이 불가능**하다. 길다고 지적해도 고칠 방법이
            #   줄이는 것뿐인데 그건 아래 table-cell 규칙이 이미 본다. 두 규칙이 같은
            #   대상을 이중으로 세면 "고칠 수 없는 위반"이 쌓여 도구 전체가 무시된다.
            line_width = width(LINK_TEXT_RE.sub(r"\1", line))
            if not is_table_row and line_width > MAX_LINE_LENGTH:
                findings.append(
                    f"{rel}:{lineno}: line-length {line_width}열 "
                    f"(상한 {MAX_LINE_LENGTH})"
                )

            # 표 셀 — 구분행(| --- |)은 건너뛴다.
            if is_table_row and not re.fullmatch(r"[|\s:-]+", stripped):
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
