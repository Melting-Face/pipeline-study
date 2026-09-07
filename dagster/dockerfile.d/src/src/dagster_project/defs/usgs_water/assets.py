"""USGS 수문(NWIS) bronze 적재 에셋 (공개 HTTP API → Iceberg).

두 자산의 원천 형식과 적재 모드가 다르다.
- water_iv_raw : 순간값(IV) **JSON** → **append**. Flink 스트리밍 소스가 될
  테이블이라 overwrite를 쓰면 안 된다(Iceberg Flink 소스는
  IncrementalAppendScan 기반이고, 실패 모드가 에러가 아니라 조용한 누락이다).
- water_sites  : 관측소 메타 **RDB**(탭 구분) → **replace**. 조인 차원이고
  스트리밍 소스가 아니라 갱신 방식에 제약이 없다.
  Site Service는 `format=json`에 HTTP 400을 준다(2026-09-05 실측).

🔴 IV는 매 실행이 LOOKBACK 창을 **다시** 긁는다. 최신만 받으면 관측점이 한
번씩만 들어와 중복 제거 실습 재료가 생기지 않는다. 창을 겹쳐 받아야 값
수정(qualifier "P" = Provisional data subject to revision)이 새 버전으로
들어온다. 단, 상태 전이(P→A)는 IV에서 관측되지 않는다 — 90일 전 구간도
qualifier가 100% "P"였다(2026-09-05 실측).

주의: Dagster가 context를 클래스 identity로 검사하므로, 자산 모듈에서는
`from __future__ import annotations`(어노테이션 문자열화)를 사용하지 않는다.
"""

from collections import Counter
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
from dagster_iceberg.resource import IcebergTableResource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.helper import (
    append_arrow_to_iceberg,
    fetch_json,
    fetch_text,
)
from dagster_project.defs.usgs_water.constants import (
    GROUP_NAME,
    HTTP_RETRIES,
    HTTP_TIMEOUT_S,
    IV_ENDPOINT,
    LOOKBACK,
    PARAMETER_CODES,
    SITE_ENDPOINT,
    TARGET_STATES,
)

# bronze 스키마 — 값은 **원본 문자열 그대로** 담는다(mimic_iv VALUE_AS_STRING과
# 같은 축). 숫자 캐스팅은 silver의 몫이다. 결측은 응답에서 noDataValue
# (-999999.0)로 오므로 값 자체를 해석하려면 unit_code가 함께 필요하다.
IV_SCHEMA = pa.schema(
    [
        ("site_no", pa.string()),
        ("station_nm", pa.string()),
        ("parameter_cd", pa.string()),
        ("unit_code", pa.string()),
        ("value", pa.string()),
        # 🔴 배열 그대로 보존한다. P 외에 Rat·ZFL·Ssn·Eqp가 함께 붙는 것을
        #    실측했고, 문자열로 접으면 그 조합이 손실된다.
        ("qualifiers", pa.list_(pa.string())),
        # 이벤트타임(관측 시각) — Flink 워터마크의 기준.
        ("date_time", pa.timestamp("us", tz="UTC")),
        # 처리시간(수집 시각) — dedup의 ORDER BY 키. date_time과 합치면
        # 둘 중 하나를 못 쓴다.
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)

# RDB 응답의 컬럼 중 조인·분석에 쓰는 것만 추린다(원본은 41열).
# RDB는 전 컬럼이 문자열이고 빈 값이 흔해 타입 강제를 하지 않는다.
SITE_COLUMNS = (
    "agency_cd",
    "site_no",
    "station_nm",
    "site_tp_cd",
    "dec_lat_va",
    "dec_long_va",
    "state_cd",
    "county_cd",
    "huc_cd",
    "drain_area_va",
    "alt_va",
    "tz_cd",
)

SITE_SCHEMA = pa.schema(
    [(name, pa.string()) for name in SITE_COLUMNS]
    + [("ingested_at", pa.timestamp("us", tz="UTC"))]
)


def _iv_json_to_arrow(payload: dict[str, Any], ingested_at: datetime) -> pa.Table:
    """NWIS IV JSON 응답을 bronze Arrow 테이블로 변환한다.

    외부 응답이므로 낙관적으로 읽지 않는다 — 없을 수 있는 키는 기본값을 준다.
    다만 site_no·parameter_cd·date_time은 **없으면 레코드가 성립하지 않으므로**
    KeyError로 드러나게 둔다(조용히 빈 값으로 채우면 dedup 키가 무너진다).

    Args:
        payload: IV 서비스의 JSON 응답.
        ingested_at: 이번 수집의 처리시간(모든 행에 같은 값이 들어간다).

    Returns:
        IV_SCHEMA를 따르는 Arrow 테이블(빈 응답이면 0행).
    """
    rows: list[dict[str, Any]] = []
    for series in payload.get("value", {}).get("timeSeries", []):
        source_info = series["sourceInfo"]
        variable = series["variable"]
        site_no = source_info["siteCode"][0]["value"]
        station_nm = source_info.get("siteName")
        parameter_cd = variable["variableCode"][0]["value"]
        unit_code = (variable.get("unit") or {}).get("unitCode")

        for block in series.get("values", []):
            for point in block.get("value", []):
                observed = datetime.fromisoformat(point["dateTime"])
                rows.append(
                    {
                        "site_no": site_no,
                        "station_nm": station_nm,
                        "parameter_cd": parameter_cd,
                        "unit_code": unit_code,
                        "value": point.get("value"),
                        "qualifiers": point.get("qualifiers") or [],
                        "date_time": observed.astimezone(timezone.utc),
                        "ingested_at": ingested_at,
                    }
                )
    return pa.Table.from_pylist(rows, schema=IV_SCHEMA)


def _rdb_to_arrow(text: str, ingested_at: datetime) -> pa.Table:
    """NWIS RDB(탭 구분) 응답을 bronze Arrow 테이블로 변환한다.

    RDB 형식은 세 부분이다.
      1. `#`로 시작하는 주석 헤더(여러 줄)
      2. 컬럼명 행(탭 구분)
      3. **컬럼 폭 지정자 행**(`5s`·`15s`…) — 데이터가 아니므로 버린다
      4. 데이터 행

    3번을 데이터로 읽으면 첫 관측소가 `site_no="15s"`인 유령 행이 된다.

    Args:
        text: RDB 응답 본문.
        ingested_at: 이번 수집의 처리시간.

    Returns:
        SITE_SCHEMA를 따르는 Arrow 테이블.
    """
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    header_and_spec = 2
    if len(lines) < header_and_spec:
        return pa.Table.from_pylist([], schema=SITE_SCHEMA)

    header = lines[0].split("\t")
    rows: list[dict[str, Any]] = []
    for line in lines[header_and_spec:]:
        record = dict(zip(header, line.split("\t"), strict=False))
        row: dict[str, Any] = {
            name: (record.get(name) or "").strip() or None for name in SITE_COLUMNS
        }
        row["ingested_at"] = ingested_at
        rows.append(row)
    return pa.Table.from_pylist(rows, schema=SITE_SCHEMA)


def _observation_metadata(arrow: pa.Table) -> dict[str, Any]:
    """적재 결과를 관측 메타데이터로 요약한다(행 수 외 분포·범위)."""
    if arrow.num_rows == 0:
        return {"sites": 0, "date_time_range": "empty", "qualifiers": "empty"}

    qualifier_counts = Counter(
        code
        for codes in arrow.column("qualifiers").to_pylist()
        for code in (codes or [])
    )
    bounds = pc.min_max(arrow.column("date_time")).as_py()
    return {
        "sites": len(set(arrow.column("site_no").to_pylist())),
        "date_time_range": f"{bounds['min'].isoformat()} ~ {bounds['max'].isoformat()}",
        "qualifiers": str(dict(qualifier_counts)),
    }


@dg.asset(group_name=GROUP_NAME, kinds={"python", "iceberg", "bronze"})
def water_iv_raw(
    context: AssetExecutionContext,
    usgs_water_iv_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """NWIS 순간값(IV)을 bronze Iceberg 테이블에 append 적재한다.

    LOOKBACK 창을 매 실행 다시 긁으므로 같은 (site_no, parameter_cd,
    date_time)이 여러 번 쌓인다. 이는 버그가 아니라 **의도**다 — 중복 제거와
    값 수정 관측이 다음 단계(Flink)의 실습 대상이다.
    """
    ingested_at = datetime.now(tz=timezone.utc)
    tables: list[pa.Table] = []

    for state in TARGET_STATES:
        payload = fetch_json(
            IV_ENDPOINT,
            {
                "format": "json",
                "stateCd": state,
                "parameterCd": ",".join(PARAMETER_CODES),
                "period": LOOKBACK,
                "siteStatus": "active",
            },
            timeout_s=HTTP_TIMEOUT_S,
            retries=HTTP_RETRIES,
        )
        table = _iv_json_to_arrow(payload, ingested_at)
        context.log.info("IV %s: %d rows", state, table.num_rows)
        tables.append(table)

    arrow = (
        pa.concat_tables(tables)
        if tables
        else pa.Table.from_pylist([], schema=IV_SCHEMA)
    )
    return append_arrow_to_iceberg(
        context,
        iceberg_table=usgs_water_iv_table,
        arrow=arrow,
        mode="append",
        extra_metadata={
            "states": ",".join(TARGET_STATES),
            "parameters": ",".join(PARAMETER_CODES),
            "lookback": LOOKBACK,
            "api_requests": len(TARGET_STATES),
            **_observation_metadata(arrow),
        },
    )


@dg.asset(group_name=GROUP_NAME, kinds={"python", "iceberg", "bronze"})
def water_sites(
    context: AssetExecutionContext,
    usgs_water_sites_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """NWIS 관측소 메타를 bronze Iceberg 테이블에 append 적재한다.

    스트림의 조인 상대(차원)다. site_no가 공통 키이고 위경도·유역(huc_cd)·
    유역면적을 함께 담는다.

    🔴 **replace를 쓰지 않는다.** 초기 설계는 "차원은 스트리밍 소스가 아니므로
    갱신 방식에 제약이 없다"였는데 이는 오판이었다 — **「소스가 아니다」와
    「잡이 읽지 않는다」는 다르다.** 조인 상대도 잡이 읽는다. `replace`는
    `drop_table` 후 재생성이라 두 가지가 동시에 깨진다.

      1. 스트리밍 잡이 도는 중 이 자산이 실행되면 **조인 대상이 사라진다**
      2. 재생성된 테이블은 `parent_id = NULL`인 **새 루트**라 스냅샷 계보가
         끊긴다 — `IncrementalAppendScan`이 따라갈 조상이 없어져 증분 읽기가
         구조적으로 불가능해진다

    그래서 매 실행이 전체 관측소를 다시 append한다. 중복이 누적되지만 대상이
    수백 행 규모라 감당 가능하고, 최신 판은 Flink에서 site_no별
    `ingested_at` 최대값으로 고른다(순간값과 같은 dedup 패턴).
    """
    ingested_at = datetime.now(tz=timezone.utc)
    tables: list[pa.Table] = []

    for state in TARGET_STATES:
        text = fetch_text(
            SITE_ENDPOINT,
            {
                "format": "rdb",
                "stateCd": state,
                "siteStatus": "active",
                "siteOutput": "expanded",
            },
            timeout_s=HTTP_TIMEOUT_S,
            retries=HTTP_RETRIES,
        )
        table = _rdb_to_arrow(text, ingested_at)
        context.log.info("Site %s: %d rows", state, table.num_rows)
        tables.append(table)

    arrow = (
        pa.concat_tables(tables)
        if tables
        else pa.Table.from_pylist([], schema=SITE_SCHEMA)
    )
    return append_arrow_to_iceberg(
        context,
        iceberg_table=usgs_water_sites_table,
        arrow=arrow,
        mode="append",
        extra_metadata={
            "states": ",".join(TARGET_STATES),
            "api_requests": len(TARGET_STATES),
            "sites": arrow.num_rows,
        },
    )
