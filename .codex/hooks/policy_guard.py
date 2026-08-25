#!/usr/bin/env python3
"""Codex 전용 PreToolUse 정책 가드."""

import json
import re
import sys
from pathlib import Path

HTTP_MUTATION_RE = re.compile(
    r"(?:\bcurl\b.*(?:"
    r"(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)\b|"
    r"--json\b|--data(?:-ascii|-binary|-raw|-urlencode)?\b|"
    r"(?:^|\s)-[dFT](?:\s|$)|--form\b|--upload-file\b"
    r")|\bwget\b.*(?:--post-data|--post-file|--method)\b)",
    re.IGNORECASE | re.DOTALL,
)

WRITE_SIGNAL_RE = re.compile(
    r">|\b(?:tee|cp|mv|rm|truncate|dd|install|touch|patch)\b|"
    r"\b(?:sed|perl)\s+-i\b|write_text|writelines|json\.dump|"
    r"yaml\.dump|Path\.write|shutil\.(?:copy|move)|unlink",
    re.IGNORECASE,
)

SECRET_OR_STATE_RE = re.compile(
    r"(?:^|[/\s\"'])\.env(?:\.[^/\s\"']+)?(?:$|[\s\"'])|"
    r"\.tfstate(?:\.[^/\s\"']+)?(?:$|[\s\"'])",
    re.IGNORECASE,
)

REVIEW_COMMAND_RE = re.compile(
    r"\bgit\s+(?:commit|push)\b|"
    r"\bterraform\s+(?:apply|destroy)\b|"
    r"\bkubectl\s+(?:apply|delete)\b|"
    r"\bhelm\s+(?:install|upgrade|uninstall|delete|rollback)\b|"
    r"\b(?:docker|podman)(?:-compose|\s+compose)\s+down\b.*(?:-v|--volumes)|"
    r"\bdbt\b.*(?:--full-refresh|run-operation|\bseed\b)|"
    r"\b(?:DROP|TRUNCATE|DELETE)\b|"
    r"\b(?:pip|pip3|pipx|npm|npx|pnpm|yarn|bun|bunx|cargo|go|gem|brew)\b.*"
    r"\b(?:install|add|get)\b",
    re.IGNORECASE | re.DOTALL,
)

CANONICAL_PATHS = (
    "AGENTS.md",
    "CLAUDE.md",
    ".codex/",
    ".claude/agents/",
    ".claude/settings.json",
    ".agents/skills/",
    ".claude/skills/",
    "skills-lock.json",
    "compose.yml",
)


def emit_deny(reason: str) -> None:
    """도구 호출을 확정적으로 차단한다."""
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


def emit_context(message: str) -> None:
    """도구 실행 전 모델이 다시 판단할 문맥을 추가한다."""
    print(  # noqa: T201 - hook 프로토콜은 stdout JSON을 사용한다.
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": message,
                }
            },
            ensure_ascii=False,
        )
    )


def patch_paths(command: str) -> list[str]:
    """apply_patch 명령에서 변경 대상 경로를 추출한다."""
    patterns = (
        r"^\*\*\* (?:Add|Update|Delete) File: (.+)$",
        r"^\*\*\* Move to: (.+)$",
    )
    paths = []
    for pattern in patterns:
        paths.extend(re.findall(pattern, command, re.MULTILINE))
    return [str(Path(path.strip())) for path in paths if path.strip()]


def main() -> None:
    """Bash와 apply_patch의 고위험 입력을 검사한다."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        emit_deny("Codex 정책 가드가 hook 입력을 읽지 못했다(fail-closed).")
        return

    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    command = str(tool_input.get("command") or "")

    if tool_name == "Bash":
        if "tistory.com" in command.lower() or HTTP_MUTATION_RE.search(command):
            emit_deny(
                "외부 HTTP mutation·업로드는 이 저장소의 Codex 정책에서 금지된다. "
                "필요하면 사용자에게 목적과 대상을 제시하고 별도 승인 경로를 사용하라."
            )
            return
        if WRITE_SIGNAL_RE.search(command) and SECRET_OR_STATE_RE.search(command):
            emit_deny(
                "`.env` 또는 Terraform state에 대한 shell 쓰기를 차단했다. "
                "비밀값과 state는 Codex가 직접 수정하지 않는다."
            )
            return
        if REVIEW_COMMAND_RE.search(command):
            emit_context(
                "이 명령은 커밋·배포·파괴·설치처럼 비용 또는 외부 상태를 바꿀 수 있다. "
                "현재 사용자 요청이 이 정확한 실행을 명시적으로 허용했는지 재확인하고, "
                "아니면 실행하지 말고 계획만 반환하라."
            )
        return

    if tool_name == "apply_patch":
        paths = patch_paths(command)
        if any(SECRET_OR_STATE_RE.search(path) for path in paths):
            emit_deny(
                "`.env` 또는 Terraform state 파일에 대한 patch를 차단했다. "
                "비밀값과 state는 Codex가 직접 수정하지 않는다."
            )
            return
        canonical = [
            path
            for path in paths
            if any(
                path == protected.rstrip("/") or path.startswith(protected)
                for protected in CANONICAL_PATHS
            )
        ]
        if canonical:
            emit_context(
                "실행 규칙 또는 통제 배선을 수정한다: "
                f"{', '.join(sorted(set(canonical)))}. 무엇을 바꾸는지, 왜 지금인지, "
                "어떤 부정 테스트로 실효를 확인할지 답한 뒤 최소 변경으로 진행하라."
            )


if __name__ == "__main__":
    main()
