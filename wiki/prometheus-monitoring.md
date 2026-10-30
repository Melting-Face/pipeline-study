# 클러스터 안 Prometheus 하나 — 수집기보다 먼저 「먹일 것이 있는가」를 묻는다

> 스터디 장. 클러스터 안에 Prometheus server를 단독으로 두면서 배운 것.
> 결론부터 쓰면 — **관측은 타깃, 수집기, 규칙 셋이 이어져야 성립하고, 초록 화면은 그 셋이 살아 있다는 증거가 아니다.**

## 개념

Prometheus는 pull 모델의 시계열 수집기다.
서버가 설정된 타깃의 `/metrics` 엔드포인트를 주기적으로 긁어 라벨 붙은 시계열로 저장하고 PromQL로 질의한다.
서비스가 직접 메트릭을 내지 못하면 exporter를 앞에 둔다.
임계 조건은 규칙 파일의 알림 규칙으로 평가하고, 발화된 알림의 묶음과 라우팅은 Alertmanager가 맡는다.

- 공식 문서: [Prometheus — Overview](https://prometheus.io/docs/introduction/overview/) ·
  [Prometheus — Configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)

## 이 저장소에서의 쓰임

- 수집기는 클러스터 안에 하나다. ArgoCD 앱 `prometheus`가 `monitoring` 네임스페이스에
  [`gitops/charts/prometheus/`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/charts/prometheus/values.yaml)를 띄운다.
  upstream 차트의 server만 켜고 Alertmanager·kube-state-metrics·node-exporter·pushgateway는 끈다.
  수집을 맡긴 앱 수렴의 배경은 [argocd-bootstrap.md](argocd-bootstrap.md)다.
- 수집 대상은 values의 `scrapeConfigs`가 정본이다.

| job | 대상 | 비고 |
| --- | --- | --- |
| `prometheus` | 자기 자신 | |
| `kubernetes-nodes-cadvisor` | 노드 kubelet의 cadvisor 경로 | 컨테이너 CPU와 메모리. kind에 metrics-server가 없어 이 경로로 대신한다 |
| `seaweedfs` | `seaweedfs-ext:9324` | 클러스터 밖 컨테이너. kind 네트워크 별칭으로 닿는다. [seaweedfs-s3.md](seaweedfs-s3.md) |
| `spark-operator` | 오퍼레이터 파드의 `/prometheus` | 경로가 `/metrics`가 아니다. `/metrics`는 404다. [spark-on-k8s.md](spark-on-k8s.md) |
| `cnpg` | 카탈로그 Postgres 인스턴스 파드 | [iceberg-catalog.md](iceberg-catalog.md) |

- 긁지 않는 것도 선언돼 있다. Trino는 `/metrics`가 무인증 쿼리 포트와 같아서, Airflow는 statsd를 꺼 둬서,
  Flink 오퍼레이터는 slf4j 로그 리포터라서 긁지 않는다.
- 알림은 관측 등급이다. 규칙은 `TargetDown` 하나이고 Alertmanager가 없어 발화해도 어디에도 전달되지 않는다.
  UI의 `/alerts`를 보는 사람이 있을 때만 의미가 있다.
- 저장은 휘발이다. PVC 없이 `emptyDir`에 짧은 보존이라 파드가 재기동하면 시계열이 사라진다.
- 설정 반영은 configmap-reload 사이드카가 `/-/reload`를 부르는 길뿐이라 `web.enable-lifecycle`을 유지한다.
  그 대가로 UI와 함께 reload·quit API도 Ingress로 나간다.
- 규칙 정본은 [docs/conventions/monitoring.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/monitoring.md),
  현행 실태와 후보 비교는 [docs/architectures/monitoring.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/monitoring.md)다.

## 실습

**띄우기** — 따로 띄우지 않는다. 앱 수렴으로 함께 뜬다.
순서는 [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 5단계 「⑤ 앱 수렴」이다.

**확인 1 — 서버가 준비됐는가**

UI 주소 `http://prometheus.localtest.me:8080`은 [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-1에 있고,
`/-/ready`는 [`values.yaml`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/charts/prometheus/values.yaml) 머리 주석이 선언한 readiness probe 경로다.
아래 curl 형태는 저장소 문서의 명령이 아니라 관측 시 쓴 형태다.

```shell
curl -s http://prometheus.localtest.me:8080/-/ready
```

```text
# 관측: curl -s http://prometheus.localtest.me:8080/-/ready
Prometheus Server is Ready.
```

- 성공 기준: `Prometheus Server is Ready.`가 보인다.

**확인 2 — 무엇을 긁고 있고 살아 있는가**

타깃 목록 API 경로 `/api/v1/targets`는 저장소 문서에 없다. 역시 관측 시 쓴 형태이며 job과 health만 뽑아 센다.

```shell
curl -s http://prometheus.localtest.me:8080/api/v1/targets | jq -r '.data.activeTargets[] | "\(.labels.job) \(.health)"' | sort | uniq -c
```

```text
# 관측: curl -s http://prometheus.localtest.me:8080/api/v1/targets | jq -r '.data.activeTargets[] | "\(.labels.job) \(.health)"' | sort | uniq -c
   1 cnpg up
   1 kubernetes-nodes-cadvisor up
   1 prometheus up
   1 seaweedfs up
   1 spark-operator up
```

- 성공 기준: 위 쓰임 절의 표에 있는 job 다섯이 모두 있고 `health`가 `up`이다.
- 이 출력이 보여 주는 것은 규칙 문서 §3의 ①타깃 목록과 ②`up`까지다.
  ③로그 최신 시각과 ④응답하는 대상이 정본인가는 따로 본다. ④는 이 출력이 증명하지 못한다.
- 타깃이 하나도 없는 경우와 수집기가 죽은 경우를 구분하려고, 부정 결과를 쓸 때는 이 두 확인을 함께 싣는다.

**내리기** — 앱 제거는 설정 삭제 PR과 Application 손 삭제를 더한 절차다. 시계열은 휘발이라 따로 남기지 않는다.

## 고장 대응

| 증상 | 먼저 볼 것 | 처방 |
| --- | --- | --- |
| 메트릭이 0이다 | 타깃 목록, `up`, 로그의 최신 시각 | 「메트릭이 0」과 「수집기가 죽었다」를 구분하고 나서 해석한다 |
| `spark-operator` 타깃이 404다 | 스크레이프 경로 | `/metrics`가 아니라 `/prometheus`다 |
| scrape 설정을 바꿨는데 반영이 안 된다 | configmap-reload 사이드카와 `web.enable-lifecycle` | 플래그를 끄면 머지된 변경이 반영되지 않은 채 정상으로 보인다. 유지한다 |
| 파드를 재기동했더니 시계열이 없다 | 저장 방식 | `emptyDir`라 휘발이다. 장기 추세를 근거로 쓰려면 이 전제를 먼저 바꾼다 |
| 알림이 발화했는데 아무도 모른다 | 수신 경로 | Alertmanager가 없다. UI `/alerts`를 본다 |
| `kubectl top`이 안 된다 | metrics-server 유무 | kind에 없다. cadvisor job으로 질의한다 |

여섯 행 모두 monitoring.md 두 문서에 적힌 증상이다.

## 함정

### 응답하는 그것이 정본인가는 따로 묻는다

출처: [docs/conventions/monitoring.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/monitoring.md) §3.
SeaweedFS 메트릭은 한때 compose 정의에만 있었고 정본이던 K8s 쪽에는 포트도 인자도 없었다.
수집기는 응답을 받았지만 그 응답은 레거시에서 왔다.
같은 서비스가 두 환경에 이중으로 있으면 타깃 목록, `up`, 타임스탬프를 전부 통과한다.
그래서 병기 항목이 셋이 아니라 넷이다. 이 저장소에서 방향을 바꿔 두 번 나타난 실패 양식이다.

### 타깃 없는 수집기는 거짓 신호다

출처: conventions/monitoring.md §2.
타깃이 사실상 자기 자신뿐인 수집기는 데이터를 만들지 않고 「모니터링이 있다」는 인상만 만든다.
`profiles` opt-in으로 기본 기동에서 뺀 경우도 면제가 아니다.
이 이유로 compose Prometheus를 철거하고 클러스터 쪽으로 옮겼다.

### 알림이 있다고 관측이 되는 것은 아니다

출처: architectures/monitoring.md 「알림은 관측 등급이다」와
conventions/monitoring.md §3 「신호가 살아 있어도 판별력은 죽는다」.
읽는 사람이 없는 알림은 관측이 아니라 무시다. 신호가 포화되면 경로는 살아 있는 채로 판별력만 죽는다.

### 수집기가 죽으면 자기 감시도 같이 죽는다

출처: architectures/monitoring.md 「운영 메모」.
`TargetDown`은 수집기 위에서 평가되므로 수집기 자신이 죽으면 함께 죽는다. 자기 감시는 생존 확인을 대신하지 못한다.
