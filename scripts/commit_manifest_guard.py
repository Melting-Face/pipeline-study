#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""커밋 대상 파일 집합을 **계획서의 G1 매니페스트와 대조**한다(`PreToolUse` · `Bash`).

사용법(hook 배선):
    "$CLAUDE_PROJECT_DIR"/scripts/commit_manifest_guard.py

왜 이 가드인가:
    `security` 최종 컨펌의 G2는 *"변경 파일 집합을 `git status`·`git diff --stat`으로
    **직접 재구성**해 G1 매니페스트와 대조한다(제출 목록을 재료로 삼지 않는다)"* 인데,
    2계층에서 그 대조를 **부르는 주체가 supervisor 자신**이라 실질은 자기신고다.
    실측: 한 세션에서 커밋 9건이 나가는 동안 G1 대조가 **자동으로 0회** 돌았고,
    계획 밖 쓰기 경로가 **3회** 늘었는데 전부 자기신고였다. 그중 하나는
    "Δ입니다"라고 스스로 말했을 뿐 **아무도 검사하지 않았다.**

🔴 이 가드가 하는 일과 안 하는 일:
    한다   — 커밋 대상 파일 집합 ↔ 매니페스트의 **집합 비교**.
    안 한다 — *"이 파일 조합이 노출인가"* · *"문서가 내부 실태를 반출하나"* ·
             ISMS-P 매핑. 그건 판단이고 `security` 워커 몫이다.
    ⇒ **"security를 훅으로 옮겼다"고 읽지 마라.** G2의 **대조 축 하나**만 기계가
       가져갔다. 판단 축이 사라진 걸 모른 채 초록으로 읽는 것이 경계 대상이다.

🔴 **이 가드는 절대 `allow`를 내지 않는다.**
    `git commit`은 이미 `permissions.ask`에 걸려 있다. 여기서 `allow`를 내면
    **기존 게이트를 약화**시킨다. 낼 수 있는 결정은 `ask` 하나뿐이고, 커밋이
    아닌 명령만 **무출력 종료**한다(defer). ⇒ 이 가드는 **막는 장치가 아니라
    「멈춘 자리에서 무엇을 볼지」를 채우는 장치**다. 최악의 경우에도 현행보다
    나빠지지 않는다.

계측 단위(무엇을 세는가):
    - 「대상 파일」은 **커밋이 실제로 담을 파일**이다. 명령에 `--` pathspec이 있으면
      그것이 대상이고(우리 규약의 기본형), 없으면 **스테이징분**이다. 둘은 다르다 —
      공유 트리에서 `git add`는 남의 것까지 담으므로 pathspec이 있으면 그쪽이 사실이다.
    - 「위반」은 **매니페스트 밖 파일 1개당 1건**이다.

의존과 그 한계:
    - 매니페스트는 **계획서 안의 `<!-- manifest ... -->` 블록**이다.
      🔴 **블록이 없으면 이 가드는 아무것도 안 한다** — 즉 실효는 「계획서에 블록을 두는
      규율」에 묶여 있고 **기계가 그 규율을 강제하지 않는다.** 이걸 적지 않으면
      "커밋 드리프트를 기계화했다"가 거짓이 된다.
    - 계획서 위치는 `.claude/.claims/plans/<ref>.json`에서 읽는다
      (`scripts/plan_mirror_guard.py`가 쓰는 레지스트리를 **읽기만** 한다).
    - `session_id`가 payload에 없으면 `CLAUDE_SESSION_ID` 환경변수로 폴백한다
      (`session_sync_guard.py`와 같은 처리 — 그쪽도 확신이 없어 폴백을 뒀다).

검증(프로덕션 경로 그대로 · stdin 페이로드 — 각 프로브 후 원상 복구):
    P-0 대조군 `ls -la` → **무출력**(커밋이 아니면 아무 결정도 안 낸다)
    P-1 대조군 `git commit -- <매니페스트 안>` → `ask` + **✅ CLEAN**
        🔴 이 대조군을 두 번 세웠다. 처음엔 매니페스트 밖 파일이 **작업 트리에 없어서**
        통과했다 — 빈 집합끼리 비교한 것이라 아무것도 증명하지 못했다. 밖 파일을 실제로
        훼손해 둔 상태에서 다시 돌려야 「pathspec이 갈랐다」가 증명된다.
    P-2 `git commit -- <매니페스트 밖>` → `ask` + **🔴 DRIFT** (파일명 나열)
    P-3 pathspec 없이 스테이징분이 매니페스트 밖 → `ask` + **🔴 DRIFT**
    P-4 `session_id` 없음 / 계획 레코드 없음 → `ask` + **⚠️ 미확인**(사유가 서로 다름)

⚠️ **미확인 — hook 배선의 실발동은 확인하지 못했다.**
    `.claude/settings.json`의 `hooks` 배선은 **정의 로드 시점 스냅샷**이라 바꾼 세션에
    반영되지 않고, 이 브랜치는 전용 worktree라 실행 세션의 `$CLAUDE_PROJECT_DIR`도
    아니다.
    ⇒ 위 프로브가 증명한 것은 **스크립트 로직**까지다. **「커밋할 때 실제로 뜬다」는
    새 세션에서 재대조해야 한다**(`enforcement.md` §반영 시점).
"""

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

CLAIMS_PLANS = Path(".claude") / ".claims" / "plans"

# 계획서 안의 매니페스트 블록. `plan_mirror_guard.py`의 `<!-- plan-mirror: off -->`와
# 같은 관용(HTML 주석)을 쓴다 — 계획서는 렌더링되므로 본문에 안 보여야 한다.
MANIFEST_RE = re.compile(r"<!--\s*manifest\s*\n(.*?)-->", re.DOTALL)

# `git commit`을 가려낸다. `git -C <경로> commit` 같은 형태도 잡히도록 느슨하게 본다.
COMMIT_RE = re.compile(r"\bgit\b[^\n;|&]*\bcommit\b")

# 🔴 세 상태를 **전부 말한다.** 침묵을 「통과」로 쓰면 「가드가 죽었다」와
#    구분되지 않는다 — 부정 결과는 관측 경로 생존을 함께 제시해야 유효하다(원칙 7).
#    그래서 `git commit`에서는 항상 출력하고, 상태 라벨을 셋으로 가른다.
DRIFT = (
    "🔴 G1 매니페스트 밖 경로가 커밋에 담긴다 ({n}건)\n"
    "{files}\n"
    "계획서: {plan}\n"
    "\n"
    "Δ(계획 델타) 판정이 필요하다 — ⓐ쓰기 경로 추가 ⓑ비가역 ⓒ외부 발신 중 하나면\n"
    "`security` 컨펌을 거치고, 아니면 계획서의 manifest 블록을 먼저 갱신하라.\n"
    "⚠️ 이 가드는 **집합 비교만** 한다. 노출·규제 판단은 여전히 `security` 몫이다."
)
CLEAN = (
    "✅ G1 매니페스트 대조 — 대상 {n}건 전부 매니페스트 안({p}패턴)\n"
    "계획서: {plan}\n"
    "⚠️ **집합 비교만 통과했다.** 노출·규제·거버넌스 판단은 안 했다(`security` 몫)."
)
UNCHECKED = (
    "⚠️ 미확인 — G1 매니페스트가 없어 **대조하지 않았다**\n"
    "{why}\n"
    "\n"
    "🔴 이것을 「문제 없음」으로 읽지 마라. 계획서에 아래 블록을 두면 대조가 돈다:\n"
    "    <!-- manifest\n"
    "    docs/**\n"
    "    scripts/foo.py\n"
    "    -->\n"
    "⚠️ 없는 상태로 진행해도 되지만, **그때 커밋 범위를 보증하는 것은 사람뿐**이다."
)


def emit_ask(reason: str) -> None:
    """`ask` 결정을 내보내고 종료한다.

    🔴 유효 값은 `allow`·`deny`·`ask`·`defer` 넷뿐이고, 하나라도 어긋나면
    `hookSpecificOutput` 전체가 검증 실패해 **결정이 폐기된 채 도구가 진행한다**
    (fail-open).
    """
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                },
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


def defer() -> None:
    """아무 결정도 내지 않고 종료한다 — 기존 `permissions.ask`가 그대로 진다.

    🔴 `allow`를 내지 않는 이유가 여기 있다. 판정할 근거가 없을 때 `allow`를 내면
    **기존 게이트를 지우는 것**이 된다. 무출력이 안전한 기본값이다.
    """
    sys.exit(0)


def targets_of(command: str, cwd: str) -> list[str]:
    """이 커밋이 담을 파일 목록을 만든다.

    🔴 `--` pathspec이 있으면 **그것이 대상**이다(우리 규약의 기본형). 없을 때만
    스테이징분을 본다 — 공유 트리에서 `git add`는 남의 것까지 담으므로 둘은 다르다.

    🔴 **비교 기준이 갈린다.** pathspec이 있으면 `git commit -- <경로>`는 그 경로의
    **작업 트리 내용**을 커밋하므로 `HEAD` 대비로 봐야 하고(`--cached`로 보면 스테이징
    안 한 변경을 통째로 놓친다), 없으면 커밋 대상이 인덱스이므로 `--cached`가 맞다.
    ⚠️ 처음에 둘 다 `--cached`로 짰다가 프로브에서 잡혔다 — 미스테이징 파일만 담은
    커밋이 **대상 0건으로 조용히 통과**했다. 대조군이 공허했던 것이 발견 경로였다.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    pathspec = argv[argv.index("--") + 1 :] if "--" in argv else []
    args = ["git", "diff", "--name-only"]
    args += ["HEAD", "--", *pathspec] if pathspec else ["--cached"]
    # S603: 셸을 안 태우고(리스트 인자) `git diff`는 읽기 전용이다. pathspec은
    # 모델이 낸 문자열이지만 `--` 뒤로만 들어가 옵션으로 해석되지 않는다.
    try:
        out = subprocess.run(  # noqa: S603
            args, cwd=cwd, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def manifest_of(root: Path, session_id: str) -> tuple[list[str], str] | str:
    """매니페스트 패턴과 계획서 경로를 읽는다.

    읽지 못하면 **사유 문자열**을 돌려준다 — `None` 하나로 뭉치면 「계획이 없다」와
    「블록이 없다」와 「파일이 깨졌다」가 같아 보이고, 그러면 사용자가 무엇을 고쳐야
    할지 모른다. 🔴 실패 사유를 세는 것이 곧 모집단을 밝히는 것이다.
    """
    ref = (session_id or "").replace("-", "")[:6]
    if not ref:
        return "payload에 `session_id`가 없다 — 세션을 특정하지 못했다."
    record = root / CLAIMS_PLANS / f"{ref}.json"
    if not record.is_file():
        return f"이 세션(`{ref}`)의 계획서 레코드가 없다 — 플랜 모드를 거치지 않았다."
    try:
        plan_path = Path(json.loads(record.read_text(encoding="utf-8"))["plan"])
        text = plan_path.read_text(encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        return f"계획서를 읽지 못했다({record}) — {type(exc).__name__}."
    block = MANIFEST_RE.search(text)
    if block is None:
        return f"계획서에 `<!-- manifest -->` 블록이 없다 — {plan_path}"
    patterns = [
        ln.strip()
        for ln in block.group(1).splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not patterns:
        return f"manifest 블록이 비어 있다 — {plan_path}"
    return patterns, str(plan_path)


def covered(path: str, patterns: list[str]) -> bool:
    """파일 하나가 매니페스트 패턴 중 하나에 걸리는가."""
    for raw in patterns:
        pat = raw.rstrip("/") + "/**" if raw.endswith("/") else raw
        if fnmatch.fnmatch(path, pat):
            return True
        # `docs/**` 가 `docs/a.md`도 담도록 — fnmatch의 `*`는 `/`를 넘는다
        if pat.endswith("/**") and (path == pat[:-3] or path.startswith(pat[:-2])):
            return True
    return False


def main() -> None:
    """커밋 명령이면 대상 집합을 매니페스트와 대조하고, 벗어나면 `ask`."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        defer()  # 🔴 여기서 deny하지 않는다 — 이 가드는 막는 장치가 아니다

    if payload.get("tool_name") != "Bash":
        defer()
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not COMMIT_RE.search(command):
        defer()

    cwd = payload.get("cwd") or os.getcwd()
    root = Path(cwd)
    session_id = payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID", "")

    found = manifest_of(root, session_id)
    if isinstance(found, str):
        # 🔴 대조 못 한 것을 **침묵으로 두지 않는다.** 「계획을 안 만들면 게이트가
        #    없다」가 되면 우회하는 가장 쉬운 길이 「계획을 안 만드는 것」이 된다.
        #    다만 막지는 않는다 — 라벨이 `미확인`이지 `위반`이 아니다.
        emit_ask(UNCHECKED.format(why=found))
    patterns, plan = found

    targets = targets_of(command, cwd)
    outside = [f for f in targets if not covered(f, patterns)]
    if outside:
        emit_ask(
            DRIFT.format(
                n=len(outside),
                files="\n".join(f"  · {f}" for f in sorted(outside)),
                plan=plan,
            )
        )
    emit_ask(CLEAN.format(n=len(targets), p=len(patterns), plan=plan))


if __name__ == "__main__":
    main()
