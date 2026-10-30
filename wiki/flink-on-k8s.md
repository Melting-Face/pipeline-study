# Flink를 K8s 위에 올리며 — 막힌 층이 다르면 같은 처방이 안 듣는다

> 스터디 장. 오퍼레이터로 Flink 세션 클러스터를 띄우고 Iceberg 테이블을 읽고 쓰며 배운 것.
> 결론부터 쓰면 — **막힌 자리를 「권한」·「버전」 같은 한 단어로 묶는 순간 처방이 빗나간다.**

## 개념

Flink는 상태 기반 스트림 처리 엔진이다.
JobManager가 조율하고 TaskManager가 병렬 처리하며, 배치는 스트림의 특수 경우로 다룬다.
이 저장소는 Flink Kubernetes Operator의 `FlinkDeployment` 한 장으로 **세션 클러스터**를 띄운다.
`spec.job`이 없는 선언이라 잡이 없어도 JobManager가 상주하고, 잡은 나중에 붙인다.

- 공식 문서: [Apache Flink](https://flink.apache.org/documentation/flink-stable/) ·
  [Flink 다운로드·릴리스](https://flink.apache.org/downloads/) ·
  [Iceberg + Flink](https://iceberg.apache.org/docs/latest/flink/)
- 오퍼레이터 문서는 릴리스 버전 문서를 본다 — 규칙은
  [docs/architectures/flink.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/flink.md) 「참고」에 있다.

### 출처

아래는 저장소가 1차 출처로 등재해 둔 것을 옮긴 것이고, **이 노트를 쓰며 다시
열어보지는 않았다** — 확인 시점은 등재 시점이지 작성 시점이 아니다. 표에 없는 것은
「없다」가 아니라 이 노트가 고르지 않았거나 확인하지 못한 것이다.

| 문서 | 링크 |
| --- | --- |
| Flink 문서 | https://flink.apache.org/documentation/flink-stable/ |
| Flink 다운로드·릴리스 | https://flink.apache.org/downloads/ |
| Iceberg Flink 커넥터 | https://iceberg.apache.org/docs/latest/flink/ |
| Maven Central | https://repo1.maven.org/maven2/ |

여기서 「본문」은 [함정 쪽](flink-on-k8s-pitfalls.md)이다.
본문 인용은 전부 **저장소 파일**에서 왔다 — 매니페스트 넷(`flinkdeployment-session` ·
`flink-operator-webhook-rbac`(삭제됨) · `flink-workload-rbac` · `values`; 뒤 셋은 `gitops/charts/flink-operator/` 안),
SQL ConfigMap 셋(`iceberg-batch-job` · `iceberg-stream-job` · `usgs-water-stream-job`),
`Dockerfile.flink-runner`, Dagster 자산 코드.

**확인하지 못한 것도 적는다.** 플러그인 디렉터리의 격리 클래스로더 규약은 저장소
안의 실측 메모에 기대고 있고 1차 문서 링크를 고르지 않았다.
라이브러리 디렉터리에 두었을 때의 실제 충돌 증상도 **돌려본 적이 없다.**
오퍼레이터 문서는 **릴리스 버전 문서**를 봐야 한다 — 나이틀리에는 설치된
오퍼레이터가 모르는 필드가 섞인다.

## 이 저장소에서의 쓰임

- 오퍼레이터: ArgoCD 앱 [`gitops/charts/flink-operator/`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/charts/flink-operator)
- 세션 클러스터: [`k8s/flink/flinkdeployment-session.yaml`](https://github.com/Melting-Face/pipeline-study/blob/main/k8s/flink/flinkdeployment-session.yaml)
- 잡 매니페스트: [`k8s/flink/`](https://github.com/Melting-Face/pipeline-study/blob/main/k8s/flink)의 `*-job.yaml` —
  jar가 아니라 SQL을 담은 ConfigMap이고, JobManager 파드 안에서 `sql-client.sh`로 실행한다.
- 러너 이미지: [`k8s/flink/Dockerfile.flink-runner`](https://github.com/Melting-Face/pipeline-study/blob/main/k8s/flink/Dockerfile.flink-runner)
- 트리거: [`defs/flink/resources.py`](https://github.com/Melting-Face/pipeline-study/blob/main/dagster/dockerfile.d/src/src/dagster_project/defs/flink/resources.py)의
  `FlinkSessionResource`다. 배치 경로에서는 자산이 기동 → `sql-client` 실행 → 회수를 `finally`로 관통한다.
- 소스는 Iceberg bronze 테이블의 스트리밍 읽기다(Kafka 미도입). 카탈로그는 Spark와 같은 `iceberg`다.

경로 출처는 [docs/architectures/flink.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/flink.md) 「어떻게 도나」다.

## 실습

세션 클러스터는 잡이 없어도 자원을 먹는다. **띄운 자리에서 내린다.**

**띄우기** — 러너 이미지를 로컬 레지스트리에 올린다. 최초 1회, 그리고 이미지를 바꿀 때 한다.

출처: [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §4 「러너 이미지」

```shell
podman build -f k8s/flink/Dockerfile.flink-runner -t localhost:5001/flink-runner:0.3.0 k8s/flink
podman push --tls-verify=false localhost:5001/flink-runner:0.3.0
```

레지스트리에 이미 태그가 있으면 다시 올리지 않는다. 이번 관측에서는 이미지가 레지스트리 볼륨에 남아 있어
build·push를 하지 않았다 — 두 명령의 출력은 미관측이다.

```text
# 관측: curl -s http://127.0.0.1:5001/v2/flink-runner/tags/list
{"name":"flink-runner","tags":["0.3.0"]}
```

ConfigMap 5종을 먼저 올리고 세션 클러스터를 띄운다.
ConfigMap이 하나라도 없으면 파드가 `CreateContainerConfigError`로 멈춘다(§3-2).

출처: [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-2 「컴퓨트 기동·회수」

```shell
F=k8s/flink
kubectl apply -n default -f $F/iceberg-catalog-init.yaml -f $F/iceberg-batch-job.yaml \
  -f $F/iceberg-stream-job.yaml -f $F/iceberg-datagen-job.yaml -f $F/usgs-water-stream-job.yaml
kubectl apply  -f $F/flinkdeployment-session.yaml   # Flink 세션 클러스터 → http://flink.localtest.me:8080
```

```text
# 관측: kubectl apply -n default -f $F/iceberg-catalog-init.yaml -f $F/iceberg-batch-job.yaml -f $F/iceberg-stream-job.yaml -f $F/iceberg-datagen-job.yaml -f $F/usgs-water-stream-job.yaml
configmap/iceberg-catalog-init created
configmap/iceberg-batch-job created
configmap/iceberg-stream-job created
configmap/iceberg-datagen-job created
configmap/usgs-water-stream-job created
```

```text
# 관측: kubectl apply -f $F/flinkdeployment-session.yaml
flinkdeployment.flink.apache.org/flink-session created
```

apply 직후 `/overview`는 503(기동 중)이고, 잠시 뒤 200으로 바뀐다.

- ConfigMap은 파일을 나열한다. `iceberg-*.yaml`은 usgs를, `*-job.yaml`은 catalog-init을 놓치고 둘 다
  4개라 개수만 세면 맞아 보인다.
  종수 정본은 `defs/flink/constants.py`의 `REQUIRED_CONFIGMAPS`다(§3-2).

**확인** — 세션 클러스터의 REST 엔드포인트를 부른다.

출처: [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-2 「컴퓨트 기동·회수」

```shell
curl http://flink.localtest.me:8080/overview
```

```text
# 관측: curl http://flink.localtest.me:8080/overview
{"taskmanagers":0,"slots-total":0,"slots-available":0,"jobs-running":0,"jobs-finished":0,"jobs-cancelled":0,"jobs-failed":0,"flink-version":"2.1.3","flink-commit":"6cda56b"}
```

- 성공 기준: JSON이 돌아오고 `taskmanagers`가 0이다(§3-2). TM은 잡 제출 시 온디맨드라 세션만 띄우면 0이 정상이다.
- 세션이 떠 있는 동안 CR을 나열하면 `flink-session`이 보인다. 이 조회가 살아 있다는 대조이므로,
  회수 뒤의 `No resources found`가 의미를 가진다.

```text
# 관측: kubectl get flinkdeployment,sparkapplication -A   (세션 기동 중 — 관측 경로 생존 대조)
NAMESPACE   NAME                                             JOB STATUS    LIFECYCLE STATE
default     flinkdeployment.flink.apache.org/flink-session   RECONCILING   STABLE
```

- 이 포트는 UI와 REST가 같아서, UI를 열면 잡 제출 API도 함께 나간다(§3-1).
- 매니페스트가 push한 태그를 가리키는지는 아래 명령으로 본다(§4).

```shell
grep -rn "image:" k8s/spark/*.yaml k8s/flink/*.yaml
```

```text
# 관측: grep -rn "image:" k8s/spark/*.yaml k8s/flink/*.yaml
k8s/spark/sparkapplication-poc.yaml:32:        spark.kubernetes.container.image: "localhost:5001/spark-runner:0.6.0"
k8s/flink/flinkdeployment-session.yaml:19:    image: localhost:5001/flink-runner:0.3.0
```

- 성공 기준: `k8s/flink/*.yaml`의 `image:` 태그(`flink-runner:0.3.0`)가 레지스트리 태그 목록에 있다.
- 잡 제출과 그 결과 판정은 출처 문서에 명령이 없다 — 미관측이라 비워 둔다.
- Spark와 동시에 띄울 때는 `spark.executor.instances` ≤ 1을 지킨다(§3-2).

**내리기** — 세션 클러스터를 회수한다. ConfigMap은 띄울 때와 같은 `-f` 목록으로 delete한다(§3-2).

출처: [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-2 「컴퓨트 기동·회수」

```shell
F=k8s/flink
kubectl delete -f $F/flinkdeployment-session.yaml   # 회수
kubectl delete -n default -f $F/iceberg-catalog-init.yaml -f $F/iceberg-batch-job.yaml \
  -f $F/iceberg-stream-job.yaml -f $F/iceberg-datagen-job.yaml -f $F/usgs-water-stream-job.yaml
```

```text
# 관측: kubectl delete -f $F/flinkdeployment-session.yaml
flinkdeployment.flink.apache.org "flink-session" deleted from default namespace
```

```text
# 관측: kubectl delete -n default -f $F/iceberg-catalog-init.yaml -f $F/iceberg-batch-job.yaml -f $F/iceberg-stream-job.yaml -f $F/iceberg-datagen-job.yaml -f $F/usgs-water-stream-job.yaml
configmap "iceberg-catalog-init" deleted from default namespace
configmap "iceberg-batch-job" deleted from default namespace
configmap "iceberg-stream-job" deleted from default namespace
configmap "iceberg-datagen-job" deleted from default namespace
configmap "usgs-water-stream-job" deleted from default namespace
```

```text
# 관측: curl -s -o /dev/null -w '%{http_code}' http://flink.localtest.me:8080/overview   (회수 뒤)
404
```

```text
# 관측: kubectl get flinkdeployment,sparkapplication -A   (회수 뒤)
No resources found
```

- 성공 기준: 위 `curl`이 404로 돌아오고 `kubectl get flinkdeployment,sparkapplication -A`가 `No resources found`다.
  Ingress까지 정리된 것이다(§3-2).
- 스트림 잡이 돌고 있으면 삭제 전에 잡을 먼저 취소한다. 순서를 뒤집으면 CR이 `DELETING`에 고착한다 —
  [함정 쪽](flink-on-k8s-pitfalls.md#순서가-규칙이-된-자리-둘).

---

**고장 대응과 함정은 [Flink를 K8s 위에 올리며 — 함정](flink-on-k8s-pitfalls.md)에 있다.**

- 같은 단어(「권한」·「경로」·「버전」)로 묶이는 막힘도 판정하는 층은 둘이다 — 웹훅과 잡 권한, 허용목록과 아티팩트 해석.
- 세션 클러스터는 잡이 없어도 상주하고, 회수는 순서(잡 취소 → 삭제, 로그 회수 → 자원 회수)까지가 규칙이다.
- 부분 성공(DDL은 되고 쿼리만 막힘)은 성공의 부분집합이 아니라 별개 증상이다.

[← 홈으로](Home.md) · [함정 쪽](flink-on-k8s-pitfalls.md) · [스트림 소스를 고르며](streaming-source-choice.md)
