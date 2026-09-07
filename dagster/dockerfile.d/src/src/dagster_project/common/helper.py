"""원천 → Iceberg 적재 헬퍼 (데이터셋 무관 공통).

**S3 csv.gz** 경로 두 가지:
- read_csv_gz_table: 일반(부하 없는) 파일을 통째로 읽어 pa.Table 반환
  → IO 매니저가 write.
- load_heavy_csv_gz_to_iceberg: 대용량 csv.gz(예: 3.3GB)를 boto3 스트리밍 +
  청크 append로 메모리를 일정하게 유지하며 적재(IO 매니저 미사용).

**외부 HTTP API** 경로 두 가지:
- fetch_json: 공개 API GET(재시도 포함). 응답 본문은 신뢰하지 않는 외부
  데이터이므로 호출부가 스키마를 명시해 변환한다.
- append_arrow_to_iceberg: 이미 만들어진 pa.Table을 Iceberg에 append/replace.
  🔴 Flink 스트리밍 소스로 쓸 bronze 테이블은 **반드시 append**여야 한다 —
  Iceberg Flink 소스는 IncrementalAppendScan 기반이라 overwrite·delete 스냅샷을
  지원하지 않고, 그 실패 모드는 에러가 아니라 **조용한 누락**이다.
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any

import pyarrow as pa
import pyarrow.csv as pacsv
import requests
from dagster_aws.s3 import S3Resource
from dagster_iceberg.resource import IcebergTableResource

import dagster as dg
from dagster_project.common.constants import DEFAULT_CHUNK_ROWS

if TYPE_CHECKING:
    from pyiceberg.catalog import Catalog
    from pyiceberg.table import Table


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """s3://bucket/key → (bucket, key)."""
    if not uri.startswith("s3://"):
        message = f"s3:// URI가 아님: {uri}"
        raise ValueError(message)
    bucket, _, key = uri[len("s3://") :].partition("/")
    return bucket, key


def open_csv_gz_stream(
    s3: S3Resource,
    source_uri: str,
    column_types: dict[str, pa.DataType] | None = None,
) -> pacsv.CSVStreamingReader:
    """boto3로 s3 객체를 받아 gzip 해제 스트림을 pyarrow CSV 리더로 연다.

    Args:
        s3: dagster-aws S3Resource.
        source_uri: 원본 csv.gz의 s3 URI.
        column_types: 특정 컬럼의 추론 타입을 강제할 매핑(예: 자유형 value 컬럼을
            string으로 고정). 미지정 컬럼은 pyarrow가 자동 추론한다.
    """
    bucket, key = parse_s3_uri(source_uri)
    body = s3.get_client().get_object(Bucket=bucket, Key=key)["Body"]
    # StreamingBody(file-like)를 pyarrow로 감싸 gzip 스트리밍 해제
    stream = pa.CompressedInputStream(pa.PythonFile(body, mode="r"), "gzip")
    convert_options = (
        pacsv.ConvertOptions(column_types=column_types) if column_types else None
    )
    return pacsv.open_csv(stream, convert_options=convert_options)


def read_csv_gz_table(
    s3: S3Resource,
    source_uri: str,
    column_types: dict[str, pa.DataType] | None = None,
) -> pa.Table:
    """일반 파일을 통째로 읽어 Arrow 테이블로 반환한다(IO 매니저가 적재).

    대용량 파일에는 사용하지 말 것(전량 메모리 적재). 그 경우
    load_heavy_csv_gz_to_iceberg를 쓴다.

    Args:
        s3: dagster-aws S3Resource.
        source_uri: 원본 csv.gz의 s3 URI.
        column_types: 컬럼 타입 강제 매핑(선택). 자유형 문자열 컬럼이 숫자로
            잘못 추론되는 것을 막을 때 쓴다.
    """
    reader = open_csv_gz_stream(s3, source_uri, column_types=column_types)
    return reader.read_all()


def table_exists(catalog: Catalog, identifier: str) -> bool:
    """식별자에 해당하는 테이블이 카탈로그에 존재하는지 확인한다."""
    from pyiceberg.exceptions import NoSuchTableError

    try:
        catalog.load_table(identifier)
    except NoSuchTableError:
        return False
    else:
        return True


def ensure_table(catalog: Catalog, identifier: str, schema: pa.Schema) -> Table:
    """테이블을 로드하고, 없으면 네임스페이스·테이블을 생성해 반환한다."""
    from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError

    namespace = identifier.rsplit(".", 1)[0]
    with contextlib.suppress(NamespaceAlreadyExistsError):
        catalog.create_namespace(namespace)
    try:
        return catalog.load_table(identifier)
    except NoSuchTableError:
        return catalog.create_table(identifier, schema=schema)


def load_heavy_csv_gz_to_iceberg(
    context: dg.AssetExecutionContext,
    *,
    s3: S3Resource,
    iceberg_table: IcebergTableResource,
    source_uri: str,
    mode: str = "replace",
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
    column_types: dict[str, pa.DataType] | None = None,
) -> dg.MaterializeResult:
    """대용량 csv.gz를 청크 단위로 Iceberg 테이블에 적재한다.

    IcebergTableResource.load()는 기존 테이블만 로드하므로, 생성/append를 위해
    리소스의 config(properties)로 pyiceberg 카탈로그를 재구성한다.

    Args:
        context: 에셋 실행 컨텍스트.
        s3: dagster-aws S3Resource.
        iceberg_table: 대상 테이블 바인딩 리소스(name·config·table·namespace).
        source_uri: 원본 csv.gz의 s3 URI.
        mode: "replace"(재적재) 또는 "append"(누적).
        chunk_rows: 한 번에 append 할 행 수.
        column_types: 컬럼 타입 강제 매핑(선택). 자유형 value 컬럼(chartevents·
            labevents)이 청크마다 다른 타입으로 추론돼 스키마가 어긋나는 것을 막는다.

    Returns:
        적재 메타데이터(테이블·원본·행 수)를 담은 MaterializeResult.
    """
    from pyiceberg.catalog import load_catalog

    properties = iceberg_table.config.model_dump()["properties"]
    catalog = load_catalog(iceberg_table.name, **properties)
    identifier = f"{iceberg_table.schema_}.{iceberg_table.table}"

    if mode == "replace" and table_exists(catalog, identifier):
        catalog.drop_table(identifier)

    reader = open_csv_gz_stream(s3, source_uri, column_types=column_types)
    table = None
    pending: list = []
    pending_rows = 0
    total_rows = 0

    def flush() -> None:
        nonlocal table, pending, pending_rows
        if not pending:
            return
        arrow = pa.Table.from_batches(pending)
        if table is None:
            table = ensure_table(catalog, identifier, arrow.schema)
        table.append(arrow)
        pending = []
        pending_rows = 0

    for batch in reader:
        pending.append(batch)
        pending_rows += batch.num_rows
        total_rows += batch.num_rows
        if pending_rows >= chunk_rows:
            flush()
    flush()

    context.log.info(
        "%s ← %s 적재 완료 (%d rows, mode=%s)",
        identifier,
        source_uri,
        total_rows,
        mode,
    )
    return dg.MaterializeResult(
        metadata={
            "table": identifier,
            "source_uri": source_uri,
            "rows": total_rows,
            "mode": mode,
        }
    )


def _request(
    url: str,
    params: dict[str, str],
    *,
    timeout_s: int,
    retries: int,
) -> requests.Response:
    """공개 API를 GET하고 성공 응답을 반환한다(429·5xx만 백오프 재시도).

    그 외 4xx는 **즉시 실패**시킨다 — 잘못된 요청을 반복해도 답이 바뀌지 않고
    rate limit 예산만 쓴다. `Retry-After` 헤더가 오면 계산된 백오프보다
    그것을 우선한다.
    """
    for attempt in range(retries + 1):
        response = requests.get(url, params=params, timeout=timeout_s)
        if response.ok:
            return response

        retryable = response.status_code == 429 or response.status_code >= 500
        if not retryable or attempt == retries:
            response.raise_for_status()
            # 3xx처럼 raise_for_status가 침묵하는 경우까지 닫는다(fail-closed).
            message = f"예상 밖 응답 {response.status_code}: {url}"
            raise RuntimeError(message)

        wait_s = float(response.headers.get("Retry-After") or 2**attempt)
        time.sleep(wait_s)

    message = f"재시도 소진: {url}"  # 도달 불가(위에서 raise) — 타입 체커용
    raise RuntimeError(message)


def fetch_json(
    url: str,
    params: dict[str, str],
    *,
    timeout_s: int = 30,
    retries: int = 3,
) -> dict[str, Any]:
    """공개 API에서 JSON을 받아 파싱해 반환한다."""
    return _request(url, params, timeout_s=timeout_s, retries=retries).json()


def fetch_text(
    url: str,
    params: dict[str, str],
    *,
    timeout_s: int = 30,
    retries: int = 3,
) -> str:
    """공개 API에서 텍스트 본문을 받아 반환한다.

    JSON을 지원하지 않는 서비스용이다 — 예: NWIS Site Service는
    `format=json`에 HTTP 400을 주고 `format=rdb`(탭 구분)만 지원한다
    (2026-09-05 실측).
    """
    return _request(url, params, timeout_s=timeout_s, retries=retries).text


def append_arrow_to_iceberg(
    context: dg.AssetExecutionContext,
    *,
    iceberg_table: IcebergTableResource,
    arrow: pa.Table,
    mode: str = "append",
    extra_metadata: dict[str, Any] | None = None,
) -> dg.MaterializeResult:
    """완성된 Arrow 테이블을 Iceberg에 적재한다.

    load_heavy_csv_gz_to_iceberg와 카탈로그 재구성 로직이 겹치지만, 원천이
    스트리밍 리더가 아니라 이미 만들어진 테이블이라 청크 루프가 없다.
    겹침은 2회째이므로 아직 공통 함수로 추출하지 않는다(Rule of Three).

    Args:
        context: 에셋 실행 컨텍스트.
        iceberg_table: 대상 테이블 바인딩 리소스.
        arrow: 적재할 Arrow 테이블. 스키마는 호출부가 명시한다.
        mode: "append"(누적) 또는 "replace"(기존 테이블 drop 후 재생성).
            🔴 **Flink 잡이 읽는 테이블에는 "replace"를 쓰지 않는다.**
            소스만이 아니라 **조인 상대도 포함**이다 — "스트리밍 소스가
            아니다"와 "잡이 읽지 않는다"는 다르다. `replace`는 `drop_table` 후
            재생성이라 ⓐ실행 중이면 대상이 사라지고 ⓑ재생성본은
            `parent_id = NULL`인 새 루트라 **스냅샷 계보가 끊겨**
            `IncrementalAppendScan`이 따라갈 조상을 잃는다.
            (`scripts/iceberg_changelog_probe.py` docstring 참조 —
            "멱등은 데이터 축이다. 계보를 매번 끊는 것은 별개다.")
        extra_metadata: 자산이 덧붙일 관측 메타데이터(행 수·분포 등).

    Returns:
        적재 메타데이터를 담은 MaterializeResult.
    """
    from pyiceberg.catalog import load_catalog

    properties = iceberg_table.config.model_dump()["properties"]
    catalog = load_catalog(iceberg_table.name, **properties)
    identifier = f"{iceberg_table.schema_}.{iceberg_table.table}"

    if mode == "replace" and table_exists(catalog, identifier):
        catalog.drop_table(identifier)

    table = ensure_table(catalog, identifier, arrow.schema)
    table.append(arrow)

    context.log.info("%s ← %d rows 적재 (mode=%s)", identifier, arrow.num_rows, mode)
    metadata: dict[str, Any] = {
        "table": identifier,
        "rows": arrow.num_rows,
        "mode": mode,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    return dg.MaterializeResult(metadata=metadata)
