"""MIMIC-IV bronze 적재 에셋 (S3 csv.gz → Iceberg).

각 테이블은 **개별 명시적 @dg.asset**으로 정의한다(프로젝트 컨벤션).
- 일반(부하 없는) 파일: pa.Table 반환 → dagster-iceberg IO 매니저가 자동 적재.
- 대용량 파일(icu.chartevents·hosp.labevents): boto3 스트리밍 + 청크 append
  (IO 매니저 미사용). 자유형 value 컬럼은 청크 간 타입 불일치를 막기 위해
  string으로 고정한다.

주의: Dagster가 context를 클래스 identity로 검사하므로, 자산 모듈에서는
`from __future__ import annotations`(어노테이션 문자열화)를 사용하지 않는다.
"""

import pyarrow as pa
from dagster_aws.s3 import S3Resource
from dagster_iceberg.resource import IcebergTableResource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.helper import (
    load_heavy_csv_gz_to_iceberg,
    read_csv_gz_table,
)
from dagster_project.defs.mimic_iv.constants import GROUP_NAME, SOURCE_BASE
from dagster_project.defs.mimic_iv.raw_assets import (
    raw_mimiciv_admissions,
    raw_mimiciv_chartevents,
    raw_mimiciv_d_items,
    raw_mimiciv_d_labitems,
    raw_mimiciv_icustays,
    raw_mimiciv_inputevents,
    raw_mimiciv_labevents,
    raw_mimiciv_microbiologyevents,
    raw_mimiciv_outputevents,
    raw_mimiciv_patients,
    raw_mimiciv_prescriptions,
)

# 일반 경로 에셋은 이 데이터셋 전용 IO 매니저(namespace=mimiciv)로 적재한다.
IO_MANAGER_KEY = "io_manager_mimiciv"

# 자유형 value 컬럼은 숫자/문자 혼재라 청크마다 다른 타입으로 추론될 수 있다.
# 대용량 append 시 스키마 충돌을 막기 위해 string으로 고정한다.
VALUE_AS_STRING = {"value": pa.string()}


# ── icu 모듈 ────────────────────────────────────────────────────────────


@dg.asset(
    group_name=GROUP_NAME,
    io_manager_key=IO_MANAGER_KEY,
    deps=[raw_mimiciv_icustays],
    kinds={"python", "iceberg", "bronze"},
)
def icustays(s3: S3Resource) -> pa.Table:
    """MIMIC-IV icu.icustays 원본을 bronze Iceberg 테이블로 적재한다."""
    return read_csv_gz_table(s3, f"{SOURCE_BASE}/icu/icustays.csv.gz")


@dg.asset(
    group_name=GROUP_NAME,
    deps=[raw_mimiciv_chartevents],
    kinds={"python", "iceberg", "bronze"},
)
def chartevents(
    context: AssetExecutionContext,
    s3: S3Resource,
    mimiciv_chartevents_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """대용량 icu.chartevents를 청크 단위로 bronze Iceberg에 적재한다.

    IO 매니저를 사용하지 않고 boto3 스트리밍 + 청크 append로 처리한다.
    """
    return load_heavy_csv_gz_to_iceberg(
        context,
        s3=s3,
        iceberg_table=mimiciv_chartevents_table,
        source_uri=f"{SOURCE_BASE}/icu/chartevents.csv.gz",
        column_types=VALUE_AS_STRING,
    )


@dg.asset(
    group_name=GROUP_NAME,
    io_manager_key=IO_MANAGER_KEY,
    deps=[raw_mimiciv_inputevents],
    kinds={"python", "iceberg", "bronze"},
)
def inputevents(s3: S3Resource) -> pa.Table:
    """MIMIC-IV icu.inputevents 원본을 bronze Iceberg 테이블로 적재한다."""
    return read_csv_gz_table(s3, f"{SOURCE_BASE}/icu/inputevents.csv.gz")


@dg.asset(
    group_name=GROUP_NAME,
    io_manager_key=IO_MANAGER_KEY,
    deps=[raw_mimiciv_outputevents],
    kinds={"python", "iceberg", "bronze"},
)
def outputevents(s3: S3Resource) -> pa.Table:
    """MIMIC-IV icu.outputevents 원본을 bronze Iceberg 테이블로 적재한다."""
    return read_csv_gz_table(s3, f"{SOURCE_BASE}/icu/outputevents.csv.gz")


@dg.asset(
    group_name=GROUP_NAME,
    io_manager_key=IO_MANAGER_KEY,
    deps=[raw_mimiciv_d_items],
    kinds={"python", "iceberg", "bronze"},
)
def d_items(s3: S3Resource) -> pa.Table:
    """MIMIC-IV icu.d_items(itemid 사전) 원본을 bronze Iceberg에 적재한다."""
    return read_csv_gz_table(s3, f"{SOURCE_BASE}/icu/d_items.csv.gz")


# ── hosp 모듈 ───────────────────────────────────────────────────────────


@dg.asset(
    group_name=GROUP_NAME,
    io_manager_key=IO_MANAGER_KEY,
    deps=[raw_mimiciv_patients],
    kinds={"python", "iceberg", "bronze"},
)
def patients(s3: S3Resource) -> pa.Table:
    """MIMIC-IV hosp.patients 원본을 bronze Iceberg 테이블로 적재한다."""
    return read_csv_gz_table(s3, f"{SOURCE_BASE}/hosp/patients.csv.gz")


@dg.asset(
    group_name=GROUP_NAME,
    io_manager_key=IO_MANAGER_KEY,
    deps=[raw_mimiciv_admissions],
    kinds={"python", "iceberg", "bronze"},
)
def admissions(s3: S3Resource) -> pa.Table:
    """MIMIC-IV hosp.admissions 원본을 bronze Iceberg 테이블로 적재한다."""
    return read_csv_gz_table(s3, f"{SOURCE_BASE}/hosp/admissions.csv.gz")


@dg.asset(
    group_name=GROUP_NAME,
    deps=[raw_mimiciv_labevents],
    kinds={"python", "iceberg", "bronze"},
)
def labevents(
    context: AssetExecutionContext,
    s3: S3Resource,
    mimiciv_labevents_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """대용량 hosp.labevents를 청크 단위로 bronze Iceberg에 적재한다.

    IO 매니저를 사용하지 않고 boto3 스트리밍 + 청크 append로 처리한다.
    """
    return load_heavy_csv_gz_to_iceberg(
        context,
        s3=s3,
        iceberg_table=mimiciv_labevents_table,
        source_uri=f"{SOURCE_BASE}/hosp/labevents.csv.gz",
        column_types=VALUE_AS_STRING,
    )


@dg.asset(
    group_name=GROUP_NAME,
    io_manager_key=IO_MANAGER_KEY,
    deps=[raw_mimiciv_d_labitems],
    kinds={"python", "iceberg", "bronze"},
)
def d_labitems(s3: S3Resource) -> pa.Table:
    """MIMIC-IV hosp.d_labitems(lab itemid 사전) 원본을 bronze Iceberg에 적재한다."""
    return read_csv_gz_table(s3, f"{SOURCE_BASE}/hosp/d_labitems.csv.gz")


@dg.asset(
    group_name=GROUP_NAME,
    io_manager_key=IO_MANAGER_KEY,
    deps=[raw_mimiciv_prescriptions],
    kinds={"python", "iceberg", "bronze"},
)
def prescriptions(s3: S3Resource) -> pa.Table:
    """MIMIC-IV hosp.prescriptions 원본을 bronze Iceberg 테이블로 적재한다."""
    return read_csv_gz_table(s3, f"{SOURCE_BASE}/hosp/prescriptions.csv.gz")


@dg.asset(
    group_name=GROUP_NAME,
    io_manager_key=IO_MANAGER_KEY,
    deps=[raw_mimiciv_microbiologyevents],
    kinds={"python", "iceberg", "bronze"},
)
def microbiologyevents(s3: S3Resource) -> pa.Table:
    """MIMIC-IV hosp.microbiologyevents 원본을 bronze Iceberg 테이블로 적재한다."""
    return read_csv_gz_table(s3, f"{SOURCE_BASE}/hosp/microbiologyevents.csv.gz")
