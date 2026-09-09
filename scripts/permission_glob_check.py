#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""`.claude/settings.json`의 `Bash(...)` 규칙이 글롭 작성 규약을 지키는지 검사한다.

Issue #33 — 두 작성 스타일이 섞여 있고 한쪽이 **인자 삽입**에 취약하다.

사용법:
    python3 scripts/permission_glob_check.py     # repo 루트에서

규약 (정본 `docs/conventions/agents/permissions.md` §명령 글롭):

    | 결정      | 스타일                  | 빗나갔을 때                        |
    | deny·ask  | 전면 와일드카드 `*a*b*` | 과차단 — 최악이 프롬프트 1회       |
    | allow     | 접두 앵커 `cmd sub*`    | **fail-open** — 인자 삽입이 샌다   |

    🔴 **스타일을 하나로 통일하지 않는다.** 넓은 매칭은 **방향**이 있어서,
    차단 축에서 안전한 것이 허용 축에서는 그대로 구멍이다. 어느 쪽을 쓰는지가
    **결정 종류로 정해지므로** 기계 검사가 성립한다.

왜 이 축인가 (실측 — Claude Code 2.1.236):
    매처는 **원문 문자열을 보지 않는다.** 공백은 정규화되고, `&&`·`;`는 조각별로
    판정되며, `env`·`nice` 같은 셸 프리픽스는 벗겨진다.
    ⇒ 이슈 본문이 지목한 「공백 수」는 **취약 축이 아니었다.**
    실제로 새는 곳은 **인자 삽입**이다 — `echo -n x`는 `echo x*`를 빠져나가지만
    `*echo*x*`는 잡는다. **틀린 전제 위에 세운 규칙도 통과는 하므로**
    근거를 실호출로 갈아 끼웠다(재측 절차는 정본 §명령 글롭).

보증하지 않는 것:
    · 규칙이 **옳은 명령을 겨냥하는지**는 보지 않는다 — **작성 스타일까지**다.
      겨냥 대상의 타당성은 사람이 진다.
    · 비-`Bash` 규칙(`Edit(...)`·`WebFetch(domain:...)`)은 **축이 다르다.**
      모집단 밖이며 **빠뜨린 것이 아니다**(경로 축은 §경로 경계가 갖는다).
    · `.claude/settings.local.json`은 **gitignore 대상**(개인 설정)이라 보지 않는다.
      커밋되지 않는 파일을 커밋 게이트가 요구할 수 없다.
      ⚠️ 그쪽 `allow`도 런타임에는 실제로 통제에 참여한다 — **선언된 공백**이다.
"""

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = PROJECT_ROOT / ".claude/settings.json"

# `Bash(<패턴>)` 규칙만 이 검사기의 모집단이다.
BASH_RULE_RE = re.compile(r"^Bash\((.*)\)$", re.DOTALL)

# 넓게 잡아야 하는 축(빗나가면 과차단 = fail-safe)과 좁게 잡아야 하는 축.
BROAD_DECISIONS = ("deny", "ask")
NARROW_DECISION = "allow"

# 규약을 벗어나야 하는 규칙과 **그 사유**.
# 🔴 사유 없는 예외는 이 검사기가 거부한다 — 주석으로만 두면 늘릴 때 조용히 빠진다
#    (`scripts/worker_boundaries.py`의 `ASYMMETRY_REASONS`와 같은 형태).
# 🔴 규약을 이미 지키는 규칙을 여기 남겨 두는 것도 위반이다(죽은 예외) —
#    남아 있으면 다음 사람이 "이 축은 원래 예외"로 읽는다.
EXEMPT_REASONS: dict[str, str] = {}


def main() -> int:
    """`settings.json`의 Bash 규칙을 축별로 갈라 규약 위반을 센다."""
    # ── 입력 ────────────────────────────────────────────────────────────────
    # 🔴 fail-closed: 읽지 못하거나 깨졌으면 **통과가 아니라 실패**다.
    #    "검사 못 함"이 "위반 없음"으로 둔갑하는 것이 이 저장소가 반복해 밟은 형태다.
    if not SETTINGS.is_file():
        print(f"🔴 설정 파일을 찾지 못했다: {SETTINGS}", file=sys.stderr)
        return 1

    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"🔴 설정 파일을 읽지 못했다 — {error}", file=sys.stderr)
        return 1

    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        # 🔴 판정 키 부재는 **값이 0건인 것과 다르다**. 이 저장소는 반드시
        #    `permissions`를 갖는다 — 없다면 배선이 무너진 것이다.
        print("🔴 `permissions` 키가 없다 — 통제 배선이 사라진 상태다", file=sys.stderr)
        return 1

    # ── 판정 ────────────────────────────────────────────────────────────────
    violations: list[str] = []
    counted = {"deny": 0, "ask": 0, "allow": 0}
    exempted: list[str] = []
    seen_rules: set[str] = set()

    for decision in (*BROAD_DECISIONS, NARROW_DECISION):
        rules = permissions.get(decision, [])
        if not isinstance(rules, list):
            violations.append(f"{decision}: 배열이 아니다 — 파싱할 수 없다")
            continue

        for rule in rules:
            if not isinstance(rule, str):
                violations.append(f"{decision}: 문자열이 아닌 규칙이 있다 — {rule!r}")
                continue

            matched = BASH_RULE_RE.match(rule.strip())
            if matched is None:
                # 비-`Bash` 규칙 — 모집단 밖. 세지도 않는다.
                continue

            seen_rules.add(rule)
            counted[decision] += 1
            pattern = matched.group(1)

            if rule in EXEMPT_REASONS:
                exempted.append(f"{decision}: {rule}")
                continue

            starts_broad = pattern.startswith("*")
            ends_broad = pattern.endswith("*")

            if decision in BROAD_DECISIONS:
                if not (starts_broad and ends_broad):
                    violations.append(
                        f"{decision}: `{rule}` — 차단 축은 **전면 와일드카드**여야 한다"
                        " (앞뒤를 `*`로 감싼다). 접두 앵커는 인자 삽입으로 빠져나간다"
                    )
            elif starts_broad:
                violations.append(
                    f"{decision}: `{rule}` — 허용 축은 **접두 앵커**여야 한다"
                    " (`*`로 시작하지 않는다). 넓히면 결합 명령이 허용쪽으로 샌다"
                )

    # 죽은 예외 — 목록에는 있는데 규칙이 사라졌거나, 이미 규약을 지키는 것.
    for rule, reason in EXEMPT_REASONS.items():
        if not reason.strip():
            violations.append(f"예외 `{rule}`에 사유가 비어 있다")
        if rule not in seen_rules:
            violations.append(
                f"예외 `{rule}`에 대응하는 규칙이 `settings.json`에 없다 — 죽은 예외"
            )

    # ── 출력 ────────────────────────────────────────────────────────────────
    total = sum(counted.values())
    if violations:
        print(f"🔴 글롭 작성 규약 위반 {len(violations)}건 (Bash 규칙 {total}개 중)")
        for line in violations:
            print(f"  · {line}")
        print()
        print("규약: docs/conventions/agents/permissions.md §명령 글롭")
        print("  deny·ask → `*a*b*` (전면 와일드카드) / allow → `cmd sub*` (접두 앵커)")
        return 1

    summary = " · ".join(f"{key} {value}" for key, value in counted.items())
    print(f"✅ 글롭 작성 규약 위반 0건 — Bash 규칙 {total}개 ({summary})")
    if exempted:
        print(f"   예외 {len(exempted)}건(사유 있음): {', '.join(exempted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
