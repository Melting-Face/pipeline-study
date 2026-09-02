#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""워커 경로 경계 가드 — 에이전트 스코프 PreToolUse hook.

왜 이 스크립트인가:
    각 워커의 쓰기 범위는 규약으로 정해져 있으나(`docs/conventions/agents.md`
    §권한 매트릭스), 그 경계를 `permissions`로는 걸 수 없다 — `permissions`는
    **세션 전역**이라 특정 `subagent_type`에만 범위를 못 주고, `Edit(terraform/**)`를
    `deny`에 넣으면 `devops-engineer`까지 함께 막힌다.

    반면 **에이전트 정의(`.claude/agents/<worker>.md`) 안에 선언한 hook은
    그 subagent에만 걸린다.** 그래서 워커별 경로 강제의 유일한 수단이다.

    워커마다 스크립트를 복제하지 않고 **대상 워커를 인자로 받는다**(Rule of Three) —
    경계 표가 한 곳에 모여 있어야 규약 문서와 대조하기 쉽다.

배선 (각 워커의 프론트매터):
    hooks:
      PreToolUse:
        - matcher: "Edit|Write|NotebookEdit"
          hooks:
            - type: command
              command: "$CLAUDE_PROJECT_DIR/scripts/worker_path_guard.py <worker>"

    🔴 `command`의 인용 규칙은 `.claude/settings.json`과 **다르다.**
    `"\\"$CLAUDE_PROJECT_DIR\\"/scripts/…"` 처럼 안쪽 따옴표를 이스케이프하면
    프론트매터(YAML)에서는 벗겨지지 않아 경로가 깨지고, **에러 없이 그냥 통과**한다
    (2026-08-19 실측 — "막았다고 믿는데 안 막힌" 상태가 된다).
    배선을 바꾸면 반드시 §실발동 확인을 다시 돌린다.

한계(정직하게):
    워커들에게는 `Bash`가 있어 `sed`·리다이렉트 경유 쓰기는 이 matcher 밖이다.
    그 층은 `protected_paths_guard.py`(보호 경로)와 경계 지시문이 맡는다.
    **완전한 봉쇄가 아니라 도구 경로의 확정적 차단**이다.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn

# 워커별 저장소 **안** 경계. 정본은 docs/conventions/agents.md §권한 매트릭스.
#   allow  — 여기 나열된 접두어만 쓸 수 있다(그 외 전부 거부). 좁은 범위의 워커용.
#   deny   — 여기 나열된 접두어만 막는다(그 외 허용). 넓은 범위의 구현 워커용.
#   except — `allow`/`deny` **판정보다 먼저** 평가하는 구멍 막이. 넓은 `allow` 안에
#            박혀 있는 소수의 금지 항목을 파일 단위로 판다.
# 🔴 `except`는 왜 필요한가: `allow`는 디렉터리 접두어라 "이 디렉터리는 되는데 그 안의
#    이 파일만 안 된다"를 표현할 수 없었다. 그래서 그런 항목은 전부 **규율**로 남았고
#    (docs/security.md §공개물 반출 차단의 잔여 위험 행), 규율은 기계가 집행하지 않는다.
#    `deny` 축을 쓰면 그 워커의 `allow`가 통째로 사라지므로 별도 축이어야 한다.
# 🔴 접두어 끝의 `/`는 필수다 — 없으면 `docs/analyses_fake/`가 통과한다(실측 버그).
#    `allow`에 **파일 하나**를 열 때만 `/` 없이 적고, 그때는 **완전일치**로 본다 —
#    `README.md`를 접두어로 두면 `README.md.bak`까지 함께 열린다
#    (2026-08-20 `security` 지적).
#    `deny`에는 이 분기를 두지 않는다: 막는 쪽은 넓게 걸리는 편이 안전하고
#    (`.env` 접두어가 `.env.example`까지 막는 것은 의도된 여유다),
#    좁히면 경계가 조용히 샌다.
BOUNDARIES = {
    # 🔴 `analyst`는 여기 두지 않는다 — 정본이 **다른 가드**에 있다.
    #    사유와 처방은 아래 `KNOWN_ELSEWHERE`에 모여 있다(설명이 곧 그 상수의 정의다).
    # 데이터 엔지니어 — 인프라 선언은 devops-engineer 소관.
    # 🔴 `.github/`는 나중에 추가됐다. 그 전까지 **CI 워크플로에 소유자가 없었다** —
    #    이 표에 `.github/**`가 어느 워커에도 없어 `data-engineer`·`devops-engineer`
    #    **둘 다 쓸 수 있는 이중 소유**였다(`deny` 축은 열거되지 않으면 통과한다).
    #    `devops-engineer` 쪽은 손대지 않아 **결과적으로 단독 소유**가 된다.
    #    ⚠️ 이것은 `deny` 축의 구조적 성질이다 — **넓은 워커에 경계를 더하는 유일한
    #    방법은 「빼는 쪽」을 적는 것**이라, 새 최상위 디렉터리가 생길 때마다
    #    소유자를 정하지 않으면 조용히 공유된다. `.github/`는 그 사례 1호다.
    "data-engineer": {
        "deny": (
            "terraform/",
            "k8s/",
            "compose.yml",
            ".env",
            ".claude/",
            ".github/",
        ),
    },
    # 데브옵스 엔지니어 — 파이프라인 정의·분석 산출물은 남의 소관.
    # 🔴 `dagster_project/`·`dbt/`로 적혀 있었으나 **둘 다 추적 파일 0건**이었다
    #    (2026-08-20 `tech-writer` 반환에서 발견 → `git ls-files`로 재확인).
    #    실제 코드는 `dagster/dockerfile.d/src/` 아래에 있어 **겨냥이 빗나가 있었다** —
    #    배선을 이어도 이 두 접두어는 아무것도 막지 못했다.
    #    "배선됨"과 "겨냥이 맞음"은 다른 층이다
    #    (이번 미션에서 세 번째 "막았다고 믿는" 형태).
    #    `Dockerfile`·`.dockerignore`는 devops 소관이라 `src/`만 막는다.
    "devops-engineer": {
        "deny": (
            "dagster/dockerfile.d/src/",
            "notebooks/",
            "docs/analyses/",
            ".env",
            ".claude/",
        ),
    },
    # 기록관 — 저널은 저장소 **밖** 볼트에 쓴다. 저장소 안에는 쓸 것이 없다.
    "archivist": {"allow": ()},
    # 데이터 추출자 — 요구사항 명세대로 데이터를 뽑아 **저장소 밖**으로만 낸다.
    # 🔴 `analyst`와 방법(읽기 조회·SQL)은 겹치지만 **노출 등급이 다르다.**
    #    분리의 근거는 업무가 아니라 통제다(2026-08-22 supervisor 결정).
    #    실측 근거(2026-08-22 `git check-ignore`): `notebooks/out.csv`·
    #    `notebooks/out.parquet`·`docs/analyses/out.csv`가 **무시되지 않는다.**
    #    `.gitignore`에는 `data/` 한 줄뿐이었고 `nbstripout`은 `.ipynb` 셀
    #    출력만 걷어낸다(gitleaks는 헬스 데이터를 못 잡는다).
    #    ⇒ 추출물이 저장소 안에 착지할 경로를 아예 주지 않는다.
    "data-extractor": {"allow": ()},
    # 리서처 — 읽기 전용. `disallowedTools`가 1차 방어이고 이건 2차(심층 방어)다.
    # 둘 다 두는 이유: `disallowedTools`의 실효는 워커마다 실측해야 확정되는데
    # (§권한 매트릭스 — 선언한 tools가 전부 실재하지는 않는다), 이 워커는 유일하게
    # **외부 네트워크에 접촉**하므로 가져온 내용이 파일로 착지하는 경로를 남기지 않는다.
    "researcher": {"allow": ()},
    # 테크라이터 — 저장소의 **문서 소유자**. `docs/` 전체와 최상위 `README.md`를 쓴다.
    # 🔴 이 경계는 기계가 가르지 못하는 두 가지를 **규율**로 남긴다(지시문 §역할 경계):
    #   ① `docs/analyses/`는 analyst와 **이중 소유**다 — 내부 결론의 저자는 analyst이고
    #      tech-writer는 표현만 손본다(수치·결론 변경 금지).
    #   ② `docs/conventions/`는 **규약 정본**이라 supervisor 결정을 받아적을 뿐,
    #      스스로 규칙을 만들거나 바꾸지 않는다.
    # 🔴 `README.md`는 디렉터리가 아니라 **파일 단위**다
    #    — 접두어로 적으면 `.bak`까지 열린다.
    # 🔴 `except` 2종은 **판정 근거 문서**다(2026-08-22 신설). `docs/security.md`는
    #    ISMS-P 통제 매핑·반출 금지선, `docs/skills.md`는 스킬 출처 등급을 담는데,
    #    2026-08-20 쓰기 범위 확대로 **판정 대상이 자기 판정 근거를 고칠 수 있는**
    #    상태가 됐다(docs/security.md §공개물 반출 차단 ↳ 잔여 위험 행 — 🔴 규율).
    #    같은 날 정본 게이트(`protected_paths_guard.py` CANON_PATTERNS)에서
    #    `docs/conventions/**`·`docs/architectures/**`를 뺐으므로, 규율에만 기대는
    #    면이 늘어난 만큼 **가장 위험한 2종은 기계 강제로 승격**한다.
    # 🔴 `docs/conventions/**`는 여기 넣지 않는다 — 링크·목차·요약 동기화(doc-sync
    #    체인)가 이 워커의 정당한 업무라, 막으면 매 교정이 supervisor 왕복이 된다.
    #    그쪽은 지시문 §역할 경계의 규율로 남는다("규칙 신설·변경은 supervisor").
    # 🔴 `wiki/`는 2026-08-27 추가. GitHub 위키로 **미러돼 나가는 원본**이라 노출 등급이
    #    `docs/`보다 높지만 소유 축은 같다 — 독자가 `docs/posts/`와 같은
    #    "저장소를 모르는 사람"이다.
    #    CLAUDE.md **"매체는 축이 아니다"** — 새 워커를 만들지 않고 이 워커에 붙인다.
    #    통제는 워커 축이 아니라 **게이트 축**에서 진다: 커밋 전 `security` 컨펌
    #    (publishing.md §4-1 — 미러 자동화는 *배달*이지 *승인*이 아니다).
    "tech-writer": {
        "allow": ("docs/", "README.md", "wiki/"),
        # ✅ **라이브 실발동 확인**(2026-08-22 3셀 대조, `Edit` 도구 경로).
        #    `except`에 프로브 경로를 **한시적으로** 올려 `docs/` 하위인데도 `deny`가
        #    나는 것과, 그 문구가 `allow` 분기와 **다른 분기**임을 확인한 뒤 내렸다.
        #    🔴 프로브를 `except`가 아닌 일반 경계로 돌리면 이 축은 관측되지 않는다 —
        #    `docs/` 하위는 `allow`가 통과시켜 두 분기가 갈리지 않기 때문이다.
        # ✅ **`permissions.allow`보다 이 hook의 `deny`가 이긴다**(2026-08-22 실측).
        #    `.claude/settings.json`에 `Edit(docs/**)`를 `allow`로 넣은 상태에서
        #    `except` 경로를 쳐도 **차단됐고 파일 내용도 안 바뀌었다**(대조군: 같은
        #    세션의 `docs/` 일반 경로 쓰기는 성공 — 죽은 가드가 아니라 선별 차단).
        #    🔴 이 순서가 반대였다면 `allow` 한 줄이 **워커 경계 전체를 무력화**했다.
        #    편의를 위해 `allow`를 넓힐 때는 이 순서를 **다시 실측**하고 넓힌다.
        # 🔴 `docs/skills/`는 **끝에 `/`가 있어 접두어**다(아래 매칭부) —
        #    허브 + 하위 4문서로 쪼갤 때 함께 넣었다. 완전일치만 두면 쪼개진 파일이
        #    경계에서 빠져 **판정 대상이 자기 판정 근거를 쓸 수 있게 된다.**
        #    링크 검사도 doc_lint도 이 구멍을 못 잡는다 — **조용한 통제 후퇴**다.
        "except": (
            "docs/security.md",
            "docs/skills.md",
            "docs/skills/",
        ),
    },
}
# 🔴 **워커를 없애면 이 표에서도 지운다**(2026-08-23 `director` 폐기 시 남을 뻔했다).
#    반대로 **여기 추가하면 그 워커 정의의 `hooks`도 함께 잇는다**(§배선 감사).
#    ⇒ 이 양방향을 이제 **커밋 시점에 `scripts/worker_wiring_check.py`가 대조**한다.
#    예전에는 표와 배선이 갈려도 아무 신호가 없었다 — 죽은 항목은 부를 워커가 없어
#    조용했고, 반대로 배선만 있고 정의가 없으면 `main()`이 fail-open으로 통과시켰다.

# 정본이 **다른 가드**에 있는 워커 → 그 정본 경로.
# 🔴 여기 걸려도 **통과가 아니라 `deny`** 다.
#    `analyst`가 여기 있는 사연: `.claude/agents/analyst.md`의 hook은
#    `analyst_path_guard.py`를 부르므로 `worker_path_guard.py analyst`는
#    **호출처가 0건**이다. 그런데도 같은 경계(`notebooks/`·`docs/analyses/`)가
#    `BOUNDARIES`에 중복 선언돼 있었고, 죽은 항목은 아무 신호도 내지 않아
#    **한쪽만 고치면 "고쳤다고 믿는" 상태**가 됐다.
#    ⇒ 그 항목을 지운 자리가 여기다: **같은 경계를 두 곳에 정의하지 않는다**
#    ([permissions.md](../docs/conventions/agents/permissions.md) §경로 경계).
#    경계를 바꿔야 하면 `analyst_path_guard.py`의 `ALLOWED_PREFIXES`를 고친다.
# 🔴 **왜 통과가 아니라 `deny`인가**: 이 이름으로 호출이 실제로 왔다면 그것은
#    **배선이 틀렸다는 뜻**이다(정상 경로라면 전용 가드가 불린다). 통과시키면
#    이 커밋이 닫으려는 fail-open이 그대로 되살아난다. 그래서 `deny`하되
#    **사유를 일반 미정의와 다르게** 낸다 — 일반 사유("`BOUNDARIES`에 등재하라")를
#    그대로 따르면 위에서 지운 중복 정의를 되살리기 때문이다.
#    **두 분기가 같은 문구를 내면 축이 갈리지 않은 것이다.**
KNOWN_ELSEWHERE = {
    "analyst": "scripts/analyst_path_guard.py",
}

# 저장소 **밖**에서 예외로 허용할 절대경로 접두어. 미지정 워커는 사용자 확인(`ask`).
# archivist는 Claude 전용 저널·템플릿과 공유 MOC만 쓴다. 볼트 전체를 열면
# Codex 기록이나 보안 posture까지 수정할 수 있어 런타임 분리가 권한 분리가 되지 않는다.
OBSIDIAN_ROOT = Path(
    os.environ.get("OBSIDIAN_VAULT") or str(Path.home() / "obsidian")
).expanduser()
DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JOURNAL_NAME_RE = re.compile(r"^(\d{2})-[a-z0-9][a-z0-9-]*\.md$")

OUTSIDE_ALLOW = {
    # 🔴 저널은 접두어로 열지 않는다. 평탄화(2026-08-27) 이후 두 런타임이 **같은
    #    `agents/<날짜>/`를 공유**하므로, `agents/`를 접두어로 열면 Claude archivist가
    #    **Codex 기록까지** 쓸 수 있어 런타임 분리가 권한 분리가 되지 않는다.
    #    ⇒ 경계 축을 **경로에서 내용으로** 옮겨 아래 `is_claude_journal_path()`가 진다.
    #    (`.codex/hooks/worker_path_guard.py`가 먼저 푼 문제의 거울상 — 그쪽이 이미
    #     평탄이라 같은 판정을 갖고 있고, 둘은 **짝으로 유지**해야 한다.)
    "archivist": (
        str(OBSIDIAN_ROOT / "agents" / "_MOC.md"),
        str(OBSIDIAN_ROOT / "agents" / "_TEMPLATE.md"),
    ),
    # 🔴 추출물은 **원천 진료 데이터**다(DUA·재식별 금지 — docs/security.md).
    #    저장소 밖 단 한 곳으로만 나간다(그 밖은 아래 `OUTSIDE_STRICT`가 막는다).
    #    `archivist`와 형태는 같되 성격이 반대다 — 저쪽은 기록을 **남기려고**
    #    밖에 쓰고, 이쪽은 데이터를 저장소에 **남기지 않으려고** 밖에 쓴다.
    "data-extractor": (
        os.environ.get("DATA_EXTRACT_DIR") or str(Path.home() / "extracts"),
    ),
}

# 저장소 밖에서 **허용 목록을 벗어나면 `ask`가 아니라 `deny`** 로 처리할 워커.
# 🔴 기본값(`ask`)은 auto 모드에서 **막히지 않는다** — 분류기가 파일 도구의 `ask`를
#    경로 민감도와 무관하게 흡수한다(CLAUDE.md §강제 수단, 2026-08-19 실측).
#    그래서 "사람이 판단한다"는 문구는 원천 진료 데이터에 대해서는 **죽은 규칙**이 된다.
#    ⚠️ `archivist`는 여기 넣지 않는다 — 저널은 진료 데이터가 아니고, 볼트 경로가
#    환경마다 달라 `ask`로 사람에게 묻는 편이 맞다(둘의 성격이 반대다).
OUTSIDE_STRICT = frozenset({"data-extractor"})


def read_journal_agent(path: Path) -> str:
    """기존 저널 frontmatter의 `agent:` 값을 반환한다. 없으면 빈 문자열."""
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


def is_claude_journal_path(target: Path) -> bool:
    """Claude archivist가 무승인으로 쓸 수 있는 `agents/<날짜>/<NN>-<slug>.md`인가.

    🔴 판정 축이 **경로가 아니라 내용**이다. 평탄화 이후 두 런타임이 같은 폴더를
    공유하므로 경로만으로는 남의 기록을 가릴 수 없다.
      - **기존 파일**: frontmatter `agent:`가 `claude-code`일 때만 허용
        (= Codex 저널을 덮어쓰려 하면 여기서 걸린다).
      - **신규 파일**: 그 날짜의 **다음 번호**일 때만 허용
        (= 이미 있는 번호를 재사용해 남의 것을 밀어내지 못한다).
    """
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
        return read_journal_agent(target) == "claude-code"

    numbers = [
        int(existing.group(1))
        for path in target.parent.glob("*.md")
        if (existing := JOURNAL_NAME_RE.fullmatch(path.name)) is not None
    ]
    return int(matched.group(1)) == max(numbers, default=0) + 1


# PreToolUse 입력에서 대상 경로가 담기는 키 — 도구마다 이름이 다르다.
PATH_KEYS = ("file_path", "notebook_path", "path")

# 🔴 가드 스크립트 자신은 **어느 워커도 고치지 못한다**
#    (2026-08-20 `data-engineer`의 Δ 반환에서 발견).
#    경계를 강제하는 스크립트가 정작 경계에 없었다 — `data-engineer`의 deny에는
#    `scripts/`가 빠져 있고, `devops-engineer`는 `scripts/`를 정당하게 소유하므로
#    디렉터리를 통째로 막을 수도 없다.
#    그래서 **접두어가 아니라 접미어**로 건다(`deny`/`allow` 분기보다 먼저 평가).
#    `permissions.ask`의 `Edit(scripts/*_guard.py)`가 2층에 있지만 1층이 비어 있었다.
GUARD_SUFFIX = "_guard.py"


def emit(decision: str, reason: str) -> NoReturn:
    """Hook 결정을 stdout에 내고 종료한다.

    🔴 `decision`의 유효 enum은 `allow`·`deny`·`ask`·`defer` **넷뿐**이다.
    벗어나면 출력 객체 전체가 검증에 실패해 **결정이 사라진 채 도구가 진행한다**
    (fail-open, 2026-08-19 실측). 새 값을 넣기 전에 정본을 확인한다.
    """
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                },
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


def main() -> None:
    """워커의 파일 쓰기가 경계 밖이면 차단하거나 사용자 확인으로 올린다."""
    worker = sys.argv[1] if len(sys.argv) > 1 else ""
    # 🔴 **미정의 워커를 통과시키지 않는다**(fail-closed, Issue #35).
    #    배선 오타 한 글자로 그 워커의 경계가 통째로 사라지는데 아무 신호가 없었다 —
    #    이 가드가 워커별 경로 강제의 **유일한** 수단이라 알려주는 다른 층도 없다.
    #    짝 가드 `.codex/hooks/worker_path_guard.py`가 같은 자리에서 이미 `deny`이고
    #    형제 `skill_gate_guard.py`도 미식별을 전부 `deny`한다 — **이쪽만 반대였다.**
    # 🔴 「경고」는 선택지가 아니었다. 경고에 해당하는 `ask`는 auto 모드 분류기가
    #    파일 도구에서 흡수하므로(아래 `OUTSIDE_STRICT` 주석과 같은 근거),
    #    `ask`를 고르면 **"신호를 냈다고 믿는데 안 나는"** 상태가 된다 —
    #    **갭의 동어반복**이다.
    # 🔴 조회는 **대소문자를 구분한 채로 둔다**(경로 비교의 `deny` 분기와 반대 방향).
    #    소문자화하면 `DevOps-Engineer` 같은 배선 오타가 정상으로 통과해 fail-open이
    #    되살아난다. 여기서는 걸러지는 쪽이 fail-closed다 —
    #    **일관성을 이유로 뒤집지 마라.**
    # ⚠️ 이 커밋이 닫은 것은 **워커명 축 하나**다. 아래 입력 파싱 실패·경로 키 부재의
    #    fail-open은 그대로 남는다(빠뜨린 것이 아니라 범위 밖 — 안 건드린다).
    if elsewhere := KNOWN_ELSEWHERE.get(worker):
        emit(
            "deny",
            f"`{worker}`의 경로 경계 정본은 이 가드가 아니라 `{elsewhere}`다 — "
            "이 이름으로 호출이 왔다는 것은 **배선이 틀렸다**는 뜻이다. "
            f"고칠 곳은 `.claude/agents/{worker}.md`의 hook `command`이며, "
            "🔴 `BOUNDARIES`에 추가해서 풀지 마라 — "
            "같은 경계를 두 곳에 정의하면 한쪽만 고치는 사고가 재발한다.",
        )
    boundary = BOUNDARIES.get(worker)
    if boundary is None:
        emit(
            "deny",
            f"경계가 정의되지 않은 워커다: `{worker or '(인자 없음)'}`. "
            "배선 오타이거나 `BOUNDARIES` 항목이 빠졌다 — 어느 쪽이든 "
            "이 워커의 경로 경계가 **사라진 상태**라 통과시키지 않는다. "
            f"정의된 워커: {' · '.join(sorted(BOUNDARIES))}. "
            "새 워커면 `BOUNDARIES`에, 정본이 다른 가드면 `KNOWN_ELSEWHERE`에 "
            "등재하고 그 워커 정의의 `hooks`도 함께 잇는다 — "
            "🔴 다만 **직접 고치지 말고 변경안을 반환해 supervisor 승인을 받아라.** "
            "이 표를 넓히는 것은 경계를 푸는 것이고, 차단당한 당사자가 할 일이 아니다.",
        )

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # 입력을 못 읽으면 통과 — 가드가 작업을 멈추게 하지 않는다

    # 🔴 **배선 인자와 실제 워커를 대조한다.** 위 두 분기는 「표에 없는 이름」만
    #    잡으므로, `devops-engineer.md`가 실수로 `data-engineer`를 넘기면
    #    **둘 다 유효해서 통과**하고 **엉뚱한 경계가 조용히 적용된다.**
    #    그 축은 여기서만 갈린다.
    #    `agent_type`은 하네스가 넣는 값이라 배선 오타의 영향을 받지 않는다
    #    (`skill_gate_guard.py`가 애초에 인자를 안 받는 근거와 같다).
    # 실측(2026-09-01, `tech-writer`의 `Edit` 프로브 — 프로덕션 hook 경로 그대로):
    #    payload 키 12종에 `agent_type`이 있고 값은 `'tech-writer'`로 인자와 일치했다.
    #    ⚠️ 그 실측은 **`Edit`** 기준이다 — `Write`·`NotebookEdit`은 따로 재지 않았다.
    # 🔴 그래서 **인자를 없애지 않고 둘 다 남긴다.** 하나로 줄이면 대조할 상대가
    #    사라진다(`skill_gate_guard.py`와 갈리는 지점 — 저쪽은 배선처가 인자를
    #    주지 않아 이 축 자체가 없다).
    # ⚠️ `agent_type`이 **없으면 통과시킨다**(서브에이전트 밖 호출일 수 있다) —
    #    이 sub-축은 fail-open이고, 그 사실을 여기 적어 「빠뜨린 것」과 구분한다.
    if (actual := payload.get("agent_type", "")) and actual != worker:
        emit(
            "deny",
            f"배선 인자와 실제 워커가 다르다 — 인자 `{worker}` / 실제 `{actual}`. "
            f"`.claude/agents/{actual}.md`의 hook `command`가 남의 이름을 넘기고 있어 "
            "**엉뚱한 경계가 적용되는 중**이다(둘 다 유효한 이름이라 조용히 통과했다). "
            f"인자를 `{actual}`로 고쳐라. "
            "정본은 docs/conventions/agents.md §권한 매트릭스다.",
        )

    tool_input = payload.get("tool_input") or {}
    raw_path = next((tool_input[k] for k in PATH_KEYS if tool_input.get(k)), "")
    if not raw_path:
        sys.exit(0)

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = project_dir / target
    target = target.resolve()

    # 🔴 저장소 경계 판정도 **대소문자를 무시**해야 한다(2026-08-20 G2 지적 M6).
    #    `is_relative_to`는 대소문자를 구분하는데 macOS 파일시스템은 무시한다.
    #    그래서 `<PROJECT_DIR의 대소문자 변형>/terraform/main.tf`가 **같은 실파일인데
    #    "밖"으로 판정**돼 `deny`가 아니라 `ask`로 **강등**됐다(실측: inode 동일).
    #    더 나쁜 것은 아래 문구다 — "저장소 밖 … 임시 파일이면 승인하고"가 뜨는데
    #    실제로는 **저장소 안 금지 경로**라, 사람을 **승인 쪽으로 유도**한다.
    #    강등된 게이트보다 **틀린 방향으로 유도하는 게이트**가 더 위험하다.
    #    길이는 대소문자로 바뀌지 않으므로 접두어 길이로 잘라 **원본 표기를 보존**한다
    #    (allow 분기는 대소문자를 구분해야 해서 소문자화한 값을 쓰면 안 된다).
    project_text = project_dir.as_posix()
    target_text = target.as_posix()
    inside = target_text.lower().startswith(project_text.lower() + "/")

    if not inside:
        # 저장소 밖 — 워커별 예외 목록에 있으면 통과, 아니면 사람이 판단한다.
        allowed = OUTSIDE_ALLOW.get(worker, ())
        roots = (Path(prefix).expanduser().resolve() for prefix in allowed)
        if any(target.is_relative_to(root) for root in roots):
            sys.exit(0)
        # 저널은 접두어가 아니라 **내용**으로 판정한다(공유 폴더라 경로로는 못 가른다).
        if worker == "archivist" and is_claude_journal_path(target):
            sys.exit(0)
        if worker in OUTSIDE_STRICT:
            # 🔴 `ask`로 두면 **막히지 않는다** — auto 모드 분류기가 파일 도구의 `ask`를
            #    경로 민감도와 무관하게 흡수한다(CLAUDE.md §강제 수단).
            #    원천 진료 데이터에서는 그 흡수가 곧 **무통제 반출**이라 `deny`다.
            #    허용 경로를 넓혀야 하면 `OUTSIDE_ALLOW`를 고치지 이 분기를 풀지 않는다.
            emit(
                "deny",
                f"`{worker}`는 지정된 반출 경로 밖에 쓸 수 없다: {target}. "
                f"허용: {' · '.join(allowed) if allowed else '없음'}. "
                "추출물은 원천 진료 데이터이며 반출 경로는 정본이 정한다 — "
                "다른 경로가 필요하면 계획을 반환해 supervisor 승인을 받아라.",
            )
        # 🔴 값은 `ask`다 — 유효 enum은 allow·deny·ask·defer뿐이고, 벗어나면 출력
        #    전체가 검증 실패해 **결정이 사라진 채 통과**한다(fail-open).
        #    2026-08-19 실측.
        decision = "ask"
        reason = (
            f"`{worker}`가 저장소 밖 경로에 쓰려 한다: {target}. "
            "임시 파일이면 승인하고, 아니면 거부하라."
        )
    elif relative_guard := (
        target_text[len(project_text) + 1 :]
        if target_text.lower().endswith(GUARD_SUFFIX)
        else ""
    ):
        # 가드 자신 — 워커 종류와 무관하게 막는다(위 GUARD_SUFFIX 주석).
        decision = "deny"
        reason = (
            f"`{worker}`는 가드 스크립트 `{relative_guard}`를 고칠 수 없다. "
            "경계를 강제하는 스크립트는 어느 워커의 소관도 아니다 — "
            "변경안을 반환해 supervisor가 `security` 컨펌 후 반영한다."
        )
    elif blocked := next(
        (
            item
            for item in boundary.get("except", ())
            # 🔴 여기는 **대소문자를 무시한다** — `deny` 분기와 같은 방향(막는 쪽)이다.
            #    macOS 파일시스템이 대소문자를 무시하므로 `docs/Security.md`가 같은
            #    실파일에 착지하는데 구분해 비교하면 **그대로 통과**한다.
            #    막는 쪽의 과잉은 fail-closed라 안전하다(§BOUNDARIES 주석의 두 방향).
            if (
                target_text[len(project_text) + 1 :].lower().startswith(item.lower())
                if item.endswith("/")
                else target_text[len(project_text) + 1 :].lower() == item.lower()
            )
        ),
        "",
    ):
        # `allow`/`deny` 판정보다 **먼저** 평가한다 — 넓은 `allow` 안에 박힌 구멍이라
        # 뒤에 두면 `allow`가 먼저 통과시켜 이 축이 통째로 죽는다.
        decision = "deny"
        reason = (
            f"`{worker}`는 `{blocked}`를 쓸 수 없다 — 이 문서는 "
            "**그 워커를 판정하는 근거**다(통제·공급망 정본). "
            "판정 대상이 판정 기준을 고치면 통제가 성립하지 않는다. "
            "문안 정합조차 여기서는 예외가 아니다 — 변경안을 반환해 supervisor가 "
            "`security` 컨펌 후 반영한다. "
            "정본은 docs/conventions/agents.md §권한 매트릭스다."
        )
    else:
        relative = target_text[len(project_text) + 1 :]
        if "allow" in boundary:
            scope = boundary["allow"]
            # 🔴 `startswith(scope)` 하나로 두면 파일 항목이 접두어가 된다 —
            #    `README.md`가 `README.md.bak`·`README.mdx`까지 열어준다
            #    (2026-08-20 `security` 지적 ⓔ. 주석만 먼저 들어가고 이 분기가
            #    빠져 있어 "막았다고 믿는" 상태로 한 차례 남았었다).
            # 🔴 여기는 **대소문자를 구분한 채로 둔다**(아래 deny와 반대 방향이다).
            #    `DOCS/x.md`가 안 걸려 거부되는 쪽이 fail-closed이고, 소문자화하면
            #    대소문자 구분 파일시스템(Linux CI)에서 **진짜 다른 디렉터리를
            #    열어주는** fail-open이 된다.
            permitted = any(
                relative.startswith(item) if item.endswith("/") else relative == item
                for item in scope
            )
            # 라벨을 붙인다 — 없으면 "…쓸 수 없다. docs/posts/."처럼 읽혀
            # 그 경로가 금지인지 허용인지 뒤집혀 읽힌다(2026-08-20 실발동 로그 관측).
            scope_text = (
                f"쓸 수 있는 곳: {' · '.join(scope)}"
                if scope
                else "이 워커는 저장소 안에 쓸 수 있는 경로가 없다"
            )
        else:
            scope = boundary["deny"]
            # 🔴 여기는 **대소문자를 무시한다**(위 allow와 반대 방향이다). macOS
            #    파일시스템은 대소문자를 무시해 `Terraform/main.tf`·`.CLAUDE/`가
            #    실제로 금지 경로에 착지하는데, 대소문자를 구분해 비교하면 **통과한다**
            #    (2026-08-20 `security` 사후 컨펌 M5 — 합성 페이로드로 실측).
            #    소문자화하면 구분 파일시스템에서 무관한 `Terraform/`까지 막지만,
            #    막는 쪽의 과잉은 fail-closed라 안전하다.
            # 🔴 **두 분기의 안전 방향이 반대**라는 것이 이 가드의 핵심이다 —
            #    `3885700`은 "막는 쪽은 넓게 거는 편이 안전하니 분기를 두지 않는다"고
            #    적었는데, 대소문자 축에서는 그 `deny`가 오히려 뚫려 있었다.
            #    넓게 걸려면 **넓게 걸리도록 비교**해야 한다 — 의도만으로는 안 넓어진다.
            lowered = relative.lower()
            permitted = not lowered.startswith(tuple(item.lower() for item in scope))
            scope_text = f"금지: {' · '.join(scope)}"
        if permitted:
            sys.exit(0)
        decision = "deny"
        reason = (
            f"`{worker}`는 `{relative}`를 쓸 수 없다. {scope_text}. "
            "정본은 docs/conventions/agents.md §권한 매트릭스다 — "
            "필요하면 변경안을 반환해 소관 워커에 재배정하라."
        )

    emit(decision, reason)


if __name__ == "__main__":
    main()
