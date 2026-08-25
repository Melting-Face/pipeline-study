"""Claude Code·Codex 공용 저널 가드의 런타임 분리와 Stop 보정을 검증한다."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.plan_mirror_guard import compose

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD = PROJECT_ROOT / "scripts" / "journal_guard.py"
CLAUDE_PATH_GUARD = PROJECT_ROOT / "scripts" / "worker_path_guard.py"
CODEX_PATH_GUARD = PROJECT_ROOT / ".codex" / "hooks" / "worker_path_guard.py"
KST = timezone(timedelta(hours=9))


class JournalGuardTest(unittest.TestCase):
    """임시 Git 저장소와 볼트로 실제 hook 입출력을 대조한다."""

    def setUp(self) -> None:
        """Codex 세션 기준점이 Git 상태에 섞이지 않는 테스트 환경을 만든다."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.vault = self.root / "vault"
        (self.vault / "agents").mkdir(parents=True)
        self.repository.mkdir()
        (self.repository / ".gitignore").write_text(
            ".codex/.claims\n", encoding="utf-8"
        )

        git = shutil.which("git")
        if git is None:
            self.skipTest("git 실행 파일이 필요하다")
        self.git = git
        self._run_git("init", "--quiet")
        self._run_git("config", "user.name", "Journal Guard Test")
        self._run_git("config", "user.email", "journal-guard@example.invalid")
        self._run_git("add", ".gitignore")
        self._run_git("commit", "--quiet", "-m", "test: 기준점")

        self.session_id = "11111111-2222-3333-4444-555555555555"
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "CLAUDE_PROJECT_DIR": str(self.repository),
                "CODEX_SESSION_ID": self.session_id,
                "JOURNAL_RUNTIME": "codex",
                "OBSIDIAN_VAULT": str(self.vault),
            }
        )

    def tearDown(self) -> None:
        """임시 저장소와 볼트를 제거한다."""
        self.temporary_directory.cleanup()

    def _run_git(self, *arguments: str) -> None:
        """테스트 저장소에서 Git 명령을 실행한다."""
        subprocess.run(  # noqa: S603
            [self.git, "-C", str(self.repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    def _run_guard(
        self, command: str, payload: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        """저널 가드에 실제 hook JSON을 전달한다."""
        return subprocess.run(  # noqa: S603
            [sys.executable, str(GUARD), command],
            input=json.dumps(payload),
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def _run_path_guard(
        self, guard: Path, payload: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        """archivist 경로 가드에 실제 hook JSON을 전달한다."""
        return subprocess.run(  # noqa: S603
            [sys.executable, str(guard), "archivist"],
            input=json.dumps(payload),
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_pre_write_enforces_runtime_namespace(self) -> None:
        """Codex 신규 저널은 Codex 날짜 경로만 허용한다."""
        today = datetime.now(tz=KST).strftime("%Y-%m-%d")
        valid = self.vault / "agents" / "codex" / today / "01-valid.md"
        legacy = self.vault / "agents" / today / "01-legacy.md"

        valid_result = self._run_guard(
            "pre-write", {"tool_input": {"file_path": str(valid)}}
        )
        legacy_result = self._run_guard(
            "pre-write", {"tool_input": {"file_path": str(legacy)}}
        )

        assert valid_result.returncode == 0
        assert valid_result.stdout == ""
        denial = json.loads(legacy_result.stdout)
        assert denial["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "agents/codex" in legacy_result.stdout

    def test_codex_stop_blocks_once_until_journal_is_closed(self) -> None:
        """변경 세션은 미기록 시 한 번만 이어지고 마감 저널이 있으면 통과한다."""
        payload = {"cwd": str(self.repository), "session_id": self.session_id}
        start = self._run_guard("session-start", payload)
        unchanged = self._run_guard("stop", payload)

        assert start.returncode == 0
        assert "runtime/codex" in start.stdout
        assert unchanged.stdout == ""

        (self.repository / "changed.txt").write_text("변경\n", encoding="utf-8")
        first_stop = self._run_guard("stop", payload)
        second_stop = self._run_guard("stop", {**payload, "stop_hook_active": True})

        first_output = json.loads(first_stop.stdout)
        second_output = json.loads(second_stop.stdout)
        assert first_output["decision"] == "block"
        assert "systemMessage" in second_output
        assert "decision" not in second_output

        today = datetime.now(tz=KST).strftime("%Y-%m-%d")
        journal = self.vault / "agents" / "codex" / today / "01-closed.md"
        journal.parent.mkdir(parents=True)
        journal.write_text(
            "---\n"
            "status: done\n"
            f"session_id: {self.session_id}\n"
            f"updated: {today}T00:00+09:00\n"
            "---\n",
            encoding="utf-8",
        )

        closed = self._run_guard("stop", payload)
        assert closed.stdout == ""

    def test_codex_stop_detects_edits_to_already_dirty_file(self) -> None:
        """세션 시작 전부터 수정된 파일의 추가 편집도 diff 해시로 감지한다."""
        tracked = self.repository / "tracked.txt"
        tracked.write_text("원본\n", encoding="utf-8")
        self._run_git("add", "tracked.txt")
        self._run_git("commit", "--quiet", "-m", "test: 추적 파일")
        tracked.write_text("시작\n", encoding="utf-8")

        payload = {"cwd": str(self.repository), "session_id": self.session_id}
        self._run_guard("session-start", payload)
        tracked.write_text("종료\n", encoding="utf-8")

        stopped = self._run_guard("stop", payload)
        assert json.loads(stopped.stdout)["decision"] == "block"

    def test_plan_mirror_uses_unambiguous_journal_link(self) -> None:
        """런타임별 동명 노트를 피하도록 계획서 백링크에 전체 경로를 쓴다."""
        mirrored = compose("# 계획\n", "2026-08-26/01-runtime-separation")

        expected = "[[agents/claude-code/2026-08-26/01-runtime-separation]]"
        assert expected in mirrored

    def test_archivist_write_boundaries_are_runtime_specific(self) -> None:
        """각 archivist는 자기 런타임 경로만 무승인으로 쓸 수 있다."""
        codex_path = self.vault / "agents" / "codex" / "2026-08-26" / "01-a.md"
        claude_path = self.vault / "agents" / "claude-code" / "2026-08-26" / "01-a.md"

        codex_allowed = self._run_path_guard(
            CODEX_PATH_GUARD,
            {
                "cwd": str(self.repository),
                "tool_input": {"command": f"*** Add File: {codex_path}\n"},
            },
        )
        codex_denied = self._run_path_guard(
            CODEX_PATH_GUARD,
            {
                "cwd": str(self.repository),
                "tool_input": {"command": f"*** Add File: {claude_path}\n"},
            },
        )
        claude_allowed = self._run_path_guard(
            CLAUDE_PATH_GUARD,
            {"tool_input": {"file_path": str(claude_path)}},
        )
        claude_asks = self._run_path_guard(
            CLAUDE_PATH_GUARD,
            {"tool_input": {"file_path": str(codex_path)}},
        )

        assert codex_allowed.stdout == ""
        assert (
            json.loads(codex_denied.stdout)["hookSpecificOutput"]["permissionDecision"]
            == "deny"
        )
        assert claude_allowed.stdout == ""
        assert (
            json.loads(claude_asks.stdout)["hookSpecificOutput"]["permissionDecision"]
            == "ask"
        )


if __name__ == "__main__":
    unittest.main()
