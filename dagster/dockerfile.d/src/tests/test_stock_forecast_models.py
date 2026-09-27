"""주식예측 silver 모델의 **구조 불변식**을 검사한다(SQL을 텍스트로 본다).

🔴 여기서 막는 것은 값이 아니라 **누수를 만들 수 있는 문법의 위치**다.
`feature_store_daily`가 "라벨이 없다"는 것은 지금 주석과 모델 이름으로만
보장되는데, 주석은 집행 경로에 없다. `lead()` 한 줄이면 미래 종가가 피처로
들어오고 **dbt 테스트로는 잡히지 않는다** — 값이 다 채워져 있고 grain도 맞고
관계도 성립하기 때문이다. 그 축을 여기서 닫는다.

⚠️ **이것은 파싱이 아니라 문자열 검사다.** `lead (` 처럼 띄우거나 대문자로 쓰면
피할 수 있고, 다른 미래 참조 함수(`lag`의 음수 offset 등)는 보지 않는다.
봉쇄가 아니라 **실수 방지**이며, 그래서 이 파일이 무엇을 못 보는지 여기 적는다.
정본 방어는 모델을 나눈 구조 자체다.
"""

import re
from pathlib import Path

import pytest

MODELS_DIR = (
    Path(__file__).resolve().parents[1]
    / "dbt_pipelines"
    / "models"
    / "stock_forecast"
    / "tables"
)

# 🔴 미래를 보는 윈도우 함수. 이 목록에 있는 것이 라벨 모델 밖에 나오면 실패한다.
FORWARD_LOOKING = ("lead(",)

# `lead()`가 허용되는 유일한 모델.
LABEL_MODEL = "labels_forward_return.sql"


def _sql_files() -> list[Path]:
    """silver 모델 SQL 파일 목록."""
    return sorted(MODELS_DIR.glob("*.sql"))


def test_models_directory_is_not_empty() -> None:
    """🔴 대조군 — 검사 대상이 실재하는지 먼저 센다.

    경로가 틀리면 아래 테스트들이 **0개를 검사하고 통과**한다. 그 초록은
    "위반이 없다"가 아니라 "아무것도 안 봤다"이고 둘은 모양이 같다.
    """
    files = _sql_files()

    assert len(files) >= 5, f"모델이 너무 적다({len(files)}) — 경로 확인: {MODELS_DIR}"
    assert (MODELS_DIR / LABEL_MODEL).exists()


@pytest.mark.parametrize("path", _sql_files(), ids=lambda p: p.name)
def test_only_the_label_model_looks_forward(path: Path) -> None:
    """`lead()`는 라벨 모델에만 있어야 한다.

    🔴 특히 `feature_store_daily`에 등장하면 미래 종가가 피처가 된다. 그 상태로
    학습하면 성능이 좋아지고, **어떤 dbt 테스트도 울지 않는다** — 값이 채워져
    있고 grain도 관계도 맞기 때문이다.
    """
    sql = path.read_text(encoding="utf-8").lower()
    # 주석 줄은 제외한다 — 독스트링에서 함수 이름을 언급하는 것은 위반이 아니다.
    body = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    found = [token for token in FORWARD_LOOKING if token in body]

    if path.name == LABEL_MODEL:
        assert found, f"{path.name}에 미래 참조가 없다 — 검사가 무의미하다"
    else:
        assert not found, (
            f"{path.name}에 미래 참조 {found}가 있다. "
            "피처와 라벨은 테이블로 갈라야 한다(주석이 아니라 구조로)."
        )


def test_label_model_is_tagged_as_label() -> None:
    """라벨 모델은 `label` 태그를 달아 선택에서도 갈린다.

    태그가 없으면 `fqn:stock_forecast` 한 번에 피처와 라벨이 함께 선택되고,
    "라벨만 빼고 빌드"가 불가능해진다.
    """
    sql = (MODELS_DIR / LABEL_MODEL).read_text(encoding="utf-8")
    config_line = sql.splitlines()[0]

    assert re.search(r"tags\s*=\s*\[[^\]]*'label'", config_line), (
        f"라벨 모델의 첫 줄에 label 태그가 없다: {config_line}"
    )
