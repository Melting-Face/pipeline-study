# Kafka 학습 PoC (Strimzi + Iceberg Sink)

> 🔎 **도입이 아니다.** Kafka는 [`docs/architectures/kafka.md`](../../docs/architectures/kafka.md)에서
> **미도입 유지**로 결정돼 있고, 이 디렉터리는 그 문서의 「학습은 별도 PoC 창」 경로다.
> ArgoCD `var.apps`에 등록하지 않는다 — 창 안에서 수동으로 띄우고 **그 자리에서 회수**한다.

## 구성

| 파일 | 내용 |
| --- | --- |
| `strimzi-values.yaml` | Strimzi Cluster Operator Helm 값 — 오퍼레이터는 `strimzi` 네임스페이스, CR은 `default`만 감시 |
| `kafka-kraft.yaml` | `KafkaNodePool`(controller+broker, 1노드, ephemeral) + `Kafka` |
| `topics.yaml` | `poc-events` · `control-iceberg` |
| `Dockerfile.connect-runner` | Strimzi Kafka 이미지 + Iceberg Sink(소스 빌드 배포 zip) + Postgres 드라이버 |
| `kafka-connect.yaml` | `KafkaConnect` + `KafkaConnector`(Iceberg Sink → `iceberg.kafka_poc.events`) |

버전은 **한 벌**이다 — Strimzi `1.3.0` · Kafka `4.3.1` · API `kafka.strimzi.io/v1` · Iceberg `1.11.0`.
Strimzi 1.x는 `v1` CRD만 받는다(`v1beta2` 이하 불가). 하나를 올리면 위 네 파일과 이 줄을 함께 올린다.

## 왜 `default` 네임스페이스인가

Sink가 쓰는 시크릿 `lakehouse-creds`·`catalog-pg-app`이 `default`에 있다. 다른 네임스페이스로 복제하면
사본이 늘어 **비밀번호 회전을 한 벌로 못 한다**. 그래서 CR은 `default`에 두고 오퍼레이터만 분리한다.
회수 대상은 이름·라벨(`strimzi.io/cluster`)로 특정한다.

## 확인 상태 — 근거 등급을 문장마다 붙인다

| 항목 | 상태 |
| --- | --- |
| Strimzi 1.3.0 릴리스, 지원 Kafka 4.2.0~4.3.1, `v1` 전용 | 확인(A · GitHub 릴리스, 요약 경유) |
| 단일 노드 KRaft 예제(`examples/kafka/kafka-single-node.yaml`)·`use-connector-resources` 어노테이션 | 확인(A · 업스트림 예제) |
| 오퍼레이터 기본 resources(`200m`/`384Mi` req · `1000m`/`384Mi` lim)·`watchNamespaces` 키 | 확인(A · 차트 values.yaml) |
| Iceberg Sink: `connector.class`·`iceberg.tables`·`iceberg.control.topic`(기본 `control-iceberg`)·커밋 주기 기본 5분·JDBC 드라이버 미포함·소스 빌드 배포 | 확인(A · 1.11.0 문서) |
| Helm 차트 **OCI 주소** | 🔴 **미확인** — 설치 전 아래 §사전 조건 3으로 확인 |
| `EnvVarConfigProvider`·`template.connectContainer.env`의 secretKeyRef | 🔴 **미확인** |
| `iceberg.tables.auto-create-enabled`·`iceberg.catalog`(카탈로그 이름) 키 | 🔴 **미확인** |
| 배포 zip에 S3FileIO(`iceberg-aws`) 포함 | 🔴 **미확인** — 없으면 이미지 빌드가 그 자리에서 실패한다(Dockerfile 단언) |
| 태그 아카이브만으로 `./gradlew` 빌드 가능(wrapper jar 포함 여부) | 🔴 **미확인** — 이미지를 실제로 빌드해야 닫힌다 |
| 브로커·Connect resources | 선언값(근거 문서 없음) — 창에서 실측하고 값은 볼트에 둔다 |

## 사전 조건

1. 🔴 **예산 확보** — [`docs/conventions/k8s.md`](../../docs/conventions/k8s.md) §9-3 경계 ③.
   Flink 세션을 내리고 Spark Connect를 `--replicas=0`으로 둔 뒤 **executor 파드도 사라졌는지** 본다.
   그다음 노드 Allocatable 대비 Σrequests(Airflow 상주분 포함)에 아래 PoC 합계가 들어가는지 계산한다.
2. 러너 이미지 빌드·푸시(k8s.md §10 — 태그와 매니페스트를 함께 올린다).

   ```shell
   podman build -f k8s/kafka/Dockerfile.connect-runner -t localhost:5001/connect-runner:0.1.0 k8s/kafka
   podman push --tls-verify=false localhost:5001/connect-runner:0.1.0
   ```

3. 차트 주소 확인 — 설치 전에 **차트 메타데이터의 버전이 1.3.0인지** 본다(미확인 항목을 실행으로 닫는다).

   ```shell
   helm show chart oci://quay.io/strimzi-helm/strimzi-kafka-operator --version 1.3.0
   ```

## 기동 (창 안)

> 🔴 이하 클러스터 조작은 실행 전 `reviewer` 보안 체크리스트 1회 + 사용자 승인([`docs/risk.md`](../../docs/risk.md) §4).

```shell
helm install strimzi oci://quay.io/strimzi-helm/strimzi-kafka-operator --version 1.3.0 \
    -n strimzi --create-namespace -f k8s/kafka/strimzi-values.yaml
kubectl apply -f k8s/kafka/kafka-kraft.yaml -f k8s/kafka/topics.yaml
kubectl wait kafka/kafka-poc -n default --for=condition=Ready --timeout=10m
kubectl apply -f k8s/kafka/kafka-connect.yaml
kubectl wait kafkaconnector/iceberg-sink-poc -n default --for=condition=Ready --timeout=10m
```

## 검증 — 판정은 Spark count로만

「Connect가 Running」·「Connector Ready」는 성공 신호가 아니다. 카탈로그 이름·체크섬 설정이 어긋나면
에러 없이 테이블이 비거나 손상된다([`docs/philosophy.md`](../../docs/philosophy.md) 원칙 7).

1. 고정 N건을 produce한다(JSON 한 줄 = 1건).

   ```shell
   kubectl run kafka-producer -n default --rm -i --restart=Never \
       --image=quay.io/strimzi/kafka:1.3.0-kafka-4.3.1 \
       --overrides='{"spec":{"containers":[{"name":"kafka-producer","image":"quay.io/strimzi/kafka:1.3.0-kafka-4.3.1","stdin":true,"command":["bin/kafka-console-producer.sh","--bootstrap-server","kafka-poc-kafka-bootstrap:9092","--topic","poc-events"],"resources":{"requests":{"cpu":"100m","memory":"256Mi"},"limits":{"cpu":"250m","memory":"256Mi"}}}]}}' \
       < events.jsonl
   ```

2. 커밋 주기(`iceberg.control.commit.interval-ms`)가 두 번 지날 때까지 기다린다.
3. **회수 ①** — Connector → Connect → Kafka 순으로 내리고 오퍼레이터를 지운다(Spark를 올릴 자리를 만든다).

   ```shell
   kubectl delete -f k8s/kafka/kafka-connect.yaml
   kubectl delete -f k8s/kafka/topics.yaml -f k8s/kafka/kafka-kraft.yaml
   helm uninstall strimzi -n strimzi
   kubectl get pods -n default -l strimzi.io/cluster   # 기대 0건
   ```

4. Spark Connect를 올려 대조한다. **양성**과 **음성**을 같은 세션에서 본다.

   ```sql
   SELECT count(*) FROM iceberg.kafka_poc.events;      -- 기대: N
   SHOW TABLES IN iceberg.kafka_poc_absent;            -- 기대: namespace 없음 에러(조회 경로가 살아 있다는 대조군)
   ```

   끝나면 Spark Connect를 다시 `--replicas=0`으로 내린다.

## 회수 ② (선택 — 각각 별도 승인)

- 테이블: `DROP TABLE iceberg.kafka_poc.events PURGE` 후 `DROP NAMESPACE iceberg.kafka_poc`.
- Strimzi CRD: `helm uninstall`이 **남긴다**. `kubectl get crd | grep strimzi.io`로 목록을 본 뒤 지운다.
- 네임스페이스: `kubectl delete namespace strimzi`.

## PoC 창 예산 (선언값 — 창 한정·상주 아님)

| 워크로드 | CPU req | Mem req | CPU lim | Mem lim |
| --- | --- | --- | --- | --- |
| Strimzi Cluster Operator | 200m | 384Mi | 1000m | 384Mi |
| Kafka 브로커+컨트롤러(1노드) | 500m | 1Gi | 1000m | 1Gi |
| Topic Operator | 100m | 256Mi | 250m | 256Mi |
| Kafka Connect(1 replica) | 500m | 1Gi | 1000m | 1Gi |
| producer(일회성 파드) | 100m | 256Mi | 250m | 256Mi |

정본 배분표에는 「창 한정」 한 줄만 두고 값은 이 표가 갖는다([`docs/resource-sizing.md`](../../docs/resource-sizing.md)).
