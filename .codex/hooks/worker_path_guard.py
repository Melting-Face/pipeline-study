#!/usr/bin/env python3
"""Codex apply_patch에 대한 워커별 쓰기 경계 가드."""

import json
import os
import re
import sys
from pathlib import Path

BOUNDARIES = {
    "analyst": {"allow": ("notebooks/", "docs/analyses/")},
    "data-engineer": {
        "deny": ("terraform/", "k8s/", "compose.yml", ".env", ".claude/", ".codex/")
    },
    "devops-engineer": {
        "deny": (
            "dagster/dockerfile.d/src/",
            "notebooks/",
            "docs/analyses/",
            ".env",
            ".claude/",
            ".codex/",
        )
    },
    "archivist": {"allow": ()},
    "data-extractor": {"allow": ()},
    "tech-writer": {
        "allow": ("docs/", "README.md"),
        "except": ("docs/security.md", "docs/skills.md", "docs/skills/"),
    },
}

OUTSIDE_ALLOW = {
    "archivist": (os.environ.get("OBSIDIAN_VAULT") or str(Path.home() / "obsidian"),),
    "data-extractor": (
        os.environ.get("DATA_EXTRACT_DIR") or str(Path.home() / "extracts"),
    ),
}


def emit_deny(reason: str) -> None:
    """워커 경계를 벗어난 patch를 차단한다."""
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


def extract_paths(command: str) -> list[str]:
    """apply_patch 명령에서 변경 경로를 추출한다."""
    paths = re.findall(
        r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", command, re.MULTILINE
    )
    paths.extend(re.findall(r"^\*\*\* Move to: (.+)$", command, re.MULTILINE))
    return [path.strip() for path in paths if path.strip()]


def matches_prefix(path: str, prefix: str) -> bool:
    """디렉터리 접두어와 단일 파일 규칙을 구분해 대조한다."""
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix


def denied_reason(worker: str, raw_path: str, root: Path) -> str | None:
    """경계를 벗어난 경우 거부 사유를 반환한다."""
    boundary = BOUNDARIES[worker]
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = root / target
    target = target.resolve()

    try:
        relative = target.relative_to(root).as_posix()
    except ValueError:
        allowed_roots = [
            Path(path).expanduser().resolve() for path in OUTSIDE_ALLOW.get(worker, ())
        ]
        if any(target.is_relative_to(allowed) for allowed in allowed_roots):
            return None
        return (
            f"`{worker}`가 승인되지 않은 저장소 밖 경로에 쓰려 한다: {target}. "
            "Codex hook은 대화형 ask 결정을 지원하지 않으므로 안전하게 거부했다."
        )

    if target.name.endswith("_guard.py"):
        return f"`{worker}`는 통제 스크립트 `{relative}`를 수정할 수 없다."

    for excluded in boundary.get("except", ()):
        if matches_prefix(relative, excluded):
            return f"`{worker}` 쓰기 예외 경로를 차단했다: `{relative}`."

    allowed = boundary.get("allow")
    if allowed is not None and not any(
        matches_prefix(relative, prefix) for prefix in allowed
    ):
        return f"`{worker}`의 허용 쓰기 범위 밖이다: `{relative}`."

    if any(matches_prefix(relative, prefix) for prefix in boundary.get("deny", ())):
        return f"`{worker}`의 금지 쓰기 범위다: `{relative}`."
    return None


def main() -> None:
    """선택된 워커의 모든 patch 경로를 검사한다."""
    worker = sys.argv[1] if len(sys.argv) > 1 else ""
    if worker not in BOUNDARIES:
        emit_deny(f"정의되지 않은 Codex 워커 경계다: `{worker}`.")
        return

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        emit_deny("워커 경계 가드가 hook 입력을 읽지 못했다(fail-closed).")
        return

    command = str((payload.get("tool_input") or {}).get("command") or "")
    paths = extract_paths(command)
    if not paths:
        emit_deny("apply_patch 입력에서 변경 경로를 찾지 못했다(fail-closed).")
        return

    root = Path(payload.get("cwd") or ".").resolve()
    while root.parent != root and not (root / ".git").exists():
        root = root.parent

    for path in paths:
        reason = denied_reason(worker, path, root)
        if reason:
            emit_deny(reason)
            return


if __name__ == "__main__":
    main()
