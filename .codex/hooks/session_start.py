#!/usr/bin/env python3
"""기존 저널 상태를 Codex SessionStart 문맥으로 전달한다."""

import json
import os
import subprocess
import sys
from pathlib import Path


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
    message = (result.stdout or result.stderr).strip()
    if not message:
        return
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


if __name__ == "__main__":
    main()
