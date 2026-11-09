# NVD 취약점 원천 (nvd)

원천 데이터셋 색인은 [`../dataset_schema.md`](../dataset_schema.md)이고, 이 문서는 Airflow DAG
`nvd_cves`·`nvd_cve_history`·`nvd_kev`가 적재하는 **raw 스냅샷 레이아웃**과 **bronze 세 테이블**을 갖는다.

Iceberg 네임스페이스 `nvd`. 원천은 셋이다 — NVD **CVE API 2.0**(CVE 레코드의 현재 버전),
**CVE Change History API 2.0**(변경 이벤트), CISA **KEV**(악용 확인 취약점 카탈로그, 키 없음).
bronze 적재 주체는 **Airflow DAG 3개**(raw 착지 → bronze 적재 2태스크)이고, 펼침(silver)은 dbt `models/nvd/`를
Airflow DAG `nvd_dbt`(Cosmos)가 빌드한다. bronze 태스크가 Asset을 갱신하면 `nvd_dbt`가 트리거된다([§silver](#silver-dbt)).

| DAG (raw 태스크 → bronze 태스크) | 적재 모드 | 스케줄(기본 일시정지) |
|------|-----------|-----------------------------|
| `nvd_cves` (`land_raw_nvd_cves` → `load_nvd_cves`) | 일자 파티션 교체(`window_date`) | `0 19 * * *` UTC, 데이터 구간 시작일 하루 |
| `nvd_cve_history` (`land_raw_nvd_cve_history` → `load_nvd_cve_history`) | 일자 파티션 교체(`window_date`) | `0 19 * * *` UTC, 데이터 구간 시작일 하루 |
| `nvd_kev` (`land_raw_nvd_kev` → `load_nvd_kev`) | 테이블 전체 overwrite | 매시간(KST), 내용 해시가 같으면 스킵 |

세 DAG 모두 `catchup=False`다 — 과거 구간은 `airflow backfill create`로 수동 채운다
([컨벤션](../conventions/airflow.md#2-규칙)).

## raw 스냅샷

**raw를 먼저 남긴다.** 피드마다 수집 태스크(`land_raw_nvd_*`)가 API 응답을 받은 바이트 그대로
S3에 스냅샷으로 놓고, bronze 태스크(`load_nvd_*`)가 그것을 읽는다. API에서 bronze로 바로 가지 않는 이유:
NVD 일별 구간은 **다시 물으면 답이 바뀐다** — 그 사이 다시 수정된 CVE는 `lastModified`가 다른 날로
옮겨간다. 과거 파티션의 "그때 받은 것"은 재현할 수 없는 관측이라 raw가 보존한다.

| 피드 | S3 레이아웃 | 파티션 |
| --- | --- | --- |
| CVE | `raw/nvd/cves/dt=YYYY-MM-DD/fetched=<회차>/page-NNNN.json.gz` | 일자(UTC) — `lastModStartDate`~`lastModEndDate` |
| Change History | `raw/nvd/cve_history/dt=YYYY-MM-DD/fetched=<회차>/page-NNNN.json.gz` | 일자(UTC) — `changeStartDate`~`changeEndDate` |
| KEV | `raw/nvd/kev/dt=<수집일 UTC>/fetched=<회차>/kev.json.gz` | 없음(수집 회차 스냅샷) — 내용이 같으면 회차를 만들지 않는다 |

`<회차>`는 UTC 수집 시각 `YYYYMMDDTHHMMSSZ`다(사전순 = 시간순). bronze의 `fetched_at`은 이 값이다.

- **수집 회차마다 경로가 갈리고 어떤 실행도 기존 객체를 지우거나 덮어쓰지 않는다.** 새 회차 경로에
  객체가 이미 있으면 실패한다.
- 객체마다 `<key>.sha256` 사이드카가 붙고 **쓰기 순서는 데이터 → 사이드카**다. 회차의 커밋 표지는
  **page-0000(KEV는 `kev.json.gz`)의 사이드카**다 — 맨 마지막에 쓰므로, 표지가 없는 회차는 중간에 죽은 것이고
  bronze는 읽지 않는다. bronze는 dt마다 **가장 최근에 커밋된 회차**를 읽는다(KEV는 전체에서 최근).
- **CVE·History**는 해당 dt에 커밋된 회차가 있으면 **기본 스킵**한다(`skipped_committed`). DAG param
  `force=true`는 **새 회차를 추가할 뿐 기존 스냅샷을 지우지 않는다** — force가 도중에 실패하면
  직전 커밋 회차가 그대로 최신으로 읽힌다.
- **KEV**는 dt 스킵이 없다 — 항상 GET하고 **내용 해시**(gzip sha256)가 직전 커밋 회차와 같으면 쓰지 않는다
  (`skipped_unchanged`). 적재 쪽은 **테이블의 `fetched_at`이 최신 회차와 같으면** 건너뛴다(스킵의 정본은 이쪽).
- 커밋 **전에** 절단(`count`·`totalResults` 대조)과 레코드 ID 중복을 거른다 — 깨진 회차가 최신이 되지 않게.
- 파티션 구간은 `D T00:00:00.000Z` ~ `D T23:59:59.999Z`다. 끝을 다음 날 0시로 두면 경계 시각의
  레코드가 이웃 파티션과 두 번 세진다. 과거 전체는 **`airflow backfill create`**로 받는다.
- 🔴 **받은 합계 ≠ `totalResults`면 실패한다**(조용한 절단 금지). 순회 도중 `totalResults`가 바뀌어도
  실패한다 — 구간 안 레코드가 이동하면 `startIndex`가 한 건을 건너뛸 수 있다.
- 🔴 **두 일자 피드의 시작일이 다르다.** CVE 피드의 `lastModified`는 재수정될 때마다 앞으로 끌려와
  과거가 비지만, Change History는 변경이 일어난 시각을 보존한다. 두 상수를 「맞추면」 History 앞 24년이
  에러 없이 수집 범위 밖이 된다 — 이 가드의 근거를 각 DAG 파일의 `start_date` 옆 주석에 두었고, 값의 근거는 프로브다.
- 요청 간 최소 간격은 run 안에서 지키고, **run 사이**는 API 태스크(`land_raw_nvd_cves`·`land_raw_nvd_cve_history`)의
  Airflow Pool `nvd_api`(slots 1)가 막는다. Pool은 차트 PostSync Job이 선언한다([컨벤션](../conventions/airflow.md#2-규칙)).
  Dagster 때의 「한도 미설정이면 실패」 검사는 이식하지 않았다 — Pool이 없을 때 스케줄러 동작은 `미확인`이다
  ([`../setup.md`](../setup.md) §6-3).

## nvd.nvd_cves

| 컬럼 | 타입 | 의미 |
|------|------|------|
| `window_date` | date | 조회한 UTC 하루 = 파티션 키(교체 필터) |
| `cve_id` | string | CVE ID |
| `published`·`last_modified` | timestamp(UTC) | 공개·마지막 수정 시각 |
| `vuln_status` | string | 분석 상태 |
| `raw_json` | string | `vulnerabilities[].cve` 객체 원문 |
| `fetched_at` | timestamp(UTC) | 읽은 raw 회차의 수집 시각(경로 `fetched=`) |

**같은 `cve_id`가 여러 파티션에 있는 것이 정상이다** — 수정될 때마다 그날 파티션에 그 버전이 한 행
생긴다(버전 이력이 행으로 누적). 현재 버전 1행으로 접는 것은 silver의 일이다. 한 파티션 **안에서는**
`cve_id`가 유일하다(중복이면 적재가 실패한다).

## nvd.nvd_cve_history

| 컬럼 | 타입 | 의미 |
|------|------|------|
| `window_date` | date | 조회한 UTC 하루 = 파티션 키 |
| `cve_change_id` | string | 변경 이벤트 ID(파티션 안에서 유일) |
| `cve_id` | string | 대상 CVE |
| `event_name` | string | 이벤트 종류 |
| `created` | timestamp(UTC) | 변경 시각 |
| `raw_json` | string | `cveChanges[].change` 객체 원문(`details` 포함) |
| `fetched_at` | timestamp(UTC) | 읽은 raw 회차의 수집 시각 |

CVE 피드는 "지금의 마지막 버전"만 주므로 **그 사이의 수정은 이 테이블에만 남는다**.

## nvd.nvd_kev

| 컬럼 | 타입 | 의미 |
|------|------|------|
| `catalog_version` | string | 카탈로그 버전 |
| `date_released` | timestamp(UTC) | 카탈로그 발행 시각 |
| `cve_id` | string | CVE ID(유일 — 중복이면 적재 실패) |
| `date_added`·`due_date` | date | 등재일·조치 기한 |
| `known_ransomware_campaign_use` | string(nullable) | 선택 필드 — 없으면 NULL(빈 문자열로 채우지 않는다) |
| `raw_json` | string | `vulnerabilities[]` 항목 원문(`cwes`·`notes` 등 선택 필드 포함) |
| `fetched_at` | timestamp(UTC) | 읽은 raw 회차의 수집 시각 |

가장 최근에 커밋된 raw 스냅샷으로 **테이블 전체를 교체**한다. `drop_table` 재생성이 아니라
`Table.overwrite`라 스냅샷 계보가 이어진다(`lakehouse.iceberg.overwrite_table`). 착지 전과 적재 전에
`count == len(vulnerabilities)`를 대조한다. 적재 스킵은 **테이블의 `fetched_at`이 최신 회차와 같은지**로만
판정한다(raw 쪽 `skipped_unchanged`는 판정이 아니다). 스킵된 회차가 Asset 이벤트를 내는지는 `미확인`이다.

## silver (dbt)

`dbt_pipelines/models/nvd/` — 스키마는 bronze와 같은 `nvd`, 레이어는 `tags=['silver']`로 표기한다.
빌드 소유자는 Airflow DAG `nvd_dbt`(`images/airflow/dags/nvd_dbt.py`)이고, 스케줄은 CVE·History bronze Asset의
AND다(`nvd_cves`·`nvd_cve_history`의 bronze 태스크가 Asset을 내는 생산자다).

| 모델 | 1행 | 원천 |
|------|-----|------|
| `nvd_cve_latest` | CVE(현재 버전) — 스칼라·영어 설명·`is_rejected`·원문 | `nvd_cves` |
| `nvd_cve_metrics` | (CVE, `cvss_version`, `source`, `source_type`) — CVSS v2·v3.0·v3.1·v4.0 | `nvd_cve_latest` |
| `nvd_cve_cwe` | (CVE, `source`, `cwe_id`) — weaknesses 1:N | `nvd_cve_latest` |
| `nvd_cve_cpe` | (CVE, `config_idx`, `node_idx`, `match_idx`) — cpeMatch·`part`·`vendor`·`product`·버전 범위 | `nvd_cve_latest` |
| `nvd_cve_changes` | (`cve_change_id`, `detail_idx`) — 변경 이벤트의 details | `nvd_cve_history` |
| `nvd_kev_entries` | CVE — KEV 스칼라 + `vendor_project`·`product`·`vulnerability_name` | `nvd_kev` |

- **최신 = `last_modified` 최대, 동률이면 `window_date` 최대.** 백필 파티션은 「받은 날의 현재 버전」이라
  window_date만으로 고르면 옛 버전을 고를 수 있다.
- **이력의 정본은 `nvd_cve_changes`다.** 버전 행 diff(SCD2)는 증분 시작 이후에만 존재해 두지 않는다.
- silver는 **해석하지 않는다** — 대표 CVSS 점수, Rejected 제외, KEV 조인, CPE operator·negate 해석은
  gold의 몫이다. KEV도 silver(`nvd_kev_entries`)를 거친다 — gold가 source를 바로 읽지 않는다
  ([analysis.md](../conventions/analysis.md) §2).
- 🔴 **`nvd_kev_entries`는 `nvd_dbt`가 아니라 `alerts_dbt`가 빌드한다**(`tags=['silver','alerts']`).
  `nvd_dbt`는 `path:models/nvd`에서 `tag:alerts`를 `exclude`해 같은 모델을 두 DAG가 빌드하지 않는다.
- CPE `vendor`·`product`는 이스케이프된 콜론(`\:`)에서 자르지 않고 뽑은 뒤 이스케이프를 푼다.
- JSON은 `macros/cross_engine.sql`의 `json_value_at`·`json_array_at`·`unnest_array_ordinal`(Trino만 구현)로 펼친다.
- **경로 대조는 「키가 있는 원문 수 = 펼친 행의 고유 수」로 한다** — `raw_json`에 `cvssMetric*`·비지 않은
  `weaknesses`·`configurations`가 있는 CVE 수와 `details`가 있는 이벤트 수가 각 silver의 고유 `cve_id`(changes는
  `cve_change_id`) 수와 같아야 한다. 어긋나면 경로가 틀려 **에러 없이 0행**이 된 것이다.
- ⚠️ **최신 선택(동률 포함)과 CPE 이스케이프는 unit_tests만 받친다** — bronze에 CVE당 버전이 하나뿐이거나
  이스케이프된 criteria가 없으면 실데이터는 그 분기를 지나지 않는다(통과가 검사를 뜻하지 않는다).
- Cosmos는 모델마다 `.run`·`.test` 태스크만 만들고 unit_test 전용 태스크는 없다. `.test`의
  `--select <모델>`(cautious)은 그 모델의 unit_test도 고르므로(`dbt ls` 동등 셀렉터 기준 — Airflow 실행으로는
  미관측) unit_test는 **모델 빌드 뒤에** 돈다. 빌드 전에 막는 게이트는 로컬 `dbt build`다.

## gold (dbt)

`nvd.nvd__cve_digest`(`tags=['gold','alerts']`) — grain은 **CVE당 1행(Rejected 제외)** 이다. 알림이 읽는 CVE 요약이고,
빌드 소유자는 Airflow DAG `alerts_dbt`(`images/airflow/dags/alerts_dbt.py`)다. 스케줄은 NVD silver·GeekNews silver·
KEV bronze Asset의 **OR**다. 정의의 정본은 `models/nvd/tables/schema.yml`의 description이다.

| 컬럼 | 규칙 |
|------|------|
| `cve_id`·`published`·`last_modified` | `nvd_cve_latest`에서 가져온다. `is_rejected`인 CVE는 **행이 없다** |
| `cvss_version`·`cvss_score`·`cvss_severity` | 대표 CVSS: 버전 4.0 > 3.1 > 3.0 > 2.0 → `source_type` Primary > Secondary → `base_score` 큰 쪽 → `source` 사전순. 메트릭이 없으면 NULL |
| `is_kev` | `nvd_kev_entries`에 있으면 true, 없으면 **false**(NULL 아님) |
| `kev_date_added`·`kev_vendor_project`·`kev_product` | KEV에 있을 때만 값이 있다 |
| `cpe_vendor_products` | `nvd_cve_cpe`에서 `vulnerable = true`인 행의 `'vendor:product'` distinct 정렬 배열. 없으면 **빈 배열** |

- 🔴 **NVD `description_en`은 gold에 없다.** 재배포 가능 여부가 `미확인`이라 알림 경로에 싣지 않는다
  ([security/datasets.md](../security/datasets.md) §Discord 발신 판정). silver `nvd_cve_latest`에는 남아 있다.
- **관심목록(watchlist) 필터는 gold에 없다.** gold는 비공개 입력 없이 재현돼야 하므로, 필터는 발신층 Python이
  `cpe_vendor_products`에 건다.
- 버전이 점수보다 앞선다 — v4.0 점수가 v3.1보다 낮아도 v4.0을 고른다. 같은 CVE의 「가장 높은 점수」가 필요한
  질문이면 이 컬럼이 아니라 `nvd_cve_metrics`를 읽는다.
- ⚠️ `cpe_vendor_products`는 operator(AND/OR)·negate를 해석하지 않는다. 「영향받는 제품 목록」이 아니라
  「vulnerable로 표시된 조건에 나온 vendor:product」다.
- ⚠️ 대표 CVSS·Rejected 제외·KEV 조인은 unit_tests가 받친다. **CPE 배열(빈 배열 분기 포함)에는 unit_test가 없다** —
  array 기대값을 dict 픽스처로 줄 수 없어 미작성이고, `not_null`이 「NULL 대신 빈 배열」만 지킨다.

## 함정

⚠️ **최신 회차가 해시 불일치면 bronze가 실패한다**(옛 회차로 조용히 내려가지 않는다). 복구는 raw 태스크를
`force=true`로 다시 돌려 **새 회차를 추가**하는 것이다 — 깨진 회차를 지우지 않으므로 비파괴다.

⚠️ **NVD 시각 문자열에는 오프셋이 없다.** bronze는 이것을 **UTC로 해석하고, 그 가정은 미확인이다**
(API 파라미터는 `Z`로 받으므로 개연성은 높지만 응답 쪽을 명시한 1차 문서를 읽지 못했다). 틀리면 모든
시각이 같은 크기로 밀려 에러 없이 들어간다. 검산 수단으로 bronze 태스크가 파티션 구간 밖 시각 수
(`last_modified_outside_window`·`created_outside_window`)를 Asset event 메타데이터로 남긴다 — 판정 없이 관측만 한다.

⚠️ **`fetched_at`은 raw 태스크 프로세스의 시계로 찍은 회차 시각이다**(스토리지 시계가 아니다). 객체를
다시 복사해도 바뀌지 않지만, 수집 호스트의 시계가 어긋나 있었다면 그만큼 밀린 채 고정된다.

⚠️ **이용 조건**: 판정 정본은 [security/datasets.md](../security/datasets.md)다. KEV는 CC0 1.0이고,
NVD(CVE·Change History)는 NVD Terms of Use에 따라 **수집·분석이 허용**된다. 재배포는 `미확인`이라
원천 값을 저장소·공개물에 담지 않는다.
