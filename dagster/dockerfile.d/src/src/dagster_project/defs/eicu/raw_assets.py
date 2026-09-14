"""eICU-CRD 원천 획득 에셋 (PhysioNet → S3 raw/).

`assets.py`의 **앞 단계**다 — 여기서 S3 `raw/`에 놓인 csv.gz를 `assets.py`가
읽어 Iceberg bronze로 올린다. 이 모듈이 생기기 전에는 그 구간이 사람 손
(수동 다운로드 + `scripts/upload_raw_to_seaweedfs.py`)에 있었다.

각 파일은 **개별 명시적 자산**이다(프로젝트 컨벤션). 데이터셋당 1개로 묶지
않는 이유는 ⓐ `assets.py`의 입도와 1:1로 맞아 `deps`가 곧게 이어지고
ⓑ 파일 하나가 실패했을 때 재시도 단위가 파일 하나로 유지되기 때문이다.

⚠️ **`kinds`에 `bronze`를 넣지 않는다.** 이 자산은 Iceberg 테이블을 만들지
않는 pre-bronze 랜딩이고, kind가 메달리온 레이어 표기 수단이라 잘못 붙이면
레이어 집계가 거짓이 된다.

주의: Dagster context 클래스 identity 검사 때문에 자산 모듈에서는
`from __future__ import annotations`를 사용하지 않는다.
"""

from dagster_aws.s3 import S3Resource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.fetch import RawFetchConfig
from dagster_project.common.physionet import PhysioNetResource, fetch_physionet_file
from dagster_project.defs.eicu.constants import (
    DIAGNOSIS_PATH,
    GROUP_NAME,
    NURSE_CHARTING_PATH,
    PATIENT_PATH,
    PHYSIONET_PROJECT,
    PHYSIONET_VERSION,
    SOURCE_BASE,
)

# 수집 자산은 `bronze`가 아니라 랜딩이므로 kind를 따로 둔다.
RAW_KINDS = {"python", "s3"}


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_eicu_patient(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """EICU patient.csv.gz를 PhysioNet에서 받아 S3 raw/에 놓는다."""
    return fetch_physionet_file(
        context,
        s3=s3,
        physionet=physionet,
        project=PHYSIONET_PROJECT,
        version=PHYSIONET_VERSION,
        rel_path=PATIENT_PATH,
        target_base=SOURCE_BASE,
        force=config.force,
    )


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_eicu_diagnosis(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """EICU diagnosis.csv.gz를 PhysioNet에서 받아 S3 raw/에 놓는다."""
    return fetch_physionet_file(
        context,
        s3=s3,
        physionet=physionet,
        project=PHYSIONET_PROJECT,
        version=PHYSIONET_VERSION,
        rel_path=DIAGNOSIS_PATH,
        target_base=SOURCE_BASE,
        force=config.force,
    )


@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_eicu_nurse_charting(
    context: AssetExecutionContext,
    s3: S3Resource,
    physionet: PhysioNetResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """EICU nurseCharting.csv.gz를 PhysioNet에서 받아 S3 raw/에 놓는다.

    대용량 파일이다. 다운로드는 스트리밍이라 메모리가 일정하지만, 전송이
    끊기면 처음부터 다시 받는다(`Range` 재개는 두지 않았다 — 선언된 공백).
    """
    return fetch_physionet_file(
        context,
        s3=s3,
        physionet=physionet,
        project=PHYSIONET_PROJECT,
        version=PHYSIONET_VERSION,
        rel_path=NURSE_CHARTING_PATH,
        target_base=SOURCE_BASE,
        force=config.force,
    )
