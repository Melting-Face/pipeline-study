#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""루트 워킹트리 쓰기 차단 가드 — worktree 의무화를 집행하는 PreToolUse hook.

왜 이 스크립트인가:
    `docs/conventions/git.md` §7의 worktree 규칙은 **조건부**였다 — "main 워킹트리에
    **다른 세션이 있으면** 자기 worktree를 만들고 옮긴다". 조건부라 지켜지지 않았고,
    같은 브랜치에 여러 미션이 섞였다.

    🔴 **이것은 가설이 아니라 재현이다** — 같은 증상(여러 세션이 전부 main 워킹트리)이
    두 번 관측됐다. `docs/conventions/git/worktree.md` §「왜 지켜지지 않는가 (실측)」.

    ⇒ **문서 규칙은 이미 있었고, 읽혔고, 지켜지지 않았다.** 같은 층에 문장을 하나 더
    얹는 것은 기각된 처방의 반복이므로, 이번에는 **기계 강제**를 만든다.

    목표는 봉쇄가 아니라 **마찰 부여**다. 루트에서 쓰려 하면 막고, 차단 메시지가
    이주 명령을 직접 쥐여준다.

판정축 (2026-09-19 실측으로 확정):
        git rev-parse --absolute-git-dir  ==  --git-common-dir   →  메인 워킹트리

    | | 메인 워킹트리 | 링크된 worktree |
    | --- | --- | --- |
    | `--absolute-git-dir` | `<repo>/.git` | `<repo>/.git/worktrees/<name>` |
    | `--git-common-dir`   | `<repo>/.git` | `<repo>/.git` |
    | 같은가 | **YES** | NO |

    🔴 `.git`이 **디렉터리냐 파일이냐**로 재는 방법은 쓰지 않는다 — submodule과
    `git worktree repair` 중간 상태에서 갈린다. 위 두 값의 비교가 git 자신의 정의다.

    🔴 **`CLAUDE_PROJECT_DIR`에 의존하지 않는다.** 이 변수로 루트를 잡는 가드도
    있었다. 2026-09-19 worktree 이주 직후
    이 변수가 **미설정**인 것을 관측했는데, ⚠️ 그것은 **`Bash` 도구 환경**의 값이고
    **hook 실행 환경은 미확인**이다(hook 배선이 `"$CLAUDE_PROJECT_DIR"/scripts/…`로
    동작하므로 그쪽에는 설정된다고 보는 편이 옳다). **값은 맞았고 단위가 달랐다** —
    두 환경을 한 이름으로 묶어 읽으면 근거가 무너진다.

    ⇒ 그래서 이 가드는 **어느 쪽이든 안전한 설계**를 택한다: 환경변수를 읽지 않고
    **대상 경로 자체에서** git에게 직접 묻는다. 루트/worktree가 자동으로 갈리고,
    변수가 있든 없든 판정이 같다. (배선 경로에 `$CLAUDE_PROJECT_DIR`를 쓰는 것은
    기존 관례를 따르는 것이며, 그것은 **스크립트를 찾는 축**이지 판정축이 아니다.)

예외 (고정 화이트리스트 — 모델이 푸는 스위치는 두지 않는다):
    `.claude/settings.local.json` 은 worktree에서 루트로 향하는 **심볼릭 링크**다
    (`scripts/worktree-new.sh`의 `LINK_ASSETS`). `resolve()` 하면 **루트 경로로
    보이므로** 화이트리스트가 없으면 worktree에서조차 막힌다.

두 축을 갖는다:
    `file-pre` — `Edit`·`Write`·`NotebookEdit`. 경로 키가 도구마다 갈리므로 3종을
        모두 읽는다.
    `bash-pre` — `Bash`. **루트 커밋만** 판정한다. Bash 경유 쓰기(`sed -i`·`>`)는
        보지 않는다 — 문자열 휴리스틱이 리다이렉트 조회까지 막는 오탐과 fix 연쇄를
        낳아 철거했다(선언된 공백, 재정비 스펙 §설계 1).

사용: `PreToolUse` hook에서 호출한다.
    scripts/worktree_guard.py file-pre   # matcher: Edit|Write|NotebookEdit
    scripts/worktree_guard.py bash-pre   # matcher: Bash
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# 커밋 축. `-C <경로>`가 있으면 그 경로를 커밋 대상 트리로 본다.
COMMIT_RE = re.compile(r"\bgit(?:\s+-C\s+(\S+))?\s+commit\b")

# 명령을 쪼개는 구분자. 커밋 판정과 `cd` 추적을 **세그먼트 단위**로 내린다.
SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|[;\n|&]")

# 디렉터리 이동. 🔴 **세그먼트마다 따라간다** — 한 세그먼트 안의 `cd ../x`만 보면
# `cd .. ; cd <repo> ; git commit` 같은 **다단 체인**을 놓친다.
# 🔴 선행 토큰을 견딘다 — `^cd\s+`로만 두면 `(cd …)`·`pushd …`·`cd -- …`가
#    전부 빠져나갔다(security G2 실측 6종).
#    **전부 리터럴 경로라 「변수·인코딩」 선언 밖**이었다.
# ⚠️ 중첩 셸(`bash -c "cd … && …"`)은 **못 본다** — 인용 안을 파싱하지 않는다.
#    넓히는 대신 문서에 계열로 선언한다(봉쇄가 목표가 아니다).
CD_RE = re.compile(r"^[(\s]*(?:cd|pushd)\s+(?:--\s+)?([^\s;|&)]+)")

# 루트에서 허용되는 예외 경로. 프로젝트 루트 기준 상대경로로 대조한다.
#
# 🔴 **`worktree-new.sh`의 `LINK_ASSETS`는 2종인데 여기는 1종** — `.env`를 일부러 뺐다.
#    둘 다 루트로 향하는 링크인데 `.env`만 목록에 없어 **`deny`된다**.
#    비밀정보이고 편집할 대상이 아니기 때문이다. 권한 오버라이드는
#    링크를 **통해 써야** 기능이 성립한다.
WHITELIST_RE = (re.compile(r"^\.claude/settings\.local\.json$"),)

# git 호출 상한. hook은 `timeout: 10`으로 걸리므로 그보다 짧게 잡는다 —
# 여기서 늘어지면 hook 전체가 죽고 **결정이 사라진 채 도구가 진행한다**(fail-open).
GIT_TIMEOUT = 5

DENY_REASON = (
    "[worktree 의무] 저장소 **루트 워킹트리**에는 쓸 수 없다: {target}\n"
    "\n"
    "루트는 읽기·조회 전용이다. 모든 편집·커밋은 전용 worktree에서 한다\n"
    "(git.md §7 — 조건부였던 규칙이 의무가 됐다).\n"
    "\n"
    "이주하라:\n"
    "  ./scripts/worktree-new.sh <type>/<kebab-요약>\n"
    "  # 그 뒤 EnterWorktree 로 그 경로에 들어간다\n"
    "\n"
    "맨손 `git worktree add`는 비커밋 자산(.env·settings.local.json)이 안 따라온다."
)

UNKNOWN_REASON = (
    "[worktree 의무] 트리 위치를 판정하지 못했다: {target}\n"
    "\n"
    "이 가드의 통제가 **소멸한 상태**이므로 통과시키지 않는다(fail-closed).\n"
    "사유: {detail}\n"
    "\n"
    "통제 없이 진행할 의도면 사용자가 직접 승인해야 한다."
)


def emit_deny(reason: str) -> None:
    """`deny` 결정을 내보내고 종료한다.

    🔴 `permissionDecision`의 유효 값은 `allow`·`deny`·`ask`·`defer` **넷뿐**이다.
    목록 밖 값을 쓰면 `hookSpecificOutput` 전체가 검증 실패해 **결정이 폐기된 채
    도구가 그냥 진행한다**(fail-open). 증상은 배너 한 줄뿐이라 알아채기 어렵다.

    🔴 `ask`를 쓰지 않는 이유: auto 모드 분류기가 **파일 도구의 `ask`를 경로
    민감도와 무관하게 흡수**한다.
    경로 경계는 `deny`여야 실제로 막힌다.
    """
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                },
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


def git_query(cwd: Path, *args: str) -> str | None:
    """`cwd`에서 git을 호출해 첫 줄을 돌려준다. 실패하면 `None`.

    반환값 `None`은 **판정 불가**이지 "저장소 밖"이 아니다 — 둘을 한 분기로 묶으면
    통제가 죽은 상태가 정상 통과와 구분되지 않는다(원칙 7).
    """
    try:
        done = subprocess.run(  # noqa: S603 - 인자는 이 모듈의 리터럴뿐이다
            ["git", "-C", str(cwd), *args],  # noqa: S607 - PATH의 git을 쓴다
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def nearest_existing_dir(target: Path) -> Path | None:
    """`target`에서 위로 올라가며 **존재하는** 첫 디렉터리를 찾는다.

    `Write`로 새 파일을 만들 때는 대상도 그 부모도 아직 없을 수 있다. 그때
    git에게 물을 자리가 없으면 판정이 통째로 실패하므로 조상까지 거슬러 본다.
    """
    for candidate in [target, *target.parents]:
        if candidate.is_dir():
            return candidate
    return None


def common_dir_of(start: Path) -> Path | None:
    """`start`가 속한 저장소의 common git dir(절대경로). 아니면 `None`."""
    raw = git_query(start, "rev-parse", "--git-common-dir")
    if raw is None:
        return None
    common = Path(raw)
    if not common.is_absolute():
        common = start / common
    try:
        return common.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


_OWN_COMMONS: set[Path] | None = None


def own_repo_common_dirs() -> set[Path]:
    """**이 저장소**로 볼 common git dir들. 판정 대상을 한정한다.

    🔴 **프로세스 수명 동안 상수라 캐시한다.** 캐시 없이 `classify()`마다 부르면
    git 호출이 3회 → 5회(**+67%**)로 늘고, 그것이 hook `timeout: 10`을 넘겨
    **무출력+비-0 = 「결정 없음」 = 통과**(fail-open)를 만든다.
    실측: 세그먼트 10개짜리 명령이 캐시 전 **12.95s**로 타임아웃했다.
    ⇒ **통제를 넓히는 수정이 다른 축에서 통제를 없앴다.** 비용 축을 같이 안 봤다.

    🔴 축이 **둘**이다.
      ⓐ **가드 파일 자신의 위치** — 가드는 언제나 자기 저장소 안에 있고, 이 축은
        누구도 바꿀 수 없다(환경변수 비의존 원칙과 같은 근거).
      ⓑ **`CLAUDE_PROJECT_DIR`** — hook이 도는 저장소. 테스트가 임시 저장소를
        세우는 경로이기도 하다.

    ⚠️ **둘 중 하나라도 맞으면 판정 대상**으로 본다. 교집합이 아니라 합집합인 이유는
    방향 때문이다 — 좁게 잡으면 빠져나갈 구멍이 생기고, 넓게 잡으면 **더 많이 막는
    쪽**(fail-safe)이 된다. ⓑ를 조작해 빠져나가려 해도 ⓐ가 남는다.

    ⚠️ 비어 있으면 호출부가 `unknown`으로 처리한다 — 판정 기준을 모르는 상태를
    통과로 읽지 않는다.
    """
    global _OWN_COMMONS
    if _OWN_COMMONS is not None:
        return _OWN_COMMONS

    found = set()
    starts = (
        Path(__file__).resolve().parent,
        Path(os.environ.get("CLAUDE_PROJECT_DIR", "")),
    )
    for start in starts:
        if not start or not start.is_dir():
            continue
        common = common_dir_of(start)
        if common is not None:
            found.add(common)
    _OWN_COMMONS = found
    return found


def classify(raw_path: str) -> tuple[str, str]:
    """경로가 어느 트리에 속하는지 판정한다.

    Returns:
        `(판정, 상세)` — 판정은 `main`(루트 워킹트리) · `worktree` ·
        `outside`(저장소 밖) · `unknown`(판정 불가) 중 하나.

    🔴 `resolve()`를 쓴다. 심볼릭 링크와 `../`를 따라가므로
    `<worktree>/../dagster-study/CLAUDE.md` 형태의 우회가 막힌다. 그 대가로
    링크 자산(`settings.local.json`)이 루트 경로로 보이는데,
    그쪽은 화이트리스트가 받는다.
    """
    # 🔴 `ValueError`를 함께 잡는다 — 널 문자가 섞인 경로에서 `resolve()`가
    #    이것을 던지는데, 안 잡으면 가드가 **크래시**한다. 크래시는 무출력 +
    #    비-0 종료라 하네스에게는 「결정 없음」이고 그것은 **통과와 같다**
    #    (fail-open). 테스트가 실제로 이 자리를 잡아냈다.
    try:
        target = Path(raw_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return "unknown", f"경로를 정규화하지 못했다 ({exc})"

    probe_dir = nearest_existing_dir(target)
    if probe_dir is None:
        return "unknown", "존재하는 상위 디렉터리를 찾지 못했다"

    toplevel = git_query(probe_dir, "rev-parse", "--show-toplevel")
    if toplevel is None:
        # git이 비-0으로 끝났다 = 저장소 밖이다(판정 성공, 대상 아님).
        # 볼트·계획 파일이 여기로 온다.
        if git_query(probe_dir, "rev-parse", "--is-inside-work-tree") is None:
            return "outside", "git 저장소 밖"
        return "unknown", "toplevel을 얻지 못했다"

    git_dir = git_query(probe_dir, "rev-parse", "--absolute-git-dir")
    common_raw = git_query(probe_dir, "rev-parse", "--git-common-dir")
    if git_dir is None or common_raw is None:
        return "unknown", "git-dir / git-common-dir를 얻지 못했다"

    # `--git-common-dir`는 상대경로로 나올 수 있다(메인 워킹트리에서 `.git`).
    # 🔴 정규화 없이 비교하면 **메인인데 다르다고 읽혀 전부 통과**한다.
    common = Path(common_raw)
    if not common.is_absolute():
        common = probe_dir / common
    try:
        common = common.resolve()
        same = Path(git_dir).resolve() == common
    except (OSError, RuntimeError, ValueError) as exc:
        return "unknown", f"git-dir 비교에 실패했다 ({exc})"

    # 🔴 **「이 저장소인가」를 묻는다**(보안 점검 실측으로 추가).
    #    이 검사가 없으면 **아무 git 저장소의 메인 워킹트리**가 전부 `deny`가 된다.
    #    실제 피해: `$OBSIDIAN_VAULT`(볼트)가 git 저장소라 **저널 기록이 통째로
    #    막혔다** — 저널은 이 세션 체계의 감사기록 정본이다. 게다가 볼트 경로에
    #    "`worktree-new.sh`로 이주하라"는 **성립하지 않는 처방**을 띄웠다.
    #    교훈은 하나다 — **강등된 게이트보다
    #    틀린 방향으로 유도하는 게이트가 더 위험하다.**
    # ⚠️ 기준은 **가드 파일 자신의 위치**다. 환경변수에 의존하지 않는다는 이 가드의
    #    설계 원칙과 같은 근거이며, 가드는 언제나 자기 저장소 안에 있다.
    own_commons = own_repo_common_dirs()
    if not own_commons:
        return "unknown", "이 가드가 속한 저장소를 판정하지 못했다"
    if common not in own_commons:
        return "outside", f"다른 저장소({toplevel})"

    if not same:
        return "worktree", toplevel

    # 메인 워킹트리다. 화이트리스트를 확인한다.
    try:
        relative = target.relative_to(Path(toplevel).resolve())
    except ValueError:
        # toplevel 밖인데 메인으로 잡혔다 — 판정이 어긋났다.
        return "unknown", "대상이 toplevel 밖이다"

    rel = relative.as_posix()
    if any(pattern.match(rel) for pattern in WHITELIST_RE):
        return "worktree", f"화이트리스트: {rel}"
    return "main", rel


def guard_target(raw_path: str) -> None:
    """경로 하나를 판정해 `main`이면 차단한다."""
    verdict, detail = classify(raw_path)
    if verdict == "main":
        emit_deny(DENY_REASON.format(target=detail))
    if verdict == "unknown":
        emit_deny(UNKNOWN_REASON.format(target=raw_path, detail=detail))


def run_file_guard(payload: dict) -> None:
    """파일 도구가 루트 워킹트리를 쓰려 하면 차단한다."""
    tool_input = payload.get("tool_input") or {}
    # 🔴 matcher가 3개 도구에 걸치는데 경로 키가 갈린다 — `Edit`·`Write`는
    #    `file_path`, **`NotebookEdit`은 `notebook_path`**다. 하나만 읽으면
    #    그 도구에만 조용히 투명해진다(matcher 함정).
    raw_path = (
        tool_input.get("file_path")
        or tool_input.get("notebook_path")
        or tool_input.get("path")
        or ""
    )
    if not raw_path:
        # 🔴 **의도된 fail-open**이다(사유를 남긴다 — 사유 없는 fail-open은 이 가드가
        #    스스로 세운 기준을 어긴다). 경로 키 3종을 모두 덮었으므로 여기 오는 것은
        #    페이로드 형식이 어긋난 경우이고, 그때 막으면 파일 도구가 통째로 마비된다.
        #    ⚠️ 새 파일 도구가 **네 번째 키 이름**을 쓰기 시작하면 이 자리가 조용한
        #    구멍이 된다 — 도구가 늘면 `PATH_KEYS` 축을 먼저 다시 센다.
        sys.exit(0)
    guard_target(raw_path)
    sys.exit(0)


def run_bash_guard(payload: dict) -> None:
    """`Bash` 경유 **커밋**이 루트 워킹트리를 향하면 차단한다.

    Bash 쓰기는 판정하지 않는다(선언된 공백). 커밋 판정의 한계: `cd` 추적은
    **리터럴 경로**만 따라간다 — 변수·명령치환(`cd $(…)`)·중첩 셸은 못 본다.
    """
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not command:
        sys.exit(0)

    # 세그먼트를 순회하며 `cd`로 바뀐 자리(`here`)에서 커밋을 판정한다.
    # 주석(`#` 이후)은 실행되지 않으므로 판정 재료에서 뺀다.
    here = payload.get("cwd") or "."
    for raw_segment in SEGMENT_SPLIT_RE.split(command):
        segment = raw_segment.split("#", 1)[0].strip()
        if not segment:
            continue

        if moved := CD_RE.match(segment):
            here = str(Path(os.path.normpath(Path(here) / moved.group(1).strip("'\""))))
            continue

        commit = COMMIT_RE.search(segment)
        if commit is None:
            continue  # 커밋이 아닌 세그먼트 — 통과

        # `git -C <경로> commit`이면 그 경로가 커밋 대상 트리다(cwd와 무관).
        target = here
        if commit.group(1):
            target = str(Path(here) / Path(commit.group(1).strip("'\"")).expanduser())
        verdict, detail = classify(target)
        # 🔴 판정할 수 없으면 막는다(fail-closed) — 크래시·무출력은 통과와 같다.
        if verdict == "unknown":
            emit_deny(UNKNOWN_REASON.format(target=f"cwd={target}", detail=detail))
        if verdict == "main":
            emit_deny(DENY_REASON.format(target=f"{detail or '.'} / 커밋"))
    sys.exit(0)


def main() -> None:
    """서브커맨드 분기.

    🔴 기본값을 두지 않는다 — 인자가 빠진 배선은 **조용히 엉뚱한 축으로** 도는 것보다
    판정 불가로 막히는 편이 낫다(이 가드는 하위 호환 부채가 없다).
    """
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    # 🔴 페이로드를 못 읽으면 통과시킨다 — **의도된 fail-open**이다.
    #    여기서 막으면 하네스 계약이 어긋난 순간 모든 도구가 마비된다.
    #    (`test_guard_fail_direction.py`: "의도로 남긴 축은 코드 주석에 사유가 적혀
    #    있다" — 이 주석이 그 사유다. 나머지 축은 전부 fail-closed다.)
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # 🔴 예상 못 한 예외는 **크래시 = fail-open**이다. 무출력 + 비-0 종료를
    #    하네스는 「결정 없음」으로 읽고, 그것은 통과와 관측상 같다.
    #    `emit_deny`는 `SystemExit`을 던지는데 `Exception`의 하위가 아니라
    #    여기 걸리지 않는다(정상 경로가 안전망에 삼켜지지 않는다).
    try:
        if mode == "file-pre":
            run_file_guard(payload)
        elif mode == "bash-pre":
            run_bash_guard(payload)
        else:
            emit_deny(
                UNKNOWN_REASON.format(
                    target="(배선)",
                    detail=f"알 수 없는 모드: {mode or '(없음)'}",
                )
            )
    except Exception as exc:
        emit_deny(
            UNKNOWN_REASON.format(
                target="(가드 내부 오류)",
                detail=f"{type(exc).__name__}: {exc}",
            )
        )


if __name__ == "__main__":
    main()
