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
| `@dbt_assets(select=...)` | **Cosmos `DbtTaskGroup`**(모델 = run·test 태스크) |
| 자산 계보 | **Asset 스케줄**(`schedule=A & B`) |
| `common/` | `images/airflow/dags/lakehouse/` — **Airflow를 import하지 않는** 순수 패키지 |

## 2. 규칙

- **DAG는 기본 일시정지**(`is_paused_upon_creation=True`)·`catchup=False`. 켜는 것은 사람이다.
- **파티션 키가 API 날짜로 나가면 timetable은 UTC**다 — [타임존 정책](timezone.md)의 의도된 예외.
- **로직은 `lakehouse`에, DAG는 배선만** 둔다. `lakehouse`는 호스트 venv에서 인프라 없이 단위 테스트한다
  (`images/airflow/tests/lib/`, `PYTHONPATH=images/airflow/dags`).
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
  `max_active_tis_per_dag=1`, **두 번째 DAG가 생기면 Pool로 바꾼다**(Pool은 Git으로 선언할 수 없다).

## 3. 검증

- DAG를 더하면 `images/airflow/tests/dag-import.test.sh`에 **모양 셀**(스케줄·timetable·일시정지·outlet·동시성)을
  함께 더한다. 새 게이트는 일부러 위반시켜 FAIL을 본다.
- 이미지 테스트는 **로컬 podman 빌드**로 돌린다 — 린트 CI 초록은 빌드 보증이 아니다.
- 🔴 import 셀은 **실행 경로**(Connection 조회·Asset event·dbt 실행)를 보지 않는다. 컷오버 때 이미 적재된
  파티션을 다시 돌려 행 수·키 집합이 같은지 대조한 뒤 unpause한다.
