"""Frankfurter 환율 bronze 적재 에셋 (공개 HTTP API → Iceberg, 일자 파티션).

이 저장소의 **첫 파티션 자산**이다. 원천이 날짜 하나를 받아 그날 값을 주므로
적재 단위도 하루다 — "테이블을 다시 만든다"가 아니라 "그날만 다시 받는다".

🔴 파티션 자산은 **재실행·백필이 전제**라 append를 쓰면 같은 날짜가 쌓인다
(중복 누적이 의도인 `usgs_water`와는 축이 반대다). 그렇다고 replace는
`drop_table`이라 스냅샷 계보를 끊는다. 그래서 파티션 범위만 교체하는
`replace_partition_in_iceberg`를 쓴다.

🔴 **요청한 날짜와 원천이 돌려준 날짜를 둘 다 담는다.** 상류 중앙은행은
영업일에만 고시하고, 원천은 주말 요청에 **직전 영업일 값을 조용히 돌려준다**(대조 실험
실측: 토요일 요청 → HTTP 200 · 통화 29개로 평일과 동일 · `date` 필드만
금요일). 상태코드로도 행 수로도 값의 범위로도 잡히지 않고 **`date` 필드
하나만 다르다.** 그래서 `rate_date`(요청)와 `source_date`(응답 에코)를
나란히 두고 일치 여부를 메타데이터에 남긴다 — 판정하지 않고 **관측**만 한다.
(원천 문서는 이 동작을 서술하지 않는다 — 실측으로만 확인된다.)

주의: Dagster가 context를 클래스 identity로 검사하므로, 자산 모듈에서는
`from __future__ import annotations`(어노테이션 문자열화)를 사용하지 않는다.
"""

from datetime import datetime, timezone
from typing import Any

import pyarrow as pa
from dagster_iceberg.resource import IcebergTableResource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.helper import fetch_json, replace_partition_in_iceberg
from dagster_project.defs.frankfurter_fx.constants import (
    GROUP_NAME,
    HTTP_RETRIES,
    HTTP_TIMEOUT_S,
    PARTITION_START_DATE,
    PARTITION_TIMEZONE,
    RATES_BASE,
)

# 일자 파티션 — 이 저장소의 첫 사례. 시작일 리터럴 고정·타임존 UTC의 근거는
# constants.py 주석 참조(둘 다 조용히 어긋나는 축이다).
DAILY_PARTITIONS = dg.DailyPartitionsDefinition(
    start_date=PARTITION_START_DATE,
    timezone=PARTITION_TIMEZONE,
)

# bronze 스키마 — 응답의 `rates` 객체를 **long 형태로 편다**.
# 통화가 늘고 줄어도 스키마가 그대로라 재적재가 필요 없다(wide면 컬럼이 바뀐다).
#
# `rate`만 float64인 이유: 원천이 JSON **수치**로 준다. 문자열로 담는 것이
# 원문 보존인 다른 데이터셋(usgs_water·mimic_iv)과 축이 다르다 — 저쪽은
# 원천이 문자열이라 문자열이 원문이고, 여기서는 문자열화가 오히려 변환이다.
FX_SCHEMA = pa.schema(
    [
        # 우리가 요청한 날짜 = Dagster 파티션 키. 파티션 교체의 필터 대상이다.
        ("rate_date", pa.string()),
        # 원천이 응답에 에코한 날짜. rate_date와 다를 수 있다(위 docstring).
        ("source_date", pa.string()),
        ("base_currency", pa.string()),
        ("quote_currency", pa.string()),
        ("rate", pa.float64()),
        # 처리시간(수집 시각).
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


def _rates_json_to_arrow(
    payload: dict[str, Any],
    rate_date: str,
    ingested_at: datetime,
) -> pa.Table:
    """Frankfurter 응답을 bronze Arrow 테이블로 편다.

    응답 형태는 최상위 4필드다(실응답 실측):
    `amount`(number) · `base`(string) · `date`(string) · `rates`(object).

    외부 응답이므로 낙관적으로 읽지 않는다. 다만 `date`·`base`는 **없으면
    레코드가 성립하지 않으므로** KeyError로 드러나게 둔다 — 조용히 빈 값으로
    채우면 `source_date` 축(요청↔응답 대조)이 통째로 무의미해진다.

    Args:
        payload: Frankfurter 응답 JSON.
        rate_date: 이번 파티션 키(= 요청한 날짜, "YYYY-MM-DD").
        ingested_at: 이번 수집의 처리시간(모든 행에 같은 값이 들어간다).

    Returns:
        FX_SCHEMA를 따르는 Arrow 테이블(통화당 1행, 빈 응답이면 0행).
    """
    source_date = payload["date"]
    base_currency = payload["base"]
    rows = [
        {
            "rate_date": rate_date,
            "source_date": source_date,
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "rate": float(rate),
            "ingested_at": ingested_at,
        }
        # 정렬해 담는다 — 같은 파티션을 다시 받아도 행 순서가 흔들리지 않아
        # 스냅샷 간 비교가 쉬워진다(dict 순서에 기대지 않는다).
        for quote_currency, rate in sorted((payload.get("rates") or {}).items())
    ]
    return pa.Table.from_pylist(rows, schema=FX_SCHEMA)


@dg.asset(
    group_name=GROUP_NAME,
    partitions_def=DAILY_PARTITIONS,
    kinds={"python", "iceberg", "bronze"},
)
def fx_rates_daily(
    context: AssetExecutionContext,
    frankfurter_fx_rates_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """환율 하루치를 bronze Iceberg 테이블에 파티션 단위로 적재한다.

    같은 파티션을 여러 번 실행해도 **행 수가 늘지 않는다**(멱등). 이것이
    `usgs_water`와 갈리는 지점이고, 파티션 자산의 최소 요건이다.
    """
    rate_date = context.partition_key
    ingested_at = datetime.now(tz=timezone.utc)

    payload = fetch_json(
        f"{RATES_BASE}/{rate_date}",
        {},
        timeout_s=HTTP_TIMEOUT_S,
        retries=HTTP_RETRIES,
    )
    arrow = _rates_json_to_arrow(payload, rate_date, ingested_at)

    source_date = payload["date"]
    context.log.info(
        "FX %s: %d rows (source_date=%s)", rate_date, arrow.num_rows, source_date
    )

    return replace_partition_in_iceberg(
        context,
        iceberg_table=frankfurter_fx_rates_table,
        arrow=arrow,
        partition_column="rate_date",
        partition_value=rate_date,
        extra_metadata={
            "source_date": source_date,
            # 🔴 이 셀이 주말·휴장일 대체를 드러내는 유일한 관측점이다.
            # 판정하지 않고 값만 남긴다(data-quality.md의 **관측** 등급).
            "date_matches_request": dg.MetadataValue.bool(source_date == rate_date),
            "base_currency": payload["base"],
            "quote_currencies": arrow.num_rows,
            # amount는 환율의 기준 수량이다. 1이 아니면 rate의 의미가 달라지므로
            # 값을 남겨 조용한 변경을 드러낸다.
            "amount": payload.get("amount"),
            "api_requests": 1,
        },
    )
