# GitHub 트렌드 원천 (github_trending)

원천 데이터셋 색인은 [`../dataset_schema.md`](../dataset_schema.md)이고, 이 문서는 Airflow DAG
`github_trending`이 남기는 **raw JSON의 레이아웃·필드·소비 시 함정**을 갖는다.

S3 경로 `s3://warehouse/raw/github_trending/`. 다른 데이터셋과 **두 축이 다르다** — 적재 주체가 Dagster가 아니라
**Airflow DAG**(`images/airflow/dags/github_trending.py`)이고, 산출물이 Iceberg 테이블이 아니라
**raw JSON 파일**이다. 그래서 Iceberg 네임스페이스·`source.yml`·dbt 모델이 없다(아래 §공백).

🔴 **「트렌드」는 근사다.** github.com/trending 은 *기간 안에 받은 별*로 순위를 매기지만, 이 DAG는
GitHub Search API(`GET /search/repositories`)에 `created:>=<기준일 - LOOKBACK_DAYS>`를 걸고 **누적 별 수로
정렬**한 상위 `PER_PAGE`건을 받는다. 즉 *최근 생성된 저장소 중 별이 많은 것*이지 *요즘 별이 몰리는 것*이 아니다 —
오래된 저장소의 재부상은 구조적으로 잡히지 않는다. 기준일은 실행 시각이 아니라 **`dag_run.run_after`를
KST로 바꾼 날짜**라 같은 run을 재실행하면 같은 쿼리를 본다(단, 응답 자체는 호출 시점의 값이다).

**레이아웃.** `dt=<YYYY-MM-DD>/repos.json` — 날짜(KST)당 파일 1개, UTF-8 JSON 객체 1개.
Hive식 `dt=` 접두어는 나중에 외부 테이블·bronze로 올릴 때 파티션 컬럼으로 읽히게 하려는 것이다.

## 봉투(파일 최상위)

| 키 | 타입 | 의미 |
|----|------|------|
| `dt` | string | 파티션 날짜(KST). 경로의 `dt=`와 같은 값 |
| `query` | string | 실제로 보낸 검색 쿼리. 재현·감사용 |
| `collected_at` | string(UTC ISO8601) | 수집 처리시간 |
| `total_count` | integer | 원천이 보고한 **매치 수**. 저장된 `items` 수가 아니다(아래 탐색 상한 참고) |
| `incomplete_results` | boolean | 검색이 시간 제한을 넘겨 **그때까지 찾은 것만** 돌려줬는지 |
| `items` | array | 저장소 목록(아래). 원천의 정렬 순서 그대로 |

## `items[]`

원천 응답 필드 중 `KEEP_FIELDS` 8종만 남기고 나머지(`owner` 객체·라이선스 등)는 버린다.
타입은 원천 OpenAPI 명세(`repo-search-result-item`) 기준이다. **`topics`만 `required`가 아니다** — 원천 응답에
키가 없을 수 있고, DAG가 `repo.get(k)`로 담으므로 그때 저장 값은 `null`이 된다(원천의 null이 아니라 **키 부재**).

| 키 | 타입 | 의미 |
|----|------|------|
| `full_name` | string | `owner/repo`. **저장소의 자연키**. owner가 개인 계정 login일 수 있다 |
| `html_url` | string(uri) | 저장소 웹 URL |
| `description` | string \| null | 저장소 설명(자유 텍스트) |
| `language` | string \| null | 원천이 판정한 주 언어 |
| `stargazers_count` | integer | **수집 시점의 누적** 별 수 |
| `forks_count` | integer | 수집 시점의 누적 포크 수 |
| `created_at` | string(date-time, UTC) | 저장소 생성 시각. 검색 조건이 걸리는 컬럼 |
| `topics` | array\<string\> \| null | 저장소 토픽. 빈 배열과 `null`(원천 키 부재)을 구분한다 |

## 소비 전에 알아야 할 것

- **증가량은 파티션 간 차분으로만 나온다.** `stargazers_count`는 누적 스냅샷이다 — 「하루 동안 받은 별」은
  `full_name`으로 두 `dt`를 조인해 빼야 하고, 한쪽 날짜에 순위 밖이면 그날 값이 **없다**(0이 아니다).
- **같은 저장소가 여러 날 반복 등장하는 것이 정상이다.** 생성 후 `LOOKBACK_DAYS`일 동안 검색 창 안에 있다.
  날짜를 합칠 때 `full_name` 기준 중복을 전제한다.
- **상위 `PER_PAGE`건에서 잘린다.** 페이지네이션을 하지 않는다(원천의 페이지당 상한이 `PER_PAGE`와 같아
  1회 호출로 끝낸다). `total_count`와 `items` 길이의 차이가 잘린 양이다.
  ⚠️ 원천은 **질의가 훑는 저장소 수**와 **검색당 반환 결과 수**에 각각 상한을 둔다(원천 문서 *Search scope
  limits*·*About search*). 그래서 `total_count`를 GitHub 전체의 매치 수로 단정하지 않는다 — 두 상한과
  `total_count`의 관계는 원천 문서가 말하지 않는다(`미확인`).
- **`incomplete_results`는 기록만 한다.** `true`여도 실패시키지 않는다(관측 —
  [conventions/data-quality.md](../conventions/data-quality.md)). 그날 순위의 신뢰도를 따질 때 먼저 본다.

**멱등·실패.** 쓰기는 `replace=True`로 그날 파일을 덮어쓴다 — 재실행은 멱등이지만 **이전 응답은 남지 않는다**
(버전 보존 없음). 결과 0건은 「트렌드 없음」이 아니라 쿼리·원천 이상으로 보고 **실패**시킨다 — 빈 파일이
성공으로 남으면 하류에서 「그날 0건」과 구분되지 않는다. 쓰기 태스크는 Airflow Asset
`s3://warehouse/raw/github_trending`을 outlet으로 선언한다.

**호출 한도.** 하루 1회 호출이라 **비인증 Search 한도 안**에 든다. Variable `github_token`은 선택이고
있으면 한도만 올라간다. 실패 시 재시도는 `default_args`(지연 + 지수 백오프)를 따른다.

## 공백

bronze(Iceberg) 테이블·`source.yml`·dbt 모델이 **없고**, 위 Asset을 받는 하류 DAG도 없다 —
raw 착지에서 멈춘 상태다(**건너뛴 것이지 빠뜨린 것이 아니다**). 이 데이터를 분석에 쓰려면 bronze 적재를
먼저 둔다(임시 SQL 결과를 리포트에 옮기지 않는다 — [conventions/analysis.md](../conventions/analysis.md)).
거버넌스 판정은 [security/datasets.md](../security/datasets.md)(security §0-1) — **저장소 단위 값은 공개물에 담지 않는다.**

## 참고

- GitHub Docs — *REST API endpoints for search › Search repositories* (응답 스키마·`incomplete_results`·
  `per_page` 상한·Search 한도): https://docs.github.com/en/rest/search/search
- GitHub Docs — *Rate limits for the REST API*: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- GitHub REST API OpenAPI 명세 — `components.schemas.repo-search-result-item`의 `required`·`nullable`:
  https://github.com/github/rest-api-description (`descriptions/api.github.com/api.github.com.json`)
- github.com/trending 에 공식 API가 없다는 서술은 DAG docstring의 주장이며 1차 출처로 확인하지 않았다(`미확인`).
