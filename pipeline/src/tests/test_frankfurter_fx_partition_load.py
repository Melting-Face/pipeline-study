"""적재 모드 테스트 — `common/catalog.py`의 `write_arrow_to_iceberg`.

**실인프라에 붙지 않는다.** pyiceberg의 SQLite 카탈로그 + `tmp_path` warehouse로
실제 Iceberg 테이블을 만들어 검증한다 — SeaweedFS·CNPG·K8s 어디에도 접속하지
않으므로 `docs/test.md`의 격리 원칙에 어긋나지 않는다.

🔴 음성 대조를 함께 둔다. "두 번 넣어도 4행"은 적재가 멱등이라는 뜻일 수도,
**테스트가 둔감하다는 뜻일 수도** 있다. 같은 데이터를 `append`로 넣으면 8행이
되는 것을 함께 확인해야 앞의 4행이 의미를 갖는다.

**원본과 갈리는 지점 셋**(오케스트레이터 중립 이식):

1. `dg.build_asset_context(partition_key=...)` → `rate_date` **문자열 인자**.
   파티션 키는 오케스트레이터 컨텍스트가 아니라 호출부가 넘기는 값이다.
2. 원본은 `defs/frankfurter_fx/assets.py`의 `_rates_json_to_arrow`를 불렀다.
   그 파서는 **P0 범위 밖**(DAG·데이터셋 정의는 단계 2~10)이므로 **테스트 안에
   최소 빌더(`_fx_arrow`)를 둔다**. 검증 대상은 파서가 아니라 `write_arrow_to_iceberg`
   의 적재 모드이므로 축은 보존된다. ⚠️ 단 **파서 자체의 축은 이 파일이 더 이상
   덮지 않는다**(원본 `test_frankfurter_fx_parse.py`의 몫이고 그것은 P0에서
   제외됐다). 파서가 이식되면 `_fx_arrow`를 그 함수로 되돌린다.
3. 🔴 `recreate`·`overwrite`의 **계보 축 대조를 새로 넣었다.** `WriteMode` 주석이
   "`recreate`는 계보를 끊는다 / `overwrite`는 잇는다"고 단정하는데, 그것이
   선언으로만 남으면 검증되지 않은 주장이다(원칙 7 — 새로 건 규칙은 일부러
   위반시켜 본다).
"""

from datetime import datetime, timezone
from typing import Any

import pyarrow as pa
import pytest
from pipeline_project.common import catalog as catalog_module
from pipeline_project.common.catalog import write_arrow_to_iceberg
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import EqualTo

INGESTED_AT = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)
NAMESPACE = "frankfurter_fx"
TABLE = "fx_rates_daily"
IDENTIFIER = f"{NAMESPACE}.{TABLE}"

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

# bronze 스키마 최소 사본. `rate_date`(요청)와 `source_date`(응답 에코)를 나란히
# 두는 것이 원천의 **주말 시프트**를 관측하는 축이다 — 상류 중앙은행은 영업일에만
# 고시하고 원천은 주말 요청에 직전 영업일 값을 **조용히** 돌려준다(상태코드·행
# 수·값의 범위로는 잡히지 않고 `date` 필드 하나만 다르다).
FX_SCHEMA = pa.schema(
    [
        ("rate_date", pa.string()),
        ("source_date", pa.string()),
        ("base_currency", pa.string()),
        ("quote_currency", pa.string()),
        ("rate", pa.float64()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


def _fx_arrow(payload: dict[str, Any], rate_date: str) -> pa.Table:
    """픽스처 응답을 bronze Arrow 테이블로 편다(테스트 전용 최소 빌더)."""
    rows = [
        {
            "rate_date": rate_date,
            "source_date": payload["date"],
            "base_currency": payload["base"],
            "quote_currency": quote_currency,
            "rate": float(rate),
            "ingested_at": INGESTED_AT,
        }
        for quote_currency, rate in sorted(payload["rates"].items())
    ]
    return pa.Table.from_pylist(rows, schema=FX_SCHEMA)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """로컬 SQLite 카탈로그로 `catalog.load_catalog()`를 갈아끼운다.

    env를 바꾸지 않고 함수를 바꾸는 이유: `load_catalog`에는 `@lru_cache`가 걸려
    있어 env를 고쳐도 첫 호출 값이 박제된다(그 캐시가 의도다 — 커넥션 풀 재사용).
    """
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()
    cat = SqlCatalog(
        "probe",
        uri=f"sqlite:///{tmp_path / 'catalog.db'}",
        warehouse=f"file://{warehouse}",
    )
    monkeypatch.setattr(catalog_module, "load_catalog", lambda: cat)
    return cat


def _load(payload: dict[str, Any], rate_date: str) -> dict[str, Any]:
    """파티션 교체 모드로 하루치를 적재한다."""
    return write_arrow_to_iceberg(
        _fx_arrow(payload, rate_date),
        NAMESPACE,
        TABLE,
        mode="replace_partition",
        partition_column="rate_date",
        partition_value=rate_date,
    )


def _count(catalog: SqlCatalog, rate_date: str | None = None) -> int:
    table = catalog.load_table(IDENTIFIER)
    if rate_date is None:
        return table.scan().to_arrow().num_rows
    return table.scan(row_filter=EqualTo("rate_date", rate_date)).to_arrow().num_rows


def _roots(catalog: SqlCatalog) -> int:
    """스냅샷 계보의 루트 수(= `parent_snapshot_id`가 없는 스냅샷)."""
    snapshots = catalog.load_table(IDENTIFIER).metadata.snapshots
    return len([s for s in snapshots if s.parent_snapshot_id is None])


# ── replace_partition: 멱등·격리·계보 ───────────────────────────────────


def test_same_partition_twice_does_not_grow(catalog) -> None:
    """🔴 파티션 적재의 최소 요건 — 재실행이 행을 늘리지 않는다."""
    _load(TUESDAY, "2026-09-01")
    first = _count(catalog)
    _load(TUESDAY, "2026-09-01")

    assert first == 4
    assert _count(catalog) == first


def test_append_would_grow(catalog) -> None:
    """음성 대조 — append였다면 두 배가 된다.

    이 테스트가 없으면 위의 "4행 그대로"가 **멱등의 증거인지 테스트가 둔감한
    것인지** 구분되지 않는다.
    """
    arrow = _fx_arrow(TUESDAY, "2026-09-01")
    write_arrow_to_iceberg(arrow, NAMESPACE, TABLE, mode="append")
    first = _count(catalog)

    write_arrow_to_iceberg(arrow, NAMESPACE, TABLE, mode="append")

    assert first == 4
    assert _count(catalog) == first * 2


def test_other_partitions_are_not_touched(catalog) -> None:
    """overwrite_filter가 범위를 가른다 — 한 파티션 재적재가 다른 것을 지우지 않는다."""
    _load(TUESDAY, "2026-09-01")
    _load(SATURDAY, "2026-09-05")
    _load(SATURDAY, "2026-09-05")

    assert _count(catalog) == 8
    assert _count(catalog, "2026-09-01") == 4
    assert _count(catalog, "2026-09-05") == 4


def test_snapshot_lineage_stays_single_rooted(catalog) -> None:
    """🔴 계보가 끊기지 않는다 — 행 수로는 안 보이는 축이다.

    `drop_table` 후 재생성이면 재생성본이 `parent_snapshot_id = None`인 새
    루트가 되어 루트가 여러 개로 늘어난다.
    """
    _load(TUESDAY, "2026-09-01")
    _load(TUESDAY, "2026-09-01")
    _load(SATURDAY, "2026-09-05")

    snapshots = catalog.load_table(IDENTIFIER).metadata.snapshots

    assert len(snapshots) > 1
    assert _roots(catalog) == 1


def test_weekend_substitution_is_persisted(catalog) -> None:
    """요청 날짜와 응답 날짜의 갈림이 테이블에 실제로 남는다.

    ⚠️ 파서가 그 갈림을 *만드는지*는 이 파일의 축이 아니다(원본
    `test_frankfurter_fx_parse.py`의 몫). 여기서 보는 것은 적재 왕복에서
    두 컬럼이 **살아남는지**다.
    """
    _load(SATURDAY, "2026-09-05")

    rows = (
        catalog.load_table(IDENTIFIER)
        .scan(row_filter=EqualTo("rate_date", "2026-09-05"))
        .to_arrow()
        .to_pylist()
    )
    assert {(r["rate_date"], r["source_date"]) for r in rows} == {
        ("2026-09-05", "2026-09-04")
    }


def test_quote_in_partition_value_is_rejected(catalog) -> None:
    """🔴 새로 건 가드를 일부러 위반시켜 막히는지 본다.

    파티션 필터를 문자열로 조립하므로 값에 따옴표가 섞이면 필터가 깨진다.
    **조용히 잘못된 범위를 지우는 것**이 최악이라 fail-closed로 막는다.
    날짜 파티션에서는 실제로 오지 않지만, 막힌다는 것은 확인해야 "막았다"고
    쓸 수 있다.
    """
    arrow = _fx_arrow(TUESDAY, "2026-09-01")
    with pytest.raises(ValueError, match="작은따옴표"):
        write_arrow_to_iceberg(
            arrow,
            NAMESPACE,
            TABLE,
            mode="replace_partition",
            partition_column="rate_date",
            partition_value="2026-09-01' OR '1'='1",
        )


def test_replace_partition_requires_partition_args(catalog) -> None:
    """파티션 인자 없이 `replace_partition`을 쓰면 막힌다(fail-closed).

    빠뜨린 필터가 `ALWAYS_TRUE`로 떨어지면 **전량이 조용히 교체**된다 —
    모드 이름은 "파티션"인데 동작은 전량이라 가장 오해하기 쉬운 조합이다.
    """
    arrow = _fx_arrow(TUESDAY, "2026-09-01")
    with pytest.raises(ValueError, match="partition_column"):
        write_arrow_to_iceberg(arrow, NAMESPACE, TABLE, mode="replace_partition")


# ── overwrite vs recreate: 계보 대조 (신규) ─────────────────────────────


def test_overwrite_replaces_all_rows_and_keeps_lineage(catalog) -> None:
    """🔴 구 IO 매니저 11자산의 대응물 — 전량 교체이면서 계보는 잇는다.

    판정 근거는 `catalog.py` 상단 주석이다(dagster-iceberg 0.3.14는
    `Table.overwrite(ALWAYS_TRUE)`를 쓰고 `drop_table`은 0건).
    """
    write_arrow_to_iceberg(
        _fx_arrow(TUESDAY, "2026-09-01"), NAMESPACE, TABLE, mode="overwrite"
    )
    first = _count(catalog)
    write_arrow_to_iceberg(
        _fx_arrow(SATURDAY, "2026-09-05"), NAMESPACE, TABLE, mode="overwrite"
    )

    assert first == 4
    # 전량 교체 — 행이 쌓이지 않고, 남은 것은 두 번째 적재분뿐이다.
    assert _count(catalog) == 4
    assert _count(catalog, "2026-09-01") == 0
    assert _count(catalog, "2026-09-05") == 4
    # 🔴 계보는 이어진다 — 루트는 1개이고 **이력이 남는다**(아래 recreate 대조에서
    #    이 「이력 길이」가 갈리는 유일한 축임이 드러난다).
    assert _roots(catalog) == 1
    assert len(catalog.load_table(IDENTIFIER).metadata.snapshots) > 1


def test_recreate_breaks_snapshot_lineage(catalog) -> None:
    """🔴 음성 대조 — `recreate`는 실제로 계보를 끊는다.

    위 `overwrite`의 "루트 1개 · 이력 2개 이상"이 의미를 가지려면, 끊는 모드에서
    측정값이 **달라진다**는 것을 같은 측정으로 보여야 한다.

    🔴 **단 신호는 「루트 수」가 아니라 「이력 소실」이다.** 원본
    (`defs/.../helper.py`·`test_frankfurter_fx_partition_load.py`)은 "재생성본이
    새 루트가 되어 **루트가 여러 개로 늘어난다**"고 적었는데, `drop_table`은
    테이블 메타데이터를 통째로 지우므로 재생성본의 스냅샷 이력은 **1개에서 다시
    시작**한다 — 루트 수는 여전히 1이다. 즉 루트 수로 감시하면 이 사고가
    **잡히지 않는다**(계보 단절이 「끊김」이 아니라 「처음부터 짧음」으로 보인다).
    행 수로도 구분되지 않으니(둘 다 4행) 이 축은 이중으로 조용하다.
    """
    write_arrow_to_iceberg(
        _fx_arrow(TUESDAY, "2026-09-01"), NAMESPACE, TABLE, mode="recreate"
    )
    assert _roots(catalog) == 1

    write_arrow_to_iceberg(
        _fx_arrow(SATURDAY, "2026-09-05"), NAMESPACE, TABLE, mode="recreate"
    )

    # 행 수로는 overwrite와 똑같이 보인다 — 여기가 오진 지점 하나.
    assert _count(catalog) == 4
    # 루트 수로도 똑같이 보인다 — 여기가 오진 지점 둘(원본이 감시하던 축).
    assert _roots(catalog) == 1
    # 🔴 갈리는 것은 **이력 길이**다. overwrite는 2회 적재에 스냅샷이 쌓이지만
    #    (`test_overwrite_...`가 루트 1개 + 복수 스냅샷을 본다) recreate는 메타데이터를
    #    새로 만들어 이력이 1개로 되돌아간다 — 그래서 `IncrementalAppendScan`이
    #    따라갈 조상이 없다.
    snapshots = catalog.load_table(IDENTIFIER).metadata.snapshots
    assert len(snapshots) == 1
    assert snapshots[0].parent_snapshot_id is None


def test_unknown_mode_is_rejected(catalog) -> None:
    """타입 체커를 우회해 들어온 모드도 런타임에서 막힌다(fail-closed)."""
    arrow = _fx_arrow(TUESDAY, "2026-09-01")
    with pytest.raises(ValueError, match="알 수 없는 적재 모드"):
        write_arrow_to_iceberg(arrow, NAMESPACE, TABLE, mode="replace")  # type: ignore[arg-type]
