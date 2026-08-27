#!/usr/bin/env python3
"""Codex apply_patch의 신규 저널 경로를 공용 journal_guard에 연결한다."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ADD_FILE_RE = re.compile(r"^\*\*\* Add File: (.+)$", re.MULTILINE)


def tool_source(payload: dict[str, object]) -> str:
    """직접 도구와 freeform exec의 입력 문자열을 동일한 형태로 반환한다."""
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, str):
        return tool_input
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "source", "input"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def main() -> None:
    """신규 파일별 pre-write 결과 중 첫 거부를 Codex hook에 반환한다."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    command = tool_source(payload)
    is_patch = (
        "tools.apply_patch(" in command
        or payload.get("tool_name") == "apply_patch"
        or "*** Begin Patch" in command
    )
    if not is_patch:
        return
    command = command.replace("\\n", "\n")

    guard = Path(__file__).resolve().parents[2] / "scripts" / "journal_guard.py"
    if not guard.is_file():
        return

    environment = os.environ.copy()
    environment["JOURNAL_RUNTIME"] = "codex"
    for raw_path in ADD_FILE_RE.findall(command):
        result = subprocess.run(  # noqa: S603 - 저장소 내부의 고정 가드만 실행한다.
            [sys.executable, str(guard), "pre-write"],
            input=json.dumps({"tool_input": {"file_path": raw_path.strip()}}),
            env=environment,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        output = result.stdout.strip()
        if output:
            print(output)  # noqa: T201 - hook 프로토콜은 stdout JSON을 사용한다.
            return


if __name__ == "__main__":
    main()
