# GitHub 트렌드 bronze 적재 설계 (github_trending)

> **상태: 설계만 있고 구현은 없다.** 구현은 후속 PR이 이 문서를 입력으로 삼는다(Issue #257).
> raw JSON의 레이아웃·필드는 [`github-trending.md`](github-trending.md)가 정본이고,
> 이 문서는 그 raw를 Iceberg bronze로 올리고 dbt로 잇는 **결정과 그 근거**를 갖는다.

## 0. 확정된 전제

- **적재 주체는 Airflow다**(사용자 결정). 수집 DAG가 이미 Airflow에 있고(`images/airflow/dags/github_trending.py`),
  두 오케스트레이터 경계를 bronze 앞에서 한 번 더 넘지 않는다.
- **수단은 Airflow 태스크 안의 pyiceberg**다. Dagster와 **같은 라이브러리·같은 JDBC 카탈로그**를 쓴다
  — Dagster 리소스는 pyiceberg `type: sql` 카탈로그로 같은 Postgres를 본다(`defs/resources.py:86-101`).
- 카탈로그 이름은 **`iceberg`** 로 통일한다. JDBC 카탈로그는 `catalog_name`으로 레지스트리를 나눠, 이름이 다르면
  같은 DB를 봐도 서로의 테이블이 보이지 않는다(`common/constants.py:17`, `gitops/apps/trino/values.yaml:50-54`).

### 기각한 수단

| 대안 | 기각 사유 |
| --- | --- |
| Trino provider + SQL(`INSERT`/`DELETE`) | JSON 파싱을 SQL로 하거나 행을 문자열로 조립해야 한다. 덮어쓰기가 `DELETE`+`INSERT` 두 문장이라 원자성이 없다 |
| Spark 배치(SparkApplication) | 파티션당 `PER_PAGE`행 이하의 작업에 드라이버·익스큐터 파드를 띄운다. CPU 축이 급소인 동시 기동 예산에 맞지 않는다 |
| Dagster 에셋(raw를 `deps`로) | 사용자 결정으로 닫혔다. 호스트 Dagster가 클러스터 Airflow의 Asset 이벤트를 볼 경로도 없다 |

## 1. 태스크 구성 — 같은 DAG에 `load_bronze`를 잇는다

**권고: 기존 `write_raw` 뒤에 `load_bronze` 태스크를 같은 DAG에 둔다.**

```text
fetch_trending ──▶ write_raw(outlet RAW_ASSET) ──▶ load_bronze
```

- `load_bronze`는 **XCom 페이로드가 아니라 `write_raw`가 돌려준 S3 객체를 다시 읽는다**(`github_trending.py:124-126`).
  bronze가 늘 raw에서 파생되므로 raw만 있으면 bronze를 재현할 수 있다. XCom에 실린 `items`는 쓰지 않는다.
- 같은 run 안이라 `dt`가 경로에서 확정된다. 실패하면 그 run이 빨갛게 남아 「raw는 있는데 bronze가 없다」가 보인다.
  재실행은 `load_bronze` 태스크만 clear하면 된다.

| 선택지 | 장점 | 비용 |
| --- | --- | --- |
| **같은 DAG**(권고) | 의존이 코드 한 줄. `dt`를 경로로 받는다. 감시 대상 DAG 1개 | 수집과 적재의 재시도 정책이 `default_args`를 공유한다 |
| `RAW_ASSET` outlet으로 트리거되는 별도 DAG | 적재만 독립 재처리 | 트리거 이벤트에서 `dt`를 꺼내는 배선이 는다. 받는 하류 소비자가 없어 분리 이득이 없다 |

- 재검토 트리거: **raw를 여러 `dt`에 걸쳐 일괄 재적재할 일**(스키마 변경 뒤 전체 재적재 등)이 생길 때.
  그때는 별도 DAG가 수동 트리거 단위로 낫다.
- bronze용 Airflow Asset outlet은 **두지 않는다**. 이 Asset을 받는 하류 DAG가 없다(YAGNI).
- 실행기는 기본 Celery다. 파티션당 행 수가 `PER_PAGE` 이하라 K8s 태스크 파드로 격리할 이유가 없다.
  pyiceberg import가 worker 메모리에 주는 부담은 `미측정`이다.

## 2. 스키마 — 테이블 하나, 봉투는 컬럼으로

**테이블 `iceberg.github_trending.trending_repos`**(네임스페이스 = 데이터셋명, 다른 데이터셋 관례와 같다).
**grain: 1행 = (`dt`, `full_name`)** — `items[]`를 편 long 테이블이다.

### 봉투를 같은 테이블에 두는 이유

**권고: `query`·`total_count`·`incomplete_results`·`collected_at`을 같은 테이블의 컬럼으로 둔다.**

- **원자성.** Iceberg 커밋은 테이블 단위다. 메타 테이블을 따로 두면 한 `dt`의 교체가 두 커밋으로 갈려,
  한쪽만 성공한 상태가 관측될 수 있다. 테이블이 하나면 `dt` 하나가 커밋 하나다.
- **봉투만 있는 날이 없다.** DAG는 결과 0건을 실패로 처리한다(`github_trending.py:93-97`). 그래서 「봉투는 있고
  저장소가 0행」인 파티션이 생기지 않고, 봉투를 행과 분리해 보존할 이유가 없다.
- **비용.** 반복되는 값은 스칼라 4개이고 Parquet 사전 인코딩이 흡수한다. 조회 때 조인도 없다.
- 대가: `dt`별 봉투를 볼 때 `select distinct`가 필요하다. 그 조회가 3회 반복되면 dbt 모델로 승격한다(Rule of Three).

### 컬럼

| 컬럼 | Iceberg 타입 | null | 출처·비고 |
| --- | --- | --- | --- |
| `dt` | `date` | 불가 | 봉투 `dt`(KST 파티션 날짜). 파티션 키 |
| `rank` | `int` | 불가 | `items` 안의 1부터 센 위치. 원천 정렬 순서를 행으로 펴도 잃지 않게 한다 |
| `full_name` | `string` | 불가 | 자연키. login이 섞일 수 있다(§7) |
| `html_url` | `string` | 불가 | |
| `description` | `string` | 허용 | |
| `language` | `string` | 허용 | |
| `stargazers_count` | `long` | 불가 | 수집 시점 누적값 |
| `forks_count` | `long` | 불가 | 수집 시점 누적값 |
| `created_at` | `timestamptz` | 불가 | 원천 UTC 문자열을 파싱해 UTC로 저장(timezone 규약) |
| `topics` | `list<string>` | 허용 | `null`(원천 키 부재)과 `[]`를 **구분해 보존**한다(#261). 원소는 nullable로 둔다 |
| `query` | `string` | 불가 | 봉투 |
| `total_count` | `long` | 허용 | 봉투. DAG가 `body.get`으로 담아 부재 시 `null`이다 |
| `incomplete_results` | `boolean` | 허용 | 봉투. 같은 이유로 `null` 가능 |
| `collected_at` | `timestamptz` | 불가 | 봉투(UTC) |

- **Arrow 스키마를 코드에 명시**해 쓰기 직전에 맞춘다. 추론에 맡기면 첫 날의 값 분포가 타입을 정한다
  (예: 모든 `topics`가 `null`인 날은 원소 타입을 알 수 없다). 필수 컬럼은 Arrow 필드도 `nullable=False`로 둔다.
  pyiceberg가 nullable 불일치를 어느 방향으로 거부하는지는 `미확인`이라 구현 PR의 검증 항목이다(§8).
- **파티션 스펙은 `identity(dt)`** 다. `day(collected_at)` 변환을 쓰지 않는다 — 변환은 UTC 기준이라
  KST 날짜와 하루가 어긋난다. 파티션 날짜는 이미 `dt`로 정해져 있다.
- 스키마 진화는 자동으로 하지 않는다. 테이블이 없으면 만들고(`create_table_if_not_exists`),
  있으면 기대 스키마와 비교해 다르면 **실패**한다. `KEEP_FIELDS`를 바꾸는 변경은 스키마 진화를 같은 PR에 담는다.

## 3. 적재 모드 — `dt` 파티션 덮어쓰기

**권고: `table.overwrite(df, overwrite_filter=EqualTo("dt", <dt>))`** — 필터가 파티션 경계와 일치하므로
삭제는 파일 단위로 끝나고 다른 `dt`는 건드리지 않는다. 삭제와 추가가 **한 트랜잭션**으로 커밋된다.

- 쓰기 직전에 `df`의 모든 행이 필터와 같은 `dt`인지 확인한다. 어긋나면 쓰지 않고 실패한다
  — 필터와 데이터가 갈리면 「그날을 지우고 다른 날을 쓰는」 조용한 손상이 된다.
- 쓰지 않는 것:
  - **`append`** — 재실행할 때마다 행이 배로 는다.
  - **drop 후 재생성·`replace`** — 스냅샷 계보가 끊기고 다른 `dt`가 함께 사라진다.
    덮어쓰기는 계보를 유지하므로 이전 상태를 time travel로 볼 수 있다.
- 대안 `dynamic_partition_overwrite(df)`는 `df`에 든 파티션을 알아서 교체한다. 필터를 쓰지 않아
  어긋날 여지는 줄지만 **무엇을 지우는지가 코드에 드러나지 않는다**. 명시 필터를 택한다.
  두 API가 고정할 판본에 있는지와 한 run이 남기는 스냅샷 수는 `미확인`이다(§8에서 확인).
- **멱등 판정은 「행 수 불변」만으로 하지 않는다.** 두 번째 실행이 아예 쓰지 않았어도 행 수는 같다.
  행 수 불변 **과** 새 스냅샷의 생성(재실행이 실제로 커밋했다)을 함께 본다(§8).

## 4. 의존성·배선

### 4-1. pyiceberg 설치·버전 고정

- 지금은 `Dockerfile`에 pip 설치가 없다 — 공식 base에 필요한 provider가 다 있어서다(`conventions/docker.md:222`).
  pyiceberg는 base에 없다고 보고(`미확인` — 구현 PR에서 이미지 안에서 확인) **`images/airflow/requirements.txt`에
  `==`로 고정**해 `Dockerfile`에서 설치한다.
- 설치는 Airflow 자신의 판본을 함께 고정해 연쇄 업그레이드를 막는 형태로 한다
  (`apache-airflow==<base 판본>`을 같은 설치 명령에).
  extra는 `pyarrow`(FileIO·Arrow 변환)와 SQL 카탈로그의 Postgres 드라이버다.
- **Dagster와 같은 판본 계열**을 고른다. Dagster는 `pyiceberg>=0.9`로 하한만 두고 잠금 파일이 없어
  실제 판본이 설치 시점에 정해진다(`dagster/dockerfile.d/src/pyproject.toml:44`). 두 런타임이 같은 카탈로그
  스키마에 쓰므로, 구현 PR에서 Dagster 쪽 설치 판본을 확인해 같은 계열로 맞춘다.
- 이미지 내용이 바뀌므로 `values.yaml`의 `# bump-image-tag` 태그를 올린다(`conventions/docker.md` §3).
  lint CI 초록은 빌드 보증이 아니다 — 머지 전 `dag-import.test.sh`로 로컬 빌드를 돌린다.

### 4-2. 크리덴셜 — 카탈로그는 새 롤, S3는 기존 Connection 재사용

**S3 키는 새 사본을 만들지 않는다.** 태스크가 기존 Connection `seaweedfs_s3`(Secret `airflow-conn-seaweedfs`,
`values.yaml:38-41`)에서 키와 `endpoint_url`을 읽어 pyiceberg FileIO 속성(`s3.endpoint`·`s3.access-key-id`·
`s3.secret-access-key`·`s3.region`·`s3.path-style-access`)에 넘긴다. 사본이 늘지 않으므로
`default/lakehouse-creds`와의 한 벌 회전 규칙(`scripts/k8s-secrets.sh:177-217`)이 그대로 덮는다.

**카탈로그 Postgres는 Airflow 전용 롤을 새로 둔다.**

| 선택지 | 내용 | 판단 |
| --- | --- | --- |
| R1 `iceberg` 롤 재사용 | `default/catalog-pg-app` 값을 `airflow` 네임스페이스에 사본으로 둔다 | 회전 때 Secret·DB 롤·`.env`·워크로드를 한 벌로 바꾸는 참여자가 하나 더 는다. 사본 일치 검사도 새로 필요하다 |
| **R2 전용 롤**(권고) | CNPG `managed.roles`에 롤을 추가하고 Secret 쌍을 한 랜덤 값에서 만든다 | Airflow 메타 롤과 같은 선례다(`cluster.yaml:58-67`). Dagster·Trino·Spark·Flink 회전과 독립이다 |

- R2의 Secret 쌍은 기존 `ensure_secret_pair` 패턴을 따른다(`k8s-secrets.sh:149-160`). 반쪽은 `default`에 두어
  CNPG가 롤에 적용하고, 다른 반쪽은 `airflow`에 Connection으로 둔다
  (env `AIRFLOW_CONN_ICEBERG_CATALOG`, conn_type `postgres`).
  태스크는 그 필드로 `postgresql+psycopg2://…@catalog-postgres-rw.default.svc:5432/iceberg`를 조립한다.
  조립한 URI는 로그에 남기지 않는다.
- **권한 부여 방식이 열린 결정이다**(아래 「사용자 결정 필요」 D1).
  ⚠️ 어느 방식이든 카탈로그 행(`iceberg_tables`)에 대한 DML은 테이블 단위로 쪼개지지 않는다. 그래서 R2의 이득은
  **권한 축소가 아니라 크리덴셜 분리**(독립 회전·접속 주체 식별)다. S3 키도 버킷 전체 권한이라 그 축은 원래 같다.
- `secretKeyRef`는 필수 참조다 — Secret 없이 `values.yaml`의 `secret:`을 늘리면 **모든 Airflow 파드가**
  `CreateContainerConfigError`로 멈춘다(`conventions/docker.md:217-221`).
  순서는 `k8s-secrets.sh` 실행이 먼저, 차트 머지가 나중이다.
- 접속 수: 카탈로그 Postgres의 `max_connections`를 Airflow 메타 DB와 공유한다(`values.yaml:49-55`).
  태스크가 만드는 SQLAlchemy 엔진은 태스크 종료와 함께 닫는다. 늘어나는 접속을 `resource-sizing.md` §Postgres에 더한다.

### 4-3. 체크섬 env

- 차트 `env:`가 이미 모든 Airflow 컨테이너와 K8s 태스크 파드에 `AWS_REQUEST_CHECKSUM_CALCULATION`·
  `AWS_RESPONSE_CHECKSUM_VALIDATION=when_required`를 넣는다(`values.yaml:42-47`). 새 배선은 필요 없다.
- 태스크 코드에서도 Dagster처럼 `os.environ.setdefault`로 한 번 더 박는다(`common/constants.py:13-14`).
  env가 빠지면 증상이 실패가 아니라 **다음 읽기에서의 파싱 오류**라서다(`conventions/k8s/checksum.md` §1).
- pyiceberg의 S3 쓰기가 어느 SDK를 타는지는 설치된 FileIO 구현이 정한다. Dagster 쪽에서 이 env로 손상이
  사라진 실측은 **같은 판본 조합일 때만** 옮겨 온다. 판본이 다르면 `미확인`이므로 §8에서 쓴 객체를 다시 읽어 확인한다.

## 5. dbt 연결과 lineage

- **source**: `models/github_trending/source.yml`, source 이름 `github_trending`, `schema: github_trending`,
  테이블 `trending_repos`. `database:`는 쓰지 않는다(`conventions/dbt.md` §dbt 타깃).
- **lineage는 Dagster에 명시 external 자산을 둔다.** `defs/github_trending/assets.py`에
  `dg.AssetSpec(key="trending_repos", ...)`를 선언하고(설명에 「Airflow DAG `github_trending`이 적재」, kinds에
  `iceberg`·`bronze`) `source.yml`의 `meta.dagster.asset_key: ["trending_repos"]`로 잇는다.
  기존 bronze 자산키가 단일 세그먼트라 같은 모양을 따른다(`models/stock_forecast/source.yml:11-12`).

| 선택지 | 판단 |
| --- | --- |
| **L1 명시 `AssetSpec`**(권고) | lineage 그래프에 bronze가 이름·설명과 함께 선다. `source.yml` 키 오타를 **고아 검사가 잡는다** |
| L2 dagster-dbt 기본값에 맡김 | source 키가 암묵적 external 자산으로 생긴다. 오타도 같은 모양이라 구분되지 않는다 |
| L3 Airflow 쪽 lineage만 | 그래프가 두 도구로 쪼개진다. dbt 모델은 Dagster가 돌린다 |

- L1이 오타를 잡는 이유: 고아 검사는 「자식이 없는 자산」을 센다(`conventions/dagster.md:105-111`).
  `source.yml` 키가 틀리면 명시한 `AssetSpec`이 자식을 잃어 고아 1건이 된다. L2에서는 틀린 키도 자식(dbt 모델)을
  가져 고아가 되지 않는다 — **명시 선언이 있어야 이 게이트가 살아 있다.**
- external 자산이라 Dagster는 Airflow 적재 시점을 모른다. Airflow가 Dagster에 머티리얼라이즈를 보고하는
  경로는 두지 않는다 — 클러스터 안 Airflow에서 호스트 Dagster로 가는 경로가 없다.
- dbt 모델 소유: `defs/github_trending/dbt_assets.py`의
  `@dbt_assets(select="fqn:github_trending", project=dbt_project)`.
  `dbt_project.yml`에서 `+schema: github_trending`과 **group 재정의**를 함께 둔다 — 기본 group `dbt_ingest`면
  `dbt_all_job`이 이 모델을 끌고 간다(`dbt_project.yml:44-52`의 선례). 실행 트리거는 열린 결정이다(D2).

## 6. 차분 모델 — 파티션 간 별 증가량

**`github_trending__star_delta`**(silver, `tags=['silver']`, `materialized='table'`). gold 규칙상 source에서 곧장
gold를 만들지 않는다(`conventions/analysis.md` §2). 언어·토픽별 집계 gold는 이 모델 위에 후속으로 둔다.

- **grain: 1행 = (`dt`, `full_name`)**, 그날 순위 안에 든 저장소만.
- **비교 대상은 직전 달력일(`dt - 1`)** 이다. `lag()`로 「마지막 등장일」과 비교하면 사흘 뒤 재등장한 저장소의
  사흘치 증가가 하루치처럼 보인다. 직전 날에 없으면 증가량은 `NULL`이다(0이 아니다).
- 날짜 가감은 dbt 내장 `dbt.dateadd`로 쓴다(의미론이 같은 경우 — CLAUDE.md §방언). 엔진 리터럴(`interval '1' day`)을
  쓰지 않는다. `dbt.dateadd`는 Spark와 Trino 사이에 초 이하 절삭 차이가 있으나 날짜 단위 가감에는 걸리지 않는다고 본다
  (`sqlfluff_libs/dbt.py` 주석). Spark에서의 값 대조는 `미확인`이고 기준 엔진은 Trino다.

```sql
-- models/github_trending/tables/github_trending__star_delta.sql
-- 1행 = (dt, full_name). 직전 달력일 파티션에 없으면 증가량은 NULL(0이 아니다).
{{ config(materialized='table', tags=['silver']) }}

with snapshots as (
    select
        dt,
        full_name,
        stargazers_count,
        forks_count
    from {{ source('github_trending', 'trending_repos') }}
)

select
    cur.dt,
    cur.full_name,
    cur.stargazers_count,
    prev.stargazers_count as prev_stargazers_count,
    cur.stargazers_count - prev.stargazers_count as star_delta,
    cur.forks_count - prev.forks_count as fork_delta
from snapshots as cur
left join snapshots as prev
    on
        cur.full_name = prev.full_name
        and prev.dt = {{ dbt.dateadd('day', -1, 'cur.dt') }}
```

- 음수 증가량은 정상이다(별 취소). 범위 테스트로 막지 않는다.
- ⚠️ 저장소 이름이 바뀌거나 소유자가 옮겨지면 `full_name`이 달라져 조인이 끊기고 `NULL`이 된다.
  원천 숫자 `id`를 `KEEP_FIELDS`에 더하면 풀리지만 수집 필드 변경이라 거버넌스 재검토 트리거에 걸린다(§7).
  이 설계에서는 더하지 않고 한계로 남긴다.
- 차분은 **누적 스냅샷의 차**이지 「그날 받은 별」의 직접 관측이 아니다(`github-trending.md` §소비 전에).

### 테스트(품질 등급은 `conventions/data-quality.md` §3)

| 대상 | 테스트 | 등급 |
| --- | --- | --- |
| source `trending_repos` | (`dt`, `full_name`) 유일 | 차단 — grain |
| source `trending_repos` | `dt`·`full_name`·`stargazers_count` not null | 차단 |
| source `trending_repos` | (`dt`, `rank`) 유일 | 차단 |
| `github_trending__star_delta` | (`dt`, `full_name`) 유일 | 차단 — grain |
| `incomplete_results` | 판정하지 않는다 | 관측 — 행 수와 함께 로그에 남긴다 |

## 7. 거버넌스 — `datasets.md` 판정을 그대로 잇는다

판정 정본은 [`../security/datasets.md`](../security/datasets.md)의 `github_trending` 행이다
(개인정보 `미확인`, 저장소 단위 원천 값은 저장소·공개물에 담지 않는다).

- **bronze·silver**: 저장소 단위 값(`full_name` 포함)을 담는다. 레이크하우스 안에 머물고 저장소·공개물로 나가지 않는다.
  login을 사람 단위로 묶는 가공(owner별 집계 등)은 하지 않는다.
- **gold**: 언어·토픽 단위 **집계만** 둔다. 집계 수치를 공개물에 담는 것도 `datasets.md`의 재검토 트리거로 판정하고
  자동 허용으로 읽지 않는다.
- **관측 메타·로그**: 적재 태스크는 `dt`·행 수·스냅샷 id만 로그에 남긴다. **미리보기(행 샘플)를 남기지 않는다**
  — 로그는 Issue·PR에 붙여지는 경로라 저장소 단위 값이 새어 나간다. 기존 DAG도 건수와 쿼리만 남긴다.
- **dbt 문서**: `schema.yml` `description`에 예시 값을 쓰지 않는다. 테스트 픽스처를 만들면 합성값으로 두고
  파일 상단에 그 사실을 적는다(`datasets.md`의 다른 데이터셋 관례).
- **노트북**: 셀 출력은 `nbstripout`이 지운다. 「값을 옮겨 적기」에는 기계 감지가 없다
  — 관측점은 `reviewer`와 사람뿐이다.
- 수집 필드에 사람 단위 정보를 더하는 변경(`owner` 객체 등)은 이 설계 범위 밖이고 `datasets.md` 재검토 트리거다.

## 8. 검증 계획 — 구현 PR의 완료 조건

| # | 확인 | 방법 | 음성 대조(이 검사가 깨짐을 잡는지) |
| --- | --- | --- | --- |
| V1 | DAG 모양 | `dag-import.test.sh`에 `load_bronze` 존재·`write_raw` 하류 판정 셀을 더한다 | 의존을 지운 사본에서 셀이 FAIL |
| V2 | 이미지 | 로컬 `podman build` 후 이미지 안에서 pyiceberg·pyarrow 판본이 고정값과 같은지 | — |
| V3 | 적재 함수 | 실인프라 없이 pyiceberg SQLite 카탈로그(로컬 warehouse)로 같은 `dt`를 두 번 적재해 행 수 불변 | 같은 함수를 `append`로 바꾼 변이본에서 행 수가 2배가 돼 FAIL |
| V4 | 다른 파티션 보존 | V3에서 `dt` 두 개를 적재한 뒤 한쪽만 재적재해 다른 쪽 행 수 불변 | 필터를 지운 변이본에서 FAIL |
| V5 | 클러스터 멱등 | `load_bronze`를 clear해 재실행하고 Trino로 그 `dt` 행 수가 raw `items` 길이와 같고 불변인지 본다 | `$snapshots` 메타 테이블에서 재실행분 스냅샷이 **늘었는지** 함께 본다 — 안 늘었으면 「안 돌았다」이지 멱등이 아니다 |
| V6 | 체크섬 무손상 | 적재 뒤 **다른 프로세스**(Trino 조회, boto3로 `metadata.json` 첫 바이트)로 읽는다 | 손상은 쓰기가 아니라 다음 읽기에서 드러난다(`checksum.md` §1) |
| V7 | 카탈로그 공유 | Trino `iceberg` 카탈로그에서 테이블이 보인다 | — |
| V8 | dbt | `dbt parse` → `dbt build --select fqn:github_trending --target dev`, source 테스트 통과. 차분 로직은 `unit_tests:`의 합성 입력(연속일·하루 건너뜀)으로 | 건너뛴 날이 `NULL`이 아니라 값으로 나오는 변이(`lag()`)에서 단위 테스트가 FAIL. 클러스터에 중복 행을 만들어 대조하지 않는다 |
| V9 | lineage | `dg check defs` 후 고아 0건, `trending_repos`의 자식에 차분 모델 | `source.yml` 키를 일부러 틀리면 고아 1건 |
| V10 | Secret | `k8s-secrets.sh` 재실행이 멱등이고 쌍 불일치를 거부한다 | 한쪽만 바꾼 상태에서 실패해야 한다 |
| V11 | 거버넌스 | 적재 로그·`schema.yml`·PR 본문에 저장소 단위 값이 없다 | `reviewer` 체크리스트 E |

- V1~V4·V9는 실인프라가 필요 없다. V5~V8·V10은 클러스터에 붙는 **수동 관문**이고, 쓰는 대상은
  `github_trending` 네임스페이스 하나다. 음성 대조로 클러스터에 깨진 상태(append 중복·손상 객체)를 만들지 않는다.
- 테이블 삭제·카탈로그 행 정리가 필요해지면 비가역 게이트(`docs/risk.md` §4)를 따른다.

## 사용자 결정 필요

- **D1 — 전용 롤의 권한 부여 방식**
  - (a) CNPG 롤 멤버십으로 `iceberg` 권한을 상속한다. 선언형이라 Git이 단일 출처다. 권한은 `iceberg`와 같고
    크리덴셜만 분리된다. CNPG `managed.roles`의 멤버십 필드 지원은 `미확인`(구현 때 CRD로 확인).
  - (b) 카탈로그 테이블 2종에 DML만 `GRANT`한다. DDL이 막혀 조금 좁지만 명령형 단계라 선언 밖에 남는다.
  - (c) R1로 물러나 `iceberg` 롤 크리덴셜 사본을 쓴다.
  - **권고 (a)** — 선언 관리와 회전 독립을 얻고, (b)가 더 좁히는 범위는 위 ⚠️처럼 크지 않다.
- **D2 — dbt 모델 실행 트리거**
  - (a) Dagster 일일 스케줄(KST, Airflow 09:00 수집 뒤 여유를 둔 시각)을 `automation.py`에 둔다. 시각 결합이라
    Airflow가 늦으면 전날 데이터로 빌드된다. 기존 `dbt_all_schedule`처럼 STOPPED로 시작한다(`automation.py:41-52`).
  - (b) Dagster 센서가 카탈로그의 최신 스냅샷을 폴링한다. 결합은 없지만 구현이 는다.
  - (c) 수동 머티리얼라이즈만 둔다.
  - **권고 (c)로 시작해 (a)로** — 지금 하류 소비자가 없고, 사용처가 생길 때 스케줄을 켠다.

## 구현 PR이 함께 갱신할 문서

- `conventions/docker.md` §3 — 「`Dockerfile`에 pip 설치를 두지 않는다」가 바뀐다.
- `conventions/k8s/cnpg.md`·`gitops/charts/catalog-postgres/templates/cluster.yaml` 주석 — 롤 추가.
- `scripts/k8s-secrets.sh` 머리 주석 — Secret 목록.
- `resource-sizing.md` §Postgres — 카탈로그 접속 주체에 Airflow 추가.
- `github-trending.md` §공백·`../dataset_schema.md` 색인 — 「구현됨」으로.
- `architectures/overview.md` — 적재 경로에 Airflow + pyiceberg 경로를 더한다.
