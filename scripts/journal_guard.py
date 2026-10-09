#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""저널 알림 — Claude Code·Codex SessionStart hook 진입점.

왜 이 스크립트인가:
    세션이 시작할 때 볼트를 훑어 **다음 미션 번호**, **열린 미션**, **WIP 상한 초과**를
    컨텍스트로 주입한다. 미션 규칙(미션 = PR 하나, WIP 상한 3)은
    `docs/conventions/agents.md`가 정본이고, 이 스크립트는 그 상태를 보여 줄 뿐
    아무것도 막지 않는다(통제가 아니라 알림).

    서브커맨드:
      session-start : 다음 NN·열린 미션·WIP 경고를 stdout으로 주입(exit 0)
      pre-write·stop: 철거된 모드. 갱신 전 배선으로 떠 있는 세션을 깨지 않도록
                      무출력으로 통과한다.

    볼트가 없는 환경(다른 머신·CI)에서는 조용히 통과한다.

사용: Claude Code·Codex의 SessionStart hook에서 호출한다.
    uv run --script scripts/journal_guard.py session-start
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 저장은 UTC·표시는 KST 정책에 따라 저널 날짜는 KST로 판정한다.
KST = timezone(timedelta(hours=9))

# 저널 파일명·하루 폴더명 규약 (docs/conventions/agents.md 정본)
JOURNAL_NAME_RE = re.compile(r"^(\d{2})-([a-z0-9][a-z0-9-]*)\.md$")
DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 미션이 아직 열려 있다고 보는 status 값
OPEN_STATUSES = ("planned", "in-progress", "blocked")

# 열린 미션 **목록 표시** 범위 — 날짜가 아니라 **날짜 폴더 수**다(저널이 없는 날은
# 폴더가 없어 7개가 7일보다 길 수 있다). WIP **집계**는 전체 폴더를 센다 —
# 오래 방치된 미션일수록 상한에 잡혀야 하기 때문이다.
RECENT_DAYS = 7

# 동시에 열어 둘 수 있는 미션 수 상한 (미션 규칙 3)
WIP_LIMIT = 3

# 런타임 출처는 경로가 아니라 frontmatter 태그(`runtime/<런타임>`)가 진다 —
# 경로로 가르면 같은 날 NN 수열이 두 갈래가 된다.
RUNTIMES = ("claude-code", "codex")


def resolve_runtime() -> str:
    """명시된 런타임을 우선하고, 없으면 실행 환경에서 판별한다."""
    configured = os.environ.get("JOURNAL_RUNTIME", "")
    if configured in RUNTIMES:
        return configured
    if os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    return "claude-code"


def resolve_journal_root() -> Path | None:
    """볼트의 `agents/` 경로. 볼트 부재면 None을 반환한다."""
    vault = os.environ.get("OBSIDIAN_VAULT") or "~/obsidian"
    root = Path(vault).expanduser() / "agents"
    return root if root.is_dir() else None


def scan_numbers(day_dir: Path) -> list[tuple[int, str]]:
    """하루 폴더의 `(NN, 파일명)` 목록. 규약 위반 파일명은 집계에서 제외한다."""
    found = []
    if not day_dir.is_dir():
        return found
    for path in sorted(day_dir.glob("*.md")):
        matched = JOURNAL_NAME_RE.match(path.name)
        if matched:
            found.append((int(matched.group(1)), path.name))
    return found


def read_frontmatter(path: Path) -> dict[str, str]:
    """저널 frontmatter를 dict로 파싱. 값의 `#` 주석과 따옴표는 떼어낸다."""
    data: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return data
    if not lines or lines[0].strip() != "---":
        return data
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line.startswith((" ", "\t", "#")):
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.split("#")[0].strip().strip("\"'")
    return data


def main() -> None:
    """SessionStart 알림을 출력한다. 철거된 모드는 무출력 통과한다."""
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in ("pre-write", "stop"):
        sys.exit(0)
    if command != "session-start":
        print(f"알 수 없는 서브커맨드: {command!r}", file=sys.stderr)
        sys.exit(1)

    root = resolve_journal_root()
    if root is None:
        sys.exit(0)  # 볼트 없는 환경 — 조용히 통과

    today = datetime.now(tz=KST).strftime("%Y-%m-%d")
    today_dir = root / today
    numbers = scan_numbers(today_dir)
    next_nn = f"{(max(n for n, _ in numbers) + 1) if numbers else 1:02d}"

    # 최신 폴더부터 훑어 전체를 WIP로 세고, 앞의 RECENT_DAYS개 폴더만 목록에 싣는다.
    day_dirs = [
        d
        for d in sorted(root.glob("????-??-??"), reverse=True)
        if DAY_DIR_RE.match(d.name)
    ]
    open_count = 0
    open_missions = []
    for index, day_dir in enumerate(day_dirs):
        for _, name in scan_numbers(day_dir):
            status = read_frontmatter(day_dir / name).get("status", "")
            if status not in OPEN_STATUSES:
                continue
            open_count += 1
            if index < RECENT_DAYS:
                open_missions.append(f"{day_dir.name}/{name[:-3]} ({status})")

    runtime = resolve_runtime()
    print(f"[저널 가드] 볼트 {root}")
    print(f"- 런타임: `{runtime}` · 태그: `runtime/{runtime}`")
    print(
        f"- 오늘({today}) 다음 미션 번호: **{next_nn}** → "
        f"`{today_dir}/{next_nn}-<mission-slug>.md`"
    )
    if numbers:
        print(f"- 오늘 기존 저널: {', '.join(name[:-3] for _, name in numbers)}")
    if open_missions:
        print(
            f"- 열린 미션(최근 날짜 폴더 {RECENT_DAYS}개): {' / '.join(open_missions)}"
        )
    if open_count > WIP_LIMIT:
        print(
            f"- ⚠️ WIP {open_count}/{WIP_LIMIT} — 새 미션을 열기 전에 "
            "열린 미션을 닫거나 정리한다"
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
