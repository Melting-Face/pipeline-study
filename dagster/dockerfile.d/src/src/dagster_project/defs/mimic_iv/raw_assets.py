"""MIMIC-IV 원천 획득 에셋 (PhysioNet → S3 raw/).

`assets.py`의 **앞 단계**다 — 여기서 S3 `raw/`에 놓인 csv.gz를 `assets.py`가
읽어 Iceberg bronze로 올린다. 이 모듈이 생기기 전에는 그 구간이 사람 손
(수동 다운로드 + `scripts/upload_raw_to_seaweedfs.py`)에 있었다.

각 파일은 **개별 명시적 자산**이다(프로젝트 컨벤션). 데이터셋당 1개로 묶지
않는 이유는 ⓐ `assets.py`의 입도와 1:1로 맞아 `deps`가 곧게 이어지고
ⓑ 3.3GB짜리 하나가 실패했을 때 재시도 단위가 데이터셋 전체가 되지 않기
때문이다(비용이 자릿수로 다르다).

⚠️ **`kinds`에 `bronze`를 넣지 않는다.** 이 자산은 Iceberg 테이블을 만들지
않는 pre-bronze 랜딩이고, kind가 메달리온 레이어 표기 수단이라 잘못 붙이면
레이어 집계가 거짓이 된다.

🔴 **`chartevents`·`labevents`는 각각 ≈3.3GB다.** 둘을 동시에 머티리얼라이즈
하지 않는다 — 다운로드는 daemon in-process 서브프로세스에서 돌고(`DefaultRunLauncher`)
업로드 버퍼가 run 수만큼 곱해진다(`docs/resource-sizing.md` §Dagster).

주의: Dagster context 클래스 identity 검사 때문에 자산 모듈에서는
`from __future__ import annotations`를 사용하지 않는다.
"""

from dagster_aws.s3 import S3Resource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.fetch import RawFetchConfig
from dagster_project.common.physionet import PhysioNetResource, fetch_physionet_file
from dagster_project.defs.mimic_iv.constants import (
    ADMISSIONS_PATH,
    CHARTEVENTS_PATH,
    D_ITEMS_PATH,
    D_LABITEMS_PATH,
    GROUP_NAME,
    ICUSTAYS_PATH,
    INPUTEVENTS_PATH,
    LABEVENTS_PATH,
    MICROBIOLOGYEVENTS_PATH,
    OUTPUTEVENTS_PATH,
    PATIENTS_PATH,
    PHYSIONET_PROJECT,
    PHYSIONET_VERSION,
    PRESCRIPTIONS_PATH,
    SOURCE_BASE,
)

# 수집 자산은 `bronze`가 아니라 랜딩이므로 kind를 따로 둔다.
RAW_KINDS = {"python", "s3"}


def _fetch(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
    rel_path: str,
) -> dg.MaterializeResult:
    """이 데이터셋의 수집 호출을 한 줄로 줄인다(자산 정의는 분리·명시 유지).

    공통 로직은 일반 함수로 재사용하되 **에셋 정의 자체는 각각 명시**한다는
    프로젝트 컨벤션에 따른다 — 이것은 팩토리가 아니라 인자 축약이다.
    """
    return fetch_physionet_file(
        context,
        s3=s3,
        physionet=physionet,
        project=PHYSIONET_PROJECT,
        version=PHYSIONET_VERSION,
        rel_path=rel_path,
        target_base=SOURCE_BASE,
        force=config.force,
    )


# ── icu 모듈 ────────────────────────────────────────────────────────────


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_icustays(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV icu/icustays.csv.gz를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, ICUSTAYS_PATH)


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_chartevents(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV icu/chartevents.csv.gz(≈3.3GB)를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, CHARTEVENTS_PATH)


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_inputevents(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV icu/inputevents.csv.gz를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, INPUTEVENTS_PATH)


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_outputevents(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV icu/outputevents.csv.gz를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, OUTPUTEVENTS_PATH)


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_d_items(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV icu/d_items.csv.gz를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, D_ITEMS_PATH)


# ── hosp 모듈 ───────────────────────────────────────────────────────────


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_patients(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV hosp/patients.csv.gz를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, PATIENTS_PATH)


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_admissions(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV hosp/admissions.csv.gz를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, ADMISSIONS_PATH)


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_labevents(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV hosp/labevents.csv.gz(≈3.3GB)를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, LABEVENTS_PATH)


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_d_labitems(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV hosp/d_labitems.csv.gz를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, D_LABITEMS_PATH)


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_prescriptions(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV hosp/prescriptions.csv.gz를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, PRESCRIPTIONS_PATH)


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_mimiciv_microbiologyevents(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """MIMIC-IV hosp/microbiologyevents.csv.gz를 받아 S3 raw/에 놓는다."""
    return _fetch(context, s3, physionet, config, MICROBIOLOGYEVENTS_PATH)
