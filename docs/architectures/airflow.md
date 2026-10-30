# Airflow (아키텍처 · 프로젝트 관점)

## 이 프로젝트에서의 역할

클러스터 안 오케스트레이터 — Dagster에서 이관한 DAG를 실행한다.
Dagster는 클러스터에 배포하지 않아 정기 실행 주체가 없다. 클러스터에 상주하는 Airflow가 그 자리를 맡는다.

## 어떻게 도나

- 배포: ArgoCD 앱 `gitops/apps/airflow/config.yaml`(tier 2) → umbrella 차트 `gitops/charts/airflow/`.
  메타 DB가 카탈로그 CNPG의 별도 롤·DB라 tier 1 뒤에 선다.
- 이미지: `images/airflow/Dockerfile`. DAG를 이미지에 굽고, dbt는 별도 venv(`/opt/dbt-venv`)에 둔다.
- DAG(`images/airflow/dags/`):
  - `fx_rates_daily.py` · `equity_ohlcv_daily.py` — 환율·시세를 Iceberg bronze에 적재한다
  - `us_equity_krw_dbt.py` — 위 두 Asset이 갱신되면 Cosmos `DbtTaskGroup`이 dbt 모델을 k8s Trino에서 돌린다
  - `github_trending.py` — raw JSON 수집, `hello.py` — 수동 트리거 전용 확인용
- 로직: `images/airflow/dags/lakehouse/` — Airflow를 import하지 않는 순수 패키지(Dagster의 `common/`에 대응).
  단위 테스트는 `images/airflow/tests/lib/`.
- 접속 정보: Connection(`AIRFLOW_CONN_<ID>`) ← Secret ← `scripts/k8s-secrets.sh`.
  `gitops/charts/airflow/values.yaml`이 Secret을 env로 주입한다(S3·Iceberg 카탈로그·Polygon).
- UI는 `airflow.localtest.me`(ingress-nginx). 주소·로그인은 [환경 세팅 §3-1](../setup.md#3-1-접근-경로).
- 이관 규칙(Dagster 개념 대응·일시정지·동시성·검증)은 [Airflow 컨벤션](../conventions/airflow.md)이 정본이다.

## 개요

Airflow는 **태스크 DAG 중심 워크플로 오케스트레이터**다. Python으로 DAG를 정의하고,
scheduler가 스케줄·의존에 따라 태스크를 executor에 넘긴다. Airflow 3에서는 **Asset**을 outlet으로 갱신하고
그 Asset으로 다른 DAG를 스케줄할 수 있어, Dagster의 자산 계보를 옮겨 담을 수 있다.

## 왜 이걸 골랐나 — 🚧 채택·이행중

설계 문서에 적힌 이유만 옮긴다. 원문은 [Airflow on ArgoCD 설계](../superpowers/specs/2026-10-11-airflow-argocd-design.md)
「결정 요약」과 [원화 종가 Airflow 이관 설계](../superpowers/specs/2026-10-24-airflow-us-equity-krw-design.md)
「Context」다.

- **클러스터 안 정기 실행 주체** — Dagster는 클러스터에 배포하지 않으므로(D6) 정기 실행 주체가 없었다.
  원화 종가 파이프라인을 Airflow로 옮겨 매일 자동 실행하는 것이 Dagster를 대체하는 첫걸음이다(사용자 결정).
- **umbrella 차트** — umbrella 앱은 `gitops/charts/<앱>` 한 모양이다(설계 A1). upstream 예외는 `trino`다
  — `gitops/apps/*/config.yaml`의 `source.type`.
- **Executor `CeleryExecutor,KubernetesExecutor`**, Celery가 기본 — 사용자 결정. worker는 replicas 1이고
  KEDA·HPA는 없다(CPU가 급소).
- **메타 DB는 CNPG `catalog-postgres`의 별도 롤·DB** — PG 이중화를 피하고,
  `iceberg` 롤을 재사용하지 말라는 `cluster.yaml`의 지시를 따른다.
- **랜덤 Secret을 수동 Secret으로 고정** — 차트가 렌더마다 값을 새로 만들면 영구 OutOfSync가 된다.
- **triggerer는 끈다** — deferrable 태스크를 쓰는 DAG가 없다(YAGNI). 재활성 조건은 그런 DAG가 처음 생길 때다.
- **이관 방식은 「Dagster 구조와 유사하게」** — 사용자가 기술 선택을 설계자에게 위임하며 단 조건이다.

Airflow라는 도구 자체를 다른 클러스터 오케스트레이터와 비교한 근거는 설계 문서에 없다.

## 운영 메모

- DAG는 기본 일시정지로 배포되고, 켜는 것은 사람이다([컨벤션 §2](../conventions/airflow.md#2-규칙)).
- 절차는 [Airflow를 클러스터 안에 두며](../../wiki/airflow-on-k8s.md#실습)로 옮겼다.
- K8s 태스크 로그는 파드 회수와 함께 사라진다 — 설계의 「관측 경로 선언」이 선언한 공백이다.

## 교훈

- [Airflow를 클러스터 안에 두며 — 정기 실행 주체는 클러스터에 상주해야 한다](../../wiki/airflow-on-k8s.md)

## 참고

- [Apache Airflow 문서](https://airflow.apache.org/docs/)
- [Airflow Helm chart](https://airflow.apache.org/docs/helm-chart/stable/index.html)
- [Astronomer Cosmos](https://astronomer.github.io/astronomer-cosmos/)
