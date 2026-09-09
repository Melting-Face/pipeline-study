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
        """가드 출력에서 결정을 뽑는다. 무출력이면 통과다."""
        if not result.stdout.strip():
            return {}
        return json.loads(result.stdout)["hookSpecificOutput"]


if __name__ == "__main__":
    unittest.main()
