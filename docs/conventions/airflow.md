# Airflow 컨벤션 (Dagster 이관의 본보기)

Dagster 정의를 Airflow DAG로 옮길 때 따르는 규칙이다. 첫 사례는 원화 종가 파이프라인
([설계](../superpowers/specs/2026-10-24-airflow-us-equity-krw-design.md))이고, 이후 이관은 이 모양을 따른다.
이미지 발행·태그 규약은 [`docker.md`](docker.md) §3이 정본이다.

## 1. 대응 — Dagster 개념을 1:1로 옮긴다

| Dagster | Airflow 3 |
|---|---|
| 자산 `@dg.asset` | **Asset** + 그것을 outlet으로 갱신하는 태스크. bronze 테이블 1개 = Asset 1개 = DAG 1개 |
| 일자 파티션(UTC) | `CronDataIntervalTimetable(..., timezone="UTC")`의 `data_interval_start` 날짜 |
| `MaterializeResult(metadata=...)` | `outlet_events[asset].extra` + 태스크 반환값 |
| 리소스 | **Connection**(`AIRFLOW_CONN_<ID>` ← Secret ← `scripts/k8s-secrets.sh`) |
| `pool` 동시성 한도 | **Airflow Pool**(`@task(pool=...)`). 런타임 한도 fail-closed 검사는 이식하지 않는다 — 선언된 공백 |
| `@dbt_assets(select=...)` | **Cosmos `DbtTaskGroup`**(모델 = run·test 태스크) |
| 자산 계보 | **Asset 스케줄**(`schedule=A & B`) |
| `common/` | `images/airflow/dags/lakehouse/` — **Airflow를 import하지 않는** 순수 패키지 |

## 2. 규칙

- **새 데이터셋은 Airflow 전용이다** — Dagster 정의·`test_parity.py` 대조를 만들지 않는다(Dagster는 철거 예정).
  아래 두 벌 공존 규칙은 **이관 중인 기존 데이터셋**에만 적용된다. 사례 `geeknews_items`·`geeknews_dbt`.
- **DAG는 기본 일시정지**(`is_paused_upon_creation=True`)·`catchup=False`. 켜는 것은 사람이다.
- **일자 파티션 DAG의 과거 구간은 backfill로만 채운다** — `dags trigger --logical-date`는 interval을
  그 날이 아니라 실행 시각 기준 최근 구간으로 잡아 **다른 파티션을 에러 없이 교체**했다(#315).
  `partition_key`가 logical date와 interval이 다르면 멈춘다. 절차와 함정:
  - `backfill create --dry-run`으로 run 목록을 본 뒤 생성하고, **출력을 버리지 않는다** — DAG당 활성 backfill은
    1개라 `AlreadyRunningBackfill`(exit 1)로 거부되는데 출력을 버리면 안 보인다.
  - 같은 날짜에 run이 이미 있으면 기본값은 건너뛰고(`already exists`), `--reprocess-behavior`는 **그 run과 그
    interval을 재사용**한다 — 잘못 만든 run이 있는 날짜는 backfill로 고쳐지지 않는다.
  - paused DAG의 backfill run은 `queued`에서 멈춘다. unpause하면 `catchup=False`라도 **최근 구간 정기 run이
    하나 함께 돈다** — 끝나면 다시 pause한다.
- **파티션 키가 API 날짜로 나가면 timetable은 UTC**다 — [타임존 정책](timezone.md)의 의도된 예외.
- **로직은 `lakehouse`에, DAG는 배선만** 둔다. `lakehouse`는 호스트 venv에서 인프라 없이 단위 테스트한다
  (`images/airflow/tests/lib/`, 실행은 `images/airflow/tests/lib.test.sh`).
- 🔴 **Dagster에 남는 데이터셋이 같은 로직을 쓰면 두 벌이 공존**한다. 이관 중 같은 테이블에 쓰는 스키마는
  `test_parity.py`가 양쪽을 대조하고, **컷오버로 Dagster 정의를 지우면 스키마 리터럴 고정으로 바꾼다**
  (필드명·타입·tz — 적재된 테이블의 모양이라서다). 프로브↔파서 필드 계약 같은 대조 테스트도 파서를 따라 옮긴다.
- **컷오버는 Dagster 쪽 삭제로 끝난다** — 자산·잡·스케줄·테이블 바인딩을 지우고, dbt `source.yml`의
  `meta.dagster.asset_key`를 「적재 소유자: Airflow DAG」 주석으로 바꾼다. Airflow가 빌드하는 dbt 모델은
  Dagster `@dbt_assets`에서 `exclude`한다 — 남은 모델은 그것을 외부 상류(비물질화 자산)로 본다.
- **Asset URI는 `lakehouse/assets.py` 한 곳**에 둔다. DAG 모듈을 다른 DAG에서 import하면 그 DAG가 두 번
  등록된다(`AirflowDagDuplicatedIdException`).
- 🔴 **Airflow 3 DagBag은 DAG 폴더를 `sys.path`에 넣지 않는다** — 이미지의 `ENV PYTHONPATH=/opt/airflow/dags`가
  `import lakehouse`를 받친다(이미지 테스트 `img-dag-import-lib`가 지킨다).
- **dbt는 별도 venv**(`/opt/dbt-venv`)이고 manifest는 이미지 빌드 때 굽는다. dbt 프로젝트는 이름 붙은 빌드
  컨텍스트 `dbt`로 넘긴다(컨텍스트를 저장소 루트로 넓히지 않는다).
- 🔴 **Cosmos `fqn:`은 완전 일치**다(dbt는 접두 일치). 디렉터리 단위 선택은 `path:models/<dataset>`를 쓴다.
- **원천 값을 로그·XCom·Asset event에 남기지 않는다** — 날짜·행 수·불리언만(재배포 제한 데이터셋).
- 🔴 **dbt DAG는 `max_active_tasks=1`**. Celery 워커(1Gi)에서 Cosmos 태스크가 겹치면 OOMKilled된다 —
  태스크마다 Airflow·Cosmos 프로세스가 떠 dbt 자체보다 크고, 같은 워커의 **다른 DAG 태스크까지 함께 죽는다**.
- **dbt 테스트가 읽는 모델은 같은 DAG가 소유**한다. 테스트 선택은 **cautious**
  (`TestIndirectSelection.CAUTIOUS`) — eager면 DAG 밖 모델의 테스트까지 끌려와 `TABLE_NOT_FOUND`로 실패한다.
  대가로 **두 모델에 걸친 테스트**(relationships 등)는 모델별 태스크 어디에도 안 잡히므로, 교집합 셀렉터
  (`a,b`)의 `DbtTestLocalOperator`를 두 모델 run 뒤·하류 run 앞에 명시로 둔다(`dbt ls`로 선택 결과를 확인).
- **유료·한도 API는 동시성 1**로 묶는다. 같은 키를 쓰는 DAG가 하나면 `max_active_runs=1` +
  `max_active_tis_per_dag=1`, **두 번째 DAG가 생기면 Pool로 바꾼다**. NVD 키를 CVE·History 두 DAG가 나눠 쓰면서
  첫 Pool `nvd_api`(slots 1)가 생겼다.
- **Pool은 Git으로 선언한다** — umbrella `gitops/charts/airflow/values.yaml`의 `pools:` 목록을 PostSync Job이
  `airflow pools set`(**upsert**)으로 반영한다(공식 차트에 pool 키가 없다). 단 values에서 지워도 클러스터의 Pool은
  **삭제되지 않는다** — 수동 `airflow pools delete`가 필요하다.
- **Pool 점유는 기록이 아니라 계산이다** — 매번 TaskInstance 상태(`queued`·`running`, `include_deferred`면
  `deferred`)로 센다(`Pool.slots_stats`). 실패한 태스크는 즉시 빠지므로 Dagster pool처럼 slot이 새지 않는다
  ([architectures/dagster.md](../architectures/dagster.md) §run 고아). ⚠️ 대신 **`running`에 멈춘 좀비 태스크**는
  스케줄러가 정리할 때까지 slot을 쥔다 — Pool이 막혀 있으면 slot 수가 아니라 점유 태스크의 상태를 먼저 본다.
- **raw 스냅샷 데이터셋은 「raw 착지 → bronze 적재」 2태스크**로 나누고 Pool은 **API 호출 태스크에만** 건다.
  다시 물으면 답이 바뀌는 원천이라 raw를 보존하고, 파서를 고친 뒤 bronze만 API 없이 재실행하기 위해서다.
- **백필은 수동 CLI**다(`catchup=False`): `airflow backfill create --dag-id <id> --from-date <D> --to-date <D>`
  (Airflow 3 — 이미지에서 `--help` 실재 확인). 요청 수 = 일수 × 페이지이므로 착수 전에 계산한다.
  구간 D의 run은 논리 날짜가 **D 19:00 UTC**다(일별 `0 19 * * *` timetable). `--from-date/--to-date`가
  이 논리 날짜에 어떻게 대응하는지는 `미확인`이라 **`--dry-run`으로 만들어질 run 목록을 먼저 확인**한다.

## 3. 무변경 스킵 — 매시 수집의 정본 패턴

매시 수집 DAG는 원천이 그대로면 적재를 건너뛰고 `AirflowSkipException`으로 끝낸다(#318).
구현이 두 갈래로 생겨 정본을 하나로 정한다.

| | `geeknews_items` | `nvd_kev` |
|---|---|---|
| 적재 판정 | bronze 스냅샷 속성 `geeknews.feed_sha256` ↔ 새 본문 digest | 테이블 `fetched_at` ↔ 최신 raw 회차 시각 |
| 마커가 세는 것 | **원천 내용** | **raw 회차**(내용 동일성은 land의 사이드카 대조가 맡는다) |
| raw 착지 스킵 | 별도 단계 없음(판정이 raw 쓰기 앞에 있다) | 있다(`skipped_unchanged`) |
| 적재 모드 | append | 전체 overwrite |

**정본: 스킵 판정은 테이블 쪽 마커로 한다.** 마커는 원천 내용 digest를 적재 스냅샷의 요약 속성
`<ns>.<원천>_sha256` 꼴로 싣는다(사례: geeknews의 `geeknews.feed_sha256`, `lakehouse/geeknews.py`).
헬퍼 추출 때(아래 전환 조건) `<원천>` 칸의 명명을 헬퍼 인자로 고정한다.
digest는 raw 사이드카와 같은 `gzip_sha256`(mtime=0)이다.
raw 착지 스킵은 API 뒤 쓰기를 줄이는 **선택 단계**이고 판정이 아니다. 근거 셋:

- **append·overwrite 양쪽에 쓴다.** 속성은 `append_rows(..., snapshot_properties)`로 싣는다
  (`lakehouse/iceberg.py`). 고정판 pyiceberg 0.11.1의 `Table.overwrite`도 `snapshot_properties`를 받아
  delete·append 스냅샷에 함께 넘긴다(한 트랜잭션 — 설치된 0.11.1 소스 대조, 저장소 내 테스트 없음).
  overwrite 테이블에 쓰려면 헬퍼 `overwrite_table`에 이 인자를 추가해야 한다(지금은 넘기지 않는다).
  반면 `nvd_kev`의 `table_fetched_at`은 행 하나만 읽는다(`scan(limit=1)`).
  모든 행의 `fetched_at`이 같은 전체 교체 테이블에서만 참이고 append에는 못 옮긴다.
- **「raw 쓰기 성공 → 적재 실패」 회차를 다음 회차가 다시 적재한다.** geeknews는 사이드카가 아니라 속성과
  대조해 실패 뒤엔 값이 달라 보인다(`test_ingest_relands_after_failed_append`). nvd_kev도 같은 성질을 갖는다 —
  land가 `skipped_unchanged`를 내도 load는 최신 회차 시각과 테이블을 대조한다(`load_nvd_kev`).
  단 nvd_kev 쪽 테스트는 부품별이다 — `kev_needs_load`(순수 함수)와 `table_fetched_at`(SqlCatalog,
  `test_nvd_parse.py`)은 있으나 「raw 착지 → 적재 실패 → 다음 회차 재적재」를 잇는 통합 테스트는 없다.
- **판정이 데이터와 같은 커밋에 실린다.** 속성은 append와 원자적이다(`append_rows` docstring).
  nvd_kev의 마커는 데이터 열 자체라 이 성질도 같다.

뒤의 두 성질은 두 구현이 **모두** 갖는다. 정본을 가르는 것은 첫째(적재 모드 무관)와 마커의 단위다.
회차 시각 마커는 `force`나 raw 재착지로 **같은 내용의 새 회차**가 생기면 다시 적재하고 Asset event를 낸다.
digest 마커는 내용이 같으면 그대로 스킵한다(그때 event가 나가는지는 M4 미관측).
판정 비용도 다르다 — 속성은 메타데이터만 읽는다.
nvd_kev는 판정 전에 raw 전체를 받아 무결성 대조(`read_latest_kev`)를 하고 데이터 파일을 하나 스캔한다.

**정본의 약점(선언).** 속성은 계보를 거슬러 찾는다(`latest_snapshot_property`). 속성 없는 스냅샷이
현재가 되고(컴팩션) 속성 있는 조상이 만료되면 `None`이 돼 한 번 다시 적재한다(append면 행이 겹친다).
지금 유지보수 대상은 MIMIC·eICU 3테이블뿐이라(`defs/maintenance.py`) 해당이 없다.
**이 테이블을 유지보수에 넣는 때가 재검토 시점**이다.
데이터 열 마커는 이 약점이 없다.

**남기는 쪽: `nvd_kev`는 그대로 둔다.** overwrite 테이블이라 첫째 근거가 걸리지 않고, 재적재·원자성이 같다.
맞추면 코드·테스트를 바꾸고, 첫 회차는 속성이 없어 한 번 더 교체 적재하며 event를 낸다. 맞추는 조건:

- KEV 적재가 append·이력형으로 바뀐다.
- KEV Asset의 소비자가 같은 내용의 재적재 event를 비용으로 느낀다 — `force`·raw 재착지를 쓰기 시작할 때
  (Skip이 event를 내지 않는다면 — M4 미관측. 낸다면 마커를 바꿔도 event는 줄지 않는다).
- 세 번째 매시 DAG가 같은 판정을 쓴다 — Rule of Three로 판정 헬퍼를 뽑으면서 함께 맞춘다.
  헬퍼 추출 때 속성 키(`<ns>.<원천>_sha256`)의 `<원천>` 칸 명명을 헬퍼 인자로 고정한다.

**세 번째 변형을 만들지 않는다.** 새 매시 수집 DAG는 정본을 따른다. 인접 구현을 베낄 때 `nvd_kev`를 본보기로
삼지 않는다. raw 2태스크 구성이면 load가 최신 회차 사이드카 값과 테이블 속성을 대조한다.
미션 ③에서 추가될 발신 DAG는 스킵 의미론에 기대지 않고 **발신 기록 테이블**로 멱등하게 만든다.

### Skip → Asset event (M4 수동 관측)

`outlets`가 있는 태스크가 Skip되면 Asset event가 나가는지 **미확인**이다(미관측). 저장소에는 이미지 고정판
Airflow 3.2.2의 소스·문서 근거가 없다. 결과에 따라 하류가 매시 재빌드되는지가 갈린다.
미션 ③에서 추가될 `alerts_dbt`(KEV Asset 소비자)도 event가 나가면 매시 재빌드된다. 사람이 실행한다.

1. **대상** — event 축은 `nvd_kev`로 본다. `force=true` 회차는 같은 내용으로 새 회차를 써 load가 성공한다
   (양성 대조, event +1 기대). 이어서 기본값 회차를 돌리면 내용이 같아 `load_nvd_kev`가 Skip된다(판정 대상).
   **판정 회차는 2회 이상** 돌린다 — 한 번의 결과는 결론이 아니다.
   하류 run 축은 `geeknews_items` → `geeknews_dbt`(`schedule=[ITEMS_ASSET]`)로 본다. 적재 성공 회차가
   양성 대조다. 피드 바이트가 매번 달라 Skip이 안 걸리면 이 축은 미관측으로 남긴다([geeknews](../dataset_schema/geeknews.md)).
2. **전제·오염 조건** — 하나라도 어기면 그 회차는 판정에 못 쓴다.
   - ⓐ 판정 회차의 load TI 상태가 `skipped`다(`success`면 판정 대상이 아니다).
   - ⓑ 하류 DAG가 paused면 run이 안 생겨 「안 생김」의 원인이 갈리지 않는다. unpaused에서 보고,
     queued event를 조회할 수단이 있으면 그것으로 보충한다.
   - ⓒ coalescing — 양성 대조로 생긴 하류 run이 생성된 것을 확인하고, 조회 수단이 있으면 queued event가
     빈 것까지 확인한 뒤에 판정 회차를 돌린다.
     queued event 조회 수단과 paused DAG에서 큐가 쌓이는지는 3.2.2에서 미확인이다. 수단이 없으면 ⓑ는
     unpaused로만 보고, ⓒ는 양성 대조의 하류 run 생성 확인으로 대신한 뒤 그 사실을 결과 표에 적는다.
   - ⓓ OR 스케줄 하류(미션 ③에서 추가될 `alerts_dbt`)는 하류 run 축에서 뺀다. 다른 Asset event가 섞여 귀속할 수 없다.
   - ⓔ 관측 창 동안 수동 Asset event를 만들지 않는다. REST로 셀 때 `source_task_id` 같은 필터를 걸지 않는다
     (필터가 Skip 쪽 event를 걸러 내면 증분 0이 관측이 아니라 필터의 결과가 된다).
3. **관측 경로** — 각 회차 직후 Asset event 수를 센다(UI Asset 상세의 이벤트 목록, 또는 REST v2 asset events).
   하류는 run 목록을 보고, queued event는 조회 수단이 있을 때만 본다(ⓒ의 미확인).
   REST 경로·CLI 하위 명령은 이미지의 OpenAPI·`--help`로 실재를 먼저 확인한다.
4. **판정** — 양성 대조가 +1이어야 관측 경로가 산 것이다(0이면 판정 회차 결과를 버린다).
   그 위에서 판정 회차마다 증분이 0이면 「Skip은 event를 내지 않는다」, 1이면 「낸다」다. 회차끼리 갈리면 미결이다.
   기대값은 DAG 주석의 **의도**일 뿐 명세가 아니므로 둘 다 열어 둔다.

| 관측 | 결과 |
|---|---|
| 양성 대조(`force=true`) 회차의 event 증분 | 미관측 |
| 판정 회차 1의 event 증분 | 미관측 |
| 판정 회차 2의 event 증분 | 미관측 |
| 판정 회차 1 뒤 하류(`geeknews_dbt`) run 생성 | 미관측 |
| 판정 회차 2 뒤 하류(`geeknews_dbt`) run 생성 | 미관측 |
| queued event 조회 수단(없으면 ⓑ·ⓒ를 대체했다고 적는다) | 미관측 |

## 4. 검증

- DAG를 더하면 `images/airflow/tests/dag-import.test.sh`에 **모양 셀**(스케줄·timetable·일시정지·outlet·동시성·pool)을
  함께 더한다. 새 게이트는 일부러 위반시켜 FAIL을 본다.
- `lakehouse` 단위 테스트는 **PR CI 필수 체크**(`ci.yml` lint 잡)에서 돈다 — 명령은 [`../test.md`](../test.md) §5.
  회귀 방어가 핵심인 가드(예: `partition_key`의 logical date ≠ interval 정지)는 여기서만 잡힌다 —
  import 셀(`dag-import.test.sh`)은 태스크 실행 경로를 보지 않는다.
- 이미지 테스트는 **로컬 podman 빌드**로 돌린다 — 린트 CI 초록은 빌드 보증이 아니다.
- 🔴 import 셀은 **실행 경로**(Connection 조회·Asset event·dbt 실행)를 보지 않는다. 컷오버 때 이미 적재된
  파티션을 다시 돌려 행 수·키 집합이 같은지 대조한 뒤 unpause한다.
