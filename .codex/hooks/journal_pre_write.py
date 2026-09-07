#!/usr/bin/env python3
"""Codex apply_patch의 신규 저널 경로를 공용 journal_guard에 연결한다."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ADD_FILE_RE = re.compile(r"^\*\*\* Add File: (.+)$", re.MULTILINE)


def deny(reason: str) -> None:
    """판정을 못 한 경우를 포함해 patch를 차단한다."""
    print(  # noqa: T201 - hook 프로토콜은 stdout JSON을 사용한다.
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )


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
    # ⚠️ **가드 파일 부재는 의도적으로 통과**시킨다(Issue #55에서 축을 갈랐다).
    #    이 hook은 모든 `apply_patch`에 걸리는데, 추적 파일 하나가 없다고 런타임
    #    전체를 막으면 그 가드는 곧 무시된다 — `git restore` 한 줄로 복구되는
    #    상태이기도 하다. **빠뜨린 것이 아니라 결정**이다.
    if not guard.is_file():
        return

    environment = os.environ.copy()
    environment["JOURNAL_RUNTIME"] = "codex"
    for raw_path in ADD_FILE_RE.findall(command):
        # 🔴 **타임아웃은 `deny`다**(Issue #55). 예전에는 `TimeoutExpired`를 잡지
        #    않아 traceback + 비-0 종료로 죽었는데, 이 저장소의 hook은 통과를
        #    **`exit 0` + 무출력**으로 표현하므로 하네스가 보는 것은 「결정 없음」
        #    으로 **통과와 같다** — 즉 검사가 죽으면 조용히 열렸다.
        #    이 hook은 저널 경로 통제의 **중계**라, 검사를 못 돌린 것을
        #    통과로 읽으면 안 된다.
        try:
            result = subprocess.run(  # noqa: S603 - 저장소 내부의 고정 가드만 실행한다.
                [sys.executable, str(guard), "pre-write"],
                input=json.dumps({"tool_input": {"file_path": raw_path.strip()}}),
                env=environment,
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except subprocess.TimeoutExpired:
            deny(
                f"저널 경로 검사가 시간 안에 끝나지 않았다({raw_path.strip()}) — "
                "판정을 못 했으므로 통과시키지 않는다(fail-closed). "
                "`scripts/journal_guard.py pre-write`를 직접 돌려 원인을 본다."
            )
            return
        output = result.stdout.strip()
        if output:
            print(output)  # noqa: T201 - hook 프로토콜은 stdout JSON을 사용한다.
            return


if __name__ == "__main__":
    main()
