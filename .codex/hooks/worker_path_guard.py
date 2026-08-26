#!/usr/bin/env python3
"""Codex apply_patch에 대한 워커별 쓰기 경계 가드."""

import json
import os
import re
import sys
from collections.abc import Iterator
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

WORKER_MARKERS = {worker: f".claude/agents/{worker}.md" for worker in BOUNDARIES}

OBSIDIAN_ROOT = Path(
    os.environ.get("OBSIDIAN_VAULT") or str(Path.home() / "obsidian")
).expanduser()
DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JOURNAL_NAME_RE = re.compile(r"^(\d{2})-[a-z0-9][a-z0-9-]*\.md$")

# Codex archivist의 날짜 저널은 아래 전용 판정으로 허용한다. `agents/` 전체를
# 접두어로 열면 Claude 이력과 다른 관리 파일까지 수정할 수 있어 허용하지 않는다.
OUTSIDE_ALLOW = {
    "archivist": (
        str(OBSIDIAN_ROOT / "agents" / "_MOC.md"),
        str(OBSIDIAN_ROOT / "agents" / "_TEMPLATE.codex.md"),
    ),
    "data-extractor": (
        os.environ.get("DATA_EXTRACT_DIR") or str(Path.home() / "extracts"),
    ),
}


def is_codex_journal_path(target: Path) -> bool:
    """Codex archivist의 `agents/<날짜>/<파일>.md` 경로인지 판정한다."""
    agents_root = (OBSIDIAN_ROOT / "agents").resolve()
    try:
        relative = target.relative_to(agents_root)
    except ValueError:
        return False
    if len(relative.parts) != 2 or DAY_DIR_RE.fullmatch(relative.parts[0]) is None:
        return False

    matched = JOURNAL_NAME_RE.fullmatch(relative.name)
    if matched is None:
        return False
    if target.exists():
        return read_journal_agent(target) == "codex"

    numbers = [
        int(existing.group(1))
        for path in target.parent.glob("*.md")
        if (existing := JOURNAL_NAME_RE.fullmatch(path.name)) is not None
    ]
    expected = max(numbers, default=0) + 1
    return int(matched.group(1)) == expected


def read_journal_agent(path: Path) -> str:
    """기존 저널 frontmatter의 agent 값을 반환한다."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator and key.strip() == "agent":
            return value.split("#")[0].strip().strip("\"'")
    return ""


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


def transcript_events(raw_path: str) -> Iterator[dict[str, object]]:
    """부분 기록 중인 transcript에서 읽을 수 있는 JSON 이벤트를 순회한다."""
    try:
        with Path(raw_path).open(encoding="utf-8") as transcript:
            for line in transcript:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event
    except OSError:
        return


def is_subagent_meta(event: dict[str, object]) -> bool:
    """서브에이전트 세션 메타 이벤트인지 확인한다."""
    meta = event.get("payload")
    return (
        event.get("type") == "session_meta"
        and isinstance(meta, dict)
        and meta.get("thread_source") == "subagent"
    )


def developer_texts(event: dict[str, object]) -> list[str]:
    """개발자 메시지 이벤트의 텍스트 조각을 반환한다."""
    message = event.get("payload")
    if not isinstance(message, dict):
        return []
    if message.get("type") != "message" or message.get("role") != "developer":
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        item["text"]
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]


def infer_worker(payload: dict[str, object]) -> str | None:
    """서브에이전트 transcript에서 활성 워커를 식별한다."""
    raw_path = payload.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path:
        return None

    events = transcript_events(raw_path)
    first_event = next(events, None)
    if first_event is None or not is_subagent_meta(first_event):
        return None

    for event in events:
        developer_text = "\n".join(developer_texts(event))
        matches = [
            worker
            for worker, marker in WORKER_MARKERS.items()
            if marker in developer_text
        ]
        if len(matches) == 1:
            return matches[0]
    return None


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
        if worker == "archivist" and is_codex_journal_path(target):
            return None
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
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        emit_deny("워커 경계 가드가 hook 입력을 읽지 못했다(fail-closed).")
        return

    worker = sys.argv[1] if len(sys.argv) > 1 else infer_worker(payload)
    if worker is None:
        return
    if worker not in BOUNDARIES:
        emit_deny(f"정의되지 않은 Codex 워커 경계다: `{worker}`.")
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
