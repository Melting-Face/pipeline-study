"""FRED(ALFRED) 경제지표 bronze 적재 에셋 (공개 HTTP API → Iceberg).

🔴 **이 데이터셋의 존재 이유는 vintage다.** 지표는 발표 뒤 개정되므로 "오늘 아는
값"을 과거 날짜에 붙이면 **그때 존재하지 않던 정보**를 쓰는 것이 된다. 실측:
UNRATE 2025-11-01은 2026-01-08까지 `4.6`이었다가 2026-01-09부터 `4.5`가 됐고,
CPIAUCSL은 관측일 44건 중 35건이 개정됐다(DGS10은 974건 중 0건 — 일별 금리는
개정되지 않는다).

🔴 **파티션 축이 시리즈다 — 날짜가 아니다.**
    계획 초안은 `vintage_date` 일자 파티션이었는데, vintage 하루를 고정하면 원천이
    **전체 히스토리를 통째로** 준다(DGS10 실측 16,544건). 그대로 두면
    `히스토리 x 시리즈 x 일수`로 같은 값을 매일 복제하는 2차 증가가 된다.

    ALFRED에는 이를 위한 표현이 이미 있다 — realtime을 **구간**으로 주면 값마다
    "유효했던 기간"이 한 행에 담겨 오고, **개정이 있었던 관측일만** 여러 행이 된다.
    그래서 시리즈당 1요청으로 전 vintage를 받고 `series_id`로 파티션을 나눈다.
    저장이 선형이 되고 as-of 조인은 구간 비교가 된다.

`releases/dates`는 반대로 **하루 스냅샷**이라 일자 파티션이 맞다(실측 ~35건/일).

주의: Dagster가 context를 클래스 identity로 검사하므로, 자산 모듈에서는
`from __future__ import annotations`(어노테이션 문자열화)를 사용하지 않는다.
"""

from datetime import datetime, timezone
from typing import Any

import pyarrow as pa
from dagster_iceberg.resource import IcebergTableResource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.fred import FredResource
from dagster_project.common.helper import replace_partition_in_iceberg
from dagster_project.defs.fred_calendar.constants import (
    GROUP_NAME,
    OBSERVATIONS_PATH,
    PARTITION_START_DATE,
    PARTITION_TIMEZONE,
    REALTIME_OPEN_END,
    REALTIME_START,
    RELEASES_DATES_PATH,
    RELEASES_PAGE_LIMIT,
    SERIES_IDS,
)

# 🔴 시리즈 파티션 — 이 저장소의 **첫 정적 파티션 자산**이다. 집합이 상수에서
# 오므로 시리즈를 빼면 그 파티션이 사라진다(적재된 행은 테이블에 남는다).
SERIES_PARTITIONS = dg.StaticPartitionsDefinition(list(SERIES_IDS))

# 릴리스 일정은 하루 단위 스냅샷이라 일자 파티션이다.
DAILY_PARTITIONS = dg.DailyPartitionsDefinition(
    start_date=PARTITION_START_DATE,
    timezone=PARTITION_TIMEZONE,
)

# 🔴 **프로브와 짝을 이루는 상수다**(`scripts/stock_source_access_probe.py`).
# 프로브는 PEP 723 단독 실행이라 이 패키지를 import할 수 없어 상수가 두 벌인데,
# 갈라지면 **프로브는 통과하고 파서만 깨진다**. `tests/test_fred_calendar_parse.py`의
# AST 대조가 그 갈라짐을 막는다(의도한 결합).
#
# `realtime_start`가 필수인 것이 이 데이터셋의 급소다 — 빠지면 vintage 축이 사라지고
# as-of 조인이 아무 값이나 집는다. 결과는 그럴듯하고 틀리다.
REQUIRED_OBSERVATION_FIELDS = ("date", "realtime_end", "realtime_start", "value")
REQUIRED_RELEASE_FIELDS = ("date", "release_id", "release_name")

# 아는 전체 필드 집합(필수와 축이 다르다 — 이쪽은 "아는 전부").
KNOWN_OBSERVATION_FIELDS = ("date", "realtime_end", "realtime_start", "value")
KNOWN_RELEASE_FIELDS = (
    "date",
    "release_id",
    "release_last_updated",
    "release_name",
)

# bronze 스키마 — 관측치 하나의 **한 vintage 구간**이 한 행이다.
#
# 수치를 문자열로 담는 이유: 원천이 문자열로 준다(`"4.41"`). 그리고 결측을
# **`"."` 로 표기**하는데, 이것을 null로 바꾸면 "발표 안 됨"·"값이 0"·"파싱 실패"가
# 한 칸에 섞인다. 원문 보존이 bronze의 일이고 해석은 silver가 한다
# (usgs_water·mimic_iv와 같은 판단, frankfurter와는 반대 — 저쪽은 원천이 수치다).
OBSERVATION_SCHEMA = pa.schema(
    [
        # 파티션 키. 🔴 응답 본문에 없다 — 요청 파라미터로만 존재하므로 파서가 넣는다.
        ("series_id", pa.string()),
        ("observation_date", pa.string()),
        # 🔴 이 값이 유효해진 날. 요청 구간 시작일로 **클리핑**된다(constants 주석).
        ("realtime_start", pa.string()),
        # 유효 종료일. 열린 구간은 "9999-12-31"로 온다.
        ("realtime_end", pa.string()),
        ("value", pa.string()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)

# bronze 스키마 — 그날 유효했던 릴리스 한 건이 한 행이다.
RELEASE_SCHEMA = pa.schema(
    [
        # 파티션 키 = 우리가 요청한 하루.
        ("as_of_date", pa.string()),
        ("release_id", pa.int64()),
        ("release_name", pa.string()),
        # 원천이 준 릴리스 날짜. as_of_date와 다를 수 있다(관측 대상).
        ("release_date", pa.string()),
        ("release_last_updated", pa.string()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


def _assert_not_truncated(payload: dict[str, Any], rows: int, label: str) -> None:
    """응답이 신고한 `count`와 실제 행 수를 대조한다.

    🔴 페이징을 안 따라가 잘린 응답은 **행이 생기고 수만 모자란다** — 상태코드도
    정상이고 스키마도 맞아서 어디서도 에러가 나지 않는다. 원천이 스스로 신고한
    수가 유일한 관측점이라 그것과 대조한다.

    Args:
        payload: 응답 JSON(`count` 포함).
        rows: 실제로 담은 행 수.
        label: 오류 메시지에 쓸 대상 이름.

    Raises:
        RuntimeError: `count`가 행 수보다 클 때.
    """
    count = payload.get("count")
    if count is not None and count > rows:
        message = (
            f"{label}: 응답이 {count}건을 신고했는데 {rows}건만 왔다 — 잘렸다. "
            "limit을 올리거나 페이징을 구현한다."
        )
        raise RuntimeError(message)


def _observations_to_arrow(
    payload: dict[str, Any],
    series_id: str,
    ingested_at: datetime,
) -> pa.Table:
    """ALFRED 관측치 응답을 bronze Arrow 테이블로 편다.

    응답의 `observations`는 `{date, realtime_start, realtime_end, value}` 목록이고,
    **개정된 관측일은 vintage마다 한 항목씩** 나온다. 그 여러 행을 하나로 접지
    않는 것이 이 파서의 핵심이다 — 접으면 PIT 조인이 불가능해진다.

    Args:
        payload: `series/observations` 응답 JSON.
        series_id: 이번 파티션 키(응답에 없어 여기서 채운다).
        ingested_at: 이번 수집의 처리시간.

    Returns:
        OBSERVATION_SCHEMA를 따르는 Arrow 테이블.

    Raises:
        KeyError: `REQUIRED_OBSERVATION_FIELDS` 중 하나라도 없을 때.
        RuntimeError: 응답이 잘렸을 때.
    """
    rows = [
        {
            "series_id": series_id,
            "observation_date": observation["date"],
            "realtime_start": observation["realtime_start"],
            "realtime_end": observation["realtime_end"],
            "value": observation["value"],
            "ingested_at": ingested_at,
        }
        for observation in payload.get("observations") or []
    ]
    # 관측일 → vintage 순으로 고정한다(재실행 시 순서가 흔들리지 않게).
    rows.sort(key=lambda row: (row["observation_date"], row["realtime_start"]))
    _assert_not_truncated(payload, len(rows), f"{series_id} 관측치")
    return pa.Table.from_pylist(rows, schema=OBSERVATION_SCHEMA)


def _release_dates_to_arrow(
    payload: dict[str, Any],
    as_of_date: str,
    ingested_at: datetime,
) -> pa.Table:
    """릴리스 일정 응답을 bronze Arrow 테이블로 편다.

    Args:
        payload: `releases/dates` 응답 JSON.
        as_of_date: 이번 파티션 키(= 요청한 하루).
        ingested_at: 이번 수집의 처리시간.

    Returns:
        RELEASE_SCHEMA를 따르는 Arrow 테이블(발표가 없는 날은 0행).

    Raises:
        KeyError: `REQUIRED_RELEASE_FIELDS` 중 하나라도 없을 때.
        RuntimeError: 응답이 잘렸을 때.
    """
    rows = [
        {
            "as_of_date": as_of_date,
            "release_id": int(entry["release_id"]),
            "release_name": entry["release_name"],
            "release_date": entry["date"],
            # 없어도 레코드가 성립한다(필수 축과 다르다).
            "release_last_updated": entry.get("release_last_updated") or "",
            "ingested_at": ingested_at,
        }
        for entry in payload.get("release_dates") or []
    ]
    rows.sort(key=lambda row: (row["release_date"], row["release_id"]))
    _assert_not_truncated(payload, len(rows), f"{as_of_date} 릴리스 일정")
    return pa.Table.from_pylist(rows, schema=RELEASE_SCHEMA)


@dg.asset(
    group_name=GROUP_NAME,
    partitions_def=SERIES_PARTITIONS,
    kinds={"python", "iceberg", "bronze"},
)
def fred_series_observations(
    context: AssetExecutionContext,
    fred: FredResource,
    fred_calendar_observations_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """한 시리즈의 **전 vintage**를 bronze 테이블에 파티션 단위로 적재한다.

    파티션이 시리즈라 재실행이 그 시리즈의 행만 교체한다 — 다른 시리즈는 건드리지
    않고, 같은 시리즈를 다시 받아도 행 수가 늘지 않는다.

    🔴 매 실행이 **구간 전체를 다시 받는다**(증분이 아니다). 개정은 과거 관측일의
    구간을 쪼개므로, 증분으로 뒤만 붙이면 이미 적재된 열린 구간
    (`realtime_end="9999-12-31"`)이 닫히지 않고 남아 **as-of 조인이 두 값을 집는다.**
    전량 교체가 그 축을 닫는다.
    """
    series_id = context.partition_key
    ingested_at = datetime.now(tz=timezone.utc)

    payload = fred.fetch(
        OBSERVATIONS_PATH,
        {
            "series_id": series_id,
            "realtime_start": REALTIME_START,
            "realtime_end": REALTIME_OPEN_END,
        },
    )
    arrow = _observations_to_arrow(payload, series_id, ingested_at)

    dates = arrow.column("observation_date").to_pylist()
    starts = arrow.column("realtime_start").to_pylist()
    revised = len(dates) - len(set(dates))
    ends = arrow.column("realtime_end").to_pylist()
    open_ended = sum(1 for end in ends if end == REALTIME_OPEN_END)
    context.log.info(
        "FRED %s: %d행 / 관측일 %d (개정 %d)",
        series_id,
        arrow.num_rows,
        len(set(dates)),
        revised,
    )

    return replace_partition_in_iceberg(
        context,
        iceberg_table=fred_calendar_observations_table,
        arrow=arrow,
        partition_column="series_id",
        partition_value=series_id,
        extra_metadata={
            "observation_dates": len(set(dates)),
            # 🔴 관측 셀 — 이 수가 0이면 그 시리즈는 개정되지 않는다는 뜻이고,
            # vintage 축이 그 시리즈에서는 값을 하지 않는다(DGS10이 그렇다).
            "revised_rows": revised,
            "open_ended_rows": open_ended,
            "earliest_realtime_start": min(starts) if starts else "(없음)",
            "response_count": payload.get("count"),
            "api_requests": 1,
        },
    )


@dg.asset(
    group_name=GROUP_NAME,
    partitions_def=DAILY_PARTITIONS,
    kinds={"python", "iceberg", "bronze"},
)
def fred_release_dates(
    context: AssetExecutionContext,
    fred: FredResource,
    fred_calendar_releases_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """하루치 릴리스 일정을 bronze 테이블에 파티션 단위로 적재한다.

    realtime을 그 하루로 고정해 **그 시점에 유효했던** 일정을 받는다. 일정이
    사후에 옮겨져도 당시 스냅샷이 남는다.

    ⚠️ 이것은 **발표가 일어난 날의 기록**이지 앞으로의 예정표가 아니다
    (실측: realtime을 하루로 고정하면 `date`가 전부 그 하루로 온다).
    "다음 CPI까지 며칠" 같은 피처는 이 테이블을 시계열로 훑어 silver에서 만든다.
    """
    as_of_date = context.partition_key
    ingested_at = datetime.now(tz=timezone.utc)

    payload = fred.fetch(
        RELEASES_DATES_PATH,
        {
            "realtime_start": as_of_date,
            "realtime_end": as_of_date,
            "limit": str(RELEASES_PAGE_LIMIT),
            "include_release_dates_with_no_data": "true",
        },
    )
    arrow = _release_dates_to_arrow(payload, as_of_date, ingested_at)

    release_dates = set(arrow.column("release_date").to_pylist())
    context.log.info("FRED 릴리스 %s: %d건", as_of_date, arrow.num_rows)

    return replace_partition_in_iceberg(
        context,
        iceberg_table=fred_calendar_releases_table,
        arrow=arrow,
        partition_column="as_of_date",
        partition_value=as_of_date,
        extra_metadata={
            "release_dates": ", ".join(sorted(release_dates)) or "(없음)",
            # 🔴 요청한 하루와 응답의 날짜가 갈리는지 관측한다(판정하지 않는다).
            "date_matches_request": dg.MetadataValue.bool(
                release_dates in ({as_of_date}, set())
            ),
            "response_count": payload.get("count"),
            "api_requests": 1,
        },
    )
