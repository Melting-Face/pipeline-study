"""짝 가드가 **같은 경계표**를 쓰는지, 비대칭이 **선언된 것뿐**인지 대조한다.

왜 이 파일인가 (Issue #53):
    두 런타임의 `worker_path_guard.py`가 같은 표를 두 번 적고 있어 값이 갈렸고,
    **갈렸다는 신호가 어디에서도 나지 않았다.** 표를 `scripts/worker_boundaries.py`
    하나로 모은 뒤 필요한 것은 *"다시 갈리면 알려주는 수단"* 이다.

🔴 **무엇을 세는가**: 아래 단정은 「가드 수」나 「워커 수」가 아니라
    **두 표가 갈리는 지점**을 본다. 워커 하나에 지점이 둘일 수 있다.

⚠️ **표가 같아도 매칭이 갈리면 결과는 갈린다.** 그래서 표 대조만으로 끝내지 않고
    실제 가드를 **호출해** 대조군과 함께 본다(§접두어 트랩·§통제 파일).
"""

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_GUARD = PROJECT_ROOT / "scripts" / "worker_path_guard.py"
CODEX_GUARD = PROJECT_ROOT / ".codex" / "hooks" / "worker_path_guard.py"
CODEX_AGENTS = PROJECT_ROOT / ".codex" / "agents"

if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import worker_boundaries  # noqa: E402


def load_guard(name: str, path: Path) -> ModuleType:
    """가드를 **모듈로 올려** 런타임 상수를 직접 읽는다.

    🔴 문서·주석이 아니라 **런타임 값**을 본다. 이 저장소에서 주석이 코드와
    갈린 채로 오래 남은 전례가 있다(`analyst_path_guard.py`의 fail 방향).
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None, path
    assert spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkerBoundariesTest(unittest.TestCase):
    """공용 표 ↔ 두 가드 ↔ 워커 배선을 세 방향으로 대조한다."""

    def setUp(self) -> None:
        """두 짝 가드를 각각 독립 모듈로 올린다."""
        self.claude = load_guard("probe_claude_path_guard", CLAUDE_GUARD)
        self.codex = load_guard("probe_codex_path_guard", CODEX_GUARD)

    # ── 표 대조 ──────────────────────────────────────────────────────────
    def test_guards_read_the_shared_table(self) -> None:
        """두 가드의 `BOUNDARIES`가 공용 모듈 조립 결과와 같다.

        🔴 이 단정이 잡는 것은 **가드가 표를 다시 자기 안에 적는 것**이다.
        리터럴을 되살리면 여기서 깨진다(Issue #53의 재발 경로 1호).
        """
        assert worker_boundaries.claude_boundaries() == self.claude.BOUNDARIES
        assert worker_boundaries.codex_boundaries() == self.codex.BOUNDARIES

    def test_asymmetry_is_only_what_was_declared(self) -> None:
        """두 표의 차이가 **선언된 overlay와 정확히 일치**한다.

        대조군을 함께 둔다 — 공통 항목이 실제로 같은지 보지 않으면
        "차집합이 비었다"가 **양쪽이 똑같이 비어 있을 때도** 성립한다.
        """
        claude_table = worker_boundaries.claude_boundaries()
        codex_table = worker_boundaries.codex_boundaries()

        divergent = {
            worker
            for worker in set(claude_table) | set(codex_table)
            if claude_table.get(worker) != codex_table.get(worker)
        }
        declared = set(worker_boundaries.CLAUDE_ONLY) | set(
            worker_boundaries.CODEX_ONLY
        )
        assert divergent == declared, (
            f"선언되지 않은 드리프트다: {sorted(divergent - declared)} / "
            f"선언만 있고 실재하지 않는 것: {sorted(declared - divergent)}"
        )

        shared = set(claude_table) & set(codex_table) - declared
        assert shared, "대조군: 공유 워커가 0이면 위 단정은 아무것도 증명하지 않는다"
        for worker in shared:
            assert claude_table[worker] == codex_table[worker], worker

    def test_every_asymmetry_carries_a_reason(self) -> None:
        """overlay 항목마다 사유가 있다 — 안 적으면 다음 사람이 갭으로 읽고 지운다.

        Issue #53이 요구한 규율을 **기계로** 강제한다. 사유를 주석으로만 두면
        항목을 늘릴 때 조용히 빠진다.
        """
        declared = set(worker_boundaries.CLAUDE_ONLY) | set(
            worker_boundaries.CODEX_ONLY
        )
        assert declared == set(worker_boundaries.ASYMMETRY_REASONS)
        for worker, reason in worker_boundaries.ASYMMETRY_REASONS.items():
            assert len(reason.strip()) > 40, f"{worker}: 사유가 너무 짧다"

    # ── 배선 대조 ────────────────────────────────────────────────────────
    def test_codex_writable_workers_all_have_a_boundary(self) -> None:
        """`workspace-write` Codex 워커는 **빠짐없이** 경계를 갖는다.

        🔴 포함(⊆)이지 일치(==)가 아니다. `read-only` 워커를 표에 넣는 것은
        심층 방어라 정상이고, 반대로 **쓰기 가능한데 표에 없는 것**만 갭이다
        (미등재는 `deny`로 떨어지지만 그것은 암묵 의존이다).
        """
        writable, declared_names = set(), set()
        for path in sorted(CODEX_AGENTS.glob("*.toml")):
            text = path.read_text(encoding="utf-8")
            name = next(
                line.split("=", 1)[1].strip().strip("\"'")
                for line in text.splitlines()
                if line.startswith("name")
            )
            declared_names.add(name)
            if 'sandbox_mode = "workspace-write"' in text:
                writable.add(name)

        assert writable, "대조군: toml에서 쓰기 워커를 하나도 못 읽었다면 파싱이 죽었다"
        missing = writable - set(self.codex.BOUNDARIES)
        assert not missing, f"쓰기 워커인데 Codex 경계가 없다: {sorted(missing)}"
        phantom = set(self.codex.BOUNDARIES) - declared_names
        assert not phantom, (
            f"`.codex/agents/`에 없는 워커가 표에 있다: {sorted(phantom)}"
        )

    # 🔴 **Claude 쪽 배선 ↔ 표 대조는 여기 두지 않는다** — 정본이
    #    `scripts/worker_wiring_check.py`(pre-commit `worker-wiring`)다.
    #    같은 검사를 두 곳에 두면 이 파일이 막으려는 형태가 그대로 재현된다.
    #    ⚠️ 처음에 여기에 넣었다가 **계측 단위가 어긋났다**: 파일 전체에서
    #    `worker_path_guard.py`를 grep하면 지시문 *본문의 언급*까지 세어
    #    `data-qa`가 배선된 것으로 잡힌다(그 워커에는 경로 가드가 없다).
    #    배선은 **프론트매터 안의 `command:` 줄**만 세야 한다 — 정본 검사기가
    #    그렇게 하고 있고, 그것이 여기 두지 않는 두 번째 이유다.
    #    Codex 쪽은 그 검사기의 모집단 밖이라 위 `test_codex_...`가 진다.

    # ── 매칭 축 (표가 같아도 여기서 갈릴 수 있다) ────────────────────────
    def test_control_paths_are_matched_case_insensitively(self) -> None:
        """통제 스크립트 판정이 **대소문자를 무시**하고 이름 규약 밖도 잡는다.

        macOS 파일시스템이 대소문자를 무시하므로 구분해 비교하면 같은 실파일이
        통과한다. Codex 짝 가드가 이 축에서 구분하고 있었다(Issue #53).
        """
        blocked = (
            "scripts/worker_path_guard.py",
            "SCRIPTS/Worker_Path_Guard.PY",
            "scripts/worker_boundaries.py",
            ".codex/hooks/session_start.py",
            ".codex/hooks/journal_pre_write.py",
            # 커밋 게이트 검사기(`*_check.py`)도 통제 배선이다 — 런타임 hook만 막고
            # 여기를 열어 두면 워커가 검사기를 고쳐 규약 강제를 무효화할 수 있다
            # (고장이 아니라 조용한 무효화라 신호가 없다). Issue #33.
            "scripts/permission_glob_check.py",
            "scripts/hook_files_check.py",
        )
        for relative in blocked:
            assert worker_boundaries.control_path(relative), relative

        # 대조군 — 이게 통과하지 않으면 위 단정은 "전부 막는다"와 구분되지 않는다.
        for relative in ("docs/setup.md", "scripts/doc_lint.py", "README.md"):
            assert worker_boundaries.control_path(relative) == "", relative

    def test_allow_file_entry_is_not_a_prefix(self) -> None:
        """`allow`의 파일 항목은 **완전일치**다 — `README.md`가 `.bak`을 열면 안 된다.

        `permissions.md` §경로 경계가 요구하는 접두어 트랩 셀이다. 표가 아니라
        **매칭**의 축이라 두 런타임을 각각 호출해 본다.
        """
        assert self._claude_decision("tech-writer", "README.md") == {}
        denied = self._claude_decision("tech-writer", "README.md.bak")
        assert denied.get("permissionDecision") == "deny"

        assert self._codex_decision("tech-writer", "README.md") == {}
        codex_denied = self._codex_decision("tech-writer", "README.md.bak")
        assert codex_denied.get("permissionDecision") == "deny"

    def test_github_workflows_are_denied_to_data_engineer_in_both_runtimes(
        self,
    ) -> None:
        """CI 워크플로 단독 소유가 **두 런타임 모두**에서 집행된다.

        Codex 쪽에만 빠져 있던 축이다(Issue #53 §축 3 — 실제 통제 갭).
        대조군으로 그 워커가 정당하게 소유한 경로를 함께 친다.
        """
        for decide in (self._claude_decision, self._codex_decision):
            denied = decide("data-engineer", ".github/workflows/ci.yml")
            assert denied.get("permissionDecision") == "deny"
            allowed = decide("data-engineer", "dagster/dockerfile.d/src/probe.py")
            assert allowed == {}, "대조군: 소관 경로는 통과해야 한다"

    def test_runtime_control_wiring_is_denied_in_both_runtimes(self) -> None:
        """`.claude/`·`.codex/` 배선을 구현 워커가 못 고친다(양방향).

        `.codex/`가 Claude 쪽에만 빠져 있어 **Claude 워커가 Codex 통제 배선을
        고칠 수 있었다** — Issue #53이 "의도된 비대칭"으로 분류한 축의 정정이다.
        """
        for decide in (self._claude_decision, self._codex_decision):
            for worker in ("data-engineer", "devops-engineer"):
                for target in (".claude/settings.json", ".codex/hooks.json"):
                    denied = decide(worker, target)
                    assert denied.get("permissionDecision") == "deny", (
                        f"{worker} → {target}"
                    )
            # 🔴 PASS 대조군 — 위와 같은 이유다(단정이 전부 `deny`뿐인 셀은
            #    관측 경로가 죽어도 초록이다). 각 워커의 소관 경로를 함께 친다.
            assert decide("data-engineer", "dagster_project/defs/probe.py") == {}
            assert decide("devops-engineer", "terraform/probe.tf") == {}

    # ── 매칭 축 · 방향 (표가 같아도 여기서 갈린다) ──────────────────────
    #
    # 🔴 아래 셋은 **같은 방향으로 통일하면 안 된다.** `deny`·`except`는 대소문자를
    #    무시하고(막는 쪽 과잉 = fail-closed), `allow`는 구분한다(넓히는 쪽 과잉은
    #    대소문자 구분 파일시스템에서 fail-open). 네 번째 셀이 그 반대 방향을 박아
    #    "일관성"을 이유로 셋을 뒤집는 것을 막는다.
    def test_deny_is_case_insensitive_in_both_runtimes(self) -> None:
        """`deny` 접두어 대조가 **대소문자를 무시**한다(양 런타임).

        macOS 파일시스템이 대소문자를 무시하므로 `Terraform/main.tf`는 금지 경로와
        **같은 실파일**이다. Codex 짝 가드가 이 축에서 구분하고 있었다(Issue #53 —
        표는 합쳐졌으나 매칭이 두 벌로 남아 있던 자리).
        """
        for decide in (self._claude_decision, self._codex_decision):
            # 대조군 — 소문자 원본이 막히지 않으면 아래 단정은 아무것도 증명하지 않는다.
            assert (
                decide("data-engineer", "terraform/main.tf").get("permissionDecision")
                == "deny"
            ), "대조군: 소문자 원본은 막혀야 한다"
            for target in (
                "Terraform/main.tf",
                "TERRAFORM/main.tf",
                ".GitHub/workflows/ci.yml",
            ):
                assert decide("data-engineer", target).get("permissionDecision") == (
                    "deny"
                ), target
            # 대조군 — 소관 경로는 통과해야 한다(전면 차단과 구분).
            assert decide("data-engineer", "dagster_project/defs/probe.py") == {}

    def test_deny_file_entry_is_a_prefix_in_both_runtimes(self) -> None:
        """`deny` 항목은 `/`가 없어도 **접두어**다 — `.env`가 `.env.local`을 막는다.

        🔴 `allow`와 **반대 방향**이다. `worker_boundaries.py` §COMMON 주석이
        *"`deny`에는 그 분기를 두지 않는다: 막는 쪽은 넓게 걸리는 편이 안전하다"* 로
        의미론을 못 박는데, Codex 짝 가드의 `matches_prefix()`가 `deny`에도 완전일치를
        적용해 **공용 표가 선언한 의미론을 런타임이 안 지키고 있었다**(Issue #53).
        ⇒ `.env`가 금지인데 `.env.local`·`.env.prod`가 열려 있었다.
        """
        for decide in (self._claude_decision, self._codex_decision):
            # 대조군 — 완전일치 자신이 막히는지 먼저 본다.
            assert decide("data-engineer", ".env").get("permissionDecision") == "deny"
            for target in (".env.local", ".envrc", "compose.yml.bak"):
                assert decide("data-engineer", target).get("permissionDecision") == (
                    "deny"
                ), target
            # 🔴 **PASS 대조군은 선택이 아니다.** 단정이 전부 `== "deny"` 뿐인 셀은
            #    관측 경로가 죽어도 초록이다 — 실측으로 확인했다(합성 페이로드를 깨자
            #    `_decision()`의 방어가 없던 시점 기준 이 셀과 아래 통제 배선 셀
            #    **둘만** 통과했고, PASS 대조군을 가진 나머지 다섯은 잡혔다).
            assert decide("data-engineer", "dagster_project/defs/probe.py") == {}

    def test_except_is_case_insensitive_in_both_runtimes(self) -> None:
        """`except`(판정 근거 문서) 대조가 **대소문자를 무시**한다(양 런타임).

        `deny`와 같은 방향이다 — 판정 대상이 판정 기준을 고치는 것을 막는 축이라
        과잉 차단이 안전하다. 접두어 항목(`docs/skills/`)도 함께 친다.
        """
        for decide in (self._claude_decision, self._codex_decision):
            # 대조군 — 소문자 원본.
            assert (
                decide("tech-writer", "docs/security.md").get("permissionDecision")
                == "deny"
            ), "대조군: 소문자 원본은 막혀야 한다"
            for target in ("docs/Security.md", "docs/Skills/hub.md"):
                assert decide("tech-writer", target).get("permissionDecision") == (
                    "deny"
                ), target
            # 대조군 — 소관 문서는 통과해야 한다.
            assert decide("tech-writer", "docs/setup.md") == {}

    def test_allow_stays_case_sensitive_in_both_runtimes(self) -> None:
        """🔴 `allow`는 **대소문자를 구분한다** — 위 두 셀과 방향이 반대다.

        소문자화하면 대소문자를 **구분하는** 파일시스템(Linux CI)에서 `DOCS/`라는
        **진짜 다른 디렉터리**를 열어 준다(fail-open). 여기서는 걸러지는 쪽이
        fail-closed다.
        ⚠️ 이 셀이 없으면 다음 사람이 "일관성"을 이유로 세 축을 모두 `lower()`로
        통일하고 그 변경이 **초록으로 통과**한다.
        """
        for decide in (self._claude_decision, self._codex_decision):
            # 대조군 — 정확한 표기는 통과한다.
            assert decide("tech-writer", "docs/setup.md") == {}
            assert (
                decide("tech-writer", "DOCS/setup.md").get("permissionDecision")
                == "deny"
            ), "allow는 대소문자를 구분해야 한다(구분 FS에서 fail-open 방지)"

    # ── 헬퍼 ────────────────────────────────────────────────────────────
    def _claude_decision(self, worker: str, target: str) -> dict[str, str]:
        """Claude 가드를 실제 hook 입출력으로 호출한다."""
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(PROJECT_ROOT / target)},
        }
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(CLAUDE_GUARD), worker],
            input=json.dumps(payload),
            env={"CLAUDE_PROJECT_DIR": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin"},
            check=False,
            capture_output=True,
            text=True,
        )
        return self._decision(result)

    def _codex_decision(self, worker: str, target: str) -> dict[str, str]:
        """Codex 가드를 워커 인자 고정으로 호출한다(변인을 경로 하나로 줄인다)."""
        payload = {
            "tool_name": "apply_patch",
            "cwd": str(PROJECT_ROOT),
            "tool_input": {"command": f"*** Update File: {target}\n"},
        }
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(CODEX_GUARD), worker],
            input=json.dumps(payload),
            check=False,
            capture_output=True,
            text=True,
        )
        return self._decision(result)

    def _decision(self, result: subprocess.CompletedProcess[str]) -> dict[str, str]:
        """가드 출력에서 결정을 뽑는다. 무출력이면 통과다.

        🔴 **fail-closed `deny`를 판정 `deny`로 읽지 않는다.** 두 가드는 입력을 못
        읽으면 `deny`를 내는데(안전한 설계다), 그 값은 *"경계가 막았다"* 가 아니라
        *"관측 경로가 죽었다"* 다. 둘이 **같은 모양**이라 구분하지 않으면 페이로드
        형태가 어긋난 날 **`assert == "deny"` 전 셀이 초록으로 통과**한다.
        실제로 Issue #53을 다시 재던 세션이 이 함정에 빠졌다 — 합성 페이로드의 JSON
        이스케이프가 깨져 Codex가 14/14 `deny`를 냈고, 사유 문자열을 열어보기 전까지
        **드리프트가 0건으로 보였다.**
        ⇒ fail-closed 표지가 있으면 **단정 실패로 떨어뜨린다**(통과시키지 않는다).

        🔴 **표지를 문구로 열거하지 않는다.** 처음에는 `"읽지 못했다"`·`"찾지 못했다"`
        두 문구를 열거했는데 `devops-qa` 감사가 **셋째를 찾아냈다** — Codex 가드의
        *"서브에이전트 역할을 식별하지 못해 patch를 차단했다"* 가 어느 쪽에도 안 걸렸다.
        당시 호출 경로로는 도달 불가라 실해는 없었으나, **주석이 "두 가드의 fail-closed
        사유"라고 검증 범위를 실제보다 넓게 주장**하고 있었다.
        ⇒ 열거 대신 **다섯 사유가 공통으로 다는 `(fail-closed)` 표지 하나**를 본다.
        새 fail-closed 분기가 규약대로 그 표지를 달면 **여기를 고치지 않아도 덮인다**.
        ⚠️ 표지를 안 달면 다시 샌다 — 그 결합은 이 단정이 아니라 가드 쪽 규율이다.
        """
        if not result.stdout.strip():
            return {}
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        reason = decision.get("permissionDecisionReason", "")
        assert "fail-closed" not in reason, (
            "가드가 경계 판정이 아니라 **fail-closed**로 deny했다 — 이 셀은 경계를 "
            f"검사하지 못했다(합성 페이로드가 프로덕션 형태와 어긋났다): {reason}"
        )
        return decision


if __name__ == "__main__":
    unittest.main()
