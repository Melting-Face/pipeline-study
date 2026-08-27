#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""저널 정합성 가드 — Claude Code·Codex hook 진입점.

왜 이 스크립트인가:
    저널 파일명 `NN-<mission>.md`의 NN은 "그날 착수 순번"인데, 각 세션이 저마다
    `ls`로 번호를 세어 잡는다. 2026-08-17 23:0x에 병렬 세션 둘이 서로를 모른 채
    같은 `11`을 점유해 넘버링이 깨졌다(→ 11·12번으로 정정). 규약 문서로는 이
    경합을 막을 수 없다 — 문서는 각 세션의 컨텍스트 안에만 있고, 파일시스템은
    하나이기 때문이다. 그래서 번호 발급·중복 차단을 파일시스템 기준으로 옮긴다.

    서브커맨드는 hook 이벤트와 1:1 대응한다:
      session-start : 다음 NN·미완 미션을 stdout으로 컨텍스트 주입(exit 0)
      pre-write     : 볼트 저널 신규 생성 시 NN 중복·파일명 규칙 위반을 차단
      stop          : 현재 세션 변경에 대응하는 오늘자 저널이 없으면 보정 요청

    실패해도 작업을 막지 않는다(fail-open). 볼트가 없는 환경(다른 머신·CI)에서는
    조용히 통과한다 — 가드가 개인 환경 의존성을 세션의 전제조건으로 만들면 안 된다.

사용: Claude Code·Codex의 lifecycle hook에서 호출한다.
    uv run --script scripts/journal_guard.py <session-start|pre-write|stop>
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

# 저장은 UTC·표시는 KST 정책에 따라 저널 날짜·시각은 KST로 판정한다.
KST = timezone(timedelta(hours=9))

# 저널 파일명·하루 폴더명 규약 (docs/conventions/agents.md 정본)
JOURNAL_NAME_RE = re.compile(r"^(\d{2})-([a-z0-9][a-z0-9-]*)\.md$")
DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# `_MOC.md`·`_TEMPLATE.md`처럼 밑줄로 시작하는 볼트 관리 파일은 넘버링 대상이 아니다.
EXEMPT_PREFIX = "_"

# 미션이 아직 열려 있다고 보는 status 값
OPEN_STATUSES = ("planned", "in-progress", "blocked")

# session-start가 훑는 과거 폴더 수 (열린 미션 상기용 — 전체 스캔은 과하다)
RECENT_DAYS = 7

# 두 런타임 모두 `agents/<날짜>/`에 바로 쌓는다 — 하루치 기록이 한 폴더에 모여야
# 사용자가 날짜로 탐색할 수 있고, NN도 하루 단위 단일 수열이 된다.
# 🔴 런타임 출처는 **경로가 아니라 frontmatter 태그**(`runtime/<런타임>`)가 진다.
#    경로로 가르면 같은 날 기록이 두 곳으로 쪼개져 그날 전체를 한 번에 못 본다.
#    ⚠️ 가르면 NN 수열도 갈려 **같은 날 같은 번호가 두 개** 생긴다(2026-08-27 실발생).
# 값이 빈 문자열이면 평탄 경로다. 표를 남겨 두는 이유는 런타임이 늘 때
# **경로를 가를지 태그로 가를지**를 이 자리에서 한 번 더 결정하게 하려는 것이다.
RUNTIME_DIRS = {
    "claude-code": "",
    "codex": "",
}


def resolve_runtime() -> str:
    """명시된 런타임을 우선하고, 없으면 실행 환경에서 판별한다."""
    configured = os.environ.get("JOURNAL_RUNTIME", "")
    if configured in RUNTIME_DIRS:
        return configured
    if os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    return "claude-code"


def resolve_agents_root() -> Path | None:
    """볼트의 공용 `agents/` 경로. 볼트 부재면 None을 반환한다."""
    vault = os.environ.get("OBSIDIAN_VAULT") or "~/obsidian"
    root = Path(vault).expanduser() / "agents"
    return root if root.is_dir() else None


def resolve_journal_root() -> Path | None:
    """현재 런타임의 신규 저널 루트. 공용 볼트 부재면 None을 반환한다."""
    agents_root = resolve_agents_root()
    if agents_root is None:
        return None
    runtime_dir = RUNTIME_DIRS[resolve_runtime()]
    return agents_root / runtime_dir if runtime_dir else agents_root


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


def resolve_project_root(payload: dict[str, object]) -> Path | None:
    """Hook cwd에서 Git 프로젝트 루트를 찾는다."""
    raw_cwd = payload.get("cwd")
    cwd = Path(raw_cwd if isinstance(raw_cwd, str) and raw_cwd else os.getcwd())
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd).resolve()
    while root.parent != root and not (root / ".git").exists():
        root = root.parent
    return root if (root / ".git").exists() else None


def resolve_session_id(payload: dict[str, object]) -> str:
    """Hook 입력과 런타임 환경에서 현재 세션 ID를 찾는다."""
    raw_session_id = payload.get("session_id")
    if isinstance(raw_session_id, str) and raw_session_id:
        return raw_session_id
    codex_session_id = os.environ.get("CODEX_SESSION_ID")
    claude_session_id = os.environ.get("CLAUDE_SESSION_ID")
    return codex_session_id or claude_session_id or ""


def run_git(root: Path, *args: str) -> str | None:
    """Git 읽기 명령을 실행하고 실패하면 None을 반환한다."""
    git_bin = shutil.which("git")
    if git_bin is None:
        return None
    result = subprocess.run(  # noqa: S603
        [git_bin, "-C", str(root), *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=5,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def git_change_fingerprint(root: Path) -> str | None:
    """변경 내용을 저장하지 않고 추적 diff와 미추적 파일 상태를 해시한다."""
    git_bin = shutil.which("git")
    if git_bin is None:
        return None
    diff = subprocess.run(  # noqa: S603
        [git_bin, "-C", str(root), "diff", "--no-ext-diff", "--binary", "HEAD"],
        capture_output=True,
        timeout=5,
        check=False,
    )
    untracked = run_git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if diff.returncode != 0 or untracked is None:
        return None

    digest = hashlib.sha256(diff.stdout)
    for raw_path in sorted(path for path in untracked.split("\0") if path):
        path = root / raw_path
        try:
            stat = path.lstat()
        except OSError:
            continue
        metadata = f"{raw_path}\0{stat.st_size}\0{stat.st_mtime_ns}\0"
        digest.update(metadata.encode("utf-8", errors="surrogateescape"))
    return digest.hexdigest()


def current_repo_state(root: Path) -> dict[str, str] | None:
    """콘텐츠를 보관하지 않고 HEAD와 작업 트리 상태를 수집한다."""
    head = run_git(root, "rev-parse", "HEAD")
    status = run_git(root, "status", "--porcelain")
    fingerprint = git_change_fingerprint(root)
    if head is None or status is None or fingerprint is None:
        return None
    return {
        "head": head.strip(),
        "status": status.strip(),
        "fingerprint": fingerprint,
    }


def baseline_path(root: Path, session_id: str) -> Path | None:
    """Codex 세션별 Git 기준점 경로를 반환한다."""
    if not session_id:
        return None
    ref = re.sub(r"[^A-Za-z0-9]", "", session_id)[:12]
    if not ref:
        return None
    return root / ".codex" / ".claims" / "journals" / f"{ref}.json"


def save_codex_baseline(payload: dict[str, object]) -> None:
    """첫 SessionStart의 Git 상태를 저장하고 resume에서는 덮어쓰지 않는다."""
    if resolve_runtime() != "codex":
        return
    root = resolve_project_root(payload)
    session_id = resolve_session_id(payload)
    if root is None:
        return
    path = baseline_path(root, session_id)
    if path is None or path.exists():
        return
    state = current_repo_state(root)
    if state is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass  # 기준점 기록 실패가 세션 시작을 막지 않는다


def codex_session_changed(payload: dict[str, object]) -> bool:
    """SessionStart 기준점과 현재 Git 상태가 달라졌는지 판정한다."""
    root = resolve_project_root(payload)
    session_id = resolve_session_id(payload)
    if root is None:
        return False
    current = current_repo_state(root)
    path = baseline_path(root, session_id)
    if current is None:
        return False
    if path is None or not path.is_file():
        # 이 기능 도입 전에 시작한 세션은 현재 변경이 있을 때만 보수적으로 요구한다.
        return bool(current["status"])
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return bool(current["status"])
    return baseline != current


def current_session_journals(day_dir: Path, session_id: str) -> list[Path]:
    """오늘 저널 중 현재 세션 ID가 일치하는 파일을 반환한다."""
    if not session_id:
        return []
    return [
        day_dir / name
        for _, name in scan_numbers(day_dir)
        if read_frontmatter(day_dir / name).get("session_id") == session_id
    ]


def deny(reason: str) -> NoReturn:
    """PreToolUse 차단 응답. 공식 스펙상 강제 정책은 exit 0 + JSON이 권장 형태다."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                },
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


def main() -> None:
    """서브커맨드를 hook 이벤트로 분기해 실행한다."""
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        loaded_payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        loaded_payload = {}
    payload = loaded_payload if isinstance(loaded_payload, dict) else {}

    root = resolve_journal_root()
    if root is None:
        sys.exit(0)  # 볼트 없는 환경 — 조용히 통과

    today = datetime.now(tz=KST).strftime("%Y-%m-%d")
    today_dir = root / today

    # ── session-start: 다음 번호와 미완 미션을 컨텍스트로 주입 ──────────────────
    # 세션이 시작하자마자 번호를 알면 `ls`로 세는 경합 자체가 사라진다.
    if command == "session-start":
        save_codex_baseline(payload)
        numbers = scan_numbers(today_dir)
        next_nn = f"{(max(n for n, _ in numbers) + 1) if numbers else 1:02d}"

        open_missions = []
        day_dirs = sorted(root.glob("????-??-??"), reverse=True)[:RECENT_DAYS]
        for day_dir in day_dirs:
            if not DAY_DIR_RE.match(day_dir.name):
                continue
            for _, name in scan_numbers(day_dir):
                status = read_frontmatter(day_dir / name).get("status", "")
                if status in OPEN_STATUSES:
                    open_missions.append(f"{day_dir.name}/{name[:-3]} ({status})")

        print(f"[저널 가드] 볼트 {root}")
        print(f"- 런타임: `{resolve_runtime()}` · 태그: `runtime/{resolve_runtime()}`")
        print(
            f"- 오늘({today}) 다음 미션 번호: **{next_nn}** → "
            f"`{today_dir}/{next_nn}-<mission-slug>.md`"
        )
        if numbers:
            existing_names = ", ".join(name[:-3] for _, name in numbers)
            print(f"- 오늘 기존 저널: {existing_names}")
        else:
            print(
                "- 오늘 저널 없음 — 미션 판단(파일 수정·위임·결정·비가역 작업) 시 "
                "위 경로로 개시할 것"
            )
        if open_missions:
            print(f"- 열린 미션(최근 {RECENT_DAYS}일): {' / '.join(open_missions)}")
        sys.exit(0)

    # ── pre-write: 저널 신규 생성의 넘버링·파일명 규약 강제 ────────────────────
    if command == "pre-write":
        raw_tool_input = payload.get("tool_input")
        tool_input = raw_tool_input if isinstance(raw_tool_input, dict) else {}
        raw_path = tool_input.get("file_path") or ""
        if not raw_path:
            sys.exit(0)
        target = Path(raw_path)

        agents_root = resolve_agents_root()
        if agents_root is None:
            sys.exit(0)

        try:
            relative = target.resolve().relative_to(agents_root.resolve())
        except ValueError:
            sys.exit(0)  # 볼트 저널 밖 — 가드 대상 아님

        if target.exists():
            sys.exit(0)  # 기존 저널 갱신은 넘버링과 무관
        if target.name.startswith(EXEMPT_PREFIX):
            sys.exit(0)  # `_MOC`·`_TEMPLATE` 등 볼트 관리 파일
        runtime_dir = RUNTIME_DIRS[resolve_runtime()]
        expected_depth = 3 if runtime_dir else 2
        is_runtime_root = not runtime_dir or relative.parts[0] == runtime_dir
        if len(relative.parts) != expected_depth or not is_runtime_root:
            path_pattern = (
                f"agents/{runtime_dir}/<YYYY-MM-DD>/<NN>-<mission-slug>.md"
                if runtime_dir
                else "agents/<YYYY-MM-DD>/<NN>-<mission-slug>.md"
            )
            deny(f"신규 저널은 `{path_pattern}` 구조여야 한다. 받은 경로: {relative}")

        day_index = 1 if runtime_dir else 0
        day_name = relative.parts[day_index]
        if not DAY_DIR_RE.match(day_name):
            deny(f"날짜 폴더명이 `YYYY-MM-DD`가 아니다: `{day_name}`")

        matched = JOURNAL_NAME_RE.match(target.name)
        if not matched:
            deny(
                "파일명이 규약 `<NN>-<mission-slug>.md`(NN=2자리, slug=소문자·숫자"
                f"·하이픈)와 어긋난다: `{target.name}`"
            )

        requested = int(matched.group(1))
        taken = dict(scan_numbers(root / day_name))
        expected = (max(taken) + 1) if taken else 1

        if requested in taken:
            deny(
                f"NN `{matched.group(1)}`은(는) 이미 `{taken[requested]}`가 점유했다. "
                f"병렬 세션 경합일 수 있으니 **{expected:02d}**로 생성하라 "
                "(NN=그날 착수 순번, 판정 기준은 본문 상호작용 로그의 첫 이벤트 시각)."
            )
        if requested != expected:
            occupied = ", ".join(f"{n:02d}" for n in sorted(taken)) or "없음"
            deny(
                f"NN이 연속되지 않는다. 다음 번호는 **{expected:02d}**인데 "
                f"`{matched.group(1)}`을 요청했다. (현 점유: {occupied})"
            )
        sys.exit(0)

    # ── stop: 현재 세션 변경에 대응하는 저널이 없으면 보정을 요청 ───────────────
    if command == "stop":
        runtime = resolve_runtime()
        project_root = resolve_project_root(payload)
        if project_root is None:
            sys.exit(0)

        if runtime == "codex":
            touched = codex_session_changed(payload)
        else:
            status = run_git(project_root, "status", "--porcelain")
            commits = run_git(project_root, "log", "--since=midnight", "--oneline")
            touched = bool((status or "").strip()) or bool((commits or "").strip())
        if not touched:
            sys.exit(0)

        session_id = resolve_session_id(payload)
        journals = (
            current_session_journals(today_dir, session_id)
            if runtime == "codex"
            else [today_dir / name for _, name in scan_numbers(today_dir)]
        )
        if not journals:
            message = (
                f"⚠️ {runtime} 저널 미개설 — 이 세션에서 저장소 변경이 있었지만 "
                f"`{today_dir}`에 현재 세션 미션 저널이 없습니다. "
                "`archivist`로 저널과 `agents/_MOC.md`를 한 벌로 기록하세요."
            )
        else:
            stale = [
                path.stem
                for path in journals
                if read_frontmatter(path).get("status") not in {"done", "blocked"}
                or not read_frontmatter(path).get("updated", "").startswith(today)
            ]
            if not stale:
                sys.exit(0)
            message = (
                f"⚠️ {runtime} 저널 마감 미완료 — {', '.join(stale)} "
                "(사용자 최종 보고 직전 status·updated와 `_MOC.md`를 갱신하세요.)"
            )

        if runtime == "codex" and payload.get("stop_hook_active") is not True:
            response = {"decision": "block", "reason": message}
            print(json.dumps(response, ensure_ascii=False))
        else:
            # 두 번째 Stop에서는 반복 루프를 만들지 않고 경고만 남긴다.
            print(json.dumps({"systemMessage": message}, ensure_ascii=False))
        sys.exit(0)

    print(f"알 수 없는 서브커맨드: {command!r}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
