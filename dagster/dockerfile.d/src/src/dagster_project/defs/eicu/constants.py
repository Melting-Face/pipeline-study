"""eICU-CRD 데이터셋 전용 상수."""

# Iceberg 네임스페이스 / Dagster 그룹
# (메달리온 레이어는 네임스페이스가 아닌 kind로 표기)
NAMESPACE = "eicu"
GROUP_NAME = "eicu"

# 원천 csv.gz 루트 (eICU는 플랫 구조)
SOURCE_BASE = "s3://warehouse/raw/eicu"

# ── 원천 획득 (PhysioNet) ──────────────────────────────────────────────
#
# 🔴 **버전은 스키마 계약이다.** 올리면 `docs/dataset_schema.md`와 한 벌로
#    고친다 — 컬럼이 달라지는데 코드만 바뀌면 적재는 성공하고 값이 어긋난다.
# ⚠️ 프로젝트 슬러그·버전은 **미확인**이다(외부 사실).
#    `scripts/physionet_access_probe.py`가 실측으로 판정한다.
PHYSIONET_PROJECT = "eicu-crd"
PHYSIONET_VERSION = "2.0"

# 파일별 상대경로 — **원천 URL과 S3 키를 동시에** 만드는 단일 출처다.
# 리스트가 아니라 상수로 두는 이유: 리스트+루프는 팩토리 금지 규약에 걸리고,
# 열거의 정본은 상수가 아니라 **자산 그래프**(`dg check`가 센다)다.
PATIENT_PATH = "patient.csv.gz"
DIAGNOSIS_PATH = "diagnosis.csv.gz"
NURSE_CHARTING_PATH = "nurseCharting.csv.gz"
