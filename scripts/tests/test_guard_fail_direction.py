"""가드가 **판정을 못 한 경우**를 통과로 읽지 않는지 검증한다(Issue #55).

왜 이 파일인가:
    이 저장소의 hook은 통과를 **`exit 0` + 무출력**으로 표현한다(예외:
    `skill_gate_guard.py`만 `allow`를 명시 출력). 그래서 스크립트가 **죽어도**
    하네스가 보는 것은 「결정 없음」으로 통과와 **같다** — 통과와 고장이
    관측상 구분되지 않는다. 아래 셋은 실제로 그 상태였다.

🔴 **「통과」가 곧 취약은 아니다.** fail-open이 의도인 자리가 있다
    (`plan_mirror_guard.py`는 통제가 아니라 미러라 결정을 아예 안 낸다).
    여기 담은 것은 **의도가 아니라 누락으로 판정된 축**뿐이고, 의도로 남긴 축은
    코드 주석에 사유가 적혀 있다.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_GATE = PROJECT_ROOT / "scripts" / "skill_gate_guard.py"
COMMIT_MANIFEST = PROJECT_ROOT / "scripts" / "commit_manifest_guard.py"
CODEX_RELAYS = (
    PROJECT_ROOT / ".codex" / "hooks" / "journal_pre_write.py",
    PROJECT_ROOT / ".codex" / "hooks" / "session_start.py",
    PROJECT_ROOT / ".codex" / "hooks" / "stop_guard.py",
)


class GuardFailDirectionTest(unittest.TestCase):
    """판정 불가 입력에 각 가드가 어느 방향으로 실패하는지 본다."""

    def setUp(self) -> None:
        """가드마다 자기 발동 조건을 만족시킨 임시 트리를 만든다."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

        # 🔴 `skipTest`가 아니라 **실패**다(Issue #54와 같은 근거) — `git`은 클론
        #    자체의 전제라 없으면 「초록인데 안 돈 상태」가 된다.
        git = shutil.which("git")
        if git is None:
            self.fail("git 실행 파일이 필요하다 — 이 테스트는 skip하지 않고 실패한다")
        self.git = git

        # 🔴 `GIT_*`를 걷어낸다. 이 테스트는 pre-commit 훅 안에서도 도는데, 그때
        #    부모 git이 `GIT_INDEX_FILE`·`GIT_DIR`을 물려줘 아래 임시 저장소의
        #    git 명령이 **바깥 저장소의 인덱스**를 쓴다.
        #    ⚠️ 손으로 돌 때는 이 변수가 없어 영영 안 드러난다 —
        #    `test_journal_guard.py`가 같은 자리에서 이미 밟았다.
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }

    def tearDown(self) -> None:
        """임시 트리를 정리한다."""
        self.temporary_directory.cleanup()

    def test_skill_gate_denies_when_the_worker_file_cannot_be_read(self) -> None:
        """지시문이 **있는데 읽히지 않으면** 차단한다.

        이 가드는 파싱 실패·표 부재·값 부재를 전부 `deny`로 닫아 두었는데
        읽기 한 곳만 미보호였다 — `OSError`가 traceback으로 새어 무출력 종료가
        되고, 하네스는 그것을 통과로 읽었다.

        대조군 둘을 같은 테스트에 둔다 — `allow`와 `deny`가 갈리지 않으면
        「전부 막힌다」와 구분되지 않아 이 단정이 아무것도 증명하지 않는다.
        """
        allowed = self._skill_decision("dagster-expert", "data-engineer", PROJECT_ROOT)
        assert allowed.get("permissionDecision") == "allow", "대조군: 등재 스킬"
        refused = self._skill_decision("terraform-test", "data-engineer", PROJECT_ROOT)
        assert refused.get("permissionDecision") == "deny", "대조군: 미등재 스킬"

        agents = self.root / ".claude" / "agents"
        agents.mkdir(parents=True)
        unreadable = agents / "data-engineer.md"
        unreadable.write_text("x", encoding="utf-8")
        os.chmod(unreadable, 0)
        try:
            blocked = self._skill_decision("anything", "data-engineer", self.root)
        finally:
            os.chmod(unreadable, 0o644)
        assert blocked.get("permissionDecision") == "deny"
        assert "읽지 못했다" in blocked["permissionDecisionReason"]

    def test_commit_manifest_separates_zero_targets_from_a_failed_count(self) -> None:
        """`git diff` 실패를 **「대상 0건」으로 읽지 않는다.**

        예전에는 실행 실패·비-0 종료가 똑같이 빈 목록이 되어
        `✅ 대상 0건 전부 매니페스트 안`이 찍혔다 — 판정을 못 한 것이
        **통과 신호로 둔갑**했다(오답보다 검산을 통과하는 정답 모양이 위험하다).

        🔴 변인을 하나로 줄인다 — 매니페스트를 **실재시켜** 그 축을 먼저
        통과시키지 않으면 「매니페스트 없음」이 앞에서 발동해 이 축을 가린다.
        """
        broken = self._make_repo(with_commit=False)
        undetermined = self._commit_decision(broken)
        assert undetermined.get("permissionDecision") == "ask"
        assert (
            "커밋 대상 목록을 만들지 못해" in undetermined["permissionDecisionReason"]
        )

        healthy = self._make_repo(with_commit=True)
        clean = self._commit_decision(healthy)
        assert "✅" in clean["permissionDecisionReason"], "대조군: 정상 diff는 대조된다"

    def test_codex_relays_handle_a_guard_timeout(self) -> None:
        """Codex 중계 hook 셋이 `TimeoutExpired`를 **잡는다**.

        ⚠️ **이 검사가 보증하는 것은 핸들러의 존재까지다.** 방향(중계는 `deny`,
        정보성 둘은 통과 + 가시 메시지)은 실호출로 확인했고 그 기록은
        `docs/conventions/agents/enforcement.md` §타임아웃 방향 표에 있다.
        여기서 실제 타임아웃을 재지 않는 이유는 **하나에 8초**라 커밋 게이트에
        올릴 수 없기 때문이다 — 빠뜨린 것이 아니라 비용 때문의 결정이다.
        """
        for relay in CODEX_RELAYS:
            text = relay.read_text(encoding="utf-8")
            assert "subprocess.run(" in text, f"{relay.name}: 대조군이 죽었다"
            assert re.search(r"except\s+subprocess\.TimeoutExpired", text), (
                f"{relay.name}: 타임아웃을 안 잡으면 traceback + 무출력이 되고 "
                "하네스는 그것을 통과로 읽는다"
            )

    # ── 헬퍼 ────────────────────────────────────────────────────────────
    def _skill_decision(self, skill: str, worker: str, cwd: Path) -> dict[str, str]:
        """스킬 게이트를 실제 hook 입출력으로 호출한다."""
        payload = {
            "tool_name": "Skill",
            "tool_input": {"skill": skill},
            "agent_type": worker,
            "cwd": str(cwd),
        }
        return self._decision(self._run(SKILL_GATE, payload))

    def _commit_decision(self, repository: Path) -> dict[str, str]:
        """커밋 매니페스트 가드를 pathspec 형태로 호출한다."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m probe -- docs/a.md"},
            "cwd": str(repository),
            "session_id": "abcdef12-0000-0000-0000-000000000000",
        }
        return self._decision(self._run(COMMIT_MANIFEST, payload))

    def _make_repo(self, *, with_commit: bool) -> Path:
        """매니페스트 축을 통과하는 저장소를 만든다.

        `with_commit=False`면 `HEAD`가 없어 pathspec 경로의 `git diff HEAD`가
        비-0으로 죽는다 — 그것이 이 테스트가 흔드는 **단 하나의 변인**이다.
        """
        repository = Path(tempfile.mkdtemp(dir=self.root))
        subprocess.run(  # noqa: S603
            [self.git, "init", "-q", str(repository)],
            check=True,
            env=self.environment,
        )
        (repository / "docs").mkdir()
        (repository / "docs" / "a.md").write_text("hello", encoding="utf-8")
        if with_commit:
            environment = {
                **self.environment,
                "GIT_AUTHOR_NAME": "probe",
                "GIT_AUTHOR_EMAIL": "probe@example.invalid",
                "GIT_COMMITTER_NAME": "probe",
                "GIT_COMMITTER_EMAIL": "probe@example.invalid",
            }
            for arguments in (["add", "."], ["commit", "-qm", "init"]):
                subprocess.run(  # noqa: S603
                    [self.git, "-C", str(repository), *arguments],
                    check=True,
                    env=environment,
                )
            (repository / "docs" / "a.md").write_text("changed", encoding="utf-8")

        plan = repository / "plan.md"
        plan.write_text("<!-- manifest\ndocs/**\n-->\n", encoding="utf-8")
        records = repository / ".claude" / ".claims" / "plans"
        records.mkdir(parents=True)
        (records / "abcdef.json").write_text(
            json.dumps({"plan": str(plan)}), encoding="utf-8"
        )
        return repository

    def _run(
        self, guard: Path, payload: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        """가드를 서브프로세스로 돌린다(프로덕션 hook 경로 그대로).

        🔴 `env`에 `GIT_*`가 없는 것이 이 헬퍼의 핵심이다 — 가드 자신이
        `git diff`를 돌리므로, 물려받은 `GIT_INDEX_FILE`이 있으면 임시 저장소가
        아니라 **바깥 저장소**를 보고 이 테스트가 재려는 축이 통째로 사라진다.
        """
        return subprocess.run(  # noqa: S603
            [sys.executable, str(guard)],
            input=json.dumps(payload),
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def _decision(self, result: subprocess.CompletedProcess[str]) -> dict[str, str]:
        """가드 출력에서 결정을 뽑는다. 무출력이면 통과다."""
        if not result.stdout.strip():
            return {}
        return json.loads(result.stdout)["hookSpecificOutput"]


if __name__ == "__main__":
    unittest.main()
