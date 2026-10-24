# USGS 수문 원천 (usgs_water)

원천 데이터셋 색인은 [`../dataset_schema.md`](../dataset_schema.md)이고, 이 문서는 `defs/usgs_water/`가
적재하는 **bronze 두 테이블**과 그 위의 **Flink 스트리밍 파생 테이블**을 갖는다.

Iceberg 네임스페이스 `usgs_water`. 원천은 USGS NWIS Water Services(공개 HTTP API, 무인증)이고
대상은 `TARGET_STATES`·`PARAMETER_CODES`(유량 `00060`·수위 `00065`)로 좁혀 둔다(`constants.py`).
다른 데이터셋과 달리 **스트리밍 실습의 소스**로 설계됐다 — 그래서 bronze 적재 모드가 전부 `append`이고,
중복이 **의도적으로** 쌓인다(아래). dbt 모델·`source.yml`이 없다 — 하류는 dbt가 아니라 Flink다.

| 자산 | 원천 형식 | 적재 모드 | 스케줄(KST, 기본 `STOPPED`) |
|------|-----------|-----------|-----------------------------|
| `water_iv_raw` | IV 서비스 JSON | `append` | `usgs_water_iv_schedule` — 15분 |
| `water_sites` | Site 서비스 RDB(탭 구분) | `append` | `usgs_water_sites_schedule` — 일 1회 |

두 자산은 주기가 자릿수로 달라 **그룹이 아니라 개별 선택**으로 잡을 나눈다(`defs/automation.py`).
두 스케줄 모두 외부 API를 주기 호출하므로 **기본 정지**이고 켜는 시점은 사람이 정한다.

## usgs_water.water_iv_raw

순간값(instantaneous values). 관측점 1개 = 1행.

| 컬럼 | 타입 | 의미 |
|------|------|------|
| `site_no` | string | 관측소 번호. dedup 키 ① · 조인 키 |
| `station_nm` | string | 관측소 이름(응답 그대로) |
| `parameter_cd` | string | 측정 항목 코드(`00060` 유량 · `00065` 수위). dedup 키 ② |
| `unit_code` | string | 단위. 값 해석에 필요하다 |
| `value` | string | **원문 문자열.** 결측은 원천의 `noDataValue` 표기로 온다 — 캐스팅은 실버의 몫 |
| `qualifiers` | array\<string\> | 품질 표기 **배열 그대로**(`P` 외 코드가 함께 붙는다 — 문자열로 접으면 조합이 손실된다) |
| `date_time` | timestamp(UTC) | **이벤트타임**(관측 시각). dedup 키 ③ · Flink 워터마크 기준 |
| `ingested_at` | timestamp(UTC) | **처리시간**(수집 시각). dedup의 정렬 키 |

🔴 **같은 `(site_no, parameter_cd, date_time)`이 여러 행인 것이 정상이다.** 매 실행이 `LOOKBACK` 창을
**다시** 긁으므로 겹친 구간이 반복 적재된다. 값이 수정되면 새 버전이 함께 들어온다 — 최신 판은
키별 `ingested_at` 최대값으로 고른다. 버그가 아니라 **dedup·값 수정 관측의 실습 재료**다.
⇒ 이 테이블에서 행 수를 그대로 세면 **관측점 수가 아니라 적재 횟수가 섞인 값**이 나온다.

⚠️ **`qualifiers`의 `P`(Provisional)는 승인 상태 전이를 뜻하지 않는다.** IV 응답은 오래된 구간도 `P`로 오므로
이 창이 잡는 것은 **값 수정(revision)** 이지 `P→A` 전이가 아니다(근거는 `constants.py` `LOOKBACK` 주석).

**왜 `append` 전용인가.** 이 테이블은 Flink Iceberg 스트리밍 소스다. 그 소스는 증분 append 스냅샷만 따라가므로
overwrite 스냅샷이 한 번이라도 생기면 소스 자격을 잃고, **그 실패는 에러가 아니라 조용한 누락**이다.
그래서 IO 매니저를 쓰지 않고 테이블 바인딩(`usgs_water_iv_table`)으로 직접 append한다.

## usgs_water.water_sites

관측소 메타 — IV의 **조인 차원**. RDB 응답(원본은 열이 훨씬 많다)에서 `SITE_COLUMNS`만 추린다.

| 컬럼 | 타입 | 의미 |
|------|------|------|
| `agency_cd` | string | 기관 코드 |
| `site_no` | string | 관측소 번호(조인 키) |
| `station_nm` | string | 관측소 이름 |
| `site_tp_cd` | string | 관측소 유형 |
| `dec_lat_va` · `dec_long_va` | string | 위도·경도(십진) |
| `state_cd` · `county_cd` | string | 주·카운티 코드 |
| `huc_cd` | string | 유역 단위 코드(HUC) |
| `drain_area_va` | string | 유역면적 |
| `alt_va` | string | 고도 |
| `tz_cd` | string | 관측소 시간대 |
| `ingested_at` | timestamp(UTC) | 수집 처리시간 |

전 컬럼이 **문자열**이다 — RDB는 타입이 없고 빈 값이 흔해 bronze에서 강제하지 않는다(빈 값은 `null`).
RDB의 **컬럼 폭 지정자 행**은 데이터가 아니라 파서가 버린다(읽으면 유령 관측소 1행이 생긴다).

🔴 **`replace`를 쓰지 않는다 — 그래서 관측소가 실행마다 다시 쌓인다.** 「스트리밍 소스가 아니다」와
「잡이 읽지 않는다」는 다르다 — 조인 상대도 잡이 읽는다. `replace`는 `drop_table` 후 재생성이라 도는 잡의
조인 대상을 없애고, 새 테이블은 **새 루트**라 스냅샷 계보가 끊긴다. 최신 판은 `site_no`별
`ingested_at` 최대값으로 고른다.

## 파생 — Flink 스트리밍 PoC (`k8s/flink/usgs-water-stream-job.yaml`)

| 테이블 | 만드는 SQL | 내용 |
|--------|-----------|------|
| `usgs_water.iv_stream_echo` | `03-usgs-echo.sql` | `water_iv_raw`를 스트리밍으로 읽어 그대로 옮긴 것 + `processed_at` |
| `usgs_water.iv_joined` | `04-usgs-join.sql` | `water_iv_raw` ⋈ `water_sites`(**regular join**) — `station_nm`·`huc_cd` 부착 |

🔴 **분석 입력으로 쓰지 않는다.** 둘 다 스트리밍 동작(증분 읽기·조인 상태 증가)을 관측하려는 **PoC 산출물**이다.
`iv_joined`는 regular join이라 상태가 TTL 없이 계속 자란다 — 결함이 아니라 관측 대상으로 둔 것이다.
⚠️ 조인 상대 `water_sites`는 위처럼 **판이 누적되는 테이블**이고 조인은 `site_no`만으로 건다. 코드상
**관측소 판이 여러 개면 IV 1행이 판 수만큼 조인된다**(조인 전 최신 판 선택이 없다) — 이 테이블의 행 수를
관측점 수로 읽지 않는다. 데이터로 재현하지는 않았다.
이 중복은 **수용**한다 — 잡의 목적이 값이 아니라 상태 증가 관측이기 때문이다. 고칠 때의 선택지
(Flink Top-N은 갱신 스트림이라 기본키 없는 싱크와의 호환이 `미확인`, 배치 쪽 최신 판 뷰와 조인)는
SQL 주석에 둔다.

## 거버넌스

미국 공공영역(재배포 제한 없음)이고 출처 표기는 **권고**다. 개인 단위 관측이 아니라 재식별·마스킹 통제는
비적용이다. 판정·근거·`미확인` 잔여는 [`../security/datasets.md`](../security/datasets.md)(security §0-1).
값을 공개물에 담을 때는 출처(**U.S. Geological Survey, National Water Information System**)를 붙인다.

## 참고

- 정의: `defs/usgs_water/assets.py`(스키마·파서·적재 모드 근거), `constants.py`(대상·창·호출 한도 근거),
  `defs/automation.py`(잡·스케줄), `defs/resources.py`(테이블 바인딩)
- 파서 단위 테스트: `tests/test_usgs_water_parse.py`
- USGS Water Services 문서: https://waterservices.usgs.gov/docs/
