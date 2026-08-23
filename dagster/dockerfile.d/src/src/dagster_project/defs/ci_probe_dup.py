"""`dg check defs` 게이트 실발동 확인용 일회성 프로브 — 다음 커밋에서 제거한다.

원칙 7: 새로 건 게이트는 **일부러 위반시켜** 막히는지 본다.

이 모듈은 `defs/mimic_iv/assets.py`가 이미 정의한 자산 키 `icustays`를
**중복** 선언한다. `load_defs`가 두 정의를 합칠 때 키가 충돌해 정의 로드
자체가 실패해야 하고, 따라서 `dg check defs` 스텝이 빨간불이어야 한다.

겨냥한 축만 걸리도록 설계했다:
- 완전히 타입이 붙어 있어 mypy(`disallow_untyped_defs = true`)를 통과한다
  → `defs` 잡이 **mypy 스텝이 아니라 `dg check defs` 스텝에서** 죽어야 한다.
- 미사용 import·누락 docstring이 없어 ruff를 통과한다 → `lint` 잡은 초록(대조군).
"""

import dagster as dg


@dg.asset(name="icustays", group_name="mimic_iv")
def ci_probe_duplicate_key() -> None:
    """기존 `icustays` 자산과 키를 고의로 충돌시킨다(프로브)."""
