#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""스킬 호출 경계 가드 — 에이전트 스코프 PreToolUse hook (`Skill` 도구).

왜 이 스크립트인가:
    `tools:`에 `Skill`을 열면 그 워커는 **세션에 보이는 전 스킬**에 접근할 수 있다.
    `skills:` 프론트매터는 화이트리스트가 아니라 **프리로드**이고(공식 문서),
    `disallowedTools`는 **도구 단위**라 스킬을 가르지 못한다.
    ⇒ 제한 수단이 없으면 경계는 지시문 문구뿐이고, 그것은 규율이지 강제가 아니다.

    실측(2026-08-24): `data-qa`가 본 스킬 목록은 **29종**이었다 —
    lock 등재 16종 + 하네스/플러그인 13종. 뒤 13종은 lock 밖·출처 미판정·
    `security` 미검토이며 그중 `update-config`는
    **`settings.json`의 permissions·hooks 편집 절차**를 가르친다.
    즉 통제 배선 자체를 겨냥한 문서가 워커 도달 범위에 들어온다.
    🔴 목록은 **워커마다 다르다**(`security`는 33종) — 세려면 그 워커에서 센다.

    이 가드가 그 축을 닫는다. 워커별 허용 스킬은 **각 워커 지시문의 §참고 스킬 표**에서
    직접 읽는다 — 표를 이 스크립트에 복사하면 두 곳이 드리프트하기 때문이다.
    지시문 표가 집행 정본이고 `docs/skills.md` §③은 파생 인덱스다.

    🔴 **이 가드가 보는 것은 `Skill` 도구 경로까지다.** 워커가 `Bash`로 자기 지시문 표를
    고치는 축은 이 가드 밖이고, 그건 경로 가드와 규율이 받는다.

배선 (각 워커의 프론트매터):
    hooks:
      PreToolUse:
        - matcher: "Skill"
          hooks:
            - type: command
              command: "$CLAUDE_PROJECT_DIR/scripts/skill_gate_guard.py"

    🔴 `command` 인용 규칙은 `settings.json`과 다르다 — 위 형태를 그대로 쓴다.
       틀리면 에러 없이 조용히 통과한다.
    🔴 대상 워커를 인자로 받지 않는다 — hook 입력의 `agent_type`이 실제 워커를 알려준다
       (2026-08-24 실측). 인자와 실제가 어긋날 여지를 없앤다.

입력 스키마 (2026-08-24 실측):
    {"tool_name": "Skill",
     "tool_input": {"skill": "<name>"},
     "agent_type": "<worker>", ...}

    🔴 필드는 `tool_input.skill`이다. `Edit`의 `file_path`·`NotebookEdit`의
       `notebook_path`처럼 키가 갈리는 축이라 추측하지 않고 실측했다.

fail-closed:
    파싱 실패·표 부재·빈 표·필드 부재는 전부 **`deny`** 다.
    가드가 "모르면 통과"시키면 그 순간 죽은 규칙이 된다.

검증 (2026-08-24, 프로덕션 경로 그대로):
    `data-qa` 표에서 한 행만 한시 제거하고 그 스킬을 호출시켜 **이 가드가 낸 문구 원문이
    워커 응답에 도달**하는 것을 관측했다. 🔴 변인을 **코드가 아니라 데이터 한 줄**로
    두는 것이 핵심이다 — 임시 가드를 따로 만들어 프로브하면
    관측 대상이 운영 경로와 달라진다.
"""

import json
import re
import sys
from pathlib import Path

# 지시문에서 §참고 스킬 절을 집어내는 패턴 — 헤딩부터 다음 `## ` 또는 EOF까지
SECTION_RE = re.compile(r"^## 참고 스킬.*?(?=^## |\Z)", re.DOTALL | re.MULTILINE)

# 표 2열의 스킬명(코드 스팬)만 뽑는다 — `| 상황 | `<skill>` | 하지 말 것 |`
SKILL_CELL_RE = re.compile(r"^\| [^|]+ \| `([a-z0-9][a-z0-9-]*)` \|", re.MULTILINE)

ESCALATE = (
    "표 밖 스킬이 필요하면 호출하지 말고 배정자(supervisor)에게 에스컬레이션하라 "
    "— 등재 여부 판정은 `/skill-audit` 소관이다."
)


def main() -> None:
    """stdin의 hook 입력을 읽어 스킬 호출을 allow/deny로 판정한다."""
    # ── 입력 파싱 (실패는 deny) ──────────────────────────────────────────
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        emit(
            "deny", "hook 입력을 파싱하지 못했다 — 호출을 허용하지 않는다(fail-closed)."
        )
        return

    tool_name = payload.get("tool_name", "")
    if tool_name != "Skill":
        # matcher 오배선 방어 — 이 가드는 `Skill`만 판정한다
        emit("allow", f"`{tool_name}`은 이 가드의 판정 대상이 아니다.")
        return

    skill = (payload.get("tool_input") or {}).get("skill", "")
    agent_type = payload.get("agent_type", "")

    if not skill or not agent_type:
        emit(
            "deny",
            f"판정에 필요한 값이 없다(skill={skill!r}, "
            f"agent_type={agent_type!r}) — fail-closed.",
        )
        return

    # ── 프로젝트 루트 결정 (hook 입력의 cwd를 우선, 없으면 이 파일 기준) ──
    cwd = payload.get("cwd")
    root = Path(cwd) if cwd else Path(__file__).resolve().parent.parent

    # ── 워커 지시문에서 허용 스킬 표를 읽는다 ────────────────────────────
    worker_md = root / ".claude" / "agents" / f"{agent_type}.md"
    if not worker_md.is_file():
        emit("deny", f"워커 지시문을 찾지 못했다({worker_md}) — 허용 목록을 못 읽는다.")
        return

    section = SECTION_RE.search(worker_md.read_text(encoding="utf-8"))
    if section is None:
        emit(
            "deny",
            f"`{agent_type}` 지시문에 §참고 스킬 절이 없다 "
            "— 등재 스킬이 없다는 뜻이므로 호출하지 않는다.",
        )
        return

    allowed = sorted(set(SKILL_CELL_RE.findall(section.group(0))))
    if not allowed:
        emit(
            "deny",
            f"`{agent_type}` §참고 스킬 표가 비어 있다 "
            "— 등재 0건이므로 스킬을 호출하지 않는다.",
        )
        return

    # ── 판정 ─────────────────────────────────────────────────────────────
    if skill in allowed:
        emit("allow", f"`{skill}`은 `{agent_type}` §참고 스킬 표에 등재돼 있다.")
        return

    emit(
        "deny",
        f"`{skill}`은 `{agent_type}` §참고 스킬 표에 없다. "
        f"등재분: {', '.join(allowed)}. {ESCALATE}",
    )


def emit(decision: str, reason: str) -> None:
    """Hook 결정을 stdout으로 낸다.

    유효값은 allow·deny·ask·defer 넷뿐이며, 어긋나면 출력 전체가 거부된 채
    도구가 그대로 진행한다(fail-open).
    """
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )


main()
