#!/usr/bin/env python3
"""기존 저널 누락 검사를 Codex Stop 이벤트에 연결한다."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    """저널 가드의 경고를 Codex systemMessage로 전달한다."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
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
    result = subprocess.run(  # noqa: S603 - 저장소 내부의 고정 가드만 실행한다.
        [sys.executable, str(guard), "stop"],
        cwd=root,
        env=env,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    output = result.stdout.strip()
    if not output:
        return
    try:
        json.loads(output)
    except (json.JSONDecodeError, ValueError):
        print(  # noqa: T201 - hook 프로토콜은 stdout JSON을 사용한다.
            json.dumps({"systemMessage": output}, ensure_ascii=False)
        )
    else:
        print(output)  # noqa: T201 - 검증된 hook JSON을 그대로 전달한다.


if __name__ == "__main__":
    main()
