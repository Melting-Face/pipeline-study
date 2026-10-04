#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""워커 경계 접두어가 **실재하는 추적 경로를 덮는지** 검사한다.

사용법:
    python3 scripts/boundary_path_check.py     # repo 루트 또는 워크트리 루트에서

왜 이 스크립트인가:
    `scripts/worker_boundaries.py`의 주석이 **이미 일어난 사고**를 기록하고 있다 —
    `devops-engineer.deny`가 `dagster_project/`·`dbt/`로 적혀 있던 시절 **둘 다 추적
    파일 0건**이라 아무것도 막지 못했다. 실제 코드는 다른 경로에 있었다.

    즉 이 저장소는 **「배선됨」과 「겨냥이 맞음」이 다른 층**이라는 것을 한 번 밟았고,
    그 사고는 **문자열 오타 하나로 언제든 재발**한다. 경계 표는 사람이 손으로 적는
    문자열이고, 틀렸을 때 나는 증상이 **에러가 아니라 침묵**이기 때문이다.
    ⇒ 사람의 주의가 아니라 게이트로 막는다.

    지금이 특히 위험한 시점이다 — 파이썬 루트를 `dagster/dockerfile.d/src/`에서
    오케스트레이터 중립 경로 `pipeline/src/`로 옮기는 중이라 **경계 문자열이 실제로
    움직인다**. 경로가 움직일 때가 그 두 층이 갈라지는 순간이다.

🔴 판정 술어를 **재구현하지 않는다** — `worker_boundaries`의 `matches_deny`·
    `matches_except`·`matches_allow`를 **그대로 불러 쓴다.**
    닮은 `startswith`를 여기 새로 쓰면 **이 게이트는 통과하는데 실제 가드는 빗나가는**
    상태가 만들어진다(두 술어가 조용히 갈린다). 축마다 방향이 반대라는 것도
    그쪽 주석이 정본이다 — 막는 축은 대소문자 무시, 여는 축은 구분.

🔴 fail-closed:
    추적 목록을 못 얻으면 **통과가 아니라 실패**다. "검사 못 함"이 "위반 없음"으로
    둔갑하는 것이 이 저장소가 반복해 밟은 형태다
    (`permission_glob_check.py`와 같은 방향).

보증하지 않는 것 (정직하게 — 이 게이트가 덮지 **못하는** 축):
    🔴 **접두어를 「지우는」 것은 잡지 못한다 — 오히려 초록이 된다.** 위반 프로브로
      실측했다(2026-09-30): `devops-engineer.deny`에서 `"pipeline/src/"`를 빼자
      0건 항목이 함께 사라져 판정이 **위반 0건**으로 바뀌었다(접두어 22→21).
      ⇒ 이 게이트는 **「선언은 있는데 겨냥이 빗나감」** 축만 갖는다.
      **「선언이 사라짐」** 축은 `scripts/tests/test_worker_boundaries.py`의
      회귀 셀이 진다(같은 프로브에서
      그쪽은 `AssertionError`로 잡았다). **둘 중 하나만으로는 부족하고, 둘은 서로를
      대체하지 않는다** — 한쪽을 지우면서 "다른 쪽이 있다"고 읽지 마라.
      ⚠️ 그래서 출력에 **접두어 수를 함께 찍는다**. 판정이 초록이어도 분모가 줄면
      읽는 사람이 삭제를 **볼 수 있다**(수치가 그 문장의 대상을 세고 있어야 한다).
    · **접두어가 옳은 대상을 겨냥하는지는 보지 않는다.** 「0건이 아니다」까지다.
      `terraform/`을 `k8s/`로 잘못 적어도 둘 다 추적 파일이 있으니 통과한다.
      겨냥 대상의 타당성은 사람이 진다 — 이 게이트가 막는 것은 **오타와 경로 이동**이다.
    · **커버리지 동등성은 보지 않는다.** 경계가 117파일을 덮다가 48파일로 줄어도
      둘 다 0건이 아니므로 통과한다. "경계가 사라졌다"가 아니라 **"절반만 남았다"**
      는 축은 여기서 안 걸린다(그건 사람이 레이아웃 결정에서 진다).
    · **`.claude/settings.json`의 `permissions` 경로 규칙은 모집단 밖**이다.
      축이 다르다(`permission_glob_check.py`가 명령 글롭 축을 갖는다).
    · **Codex 짝 가드(`.codex/**`)의 표는 이 스크립트가 읽지 않는다.**
      정합은 `scripts/skill_wiring_check.py`·`worker_boundaries.py`의 미러 축이 진다.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import worker_boundaries as wb

# 축별 판정 술어 — 🔴 정본을 불러 쓴다(위 §판정 술어를 재구현하지 않는다).
AXIS_MATCHERS = {
    "deny": wb.matches_deny,
    "except": wb.matches_except,
    "allow": wb.matches_allow,
}

# 검사 대상 경계 표. 세 벌 전부 본다 — 한 벌만 보면 런타임 고유 표가 빠진다.
BOUNDARY_TABLES = (
    ("COMMON", wb.COMMON),
    ("CLAUDE_ONLY", wb.CLAUDE_ONLY),
    ("CODEX_ONLY", wb.CODEX_ONLY),
)

# 추적 파일 0건이어도 **남아야 하는** 접두어와 그 사유.
#
# 🔴 사유 없는 예외는 이 검사기가 거부한다 — 주석으로만 두면 늘릴 때 조용히 빠진다
#    (`permission_glob_check.py`의 `EXEMPT_REASONS`와 같은 형태).
# 🔴 **예외가 불필요해지면 그것도 위반이다**(죽은 예외). 접두어가 추적 파일을 갖게
#    되면 이 목록에서 빼라고 게이트가 실패한다 — 예외는 **스스로 회수를 트리거한다**.
#    이관 중 `pipeline/src/`를 여기 넣지 않는 이유가 그것이다: 디렉터리가 생기는
#    커밋과 게이트가 도입되는 커밋을 **순서로** 가르면 예외를 잊을 자리가 없다.
# 🔴 **현재 비어 있다. `.env`를 넣으려다 뺐다 — 이 경위를 지우지 마라.**
#    처음에 `.env`를 "gitignore 대상이라 추적 0건"이라는 사유로 등재했다. 그 판단은
#    **닮은 술어로 센 결과**였다 — `f == prefix or f.startswith(prefix + "/")`로
#    세어 0건을 얻었다. 그런데 정본 `matches_deny`는 **완전일치 분기를 두지 않는다**
#    (`.env`가 금지면 `.env.local`·`.env.prod`도 금지여야 하므로). 실제로는
#    추적 파일 `.env.example` **1건**을 덮는다.
#    ⇒ 예외가 필요 없었고, 이 게이트가 **첫 실행에서 그 오차를 잡아냈다**.
#    교훈이 이 파일의 §판정 술어를 재구현하지 않는다와 정확히 같다: **값이 그럴듯해도
#    술어가 다르면 답이 다르다.** 0건을 세려면 정본 술어로 세라.
EXEMPT_REASONS: dict[str, str] = {}


def main() -> int:
    """경계 접두어마다 추적 파일 수를 세고, 0건인 것을 예외 목록과 대조한다."""
    # ── 입력 ────────────────────────────────────────────────────────────────
    # 🔴 fail-closed: 목록을 못 얻으면 통과시키지 않는다.
    try:
        # 🔴 `S603` noqa를 달지 않는다 — 여기서는 발동하지 않아 `RUF100`(죽은 noqa)이
        #    된다. `worktree_guard.py`는 둘 다 달지만 호출 형태가 달라 S603이 실제로
        #    뜬다. **선례를 복사하지 말고 지금 뜨는 규칙만 억제한다.**
        completed = subprocess.run(
            ["git", "ls-files"],  # noqa: S607 - PATH의 git을 쓴다
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"🔴 추적 파일 목록을 얻지 못했다 — {error}", file=sys.stderr)
        return 1

    tracked = [line for line in completed.stdout.splitlines() if line]
    if not tracked:
        # 🔴 0건은 "위반 없음"이 아니라 **관측 경로가 죽은 것**이다. 여기를 통과시키면
        #    모든 접두어가 0건이 되어 전부 위반으로 잡히거나(시끄러움) — 더 나쁘게 —
        #    git이 없는 환경에서 조용히 빈 목록이 돌아 게이트가 무의미해진다.
        print("🔴 추적 파일이 0건이다 — git 저장소 안에서 실행하라", file=sys.stderr)
        return 1

    # ── 판정 ────────────────────────────────────────────────────────────────
    violations: list[str] = []
    seen_prefixes: set[str] = set()
    covered: dict[str, int] = {}
    counted = 0

    for table_name, table in BOUNDARY_TABLES:
        for worker, spec in sorted(table.items()):
            for axis, matcher in AXIS_MATCHERS.items():
                for prefix in spec.get(axis, ()) or ():
                    counted += 1
                    seen_prefixes.add(prefix)
                    hits = sum(1 for path in tracked if matcher(path, (prefix,)))
                    covered[prefix] = max(covered.get(prefix, 0), hits)

                    if hits > 0 or prefix in EXEMPT_REASONS:
                        continue

                    violations.append(
                        f"{table_name}/{worker}.{axis}: `{prefix}` — 추적 파일 0건. "
                        "이 경계는 아무것도 겨냥하지 않는다(오타이거나 경로가 움직였다)"
                    )

    # 죽은 예외 — 사유가 비었거나, 표에서 사라졌거나, **이미 덮이게 된** 것.
    for prefix, reason in sorted(EXEMPT_REASONS.items()):
        if not reason.strip():
            violations.append(f"예외 `{prefix}`에 사유가 비어 있다")
        if prefix not in seen_prefixes:
            violations.append(
                f"예외 `{prefix}`에 대응하는 경계 항목이 없다 — 죽은 예외. "
                "남겨 두면 다음 사람이 「이 축은 원래 예외」로 읽는다"
            )
        elif covered.get(prefix, 0) > 0:
            violations.append(
                f"예외 `{prefix}`가 이제 추적 파일 {covered[prefix]}건을 덮는다 — "
                "예외를 회수하라(`EXEMPT_REASONS`에서 제거). 불필요한 예외가 남으면 "
                "나중에 정말 0건이 됐을 때 게이트가 침묵한다"
            )

    # ── 출력 ────────────────────────────────────────────────────────────────
    if violations:
        print(f"🔴 경계 겨냥 위반 {len(violations)}건 (접두어 {counted}개 중)")
        for line in violations:
            print(f"  · {line}")
        print()
        print("정본: scripts/worker_boundaries.py")
        print("      docs/conventions/agents/enforcement.md")
        print("  「배선됨」과 「겨냥이 맞음」은 다른 층이다 —")
        print("  0건은 에러가 아니라 침묵이다")
        return 1

    # 🔴 부정 결과(위반 0건)에 **관측 경로 생존을 함께 제시**한다(철학 원칙 7):
    #    접두어 수·추적 파일 수를 같은 출력에 찍어, 이 0건이 *검사했다*인지
    #    *아무것도 안 셌다*인지 읽는 사람이 가를 수 있게 한다.
    print(
        f"✅ 경계 겨냥 위반 0건 — 접두어 {counted}개 / 추적 파일 {len(tracked)}개 대조"
    )
    if EXEMPT_REASONS:
        names = ", ".join(f"`{key}`" for key in sorted(EXEMPT_REASONS))
        print(f"   예외 {len(EXEMPT_REASONS)}건(사유 있음): {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
