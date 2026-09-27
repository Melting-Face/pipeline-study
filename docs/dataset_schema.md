# 데이터셋 원천 스키마 · 피처 레퍼런스

이 문서는 **현재 적재된 데이터셋**의 bronze 원천 테이블과 그 위에 얹은 실버(dbt) 파이프라인의
스키마·피처 레퍼런스다. 분석 질문이 걸린 것은 **MIMIC-IV / eICU**이고, 그 질문은
**SOFA → Sepsis-3**다. 원천은 Iceberg 테이블 포맷으로 저장되며(네임스페이스 `mimiciv` / `eicu`),
Dagster `defs/<dataset>` 서브프로젝트가 S3의 `csv.gz`를 읽어 적재한다. dbt는 이 적재분을
`source()`로 참조(생성이 아님)해 실버 개념 테이블을 만든다.
여기 기술한 컬럼·itemid는 모두 `source.yml`·실버 `.sql`·`schema.yml`에서 **직접 확인된 것만** 담았다.
데이터셋을 추가할 때는 이 문서에 `## <dataset> 원천 테이블` 절을 덧붙인다 — 다른 문서를 고칠 일이 아니다.

원천이 파일이 아니라 공개 API인 데이터셋도 있다(**Frankfurter 환율** — 아래). ⚠️ `usgs_water`는
적재돼 있으나 이 문서에 절이 **없다**(선언된 공백).

> 저장은 UTC, 표시·스케줄은 KST. 저장/조인 흐름·컨테이너 구성은 [architectures/overview.md](architectures/overview.md),
> source/ref·메달리온 태깅 규칙은 [conventions/dbt.md](conventions/dbt.md) 참고.

## 원천 획득 (PhysioNet)

MIMIC-IV·eICU 원천 `csv.gz`는 **PhysioNet credentialed access** 대상이다(CITI 교육 이수 +
DUA 서명). 파일은 Dagster **수집 자산**(`defs/<dataset>/raw_assets.py`)이 인증 세션으로 받아
`s3://warehouse/raw/<dataset>/...`에 놓고, 그 뒤를 적재 자산이 읽는다.

| 데이터셋 | PhysioNet 프로젝트 | 고정 버전 | 상수 위치 |
| --- | --- | --- | --- |
| MIMIC-IV | `mimiciv` | `3.1` | `defs/mimic_iv/constants.py` |
| eICU-CRD | `eicu-crd` | `2.0` | `defs/eicu/constants.py` |

🔴 **버전은 스키마 계약이다.** 아래 컬럼 표는 위 버전의 것이고, `PHYSIONET_VERSION`을
올리면 **이 문서도 한 벌로** 고쳐야 한다 — 코드만 바꾸면 적재는 성공하고 값이 어긋난다
(검산을 통과하는 종류의 오류다).

⚠️ 프로젝트 슬러그·버전·`SHA256SUMS.txt`의 존재는 저장소 밖 사실이라 **미확인**이다.
`uv run scripts/physionet_access_probe.py`가 실측으로 판정한다(종료코드 0/1/2 —
`2`는 "통과"가 아니라 **판정 불가**다).

접근 경로가 막혔을 때의 폴백은 `scripts/upload_raw_to_seaweedfs.py`(로컬 파일 미러)다.
크리덴셜 주입은 [operations.md](operations.md) §1-1, 거버넌스는 [security.md](security.md).

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

## Frankfurter 환율 원천 테이블

Iceberg 네임스페이스 `frankfurter_fx`. 앞의 둘과 성격이 다르다 — 원천이 S3 파일이 아니라 **공개 HTTP API**이고,
**이 저장소의 첫 일자 파티션 자산**이다. dbt 모델을 두지 않으므로 `source.yml`·`dbt_project.yml` 항목이
없다(**건너뛴 것이지 빠뜨린 것이 아니다** — [conventions/dagster.md](conventions/dagster.md) 체크리스트 참조).

원천은 **Frankfurter**(환율을 무인증·무쿼터로 제공하는 공개 API)다. 처음 대상이던 상용 API는 **무료 플랜이
과거 일자 조회를 막아** 원천을 바꿨다 — 일자 파티션은 과거 조회가 전제라 그 제약이 설계를 무효화했다.

🔴 **이름을 상류 기관이 아니라 호출하는 서비스로 붙였다.** 초안은 `ecb_fx`였는데, 공식 문서가
*"By default, rates are blended across all providers"* 와 *"84 central banks"* 를 말해 **응답이 ECB
참조환율 그 자체라고 단정할 근거가 없다**(그 서술이 어느 API 판본을 가리키는지도 문서가 가르지 않는다 —
**미확인**). 실측 응답이 `base: EUR`·29통화라 모양은 맞지만 **모양이 맞는 것과 확인된 것은 다른 축**이고,
틀린 라벨은 검산을 통과한 채 남는다. 네임스페이스는 나중에 바꾸기 비싸므로 확인된 사실로 짓는다.

⚠️ **출처 표기 의무** — 상류에 ECB가 있고 그 이용조건은 자유 이용을 허용하되
*"When such information is distributed or reproduced, it must appear accurately and the ECB must be
cited as the source."* 로 **must**를 쓴다(요청이 아니다). 값을 외부로 내보내는 산출물에는 출처를 적는다.
거버넌스 판정은 [security.md](security.md) §0.

### frankfurter_fx.fx_rates_daily

응답의 `rates` 객체를 **long 형태로 편** bronze 테이블(통화당 1행). wide로 두면 통화가 늘고 줄 때마다
스키마가 바뀌어 재적재가 필요해진다.

| 컬럼 | 타입 | 의미 |
|------|------|------|
| `rate_date` | string | **우리가 요청한 날짜** = Dagster 파티션 키. 파티션 교체의 필터 대상 |
| `source_date` | string | **원천이 응답에 에코한 날짜.** `rate_date`와 다를 수 있다(아래) |
| `base_currency` | string | 기준 통화. 원천 기본값은 `EUR`이며 응답에서 읽는다 |
| `quote_currency` | string | 상대 통화(ISO 4217) |
| `rate` | double | `base_currency` 1단위당 `quote_currency` 환율 |
| `ingested_at` | timestamp(UTC) | 수집 처리시간 |

**왜 날짜가 두 개인가.** 상류 중앙은행은 영업일에만 고시하고, 원천은 **주말 요청에 직전 영업일 값을 조용히
돌려준다**(대조 실험으로 실측 — 토요일 요청에 HTTP 200, 통화 수는 평일과 동일, `date` 필드만 시프트).
상태코드로도 행 수로도 값의 범위로도 잡히지 않고 **응답의 날짜 필드 하나만 다르다.** 원천 문서는
이 동작을 서술하지 않는다.
그래서 요청값과 응답값을 나란히 두고 일치 여부를 머티리얼라이즈 메타데이터(`date_matches_request`)에
남긴다 — 판정하지 않고 **관측**만 한다([conventions/data-quality.md](conventions/data-quality.md)).
⇒ 이 테이블을 소비하는 쪽은 **`rate_date`로 조인하면 주말에 금요일 값이 딸려온다**는 것을 전제해야
한다. 영업일만 쓰려면 `rate_date = source_date` 조건을 명시적으로 건다.

**적재 모드**는 `replace_partition_in_iceberg`(파티션 범위만 교체)다. `append`는 재실행마다 행이
늘고, `replace`는 `drop_table`이라 스냅샷 계보를 끊는다. 파티션 시작일·타임존의 근거는
`defs/frankfurter_fx/constants.py` 주석에 있다(둘 다 조용히 어긋나는 축이다).

---

## Polygon 시장 데이터 원천 테이블

Iceberg 네임스페이스 `polygon_market`. 공개 HTTP API이고 두 축(시세·뉴스) 모두 **일자 파티션 교체**다.
인증은 `Authorization: Bearer` 헤더라 크리덴셜이 URL에 실리지 않는다 — 쿼리 파라미터로만 받는 원천
(FRED)과 갈리는 지점이고, 그래서 이쪽에는 예외 마스킹 래퍼가 없다(**없어도 되는 것이지 빠뜨린 것이 아니다**).

🔴 **조회 가능한 과거 범위가 롤링 윈도우다.** `frankfurter_fx`(수십 년 전까지 열려 있음)와 축이 다르다 —
시간이 지나면 가장 이른 파티션이 권한 밖으로 밀려 거부되고, **이미 적재된 것은 남지만 재적재는 영구히
불가능해진다.** 그래서 ⓐ 초기 구간 백필을 미루지 않고 ⓑ 나중에 쓸지 모르는 컬럼도 지금 받는다
(`vwap`·`trade_count`·`insights`). 버리는 비용과 담는 비용이 **비대칭**이다.
경계는 문서의 서술을 믿지 않고 `scripts/stock_source_access_probe.py`가 이분 탐색으로 잰다 —
"확인된 가장 오래된 날짜"와 "경계"는 다른 값이고, 전자를 후자로 읽으면 학습 데이터를 조용히 버린다.

### polygon_market.equity_ohlcv_daily

하루치 **전 미국 티커**를 한 번의 호출로 받는다. 파티션과 요청이 1:1이고, 그날 거래된 종목 전체가 응답
그 자체라 **별도 유니버스 테이블 없이 생존 편향이 구조적으로 해소**된다(상장폐지 종목도 그날까지 행이 남는다).

| 컬럼 | 타입 | 의미 |
|------|------|------|
| `trade_date` | string | **우리가 요청한 날짜** = 파티션 키. 교체 필터 대상 |
| `source_date` | string | 응답 bar의 시각에서 유도한 날짜. `trade_date`와 다를 수 있다 |
| `bar_timestamp` | timestamp(UTC) | 응답 bar의 시각 **원본**(아래) |
| `ticker` | string | 종목 티커 |
| `open`·`high`·`low`·`close` | double | 시가·고가·저가·종가 |
| `volume` | double | 거래량. 원천이 정수로 줄 때도 있어 넓게 받는다 |
| `vwap` | double | 거래량가중평균가. 없을 수 있다(null) |
| `trade_count` | bigint | 체결 건수. 없을 수 있다(null) |
| `ingested_at` | timestamp(UTC) | 수집 처리시간 |

**`bar_timestamp`를 날짜로 줄이지 않는 이유.** 이 컬럼 하나가 **방언 dispatch 매크로 한 벌을 없앤다.**
실버의 거래일 달력은 "세션 마감 = 16:00 America/New_York"을 UTC로 바꿔야 하는데, 그 변환 문법이
엔진마다 갈려 매크로 + sqlfluff 스텁이 필요하다. 그런데 **원천이 이미 그 값을 준다**(서머타임이 반영된
UTC 시각으로 온다).

⚠️ **그러나 실제 마감은 아니다.** 조기 폐장일에도 같은 16:00 기준 값이 온다(정상 거래일을 대조군으로
두고 실측해 반증했다). 관측 두 건만 보면 "이 값이 세션 마감"으로 읽히는데, 세 번째 관측이 그것을 깬다 —
**값이 맞는 것과 근거가 맞는 것은 다른 축**이다. 그래서 컬럼 이름에 `session_close`를 쓰지 않고,
해석은 가정을 함께 적어 실버가 붙인다.

**`source_date`가 따로 있는 이유**는 `frankfurter_fx`와 같다 — 휴장일 요청에 직전 거래일 값이 조용히
올 수 있는 축을 관측용으로 남긴다. 판정하지 않고 메타데이터에 값만 적는다.

### polygon_market.news_articles

기사 **메타데이터만** 담는다. 본문·요약은 적재하지 않는다 — 저작물 저장 축을 피한다
(거버넌스 판정은 [security.md](security.md) §0).

| 컬럼 | 타입 | 의미 |
|------|------|------|
| `published_date` | string | 요청한 UTC 하루 = 파티션 키 |
| `article_id` | string | 원천이 주는 안정 ID. 중복 제거의 단일 축 |
| `published_at` | timestamp(UTC) | **이벤트타임.** 누수 방지 설계 전체가 이 컬럼 위에 선다 |
| `title`·`article_url`·`publisher` | string | 제목·링크·발행처 |
| `tickers` | array\<string\> | 관련 종목. 전개는 실버에서 한다 |
| `insights` | array\<struct\> | **원천이 준** 티커별 감성(`ticker`·`sentiment`) |
| `ingested_at` | timestamp(UTC) | 수집 처리시간 |

**append가 아니라 파티션 교체인 이유.** 원천이 발행시각으로 **날짜 구간 질의**를 지원한다. RSS 계열은
롤링 윈도우라 폴링 주기가 피드 깊이를 넘으면 기사가 조용히 유실되고, append 자산에는 그 유실을 드러낼
수단이 없다. 날짜로 질의되면 그 축이 아예 사라지고 재실행이 멱등해진다.

**`insights`를 담는 이유.** 원천이 티커별 감성을 함께 준다. 우리가 계산하지 않으므로 모델 학습 시점이라는
또 하나의 누수 축이 생기지 않는다. 설명 문장(`sentiment_reasoning`)은 산문이라 **담지 않는다** — 기사
본문 미적재와 같은 축이고 스키마에 아예 두지 않아 구조로 강제한다.

⚠️ **이벤트타임이 없는 원천은 이 설계에 쓸 수 없다.** 후보였던 한 공개 피드는 접근·형식이 모두 통과했으나
모든 항목이 **같은 발행시각**(피드 생성 시각)을 갖는 것이 프로브에서 드러나 탈락했다. 수집 시각으로
대신하면 편향이 **에러 없이** 들어간다. 접근 가능성과 사용 가능성은 다른 축이다.

---

## FRED 경제지표 원천 테이블

Iceberg 네임스페이스 `fred_calendar`. ⚠️ **크리덴셜을 쿼리 파라미터로만 받는다** — 예외 메시지에 URL이
실려 키가 로그에 박히는 축이 열려 있어, 접속 리소스가 예외를 마스킹해 재포장한다(`common/fred.py`).
위 Polygon과 대비되는 지점이고, 두 파일을 나란히 읽으면 왜 한쪽만 감쌌는지가 보인다.

### fred_calendar.fred_series_observations

🔴 **파티션 축이 날짜가 아니라 시리즈다.** 이 저장소의 첫 정적 파티션 자산이다.

초안은 vintage 날짜 파티션이었는데 실측이 뒤집었다 — vintage 하루를 고정하면 원천이 **전체 히스토리를
통째로** 준다. 그대로 두면 `히스토리 x 시리즈 x 일수`로 같은 값을 매일 복제하는 2차 증가가 된다.
원천에는 이를 위한 표현이 이미 있다: 유효 시점을 **구간**으로 주면 값마다 "유효했던 기간"이 한 행에 담기고
**개정이 있었던 관측일만** 여러 행이 된다. 시리즈당 한 번 받고 저장은 선형이 된다.

| 컬럼 | 타입 | 의미 |
|------|------|------|
| `series_id` | string | 파티션 키. **응답 본문에 없어** 파서가 요청값을 채운다 |
| `observation_date` | string | 지표가 가리키는 기간 |
| `realtime_start` | string | 이 값이 유효해진 날. 요청 구간 시작일로 **클리핑**된다 |
| `realtime_end` | string | 유효 종료일. 열린 구간은 먼 미래 날짜로 온다 |
| `value` | string | 원문 보존. **결측을 `"."` 로 표기**한다 |
| `ingested_at` | timestamp(UTC) | 수집 처리시간 |

**같은 `observation_date`가 여러 행인 것이 정상이다.** 지표는 발표 뒤 개정되므로 각 행이 한 vintage
구간을 갖는다. 월·분기 지표는 개정이 잦고 일별 금리는 거의 없다. **여기서 하나로 접으면 그 시점에 무엇을
알았는지 복원할 수 없고, 접힌 뒤에는 어떤 테스트로도 되살릴 수 없다.**

`value`를 문자열로 두는 이유: `"."` 를 null로 바꾸면 **"발표 안 됨"·"값이 0"·"파싱 실패"가 한 칸에
섞인다.** 해석은 실버가 하고 bronze는 원천을 보존한다.

⚠️ `realtime_start` 클리핑의 뜻 — 요청 구간 시작 이전부터 유효하던 값은 시작일로 잘려 온다. 분석 창
안에서는 정확하지만 **그 이전 시점의 vintage는 이 데이터로 복원할 수 없다.** 창을 넓히려면 상수를 내리고
전량 재적재한다.

### fred_calendar.fred_release_dates

유효 시점을 하루로 고정해 받은 **그날의 릴리스 목록**이다. 파티션 키는 `as_of_date`이고
`release_id`·`release_name`·`release_date`·`release_last_updated`를 담는다.

⚠️ **발표가 일어난 날의 기록이지 앞으로의 예정표가 아니다.** "다음 발표까지 며칠" 같은 선행 피처는 이
테이블로 만들 수 없다 — 만들려면 미래 일정을 받아야 하고, 그러면 **그 시점에 알 수 있었는가**를 다시
따져야 하는 별개의 축이 생긴다.
⚠️ 응답 필드 구성이 **요청 파라미터에 달려 있다**(유효 시점을 고정하지 않으면 한 필드가 오지 않는다).
그래서 프로브는 자산과 **같은 요청 형태**로 묻는다 — 다른 요청을 보내는 프로브의 통과는 자산을 대표하지 않는다.

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

---

## 실버 피처 파이프라인 (주식예측)

Polygon·FRED를 가로질러 조인하는 실버 층이다. 위 SOFA 계열과 달리 **누수 방지(point-in-time)가
설계의 중심**이라 분량이 커서 별 문서로 두었다 — 세션 배정·as-of 조인·라벨 격리 세 축과 그 게이트,
그리고 선언된 공백은 [`dataset_schema/stock-forecast.md`](dataset_schema/stock-forecast.md)가 정본이다.

요약하면 축이 셋이다. ⓐ 기사는 **마감 경계로 거래일에 배정**한다 ⓑ 지표는 **그날 유효했던 vintage**만
붙인다 ⓒ 라벨은 피처와 **다른 테이블**에 두고 pytest가 미래 참조를 막는다.

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
