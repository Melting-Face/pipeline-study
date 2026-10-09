"""hook 배선이 **조용히 통과로 새는 경로**를 막는다(Issue #55).

왜 이 파일인가:
    이 저장소의 hook은 통과를 **`exit 0` + 무출력**으로 표현한다. 그래서 스크립트가
    **죽거나 없어도** 하네스가 보는 것은 「결정 없음」으로 통과와 **같다** — 통과와
    고장이 관측상 구분되지 않는다. 아래는 그 두 경로(배선 대상 부재·타임아웃 미처리)를
    정적으로 고정한다.
"""

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEX_RELAYS = (PROJECT_ROOT / ".codex" / "hooks" / "session_start.py",)
# hook `command` 문자열 안의 저장소 상대 스크립트 경로
WIRED_SCRIPT_RE = re.compile(r"(?:scripts|\.codex/hooks)/[A-Za-z0-9_]+\.py")


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
            for reference in WIRED_SCRIPT_RE.findall(source.read_text(encoding="utf-8"))
        ]
        assert len(wired) >= 4, f"배선 참조 추출이 {len(wired)}건 — 추출이 죽었다"
        missing = sorted({ref for ref in wired if not (PROJECT_ROOT / ref).is_file()})
        assert not missing, f"배선은 있는데 스크립트가 없다: {missing}"


if __name__ == "__main__":
    unittest.main()
