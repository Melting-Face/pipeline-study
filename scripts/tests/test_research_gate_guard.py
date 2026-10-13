"""조사 게이트(`research_gate_guard.py`)의 현행 판정을 고정하는 특성 테스트다.

왜 이 파일인가:
    이 가드는 `researcher`의 외부 반입을 막는 **유일한 층**(fail-closed)인데
    단위 테스트가 0건이었다. 리팩토링 전에 **지금 무엇을 하는지**부터 박는다 —
    기대값의 근거는 가드의
    docstring·주석 명세다. 가드 본체는 고치지 않는다(결함이 드러나면 Issue로).

🔴 음성 대조를 함께 둔다:
    `deny`만 확인하는 셀은 가드가 **전부 막아도** 초록이다. 승인된 URL이 실제로
    통과하는 셀이 있어야 「막혔다」가 「검사했다」로 읽힌다(철학 원칙 7).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.research_gate_guard import format_problems, matches, normalize

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD = PROJECT_ROOT / "scripts" / "research_gate_guard.py"


class ResearchGateGuardTest(unittest.TestCase):
    """임시 `CLAUDE_PROJECT_DIR`로 실제 hook 입출력을 대조한다."""

    def setUp(self) -> None:
        """임시 프로젝트 디렉터리와 가드 실행 환경을 만든다."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary_directory.name)
        self.research = self.project / ".claude" / ".research"

        # 🔴 `GIT_*`를 걷어낸다 — git hook 안에서 돌면 부모 git의 변수가 상속된다
        #    (`test_journal_guard.py`와 같은 처방).
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.environment["CLAUDE_PROJECT_DIR"] = str(self.project)

    def tearDown(self) -> None:
        """임시 프로젝트 디렉터리를 제거한다."""
        self.temporary_directory.cleanup()

    def _approve(self, *entries: str) -> None:
        """승인 매니페스트를 쓴다."""
        self.research.mkdir(parents=True, exist_ok=True)
        (self.research / "approved.json").write_text(
            json.dumps({"approved": list(entries)}), encoding="utf-8"
        )

    def _run_raw(self, stdin: str) -> subprocess.CompletedProcess[str]:
        """가드에 원문 stdin을 그대로 넘긴다."""
        return subprocess.run(  # noqa: S603
            [sys.executable, str(GUARD)],
            input=stdin,
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def _run(self, tool: str, tool_input: dict) -> subprocess.CompletedProcess[str]:
        """hook 페이로드를 만들어 가드를 실행한다."""
        payload = {"tool_name": tool, "tool_input": tool_input, "session_id": "t"}
        return self._run_raw(json.dumps(payload))

    def _assert_denied(self, result: subprocess.CompletedProcess[str]) -> str:
        """`deny` JSON이 나왔는지 확인하고 사유를 돌려준다."""
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
        return output["permissionDecisionReason"]

    def _assert_passed(self, result: subprocess.CompletedProcess[str]) -> None:
        """통과(= exit 0 + 무출력)인지 확인한다."""
        assert result.returncode == 0, result.stderr
        assert result.stdout == "", result.stdout

    def _log(self) -> list[dict]:
        """질의 로그를 읽는다."""
        path = self.research / "queries.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_unreadable_payload_is_denied_and_logged(self) -> None:
        """입력 파손은 fail-closed — 통과시키면 게이트가 조용히 사라진다."""
        reason = self._assert_denied(self._run_raw("{not json"))
        assert "fail-closed" in reason
        assert self._log()[-1]["event"] == "payload_unreadable"

    def test_other_tool_passes_silently(self) -> None:
        """소관 밖 도구는 판정하지 않는다(로그도 남기지 않는다)."""
        self._assert_passed(self._run("Read", {"file_path": "/x"}))
        assert not (self.research / "queries.jsonl").exists()

    def test_web_search_passes_and_is_logged(self) -> None:
        """검색은 통과시키되 질의문을 사후 관측점으로 남긴다."""
        self._assert_passed(self._run("WebSearch", {"query": "dbt dispatch"}))
        entry = self._log()[-1]
        assert entry["decision"] == "allow"
        assert entry["value"] == "dbt dispatch"

    def test_web_search_key_drift_passes_but_is_logged(self) -> None:
        """검색은 키가 없어도 통과하지만 `key_missing`이 로그에 남는다."""
        self._assert_passed(self._run("WebSearch", {"q": "x"}))
        entry = self._log()[-1]
        assert entry["why"] == "key_missing"
        assert entry["input_keys"] == ["q"]

    def test_web_fetch_key_drift_is_denied(self) -> None:
        """페치는 키가 없으면 통과가 아니라 `deny`다(키 드리프트를 시끄럽게)."""
        self._approve("https://example.com")
        reason = self._assert_denied(self._run("WebFetch", {"href": "x"}))
        assert "`url`" in reason

    def test_fetch_without_manifest_is_denied(self) -> None:
        """매니페스트가 없으면(`none`) 후보 목록을 반환하라고 안내한다."""
        reason = self._assert_denied(
            self._run("WebFetch", {"url": "https://example.com/a"})
        )
        assert "아직 승인된 목록이 없다" in reason
        assert self._log()[-1]["why"] == "none"

    def test_broken_manifest_is_denied(self) -> None:
        """매니페스트 파손이 게이트를 여는 수단이면 안 된다."""
        self.research.mkdir(parents=True)
        (self.research / "approved.json").write_text("{", encoding="utf-8")
        reason = self._assert_denied(
            self._run("WebFetch", {"url": "https://example.com/a"})
        )
        assert "JSON 파손" in reason
        assert self._log()[-1]["why"] == "broken"

    def test_unapproved_url_is_denied(self) -> None:
        """승인 목록 밖 URL은 델타 승인을 안내하며 막는다."""
        self._approve("https://example.com/a")
        reason = self._assert_denied(
            self._run("WebFetch", {"url": "https://example.com/b"})
        )
        assert "승인 목록에 없다(현재 1건 승인됨)" in reason
        assert self._log()[-1]["why"] == "ok"

    def test_approved_url_passes(self) -> None:
        """음성 대조 — 승인된 URL은 실제로 통과한다(전부 막아도 초록인 것 방지)."""
        self._approve("https://example.com/a")
        self._assert_passed(
            self._run("WebFetch", {"url": "https://www.example.com/a/?utm_source=x"})
        )
        assert self._log()[-1]["decision"] == "allow"

    def test_bare_list_manifest_is_accepted(self) -> None:
        """매니페스트는 `{"approved": [...]}` 외에 맨 배열도 받는다."""
        self.research.mkdir(parents=True)
        (self.research / "approved.json").write_text(
            json.dumps(["https://example.com/a"]), encoding="utf-8"
        )
        self._assert_passed(self._run("WebFetch", {"url": "https://example.com/a"}))

    def test_malformed_entries_are_named_in_deny_reason(self) -> None:
        """「승인했는데 막힌다」의 원인이 항목 형식이면 거부 사유에 드러난다(#206)."""
        self._approve("example.com/Docs/Page", "https://example.com/ref/*")
        reason = self._assert_denied(
            self._run("WebFetch", {"url": "https://example.com/Docs/Page"})
        )
        assert "형식 오류 항목 2건" in reason
        assert "`example.com/Docs/Page`(`https://` 누락" in reason
        assert "`https://example.com/ref/*`(`/*` 대신 `*`로 끝낸다)" in reason

    def test_unparsable_fetch_url_is_denied_not_crashed(self) -> None:
        """페치 URL이 `urlsplit`을 터뜨려도 크래시가 아니라 `deny`다(#207)."""
        self._approve("https://example.com/a")
        reason = self._assert_denied(self._run("WebFetch", {"url": "http://["}))
        assert "내부 오류" in reason
        assert "ValueError" in reason

    def test_unparsable_manifest_entry_is_denied_not_crashed(self) -> None:
        """매니페스트 항목이 `urlsplit`을 터뜨려도 `deny`다(#207)."""
        self._approve("http://[")
        reason = self._assert_denied(
            self._run("WebFetch", {"url": "https://example.com/a"})
        )
        assert "내부 오류" in reason

    def test_non_dict_payload_is_denied_not_crashed(self) -> None:
        """payload·`tool_input`이 dict가 아니어도 `deny`다(#207 재검토)."""
        reason = self._assert_denied(self._run_raw("[1]"))
        assert "AttributeError" in reason
        reason = self._assert_denied(
            self._run_raw(json.dumps({"tool_name": "WebFetch", "tool_input": "x"}))
        )
        assert "AttributeError" in reason

    def test_well_formed_entries_add_no_format_notice(self) -> None:
        """음성 대조 — 형식이 맞는 항목만 있으면 형식 오류 문구가 붙지 않는다."""
        self._approve("https://example.com/a", "https://example.com/ref*")
        reason = self._assert_denied(
            self._run("WebFetch", {"url": "https://example.com/b"})
        )
        assert "형식 오류" not in reason


class MatchesTest(unittest.TestCase):
    """승인 항목 3형태(완전일치·경로 접두어·호스트 전체)의 경계를 고정한다."""

    def test_exact_match_ignores_scheme_www_query_and_trailing_slash(self) -> None:
        """같은 문서의 표기 변형은 같은 URL로 본다."""
        target = normalize("http://www.Example.com/a/?x=1#f")
        assert matches(target, "https://example.com/a")

    def test_exact_entry_does_not_open_subpaths(self) -> None:
        """완전일치 항목은 하위 경로를 열지 않는다."""
        assert not matches(
            normalize("https://example.com/a/b"), "https://example.com/a"
        )

    def test_host_entry_opens_whole_host_only(self) -> None:
        """호스트 항목은 그 호스트 전체를 열고, 접두어만 같은 다른 호스트는 막는다."""
        assert matches(normalize("https://example.com/a/b"), "example.com")
        assert not matches(normalize("https://example.com.evil.io/a"), "example.com")

    def test_star_entry_opens_prefix(self) -> None:
        """`*` 항목은 경로 접두어를 연다."""
        entry = "https://example.com/reference*"
        assert matches(normalize("https://example.com/reference/x"), entry)
        assert matches(normalize("https://example.com/reference"), entry)

    def test_slash_star_entry_excludes_directory_itself(self) -> None:
        """현행 동작 고정 — `/*`로 끝내면 디렉터리 자신은 안 열린다(하위만)."""
        entry = "https://example.com/reference/*"
        assert matches(normalize("https://example.com/reference/x"), entry)
        assert not matches(normalize("https://example.com/reference"), entry)

    def test_schemeless_entry_lowercases_path(self) -> None:
        """현행 동작 고정 — 스킴 없는 항목은 경로까지 소문자화돼 대문자와 갈린다."""
        target = normalize("https://example.com/Docs/Page")
        assert not matches(target, "example.com/Docs/Page")
        assert matches(target, "https://example.com/Docs/Page")

    def test_format_problems_flags_only_the_two_forms(self) -> None:
        """형식 검사는 스킴 누락·`/*`만 잡고 규약대로 쓴 3형태는 통과시킨다."""
        assert (
            format_problems(
                [
                    "https://example.com/a",
                    "https://example.com/ref*",
                    "https://example.com",
                ]
            )
            == []
        )
        assert len(format_problems(["Example.com/R", "https://example.com/r/*"])) == 2

    def test_schemeless_entry_flagged_only_when_it_actually_mismatches(self) -> None:
        """스킴 누락은 실제로 어긋날 때만 지적한다 — 거짓 라벨 방지(#206 리뷰 R1)."""
        # 소문자 호스트·경로만 적은 항목은 매칭되므로 지적하지 않는다
        assert matches(normalize("https://example.com/a/b"), "example.com")
        assert format_problems(["example.com", "example.com/ref/*"]) == [
            "`example.com/ref/*`(`/*` 대신 `*`로 끝낸다)"
        ]
        # 대문자 경로·`www.`는 정규화가 갈려 실제로 어긋난다
        assert len(format_problems(["example.com/Docs"])) == 1
        assert len(format_problems(["www.example.com"])) == 1


if __name__ == "__main__":
    unittest.main()
