"""🔴 프로브 ↔ 파서 필드 계약 대조(시세 bar).

Dagster `tests/test_polygon_market_parse.py`의
`test_probe_and_parser_share_the_field_contract` 중 bar 쪽을 옮겼다 — 시세 적재가
Airflow로 이관돼 파서 상수의 정본이 `lakehouse.polygon`이 됐다(뉴스 쪽은 Dagster에
남는다).

프로브(`scripts/stock_source_access_probe.py`)는 PEP 723 단독 실행 스크립트라 패키지를
import할 수 없고, 반대로 테스트가 프로브를 import하면 의존성(`requests`)과 모듈 부작용을
끌어온다. 그래서 실행하지 않고 **AST로 값만** 꺼낸다.
"""

import ast
from pathlib import Path

from lakehouse.polygon import KNOWN_BAR_FIELDS, REQUIRED_BAR_FIELDS

# tests/lib/ → tests/ → airflow/ → images/ → 저장소 루트 순으로 네 단계 올라간다.
PROBE_PATH = (
    Path(__file__).resolve().parents[4] / "scripts" / "stock_source_access_probe.py"
)


def _probe_tuple(name: str) -> tuple[str, ...]:
    """프로브의 모듈 스코프 문자열 튜플 상수를 읽는다(이름이 바뀌면 여기서 멈춘다)."""
    assert PROBE_PATH.exists(), f"프로브를 못 찾았다: {PROBE_PATH}"
    tree = ast.parse(PROBE_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name in targets:
            return tuple(ast.literal_eval(node.value))
    message = f"프로브에 {name} 상수가 없다 — 이름이 바뀌었는지 확인한다"
    raise AssertionError(message)


def test_probe_and_parser_share_the_bar_field_contract() -> None:
    """프로브와 파서가 **같은 bar 필드 집합**을 본다.

    🔴 두 벌이 조용히 갈라지면 **프로브는 통과하고 파서만 깨진다** — 프로브의 초록이
    파서의 안전을 보증하지 못하게 된다. 그래서 갈라짐 자체를 실패로 만든다.
    깨보려면 어느 한쪽 상수에서 필드를 하나 지우면 된다.
    """
    assert _probe_tuple("POLYGON_BAR_FIELDS") == REQUIRED_BAR_FIELDS
    # "아는 전부" 집합도 묶는다 — 갈라지면 프로브의 드리프트 경고가 파서의 실제
    # 처리와 다른 것을 기준으로 삼는다.
    assert _probe_tuple("POLYGON_BAR_KNOWN_FIELDS") == KNOWN_BAR_FIELDS
