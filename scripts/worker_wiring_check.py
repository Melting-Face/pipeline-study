#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""워커 프론트매터의 `worker_path_guard.py` 배선과 그 가드의 경계 표를 대조한다.

사용법:
    uv run scripts/worker_wiring_check.py     # 또는 python3 (의존성 없음)

왜 이 스크립트인가:
    경로 경계는 두 곳에 적혀 있다 — 가드의 `BOUNDARIES`(정의)와 각 워커 프론트매터의
    hook `command` 인자(배선). **둘은 다른 층이고 갈려도 아무것도 깨지지 않는다.**
    한동안 이 규칙은 규율로만 있었다
    (`docs/conventions/agents/permissions.md` §경로 경계).

    갈리는 방향이 셋이다:
      ① 배선 인자가 **오타** → 그 워커의 경계가 통째로 사라진다
      ② 배선 인자가 **다른 유효 워커명** → 엉뚱한 경계가 적용된다
      ③ `BOUNDARIES`에 있는데 **부르는 배선이 없다** → 죽은 정의

    🔴 ①은 이제 런타임에서도 `deny`지만 **워커가 쓰기를 시도해야** 드러난다.
    오타난 워커가 한동안 안 불리면 그 사이 경계가 없다 — 여기서는 **틀린 순간**
    (커밋)에 잡는다. ②는 두 이름이 모두 유효해 런타임 `deny` 분기에 아예 닿지
    않는다 — **이 검사기만 잡는다.**
    ⇒ 한쪽이 초록이라고 다른 쪽이 초록인 것이 아니다
    (`skill_wiring_check.py`가 런타임 `skill_gate_guard.py`와 갖는 관계와 같다).

모집단(무엇을 세는가):
    🔴 **「워커 파일 전량」이 아니라 「이 가드를 배선한 파일」이다.** 읽기 전용 워커는
    `Write`/`Edit`가 없어 이 가드를 배선하지 않으므로, 전량을 세면 그들이 전부
    오탐이 된다. ⇒ **워커를 늘리거나 줄여도 이 검사기는 깨지지 않는다.**
    「배선」은 **프론트매터 안의 `command:` 줄**만 센다 — 지시문 본문에도 가드
    이름이 산문으로 등장하므로(`data-extractor.md` 등) 파일 전체를 훑으면
    그것까지 배선으로 센다.

    🔴 `BOUNDARIES`·`KNOWN_ELSEWHERE`를 **복사하지 않고 가드에서 직접 읽는다.**
    복사하면 이 검사기 자신이 정본 규약 *"같은 경계를 두 곳에 정의하지 않는다"* 를
    위반한다(`skill_wiring_check.py`가 정규식을 복사해 둔 것과 여기가 갈리는 지점 —
    저쪽은 대조 상대가 정규식이라 옮길 수밖에 없었고, 이쪽은 평범한 dict다).

검사에서 빼는 것(빠뜨린 것과 구분하기 위해 명시한다):
    - **경계 내용의 옳고 그름** — `deny`에 무엇을 넣을지는 설계 판단이다. 이름만 본다.
    - **다른 가드의 배선**(`analyst_path_guard`·`skill_gate_guard` 등) — 인자를
      받지 않아 이 축(인자↔정의 대조)이 원리상 없다.
    - **hook이 실제로 발동하는지** — 그건 런타임 축이고
      `docs/conventions/agents/enforcement.md` §실발동 확인이 본다.
      여기는 **문자열 정합**뿐이다.
    - **`command` 인용 규칙의 정오** — 틀리면 에러 없이 조용히 통과하는 축인데,
      그건 문자열 대조로는 안 보인다(같은 파일 §인용 규칙).
"""

import importlib.util
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "agents"
GUARD_PATH = PROJECT_ROOT / "scripts" / "worker_path_guard.py"

# 프론트매터 = 첫 `---`와 다음 `---` 사이. 없으면 배선도 없다고 본다.
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)^---\s*$", re.DOTALL | re.MULTILINE)
# 프론트매터 안의 `name:` — 파일명이 아니라 이 값이 하네스가 쓰는 워커 이름이다.
NAME_RE = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)
# `command:` 줄에서 가드 호출과 그 인자. 인자가 없으면 그룹이 빈 문자열로 잡힌다.
WIRING_RE = re.compile(
    r"^\s*command:.*worker_path_guard\.py[ \t]*([^\s\"']*)", re.MULTILINE
)


def load_guard_constants() -> tuple[dict, dict]:
    """가드 모듈을 import해 `BOUNDARIES`·`KNOWN_ELSEWHERE`를 그대로 가져온다."""
    spec = importlib.util.spec_from_file_location("worker_path_guard", GUARD_PATH)
    if spec is None or spec.loader is None:
        print(f"가드를 읽지 못했다: {GUARD_PATH}", file=sys.stderr)
        raise SystemExit(1)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BOUNDARIES, module.KNOWN_ELSEWHERE


def main() -> int:
    """배선과 경계 정의를 4축으로 대조하고 위반 수를 종료 코드로 낸다."""
    boundaries, known_elsewhere = load_guard_constants()

    agent_files = sorted(AGENTS_DIR.glob("*.md"))
    if not agent_files:
        print(f"검사 대상 없음: {AGENTS_DIR}/*.md", file=sys.stderr)
        return 1

    findings: list[str] = []
    wired: dict[str, str] = {}  # 배선 인자 -> 그 배선을 담은 파일명

    for path in agent_files:
        text = path.read_text(encoding="utf-8")
        matched = FRONTMATTER_RE.match(text)
        if matched is None:
            continue  # 프론트매터가 없으면 배선도 없다
        frontmatter = matched.group(1)

        arguments = WIRING_RE.findall(frontmatter)
        if not arguments:
            continue  # 이 가드를 배선하지 않은 워커 — 모집단 밖이다

        declared = NAME_RE.search(frontmatter)
        declared_name = declared.group(1) if declared else ""
        if not declared_name:
            findings.append(f"{path.name}: 프론트매터에 `name:`이 없다 — 대조 불가")

        for argument in arguments:
            label = argument or "(인자 없음)"
            # 축 1 — 배선 인자와 그 파일이 선언한 이름이 같은가.
            #        둘 다 유효한 워커명일 수 있어 축 2로는 안 잡힌다.
            if declared_name and argument != declared_name:
                findings.append(
                    f"{path.name}: 배선 인자 `{label}`가 이 파일의 "
                    f"`name: {declared_name}`와 다르다 — 엉뚱한 경계가 적용된다"
                )
            # 축 2 — 그 이름의 경계 정의가 있는가.
            if argument not in boundaries:
                hint = (
                    f"정본이 `{known_elsewhere[argument]}`다 — 배선을 그쪽으로 고쳐라"
                    if argument in known_elsewhere
                    else "`BOUNDARIES`에 등재하거나 배선 인자를 고쳐라"
                )
                findings.append(
                    f"{path.name}: 배선 인자 `{label}`가 `BOUNDARIES`에 없다 — {hint}"
                )
            wired.setdefault(argument, path.name)

    # 축 3 — 정의는 있는데 부르는 배선이 없는가(죽은 정의).
    findings.extend(
        f"BOUNDARIES `{worker}`: 이 인자로 부르는 배선이 없다 — "
        "그 워커 정의의 `hooks`를 잇거나 항목을 지워라"
        for worker in sorted(set(boundaries) - set(wired))
    )

    # 축 4 — 같은 워커가 두 상수에 동시에 있으면 정의가 둘이다.
    findings.extend(
        f"`{worker}`가 `BOUNDARIES`와 `KNOWN_ELSEWHERE`에 모두 있다 — "
        "같은 경계를 두 곳에 정의하지 않는다"
        for worker in sorted(set(boundaries) & set(known_elsewhere))
    )

    # 🔴 계수를 함께 낸다 — 위반 0건만 찍으면 빈 집합끼리 비교해도 초록이라
    #    「검사했다」와 「검사 대상이 없었다」가 구분되지 않는다.
    print(
        f"워커 파일 {len(agent_files)}개 · 가드 배선 {len(wired)}건 · "
        f"BOUNDARIES {len(boundaries)}종 · KNOWN_ELSEWHERE {len(known_elsewhere)}종"
    )
    if wired:
        print(f"배선된 워커: {' · '.join(sorted(wired))}")

    for finding in findings:
        print(finding)
    print(f"\n배선 정합 위반 {len(findings)}건", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
