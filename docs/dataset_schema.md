# 데이터셋 원천 스키마 · 피처 레퍼런스

이 문서는 본 저장소가 실제로 적재·변환하는 **MIMIC-IV / eICU bronze 원천 테이블**과 그 위에 얹은
**SOFA → Sepsis-3 실버(dbt) 파이프라인**의 스키마·피처 레퍼런스다. 원천은 Iceberg 테이블 포맷으로
저장되며(네임스페이스 `mimiciv` / `eicu`), Dagster `defs/<dataset>` 서브프로젝트가 S3의 `csv.gz`를
읽어 적재한다. dbt는 이 적재분을 `source()`로 참조(생성이 아님)해 실버 개념 테이블을 만든다.
여기 기술한 컬럼·itemid는 모두 `source.yml`·실버 `.sql`·`schema.yml`에서 **직접 확인된 것만** 담았다.

> 저장은 UTC, 표시·스케줄은 KST. 저장/조인 흐름·컨테이너 구성은 [architectures/overview.md](architectures/overview.md),
> source/ref·메달리온 태깅 규칙은 [conventions/dbt.md](conventions/dbt.md) 참고.

---

## MIMIC-IV 원천 테이블

Iceberg 네임스페이스 `mimiciv`. `source.yml`에 11개 테이블(icu 5 + hosp 6)이 선언돼 있고, 각 테이블은
`meta.dagster.asset_key`로 Dagster 적재 자산과 1:1 매핑된다. `chartevents`·`labevents`는 대용량이라
청크 append 경로로 적재한다.

### icu 모듈

#### mimiciv.icustays

ICU 체류(stay) 단위 메타 테이블. 실버의 시간 격자·조인 기준.

| 컬럼 | 의미 |
|------|------|
| `subject_id` | 환자 식별자 |
| `hadm_id` | 병원 입원 식별자 |
| `stay_id` | ICU stay 식별자 (조인 키) |
| `intime` | ICU 입실 시각 |
| `outtime` | ICU 퇴실 시각 |

#### mimiciv.chartevents (대용량 · 청크 적재)

ICU에서 기록된 모든 시계열 관측값. 활력징후·GCS·환기·체중 등 대부분의 실버 개념이 여기서 나온다.

| 컬럼 | 의미 |
|------|------|
| `subject_id` / `hadm_id` / `stay_id` | 환자·입원·stay 식별자 |
| `charttime` | 관측 시각 (피벗 grain) |
| `storetime` | 시스템 저장 시각 (o2 flow 최신값 선택 등에 사용) |
| `itemid` | 측정 항목 코드 (→ `d_items` 조인) |
| `value` | 측정값 (문자열; 예: 산소공급장치·환기모드 라벨) |
| `valuenum` | 측정값 (수치; 대부분의 활력징후·검사값) |
| `valueuom` | 단위 |

#### mimiciv.inputevents

ICU 내 투입 기록. 실버에서는 승압제(vasopressor) 4종 추출 원천이다.

| 컬럼 | 의미 |
|------|------|
| `subject_id` / `hadm_id` / `stay_id` | 식별자 |
| `starttime` / `endtime` | 투여 시작·종료 시각 |
| `itemid` | 투여 항목 코드 (승압제 itemid로 필터) |
| `amount` / `amountuom` | 투여량·단위 |
| `rate` / `rateuom` | 투여 속도·단위 |
| `linkorderid` | 동일 오더 인스턴스 식별자 |

#### mimiciv.outputevents

ICU 내 배출 기록. 실버 `urine_output`(소변량)의 원천.

| 컬럼 | 의미 |
|------|------|
| `subject_id` / `hadm_id` / `stay_id` | 식별자 |
| `charttime` | 배출 시각 |
| `itemid` | 배출 항목 코드 |
| `value` | 배출량 (mL, 수치) |

#### mimiciv.d_items

ICU 측정 항목 사전. `itemid`로 chartevents·inputevents·outputevents와 조인해 항목명(`label`)·
카테고리를 얻는다.

| 컬럼 | 의미 |
|------|------|
| `itemid` | 측정 항목 코드 (PK) |
| `label` | 항목 설명 |
| `category` | 카테고리 (예: `Antibiotics`, `IV Medication`) |

### hosp 모듈

#### mimiciv.patients

환자(개인) 단위 인구통계.

| 컬럼 | 의미 |
|------|------|
| `subject_id` | 환자 식별자 (PK) |
| `gender` | 성별 (M / F) |
| `anchor_age` | 기준 연도 당시 나이 (>89세는 91로 고정) |
| `anchor_year` | 시간 쉬프트된 기준 연도 |
| `dod` | 사망일 |

#### mimiciv.admissions

병원 입원 단위 메타. 사망 정보의 원천.

| 컬럼 | 의미 |
|------|------|
| `subject_id` / `hadm_id` | 식별자 |
| `admittime` / `dischtime` | 입원·퇴원 시각 |
| `deathtime` | 원내 사망 시각 (생존 시 NULL) |
| `hospital_expire_flag` | 1=원내 사망, 0=생존 |

#### mimiciv.labevents (대용량 · 청크 적재)

검사실 결과 기록. 혈액가스(bg)·화학(chemistry)·효소(enzyme)·CBC 실버 개념의 원천.

| 컬럼 | 의미 |
|------|------|
| `labevent_id` | PK |
| `subject_id` / `hadm_id` | 식별자 (`hadm_id`는 응급 시 NULL 가능) |
| `specimen_id` | 채취 검체 식별자 (화학·효소·CBC 피벗 grain) |
| `itemid` | 검사 항목 코드 (→ `d_labitems`) |
| `charttime` | 검사 결과 기록 시각 |
| `value` | 검사 결과값 (문자열) |
| `valuenum` | 검사 결과값 (수치) |
| `valueuom` | 단위 |

> `chartevents`와 달리 `labevents.value`는 문자열, 수치 비교·피벗에는 `valuenum`을 쓴다.

#### mimiciv.d_labitems

검사 항목 사전. `itemid`로 `labevents`와 조인해 검사명을 얻는다.

| 컬럼 | 의미 |
|------|------|
| `itemid` | 검사 항목 코드 (PK) |
| `label` | 검사명 |

#### mimiciv.prescriptions

병원 처방 기록. 실버 `antibiotic`(항생제 추출)의 원천.

| 컬럼 | 의미 |
|------|------|
| `subject_id` / `hadm_id` | 식별자 |
| `starttime` / `stoptime` | 처방 시작·종료 시각 |
| `drug` | 약물명 (자유 텍스트; 항생제 필터 대상) |
| `route` | 투여 경로 |
| `drug_type` | `MAIN` / `BASE` / `ADDITIVE` |

#### mimiciv.microbiologyevents

미생물 배양 검사 기록. 실버 `suspicion_of_infection`이 배양 시각·양성 여부·검체 판정에 사용
(source로 직접 소비). 상세 컬럼은 원천 문서 미제공분이라 여기서는 생략한다.

---

## eICU 원천 테이블

Iceberg 네임스페이스 `eicu`. `source.yml`에 3개 테이블이 선언돼 있다. **현재 실버 dbt 모델은
MIMIC-IV만 존재하며 eICU는 bronze 적재까지만** 되어 있어 아래는 참조용으로 간략히 둔다.
`nurse_charting`은 대용량이라 청크 append 경로로 적재한다.

### eicu.patient

ICU stay 단위 인구통계·재원 정보. eICU의 중심 테이블. **`*time24` 계열은 문자열 `"HH:MM:SS"`**,
시간 관계는 대부분 `*offset`(ICU 입실 기준 분)으로 표현된다.

| 컬럼 | 의미 |
|------|------|
| `patientunitstayid` | ICU stay 식별자 (PK) |
| `patienthealthsystemstayid` | 병원 stay 식별자 |
| `gender` | 성별 |
| `age` | 나이 (`"> 89"` 같은 문자열 포함) |
| `unitadmittime24` / `unitdischargetime24` / `hospitaladmittime24` | 시각, 문자열 `"HH:MM:SS"` |
| `hospitaldischargeoffset` | 병원 퇴원 offset(분), 사망 시점 원천 |
| `hospitaldischargestatus` | `"Expired"` → 병원 내 사망 |
| `unitdischargeoffset` | ICU 재원 시간(분) |
| `unitdischargestatus` | `"Expired"` → ICU 내 사망 |

### eicu.diagnosis

임상 진단 기록. sepsis 코호트 구분·onset 시점 확인에 사용.

| 컬럼 | 의미 |
|------|------|
| `patientunitstayid` | FK → patient |
| `diagnosisoffset` | 진단 기록 시점 (ICU 입실 기준 분) |
| `diagnosisstring` | 진단명 (`"sepsis"` 포함 여부로 코호트 분류) |

### eicu.nurse_charting (대용량 · 청크 적재)

간호 차트 기록. 활력징후의 주요 원천(원천 테이블명 `nurseCharting`).

| 컬럼 | 의미 |
|------|------|
| `patientunitstayid` | FK → patient |
| `nursingchartoffset` | 기록 시점 (ICU 입실 기준 분) |
| `nursingchartcelltypevallabel` | 측정 항목명 (예: Heart Rate, SBP) |
| `nursingchartvalue` | 측정값 (문자열, nullable) |

---

## 실버 피처 파이프라인 (SOFA → Sepsis-3)

MIMIC-IV bronze 위에 mimic-code concepts를 Trino로 포팅한 **22개 실버 모델**. 계층은
Tier-1(원천 `source()` 직접 소비) → 중간(다른 실버를 `ref()`) → 최종(`sofa`, `sepsis3`)으로 흐른다.
아래 itemid는 해당 `.sql`에 실제로 등장하는 값만 표기한다.

### Tier-1 — source() 직접 소비

| 모델 | 입력 (source) | 산출 · 핵심 itemid |
|------|---------------|--------------------|
| `icustay_times` | icustays, chartevents | stay별 첫·마지막 HR(220045) 시각 → fuzzy intime/outtime |
| `icustay_hourly` | icustays (+ `ref` icustay_times) | ICU 재실을 정시 격자로 전개 (hr, endtime) |
| `vitalsign` | chartevents | 활력징후 와이드 피벗. HR·혈압·호흡수·SpO2·체온 itemid를 컬럼으로 편다. temp 223761(℉)·223762(℃), temperature_site 224642, glucose 225664/220621/226537 |
| `ventilator_setting` | chartevents | 환기 세팅 피벗. fio2 223835, peep 220339/224700, mode 223849/229314, type 223848, rr_set 224688 등 |
| `oxygen_delivery` | chartevents | 산소 공급 피벗. o2_flow 223834/227582, additional 227287, device 226732 |
| `gcs` | chartevents | GCS 총점. motor **223901**, verbal **223900**, eyes **220739** |
| `weight_durations` | chartevents (+ icustays) | 체중 구간화. 입실체중 226512, 일일체중 224639 |
| `bg` | labevents (+ chartevents SpO2/FiO2) | 혈액가스 피벗 · P/F비(pao2fio2ratio) → SOFA 호흡 입력 |
| `chemistry` | labevents | creatinine·bun·전해질 피벗 (specimen_id grain) → SOFA 신장 |
| `enzyme` | labevents | bilirubin_total·ALT·AST 등 피벗 → SOFA 간 |
| `complete_blood_count` | labevents | platelet·wbc·hemoglobin 등 CBC 피벗 → SOFA 응고 |
| `urine_output` | outputevents | stay·charttime별 소변량 합산 (GU 관주액은 음수 상쇄) |
| `epinephrine` | inputevents | 에피네프린 용량·기간, itemid **221289** → SOFA 심혈관 |
| `norepinephrine` | inputevents | 노르에피네프린, itemid **221906** (mcg/kg/min 환산) → SOFA 심혈관 |
| `dopamine` | inputevents | 도파민, itemid **221662** → SOFA 심혈관 |
| `dobutamine` | inputevents | 도부타민, itemid **221653** → SOFA 심혈관 |
| `antibiotic` | prescriptions (+ icustays) | 처방에서 항생제 추출 + stay 매칭 → 감염 의심 입력 |

### 중간 — ref() 소비

| 모델 | 입력 (ref) | 산출 |
|------|-----------|------|
| `ventilation` | ventilator_setting, oxygen_delivery | 산소장치·모드를 6범주로 분류, 환기 구간(duration) 산출 |
| `urine_output_rate` | urine_output, weight_durations (+ icustays, chartevents) | 6/12/24시간 시간당 소변량(mL/kg/hr) → SOFA 신장 |
| `suspicion_of_infection` | antibiotic (+ source microbiologyevents) | 항생제↔배양 시간 근접 매칭 → 감염 의심 여부·시각·양성 |

### 최종 — SOFA · Sepsis-3

| 모델 | 입력 (ref) | 산출 |
|------|-----------|------|
| `sofa` | icustay_hourly, bg, vitalsign, gcs, ventilation, chemistry·enzyme·complete_blood_count, epinephrine·norepinephrine·dopamine·dobutamine, urine_output_rate | 매시간 6장기 SOFA + 24h 롤링 최대 합(`sofa_24hours` 0~24) |
| `sepsis3` | sofa, suspicion_of_infection | SOFA≥2 & 감염 의심인 가장 이른 시점 = Sepsis-3 onset (stay당 1건) |

> SOFA 6장기 매핑: 호흡=bg(P/F)+ventilation, 응고=platelet(CBC), 간=bilirubin(enzyme),
> 심혈관=승압제 4종+MBP, 신경=gcs, 신장=creatinine(chemistry)+urine_output_rate.

---

## 참고

- 실버 모델은 모두 **mimic-code concepts**를 Trino로 포팅한 것이다. 각 `.sql` 헤더에
  원본 출처가 `출처: mimic-code concepts/...`(예: `score/sofa.sql`, `sepsis/sepsis3.sql`,
  `measurement/vitalsign.sql`)로 명시돼 있다. 원본:
  https://github.com/MIT-LCP/mimic-code
- MIMIC-IV 공식 스키마: https://mimic.mit.edu/docs/IV/modules/
- eICU-CRD 공식 스키마: https://eicu-crd.mit.edu/
- 저장·조인·컨테이너 흐름: [architectures/overview.md](architectures/overview.md)
- source/ref·메달리온 태깅·자산키 매핑 규칙: [conventions/dbt.md](conventions/dbt.md)
