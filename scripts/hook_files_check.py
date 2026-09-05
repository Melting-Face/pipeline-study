#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""로컬 pre-commit 훅의 `files:`가 **자기 `entry` 스크립트**를 담는지 검사한다.

Issue #56 — 게이트를 바꾸는 커밋에서 하필 그 게이트가 안 도는 사각을 닫는다.

사용법:
    python3 scripts/hook_files_check.py     # repo 루트에서

무엇을 막는가:
    로컬 훅의 `files:`에 그 훅이 실행하는 스크립트가 빠져 있으면, **그 스크립트만 고치는
    커밋에서 훅이 통째로 `(no files to check) Skipped`** 가 된다. 하필 **게이트를 바꾸는
    커밋에서 그 게이트가 안 돈다.**

🔴 왜 개별 수정이 아니라 검사기인가 — Rule of Three가 성립했다.
사례 셋에 **관측자도 셋**이다:

    | 훅 | 찾은 주체 |
    | --- | --- |
    | `worker-wiring` | `security` 컨펌 |
    | `skill-wiring`  | 병렬 세션 (커밋 `9dcb412`가 실물 — R7·R8 신설 커밋에서
      `Skipped`였다) |
    | `doc-lint`      | 세션 관측 (`files: '\.md$'`가 `scripts/doc_lint.py`를
      안 잡았다) |

    세 사람이 각각 하나씩 찾았다 — 한 사람이 전수했으면 세 번 만에 안 나왔다.
    **개별 수정으로는 네 번째를 못 막는다.**

축을 섞지 않는다:
    ⓐ 모집단에 **gitignore 경로**가 섞임 → 디스크만 바뀌면 트리거 안 됨
       (규칙 *일부*가 안 돎)
    ⓑ 모집단에 **자기 검사기**가 빠짐 → 검사기만 바뀌면 **훅 전체**가 안 돎
    **이 검사기는 ⓑ만 본다.** ⓐ는 `/skill-audit`(감사 축)이 본다.

🔴 `always_run: true`를 「면제」로 두지 않는다:
    `doc-links`는 사각이 없는데 **사유가 다르다** — `files:` 없이 `always_run: true`라
    **면제가 아니라 모집단이 전체**다. 「사각 없음」을 같은 이유로 세면 다음 사람이
    *"면제받은 훅이 있다"* 로 읽는다(*"면제는 검증이 아니다"*).
    ⇒ 통과가 아니라 **별도 상태 `전체모집단`** 으로 세고 꼬리에 따로 찍는다.

🔴 `PyYAML`을 쓰지 않는다:
    호스트 `python3`에 없다(실측 — CPython 3.14.5 `ModuleNotFoundError`).
    CI `lint` 잡에는
    `pre-commit`이 딸려 와 `yaml`이 임포트되므로, 쓰면 **CI만 통과하고 로컬에서 죽는**
    최악의 조합이 된다. 이 저장소의 다른 훅 스크립트와 같이 `dependencies = []`로 둔다.
    대가는 아래 최소 파서를 우리가 지는 것이고,
    **파싱이 어긋나면 fail-closed**로 갚는다.

보증하지 않는 것:
    · `.claude/settings.json`의 hook 배선은 **`files:` 축 자체가 없다**(배선이 정의 로드
      시점 스냅샷이라 **다른 형태의 사각**이다). 여기서 보지 않는다.
    · `repo: local`이 아닌 업스트림 훅은 자기 스크립트가 저장소에 없어
      이 축이 성립하지 않는다.
    · 훅이 **옳은 일을 하는지**는 보지 않는다. **언제 도는지**만 본다.
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / ".pre-commit-config.yaml"

# `entry:`에서 실행 스크립트 경로를 뽑는다. `python3 scripts/doc_lint.py --links`처럼
# 인터프리터·인자가 붙으므로 저장소 상대 경로 부분만 집는다.
ENTRY_SCRIPT_RE = re.compile(r"(scripts/[\w./-]+\.py)")

# 최소 파서가 읽는 키. 🔴 이 목록 밖은 **읽지 않는다** — 범용 YAML 파서를 흉내내면
# 그 순간부터 이 파일이 파서의 정확도에 묶인다.
HOOK_KEYS = ("id", "name", "language", "entry", "files", "exclude", "always_run")

# 접힌·리터럴 스칼라 표식. 뒤따르는 더 깊은 들여쓰기 줄이 값의 연속이다.
BLOCK_SCALARS = (">", ">-", "|", "|-", ">+", "|+")


def main() -> int:
    """로컬 훅을 파싱해 자기포함·전체모집단·위반을 갈라 센다."""
    if not CONFIG.is_file():
        print(f"🔴 설정 파일을 찾지 못했다: {CONFIG}", file=sys.stderr)
        return 1

    lines = CONFIG.read_text(encoding="utf-8").splitlines()

    hooks: list[dict[str, str]] = []
    current_repo = ""
    hook: dict[str, str] | None = None
    hook_indent = -1
    index = 0

    while index < len(lines):
        raw = lines[index]
        index += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))

        # `- repo: <값>` — 블록이 바뀌면 진행 중이던 훅을 닫는다.
        if stripped.startswith("- repo:"):
            if hook is not None:
                hooks.append(hook)
                hook = None
            current_repo = stripped.split(":", 1)[1].strip()
            continue

        # `- id: <값>` — 훅의 시작. 들여쓰기를 기억해 소속 키를 가른다.
        if stripped.startswith("- id:"):
            if hook is not None:
                hooks.append(hook)
            hook = {"repo": current_repo, "id": stripped.split(":", 1)[1].strip()}
            hook_indent = indent
            continue

        if hook is None:
            continue

        # 훅보다 얕거나 같은 들여쓰기 = 훅 밖으로 나왔다.
        if indent <= hook_indent:
            hooks.append(hook)
            hook = None
            continue

        key, separator, value = stripped.partition(":")
        if not separator or key not in HOOK_KEYS:
            continue
        value = value.strip()

        # 접힌 스칼라: 더 깊은 들여쓰기 줄을 값의 연속으로 모은다.
        if value in BLOCK_SCALARS:
            parts: list[str] = []
            while index < len(lines):
                following = lines[index]
                if not following.strip():
                    index += 1
                    continue
                following_indent = len(following) - len(following.lstrip(" "))
                if following_indent <= indent:
                    break
                parts.append(following.strip())
                index += 1
            value = " ".join(parts)

        hook[key] = value.strip("'\"")

    if hook is not None:
        hooks.append(hook)

    # ── 판정 ────────────────────────────────────────────────────────────────
    local_hooks = [item for item in hooks if item.get("repo") == "local"]

    self_contained: list[str] = []
    whole_population: list[str] = []
    violations: list[str] = []
    not_executing: list[str] = []

    for item in local_hooks:
        entry = item.get("entry", "")
        match = ENTRY_SCRIPT_RE.search(entry)
        if match is None:
            # `language: fail`은 아무것도 실행하지 않아 이 축이 성립하지 않는다.
            not_executing.append(item["id"])
            continue

        script = match.group(1)
        files = item.get("files", "")
        always_run = item.get("always_run", "").lower() == "true"

        if always_run and files:
            violations.append(
                f"{item['id']}: `always_run: true`와 `files:`가 함께 있다 — "
                "모집단이 「전체」인지 「files 매치분」인지 읽는 사람이 가를 수 없다"
            )
            continue

        if always_run or not files:
            # 🔴 통과가 아니라 **다른 상태**다. 모집단이 전체라 사각이 없는 것이지
            #    검사를 면제받은 것이 아니다.
            whole_population.append(f"{item['id']} ({script})")
            continue

        try:
            pattern = re.compile(files)
        except re.error as error:
            violations.append(
                f"{item['id']}: `files:` 정규식을 컴파일할 수 없다 — {error}"
            )
            continue

        if pattern.search(script) is None:
            violations.append(
                f"{item['id']}: `files:`가 자기 검사기 `{script}`를 담지 않는다 — "
                f"그 파일만 고치는 커밋에서 이 훅은 통째로 Skipped다 (files: {files})"
            )
            continue

        exclude = item.get("exclude", "")
        if exclude and re.search(exclude, script):
            violations.append(
                f"{item['id']}: `files:`는 담는데 `exclude:`가 `{script}`를 도로 뺀다"
            )
            continue

        self_contained.append(f"{item['id']} ({script})")

    # 🔴 fail-closed — 0건을 통과로 읽지 않는다. 파서가 어긋나 아무것도 못 찾아도
    #    초록이 나오면, 이 검사기 자신이 「검증된 것처럼 보이는 미실행 게이트」가 된다.
    if not self_contained and not whole_population and not violations:
        print(
            "🔴 `repo: local`에서 스크립트를 실행하는 훅을 한 건도 찾지 못했다 — "
            f"통과가 아니라 파싱 실패로 본다 ({CONFIG.name}의 구조가 바뀌었는가?)",
            file=sys.stderr,
        )
        return 1

    for line in violations:
        print(f"🔴 {line}", file=sys.stderr)

    # 🔴 한 숫자로 적으면 그 자체가 거짓이 된다 — 상태마다 사유가 다르므로 나눠 찍는다.
    print(
        f"로컬 훅 files: 자기포함 검사 — 검사 {len(local_hooks)}훅 "
        f"(스크립트 실행 {len(local_hooks) - len(not_executing)} · "
        f"비실행 language:fail {len(not_executing)}) · "
        f"자기포함 {len(self_contained)} · 전체모집단 {len(whole_population)} · "
        f"위반 {len(violations)}"
    )
    for line in whole_population:
        print(f"  · 전체모집단(면제 아님 — always_run/무 files): {line}")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
