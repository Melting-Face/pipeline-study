"""Iceberg 카탈로그·파티션 교체 적재.

Dagster `common/helper.py`의 `ensure_table`·`replace_partition_in_iceberg` 이식.
🔴 `overwrite(filter)`는 delete → append이고 `drop_table`이 없어
**스냅샷 계보가 이어진다**. `replace`(drop → recreate)를 쓰지 않는 이유다.
"""

import contextlib
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.table import Table

# 🔴 전 엔진(Trino·Spark·Flink·Dagster) 공통 — JDBC 카탈로그는 이 이름으로 레지스트리를
# 나눈다
CATALOG_NAME = "iceberg"
WAREHOUSE = "s3://warehouse"
AWS_REGION = "us-east-1"


@dataclass(frozen=True)
class S3Conf:
    """S3(SeaweedFS) 접속값. 빈 값이면 생성 시점에 실패한다(빈 키로 접속하지 않는다)."""

    endpoint: str
    access_key: str
    secret_key: str

    def __post_init__(self) -> None:
        """빈 필드가 있으면 ValueError."""
        empty = [
            name
            for name in ("endpoint", "access_key", "secret_key")
            if not getattr(self, name)
        ]
        if empty:
            message = (
                f"S3 접속값이 비었다: {empty} — Connection `seaweedfs_s3`를 확인하라"
            )
            raise ValueError(message)


def make_catalog(catalog_uri: str, s3: S3Conf) -> Catalog:
    """Trino와 같은 JDBC(SQL) 카탈로그를 연다."""
    return load_catalog(
        CATALOG_NAME,
        type="sql",
        uri=catalog_uri,
        warehouse=WAREHOUSE,
        **{
            "s3.endpoint": s3.endpoint,
            "s3.access-key-id": s3.access_key,
            "s3.secret-access-key": s3.secret_key,
            "s3.region": AWS_REGION,
            "s3.path-style-access": "true",
        },
    )


def ensure_table(catalog: Catalog, identifier: str, schema: pa.Schema) -> Table:
    """테이블을 로드하고, 없으면 네임스페이스·테이블을 만든다."""
    namespace = identifier.rsplit(".", 1)[0]
    with contextlib.suppress(NamespaceAlreadyExistsError):
        catalog.create_namespace(namespace)
    try:
        return catalog.load_table(identifier)
    except NoSuchTableError:
        return catalog.create_table(identifier, schema=schema)


def replace_partition(
    table: Table, arrow: pa.Table, column: str, value: str
) -> dict[str, Any]:
    """한 파티션 범위만 지우고 다시 넣는다(멱등). 반환: 관측 메타(행 수·스냅샷 id).

    처음 적재할 때 pyiceberg가 `Delete operation did not match any records`
    경고를 낸다. 지울 행이 아직 없어서이고 결함이 아니다.
    """
    # 🔴 값을 필터 문자열에 끼워 넣으므로 따옴표가 섞이면 엉뚱한 범위를 지울 수 있다 —
    # fail-closed
    if "'" in value:
        message = f"파티션 값에 작은따옴표를 쓸 수 없다: {value!r}"
        raise ValueError(message)
    table.overwrite(arrow, overwrite_filter=f"{column} = '{value}'")
    snapshot = table.current_snapshot()
    return {
        "rows": arrow.num_rows,
        "snapshot_id": snapshot.snapshot_id if snapshot else None,
    }
