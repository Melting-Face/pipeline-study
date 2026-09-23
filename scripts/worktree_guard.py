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

    🔴 **이것은 가설이 아니라 재현이다.** `docs/conventions/git/worktree.md`
    §「왜 지켜지지 않는가 (실측)」에 *"이 날 4개 세션이 전부 main 워킹트리에서
    돌았다"* 가 박제돼 있는데, 2026-09-19 11:35 KST 실측이 **숫자까지 같았다**
    (`git worktree list` 1개 · 살아있는 세션 4개 · 전원 `main`).

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

    🔴 **`CLAUDE_PROJECT_DIR`에 의존하지 않는다.** 기존 가드들(`session_sync_guard`·
    `protected_paths_guard`)은 이 변수로 루트를 잡는다. 2026-09-19 worktree 이주 직후
    이 변수가 **미설정**인 것을 관측했는데, ⚠️ 그것은 **`Bash` 도구 환경**의 값이고
    **hook 실행 환경은 미확인**이다(hook 배선이 `"$CLAUDE_PROJECT_DIR"/scripts/…`로
    동작하므로 그쪽에는 설정된다고 보는 편이 옳다). **값은 맞았고 단위가 달랐다** —
    두 환경을 한 이름으로 묶어 읽으면 근거가 무너진다.

    ⇒ 그래서 이 가드는 **어느 쪽이든 안전한 설계**를 택한다: 환경변수를 읽지 않고
    **대상 경로 자체에서** git에게 직접 묻는다. 루트/worktree가 자동으로 갈리고,
    변수가 있든 없든 판정이 같다. (배선 경로에 `$CLAUDE_PROJECT_DIR`를 쓰는 것은
    기존 관례를 따르는 것이며, 그것은 **스크립트를 찾는 축**이지 판정축이 아니다.)

예외 (고정 화이트리스트 — 모델이 푸는 스위치는 두지 않는다):
    `.claude/.claims/**` 와 `.claude/settings.local.json` 은 worktree에서 루트로
    향하는 **심볼릭 링크**다(`scripts/worktree-new.sh`의 `LINK_ASSETS`).
    🔴 실측: worktree 안에서 `.claude/.claims`를 `resolve()` 하면
    `<메인 워킹트리>/.claude/.claims` — 즉 **루트 경로로 보인다**.
    화이트리스트가 없으면 worktree에서조차 막히므로 이 목록은 장식이 아니다.

두 축을 갖는다 — 한쪽만 막으면 반쪽이다:
    `file-pre` — `Edit`·`Write`·`NotebookEdit`. 경로 키가 도구마다 갈리므로 3종을
        모두 읽는다(`parallel.md` §matcher 함정).
    `bash-pre` — `Bash`. `sed -i`·`>`·heredoc은 파일 도구 matcher **밖**이라
        (`parallel.md` §「이 가드가 못 보는 것」) 별도로 막지 않으면 그대로 뚫린다.

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

# 쓰기 신호 목록은 **복제하지 않고 가져온다**.
# 🔴 의도적 결합이다 — 목록을 두 벌 두면 반드시 어긋나고, 어긋난 쪽이 조용히
#    통과시킨다(`protected_paths_guard`가 `load_protected_patterns`로 같은 원칙을
#    쓴다: "목록을 두 곳에 두면 반드시 어긋나므로 단일 출처를 유지한다").
#    가져오지 못하면 통제가 죽은 것이므로 fail-closed 처리한다(아래 `WRITE_SIGNALS`).
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from protected_paths_guard import WRITE_SIGNALS
except ImportError:  # pragma: no cover - 임포트 실패 자체를 아래에서 deny로 다룬다
    WRITE_SIGNALS = None

# 루트에서도 **반드시 통과해야 하는** 명령.
# 🔴 이 목록을 빠뜨리면 규칙을 지킬 방법이 사라진다 — worktree를 만드는 행위 자체가
#    루트에서 일어나기 때문이다. 규칙이 자기 탈출구를 막으면 사람은 규칙을 끈다.
ALLOWED_COMMAND_RE = re.compile(
    r"(?:"
    r"scripts/worktree-new\.sh"
    r"|git(?:\s+-C\s+\S+)?\s+worktree\s+(?:add|remove|prune|list|repair)"
    r")"
)

# 커밋 축 — 쓰기 신호와 별개로 막는다(선택된 금지 범위가 "쓰기 전면"이다).
# 🔴 `git commit`은 파일을 만들지 않아 `WRITE_SIGNALS`에 걸리지 않는다.
COMMIT_RE = re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+commit\b")

# 명령을 쪼개는 구분자. 허용 판정을 **세그먼트 단위**로 내리기 위한 것이다.
# 🔴 명령 전체를 여는 조기 탈출은 구멍이었다(security G2 실측):
#      `./scripts/worktree-new.sh f/b; echo x > <루트>/CLAUDE.md`   → 통과했다
#      `echo x > <루트>/CLAUDE.md  # see scripts/worktree-new.sh`   → 통과했다
#    후자는 **문서 관례상 자연스러운 주석 한 줄**이라 우연히도 발생한다.
#    치환도 인코딩도 아니므로 "선언된 공백"에 들어가지 않는 **진짜 구멍**이었다.
SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|[;\n|&]")

# 경로 후보를 뽑는 토큰. `/`나 `.`을 포함한 것만 쓴다(아래 `path_candidates` 참고).
TOKEN_RE = re.compile(r"[\w./@~+-]{2,}")

# 디렉터리 이동. 🔴 **세그먼트마다 따라간다** — 한 세그먼트 안의 `cd ../x`만 보면
# `cd .. ; cd <repo> ; echo x > CLAUDE.md` 같은 **다단 체인**을 놓친다.
# 🔴 선행 토큰을 견딘다 — `^cd\s+`로만 두면 `(cd …)`·`pushd …`·`cd -- …`가
#    전부 빠져나갔다(security G2 실측 6종).
#    **전부 리터럴 경로라 「변수·인코딩」 선언 밖**이었다.
# ⚠️ 중첩 셸(`bash -c "cd … && …"`)은 **못 본다** — 인용 안을 파싱하지 않는다.
#    넓히는 대신 문서에 계열로 선언한다(봉쇄가 목표가 아니다).
CD_RE = re.compile(r"^[(\s]*(?:cd|pushd)\s+(?:--\s+)?([^\s;|&)]+)")

# 루트에서 허용되는 예외 경로. 프로젝트 루트 기준 상대경로로 대조한다.
#
# 🔴 **`worktree-new.sh`의 `LINK_ASSETS`는 3종인데 여기는 2종** — `.env`를 일부러 뺐다.
#    빠뜨린 것이 아니라 선언된 차이다. 셋 다 worktree에서 루트로 향하는 심볼릭 링크라
#    `resolve()` 결과가 루트 경로가 되는데, `.env`만 목록에 없으므로 **`deny`된다**.
#    근거: `.env`는 비밀정보이고 커밋 금지이며, 워커가 편집할 대상이 아니다. 나머지 둘은
#    세션 레지스트리·권한 오버라이드라 링크를 **통해 써야** 기능이 성립한다.
#
# ⚠️ 부수 효과 하나를 명시해 둔다(선언 없이 남기면 다음 사람이 의도로 오독한다):
#    `scripts/worker_path_guard.py`가 같은 링크를 `resolve()`한 뒤 **「저장소 밖」으로
#    분류해 `ask`로 격하**시키던 결함이 있었고, 이 가드의 `deny`가 `.env` 한 종에
#    대해서는 결과적으로 그것을 덮었다. **그 결함은 Issue #108에서 해소됐다** —
#    이 주석을 「아직 열려 있다」로 읽지 마라(미해소 선언이 해소된 채로 남으면
#    다음 사람이 **없는 결함을 다시 고친다**).
#    다만 이 가드의 화이트리스트가 `.env`를 뺀 근거는 그와 **무관하게** 유효하다.
WHITELIST_RE = (
    re.compile(r"^\.claude/\.claims(?:/.*)?$"),
    re.compile(r"^\.claude/settings\.local\.json$"),
)

# git 호출 상한. hook은 `timeout: 10`으로 걸리므로 그보다 짧게 잡는다 —
# 여기서 늘어지면 hook 전체가 죽고 **결정이 사라진 채 도구가 진행한다**(fail-open).
GIT_TIMEOUT = 5

# 🔴 **한 명령 전체**의 경로 후보 예산. 타임아웃(=fail-open)을 막는다.
#    세그먼트당 상한으로 두면 세그먼트를 늘려 우회되고, 실측에서 그 형태가 12.95s로
#    hook `timeout: 10`을 넘겼다. 바닥나면 **남은 세그먼트를 안 본다**(미탐) —
#    그쪽이 타임아웃(명령 전체가 통과)보다 낫다.
CANDIDATE_BUDGET = 24

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
    "🔴 맨손 `git worktree add`를 쓰지 마라 — 비커밋 자산(.env·.claims·\n"
    "   settings.local.json)이 안 따라와 **피어 감지가 조용히 꺼진다**."
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
    민감도와 무관하게 흡수**한다(`parallel.md` §「`ask`는 하드 스톱이 아니다」).
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


def own_main_tops() -> set[Path]:
    """이 저장소의 **메인 워킹트리** 경로들(common git dir의 부모).

    🔴 `path_candidates()`가 이것을 쓴다 — 예전에는 **cwd에 물어서** 구했고,
    그래서 **cwd가 저장소 밖이면 절대경로 검사가 통째로 꺼졌다**
    (`cd /tmp ; echo x > <루트>/CLAUDE.md` 가 통과했다 — security G2 실측).
    `classify()`는 이미 자기 저장소를 아는데 `path_candidates()`만 cwd를 믿는
    **판정축 불일치**였다. 기준은 한 곳에서 온다.
    """
    return {common.parent for common in own_repo_common_dirs()}


def classify(raw_path: str) -> tuple[str, str]:
    """경로가 어느 트리에 속하는지 판정한다.

    Returns:
        `(판정, 상세)` — 판정은 `main`(루트 워킹트리) · `worktree` ·
        `outside`(저장소 밖) · `unknown`(판정 불가) 중 하나.

    🔴 `resolve()`를 쓴다. 심볼릭 링크와 `../`를 따라가므로
    `<worktree>/../dagster-study/CLAUDE.md` 형태의 우회가 막힌다. 그 대가로
    링크 자산(`.claims`)이 루트 경로로 보이는데, 그쪽은 화이트리스트가 받는다.
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

    # 🔴 **「이 저장소인가」를 묻는다**(security G2 실측으로 추가).
    #    이 검사가 없으면 **아무 git 저장소의 메인 워킹트리**가 전부 `deny`가 된다.
    #    실제 피해: `$OBSIDIAN_VAULT`(볼트)가 git 저장소라 **저널 기록이 통째로
    #    막혔다** — 저널은 이 세션 체계의 감사기록 정본이다. 게다가 볼트 경로에
    #    "`worktree-new.sh`로 이주하라"는 **성립하지 않는 처방**을 띄웠다.
    #    `worker_path_guard.py`가 적어둔 교훈 그대로다 — **강등된 게이트보다
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


def path_candidates(command: str, cwd: str, budget: int) -> list[str]:
    """명령에서 **메인 워킹트리를 가리킬 수 있는** 경로 토큰만 추린다.

    🔴 절대경로만 훑으면 뚫린다(security G2 실측):
        cwd=<worktree>, `cd ../<repo> && echo x > CLAUDE.md` → 통과했다.
    `../<repo>`가 `/`로 시작하지 않아 정규식에 안 걸렸다. 치환도 인코딩도 아닌
    **1차 시도로 닿는 경로**라 "선언된 공백"이 아니었다.

    ⚠️ 그렇다고 모든 토큰을 `classify()`에 태우면 토큰마다 git을 불러 hook
    `timeout: 10`을 넘긴다. 그래서 **문자열로 먼저 거르고** 남은 것만 git 판정에 보낸다
    — 메인 워킹트리 경로는 `--git-common-dir`의 부모로 git 호출 **1회**에 얻는다.
    """
    # 🔴 기준은 **자기 저장소**다. cwd에 물으면 cwd가 저장소 밖일 때 후보가 0건이 되고
    #    절대경로 검사가 통째로 꺼진다(`cd /tmp ;` 토큰 하나로 닫은 구멍이 되돌아왔다).
    tops = own_main_tops()
    if not tops:
        return []
    try:
        base = Path(cwd).resolve()
    except (OSError, RuntimeError, ValueError):
        base = Path.cwd()

    # 🔴 후보 수에 **상한**을 둔다. `classify()`가 후보마다 git을 부르므로 후보가
    #    수십 건인 명령에서 hook `timeout: 10`을 넘길 수 있고, 타임아웃은 무출력
    #    종료 = **fail-open**이다. 상한에 걸려 잘리는 쪽은 미탐이지만, 타임아웃은
    #    그 명령 전체가 통과하므로 더 나쁘다.
    picked: set[str] = set()
    for candidate in TOKEN_RE.findall(command):
        if len(picked) >= budget:
            break
        # 경로처럼 생긴 것만 본다. 순수 단어(`echo`·`pwned`)는 후보가 아니다.
        if "/" not in candidate and "." not in candidate:
            continue
        try:
            # 절대경로·`~`는 `base`를 무시한다(pathlib 동작).
            # 상대경로만 cwd 기준으로 펴진다.
            expanded = (base / Path(candidate).expanduser()).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if any(expanded == top or top in expanded.parents for top in tops):
            picked.add(str(expanded))
    return sorted(picked)


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
    #    그 도구에만 조용히 투명해진다(`parallel.md` §matcher 함정의 실물).
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
    """`Bash` 경유 쓰기·커밋이 루트 워킹트리를 향하면 차단한다.

    한계(정직하게): 문자열 휴리스틱이라 **완전하지 않다**. 변수 치환·인코딩으로
    우회된다(`python -c "open(...)"`의 변형 등). 목표는 봉쇄가 아니라 마찰이며,
    이 한계는 `docs/conventions/git/worktree.md`에 함께 적는다.

    ⚠️ **오탐도 선언한다**(빠뜨린 것과 구분하려고 적는다):
      · 쓰기 대상이 worktree라도 **명령 문자열에 루트 경로가 언급되면** 막는다
        (`echo "see <루트>/docs/x.md" >> notes.md`). 경로가 인자인지 인용문인지
        휴리스틱으로는 갈리지 않는다 — 막는 쪽의 과잉이라 fail-safe로 받아들인다.
      · `cd` 추적은 **리터럴 경로**만 따라간다. 변수·명령치환(`cd $(…)`)은 못 본다.
    """
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not command:
        sys.exit(0)

    if WRITE_SIGNALS is None:
        emit_deny(
            UNKNOWN_REASON.format(
                target="(Bash 명령)",
                detail="protected_paths_guard의 WRITE_SIGNALS를 가져오지 못했다",
            )
        )

    cwd = payload.get("cwd") or "."

    # 🔴 세그먼트를 **순회하며 그 자리의 cwd로** 판정한다. 세 가지가 함께 필요하다.
    #    ⓐ 허용은 **세그먼트 단위** — 전역 조기 탈출은 `worktree-new.sh …; echo x > …`로
    #      명령 전체를 열었다.
    #    ⓑ 주석(`#` 이후)은 실행되지 않으므로 판정 재료에서 뺀다 —
    #      `# see scripts/worktree-new.sh` 한 줄이 명령 전체를 열었다.
    #    ⓒ **`cd`를 따라간다** — `cd .. ; cd <repo> ; echo x > CLAUDE.md`는 토큰이
    #      `..`와 `<repo>`로 갈려 아무것도 안 잡혔다(security G2 실측).
    #      한 세그먼트의 `cd ../x`만 보던 앞 구현은 **다단 체인을 놓쳤다.**
    #    ⓓ 허용 세그먼트라도 **리다이렉션 대상은 계속 검사**한다 —
    #      `./scripts/worktree-new.sh f/b > <루트>/CLAUDE.md`가 통째로 면제됐다.
    here = cwd
    # 🔴 예산은 **명령 단위**다. 세그먼트당 상한은 세그먼트를 늘려 우회되고,
    #    실제로 세그먼트 10개짜리가 hook `timeout: 10`을 넘겨 fail-open이 됐다.
    remaining = CANDIDATE_BUDGET
    for raw_segment in SEGMENT_SPLIT_RE.split(command):
        if remaining <= 0:
            break
        segment = raw_segment.split("#", 1)[0].strip()
        if not segment:
            continue

        if moved := CD_RE.match(segment):
            here = str(Path(os.path.normpath(Path(here) / moved.group(1).strip("'\""))))
            continue

        # 허용 판정은 **리다이렉션 앞부분**에만 건다. 뒤쪽(`> 대상`)은 남겨 검사한다.
        head, redirect, tail = segment.partition(">")
        body = segment
        if ALLOWED_COMMAND_RE.search(head):
            body = (redirect + tail).strip()
            if not body:
                continue

        is_commit = bool(COMMIT_RE.search(body))
        has_write = any(s in body for s in WRITE_SIGNALS)
        if not (is_commit or has_write):
            continue  # 조회 세그먼트 — 통과

        # 🔴 **커밋은 cwd로 판정한다** — 커밋에는 쓰기 대상 경로가 없다.
        #    ⚠️ 다만 `git -C <루트> commit` 처럼 **경로가 인자로 오는 형태**가 있어
        #    cwd만 보면 `cd /tmp ; git -C <루트> commit` 이 빠져나갔다(내 수정의 빈틈).
        #    그래서 아래 경로 후보 검사를 **커밋에도 적용**한다.
        verdict, detail = classify(here)
        # 🔴 cwd를 **판정할 수 없으면** 막는다(fail-closed). 쓰기 판정을 대상 축으로
        #    옮기면서 이 경로를 잃었고, 널 문자 cwd가 통과했다 — 기존 셀이 잡았다.
        if verdict == "unknown":
            emit_deny(UNKNOWN_REASON.format(target=f"cwd={here}", detail=detail))
        if is_commit and verdict == "main":
            emit_deny(DENY_REASON.format(target=f"cwd={detail or '.'} / 커밋"))

        # 🔴 **쓰기는 「대상」으로 판정한다 — cwd로 판정하지 않는다.**
        #    예전에는 cwd가 루트면 **대상이 어디든** 막았고, 그래서
        #    `$OBSIDIAN_VAULT` 저널 쓰기와 **순수 조회까지** 차단됐다
        #    (`2>/dev/null`·`2>&1`의 `>`가 쓰기 신호로 잡힌다 — security G2 실측).
        #    H1에서 고친 「볼트가 막힌다」가 **축만 바꿔 재발한 자리**다.
        #    상대경로 대상은 `path_candidates()`가 `here` 기준으로 펴므로
        #    cwd가 루트일 때의 `echo x > note.txt`는 그대로 잡힌다.
        # ⚠️ 대가는 미탐이다 — 대상을 토큰으로 못 뽑는 쓰기(`python -c "open(…)"`)는
        #    빠져나간다. **과차단으로 감사기록을 끊는 것보다 낫다**고 판단했다.
        for candidate in path_candidates(body, here, remaining):
            remaining -= 1
            path_verdict, path_detail = classify(candidate)
            if path_verdict == "main":
                emit_deny(DENY_REASON.format(target=path_detail))
    sys.exit(0)


def main() -> None:
    """서브커맨드 분기.

    🔴 기본값을 두지 않는다 — 인자가 빠진 배선은 **조용히 엉뚱한 축으로** 도는 것보다
    판정 불가로 막히는 편이 낫다(`protected_paths_guard`는 하위 호환 때문에 기본값을
    두지만, 이 가드는 신설이라 그 부채가 없다).
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
