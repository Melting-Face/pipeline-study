"""파티션 적재 멱등성 테스트 — `replace_partition_in_iceberg`.

**실인프라에 붙지 않는다.** pyiceberg의 SQLite 카탈로그 + `tmp_path` warehouse로
실제 Iceberg 테이블을 만들어 검증한다 — SeaweedFS·CNPG·K8s 어디에도 접속하지
않으므로 `docs/test.md`의 격리 원칙에 어긋나지 않는다(`common.constants`가
모듈 로드 시점에 읽는 env는 `conftest.py`가 더미로 채운다).

파서 테스트(`test_frankfurter_fx_parse.py`)가 못 잡는 축을 잡는다 — 파서는 Arrow
테이블까지만 만들고, **재실행이 행을 늘리는지**는 적재 경로에서 갈린다.

🔴 음성 대조를 함께 둔다. "두 번 넣어도 4행"은 적재가 멱등이라는 뜻일 수도,
**테스트가 둔감하다는 뜻일 수도** 있다. 같은 데이터를 `append`로 넣으면 8행이
되는 것을 함께 확인해야 앞의 4행이 의미를 갖는다.
"""

from datetime import datetime, timezone

import pytest
from dagster_project.common.helper import replace_partition_in_iceberg
from dagster_project.defs.frankfurter_fx.assets import _rates_json_to_arrow
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import EqualTo

import dagster as dg

INGESTED_AT = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)
IDENTIFIER = "frankfurter_fx.fx_rates_daily"

# 🔴 환율 값은 **합성값**이다 — 원천 데이터를 저장소에 담지 않는다.
# 검증 대상은 값이 아니라 적재 모드(멱등·격리·계보)와 날짜 시프트 구조다.
TUESDAY = {
    "amount": 1.0,
    "base": "EUR",
    "date": "2026-09-01",
    "rates": {"KRW": 1500.0, "USD": 1.10, "JPY": 180.0, "GBP": 0.80},
}
SATURDAY = {
    "amount": 1.0,
    "base": "EUR",
    "date": "2026-09-04",
    "rates": {"KRW": 1450.0, "USD": 1.15, "JPY": 175.0, "GBP": 0.82},
}


class _FakeTableResource:
    """`IcebergTableResource` 대역 — 헬퍼가 쓰는 속성만 갖는다.

    실물은 `config.model_dump()["properties"]`로 카탈로그를 재구성하는데,
    여기서는 이미 만든 SQLite 카탈로그를 그대로 돌려주면 된다.
    """

    def __init__(self, catalog: SqlCatalog) -> None:
        self._catalog = catalog
        self.name = "probe"
        self.schema_ = "frankfurter_fx"
        self.table = "fx_rates_daily"


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    cat = SqlCatalog(
        "probe",
        uri=f"sqlite:///{tmp_path / 'catalog.db'}",
        warehouse=f"file://{warehouse}",
    )
    # 헬퍼가 리소스 config로 카탈로그를 새로 여는 대신 이 인스턴스를 쓰게 한다.
    monkeypatch.setattr(
        "dagster_project.common.helper._load_iceberg_table",
        lambda _resource: (cat, IDENTIFIER),
    )
    return cat


def _load(catalog: SqlCatalog, payload: dict, rate_date: str) -> None:
    arrow = _rates_json_to_arrow(payload, rate_date, INGESTED_AT)
    replace_partition_in_iceberg(
        dg.build_asset_context(partition_key=rate_date),
        iceberg_table=_FakeTableResource(catalog),
        arrow=arrow,
        partition_column="rate_date",
        partition_value=rate_date,
    )


def _count(catalog: SqlCatalog, rate_date: str | None = None) -> int:
    table = catalog.load_table(IDENTIFIER)
    if rate_date is None:
        return table.scan().to_arrow().num_rows
    return table.scan(row_filter=EqualTo("rate_date", rate_date)).to_arrow().num_rows


def test_same_partition_twice_does_not_grow(catalog) -> None:
    """🔴 파티션 자산의 최소 요건 — 재실행이 행을 늘리지 않는다."""
    _load(catalog, TUESDAY, "2026-09-01")
    first = _count(catalog)
    _load(catalog, TUESDAY, "2026-09-01")

    assert first == 4
    assert _count(catalog) == first


def test_append_would_grow(catalog) -> None:
    """음성 대조 — append였다면 두 배가 된다.

    이 테스트가 없으면 위의 "4행 그대로"가 **멱등의 증거인지 테스트가 둔감한
    것인지** 구분되지 않는다.
    """
    arrow = _rates_json_to_arrow(TUESDAY, "2026-09-01", INGESTED_AT)
    from dagster_project.common.helper import ensure_table

    table = ensure_table(catalog, IDENTIFIER, arrow.schema)
    table.append(arrow)
    first = _count(catalog)

    catalog.load_table(IDENTIFIER).append(arrow)

    assert first == 4
    assert _count(catalog) == first * 2


def test_other_partitions_are_not_touched(catalog) -> None:
    """overwrite_filter가 범위를 가른다 — 한 파티션 재적재가 다른 것을 지우지 않는다."""
    _load(catalog, TUESDAY, "2026-09-01")
    _load(catalog, SATURDAY, "2026-09-05")
    _load(catalog, SATURDAY, "2026-09-05")

    assert _count(catalog) == 8
    assert _count(catalog, "2026-09-01") == 4
    assert _count(catalog, "2026-09-05") == 4


def test_snapshot_lineage_stays_single_rooted(catalog) -> None:
    """🔴 계보가 끊기지 않는다 — 행 수로는 안 보이는 축이다.

    `drop_table` 후 재생성이면 재생성본이 `parent_snapshot_id = None`인 새
    루트가 되어 루트가 여러 개로 늘어난다.
    """
    _load(catalog, TUESDAY, "2026-09-01")
    _load(catalog, TUESDAY, "2026-09-01")
    _load(catalog, SATURDAY, "2026-09-05")

    snapshots = catalog.load_table(IDENTIFIER).metadata.snapshots
    roots = [s for s in snapshots if s.parent_snapshot_id is None]

    assert len(snapshots) > 1
    assert len(roots) == 1


def test_weekend_substitution_is_persisted(catalog) -> None:
    """요청 날짜와 응답 날짜의 갈림이 테이블에 실제로 남는다."""
    _load(catalog, SATURDAY, "2026-09-05")

    rows = (
        catalog.load_table(IDENTIFIER)
        .scan(row_filter=EqualTo("rate_date", "2026-09-05"))
        .to_arrow()
        .to_pylist()
    )
    assert {(r["rate_date"], r["source_date"]) for r in rows} == {
        ("2026-09-05", "2026-09-04")
    }
