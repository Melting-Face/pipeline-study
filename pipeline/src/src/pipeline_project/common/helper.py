"""원천 → Iceberg 적재 헬퍼 (데이터셋 무관 공통 · 오케스트레이터 중립).

**S3 csv.gz** 경로 두 가지:
- read_csv_gz_table: 일반(부하 없는) 파일을 통째로 읽어 pa.Table 반환
  → 호출부가 `catalog.write_arrow_to_iceberg`로 적재한다.
- load_heavy_csv_gz_to_iceberg: 대용량 csv.gz(예: 3.3GB)를 boto3 스트리밍 +
  청크 append로 메모리를 일정하게 유지하며 적재.

**외부 HTTP API** 경로 두 가지:
- fetch_json / fetch_text: 공개 API GET(재시도 포함). 응답 본문은 신뢰하지 않는
  외부 데이터이므로 호출부가 스키마를 명시해 변환한다.

🔴 **Dagster 트리와 갈리는 지점 셋**(이식 시 시그니처가 바뀐 이유):
1. `context: dg.AssetExecutionContext` → **`logger: logging.Logger`**.
   Airflow에는 `context.log`의 대응물이 없고, 태스크 로거는 표준 `logging`이다.
2. `dg.MaterializeResult(metadata=...)` → **`dict[str, Any]` 반환**.
   관측 메타데이터를 남기는 규약은 그대로다 — **값은 같고 그릇만 바뀐다**
   (호출부가 `ti.xcom_push`·`outlet_events` 어디에 싣든 이 dict를 쓴다).
3. `IcebergTableResource`(테이블 바인딩 리소스) → **`namespace`·`table` 인자**.
   카탈로그 접속은 `common/catalog.py`가 단일 출처로 갖는다.
"""

import logging
import time
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.csv as pacsv
import requests
from botocore.client import BaseClient

from pipeline_project.common.catalog import ensure_table, load_catalog, table_exists
from pipeline_project.common.constants import (
    DEFAULT_CHUNK_ROWS,
    aws_region,
    s3_access_key_id,
    s3_endpoint,
    s3_secret_access_key,
)

# 대용량 청크 적재에서 허용하는 모드. `overwrite`·`replace_partition`은 **청크
# 단위로 표현되지 않는다** — 첫 청크가 전량을 덮어쓴 뒤 나머지를 append하는
# 변형이 이론상 가능하나, 원천이 0행이면 flush가 한 번도 돌지 않아 **기존
# 데이터가 조용히 살아남는다**(원칙 7: 조용한 실패가 최악이다).
# ⇒ 그 변형은 「빈 원천」 테스트와 함께 들어와야 하므로 지금은 닫아 둔다.
HEAVY_WRITE_MODES = ("append", "recreate")


def build_s3_client() -> BaseClient:
    """S3(SeaweedFS) 클라이언트를 만든다.

    원본에서는 dagster-aws `S3Resource`가 이 역할을 했다. 리소스 시스템이 없는
    트리에서는 **평범한 boto3 클라이언트**가 그 자리를 대신하고, 자격증명 규칙
    (`ICEBERG_S3_*` 우선 · 미설정 시 `AWS_*` 폴백)은 `common/constants.py`가
    단일 출처로 갖는다.

    ⚠️ 클라이언트를 캐시하지 않는다 — `constants`가 지연 평가라 태스크가 바뀐 env를
    보는 경로를 막지 않는다(객체 생성 비용은 접속이 아니라 서명자 초기화뿐이다).

    Returns:
        boto3 S3 클라이언트.
    """
    return boto3.client(
        "s3",
        endpoint_url=s3_endpoint(),
        aws_access_key_id=s3_access_key_id(),
        aws_secret_access_key=s3_secret_access_key(),
        region_name=aws_region(),
    )


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """s3://bucket/key → (bucket, key)."""
    if not uri.startswith("s3://"):
        message = f"s3:// URI가 아님: {uri}"
        raise ValueError(message)
    bucket, _, key = uri[len("s3://") :].partition("/")
    return bucket, key


def open_csv_gz_stream(
    s3_client: BaseClient,
    source_uri: str,
    column_types: dict[str, pa.DataType] | None = None,
) -> pacsv.CSVStreamingReader:
    """boto3로 s3 객체를 받아 gzip 해제 스트림을 pyarrow CSV 리더로 연다.

    Args:
        s3_client: boto3 S3 클라이언트(`build_s3_client`).
        source_uri: 원본 csv.gz의 s3 URI.
        column_types: 특정 컬럼의 추론 타입을 강제할 매핑(예: 자유형 value 컬럼을
            string으로 고정). 미지정 컬럼은 pyarrow가 자동 추론한다.
    """
    bucket, key = parse_s3_uri(source_uri)
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"]
    # StreamingBody(file-like)를 pyarrow로 감싸 gzip 스트리밍 해제
    stream = pa.CompressedInputStream(pa.PythonFile(body, mode="r"), "gzip")
    convert_options = (
        pacsv.ConvertOptions(column_types=column_types) if column_types else None
    )
    return pacsv.open_csv(stream, convert_options=convert_options)


def read_csv_gz_table(
    s3_client: BaseClient,
    source_uri: str,
    column_types: dict[str, pa.DataType] | None = None,
) -> pa.Table:
    """일반 파일을 통째로 읽어 Arrow 테이블로 반환한다.

    대용량 파일에는 사용하지 말 것(전량 메모리 적재). 그 경우
    load_heavy_csv_gz_to_iceberg를 쓴다.

    Args:
        s3_client: boto3 S3 클라이언트.
        source_uri: 원본 csv.gz의 s3 URI.
        column_types: 컬럼 타입 강제 매핑(선택). 자유형 문자열 컬럼이 숫자로
            잘못 추론되는 것을 막을 때 쓴다.
    """
    reader = open_csv_gz_stream(s3_client, source_uri, column_types=column_types)
    return reader.read_all()


def load_heavy_csv_gz_to_iceberg(
    logger: logging.Logger,
    *,
    s3_client: BaseClient,
    namespace: str,
    table: str,
    source_uri: str,
    mode: str,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
    column_types: dict[str, pa.DataType] | None = None,
) -> dict[str, Any]:
    """대용량 csv.gz를 청크 단위로 Iceberg 테이블에 적재한다.

    전량을 메모리에 올리지 않는다 — boto3 스트리밍 응답을 pyarrow CSV 리더로
    흘리며 `chunk_rows`마다 append한다.

    🔴 **`mode`에 기본값이 없다.** 원본은 `mode: str = "replace"`였고 그
    `"replace"`가 `drop_table`이었다 — 즉 **아무것도 적지 않은 호출이 스냅샷
    계보를 끊는** 기본값이었다. 지금은 호출부가 반드시 고른다.

    Args:
        logger: 진행 로그를 남길 로거.
        s3_client: boto3 S3 클라이언트.
        namespace: 대상 Iceberg 네임스페이스.
        table: 대상 테이블명.
        source_uri: 원본 csv.gz의 s3 URI.
        mode: `"append"`(누적) 또는 `"recreate"`(drop 후 재적재).
            🔴 `recreate`는 계보를 끊는다 — `catalog.WriteMode` 주석 참조.
            ⚠️ `recreate`로 돌다 원천이 0행이면 **테이블이 drop된 채 남는다**
            (한 청크도 flush되지 않아 재생성이 일어나지 않는다).
        chunk_rows: 한 번에 append 할 행 수.
        column_types: 컬럼 타입 강제 매핑(선택). 자유형 value 컬럼(chartevents·
            labevents)이 청크마다 다른 타입으로 추론돼 스키마가 어긋나는 것을 막는다.

    Returns:
        적재 메타데이터(테이블·원본·행 수·모드).

    Raises:
        ValueError: 청크 경로가 표현할 수 없는 모드일 때.
    """
    if mode not in HEAVY_WRITE_MODES:
        message = (
            f"대용량 청크 경로가 지원하지 않는 모드: {mode!r} "
            f"(가능: {', '.join(HEAVY_WRITE_MODES)})"
        )
        raise ValueError(message)

    catalog = load_catalog()
    identifier = f"{namespace}.{table}"

    if mode == "recreate" and table_exists(catalog, identifier):
        catalog.drop_table(identifier)

    reader = open_csv_gz_stream(s3_client, source_uri, column_types=column_types)
    target = None
    pending: list[pa.RecordBatch] = []
    pending_rows = 0
    total_rows = 0

    def flush() -> None:
        nonlocal target, pending, pending_rows
        if not pending:
            return
        arrow = pa.Table.from_batches(pending)
        if target is None:
            target = ensure_table(catalog, identifier, arrow.schema)
        target.append(arrow)
        pending = []
        pending_rows = 0

    for batch in reader:
        pending.append(batch)
        pending_rows += batch.num_rows
        total_rows += batch.num_rows
        if pending_rows >= chunk_rows:
            flush()
    flush()

    logger.info(
        "%s ← %s 적재 완료 (%d rows, mode=%s)",
        identifier,
        source_uri,
        total_rows,
        mode,
    )
    return {
        "table": identifier,
        "source_uri": source_uri,
        "rows": total_rows,
        "mode": mode,
    }


def _request(
    url: str,
    params: dict[str, str],
    *,
    timeout_s: int,
    retries: int,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    """공개 API를 GET하고 성공 응답을 반환한다(429·5xx만 백오프 재시도).

    그 외 4xx는 **즉시 실패**시킨다 — 잘못된 요청을 반복해도 답이 바뀌지 않고
    rate limit 예산만 쓴다. `Retry-After` 헤더가 오면 계산된 백오프보다
    그것을 우선한다.

    ⚠️ **크리덴셜을 쿼리 파라미터로 받는 API에는 이 함수를 그대로 쓰지 않는다.**
    `requests`의 `HTTPError`·`ConnectionError` 메시지에는 **쿼리스트링을 포함한
    전체 URL**이 담겨, 4xx 한 번에 키가 태스크 로그에 평문으로 박힌다.
    ⇒ 그래서 **`headers`가 있다.** 키를 헤더로 보내는 API(예: Polygon의
    `Authorization: Bearer`)는 쿼리스트링에 크리덴셜이 실리지 않아 이 축이 아예
    닫힌다. 키가 필요한 원천은 **가능하면 헤더 경로를 고르고**, 쿼리 파라미터뿐인
    원천(예: FRED `?api_key=`)만 호출부에서 예외 재포장·마스킹을 함께 넣는다.

    ⚠️ `headers`는 **재시도마다 그대로 다시 보낸다.** 일회용 토큰에는 맞지 않는다.
    """
    for attempt in range(retries + 1):
        response = requests.get(url, params=params, headers=headers, timeout=timeout_s)
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
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """공개 API에서 JSON을 받아 파싱해 반환한다.

    `headers`는 키를 헤더로 보내는 원천용이다 — 근거는 `_request` 독스트링.
    """
    return _request(
        url, params, timeout_s=timeout_s, retries=retries, headers=headers
    ).json()


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
