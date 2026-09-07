"""USGS 수문 응답 파서 단위 테스트.

이 저장소의 Dagster 자산은 대부분 실인프라(S3·Iceberg·K8s)에 붙어 단위 테스트
대상이 아니다. 그러나 `_iv_json_to_arrow`·`_rdb_to_arrow`는 **입력이 문자열/
dict이고 출력이 Arrow 테이블인 순수 함수**라 인프라 없이 검증된다.

픽스처는 2026-09-05 실측 응답을 축소한 것이다(구조는 그대로, 행 수만 줄임).
"""

from datetime import datetime, timezone

import pyarrow as pa
from dagster_project.defs.usgs_water.assets import (
    IV_SCHEMA,
    SITE_SCHEMA,
    _iv_json_to_arrow,
    _rdb_to_arrow,
)

INGESTED_AT = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)

IV_PAYLOAD = {
    "value": {
        "timeSeries": [
            {
                "sourceInfo": {
                    "siteName": "ROCK CREEK AT SHERRILL DRIVE WASHINGTON, DC",
                    "siteCode": [{"value": "01648000", "agencyCode": "USGS"}],
                },
                "variable": {
                    "variableCode": [{"value": "00060", "network": "NWIS"}],
                    "unit": {"unitCode": "ft3/s"},
                    "noDataValue": -999999.0,
                },
                "values": [
                    {
                        "value": [
                            {
                                "value": "81.7",
                                "qualifiers": ["P"],
                                "dateTime": "2026-03-19T10:15:00.000-04:00",
                            },
                            {
                                "value": "82.0",
                                "qualifiers": ["P", "Eqp"],
                                "dateTime": "2026-03-19T10:30:00.000-04:00",
                            },
                        ]
                    }
                ],
            }
        ]
    }
}

# RDB는 주석(#) → 컬럼명 행 → **폭 지정자 행** → 데이터 행 순서다.
RDB_TEXT = (
    "#\n"
    "# US Geological Survey\n"
    "#\n"
    "agency_cd\tsite_no\tstation_nm\tsite_tp_cd\tdec_lat_va\tdec_long_va\t"
    "state_cd\tcounty_cd\thuc_cd\tdrain_area_va\talt_va\ttz_cd\n"
    "5s\t15s\t50s\t7s\t16s\t16s\t2s\t3s\t16s\t8s\t8s\t6s\n"
    "USGS\t01647600\tPOTOMAC RIVER AT WISCONSIN AVE\tES\t38.9033611\t-77.0676667\t"
    "11\t001\t02070010\t\t 0.00\tEST\n"
)


def test_iv_flattens_points_into_rows() -> None:
    """시계열 안의 관측점이 각각 한 행이 된다."""
    table = _iv_json_to_arrow(IV_PAYLOAD, INGESTED_AT)

    assert table.num_rows == 2
    assert table.schema == IV_SCHEMA
    assert table.column("site_no").to_pylist() == ["01648000", "01648000"]
    assert table.column("parameter_cd").to_pylist() == ["00060", "00060"]
    assert table.column("unit_code").to_pylist() == ["ft3/s", "ft3/s"]
    assert table.column("value").to_pylist() == ["81.7", "82.0"]


def test_iv_converts_offset_to_utc() -> None:
    """응답의 -04:00 오프셋이 UTC로 변환된다(저장은 UTC 규약)."""
    table = _iv_json_to_arrow(IV_PAYLOAD, INGESTED_AT)

    observed = table.column("date_time").to_pylist()
    assert observed[0] == datetime(2026, 3, 19, 14, 15, tzinfo=timezone.utc)
    assert observed[1] == datetime(2026, 3, 19, 14, 30, tzinfo=timezone.utc)


def test_iv_keeps_qualifiers_as_list() -> None:
    """qualifier는 배열로 보존된다 — P 외 코드가 함께 붙는 조합이 실재한다."""
    table = _iv_json_to_arrow(IV_PAYLOAD, INGESTED_AT)

    assert table.column("qualifiers").to_pylist() == [["P"], ["P", "Eqp"]]


def test_iv_stamps_same_ingested_at_on_every_row() -> None:
    """처리시간은 수집 단위로 하나다(dedup의 ORDER BY 키가 된다)."""
    table = _iv_json_to_arrow(IV_PAYLOAD, INGESTED_AT)

    assert set(table.column("ingested_at").to_pylist()) == {INGESTED_AT}


def test_iv_empty_payload_keeps_schema() -> None:
    """빈 응답도 스키마를 지킨 0행 테이블이어야 concat이 깨지지 않는다."""
    table = _iv_json_to_arrow({"value": {"timeSeries": []}}, INGESTED_AT)

    assert table.num_rows == 0
    assert table.schema == IV_SCHEMA
    assert pa.concat_tables([table, table]).num_rows == 0


def test_rdb_skips_the_width_spec_row() -> None:
    """🔴 폭 지정자 행(`5s`·`15s`…)을 데이터로 읽지 않는다.

    읽으면 site_no="15s"인 유령 관측소가 차원 테이블에 들어가고, 조인에서
    조용히 매칭 실패로 나타난다.
    """
    table = _rdb_to_arrow(RDB_TEXT, INGESTED_AT)

    assert table.num_rows == 1
    assert table.column("site_no").to_pylist() == ["01647600"]
    assert "15s" not in table.column("site_no").to_pylist()


def test_rdb_parses_join_columns() -> None:
    """조인·분석에 쓰는 컬럼이 값을 담는다."""
    table = _rdb_to_arrow(RDB_TEXT, INGESTED_AT)

    row = table.to_pylist()[0]
    assert row["station_nm"] == "POTOMAC RIVER AT WISCONSIN AVE"
    assert row["huc_cd"] == "02070010"
    assert row["dec_lat_va"] == "38.9033611"
    assert row["ingested_at"] == INGESTED_AT


def test_rdb_blank_becomes_none() -> None:
    """빈 칸은 빈 문자열이 아니라 None이다(공백만 있는 칸 포함)."""
    table = _rdb_to_arrow(RDB_TEXT, INGESTED_AT)

    row = table.to_pylist()[0]
    assert row["drain_area_va"] is None
    assert row["alt_va"] == "0.00"  # " 0.00" → strip 후 보존


def test_rdb_header_only_returns_empty() -> None:
    """데이터 행이 없어도 스키마를 지킨 0행을 반환한다."""
    header_only = "#\nagency_cd\tsite_no\n5s\t15s\n"
    table = _rdb_to_arrow(header_only, INGESTED_AT)

    assert table.num_rows == 0
    assert table.schema == SITE_SCHEMA
