# Spark Connect — client mode 운영 규칙

> [../k8s.md](../k8s.md) §9-4에서 분리했다. 이 문서가 `--master k8s://` 설정의 정본이다.
> 자원 경계는 §9-3, 노출·연결은 §10, 체크섬은 §11이 갖는다.

## 실행 모드는 client다

Connect 서버는 `--master k8s://…`에 **`--deploy-mode`를 주지 않는다**(기본 client).
`cluster`를 주면 spark-submit이 **별도 driver 파드**를 띄우고 Deployment 파드는 즉시 종료한다.
그러면 Service가 gRPC 리스너 없는 대상을 가리키고, 증상은 "접속이 안 된다"가 아니라 **CrashLoop**다.

⇒ **driver는 Deployment 파드 그 자체**다. 따라오는 것 셋:

- driver 자원 다이얼은 `spark.driver.cores/memory`가 아니라 **파드의 `requests/limits`** 다.
- executor는 이 파드가 K8s API로 직접 만든다 → 인증 주체는 **파드의 ServiceAccount**.
- executor가 driver에게 **역방향으로 접속**한다 → driver 도달 주소가 필요하다.

## 필수 설정

| 설정 | 왜 필요한가 | 빠뜨리면 |
| --- | --- | --- |
| `spark.driver.host` ← Downward API `POD_IP` | executor가 driver로 역접속한다 | executor가 driver를 못 찾는다 |
| `spark.driver.bindAddress=0.0.0.0` | 리슨 주소와 광고 주소를 분리 | 컨테이너 밖에서 못 붙는다 |
| `spark.driver.port` · `spark.blockManager.port` 고정 | 기본값이 임의 포트다 | 적을 수도 정책을 걸 수도 없다 |
| `spark.kubernetes.driver.pod.name` ← `POD_NAME` | executor의 **소유자**를 건다 | 회수 다이얼이 driver만 내린다 |
| `spark.kubernetes.executor.limit.cores` | executor 파드의 CPU limit | limit이 **아예 없다**(§2 위반) |
| `spark.executor.memoryOverhead` 명시 | 파드 요청은 유도값이 아니다 | 배분표가 실제와 어긋난다 |

### driver 도달 주소로 headless Service를 쓰지 않는 이유

`architectures/spark.md`는 처방을 *"headless Service + `spark.driver.host`"* 로 적고 있었다.
그 길을 택하지 않았다 — headless는 `publishNotReadyAddresses`가 기본 `false`라
**readinessProbe 통과 전에는 A 레코드가 빈다.** 그런데 executor 요청은 gRPC 바인딩보다 **먼저**
일어나므로 초기 executor가 driver를 못 찾는다. 증상은 에러가 아니라 **대기**이고,
`Initial job has not accepted any resources`는 로그 안쪽에만 남는다.

`POD_IP`는 오브젝트를 하나도 늘리지 않고 그 결합이 없다. IP는 driver 재기동 시 바뀌지만
**executor도 함께 죽으므로** 무해하다.

필요해지면 headless로 갈 수 있으나 **`publishNotReadyAddresses: true`가 필수**다.
StatefulSet 전환은 택하지 않았다 — `kubectl scale deploy/spark-connect`가 문서·노트북 여러 곳에
박혀 있고, 얻는 것은 `replicas=1`에서 이름이 안정되는 것뿐이다.

## 크리덴셜은 두 수단을 나눠 쓴다

| 수단 | 무엇을 하는가 | 여기서 |
| --- | --- | --- |
| `spark.kubernetes.driver.secretKeyRef.*` | **driver 파드 스펙**에 env를 넣는다(cluster 전용) | ❌ 파드를 Spark가 안 만든다 |
| `spark.kubernetes.executor.secretKeyRef.<ENV>` | executor 파드에 **Secret 참조** | ✅ S3 키 |
| `spark.executorEnv.<ENV>` | executor 파드에 **평문** env | ✅ region·체크섬 플래그 |

⚠️ **체크섬 env 2종(`AWS_REQUEST_CHECKSUM_CALCULATION`·`AWS_RESPONSE_CHECKSUM_VALIDATION`)은
executor에도 보낸다.** 로컬 모드에서는 driver=executor가 한 몸이라 driver env 하나가 양쪽을 덮었고,
client mode가 그 우연을 깬다. 빠지면 SeaweedFS가 aws-chunked를 못 풀어 **PUT이 성공한 것처럼
보이면서 객체가 손상**된다(§11).

**PG 크리덴셜은 executor에 넣지 않고 시작한다** — JdbcCatalog 해석·커밋은 driver에서 일어나고
executor는 데이터 파일 I/O만 한다. 실패 서명은 executor 로그의 `org.postgresql` ·
`password authentication failed`이고, 뜨면 그때 `secretKeyRef` 2줄을 더한다.
넣고 시작하면 **필요했는지 영원히 모른다**(최소권한 §5 + 원칙 7).

## 배포 전략은 Recreate다

기본 RollingUpdate는 롤아웃 중 driver 파드를 잠깐 **2개**로 만들고, 둘이 각자 executor를 요청해
§9-3 경계 ②(`instances ≤ 1`)가 **2로 깨진다**. 예산 초과는 에러가 아니라 `Pending`으로 조용히
나타난다. 로컬 모드에서는 executor가 없어 무해했던 축이다.

## 복붙 함정

❌ **`spark.kubernetes.authenticate.driver.serviceAccountName`은 넣지 않는다.**
그 키는 cluster mode에서 driver 파드 스펙을 만들 때 쓰인다. client mode의 인증 주체는
**파드의 `serviceAccountName`** 이다. `SparkApplication` 매니페스트에서 그대로 가져오면
"설정했는데 안 먹는" 축이 하나 더 생긴다.

## 회수 다이얼은 검증 항목이다

🔴 **`--replicas=0` 뒤에 executor가 함께 사라지는지 본다.** 남으면 소유권 설정이 안 걸린 것이고,
에러도 알림도 없이 1 CPU가 샌다. `deleteOnTermination`(기본 `true`)은 **정상 종료 경로만** 덮으므로
대체재가 아니다.

```shell
kubectl get pods -l spark-role=executor          # 전환 전에 찍어 둔다 ← 음성 대조
kubectl apply -f k8s/spark/spark-connect-server.yaml
kubectl rollout status deploy/spark-connect      # wait --for=condition=ready 는 쓰지 않는다(§10)
kubectl get pod <exec> -o jsonpath='{.metadata.ownerReferences}'   # driver uid와 대조
kubectl scale deploy/spark-connect --replicas=0
kubectl get pods -l spark-role=executor          # 비어야 한다
```

⚠️ **셀렉터가 틀려도 0건이 나온다.** 부정 결과는 셀렉터 없는 전체 목록(`kubectl get pods`)을
함께 봐서 관측 경로가 살아 있었음을 확인한다(원칙 7).

## 계산이 executor에서 도는지 확인하는 법

파드가 뜬 것과 태스크가 거기서 돈 것은 다른 축이다.
호스트명을 돌려주는 **Python UDF는 쓸 수 없다** — Connect의 Python UDF는 클라이언트에서
직렬화돼 executor의 Python worker가 실행하므로, 두 Python의 minor 버전이 다르면
`PYTHON_VERSION_MISMATCH`로 죽는다(러너 이미지와 호스트 venv의 버전이 다르다).

대신 **Spark REST API로 센다.** 쿼리 전후의 executor별 `completedTasks` 증가분을 보고,
`driver` 행이 **함께 0으로 남는 것**을 음성 대조로 삼는다.
실행 예시는 [`notebooks/01-spark-on-k8s.ipynb`](../../../notebooks/01-spark-on-k8s.ipynb).
