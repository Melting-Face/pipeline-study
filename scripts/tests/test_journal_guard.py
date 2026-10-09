"""저널 가드의 SessionStart 알림(다음 번호·열린 미션·WIP 상한)을 검증한다."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD = PROJECT_ROOT / "scripts" / "journal_guard.py"
KST = timezone(timedelta(hours=9))


class JournalGuardTest(unittest.TestCase):
    """임시 볼트로 실제 hook 입출력을 대조한다."""

    def setUp(self) -> None:
        """임시 볼트와 가드 실행 환경을 만든다."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.agents = Path(self.temporary_directory.name) / "agents"
        self.agents.mkdir()
        self.today = datetime.now(tz=KST)

        # 🔴 `GIT_*`를 걷어낸다 — git hook 안에서 돌면 부모 git의 인덱스 변수가
        #    자식에게 상속된다(Issue #54에서 드러난 결함, 가드가 git을 안 써도 유지).
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.environment.update(
            {
                "JOURNAL_RUNTIME": "claude-code",
                "OBSIDIAN_VAULT": str(self.agents.parent),
            }
        )

    def tearDown(self) -> None:
        """임시 볼트를 제거한다."""
        self.temporary_directory.cleanup()

    def _write_journal(self, days_ago: int, name: str, status: str) -> None:
        """`days_ago`일 전 폴더에 지정 status의 저널을 만든다."""
        day = (self.today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        day_dir = self.agents / day
        day_dir.mkdir(exist_ok=True)
        (day_dir / name).write_text(
            f"---\nmission: x\nstatus: {status}\n---\n", encoding="utf-8"
        )

    def _run_guard(self, command: str) -> subprocess.CompletedProcess[str]:
        """저널 가드에 빈 hook JSON을 전달한다."""
        return subprocess.run(  # noqa: S603
            [sys.executable, str(GUARD), command],
            input=json.dumps({}),
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_session_start_prints_next_number(self) -> None:
        """오늘 01이 있으면 다음 번호 02를 알린다."""
        self._write_journal(0, "01-first.md", "done")
        result = self._run_guard("session-start")
        assert result.returncode == 0, result.stderr
        assert "**02**" in result.stdout

    def test_wip_warning_when_open_exceeds_limit(self) -> None:
        """열린 미션이 상한(3)을 넘으면 경고 행을 낸다."""
        for index in range(1, 5):
            self._write_journal(0, f"0{index}-m{index}.md", "in-progress")
        result = self._run_guard("session-start")
        assert "WIP 4/3" in result.stdout, result.stdout

    def test_no_wip_warning_at_limit(self) -> None:
        """열린 미션이 정확히 상한이면 경고하지 않는다."""
        for index in range(1, 4):
            self._write_journal(0, f"0{index}-m{index}.md", "blocked")
        self._write_journal(0, "04-closed.md", "done")
        result = self._run_guard("session-start")
        assert result.returncode == 0, result.stderr
        assert "WIP" not in result.stdout, result.stdout

    def test_wip_counts_missions_beyond_recent_window(self) -> None:
        """표시 창(최근 7일) 밖의 열린 미션도 WIP에 센다."""
        self._write_journal(30, "01-old.md", "in-progress")
        for index in range(1, 4):
            self._write_journal(0, f"0{index}-m{index}.md", "planned")
        result = self._run_guard("session-start")
        assert "WIP 4/3" in result.stdout, result.stdout

    def test_removed_modes_are_noop(self) -> None:
        """갱신 전 배선으로 떠 있는 세션을 깨지 않도록 옛 모드는 무출력 통과한다."""
        for command in ("pre-write", "stop"):
            with self.subTest(command=command):
                result = self._run_guard(command)
                assert result.returncode == 0, result.stderr
                assert result.stdout == "", result.stdout


if __name__ == "__main__":
    unittest.main()
