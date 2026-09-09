#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""워커 쓰기 경계의 **단일 출처** — 두 런타임 짝 가드가 함께 읽는 데이터 모듈.

왜 이 파일이 생겼는가 (Issue #53):
    `scripts/worker_path_guard.py`(Claude)와 `.codex/hooks/worker_path_guard.py`
    (Codex)는 **같은 표를 두 번 적고** 있었다. 그 결과 값이 갈렸고 **갈렸다는 신호가
    어디에서도 나지 않았다** — 눈으로 두 번 비교한 세션이 각각 「2건」에서 멈췄는데
    기계로 대조하니 실제로는 더 많았다.

    이 저장소는 이미 *"같은 경계를 두 곳에 정의하지 않는다"* 를 규약으로 갖는다
    (`docs/conventions/agents/permissions.md` §경로 경계). 짝 가드는 그 규약의
    **예외**로 존재했고, 예외에 대조 수단이 없었다. ⇒ 예외를 없앤다.

무엇을 담고 무엇을 안 담는가:
    담는 것은 **표**다 — 워커별 경계·저장소 밖 허용·통제 파일 목록.
    담지 않는 것은 **판정 로직**이다. 두 런타임은 입력 형태(`tool_input` 키 vs
    `apply_patch` 헤더)와 결정 어휘(`allow/deny/ask/defer` vs `deny`만)가 달라
    로직까지 합치면 분기가 늘어 오히려 읽기 어려워진다.
    ⚠️ 그래서 **표가 같아도 매칭이 갈리면 결과는 갈린다.** 그 축은 이 파일이 아니라
    `scripts/tests/test_worker_boundaries.py`가 대조군 셀로 본다.

🔴 이 파일 자체가 통제 배선이다:
    아래 `CONTROL_GLOBS`에 자기 자신이 들어 있고, `.claude/settings.json`의
    `permissions.ask`와 `CLAUDE.md` §정본 게이트에도 등재돼 있다. **셋을 한 벌로**
    유지한다 — 표를 가드 밖으로 뽑아낸 것이 곧 구멍이 되면 안 된다.
"""

from fnmatch import fnmatchcase
from pathlib import Path

# 워커별 저장소 **안** 경계. 규약 정본은 docs/conventions/agents.md §권한 매트릭스.
#   allow  — 나열된 접두어만 쓸 수 있다(그 외 전부 거부). 좁은 범위의 워커용.
#   deny   — 나열된 접두어만 막는다(그 외 허용). 넓은 범위의 구현 워커용.
#   except — `allow`/`deny` **판정보다 먼저** 평가하는 구멍 막이.
#
# 🔴 접두어 끝의 `/`는 필수다 — 없으면 `docs/analyses_fake/`가 통과한다(실측 버그).
#    `allow`에 **파일 하나**를 열 때만 `/` 없이 적고, 그때는 **완전일치**로 본다 —
#    `README.md`를 접두어로 두면 `README.md.bak`까지 함께 열린다.
#    `deny`에는 그 분기를 두지 않는다: 막는 쪽은 넓게 걸리는 편이 안전하다.
#
# 🔴 `deny` 축에 경계를 더하는 방법은 **「빼는 쪽」을 적는 것뿐**이다. 열거되지 않은
#    경로는 통과하므로, 새 최상위 디렉터리가 생길 때 소유자를 정하지 않으면
#    **조용히 공유된다.** `.github/`가 그 사례 1호였다.
COMMON: dict[str, dict[str, tuple[str, ...]]] = {
    # 데이터 엔지니어 — 인프라 선언은 devops-engineer 소관.
    # 🔴 `.github/`는 CI 워크플로에 **소유자가 없던** 것을 단독 소유로 확정한
    #    조치다. Claude 쪽에만 들어가 있어 Codex `data-engineer`는 CI 워크플로를
    #    쓸 수 있었다 — Issue #53이 지목한 **실제 통제 갭**이고 여기서 닫힌다.
    # 🔴 `.codex/`는 반대 방향의 누락이었다. Codex 쪽에만 있어 **Claude 워커가
    #    Codex 통제 배선을 고칠 수 있었다.** Issue #53 본문은 이것을 "런타임 고유
    #    경로라 의도된 비대칭"으로 분류했으나 **뒤집힌다** — `.claude/`를 막는
    #    사유(통제 배선은 어느 워커의 소관도 아니다)가 `.codex/`에 그대로 성립한다.
    #    `settings.json`의 `Edit(.codex/**)`는 `ask`라 방어선이 아니다 —
    #    auto 모드 분류기가 파일 도구의 `ask`를 흡수한다.
    "data-engineer": {
        "deny": (
            "terraform/",
            "k8s/",
            "compose.yml",
            ".env",
            ".claude/",
            ".codex/",
            ".github/",
        ),
    },
    # 데브옵스 엔지니어 — 파이프라인 정의·분석 산출물은 남의 소관.
    # 🔴 `dagster_project/`·`dbt/`로 적혀 있던 시절이 있는데 **둘 다 추적 파일
    #    0건**이라 아무것도 막지 못했다("배선됨"과 "겨냥이 맞음"은 다른 층이다).
    #    실제 코드는 `dagster/dockerfile.d/src/` 아래다.
    "devops-engineer": {
        "deny": (
            "dagster/dockerfile.d/src/",
            "notebooks/",
            "docs/analyses/",
            ".env",
            ".claude/",
            ".codex/",
        ),
    },
    # 기록관 — 저널은 저장소 **밖** 볼트에 쓴다. 저장소 안에는 쓸 것이 없다.
    "archivist": {"allow": ()},
    # 데이터 추출자 — 명세대로 뽑아 **저장소 밖**으로만 낸다.
    # 🔴 `analyst`와 방법(읽기 조회·SQL)은 겹치지만 **노출 등급이 다르다.**
    #    분리 근거는 업무가 아니라 통제다. 추출물이 저장소 안에 착지할 경로를
    #    아예 주지 않는다(`notebooks/out.csv`는 `.gitignore`에 안 걸린다 — 실측).
    "data-extractor": {"allow": ()},
    # 리서처 — 읽기 전용. `disallowedTools`가 1차 방어이고 이건 2차(심층 방어)다.
    # 🔴 Codex 쪽에는 이 항목이 **없었다.** 동작은 같았는데(미등재 워커를 `deny`)
    #    그것은 **암묵 의존**이다 — 미등재의 fail 방향이 언젠가 바뀌면 이 워커의
    #    경계가 신호 없이 사라진다. 값이 아니라 **의존 형태**를 고치려고 등재한다.
    #    ⇒ Issue #53이 이 축을 "갭"으로 적은 것은 값 기준으로는 틀렸다.
    "researcher": {"allow": ()},
    # 테크라이터 — 저장소의 **문서 소유자**.
    # 🔴 `README.md`는 디렉터리가 아니라 **파일 단위**다(접두어면 `.bak`까지 열린다).
    # 🔴 `wiki/`는 GitHub 위키로 **미러돼 나가는 원본**이라 노출 등급이 `docs/`보다
    #    높지만 소유 축은 같다(CLAUDE.md "매체는 축이 아니다"). 통제는 워커 축이
    #    아니라 **게이트 축**(커밋 전 `security` 컨펌)에서 진다.
    #    Codex 쪽에 없어 그쪽 tech-writer만 못 쓰고 있었다 — 대칭으로 맞춘다.
    #    🔴 **이 항목만 경계를 넓힌다**(나머지 값 변경은 좁히거나 동등이다).
    #    Claude를 좁혀 맞추지 않은 이유는 `wiki/` 소유가 이미 내려진 결정이기
    #    때문이고, 그래서 통제는 워커 축이 아니라 **게이트 축**에 있다.
    #    ⇒ 「전부 좁히는 쪽」으로 요약하지 마라 — 방향이 갈린다.
    # 🔴 `except` 2종은 **판정 근거 문서**다. 판정 대상이 판정 기준을 고치면 통제가
    #    성립하지 않는다. `docs/skills/`는 끝에 `/`가 있어 **접두어**다 — 허브를
    #    하위 문서로 쪼갤 때 완전일치만 두면 쪼개진 파일이 경계에서 빠진다.
    "tech-writer": {
        "allow": ("docs/", "README.md", "wiki/"),
        "except": (
            "docs/security.md",
            "docs/skills.md",
            "docs/skills/",
        ),
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# 런타임 고유 overlay. 워커 단위로 **항목 전체를 덮어쓴다**(부분 병합 아님).
#
# 🔴 여기 있는 것은 전부 **의도된 비대칭**이며, 사유는 아래 `ASYMMETRY_REASONS`에
#    반드시 있어야 한다 — `test_worker_boundaries.py`가 그 대응을 강제한다.
#    안 적으면 다음 사람이 갭으로 읽고 지운다(Issue #53이 요구한 규율).
# ─────────────────────────────────────────────────────────────────────────────

# Claude에만 있는 항목. **지금은 비어 있고, 그것은 선언된 공백이다** —
# 빠뜨린 것이 아니라 Claude 고유 경계가 실제로 없다는 뜻이다.
CLAUDE_ONLY: dict[str, dict[str, tuple[str, ...]]] = {}

# Codex에만 있는 항목.
CODEX_ONLY: dict[str, dict[str, tuple[str, ...]]] = {
    "analyst": {"allow": ("notebooks/", "docs/analyses/")},
}

ASYMMETRY_REASONS = {
    "analyst": (
        "Claude 쪽 정본은 `scripts/analyst_path_guard.py`이고 그 이름으로 "
        "`worker_path_guard.py`가 불릴 일이 없다(`KNOWN_ELSEWHERE` 참고). "
        "Codex에는 그 전용 가드의 **대응물이 없어** 여기서 지우면 "
        "그 워커의 경계가 통째로 사라진다. "
        "⇒ Claude를 따라 지우는 것이 자동 정답이 아니다."
    ),
}

# 정본이 **다른 가드**에 있는 워커 → 그 정본 경로. Claude 전용 축이다.
# 🔴 여기 걸려도 **통과가 아니라 `deny`** 다. 이 이름으로 호출이 실제로 왔다면
#    그것은 배선이 틀렸다는 뜻이고, 통과시키면 fail-open이 되살아난다.
KNOWN_ELSEWHERE = {
    "analyst": "scripts/analyst_path_guard.py",
}

# 저장소 밖에서 허용 목록을 벗어나면 `ask`가 아니라 `deny`로 처리할 워커.
# 🔴 기본값(`ask`)은 auto 모드에서 **막히지 않는다** — 분류기가 파일 도구의 `ask`를
#    경로 민감도와 무관하게 흡수한다. 원천 진료 데이터에서 그 흡수는 무통제 반출이다.
# ⚠️ Codex 가드는 이 상수를 **쓰지 않는다** — 그쪽은 `ask` 결정 자체가 없어
#    저장소 밖 미허용을 전부 `deny`한다(같거나 더 엄격). 속성 부재는 갭이 아니다.
OUTSIDE_STRICT = frozenset({"data-extractor"})

# 런타임별 저널 템플릿 파일명. 볼트 `agents/`를 두 런타임이 **공유**하므로
# 템플릿만 갈라 둔다(파일이 실제로 둘이다 — 표기 차이가 아니다).
JOURNAL_TEMPLATE = {
    "claude": "_TEMPLATE.md",
    "codex": "_TEMPLATE.codex.md",
}

# 통제 스크립트 — **어느 워커도 고치지 못한다.** 경계를 강제하는 코드가 경계에
# 없으면 통제가 자기 자신을 보호하지 못한다.
# 🔴 접미어(`*_guard.py`) 하나로만 걸려 있던 것을 **경로 규약으로 넓힌다.**
#    이름 규약에 얹힌 통제는 이름을 안 따르는 파일에서 조용히 샌다 —
#    `.codex/hooks/session_start.py`·`journal_pre_write.py`가 실제로 그랬다.
# 🔴 자기 자신(`scripts/worker_boundaries.py`)이 여기 있어야 한다. 표를 가드
#    밖으로 뽑아내면서 이 줄을 빠뜨리면 **뽑아낸 것이 곧 구멍**이 된다.
CONTROL_GLOBS = (
    "*_guard.py",
    # 🔴 런타임 hook(`*_guard.py`)만 막고 **커밋 게이트 검사기**(`*_check.py`)를 열어
    #    두면 통제가 반만 선다 — 워커가 검사기를 고치면 그 규약의 기계 강제가
    #    조용히 사라진다(고장이 아니라 무효화라 신호가 없다). Issue #33.
    "*_check.py",
    "scripts/worker_boundaries.py",
    ".codex/hooks/*.py",
)


def _merge(
    base: dict[str, dict[str, tuple[str, ...]]],
    overlay: dict[str, dict[str, tuple[str, ...]]],
) -> dict[str, dict[str, tuple[str, ...]]]:
    """공용 표에 런타임 overlay를 얹는다(워커 단위 치환)."""
    return {**base, **overlay}


def claude_boundaries() -> dict[str, dict[str, tuple[str, ...]]]:
    """Claude 런타임의 최종 경계표."""
    return _merge(COMMON, CLAUDE_ONLY)


def codex_boundaries() -> dict[str, dict[str, tuple[str, ...]]]:
    """Codex 런타임의 최종 경계표."""
    return _merge(COMMON, CODEX_ONLY)


def outside_allow(
    runtime: str, obsidian_root: Path, extract_dir: Path
) -> dict[str, tuple[str, ...]]:
    """저장소 **밖**에서 예외로 허용할 절대경로. 미지정 워커는 런타임 기본값.

    🔴 `agents/`를 접두어로 열지 않는다. 평탄화 이후 두 런타임이 **같은
    `agents/<날짜>/`를 공유**하므로 접두어로 열면 한쪽이 남의 기록까지 쓸 수 있어
    런타임 분리가 권한 분리가 되지 않는다. ⇒ 경계 축을 **경로에서 내용으로**
    옮겨 각 가드의 `is_*_journal_path()`가 진다.
    """
    agents = obsidian_root / "agents"
    return {
        "archivist": (
            str(agents / "_MOC.md"),
            str(agents / JOURNAL_TEMPLATE[runtime]),
        ),
        # 🔴 추출물은 **원천 진료 데이터**다(DUA·재식별 금지). 저장소 밖 단 한
        #    곳으로만 나간다. `archivist`와 형태는 같되 성격이 반대다 — 저쪽은
        #    기록을 **남기려고**, 이쪽은 저장소에 **남기지 않으려고** 밖에 쓴다.
        "data-extractor": (str(extract_dir),),
    }


def control_path(relative: str) -> str:
    """저장소 상대경로가 통제 스크립트면 그 경로를, 아니면 빈 문자열.

    🔴 **대소문자를 무시한다** — macOS 파일시스템이 무시하므로 구분해 비교하면
    `Scripts/Worker_Path_Guard.PY`가 같은 실파일에 착지하는데 통과한다.
    막는 쪽의 과잉은 fail-closed라 안전하다.
    (Codex 짝 가드가 이 축에서 대소문자를 **구분**하고 있었다 — Issue #53.)
    """
    lowered = relative.lower()
    hit = any(fnmatchcase(lowered, glob.lower()) for glob in CONTROL_GLOBS)
    return relative if hit else ""
