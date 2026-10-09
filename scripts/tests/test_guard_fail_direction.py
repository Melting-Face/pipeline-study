"""hook 배선이 **조용히 통과로 새는 경로**를 막는다(Issue #55).

왜 이 파일인가:
    이 저장소의 hook은 통과를 **`exit 0` + 무출력**으로 표현한다. 그래서 스크립트가
    **죽거나 없어도** 하네스가 보는 것은 「결정 없음」으로 통과와 **같다** — 통과와
    고장이 관측상 구분되지 않는다. 아래는 그 두 경로(배선 대상 부재·타임아웃 미처리)를
    정적으로 고정한다.
"""

import os
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEX_RELAYS = (PROJECT_ROOT / ".codex" / "hooks" / "session_start.py",)
# hook `command` 문자열 안의 저장소 상대 스크립트 경로(하이픈·하위 경로·`.sh` 포함)
WIRED_SCRIPT_RE = re.compile(r"(?:scripts|\.codex/hooks)/[\w./-]+?\.(?:py|sh)\b")
# 같은 줄에서 경로 앞에 이것이 있으면 인터프리터 경유라 실행 비트가 필요 없다
INTERPRETER_RE = re.compile(r"(?:^|[\s\"'])(?:python3?|uv run|bash|sh)\s")


HOOK_COMMAND_LINE_RE = re.compile(r"(?:\"command\"|^\s*command):")


def hook_command_text(text: str) -> str:
    """hook `command` 줄만 남긴다 — permission 규칙 문자열은 배선이 아니다."""
    return "\n".join(
        line for line in text.splitlines() if HOOK_COMMAND_LINE_RE.search(line)
    )


def wired_references(text: str) -> list[tuple[str, bool]]:
    """`text`에서 (저장소 상대 스크립트 경로, 직접 실행 여부)를 뽑는다.

    직접 실행 = 같은 줄의 경로 앞부분에 인터프리터 토큰이 없는 경우다. 바로 앞 토큰만
    보면 `python3 "$(git rev-parse --show-toplevel)/…"`처럼 경로가 치환식에 붙은 형태를
    놓친다. 직접 실행인데 실행 비트가 없으면 hook이 에러를 낸다.
    """
    references = []
    for match in WIRED_SCRIPT_RE.finditer(text):
        line_prefix = text[: match.start()].rsplit("\n", 1)[-1]
        references.append((match.group(0), not INTERPRETER_RE.search(line_prefix)))
    return references


class WiredReferencesTest(unittest.TestCase):
    """배선 참조 추출을 합성 입력으로 고정한다(M-1: 하이픈·하위 경로·`.sh`)."""

    def test_extracts_hyphen_subdir_and_shell(self) -> None:
        """하이픈 이름·하위 디렉터리·`.sh`도 참조로 뽑는다."""
        paths = [
            path
            for path, _ in wired_references(
                "scripts/foo-bar.py scripts/sub/x.py scripts/k8s-up.sh"
            )
        ]
        assert paths == ["scripts/foo-bar.py", "scripts/sub/x.py", "scripts/k8s-up.sh"]

    def test_direct_invocation_flag(self) -> None:
        """인터프리터 없이 경로로 시작하면 직접 실행, 인터프리터 경유면 아니다."""
        assert wired_references('"$CLAUDE_PROJECT_DIR"/scripts/k8s-up.sh arg') == [
            ("scripts/k8s-up.sh", True)
        ]
        assert wired_references("python3 scripts/a.py") == [("scripts/a.py", False)]
        assert wired_references("uv run scripts/a.py") == [("scripts/a.py", False)]
        codex_form = 'python3 \\"$(git rev-parse --show-toplevel)/.codex/hooks/x.py\\"'
        assert wired_references(codex_form) == [(".codex/hooks/x.py", False)]

    def test_only_hook_command_lines_are_scanned(self) -> None:
        """permission 규칙 문자열은 배선이 아니다 — hook `command` 줄만 남긴다."""
        settings_like = (
            '"Bash(*scripts/k8s-down.sh*)",\n'
            '"command": "\\"$CLAUDE_PROJECT_DIR\\"/scripts/a_guard.py x",\n'
        )
        frontmatter_like = '  command: "$CLAUDE_PROJECT_DIR/scripts/b_guard.py"\n'
        settings_refs = wired_references(hook_command_text(settings_like))
        frontmatter_refs = wired_references(hook_command_text(frontmatter_like))
        assert [path for path, _ in settings_refs] == ["scripts/a_guard.py"]
        assert [path for path, _ in frontmatter_refs] == ["scripts/b_guard.py"]

    def test_ignores_non_script_files(self) -> None:
        """`.md` 등 스크립트가 아닌 경로는 뽑지 않는다."""
        assert wired_references("scripts/README.md") == []


class GuardFailDirectionTest(unittest.TestCase):
    """판정 불가가 통과로 읽히는 배선을 찾는다."""

    def test_codex_relays_handle_a_guard_timeout(self) -> None:
        """Codex 중계 hook이 `TimeoutExpired`를 **잡는다**.

        ⚠️ **이 검사가 보증하는 것은 핸들러의 존재까지다.** 실제 타임아웃을 재지 않는
        이유는 **하나에 8초**라 커밋 게이트에 올릴 수 없기 때문이다.
        """
        for relay in CODEX_RELAYS:
            text = relay.read_text(encoding="utf-8")
            assert "subprocess.run(" in text, f"{relay.name}: 대조군이 죽었다"
            assert re.search(r"except\s+subprocess\.TimeoutExpired", text), (
                f"{relay.name}: 타임아웃을 안 잡으면 traceback + 무출력이 되고 "
                "하네스는 그것을 통과로 읽는다"
            )

    def test_all_wired_hook_scripts_exist(self) -> None:
        """배선이 가리키는 hook 스크립트가 **전부 실재**한다.

        스크립트를 지우고 배선을 남기면 hook이 매 호출 에러를 내거나, 하네스가
        그것을 「결정 없음」으로 읽어 **조용히 통과**한다(fail-open).
        대상은 settings·워커 프론트매터·Codex hooks.json 세 곳의 `command`다.

        🔴 대조군: 추출한 **배선 참조 수**(고유 스크립트 수가 아니다)가 4 미만이면
        실패한다 — 추출 정규식이 깨져 0건이 되면 아래 판정이 통과로 끝나기 때문이다.
        """
        sources = [
            PROJECT_ROOT / ".claude" / "settings.json",
            PROJECT_ROOT / ".codex" / "hooks.json",
            *sorted((PROJECT_ROOT / ".claude" / "agents").glob("*.md")),
        ]
        wired = [
            reference
            for source in sources
            for reference in wired_references(
                hook_command_text(source.read_text(encoding="utf-8"))
            )
        ]
        assert len(wired) >= 4, f"배선 참조 추출이 {len(wired)}건 — 추출이 죽었다"
        missing = sorted(
            {ref for ref, _ in wired if not (PROJECT_ROOT / ref).is_file()}
        )
        assert not missing, f"배선은 있는데 스크립트가 없다: {missing}"
        not_executable = sorted(
            {
                ref
                for ref, direct in wired
                if direct and not os.access(PROJECT_ROOT / ref, os.X_OK)
            }
        )
        assert not not_executable, (
            f"직접 실행 배선인데 실행 비트가 없다: {not_executable} — "
            "hook이 에러를 내고 하네스는 그것을 통과로 읽는다"
        )


if __name__ == "__main__":
    unittest.main()
