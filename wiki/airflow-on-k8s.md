# Airflow를 클러스터 안에 두며 — 정기 실행 주체는 클러스터에 상주해야 한다

> 스터디 장. Dagster에서 옮긴 DAG를 쿠버네티스 안의 Airflow가 돌리게 하며 배운 것.
> 결론부터 쓰면 — **Airflow는 ArgoCD 앱 하나이고, 로직은 이미지 안의 순수 패키지, DAG는 배선만 한다.**

## 개념

Airflow는 태스크 DAG 중심 워크플로 오케스트레이터다.
Python으로 DAG를 정의하면 scheduler가 스케줄과 의존에 따라 태스크를 executor에 넘긴다.
Airflow 3에서는 Asset을 outlet으로 갱신하고 그 Asset으로 다른 DAG를 스케줄할 수 있다.
Dagster의 자산 계보를 옮겨 담는 자리가 여기다.

- 공식 문서: [Apache Airflow 문서](https://airflow.apache.org/docs/) ·
  [Airflow Helm chart](https://airflow.apache.org/docs/helm-chart/stable/index.html) ·
  [Astronomer Cosmos](https://astronomer.github.io/astronomer-cosmos/)

## 이 저장소에서의 쓰임

Dagster는 클러스터에 배포하지 않으므로 정기 실행 주체가 없다. 클러스터에 상주하는 Airflow가 그 자리를 맡는다.

| 무엇 | 경로 |
| --- | --- |
| ArgoCD 앱 설정 | [`gitops/apps/airflow/config.yaml`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/apps/airflow/config.yaml) — tier `"2"` |
| umbrella 차트와 값 | [`gitops/charts/airflow/`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/charts/airflow) |
| 이미지 | [`images/airflow/Dockerfile`](https://github.com/Melting-Face/pipeline-study/blob/main/images/airflow/Dockerfile) — DAG를 이미지에 굽는다 |
| DAG | [`images/airflow/dags/`](https://github.com/Melting-Face/pipeline-study/blob/main/images/airflow/dags) |
| 순수 로직 | [`images/airflow/dags/lakehouse/`](https://github.com/Melting-Face/pipeline-study/blob/main/images/airflow/dags/lakehouse) — Airflow를 import하지 않는다 |
| 이미지 테스트 | [`images/airflow/tests/`](https://github.com/Melting-Face/pipeline-study/blob/main/images/airflow/tests) |

- DAG 구성: `fx_rates_daily.py`·`equity_ohlcv_daily.py`가 환율·시세를 Iceberg bronze에 적재하고,
  `us_equity_krw_dbt.py`가 두 Asset 갱신을 받아 Cosmos `DbtTaskGroup`으로 dbt 모델을 k8s Trino에서 돌린다.
- Executor는 `CeleryExecutor,KubernetesExecutor`다.
  첫 자리 Celery가 기본이고, 태스크 단위로 `KubernetesExecutor`로 라우팅한다.
- 메타 DB는 카탈로그 CNPG `catalog-postgres`의 별도 롤·DB다. 그래서 tier `"1"` 뒤인 `"2"`에 선다.
- 접속 정보는 Connection `AIRFLOW_CONN_<ID>` ← Secret ← `scripts/k8s-secrets.sh`다.
- triggerer는 끈다. deferrable 태스크를 쓰는 DAG가 없어서이고, 그런 DAG가 처음 생길 때 켠다.
- UI는 `airflow.localtest.me`(ingress-nginx)다. 주소와 로그인은
  [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-1에 있다.

근거는
[docs/architectures/airflow.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/airflow.md) 「어떻게 도나」,
이관 규칙은
[docs/conventions/airflow.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/airflow.md)가 정본이다.
앱이 ArgoCD에 올라가는 방식은 [argocd-bootstrap.md](argocd-bootstrap.md)다.

## 실습

Airflow는 ArgoCD 앱이라 따로 띄우는 명령이 없다. 스택 B apply 뒤 수렴하면 올라온다.
Secret이 먼저 있어야 한다 — `scripts/k8s-secrets.sh`를 앱보다 먼저 돌린다.

**띄우기** — [k8s-bootstrap.md](k8s-bootstrap.md) 「실습」의 절차를 따른다.
`airflow` Application은 마이그레이션 Job, 상주 파드, admin 생성 Job 순서로 수렴한다.

**확인** — 파드 → 헬스 → 버전 순으로 본다.

출처: [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-1 「Airflow 기동 확인·버전」

```shell
kubectl get pods -n airflow
curl -s http://airflow.localtest.me:8080/api/v2/monitor/health
kubectl -n airflow exec deploy/airflow-api-server -- airflow version
```

수렴 전에는 이런 모양이다. 마이그레이션 Job이 끝나야 나머지가 Ready가 된다.

```text
# 관측: kubectl get pods -n airflow   (수렴 전 — 기동 과정)
NAME                                     READY   STATUS     RESTARTS   AGE
airflow-api-server-<hash>      0/1     Init:0/1   0          <경과>
airflow-dag-processor-<hash>   0/2     Init:0/1   0          <경과>
airflow-redis-0                          1/1     Running    0          <경과>
airflow-run-airflow-migrations-<hash>     1/1     Running    0          <경과>
airflow-scheduler-<hash>       0/2     Init:0/1   0          <경과>
airflow-worker-0                         0/2     Init:0/1   0          <경과>
```

- 이 상태의 `Init:0/1`은 고장이 아니다. api-server가 Ready가 되기 전에는 nginx가 503을 낸다.

수렴 뒤에는 이렇게 바뀐다.

```text
# 관측: kubectl get pods -n airflow   (수렴 후)
NAME                                     READY   STATUS    RESTARTS   AGE
airflow-api-server-<hash>      1/1     Running   0          <경과>
airflow-dag-processor-<hash>   2/2     Running   0          <경과>
airflow-redis-0                          1/1     Running   0          <경과>
airflow-scheduler-<hash>       2/2     Running   0          <경과>
airflow-worker-0                         2/2     Running   0          <경과>
```

- 성공 기준: api-server·dag-processor·scheduler·worker·redis가 모두 `Ready`다. 마이그레이션 파드는 사라진다.

```text
# 관측: curl -s http://airflow.localtest.me:8080/api/v2/monitor/health
{"metadatabase":{"status":"healthy"},"scheduler":{"status":"healthy","latest_scheduler_heartbeat":"<시각>"},"triggerer":{"status":null,"latest_triggerer_heartbeat":null},"dag_processor":{"status":"healthy","latest_dag_processor_heartbeat":"<시각>"}}
```

- 성공 기준: `metadatabase`·`scheduler`·`dag_processor`가 `healthy`다.
  `triggerer`가 `null`인 것은 정상이다. 설계상 끈 것이다.

```text
# 관측: kubectl -n airflow exec deploy/airflow-api-server -- airflow version
Defaulted container "api-server" out of: api-server, wait-for-airflow-migrations (init)
3.2.2
```

- 성공 기준: 버전 한 줄이 나온다. 버전의 정본은 `gitops/charts/airflow/`의 `Chart.yaml`과 `values.yaml`의 이미지 태그다.

로그인 정보를 꺼내는 명령은 [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-1 「로그인·비밀값」에 있다.
값이 화면에 남으므로 이 장은 출력을 싣지 않는다.

이미지 테스트는 로컬 podman 빌드로 돌린다 — `images/airflow/tests/dag-import.test.sh`.

**내리기** — 앱을 따로 내리는 명령은 없다. 앱 제거는 설정 파일 삭제와 Application 손 삭제이고
[argocd-bootstrap.md](argocd-bootstrap.md)의 「삭제가 번지지 않게 막은 겹」이 이유다.
전체 철거는 [terraform-stacks.md](terraform-stacks.md) 「실습」의 내리기를 따른다.
이번 관측에서는 내리지 않았으므로 출력은 미관측이다.

## 고장 대응

| 증상 | 먼저 볼 것 | 처방 |
| --- | --- | --- |
| Airflow UI가 503 | `kubectl get pods -n airflow`에서 api-server가 Ready인가 | 기동 중이다. Ready를 기다린다 |
| `airflow` 파드가 `ImagePullBackOff` | values 태그의 이미지가 레지스트리에 있는가 | 앱 결함이 아니다. `main` 머지 발행 워크플로를 본다 — [docker.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/docker.md) §3 |
| `airflow` 파드가 `CreateContainerConfigError` | Secret이 있는가 | `scripts/k8s-secrets.sh`를 돌린다. 멱등이다 |
| Secret 값을 바꿨는데 파드가 그대로다 | 워크로드를 재기동했는가 | 차트 checksum은 Secret 이름만 해시한다. 회전은 Airflow 워크로드 재기동까지 한 벌이다 — [cnpg.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/k8s/cnpg.md) |
| K8s executor 태스크의 로그가 없다 | executor 종류 | 파드와 함께 사라진다. 선언된 공백이다. Celery 태스크는 worker PVC에서 UI가 서빙한다 |

앞 세 행은 [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §8,
뒤 두 행은 같은 문서 §3-1 「Airflow 선행 순서·회전」에서 옮겼다.

## 함정

이관 규칙 문서에 적힌 교훈 셋을 옮긴다. 출처는 모두
[docs/conventions/airflow.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/airflow.md) §2 「규칙」이다.

### dbt DAG는 `max_active_tasks=1`

Celery 워커(1Gi)에서 Cosmos 태스크가 겹치면 OOMKilled된다.
태스크마다 Airflow·Cosmos 프로세스가 떠 dbt 자체보다 크고, 같은 워커의 **다른 DAG 태스크까지 함께 죽는다**.

### Airflow 3 DagBag은 DAG 폴더를 `sys.path`에 넣지 않는다

이미지의 `ENV PYTHONPATH=/opt/airflow/dags`가 `import lakehouse`를 받친다.
이미지 테스트 `img-dag-import-lib`가 이를 지킨다.

### Cosmos `fqn:`은 완전 일치다

dbt는 접두 일치인데 Cosmos는 완전 일치다. 디렉터리 단위 선택은 `path:models/<dataset>`를 쓴다.
