"""Iceberg 카탈로그 접근의 **단일 출처** (오케스트레이터 중립).

**왜 이 파일이 새로 생겼나.** Dagster 트리에서는 카탈로그 설정이 *리소스마다*
필요해 `defs/resources.py`에 `IcebergCatalogConfig(properties={...})` 블록이
**12회 통째로 복제**돼 있었다. 원인은 규율 부족이 아니라 도구 제약이다 —
dagster-iceberg의 `IcebergCatalogConfig`는 `dg.EnvVar`를 지원하지 않아
(properties가 평문 문자열) 리소스를 선언할 때마다 8개 키를 다시 적어야 했다.

⇒ **리소스 시스템을 벗어나면 그 원인 자체가 소멸한다.** 카탈로그는 태스크가
필요할 때 함수로 열면 되고, properties는 여기 한 곳에만 있으면 된다.
(원본 12블록, 블록당 8키 = 96줄의 평문 설정이 `_catalog_properties()` 하나로 접힌다.)

적재 모드의 의미론(`WriteMode`)도 여기 있다 — 원본에서는 헬퍼 함수 이름
(`append_arrow_to_iceberg`·`replace_partition_in_iceberg`)과 문자열 인자
(`mode="replace"`)에 흩어져 있어, 어떤 조합이 스냅샷 계보를 끊는지 읽는 사람이
세 군데를 맞춰봐야 알 수 있었다.
"""

import contextlib
import logging
from functools import lru_cache
from typing import Any, Literal

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.catalog import load_catalog as _pyiceberg_load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.table import Table

from pipeline_project.common.constants import (
    CATALOG_NAME,
    aws_region,
    iceberg_catalog_uri,
    s3_access_key_id,
    s3_endpoint,
    s3_secret_access_key,
    warehouse,
)

# 적재 모드. **문자열 Literal로 두는 것이 결정이다** — 이 값은 DAG 정의·설정
# 파일·로그 메타데이터를 왕복하므로, Enum이면 경계마다 변환이 붙고 로그에는
# `WriteMode.append`처럼 접두어가 섞인 문자열이 남는다.
WriteMode = Literal["append", "overwrite", "replace_partition", "recreate"]
#   append            : 누적. Flink IncrementalAppendScan 소스의 유일한 허용 모드
#   overwrite         : 전량 교체, 스냅샷 계보 유지 ← 구 IO 매니저 11자산의 대응물
#   replace_partition : 파티션 범위만 교체(멱등)
#   recreate          : drop_table 후 재생성 — 🔴 계보 단절. 기본값에서 배제

# 🔴 **`mode`에는 기본값을 두지 않는다**(키워드 필수 인자).
#    원본에서는 대용량 경로가 `mode: str = "replace"`, 일반 경로가
#    `mode: str = "append"`로 **함수마다 다른 기본값**을 들고 있었다. 즉 호출부가
#    모드를 적지 않으면 무엇이 되는지가 호출한 함수에 달렸고, 그 둘 중 하나는
#    `drop_table`이었다.
#
#    `recreate`는 `drop_table` → 재생성이라 재생성본이 `parent_snapshot_id = NULL`인
#    **새 루트**가 된다. 그러면 ⓐ실행 중인 Flink 잡은 대상이 사라지고
#    ⓑ`IncrementalAppendScan`이 따라갈 조상을 잃어 **스트리밍 소스 자격을 잃는다**.
#    그 실패는 에러가 아니라 **조용한 누락**이고, 행 수로는 보이지 않는다.
#    (`scripts/iceberg_changelog_probe.py` — "멱등은 데이터 축이다. 계보를 매번
#     끊는 것은 별개다.")
#
#    ⚠️ 대상이 **스트리밍 소스가 아니어도** 위험은 남는다 — "소스가 아니다"와
#    "잡이 읽지 않는다"는 다르다(조인 상대도 잡이 읽는다).
#
# 🔴 **구 IO 매니저 11자산의 대응물은 `append`가 아니라 `overwrite`다.**
#    판정 근거(dagster-iceberg 0.3.14 소스 실측):
#      · `_utils/io.py:52`  `DEFAULT_WRITE_MODE = WriteMode.overwrite`
#      · `_utils/io.py:184` 파티션이 없으면 `row_filter = ALWAYS_TRUE`
#      · `_utils/io.py:384` overwrite는 `Table.overwrite(data, overwrite_filter=...)`
#      · 패키지 전체에서 `drop_table` **0건**(grep)
#    즉 IO 매니저는 테이블을 지우지 않고 `ALWAYS_TRUE` 필터로 덮어썼다 →
#    전량 교체이면서 **계보는 이어진다**. `pa.Table`을 반환하던 11개 자산
#    (`grep -c io_manager_key` = 11)을 옮길 때 `recreate`를 쓰면 원본에 없던
#    계보 단절이 새로 생긴다.


@lru_cache(maxsize=1)
def _catalog_properties() -> dict[str, str]:
    """Pyiceberg 카탈로그 properties (원본 12블록의 단일 출처).

    Trino·Spark·Flink가 쓰는 것과 **같은** Iceberg JDBC 카탈로그를 가리킨다
    (별도 메타스토어를 두지 않는다).

    🔴 값에 S3 시크릿과 카탈로그 비밀번호가 들어 있다 — **로그·메타데이터에
    싣지 않는다**(dict 전체를 그대로 찍는 코드를 만들지 않는다).
    """
    return {
        "type": "sql",
        "uri": iceberg_catalog_uri(),
        "warehouse": warehouse(),
        "s3.endpoint": s3_endpoint(),
        "s3.access-key-id": s3_access_key_id(),
        "s3.secret-access-key": s3_secret_access_key(),
        "s3.region": aws_region(),
        "s3.path-style-access": "true",
    }


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    """Pyiceberg 카탈로그를 열어 반환한다(프로세스당 1개 재사용).

    ⚠️ **여기에는 캐시를 건다**(`constants`의 함수들과 다르다). 카탈로그 객체는
    sqlalchemy 엔진·커넥션 풀을 들고 있어 태스크마다 새로 만들면 카탈로그 DB의
    커넥션이 실행 수만큼 늘어난다. 대신 env 변경이 **프로세스 재기동까지**
    반영되지 않으므로, 테스트는 이 함수를 monkeypatch한다(env를 바꾸지 않는다).

    Returns:
        `CATALOG_NAME` 이름으로 열린 pyiceberg 카탈로그.
    """
    return _pyiceberg_load_catalog(CATALOG_NAME, **_catalog_properties())


def table_exists(catalog: Catalog, identifier: str) -> bool:
    """식별자에 해당하는 테이블이 카탈로그에 존재하는지 확인한다."""
    try:
        catalog.load_table(identifier)
    except NoSuchTableError:
        return False
    else:
        return True


def ensure_table(catalog: Catalog, identifier: str, schema: pa.Schema) -> Table:
    """테이블을 로드하고, 없으면 네임스페이스·테이블을 생성해 반환한다."""
    namespace = identifier.rsplit(".", 1)[0]
    with contextlib.suppress(NamespaceAlreadyExistsError):
        catalog.create_namespace(namespace)
    try:
        return catalog.load_table(identifier)
    except NoSuchTableError:
        return catalog.create_table(identifier, schema=schema)


def load_iceberg_table(namespace: str, table: str) -> Table:
    """기존 Iceberg 테이블을 로드한다(읽기 경로).

    없으면 pyiceberg의 `NoSuchTableError`가 그대로 올라간다 — 읽기 경로에서
    테이블 부재를 **빈 결과로 바꾸지 않는다**(조용한 0행 금지).

    Args:
        namespace: Iceberg 네임스페이스(= dbt source schema).
        table: 테이블명.

    Returns:
        pyiceberg 테이블 객체.
    """
    return load_catalog().load_table(f"{namespace}.{table}")


def _partition_filter(partition_column: str | None, partition_value: str | None) -> str:
    """파티션 교체용 `overwrite_filter` 문자열을 만든다.

    🔴 필터를 `EqualTo(...)` 객체가 아니라 **문자열**로 넘긴다.
    `overwrite_filter`는 `BooleanExpression | str` 둘 다 받는데(pyiceberg 0.11.1),
    표현식 클래스는 pydantic 모델이면서 커스텀 `__init__`을 갖고 있어 **mypy가
    그 `__init__`을 못 보고** 합성 시그니처로 판정한다(오류 4건). 이 저장소는
    소스에 `type: ignore`가 0건이고 `warn_unused_ignores`도 켜져 있어, 선례를
    깨는 대신 문서화된 다른 입력 형태를 쓴다. 두 형태가 같은 결과를 내는 것은
    실측으로 확인했다(멱등·격리·루트 1개).

    ⚠️ 값을 문자열에 끼워 넣으므로 따옴표가 섞이면 필터가 깨진다. 파티션 키는
    상류가 만드는 날짜 문자열이라 실제로는 오지 않지만, **조용히 잘못된 범위를
    지우는 것**이 최악이므로 fail-closed로 막는다.

    Args:
        partition_column: 파티션을 가르는 컬럼명(예: `"rate_date"`).
        partition_value: 이번 파티션의 값.

    Returns:
        `"<column> = '<value>'"` 형태의 필터 문자열.

    Raises:
        ValueError: 인자가 비었거나 값에 작은따옴표가 섞였을 때.
    """
    if not partition_column or partition_value is None:
        message = (
            "replace_partition 모드에는 partition_column·partition_value가 필요하다"
        )
        raise ValueError(message)
    if "'" in partition_value:
        message = f"파티션 값에 작은따옴표를 쓸 수 없다: {partition_value!r}"
        raise ValueError(message)
    return f"{partition_column} = '{partition_value}'"


def write_arrow_to_iceberg(
    arrow: pa.Table,
    namespace: str,
    table: str,
    *,
    mode: WriteMode,
    partition_column: str | None = None,
    partition_value: str | None = None,
    logger: logging.Logger | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """완성된 Arrow 테이블을 Iceberg에 적재하고 관측 메타데이터를 반환한다.

    스키마는 호출부가 명시한다(추론에 기대지 않는다). 테이블이 없으면 만든다.

    Args:
        arrow: 적재할 Arrow 테이블.
        namespace: Iceberg 네임스페이스.
        table: 테이블명.
        mode: 적재 모드. **기본값이 없다** — 모듈 상단 `WriteMode` 주석 참조.
        partition_column: `replace_partition` 전용. 파티션을 가르는 컬럼명.
        partition_value: `replace_partition` 전용. 이번 파티션의 값.
        logger: 로그를 남길 로거(없으면 남기지 않는다).
        extra_metadata: 호출부가 덧붙일 관측 메타데이터(행 수 분포 등).

    Returns:
        관측 메타데이터 dict(`table`·`rows`·`mode`, 파티션 모드면 `partition`).

    Raises:
        ValueError: 알 수 없는 모드이거나 파티션 인자가 어긋날 때.
    """
    catalog = load_catalog()
    identifier = f"{namespace}.{table}"
    partition: str | None = None

    if mode == "replace_partition":
        # ⚠️ 파티션을 **처음** 적재할 때 pyiceberg가 `UserWarning: Delete operation
        # did not match any records`를 낸다. 지울 행이 아직 없어서 나는 것이므로
        # 결함이 아니다 — 로그에서 보고 오진하지 않도록 적어둔다.
        overwrite_filter = _partition_filter(partition_column, partition_value)
        partition = f"{partition_column}={partition_value}"
        ensure_table(catalog, identifier, arrow.schema).overwrite(
            arrow, overwrite_filter=overwrite_filter
        )
    elif mode == "overwrite":
        # `Table.overwrite(df)`의 기본 필터는 ALWAYS_TRUE다 → 전량 교체.
        # 구현이 `delete(filter)` → append이고 **`drop_table`이 없어 계보가
        # 이어진다**(pyiceberg 0.11.1 `table/__init__.py:636-653` 실측).
        # 다만 한 트랜잭션에서 **스냅샷은 여러 개가 날 수 있다**(DELETE·OVERWRITE·
        # APPEND) — "한 커밋 한 스냅샷"으로 읽지 않는다.
        ensure_table(catalog, identifier, arrow.schema).overwrite(arrow)
    elif mode in {"append", "recreate"}:
        if mode == "recreate" and table_exists(catalog, identifier):
            catalog.drop_table(identifier)
        ensure_table(catalog, identifier, arrow.schema).append(arrow)
    else:
        message = f"알 수 없는 적재 모드: {mode!r}"
        raise ValueError(message)

    metadata: dict[str, Any] = {
        "table": identifier,
        "rows": arrow.num_rows,
        "mode": mode,
    }
    if partition is not None:
        metadata["partition"] = partition
    if extra_metadata:
        metadata.update(extra_metadata)

    if logger is not None:
        logger.info(
            "%s ← %d rows 적재 (mode=%s, partition=%s)",
            identifier,
            arrow.num_rows,
            mode,
            partition or "없음",
        )
    return metadata
