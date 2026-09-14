"""MIMIC-IV 데이터셋 전용 상수."""

# Iceberg 네임스페이스 / Dagster 그룹
# (메달리온 레이어는 네임스페이스가 아닌 kind로 표기)
NAMESPACE = "mimiciv"
GROUP_NAME = "mimiciv"

# 원천 csv.gz 루트 (모듈별 하위 경로: hosp/, icu/)
SOURCE_BASE = "s3://warehouse/raw/mimiciv"

# ── 원천 획득 (PhysioNet) ──────────────────────────────────────────────
#
# 🔴 **버전은 스키마 계약이다.** 올리면 `docs/dataset_schema.md`와 한 벌로
#    고친다 — 3.1과 2.2는 컬럼이 달라, 코드만 바뀌면 적재는 성공하고 값이
#    어긋난다(검산을 통과하는 종류의 오류다).
# ⚠️ 프로젝트 슬러그·버전은 **미확인**이다(외부 사실).
#    `scripts/physionet_access_probe.py`가 실측으로 판정한다.
PHYSIONET_PROJECT = "mimiciv"
PHYSIONET_VERSION = "3.1"

# 파일별 상대경로 — **원천 URL과 S3 키를 동시에** 만드는 단일 출처다.
# 리스트가 아니라 상수로 두는 이유: 리스트+루프는 팩토리 금지 규약에 걸리고,
# 열거의 정본은 상수가 아니라 **자산 그래프**(`dg check`가 센다)다.
ICUSTAYS_PATH = "icu/icustays.csv.gz"
CHARTEVENTS_PATH = "icu/chartevents.csv.gz"
INPUTEVENTS_PATH = "icu/inputevents.csv.gz"
OUTPUTEVENTS_PATH = "icu/outputevents.csv.gz"
D_ITEMS_PATH = "icu/d_items.csv.gz"
PATIENTS_PATH = "hosp/patients.csv.gz"
ADMISSIONS_PATH = "hosp/admissions.csv.gz"
LABEVENTS_PATH = "hosp/labevents.csv.gz"
D_LABITEMS_PATH = "hosp/d_labitems.csv.gz"
PRESCRIPTIONS_PATH = "hosp/prescriptions.csv.gz"
MICROBIOLOGYEVENTS_PATH = "hosp/microbiologyevents.csv.gz"
