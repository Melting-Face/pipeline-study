# Kubernetes 규칙 (이행)

> **상태**: 🚧 **채택·이행중**. 재설계로 **컴퓨트·데이터 서비스를 K8s로 이전**했고 **Dagster도 클러스터로 들어왔다**
> 전체 로드맵은 [../redesign.md](../redesign.md), PoC 게이트는 그 Phase 0.
> 아래 §1~8은 [docker.md](docker.md)의 원칙(이미지 고정·자원 한도·비밀 참조·non-root)을 K8s 리소스로 옮긴 공통 규칙,
> §9~12는 **본 재설계 고유 규칙**(Spark Operator·노출·로컬 클러스터·CNPG 카탈로그 PG)이다.
> **연관**: 아키텍처 [../architectures/k8s.md](../architectures/k8s.md)·[../architectures/spark.md](../architectures/spark.md),
> 환경변수 전파 [../operations.md](../operations.md), 보안 통제 [../security.md](../security.md).

## 1. 워크로드 유형

- **오퍼레이터/컨트롤러**(Spark Operator·Flink Operator): `Deployment`.
- **컴퓨트 잡**(Spark driver/executor·Flink JM/TM): 오퍼레이터가 CRD(`SparkApplication`·`FlinkDeployment`)로 생성.
- **상태 저장**(seaweedfs·redpanda): `StatefulSet` + `PersistentVolumeClaim`(PVC)로 데이터 유실 방지.
  단 **카탈로그 postgres는 오퍼레이터(CNPG)** 가 관리한다(§12) — 파드·PVC·서비스를 오퍼레이터가 만든다.
  **`emptyDir`를 상태 저장에 쓰지 않는다** — CNPG 이전 전까지 카탈로그 PG가 `emptyDir`였고,
  파드 재기동만으로 Iceberg 테이블 메타가 전부 소멸하는 상태였다(S3 parquet은 남아 "부분 생존"으로 보인다).
- 노출은 `Service`(기본 ClusterIP), 외부 진입은 필요 시 `Ingress`. (**Dagster도 클러스터 안**이다, §8)

## 2. 리소스 requests/limits 필수 (compose `deploy.resources` 매핑)

모든 컨테이너에 `requests`(예약)·`limits`(상한)를 명시한다(compose와 동일 원칙).

```yaml
resources:
  requests: { cpu: "500m", memory: "1Gi" }
  limits:   { cpu: "1",    memory: "2Gi" }
```

- 수치의 단일 출처는 [../resource-sizing.md](../resource-sizing.md). `limits.memory` 합 ≤ 노드 할당가능 메모리.
- **예외는 외부 매니페스트를 그대로 적용하는 경우뿐**이고, 그때는 예외임을 기록한다.
  현재 유일한 예외는 **ingress-nginx**(kind provider `deploy.yaml`) — `requests` 100m/90Mi만 있고
  **`limits`가 없다**(실측). 상주 부하가 작아 수용하되, 자체 오버레이로 값을 얹는 것은 후속 과제로 둔다.

## 3. 헬스체크는 probe로 (compose healthcheck 매핑)

- `readinessProbe`(트래픽 수용 준비), `livenessProbe`(교착 시 재시작), 느린 기동은 `startupProbe`.
- compose `depends_on: condition: service_healthy`는 K8s에서 **readiness gating**·initContainer로 대체한다.

## 4. 설정·비밀정보는 ConfigMap·Secret 참조 (하드코딩 금지)

- 비밀값(`POSTGRES_PASSWORD`·`AWS_*`)은 `Secret`, 일반 설정은 `ConfigMap` → `envFrom`/`valueFrom`으로 주입.
- Secret은 최소 노출: `readOnly` 볼륨·필요한 파드만. etcd 저장 암호화·외부 시크릿 매니저(External Secrets)
  검토([security.md](../security.md) §4-2 at-rest).
- **이미지 태그 고정**(`latest` 금지, [docker.md](docker.md) §1-3) + 구체 태그와 `imagePullPolicy`.

## 5. RBAC 최소권한

- 워크로드별 `ServiceAccount` 분리, 필요한 `Role`/`RoleBinding`만 부여([security.md](../security.md) 2.5).
  클러스터 전역 권한(`ClusterRole`) 남발 금지.
- `NetworkPolicy`로 파드 간 통신 최소화(기본 deny + 허용 리스트).

## 6. 보안 컨텍스트

- `securityContext`: `runAsNonRoot: true`·`runAsUser: 1000`([docker.md](docker.md) Dockerfile 규칙과 일관)·
  `readOnlyRootFilesystem`·`allowPrivilegeEscalation: false`·불필요 capability drop.

## 7. 패키징은 Helm

- 환경별 차이는 `values-<env>.yaml`로 분리(값 오버라이드), 템플릿은 공통. 차트 버전·appVersion을 관리한다.
- compose profiles(옵션 기능)는 Helm values 토글(`monitoring.enabled` 등)로 옮긴다.

## 8. Dagster 배치 — in-cluster

- **원칙**(in-cluster 이전 전): webserver·daemon을 **클러스터 안 Deployment 2개**로 두고 UI는 Ingress로 낸다.
  run launcher는 **`DefaultRunLauncher` 유지**(run = daemon 파드 내 서브프로세스). `K8sRunLauncher`는 후속 과제다.
- 토폴로지·env 매핑·probe·이미지 규칙의 정본은 [dagster.md](dagster.md) §K8s in-cluster 배포.
  호스트 `dg dev`는 **개발 루프 대안**으로 남는다(메타 DB·S3에 port-forward 전제).

## 9. Spark Operator·SparkApplication 규칙

- **오퍼레이터**: Apache 공식 **Spark Kubernetes Operator**([apache/spark-kubernetes-operator](https://github.com/apache/spark-kubernetes-operator),
  GA **1.0.0**)를 Helm으로 `ns=spark-operator`에 설치한다. Kubeflow spark-operator에서 이전했다
  (공식 생태계 무게중심 이동). 오퍼레이터가 `spark-submit`을 대행하므로 자산은 명령형 submit 대신
  **선언형 `SparkApplication`(CRD)** 을 제출한다.
- **차트 버전 ≠ appVersion**(설치 시 최다 실수): GA **appVersion 1.0.0**은 **chart 1.8.0**이다.
  `--version 1.0.0`을 주면 **appVersion 0.2.0**이 깔린다.
  `helm search repo … --versions`로
  대조하고 `scripts/k8s-env.sh`의 `SPARK_OPERATOR_CHART_VERSION`에 **chart 버전**을 핀한다.
- **CRD**: `apiVersion: spark.apache.org/**v1**`, `kind: SparkApplication`.
  chart 1.8.0의 CRD는 **`v1beta1`(served) + `v1`(served·**storage**) 2버전**이고 `storedVersions=["v1"]`이라
  **`v1`이 정본**이다(라이브 실측 — `kubectl get crd sparkapplications.spark.apache.org -o json`).
  `v1beta1`도 served라 apply 자체는 되지만, 저장 시 `v1`로 변환되고 **`v1` 전용 필드
  (`resourceRetainDurationMillis`·`ttlAfterStopMillis`)를 못 쓴다**. 버전은 추측하지 말고 클러스터에서 읽는다.
  Kubeflow(`sparkoperator.k8s.io/v1beta2`)와 **스펙이 다르다** —
  Apache는 **`spec.sparkConf` 중심**이다.
  - **PySpark 진입점**: `spec.pyFiles`(문자열). `mainApplicationFile` 필드는 **없다**
    (근거: 공식 예제 `examples/pi-python.yaml`).
  - 이미지: `spark.kubernetes.container.image`
  - Spark 런타임: `spec.runtimeVersions.sparkVersion`
  - 자원: `spark.driver.{cores,memory}`·`spark.executor.{instances,cores,memory}`(§2 원칙, 수치는 [../resource-sizing.md](../resource-sizing.md))
  - ServiceAccount: `spark.kubernetes.authenticate.driver.serviceAccountName`
  - **Secret→env**: `spark.kubernetes.{driver,executor}.secretKeyRef.<ENV>=<secret>:<key>`(§4 비밀 참조, 평문 금지)
- **버전 고정**: 오퍼레이터 차트/이미지와 Spark 런타임 태그는 **구체 버전으로 고정**한다(`latest` 금지, §4).
  최신 릴리스는 설치 시점에 [releases](https://github.com/apache/spark-kubernetes-operator/releases)에서 확인해 핀한다.
- **러너 이미지**: PySpark + `iceberg-spark-runtime`/`iceberg-aws-bundle` + `postgresql`(JDBC 카탈로그)
  + **`hadoop-aws`/`aws-java-sdk-bundle`(S3A)** 를 포함한 **전용 이미지**를 빌드해 로컬 레지스트리에 push하고,
  `spark.kubernetes.container.image`가 이를 참조한다(§10 이름 규칙 주의).
  태그는 **구체 버전 고정**(`:0.2.0` 등) — `:poc` 같은 가변 채널 태그는 `pullPolicy: Always`와 만나면
  같은 태그가 다른 내용을 가리키는 드리프트를 만든다(§4 `latest` 금지와 같은 이유).
  - **S3 접근 경로가 둘이고 역할이 다르다**(혼동 주의):
    **Iceberg `S3FileIO`**(AWS SDK v2, `iceberg-aws-bundle`)는 **테이블 데이터 I/O** 전담이고,
    **S3A**(`hadoop-aws`, AWS SDK v1)는 `s3a://` 스킴으로 **원본 파일**(csv.gz)을 읽거나 이벤트로그를 쓸 때 쓴다.
    Iceberg만 쓰는 잡은 S3A가 없어도 돌기 때문에 **부재를 알아차리기 어렵다**(한동안 이미지에 없었다).
  - **`hadoop-aws` 버전은 베이스 이미지의 `hadoop-client-*`와 정확히 일치**시킨다
    (Spark 3.5.9 → **3.3.4**). SDK 번들 버전은 추측하지 말고 `hadoop-project` pom의
    `<aws-java-sdk.version>`을 본다(3.3.4 → **1.12.262**).
  - **S3A로 직접 쓰기(`df.write.parquet("s3a://...")`)는 SeaweedFS에서 실패한다** — 기본
    `FileOutputCommitter`가 `_temporary` **rename**에 의존하는데 오브젝트 스토어에는 rename이 없다
    (실측: `Could not rename ... _temporary/...`). 필요해지면 S3A committer(magic)와
    `spark-hadoop-cloud` 의존을 추가해야 한다.
    **다만 본 설계는 영향받지 않는다** — 쓰기는 전부 Iceberg 테이블(=S3FileIO, rename 미사용)로 나가고
    S3A는 **읽기 전용**으로만 쓴다. 검증: `s3a://` csv.gz 4행 read → Iceberg 테이블 write 4행.
  - **진입점 스크립트는 driver CWD 밖에 둔다** — 이미지 WORKDIR(`/opt/spark/work-dir`)에 두면
    `spark-submit`이 `local://` 진입점을 CWD로 복사하며 **대상을 먼저 삭제**해 소스가 사라지고
    `NoSuchFileException`으로 죽는다(실측). 이 레포는 `/opt/spark/app/`을 쓴다.
- **잡 네임스페이스를 반드시 지정**한다 — 차트 기본값 `workloadResources.namespaces.data`는 비어 있고
  `overrideWatchedNamespaces: true`라, 비워두면 **감시 네임스페이스가 없고 workload SA·rolebinding도 생기지 않는다**.
  설치 시 `--set workloadResources.namespaces.data[0]=<ns>`.
- **정리 권한 보완(deletecollection)**: 차트의 `spark-workload-clusterrole`은 verbs가 템플릿에 하드코딩돼
  **`deletecollection`이 빠져 있다**(values로 조정 불가). driver는 종료 시 라벨 셀렉터로 일괄 삭제를 호출하므로,
  없으면 잡이 성공해도 `*-driver-svc`·PVC가 남고 ERROR가 찍힌다. 최소권한(§5)에 맞춰 **잡 네임스페이스 한정 Role**로
  `deletecollection`만 보완한다 → `k8s/spark/spark-workload-cleanup-rbac.yaml`.
- **로그 회수를 위한 retain 정책**: Dagster가 **종료 후** driver 로그를 읽어 materialization 메타
  (행 수 등)를 남기므로 `applicationTolerations.resourceRetainPolicy: **Always**` + `resourceRetainDurationMillis`
  (예: `600000`=10분)를 준다. `OnFailure`면 **성공 즉시 driver 파드가 삭제**돼 로그가 사라진다.
  기본값 `-1`(무기한)은 파드가 계속 쌓이므로 쓰지 않는다.
- **정기 실행**은 원칙적으로 **Dagster(호스트)** 가 주기적으로 `SparkApplication`을 제출한다(단일 오케스트레이션).
- **Dagster 쪽 상태 판정**(`defs/poc/resources.py`): Apache는 **`status.currentState.currentStateSummary`** 를 쓴다
  (Kubeflow의 `status.applicationState.state`가 아니다). **성공·실패 모두 최종 `ResourceReleased`로 수렴**하므로
  최종 상태만으로는 결과를 구분할 수 없다 → **`status.stateTransitionHistory`에 `Succeeded`가 있었는지**로 판정한다.
  오퍼레이터를 갈아끼울 때는 매니페스트·스크립트뿐 아니라 **이 글루 코드까지 함께** 옮긴다
  (이전 시 누락돼 자산이 죽어 있었다).
- **오퍼레이터 watch는 장시간 후 죽는다 — 상태 필드만 믿지 않는다**(실측).
  `SparkApplication`의 `currentStateSummary`가 driver 파드 `Succeeded` 이후에도 **`DriverReady`에
  영구 고착**하는 현상을 확인했다(최장 **4시간 32분**). 오퍼레이터 파드는 재시작 0에 GC 로그도
  정상이라 **살아 있는 것처럼 보인다** — 죽은 것은 `SparkApplication` **watch**다.

  | 오퍼레이터 기동 후 경과 | 완료 감지 |
  | --- | --- |
  | 5분 | ✅ 2.5분 뒤 `within retention` 로그 |
  | 13.7시간 · 27시간 | ❌ 전이 없음 |
  | `rollout restart` 직후 20초 | ✅ 2.5분 뒤 정상 전이 |

  관측 일자와 전체 타임라인은 `$OBSIDIAN_VAULT/status/observations.md`에 있다.

  **기각한 가설 2개**(둘 다 실측으로): ① `delete`→즉시 `create` 경합 — 간격을 60초 둔 대조군도
  동일하게 실패했다. ② retain이 전이를 지연시킨다 — `Application is within retention ...`
  로그는 **이미 완료를 감지한 뒤** GC를 미루는 메시지지 전이 지연이 아니다. retain을 늘려도
  완료 감지는 늦어지지 않는다(두 요구는 애초에 분리돼 있다).

  **영향**: watch가 죽으면 `timeout_s`(900초)가 통째로 흐른 뒤 `succeeded=False`가 되어
  **성공한 잡이 `dg.Failure`로 기록**된다. 잡은 성공했고 Iceberg에 행도 들어갔는데
  오케스트레이터만 모르는 상태다([philosophy.md](../philosophy.md) 원칙 7 사례).

  **대응**: 글루가 `currentStateSummary`와 **driver 파드 `status.phase`를 함께** 본다.
  파드가 종료 상태(`Succeeded`/`Failed`)가 된 뒤 유예(`pod_terminal_grace_s`, 기본 180초) 안에
  오퍼레이터 전이가 없으면 **파드 기준으로 판정**한다. 정상 경로(전이 2.5분)는 그대로 두고
  watch 사멸 시에만 탈출하는 설계다. 회복은 `kubectl -n spark-operator rollout restart deploy`.
  근본 해결은 업스트림(apache/spark-kubernetes-operator) 몫이다.

  **이 방어는 뒤늦게야 실동작이 확인됐다**(그전까지는 **작성됐을 뿐 발동한 적이 없었다** —
  "구현했다"와 "작동한다"는 다른 축이다, [../philosophy.md](../philosophy.md) 원칙 7).

  | 항목 | 실측 |
  | --- | --- |
  | driver `Succeeded` → 자산 탈출까지 | **187초** |
  | 설정값 `pod_terminal_grace_s` | 180 |
  | 폴링 간격 `poll_interval_s` | 5 |
  | 기대 창(폴링 양자화 감안) | 180~185초 |
  | 기록된 판정 상태 문자열 | `DriverReady(watch-stalled,pod=Succeeded)` |

  **판정 문자열이 직접 증거인 이유**: `...(watch-stalled,pod=...)` 형식은 `defs/poc/resources.py`의
  탈출 분기에서**만** 생성된다. 오퍼레이터도 쿠버네티스도 이 문자열을 만들지 않으므로,
  메타데이터에 이 값이 있다는 것은 **그 분기가 실제로 실행됐다**는 뜻이다 —
  *"타임아웃이 안 났다"* 같은 **부정 관측이 아니라 양성 증거**다.
  실측 187초가 기대 창 180~185초를 **2초 초과**한 것은 폴링 경계와 자산 종료 처리 지연으로 설명되며,
  이 정도 오차까지 함께 적어야 다음 사람이 "187이면 180이 아니네"로 오판하지 않는다.
- **검증 상태**: PoC **잡**(`k8s/spark/sparkapplication-poc.yaml`)은 Apache 오퍼레이터에서 **동작 확인됨**
  (Iceberg write+read-back `rows=3`, exitCode 0, 정리 오류 0건).
  PoC **자산**(`defs/poc/`)도 라이브 통과 — `dagster asset materialize` → CRD 제출·폴링 → driver 로그 회수.
  **in-cluster 재검증**: daemon 파드의 ServiceAccount로 제출·완료(RBAC 4 verb만으로 충분).
  → [redesign.md](../redesign.md) **Phase 0 게이트 통과**.

## 9-2. Flink Operator·FlinkDeployment 규칙 (스트리밍)

> ✅ **오퍼레이터는 기본 설치된다**. Flink Operator는 이제 `terraform/lakehouse-platform/`이
> 소유하며 **항상 설치된다** — 구 `INSTALL_FLINK` 다이얼은 이관과 함께 사라졌다.
> 그 다이얼을 `true`로 두었던 이유는 유효하다: **opt-in으로 남기면 "문서엔 있는데 기본 기동엔
> 없는" 드리프트를 재생산**하기 때문이고, 이제는 선언 자체가 그 상태를 만들 수 없다.
> 빼야 한다면 `variables.tf`가 아니라 **스택에서 리소스를 제거**하는 결정이다.
> 한때 제거했던 이유(잡 없는 세션 클러스터가 1 CPU / 2Gi를 상주 점유)는
> **예산 상향과 동시 기동 실측**으로 해소됐다(§9-3). VM 실측값의 정본은
> [resource-sizing.md](../resource-sizing.md) §(A)이고 **여기에 수치를 복제하지 않는다**
> (예산 상향 때 이 줄이 낡은 채 남아 있었다).
> **오퍼레이터 상주와 세션 클러스터 상주는 다른 축이다** — 오퍼레이터는 상시 두되,
> **세션 클러스터(`FlinkDeployment`)는 잡이 없어도 JM이 상주**하므로(아래 Web UI 항목)
> **검증이 끝나면 반드시 내린다.** 회수 규율은 예산이 늘어도 그대로다(§9-3).

- **오퍼레이터**: Apache **Flink Kubernetes Operator**를 Helm으로 설치하고,
  스트리밍 잡은 **`FlinkDeployment`(CRD)** 로 선언한다.
  JobManager/TaskManager 자원(`memory`·`cpu`)을 명시한다(§2, 수치는 [../resource-sizing.md](../resource-sizing.md)).
  CRD는 `flink.apache.org/**v1beta1**` 단일(실측, operator 1.15.0).
- **차트 버전은 설치 시점에 반드시 확인**한다 — `downloads.apache.org/flink/`는 **현행 릴리스만** 보관해
  구버전 차트 URL이 **404**가 된다(핀돼 있던 `1.10.0`이 사라져 설치 불가 → `1.15.0`으로 갱신).
  `curl -s https://downloads.apache.org/flink/ | grep flink-kubernetes-operator`로 대조 후 `k8s-env.sh`에 핀한다.
- **버전 짝은 엔진이 아니라 Iceberg가 정한다**: `iceberg-flink-runtime-<flinkMinor>` 아티팩트는
  **2.1까지만 존재**한다(`-2.2`는 Maven Central 404). 오퍼레이터 CRD가 `v2_2`를 받아줘도
  Iceberg가 없으면 무의미하므로 **Flink는 2.1 계열로 고정**한다(현재 `flink:2.1.3-java17` + Iceberg `1.11.0`).
- **`watchNamespaces={<잡 ns>}`를 반드시 준다** — 비우면 잡 SA(`flink`)와 Role이 **오퍼레이터 ns에만** 생겨
  잡 ns에서 파드가 못 뜬다(Spark 차트의 `workloadResources.namespaces`와 같은 함정).
  단, 지정하면 RBAC이 네임스페이스로 좁아지면서 **두 구멍**이 생긴다 → 아래 보완 매니페스트로 메운다.
  - `k8s/flink/flink-operator-webhook-rbac.yaml` — mutating webhook이 `flinkdeployments`를
    **클러스터 스코프로 list**한다. 없으면 `FlinkSessionJob` 생성 자체가 403으로 거부된다.
  - `k8s/flink/flink-workload-rbac.yaml` — JM 파드 **안에서** 잡을 제출하면 `<name>-rest` **Service를 조회**한다.
    없으면 **DDL·SHOW는 되는데 쿼리 실행만** `services ... is forbidden`으로 실패해 원인을 헷갈리게 한다.
- **`jarURI: local://`은 application 모드 전용**이다. `FlinkSessionJob`은 **오퍼레이터가 jar를 받아** JM에
  업로드하므로 Flink FileSystem 스킴(`https://` 등)이 필요하고, `local://`은
  `UnsupportedFileSystemSchemeException`으로 죽는다. (webhook 허용목록
  `kubernetes.operator.user.artifacts.allowed-schemes`를 통과시켜도 **아티팩트 단계에서 별도로** 막힌다 — 층이 다르다.)
  이미지에 구운 jar를 쓰려면 **application 모드**(`FlinkDeployment.spec.job`)로 선언한다.
- **Hadoop 클래스는 Flink 이미지에 없다**(Spark 이미지와 다른 지점). Iceberg의 `FlinkCatalogFactory`가
  `org.apache.hadoop.conf.Configuration`을 로드하므로 `CREATE CATALOG`에서 `ClassNotFoundException`이 난다.
  레거시 `flink-shaded-hadoop-2-uber` 대신 Spark 러너와 **같은 계열의 shaded 클라이언트**
  (`hadoop-client-api`·`hadoop-client-runtime` 3.3.4)를 `/opt/flink/lib`에 넣는다.
- **크리덴셜은 SQL DDL에 쓰지 않는다** — `sql-client`가 실행문을 **그대로 echo**해 터미널·로그에 평문이 남는다
  (실측). S3 키는 표준 env(`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`)로 넣어 **S3FileIO의 기본
  자격증명 체인**이 집어가게 하고 DDL에서 뺀다. Secret→env 주입은 `podTemplate`으로 한다(§4).

  #### 예외: JDBC 카탈로그 DDL — **조건부 허용** (`security` C5)

  Iceberg **JDBC 카탈로그**의 `CREATE CATALOG`는 S3 키와 달리 **환경변수 체인이 없어** DDL에
  접속 정보를 넣는 것을 피할 수 없다. 아래 **5개 조건을 전부** 만족할 때만 허용한다.
  하나라도 못 지키면 위 금지 조항이 그대로 적용된다.

  1. **커밋 파일에는 `${VAR}` 플레이스홀더만 둔다**(비밀 0). 실값은 파드 안에서 `envsubst`로 렌더한다.
  2. **`kubectl exec` 스트림으로만 실행**한다. stdout이 **컨테이너 로그가 되는 Job/파드 형태로 만들지 않는다** —
     로그로 나가는 순간 회수 불가다.
  3. **렌더 산출물은 `chmod 600` + 실행 직후 삭제**하고, `sql-client`의 **`--history`를 버릴 경로로 지정**한다.
  4. **무유출 3점 확인이 조건이다** — ⓐ 컨테이너 로그 ⓑ 파드 파일시스템 ⓒ **JobManager REST 로그 API**.
     셋 다 확인해야 통과이고, 하나라도 미확인이면 **미확인**이지 통과가 아니다.
  5. **이 예외는 크리덴셜 상태에 의존한다 — 상태가 바뀌면 무효다.**
     카탈로그 PG 비밀번호를 **실값으로 회전하는 순간 이 예외는 성립하지 않고**,
     그때는 위 5조건으로도 부족하다. 회전 시 이 절을 **먼저** 재검토한다.
     현재 회전 여부는 저장소에 적지 않는다 — 크리덴셜 상태는 **인프라 공격 표면**이라
     `$OBSIDIAN_VAULT/security/posture.md`가 소유한다.

  **관측 범위 교훈 — "관측 경로의 생존"과 "관측 범위의 충분성"은 다른 축이다.**
  `security` 점검에서 지정한 범위(`/opt/flink/log`·`/tmp`)를 **벗어난** 셸 히스토리 파일
  (`/opt/flink/.flink-sql-history`)에 평문 **5라인**이 남아 있었다. 같은 점검의 생존 확인은
  **811·6·814건**으로 멀쩡했다 — **경로가 살아 있다는 것이 범위가 충분하다는 뜻이 아니다.**
  ⇒ 유출 점검 범위에는 **숨김 파일(`.*`)·셸 히스토리·홈 디렉터리**를 반드시 포함한다
  ([../philosophy.md](../philosophy.md) 원칙 7).

  ⚠️ 이 예외를 적지 않으면 **"규칙은 금지인데 코드는 위반"** 인 상태가 남아, 규칙이 조용히 죽는다.
- **Web UI**: 오퍼레이터가 `<name>-rest`(8081) Service를 만든다. 호스트에서는
  `kubectl port-forward svc/<name>-rest 8081:8081`(§10). **세션 클러스터는 잡이 없어도 JM이 상주**해
  UI가 계속 살아 있다(Spark의 driver UI가 잡 종료와 함께 사라지는 것과 대비 —
  [../architectures/flink.md](../architectures/flink.md)). TaskManager는 잡 제출 시 온디맨드로 뜬다.
- **검증 상태**: 세션 클러스터(`k8s/flink/flinkdeployment-session.yaml`)에서
  **Spark가 쓴 Iceberg 테이블을 Flink가 읽는 것까지 확인** — `SHOW DATABASES`→`poc`,
  `SELECT * FROM poc.sample`→3행(alice/bob/carol). 카탈로그·S3는 Spark와 **동일한 JDBC 카탈로그 + SeaweedFS**.
- **소스·싱크·상태**: 소스는 **Iceberg bronze 스트리밍 읽기**,
  싱크는 Iceberg(§11 공유 카탈로그), 체크포인트는 SeaweedFS(S3, path-style).
  상태 백엔드는 RocksDB. 러너 이미지는 `iceberg-flink-runtime`+S3A 의존을 포함해
  로컬 레지스트리에 push한다(§10 이름 규칙).
- **역할 경계**: **배치는 Spark, 스트림은 Flink**로 분리한다(엔진 중복 금지). ad-hoc SQL은 Spark SQL로 대체(Trino 제거).

## 9-3. 컴퓨트 동시 기동 규약

**BATCH(Spark)와 STREAM(Flink)은 동시에 띄워도 된다.**
종전 규약은 동시 실행 금지였고, 예산이 오르고 동시 피크가 실측되면서 바뀌었다.

🔴 **규약이 바뀐 이유는 "샜던 게 괜찮아져서"가 아니라 "예산이 늘어서"** 다 —
**회수 규율은 그대로 유지**된다.

⚠️ **백분율의 분모는 노드 Allocatable이지 VM 총량이 아니다.**
둘은 수백 MiB 차이 나는 **다른 축**이라 섞으면 안 된다.
배분표·실측은 [../resource-sizing.md](../resource-sizing.md).

### 경계 3개 — 동시 기동은 무제한이 아니다

1. 🔴 **Flink 상주는 JobManager뿐이어야 한다.** TM은 **잡 제출 시 온디맨드**로 떴다가
   잡 종료와 함께 자동 회수된다. 이 전제가 성립하기 때문에 동시 기동이 허용된다 —
   **TM을 상주시키면 전제가 무너진다.**
2. 🔴 **`spark.executor.instances` ≤ 1**(Flink 세션이 떠 있는 동안).
   하나를 더 붙이면 Allocatable의 97%로 여유가 사라진다.
   executor를 늘려야 하면 **Flink 세션을 먼저 내린다** — 둘 중 하나만 확장한다.
3. **허용은 Redpanda까지 확장되지 않는다.** 도입하면 STREAM 피크가 올라가므로
   배분표와 이 경계를 **함께 재계산**한다. 재계산 전에는 허용 범위가 넓어지지 않는다.

### 경계 ①의 스트리밍 단서

**스트리밍 잡은 TaskManager를 상주시켜 경계 ①의 전제를 깬다.**
그래서 **시연·검증 창 안에서만 돌리고 그 자리에서 회수**한다 —
경계 ①을 개정해 TM 상주를 예산에 반영하는 쪽은 택하지 않았다.

예산상으로는 통과하지만 **예산 통과와 전제 성립은 다른 축**이고,
개정하면 *"TM이 떠 있으면 잡이 도는 중"* 이라는
**`kubectl get pods` 한 번으로 되는 이분법**을 잃는다.

⇒ 경계 ①은 **폐기·개정된 것이 아니라 적용 범위가 명시된 것**이다.
상시 운전이 필요해지면 그때 배분표를 TM 상주 기준으로 다시 쓴다.

### 회수 규율은 예산이 늘어도 유지된다

🔴 **예산 여유는 회수를 면제하지 않는다.** 잡 없는 세션 클러스터의 JobManager는
그냥 놀고 있는 자원이다. **검증용으로 띄운 상주 컴퓨트는 그 자리에서 내린다** —
회수 시점을 트리거하는 주체가 없으면 문서에만 있는 규약은 조용히 샌다.

📌 **발견 경로가 성능 이상이 아니라 "안 쓰는 것 정리"였다**는 점이 이 규율의 근거다.
성능으로는 안 드러난다.

### 경계 ④ — Dagster 상주는 회수 다이얼이 듣지 않는다

Spark Connect는 `--replicas=0`, Flink 세션은 `delete`로 회수되지만 **오케스트레이터는 회수 대상이 아니다**
(내리면 스케줄·센서·런큐가 함께 멈춘다). 그래서 Dagster의 **350m/1792Mi**(`describe node` 실측)는
경계 ①~③의 **모든 시나리오에 상시 더해지는 첫 워크로드**다. BATCH+STREAM 동시 피크에 얹으면 CPU **89%**이고,
`spark.executor.instances`를 2로 올리면 **101%로 스케줄 자체가 실패**한다 — 경계 ②는 이제
"여유가 없다"가 아니라 **"안 된다"** 이다. 확장이 필요하면 늘리는 쪽이 아니라 **Flink 세션을 먼저 내린다.**
Dagster 쪽 다이얼은 자원이 아니라 `max_concurrent_runs`이며 daemon 메모리와 강결합이다.

### ResourceQuota는 도입하지 않는다

네임스페이스 쿼터로 강제하는 대신 **선언값 + 회수 규율**로 간다.
단일 사용자·단일 노드 환경에서 쿼터는 **막는 것보다 디버깅을 어렵게 하는 쪽**이 크고,
초과 시 파드가 `Pending`으로 조용히 멈춰 원인 추적이 길어진다.

### 재측정 시 순서 주의

🔴 **긴 쪽을 먼저 띄운다.** 짧게 살았다 사라지는 워크로드를 먼저 띄우면
긴 쪽이 올라오기 전에 이미 회수돼 **동시 피크 창이 열리지 않는다.**
그 창을 놓친 관측은 **여유가 있는 것처럼 보인다** — 유리한 방향으로 틀린다.

## 10. 클러스터 노출·연결 규칙

- **컴퓨트 트리거**: 현행 구현은 `defs/poc/`의 커스텀 리소스가 kubernetes 클라이언트로 `SparkApplication`을
  제출·폴링한다(in-cluster는 ServiceAccount, 호스트는 kubeconfig — 인증 분기는 코드가 갖는다).
  `dagster-k8s`의 `PipesK8sClient`는 **미채택**이다(Pipes 채널을 쓰지 않는다).
- **로컬 배포판**: **kind on Podman(rootful)**. macOS에선 Podman이 **VM(podman machine)** 안에서 동작하고 kind는 그 VM
  안 컨테이너로 노드를 만든다. kind Podman provider는 experimental이라 **rootful 머신이 필수**이며,
  `export KIND_EXPERIMENTAL_PROVIDER=podman` 후 `kind create cluster` 한다.
  VM 자원은 [../resource-sizing.md](../resource-sizing.md)에서 관리한다.
- **로컬 레지스트리**: kind 공식 local-registry 방식을 쓴다 — containerd `config_path` 설정으로 **`localhost:5001`이
  호스트·클러스터 내부 공통**으로 동작한다. 매니페스트도 같은 이름으로 참조한다.
  (참고: k3d는 내부/외부 이름이 달라 매니페스트에 내부 이름을 써야 하는 함정이 있으나, kind는 공통 이름으로 회피된다.)
- **러너 이미지 빌드·배포**: 레지스트리에 **직접 push**한다(`kind load` 불필요 — 위 배선 덕분).
  빌드 컨텍스트는 각 러너 디렉터리이고, 태그는 **구체 버전 고정**(§9)이다.

  ```shell
  podman build -f k8s/spark/Dockerfile.spark-runner -t localhost:5001/spark-runner:0.4.0 k8s/spark
  podman push --tls-verify=false localhost:5001/spark-runner:0.4.0
  ```

  **태그를 올렸으면 그 태그를 참조하는 매니페스트를 함께 올린다** — 한쪽만 올리면 구 이미지가 계속 돈다.
  참조처 전수 확인은 `grep -rn 'image: localhost:5001' k8s/`이며, 현재 Spark 2곳·Flink 1곳·**Dagster 1곳**
  (`k8s/dagster/dagster-deploy.yaml` — init 컨테이너 포함이라 한 파일 안에 4회)이다.
  Dagster 이미지는 `scripts/k8s-dagster.sh`가 빌드·push하고 **적용 전에 태그 일치를 대조**한다.
- **Iceberg의 `io-impl`(S3FileIO)만으로는 부족한 작업이 있다** — `spark.hadoop.fs.s3*`(S3A)를 **함께** 준다.
  S3FileIO는 **카탈로그가 아는 파일**만 다루므로, warehouse 디렉터리를 직접 나열해야 하는
  `remove_orphan_files`(카탈로그가 *모르는* 파일을 찾는 게 목적)는 **Hadoop FileSystem**을 탄다.
  설정이 없으면 `UnsupportedFileSystemException: No FileSystem for scheme "s3"`로 죽는다(실측).
  warehouse가 `s3://`라 **`fs.s3.impl`도 S3A로 매핑**해야 하고(`fs.s3a.impl`만으론 안 잡힌다),
  jar(`hadoop-aws`·`aws-java-sdk-bundle`)는 러너 이미지에 이미 있어 **설정만** 추가하면 된다.
  S3A는 AWS SDK **v1**이라 SeaweedFS의 aws-chunked 문제(SDK v2 flexible checksum)와는 무관하다.
  참조: `k8s/spark/spark-connect-server.yaml`.
- **서비스 접근**: **HTTP 계열(웹 UI·REST)은 Ingress**(고정 URL), **그 밖의 데이터 접속(JDBC·S3)은
  `port-forward`** 를 기본으로 한다. **gRPC는 TLS Ingress로 낸다**(아래 §gRPC).
  Dagster 리소스(SeaweedFS·카탈로그 DB 엔드포인트)는 이 노출 주소를 `EnvVar`로 주입한다(하드코딩 금지, §4).
  **Flink는 REST와 UI가 같은 포트(8081)** 라 UI를 Ingress로 낸 순간 **REST도 함께 나간다** —
  `port-forward`가 필요 없고(실측: `curl http://flink.localtest.me:8080/overview` → JSON),
  동시에 **인증 없이 잡 제출·취소가 가능한 면**이 열린다는 뜻이다. kind가 `127.0.0.1`로만 바인딩해
  위험은 낮지만 **"UI만 열었다"로 읽지 않는다**(노출 범위는 포트가 아니라 그 포트가 제공하는 API가 정한다).
- **kind는 공개 포트를 클러스터 생성 시점에만 정할 수 있다.** 노드가 컨테이너라 사후에 포트를 추가할 수 없어,
  `kind-cluster.yaml`에 **`extraPortMappings`가 없으면 Ingress·NodePort 둘 다 호스트에서 닿지 않는다**
  (`hostNetwork: true`도 소용없다 — 노드는 podman VM 안이라 VM 네트워크까지만 닿는다).
  빠뜨렸다면 **클러스터 재생성**이 유일한 방법이므로 처음부터 넣어둔다(실측 후 도입).
  - 호스트 포트는 **8080/8443**을 쓴다. macOS에서 1024 미만 바인딩은 root가 필요한데
    podman의 포트 포워딩(gvproxy)은 사용자 권한으로 돈다.
  - 재생성 시 **`k8s-down.sh`를 쓰지 말고 `kind delete cluster`만** 한다. down 스크립트는
    **레지스트리까지 지워** 러너 이미지를 잃는다(재빌드 수 분). 클러스터만 지우면 `k8s-up.sh`가 멱등적으로 다시 붙인다.
- **Ingress 규칙**: 컨트롤러는 **ingress-nginx**(kind provider 매니페스트, 버전은 `k8s-env.sh`에 핀).
  호스트명은 **`<service>.localtest.me`** — 공개 DNS가 127.0.0.1로 응답해 `/etc/hosts` 수정이 필요 없다.
  - Flink는 오퍼레이터 네이티브 **`FlinkDeployment.spec.ingress`**(`template`·`className`)를 쓴다.
  - Spark(Connect UI)는 일반 `Ingress` 리소스로 4040을 노출한다(`spark.localtest.me`, 평문 HTTP).
    **gRPC(15002)는 별도 호스트 `spark-grpc.localtest.me`에 TLS로 낸다** — 아래 §gRPC.

### gRPC를 Ingress로 내보내는 규칙 (실측)

> 종전 규약은 *"gRPC는 Ingress로 내보내지 않는다(YAGNI)"* 였다. **CA 신뢰 축이 실측으로 닫히면서**
> 뒤집었다 — port-forward는 매 세션 별도 터미널을 요구하고, 끊긴 상태가 **에러가 아니라 무한 대기**로
> 보여 오진을 낳는다([../architectures/spark.md](../architectures/spark.md) §`local[2]`).

- **TLS는 선택이 아니라 전제다.** ingress-nginx의 `backend-protocol: "GRPC"`는 HTTP/2 위에서만
  동작하고, nginx는 HTTP/2를 **TLS 리스너에서만** 협상한다. ⇒ **평문 gRPC 경로는 이 방식으로 만들 수 없다**
  (평문으로 내려면 새 호스트 포트가 필요한데, kind는 포트를 **생성 시점에만** 정한다 = 클러스터 재생성).
- **호스트를 나눈다** — `backend-protocol`은 **Ingress 단위** 설정이라 같은 호스트에 HTTP(UI)와 GRPC를
  함께 둘 수 없다. `*.localtest.me`는 전부 127.0.0.1로 응답하므로 호스트를 늘리는 비용은 0이다.
- **인증서는 로컬 CA 체인**(`k8s/local-ca.yaml`)에서 발급한다. 부트스트랩 Issuer → `isCA: true` CA →
  리프 순서다. **selfSigned로 리프를 바로 만들면 `CA:FALSE`라 신뢰 앵커로 못 쓴다.**
- **클라이언트 신뢰 주입 수단은 하나뿐이다** — `sc://` URL에는 CA를 지정하는 옵션이 **없다**.
  gRPC 코어 환경변수 **`GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`** 로만 주입된다.

  ```bash
  # CA 내보내기(공개키라 비밀 아님 — 개인키는 Secret에 남는다)
  kubectl get secret spark-grpc-tls -n default -o jsonpath='{.data.ca\.crt}' | base64 -d > ~/.lakehouse-ca.crt
  export SPARK_REMOTE="sc://spark-grpc.localtest.me:8443/;use_ssl=true"
  export GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=~/.lakehouse-ca.crt
  ```

- **검증 순서**(실측, 이 순서로 통과함): ① `openssl s_client -CAfile …` → `Verify return code: 0`
  ② `curl --cacert … https://spark-grpc.localtest.me:8443/` → **`http_ver=2`·`sslverify=0`·`code=415`**
  ③ pyspark 질의 왕복 ④ `scripts/spark_connect_smoke.py`.
  **②의 `415`는 실패가 아니라 성공 신호다** — gRPC 백엔드가 비-gRPC 요청에 주는 정상 응답이라
  **그 코드가 나왔다는 것 자체가 GRPC 경로가 살아 있다는 양성 증거**다. `200`을 기대하면 오판한다.
  **`openssl s_client -alpn h2`의 `No ALPN negotiated`는 판정 근거로 쓰지 않는다** — 같은 시점
  `curl`이 `http_ver=2`를 냈다. 도구 하나의 부정 결과로 닫지 말고 **교차 확인**한다(원칙 7).
  - 설치 대기는 `wait --for=condition=ready pod`가 아니라 **`rollout status deploy/...`** 로 한다.
    파드 생성 전이면 전자는 `no matching resources found`로 **즉시 실패**한다(실측).
  - 기동 직후 컨트롤러가 **liveness 실패로 1회 재시작**할 수 있다(노드가 다른 롤아웃으로 바쁠 때
    `/healthz` 타임아웃). 자체 회복하므로 곧바로 실패로 판단하지 않는다.
- **`kubectl proxy`는 UI 대안으로 쓰지 않는다**: Flink는 동작하지만 **Spark UI는 302 `Location`이
  프록시 포트가 아니라 API 서버 주소를 가리켜** 브라우저가 따라가지 못한다(실측).

## 11. 오브젝트 스토어·Iceberg 카탈로그 정합

- **SeaweedFS는 path-style 전용**: Spark·Trino 양쪽에서 path-style 접근을 강제한다
  (Spark `spark.hadoop.fs.s3a.path.style.access=true`). 미설정 시 버킷 DNS 서브도메인 가정으로 접근 실패.
- **공유 JDBC 카탈로그**: Spark·Flink·Dagster(pyiceberg)·dbt가 **동일 Postgres 기반 Iceberg JDBC 카탈로그**를
  공유한다(낙관적 동시성). 메타 테이블(`iceberg_tables`·`iceberg_namespace_properties`) 스키마를 동일하게 유지한다.
  장기적으로 REST 카탈로그(Nessie·Polaris·lakekeeper) 이행은 후속 과제([../redesign.md](../redesign.md) 급소②).
- **카탈로그 이름은 전 엔진이 같아야 한다 — 정본은 `iceberg`.**
  JDBC 카탈로그는 `catalog_name` 컬럼으로 네임스페이스·테이블 레지스트리를 **분할**한다.
  이름이 다르면 **같은 DB·같은 버킷을 봐도 서로의 테이블이 보이지 않는다**(빈 카탈로그처럼 동작).
  실측: Spark/Flink가 `jdbccat`, Dagster/Trino가 `iceberg`로 갈려 있어
  Dagster 적재분이 Spark에서 보이지 않을 상태였다 → `iceberg`로 통일하고 기존 행을 마이그레이션했다.
  설정 위치: Spark `spark.sql.catalog.<name>`·`ICEBERG_CATALOG_NAME`(러너 env) / Flink `CREATE CATALOG <name>` /
  Dagster `common/constants.py:CATALOG_NAME` / Trino `iceberg.jdbc-catalog.catalog-name`.
- **SeaweedFS는 AWS SDK의 flexible checksum(aws-chunked)을 풀지 못한다.**
  최신 SDK는 PutObject에 CRC64NVME 체크섬을 기본 적용하며 본문을 청크로 감싸는데, SeaweedFS가 이를
  해제하지 않아 **프레이밍 바이트가 객체 내용에 그대로 저장**된다
  (실측: Iceberg `metadata.json`이 `11\r\n{...}\r\n0\r\nx-amz-checksum-...`로 저장 →
  다음 읽기에서 pyiceberg가 JSON 파싱 실패). **오류가 쓰기가 아니라 이후 읽기에서 나므로 추적이 어렵다.**
  → `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`(+`AWS_RESPONSE_CHECKSUM_VALIDATION`)로 끈다.
  코드에도 `common/constants.py`가 `os.environ.setdefault`로 기본값을 못 박는다(환경 누락 시 조용한 손상 방지).
  Java SDK 경로(Spark·Flink의 iceberg-aws-bundle)는 영향받지 않는다 — 파이썬(pyiceberg/pyarrow·boto3) 경로만 해당.

## 12. 카탈로그·메타 Postgres = CloudNativePG(CNPG)

규칙 본문은 **[k8s/cnpg.md](k8s/cnpg.md)** 로 분리했다(이 문서가 doc_lint 500줄 상한에 닿았다).
요지만: 오퍼레이터는 CNPG(chart 0.29.0 = app 1.30.0), 서비스는 **`-rw`/`-ro`/`-r` 접미사**,
크리덴셜은 **선언 시크릿 + `managed.roles`**(bootstrap은 초기화 1회라 회전이 안 된다),
probe·RBAC·securityContext는 **CR에 쓰지 않는다**(오퍼레이터가 채운다), PVC는 **사후 확장 불가**,
Dagster 메타 DB는 같은 클러스터의 **`Database` CR**로 둔다.

## 참고

외부 공식 문서는 **[`../references.md`](../references.md)에 단일 관리**한다 — URL을 여기 복제하지 않는다.
이 문서와 직접 관련된 항목: Kubernetes(리소스·probe·ConfigMap/Secret·RBAC) · Helm ·
dagster-k8s / Dagster Pipes · Apache Spark/Flink Kubernetes Operator · CloudNativePG · kind.
