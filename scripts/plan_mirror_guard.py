#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""계획 파일(플랜 모드) → Obsidian 볼트 미러 — 세션 스코프 hook.

왜 이 스크립트인가:
    플랜 모드의 계획 파일은 하네스가 `~/.claude/plans/<랜덤슬러그>.md`에
    **경로와 이름을 정해서** 만든다. 우리가 바꿀 수 없고, 슬러그가 무작위라
    나중에 어떤 미션의 계획인지 알아볼 수 없다. 저널
    (`agents/claude-code/<날짜>/<NN>-<slug>.md`)
    은 볼트에 쌓이는데 그 근거가 된 **계획서만 홈 디렉터리에 흩어져 남는** 셈이다.

    그래서 Claude 저널과 **같은 NN·같은 슬러그**로
    `plans/claude-code/<날짜>/<NN>-<slug>.md`에 복사한다.
    런타임별 동명 파일을 구분하도록 저널 링크에는 볼트 기준 전체 경로를 쓴다.

    🔴 미션 이름의 정본은 **저널**이다. 이 스크립트는 이름을 짓지 않고
    **저널에서 읽어 따라간다** — 계획서가 저널보다 먼저 생기는 경우가 흔해서
    (계획 → 승인 → 미션 개시), 저널이 나타날 때까지 미러링을 **보류**하고
    나타난 뒤에 소급 복사한다. 이름을 스스로 지으면 저널과 어긋나 짝이 깨진다.

    관측 대상이 둘(계획 파일·저널 파일)이라 `PostToolUse`로 **경로만 기록**하고,
    실제 복사는 둘이 모두 모였을 때 수행한다. `Stop`에서 한 번 더 시도해
    "계획이 먼저였던" 세션을 소급 처리한다.

한계(정직하게):
    - 결정을 내지 않는 hook이다(`hookSpecificOutput` 없음). 차단하지 않으며,
      복사에 실패해도 작업을 멈추지 않는다 — 기록 보조이지 통제가 아니다.
    - `Bash` 경유로 만든 계획 파일·저널은 관측되지 않는다(도구 경로만 본다).
    - 🔴 볼트는 obsidian-git이 **자동 커밋·푸시**한다. 여기 복사된 계획서는
      원격 저장소로 나간다. 저널이 이미 같은 조건이므로 새로 생기는 노출 축은
      아니지만, **복사 = 외부 발신**임을 잊지 않는다.

사용: `.claude/settings.json`의
    `PostToolUse[matcher: "Write|Edit"]` → `plan_mirror_guard.py post`
    `Stop` → `plan_mirror_guard.py stop`
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 하네스가 계획 파일을 만드는 고정 위치. 프로젝트가 아니라 **홈** 아래다.
PLAN_SOURCE_DIR = Path.home() / ".claude" / "plans"

# 새 기록은 런타임별 경로로 분리한다. 기존 `agents/<날짜>/`는 읽기 호환만
# 유지하며 새 계획 미러는 항상 `plans/claude-code/`에 쓴다.
JOURNAL_SUBDIR = Path("agents") / "claude-code"
LEGACY_JOURNAL_SUBDIR = Path("agents")
PLAN_SUBDIR = Path("plans") / "claude-code"

# 저널 파일명 규약 `<NN>-<mission-slug>.md`. journal_guard.py와 같은 형태를 본다.
JOURNAL_NAME_RE = re.compile(r"^(\d{2})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# PostToolUse 입력에서 대상 경로가 담기는 키.
# 🔴 `Write`·`Edit`는 `file_path`, `NotebookEdit`만 `notebook_path`다 —
#    matcher가 여러 도구에 걸치면 키가 갈린다(2026-08-20 session_sync_guard 사고).
#    이 hook의 matcher는 `Write|Edit` 둘뿐이라 `file_path` 하나로 충분하지만,
#    matcher를 넓히는 순간 조용히 투명해지므로 세 키를 모두 본다.
PATH_KEYS = ("file_path", "notebook_path", "path")

KST = timezone(timedelta(hours=9))

# 🔴 계획서를 볼트에서 빼는 유일한 수단. 계획 파일 아무 곳에나 이 문자열을 두면
#    미러링하지 않는다(이미 복사된 미러는 지우지 않는다 — 지우는 것은 사람의 판단이다).
#
#    왜 필요한가: **저널과 계획서는 노출 통제의 축이 다르다.** 저널은 supervisor·
#    archivist가 "무엇을 남길지" 고르며 쓰는 요약이지만, 계획서는 하네스 산출물을
#    **통째로** 복사하므로 고르는 단계가 없다. 2026-08-21 병렬 세션이 실제로
#    "미완화 취약점(무인증 UI·기본 비밀번호·백업 고장·권한 우회 지점)이 계획서 한 장에
#    집약돼 있다"고 알려왔고, 그 밀도는 같은 미션의 저널보다 높았다.
#    볼트가 private 원격이라 수용 가능한 범위이나, **판단할 여지 자체가 없는 것**이
#    문제라 스위치를 둔다.
OPT_OUT_MARKER = "<!-- plan-mirror: off -->"


def vault_root() -> Path:
    """볼트 루트. `$OBSIDIAN_VAULT` 우선, 없으면 `~/obsidian`(journal_guard와 동일)."""
    vault = os.environ.get("OBSIDIAN_VAULT") or "~/obsidian"
    return Path(vault).expanduser()


def registry_path(session_id: str) -> Path:
    """세션별 매핑 파일. `.claude/.claims/` 아래라 gitignore 대상이다."""
    ref = (session_id or "unknown").replace("-", "")[:6]
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    return root / ".claude" / ".claims" / "plans" / f"{ref}.json"


def load_registry(path: Path) -> dict:
    """매핑을 읽는다. 없거나 깨졌으면 빈 dict — 기록 보조라 실패해도 진행한다."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def save_registry(path: Path, record: dict) -> None:
    """매핑을 원자적으로 쓴다(임시 파일 → rename)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        body = json.dumps(record, ensure_ascii=False, indent=2)
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # 기록 실패가 작업을 멈추게 하지 않는다


def classify(target: Path) -> tuple[str, str] | None:
    """쓰기 대상이 계획 파일인지 저널 파일인지 판별한다.

    Returns:
        `("plan", 절대경로)` / `("journal", "<날짜>/<NN>-<slug>")` / 판별 불가면 None.
    """
    if target.suffix != ".md":
        return None

    # 계획 파일 — 하네스 고정 위치 **바로 아래**만 본다(하위 디렉터리는 남의 것).
    if target.parent == PLAN_SOURCE_DIR:
        return ("plan", str(target))

    # 저널 파일 — 신규 Claude 경로와 기존 역사 경로를 모두 읽는다.
    journal_roots = {
        vault_root() / JOURNAL_SUBDIR,
        vault_root() / LEGACY_JOURNAL_SUBDIR,
    }
    day_dir = target.parent
    if day_dir.parent not in journal_roots or not DAY_DIR_RE.match(day_dir.name):
        return None
    if not JOURNAL_NAME_RE.match(target.name):
        return None
    return ("journal", f"{day_dir.name}/{target.name[:-3]}")


def compose(source_text: str, journal_rel: str) -> str:
    """미러 본문을 만든다 — 저널로 되돌아가는 링크를 머리에 붙인다.

    원문이 이미 `---`로 시작하면 프론트매터를 **덧붙이지 않는다** —
    YAML 블록이 둘이면 Obsidian이 두 번째를 본문으로 읽어 메타가 깨진다.
    """
    day, name = journal_rel.split("/", 1)
    matched = JOURNAL_NAME_RE.match(f"{name}.md")
    slug = matched.group(2) if matched else name
    stamp = datetime.now(tz=KST).strftime("%Y-%m-%dT%H:%M+09:00")
    journal_link = f"agents/claude-code/{day}/{name}"
    backlink = (
        f"> 🔗 미션 저널: [[{journal_link}]] · "
        "이 파일은 **미러**다(원본은 하네스 관리)\n\n"
    )

    if source_text.startswith("---"):
        return backlink + source_text

    front = (
        "---\n"
        f"mission: {slug}\n"
        f"date: {day}\n"
        "kind: plan\n"
        f'journal: "[[{journal_link}]]"\n'
        f"mirrored: {stamp}\n"
        f"tags: [agent/plan, runtime/claude-code, mission/{slug}]\n"
        "---\n\n"
    )
    return front + backlink + source_text


def mirror(record: dict) -> str | None:
    """계획 파일을 볼트로 복사한다. 실제로 썼을 때만 대상 경로를 돌려준다."""
    plan_src = record.get("plan")
    journal_rel = record.get("journal")
    if not plan_src or not journal_rel:
        return None  # 짝이 아직 안 모였다 — 다음 기회에 소급한다

    source = Path(plan_src)
    if not source.is_file():
        return None

    day, name = journal_rel.split("/", 1)
    destination = vault_root() / PLAN_SUBDIR / day / f"{name}.md"

    # 원본이 더 최신일 때만 쓴다 — 매 턴 복사하면 볼트 자동 커밋이 무의미하게 쌓인다.
    if destination.is_file() and destination.stat().st_mtime >= source.stat().st_mtime:
        return None

    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return None

    if OPT_OUT_MARKER in text:
        return None  # 이 계획서는 볼트로 내보내지 않는다(§OPT_OUT_MARKER)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(".tmp")
        tmp.write_text(compose(text, journal_rel), encoding="utf-8")
        tmp.replace(destination)  # 원자적 — 반쯤 쓴 파일이 자동 커밋되지 않게
    except OSError:
        return None
    return str(destination)


def main() -> None:
    """계획 파일과 저널 파일의 경로를 모아 둘이 갖춰지면 볼트로 미러링한다."""
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # 입력을 못 읽으면 조용히 통과

    session_id = payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or ""
    path = registry_path(session_id)
    record = load_registry(path)

    if mode == "post":
        tool_input = payload.get("tool_input") or {}
        raw = next((tool_input.get(k) for k in PATH_KEYS if tool_input.get(k)), "")
        if not raw:
            sys.exit(0)
        found = classify(Path(str(raw)).expanduser().resolve())
        if not found:
            sys.exit(0)
        kind, value = found
        if record.get(kind) == value:
            # 같은 파일을 다시 편집한 경우 — 기록은 그대로 두고 내용만 갱신한다
            mirror(record)
            sys.exit(0)
        if kind == "plan" and record.get("plan"):
            # 🔴 교체를 조용히 하지 않는다 — 이전 계획의 미러가 갱신을 멈춘다
            print(f"[계획 미러] 대상 교체: {record['plan']} → {value}")
        record[kind] = value
        save_registry(path, record)
        written = mirror(record)
        if written:
            print(f"[계획 미러] {written}")
        sys.exit(0)

    if mode == "stop":
        # 계획이 저널보다 먼저 만들어진 세션을 소급 처리한다
        written = mirror(record)
        if written:
            print(f"[계획 미러] 볼트에 반영: {written}")
        elif record.get("plan") and not record.get("journal"):
            print(
                "[계획 미러] 계획 파일은 있으나 이 세션의 저널이 없어 보류 중이다 "
                "— 저널을 만들면 다음 턴에 소급 복사된다."
            )
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
