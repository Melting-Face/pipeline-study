#!/usr/bin/env python3
"""기존 저널 상태를 Codex SessionStart 문맥으로 전달한다."""

import json
import os
import subprocess
import sys
from pathlib import Path


def emit_context(message: str) -> None:
    """세션 시작 문맥을 stdout JSON으로 낸다."""
    print(  # noqa: T201 - hook 프로토콜은 stdout JSON을 사용한다.
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": message,
                }
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    """저널 가드를 실행하고 출력 내용을 추가 문맥으로 변환한다."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    root = Path(payload.get("cwd") or ".").resolve()
    while root.parent != root and not (root / ".git").exists():
        root = root.parent

    guard = root / "scripts/journal_guard.py"
    if not guard.is_file():
        return

    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(root)
    env["JOURNAL_RUNTIME"] = "codex"
    # ⚠️ **타임아웃은 통과다 — 다만 소리를 낸다**(Issue #55). 이 hook은 통제가
    #    아니라 세션 시작 **문맥 주입**이라 막을 것이 없다. 그러나 예전에는
    #    `TimeoutExpired`를 안 잡아 traceback으로 죽었고, 그러면 저널 번호가
    #    주입되지 않은 채 **주입된 것처럼** 세션이 시작됐다(무출력은 정상과 같다).
    try:
        result = subprocess.run(  # noqa: S603 - 저장소 내부의 고정 가드만 실행한다.
            [sys.executable, str(guard), "session-start"],
            cwd=root,
            env=env,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except subprocess.TimeoutExpired:
        message = (
            "⚠️ 저널 상태 조회가 시간 안에 끝나지 않았다 — "
            "**미션 번호가 주입되지 않았다.** "
            "저널을 열기 전에 `scripts/journal_guard.py session-start`를 직접 돌린다."
        )
        emit_context(message)
        return
    message = (result.stdout or result.stderr).strip()
    if not message:
        return
    emit_context(message)


if __name__ == "__main__":
    main()
