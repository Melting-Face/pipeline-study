#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""가드 단위 테스트를 돌리고 **「돌지 않은 것」을 통과로 읽지 않는다** (Issue #54).

사용법:
    python3 scripts/tests/run_guard_tests.py     # repo 루트에서

왜 `python3 -m unittest`를 직접 훅에 걸지 않는가:
    🔴 `unittest`의 종료 코드는 **0건 실행을 통과로 읽는다.** 모듈 임포트가 깨지거나
    디스커버리 경로가 어긋나면 `Ran 0 tests` + exit 0이 나온다 — 게이트에 걸어 둔
    상태에서 이것은 **「초록인데 아무것도 안 돈 상태」** 이고, 게이트가 없는 것보다
    나쁘다(검증됐다고 믿게 만든다).

    같은 계열을 이 저장소는 이미 막고 있다 — `.github/workflows/ci.yml`의 `terraform`
    잡이 `found -eq 0`으로 *"0건을 통과로 읽지 않는다"* 를 집행한다.
    여기도 같은 처방이다.

    skip도 같은 축이다. `setUp`이 `skipTest`를 부르면 **테스트는 초록인데 실행되지
    않았다.** 그래서 ⓐ 테스트 쪽에서 `skipTest`를 걷어내고(#54) ⓑ 여기서 **skip이
    한 건이라도 있으면 실패**시킨다 — ⓐ만 하면 새 skip이 언제든 다시 들어온다.

무엇을 세는가 (계측 단위):
    🔴 아래 `실행`은 **테스트 메서드 수**이지 *검증되는 가드 수*가 아니다.
    현재 가드 3종(`journal_guard` · Claude/Codex `worker_path_guard` ·
    `plan_mirror_guard` 일부)을 13개 메서드가 나눠 본다.
    ⇒ **기대값을 고정 숫자로 박제하지 않는다.** 테스트를 늘릴 때마다 무관한 실패가
    나고, 여기서 막고 싶은 것은 「13이 아님」이 아니라 **「0건」** 이다.

보증하지 않는 것:
    이 러너는 `scripts/tests/` 아래 테스트가 **돌았는지**만 본다. 테스트가 없는 가드는
    여기서도 보이지 않는다 — `analyst_path_guard` · `skill_gate_guard` ·
    `research_gate_guard` · `protected_paths_guard` · `session_sync_guard` ·
    `commit_manifest_guard`는 **테스트 0건**이다(Issue #55 범위).
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = PROJECT_ROOT / "scripts" / "tests"

# 디스커버리 패턴. 🔴 이 값을 어긋나게 두면 실행이 0건이 되는데, 그 상태를 아래
# `testsRun == 0` 분기가 잡는다(게이트가 자기 눈이 멀었는지 스스로 본다).
# ⚠️ 실측(호스트 CPython 3.14.5): `TextTestRunner`도 `NO TESTS RAN`을 실패로 쳐서
#    이 분기가 없어도 exit 1이 났다. 그래도 **런타임 동작에 기대지 않는다** —
#    CI 러너는 3.12이고 그쪽 동작은 **미확인**이며, 판정 사유를 사람이 읽을 수 있는
#    문장으로 남기는 것이 이 분기의 몫이다(어느 축에서 막혔는지 보이게).
TEST_PATTERN = "test_*.py"


def main() -> int:
    """가드 테스트를 discover해 실행하고 0건·skip·실패를 각각 갈라 판정한다."""
    # `from scripts.plan_mirror_guard import ...` 형태의 임포트가 살려면 repo 루트가
    # sys.path에 있어야 한다. 스크립트로 직접 실행하면 sys.path[0]은 이 파일의
    # 디렉터리(scripts/tests)라 루트가 들어오지 않는다.
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    # 🔴 `loader.discover()`를 쓰지 않는다 — `scripts/`·`scripts/tests/`에 `__init__.py`
    #    가 없어(네임스페이스 패키지) `Start directory is not importable`로 죽는다.
    #    `__init__.py`를 새로 두면 `scripts/**`가 패키지가 되어 ruff·mypy의 모집단이
    #    함께 흔들린다 — 게이트 하나를 걸려고 건드릴 범위가 아니다.
    #    ⇒ 파일을 글롭해 **모듈명으로** 올린다. 임포트 경로는 지금 쓰는 것과 같다
    #    (`python3 -m unittest scripts.tests.test_journal_guard`).
    module_names = [
        f"scripts.tests.{path.stem}" for path in sorted(TESTS_DIR.glob(TEST_PATTERN))
    ]

    # 🔴 임포트 실패는 조용히 지나가지 않는다 — unittest는 임포트 오류를
    # `_FailedTest`라는 **가짜 테스트**로 바꿔 담으므로 실행 결과에서 실패로 드러난다.
    suite = unittest.defaultTestLoader.loadTestsFromNames(module_names)
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(suite)

    ran = result.testsRun
    skipped = len(result.skipped)
    failed = len(result.failures) + len(result.errors)

    print(
        f"가드 테스트 — 실행 {ran}개(테스트 메서드 수) · "
        f"skip {skipped}개 · 실패 {failed}개"
    )

    # 세 분기를 **따로** 판정한다. 한 줄로 합치면 어느 축에서 막혔는지 안 보인다.
    if ran == 0:
        print(
            "🔴 실행된 테스트가 0건이다 — 통과로 읽지 않는다. "
            f"디스커버리 경로({TESTS_DIR})나 패턴({TEST_PATTERN})을 확인하라.",
            file=sys.stderr,
        )
        return 1

    if skipped:
        for test, reason in result.skipped:
            print(f"🔴 skip: {test} — {reason}", file=sys.stderr)
        print(
            "🔴 skip은 통과가 아니다 — 실행되지 않은 것이다. "
            "면제가 필요하면 규칙을 문서에 적고 테스트를 지워라"
            "(조용히 건너뛰지 않는다).",
            file=sys.stderr,
        )
        return 1

    if failed:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
