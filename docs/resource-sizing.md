# 리소스 산정 (resource sizing)

호스트(Docker)에 할당된 CPU·메모리에 맞춰 각 서비스의 옵션을 조정한다.
**서비스 메모리 한도의 합 ≤ 호스트 RAM − OS/버퍼 여유(약 1~2 GB)** 를 유지한다.

> 조정 지점은 이 문서에서 한곳으로 관리하고, `compose.yml`의 `deploy.resources`와
> 각 서비스 설정 파일을 함께 맞춘다. (단순함·명시적 — [philosophy.md](philosophy.md))

> **문서 구성**: 아래 "Kubernetes 재설계 시나리오"는 **목표(이행) 배분**([redesign.md](redesign.md)),
> 그 이후 섹션(Trino·Dagster·Postgres…)은 **현행 compose 배분**이다. Trino는 재설계에서 제거되므로
> Trino 섹션은 이행 완료 시 레거시 참조가 된다.

## Kubernetes 재설계 시나리오 (kind + Podman · 8 CPU / 22.3 GiB) 🚧

> 대상: [redesign.md](redesign.md)의 목표 토폴로지. **Dagster도 이 예산 안**, 컴퓨트·데이터
> 서비스만 로컬 K8s(kind on Podman)에 둔다. 컴퓨트는 **Spark(배치) / Flink(스트리밍)** 2엔진이며,
> 실측으로 **시분할 → 동시 기동**으로 규약이 바뀌었다([conventions/k8s.md](conventions/k8s.md) §9-3).

### (A) 예산의 단위 축은 **셋**이다

메모리를 인용할 때 **어느 축의 값인지 반드시 병기**한다. 셋은 서로 다른 것을 세며, 숫자만 옮기고
단위를 바꾸면 조용히 틀린다([philosophy.md](philosophy.md) §계측 단위).

> 📌 **아래 값은 실측**이다(관측 시각과 직전 판본 값은 `$OBSIDIAN_VAULT/status/observations.md`).
> 자원을 바꾸면 **이 표와 [`scripts/k8s-env.sh`](../scripts/k8s-env.sh)를 한 벌로** 갱신한다.

| 축 | 값 | 무엇을 세는가 | 어디서 읽나 |
| --- | --- | --- | --- |
| **VM 총량** | **8 CPU / 26702 MiB** (= 26.08 GiB = 28.0 GB 십진) | podman machine에 할당된 전체 | `podman machine inspect` |
| **`MACHINE_MEMORY_MIB`** | `26702` | 위와 **같은 축**(VM 총량)의 선언값 | [`scripts/k8s-env.sh`](../scripts/k8s-env.sh) |
| **노드 Allocatable** | **8000m / `26679964Ki`** (= 26054.65Mi ≈ 25.44 GiB) | 파드가 실제로 예약 가능한 양 | `kubectl describe node` |

- 🔴 **VM 총량(26702 MiB)이 노드 Allocatable(26054Mi)보다 648 MiB 많다.** 차이는 노드 OS·kubelet
  예약분이며, **둘은 다른 축이라 섞어 쓰면 안 된다.** 이 문서의 **백분율(%)은 전부 Allocatable 기준**이다.
- ⚠️ **정수로 줄일 때는 내림한다 — 예산 분모를 실제보다 크게 잡지 않기 위해서다.**
  `26679964 ÷ 1024 = 26054.65…` → **26054Mi**. 선례(`22843508 ÷ 1024 = 22308.1`)는 소수부가 `.1`이라
  **반올림과 내림이 같은 값**을 냈고, 그래서 어느 방식인지 말해주지 않았다. 이번 값이 처음으로 갈린다.
  표에는 **소수부를 살린 값**을 두고, 정수가 필요한 자리에서만 내림한다.
- ⚠️ **메모리만 늘었고 CPU는 그대로다** — `8000m`은 변하지 않았으므로 **CPU 백분율은 전부 그대로**이고,
  메모리 백분율만 분모가 커져 내려간다. **"자원을 늘렸다"를 "여유가 생겼다"로 읽지 마라**:
  §(C) 다이얼이 지목하는 급소(동시 피크 **84%**)는 **CPU 축이라 하나도 완화되지 않았다.**
- **단위를 바꾸는 순간이 함정이다** — 과거 판본에서 `22843508Ki`를 "22843Mi"로 옮긴 기록이 있었고,
  이는 **536Mi(2.4%) 과대**였다(`Ki→Mi`는 ÷1024이지 접두어 치환이 아니다). 표에는 **raw 값을 남기고
  환산식을 함께** 적는다: 현행은 `26679964 ÷ 1024 = 26054.65…` → **26054Mi**(내림).
  **값과 함께 "무엇을 세는가"를 적지 않으면 재검산이 불가능**하다.
- ⚠️ **`podman machine list`의 표시값을 그대로 옮기지 마라** — `26.08GiB`로 반올림해 보여주므로
  거기서 MiB를 역산하면 어긋난다. 선언값의 정본은 **`podman machine inspect`의
  `.Resources.Memory`(=`26702`, 단위 MiB)** 다.

```bash
# kind는 rootful 요구. 자원은 생성 시 지정하되, 사후 변경도 가능하다(코드블록 아래 참조).
podman machine init dagster-k8s --rootful --cpus 8 --memory 26702 --disk-size 93
podman machine start dagster-k8s
export KIND_EXPERIMENTAL_PROVIDER=podman
kind create cluster --name lakehouse --config kind-cluster.yaml

# 실측 확인 (환산식을 함께 남긴다)
podman machine inspect <machine> --format '{{.Resources.Memory}}'   # 26702 (MiB, VM 총량 축)
kubectl get node -o jsonpath='{.items[0].status.allocatable}'       # cpu:8, memory:26679964Ki
```

- ⚠️ **"사후 변경은 재생성 필요"는 거짓이다(반증됨).** 구 판본이 *"Apple Silicon은 생성 시
  확정"* 이라 적고 있었으나, `podman machine set`으로 **중지 상태에서 변경**할 수 있고 실제로
  `22888 → 26702 MiB`로 바뀐 뒤에도 kind 클러스터 `lakehouse`와 PVC 2종(`catalog-postgres-1`·
  `data-seaweedfs-0`)이 **그대로 Bound 상태로 살아 있었다.**
  **재생성이 강제되는 것은 kind 축뿐**이다 — `extraPortMappings`·`extraMounts`(생성 시점 전용)와
  PVC 용량(`ALLOWVOLUMEEXPANSION=false`). **두 축을 섞으면 치르지 않아도 될 재적재를 치른다.**
  절차는 [operations.md](operations.md) §클러스터 재생성.
- **호스트 headroom**: VM 26.08 GiB는 **호스트에서 통째로 빠져나간다.** 호스트 총량이 32 GB이므로
  남는 것은 **약 5.9 GiB**이고, 여기서 macOS와 Dagster(webserver+daemon+메타 Postgres)·Jupyter가
  함께 돌아야 한다 → **아래 (D) 호스트 축**. **메모리를 VM에 더 준 대가는 호스트 축에서 나간다** —
  이 배분은 *"Dagster를 클러스터로 옮긴다"* 는 전제와 한 벌일 때만 여유롭다.
- **disk 93 GiB**(≈100 GB 십진): SeaweedFS(원천 csv.gz + Iceberg parquet) + 이미지 레이어 대비.
  실측 노드 디스크 사용량 **35.2G / 92.4G(38%)**, 그중 SeaweedFS 볼륨이 **10G**다.
  ⚠️ **세 값이 각각 다른 것을 센다** — `df`는 노드 디스크 전체
  (containerd 이미지 레이어 포함), `du /data`는 SeaweedFS **볼륨 파일의 예약 공간**,
  버킷 합계는 **실제 오브젝트**다. ⚠️ `du`를 데이터량으로 읽지 마라 — SeaweedFS 볼륨은
  **preallocate된 sparse 파일**이라 `du`가 1.0G라 부르는 파일이 `ls -la`로는 62KB다.
  실측 `du` **10G** vs 버킷 합계 **106.8MB**(약 100배 차).
  용량 계획은 `du`, **백업·재적재 비용은 버킷 합계**로 본다 — 이 둘을 섞어
  `architectures/terraform.md`가 재적재 비용을 100배로 적었던 전례가 있다.

### (B) 컴포넌트 배분 (requests / limits) — 동시 기동

원칙: **Σrequests ≤ 노드 Allocatable(8000m / 26054Mi)**. 실측으로 **BATCH+STREAM 동시
기동이 예산 안에 들어옴**이 확인돼 시분할 금지가 **동시 기동 허용**으로 바뀌었다
([conventions/k8s.md](conventions/k8s.md) §9-3).
아래 표의 `req`는 전부 **실제 선언값**이며, 합계 행은 **관측 차분으로 검산**된 값이다.

⚠️ **백분율은 분모가 바뀌면 함께 바뀐다 — `req` 절대값은 그대로다.** VM 메모리 상향
(22888 → 26702 MiB)으로 Allocatable이 `22308Mi → 26054Mi`가 되어 **메모리 %만** 내려갔다.
당시 기록된 *"84% / 52%"* 의 `52%`는 **옛 분모(22308Mi) 기준**이고 현행은 **44.7%** 다.
**CPU %는 분모(`8000m`)가 안 바뀌어 전부 그대로다** — 아래 표의 `%`는 현행 분모 기준으로 재계산했다.

| 구분 | 워크로드 | req CPU | req Mem | lim CPU | lim Mem |
| --- | --- | --- | --- | --- | --- |
| **상주(baseline)** | kube-system(kind CP·CNI·coredns·local-path) ¹ | 950m | 290Mi | — | — |
| | Spark Operator(Apache, **JVM**) | 250m | 512Mi | 500m | 1Gi |
| | SeaweedFS(master+volume+filer+s3) | 300m | 768Mi | 1 | 1.5Gi |
| | **CloudNativePG 오퍼레이터**(컨트롤러) | 100m | 200Mi | 250m | 384Mi |
| | Catalog Postgres(Iceberg JDBC, CNPG `Cluster` 1인스턴스) | 250m | 512Mi | 500m | 768Mi |
| | **ingress-nginx 컨트롤러**(UI 고정 URL) ² | 100m | 90Mi | — | — |
| | **상주 기준선 소계**(Flink Operator 미설치 상태) | **1950m** | **2372Mi** | | 24% / 9% |
| **Flink Operator** ³ | 컨트롤러 파드 | 200m | 512Mi | 500m | 1Gi |
| | 웹훅(webhook) 컨테이너 | 100m | 256Mi | 200m | 512Mi |
| | **상주 + Flink Operator**(= 회수 후 실측) | **2250m** | **3140Mi** | | **28% / 12%** |
| **Dagster**(상주) ⁷ | `dagster-webserver`(UI·GraphQL) | 100m | 768Mi | 500m | 1536Mi |
| | `dagster-daemon`(스케줄·센서 + run 서브프로세스) | 250m | 1024Mi | 1 | 3Gi |
| | **상주 + Flink Operator + Dagster — 실측** | **2600m** | **4932Mi** | | ✅ **32% / 18%** |
| **온디맨드 상주** | **Spark Connect 서버**(dbt 접속용, 미사용 시 `--replicas=0`) | 500m | 1536Mi | 1 | 2Gi |
| | Flink JobManager(세션 클러스터, 잡 없어도 상주) | 1000m | 2048Mi | 1 | 2Gi |
| | **동시 피크의 기저**(관측 차분) | **4100m** | **8516Mi** | | 51% / 33% |
| **STREAM(일시)** | Flink TaskManager × 1 — **잡 제출 시 온디맨드**, 종료 시 자동 회수 ⁶ | 1000m | 2048Mi | 1 | 2Gi |
| **BATCH(일시)** | Spark driver ⁴ | 1000m | **1433Mi** | 1 | 1.5Gi |
| | Spark executor × **1** ⁴ ⁵ | 1000m | **1433Mi** | 1 | 1.5Gi |
| | **동시 기동 피크(3워크로드 상주) + Dagster** | **7100m** | **13430Mi** | | ⚠️ **89% / 52%** |

¹ **구 문서의 `750m/250Mi`는 틀렸고 합계만 맞았다.** 실측은 **950m/290Mi**다. 합계가 맞으면
  내역이 틀려도 오래 살아남는다 — 행 단위로 재측정한다.
² ingress-nginx는 **`limits`가 없는 유일한 워크로드**다(외부 매니페스트 그대로,
  [conventions/k8s.md](conventions/k8s.md) §2 예외). 상주 부하가 작아 예산 영향은 미미하지만 상한이 없다.
³ **Flink Operator 차트는 자원을 선언하지 않는다** — `helm show values` 실측 결과
  **`operatorPod.resources: {}` · `operatorPod.webhook.resources: {}`** 로 **둘 다 비어 있다.**
  `terraform/lakehouse-platform/operators.tf`의 `helm_release.flink_operator`에
  **`set` 8종**(`operatorPod.resources.{requests,limits}.{cpu,memory}` 4종 +
  `operatorPod.webhook.resources.*` 4종)이 없으면 이 표의 두 행이 **처음부터 거짓**이 된다
  (구 `scripts/k8s-operators.sh`의 helm 호출에서 이관됨).
  이는 Spark Operator에서 이미 밟은 함정의 **거울상**이다 — 그쪽은 차트 기본값 `1000m/2048Mi`가
  **조용히 적용**됐고(과대), 이쪽은 **0으로 잡혀 BestEffort가 된다**(과소). 방향만 반대이고
  **"helm은 값을 안 줘도 에러를 내지 않는다"** 는 원인은 같다.
⁴ **driver·executor의 실제 request는 `1433Mi`이지 유도값 `1408Mi`가 아니다.**
  `spark.{driver,executor}.memory=1024m`에 JVM overhead가 더해진 값을 오퍼레이터가 request로 환산한다.
  **표에는 실측 1433Mi를 쓰고 유도값을 쓰지 않는다** — 계획 예측이 메모리에서 `+50Mi`(25Mi × 2) 빗나간
  원인이 정확히 이것이었다.
  **그래서 이 값은 Spark 3.5.9 → 4.1 상향 시 재실측 대상이다**(결정됨 · **이행 전** —
  [architectures/spark.md](architectures/spark.md) §Spark 3.5.9 → 4.1 상향 결정). `1433Mi`는
  **선언값이 아니라 JVM overhead가 더해진 환산 결과**라, 런타임이 바뀌면(Scala 2.13 · JDK 17+)
  같은 `1024m` 선언에서도 다른 값이 나올 수 있다. **새 값을 여기 미리 적지 않는다 — 상향 후 실측한다.**
  이것은 §(B) 서두의 교훈("엔진·오퍼레이터를 교체하면 사이징 근거가 통째로 무효가 된다")이
  **엔진 버전 축에서 반복되는 경우**다.
⁵ 🔴 **executor는 1개로 제한**한다. 2개면 `6750m + 1000m = 7750m`,
  즉 **Allocatable의 97%** 라 사실상 여유가 없다.
⁶ **"일시"는 배치 잡 기준이다** — **스트리밍 잡의 TM은 잡 수명 내내 상주**한다.
  예산상으로는 배치 동시 피크보다 낮지만 **경계 ①의 전제는 여전히 깨진다.**
  ⇒ 이 표는 그대로 둔다. TM 상주는 **상시 구성이 아니라 시연 창 안의 일시 구성**이고,
  상시로 돌려야 할 때 **TM 상주 기준으로 재실측**한다.

⁷ **Dagster 행은 `kubectl describe node` 실측**이다(분모 = Allocatable 8000m/26054Mi).
  순증은 **350m/1792Mi**이고, 이 값은 회수 다이얼이 듣지 않아 **모든 시나리오에 상시 더해진다**
  ([conventions/k8s.md](conventions/k8s.md) §9-3 경계 ④).
  ⚠️ **마지막 행(7100m/13430Mi)만은 실측이 아니라 산술**이다 — 실측 6750m에 실측 350m을 더한 값이고,
  BATCH+STREAM+Dagster를 **동시에 띄운 관측 창은 아직 열린 적이 없다**. 재측정은 §(C-2) 순서를 따른다.
  값 자체는 두 실측의 합이라 신뢰할 만하지만 **"관측했다"로 인용하지 않는다.**

> 🔴 **`BestEffort` 파드는 `describe node` 합계에 0으로 잡힌다.** 실측 **6개**로 늘었다
> (cert-manager 3 · barman-cloud · kube-proxy · local-path-provisioner).
> **"합계에 없다 = 없다"가 아니다.** 예산을 대조할 때 함께 돌린다.
>
> ```shell
> kubectl get pods -A \
>   -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,QOS:.status.qosClass \
>   | grep BestEffort
> ```

> **Redpanda는 아직 미도입**이라 위 표에 없다. 도입 시 STREAM 피크가 그만큼 올라가므로
> **이 표와 [conventions/k8s.md](conventions/k8s.md) §9-3의 경계를 함께 재계산**한다.

> **Spark Operator 행 정정** — 종전 `100m/256Mi req · 250m/512Mi lim`은
> **Kubeflow(Go) 오퍼레이터 시절 수치**였다. 프로젝트는 Apache 오퍼레이터(**JVM**)로 이전했는데
> 이 표를 재검토하지 않아, 아래 두 가지가 동시에 성립하고 있었다.
>
> 1. **표가 거짓을 말했다** — `scripts/k8s-operators.sh`에 근거 주석만 있고 `--set`이 빠져
>    실제로는 차트 기본값 **`1000m/2048Mi`** 가 적용됐다. helm은 값을 지정하지 않아도 에러를
>    내지 않으므로 **선언과 실제가 4~8배 벌어진 채 조용히 유지**됐다(실사용은 196Mi — 10.7배 과예약).
> 2. **표대로 집행했으면 오퍼레이터가 죽었다** — 차트 jvmArgs가 `-XX:MaxRAMPercentage=80`이라
>    힙 상한이 컨테이너 한도에 직접 연동된다. 한도 `256Mi` → 힙 205Mi인데 실측 `RssAnon`이
>    이미 193MB라 기동 즉시 OOMKill이다.
>
> 새 값(`250m/512Mi req · 500m/1Gi lim`)은 **실측 196Mi 기준 한도 1Gi(힙 819Mi)로 약 4배 여유**이며,
> `InitialRAMPercentage=80`+`AlwaysPreTouch` 선점이 유효해지더라도 한도 안에 들어온다
> (= **선점 유효/무효 어느 가설에서도 안전**). 측정은 `crictl stats`와 컨테이너 내부
> `/proc/1/status`·`cgroup memory.current`를 병행했다(kind에 metrics-server가 없다).
>
> **교훈**: 이 표는 오래 "검증된 값"으로 인용됐지만 **아무도 실측과 대조하지 않았다.**
> 오퍼레이터를 교체하면 언어 런타임이 바뀌고, 그러면 사이징 근거가 통째로 무효가 된다.
> 엔진·오퍼레이터 교체 시 **이 표의 해당 행을 함께 재측정**한다([philosophy](philosophy.md) 원칙 7).

### (C) 운영 다이얼 (초과 시 조절 순서)

0. **★★★★★ Spark Connect 서버 스케일 0** — dbt를 돌리지 않을 때 `kubectl scale deploy/spark-connect --replicas=0`.
   유일하게 **상주하는 컴퓨트**다. 켜둔 채 잊으면 예산을 계속 갉아먹는다.
   **역방향도 규율이다** — 0으로 내려둔 상태에서는 `port-forward svc/spark-connect`가 붙지 않는다.
   dbt·노트북을 쓰기 전에 `--replicas=1`로 되돌린다([README](../README.md) §2-1).
1. **★★★★★ Flink 세션 클러스터 회수** — 검증·데모가 끝나는 **그 자리에서** `FlinkDeployment`를 삭제한다.
   JM은 **잡이 없어도 1000m/2048Mi를 상주 점유**하며, 이 규율이 깨져 13시간 샌 전례가 있다
   ([conventions/k8s.md](conventions/k8s.md) §9-3).
2. **★★★★★ `spark.executor.instances` ≤ 1 (Flink 세션이 떠 있는 동안)** — Dagster 상주분(350m)이
   더해지면서 동시 피크가 `7100m`(89%)이 됐고, executor를 하나 더 붙이면 **`8100m` = 101%** 다.
   이제 "여유가 없다"가 아니라 **스케줄 자체가 실패**한다. 늘려야 하면 **Flink 세션을 먼저 내린다.**
3. **★★★★☆ Flink TaskManager slot/개수** — 스트리밍 병렬도. 기본 TM 1개(× 2슬롯).
   TM은 **잡 제출 시 온디맨드**로 뜨고 잡 종료와 함께 회수되므로 유휴 비용은 0이다.
   **단, 이것은 배치 잡에서 관측된 전제다** — **스트리밍 잡의 TM은 잡 수명 내내 산다**
   (실측: 상주 피크 `4750m (59%)` / `8772Mi (39%)`). 아래 **"스트리밍 시"** 블록 참조.
   **스트리밍 잡을 내릴 때는 순서가 있다** — `externalized-checkpoint-retention` 기본값이
   `DELETE_ON_CANCELLATION`이라 `flink cancel` 후 체크포인트가 **지워진다.**
   **증거·상태 수집은 취소 전에** 한다([architectures/flink.md](architectures/flink.md) §순서 함정).
4. **★★★☆☆ Redpanda dev 모드 메모리**(도입 시) — `--memory`/`--smp`로 축소, 데모 후 스케일 0.
   **Redpanda는 미도입 유지**로 결정됐다(스트림 소스가 Iceberg bronze 스트리밍
   읽기로 바뀜 — [architectures/flink.md](architectures/flink.md)). 이 다이얼은 **당분간 죽은 항목**이며,
   [conventions/k8s.md](conventions/k8s.md) §9-3 **경계 ③(Redpanda 도입 시 재계산)도 발동하지 않는다.**
5. **★★★☆☆ `DAGSTER_MAX_CONCURRENT_RUNS`** — daemon 파드 안의 run 동시성.
   ⚠️ **회수 다이얼이 아니라 처리량 다이얼**이다 — 내려도 상주 자원은 줄지 않는다.
   Dagster 자체는 오케스트레이터라 **회수 대상이 아니다**(내리면 스케줄·센서가 함께 멈춘다).
   올릴 때는 daemon `limits.memory`를 아래 §Dagster 공식대로 **같은 커밋에서** 올린다.

> 🔴 **스트리밍은 동시 기동 허용의 전제를 깬다.**
>
> 동시 기동 허용은 *"Flink 상주는 JobManager뿐이고 TaskManager는 잡 제출 시 온디맨드로 떴다
> 곧 회수된다"* 를 전제로 한다. **스트리밍 잡은 TM이 상시 생존하므로 그 전제가 성립하지 않는다.**
>
> **채택한 대응은 「시연 창 안에서만 돌리고 그 자리에서 회수」다.**
> 경계를 개정해 TM 상주를 예산에 반영하는 쪽은 택하지 않았다 —
> 예산상으로는 통과하지만 **예산 통과와 전제 성립은 다른 축**이고,
> 개정하면 *"TM이 떠 있으면 잡이 도는 중"* 이라는
> **`kubectl get pods` 한 번으로 되는 이분법**을 잃는다.
>
> **재검토 트리거**는 스트리밍을 시연이 아니라 **상시**로 돌려야 할 때다 —
> 그때 §(B)를 TM 상주 기준으로 다시 쓰고, 재측정 시
> **긴 쪽을 먼저 띄우는 순서 규칙**을 그대로 적용한다.
> 결정 전문은 정본 [conventions/k8s.md](conventions/k8s.md) §9-3.

### (C-2) 실측 피크

📌 **실측값은 저장소 밖에 있다** — `$OBSIDIAN_VAULT/status/observations.md` §자원 실측 피크.
관측 시각·모집단·분모가 그쪽에 병기돼 있다.

**모집단을 반드시 함께 읽는다.** `kubectl describe node`의 Allocated resources는
**Σrequests이지 실사용량이 아니다.** 분모도 **노드 Allocatable이지 VM 총량이 아니다** —
둘을 섞으면 예산이 어긋난다.

⚠️ **피크는 한 번에 잡히지 않는다.** 워크로드가 뜨는 창이 짧으면 관측이 그 창을 놓치고,
**놓친 관측은 유리한 결론처럼 보인다**(여유가 있는 것으로 읽힌다).
워크로드를 더 얹기 전에는 **피크 창에서 다시 뜬다.**

### (D) 호스트 축 — VM 밖의 예산

**클러스터 예산만 보면 안 된다.** VM이 가져간 몫은 호스트에서 통째로 빠져나간다.
**Dagster도 VM 안**으로 들어가 호스트 축이 그만큼 가벼워졌다 —
남은 상시 호스트 소비자는 macOS 자체와 (옵션) Jupyter뿐이다.

🔴 **산술상 잔여를 실제 여유로 읽지 않는다.** macOS는 **메모리 압축**으로 버티므로
`unused`가 수백 MB만 남아도 동작한다 — 성립하는 것과 여유가 있는 것은 다르다.

**`free` 델타를 프로세스 비용으로 쓰지 마라.** OS가 purgeable·compressor에서 회수해
free를 일정하게 유지하므로, **"free 델타"는 *쓴 양*이 아니라 *OS가 남기기로 한 양*을 센다.**
⇒ 프로세스 비용은 **RSS**로, 시스템 압박은 **swap·compressor**로 본다 — **다른 축**이다.

#### 호스트 압박 판정 지표

| 신호 | 판정 | 근거 |
| --- | --- | --- |
| **swap `used` > 0** | ✅ **유효 · 최강** | 압축으로 못 버텨 디스크로 밀린 상태 |
| **`swapouts` 누적값** | ✅ **유효** | `used`는 회수되면 0으로 돌아가 **지나간 압박을 못 본다** |
| port-forward **15002 · 18333** 사망 | ✅ 유효 | 압박 신호로 읽는다 |
| port-forward **15432** 사망 | ❌ **무효 — 지표로 쓰지 마라** | 아래 |

**15432(`catalog-postgres-rw`)는 호스트 압박과 무관하게 죽는다** —
**클라이언트 접속이 끝날 때마다 결정론적으로** 끊긴다. swap이 0인 상태에서도 죽고,
같은 kubectl·같은 클러스터인데 **15002·18333은 생존**한다.
원인은 Postgres 경로가 FIN이 아닌 **RST**로 끊고 kubectl이 그것을 **터널 전체의 치명 오류**로
취급하는 것이다.

⇒ *"port-forward가 끊기면 호스트를 의심한다"* 를 그대로 두면 **다음 사람이 정확히 오진한다.**
우회는 `until` 루프 재기동이되 **재기동 시각을 남긴다** — 조용히 되살리면 지표가 지워진다.

```shell
until kubectl port-forward svc/catalog-postgres-rw 15432:5432; do
    echo "$(date '+%F %T') 15432 재기동" >> /tmp/pf-15432.log
    sleep 1
done
```

### (E) 참고 수치 근거

- Flink Operator FlinkDeployment 예시: JobManager/TaskManager 각 `memory 2048m / cpu 1`(권장 예시값)
  [Apache Flink Kubernetes Operator — custom-resource/overview].
- podman machine 기본 1 CPU / 2048 MiB → 반드시 상향 지정 [podman-machine-init — Podman docs].
- Flink Operator 차트는 `operatorPod.resources`·`operatorPod.webhook.resources`를 **빈 맵으로 둔다**
  (`helm show values flink-operator-repo/flink-kubernetes-operator` 실측, chart 1.15.0) →
  `--set` 미지정 시 컨트롤러·웹훅이 **`BestEffort`** 가 된다.

## 조정 지점 요약

| 서비스      | 핵심 조정 항목                                            | 위치                                              |
| ----------- | -------------------------------------------------------- | ------------------------------------------------- |
| `trino`     | JVM heap(`-Xmx`), `query.max-memory(-per-node)`, headroom | `trino/etc/jvm.config`, `trino/etc/config.properties` |
| `dagster`   | `max_concurrent_runs`, op 동시성, dbt `threads`           | `dagster.yaml`, `dbt_pipelines/profiles.yml`      |
| `postgres`  | `shared_buffers`, `work_mem`, `max_connections`           | postgres command / `postgresql.conf`             |
| `seaweedfs` | volume 수·인덱스 메모리                                   | `compose.yml`의 `seaweedfs` command               |
| 공통        | CPU·메모리 한도                                           | `compose.yml` `deploy.resources.limits/reservations` |

## Docker 서비스 자원 한도 (compose)

```yaml
services:
  trino:
    deploy:
      resources:
        limits: { cpus: "2.0", memory: 2G }
        reservations: { memory: 1G }
```

- compose v2는 비-swarm 환경에서도 `deploy.resources.limits`(cpus·memory)를 적용한다.
- 모든 서비스 `limits.memory` 합이 호스트 RAM을 넘지 않도록 한다.

## Trino

> 현재 `compose.yml`은 `trino/etc/catalog/`만 마운트한다. heap·메모리를 조정하려면
> `trino/etc/jvm.config`·`trino/etc/config.properties`를 추가하고 `trino/etc/`를 마운트한다.

메모리는 JVM heap에서 출발한다.

- `jvm.config`: `-Xmx<heap>` — 컨테이너 메모리의 약 **70~80%**
- `config.properties`:
  - `query.max-memory-per-node` — 기본 **heap × 0.3**. `per-node + heap-headroom < heap` 제약 내에서 상향 가능
  - `query.max-memory` — 클러스터 전체 한도(기본 **20GB**; 단일 노드면 per-node 수준)
  - `memory.heap-headroom-per-node` — Trino 미추적 할당용 버퍼, 기본 **heap × 0.3**

예) 컨테이너 4G → `-Xmx3G` → 기본 per-node 0.9G.
  상향 시 `per-node + headroom 0.9G < 3G`를 유지한다.

> 큰 조인/집계가 heap을 넘으면 `EXCEEDED_LOCAL_MEMORY_LIMIT`가 난다.
> 메모리를 늘리거나 쿼리를 분할/스필 설정을 검토한다.

### 메모리 설정 3중 결합 (함께 검증)

세 파일의 값이 **한 방향 제약**으로 묶여 있어, 하나만 바꾸면 기동 실패나 OOM이 난다.
아래 부등식을 위→아래로 만족시킨다.

```
compose.yml  memory limit
  └── jvm.config  -Xmx            (≤ limit − JVM 비힙 오버헤드)
        └── config.properties
              ├── memory.heap-headroom-per-node   (Trino 미추적 할당 버퍼)
              └── query.max-memory-per-node       (≤ Xmx − headroom)
```

| 파일 | 항목 | 현재값(6G 컨테이너 예) | 제약 |
| --- | --- | --- | --- |
| `compose.yml` | `deploy.resources.limits.memory` | 6G | ≥ Xmx + 비힙 오버헤드 |
| `trino/etc/jvm.config` | `-Xmx` | 예: 4~5G | < 컨테이너 limit |
| `config.properties` | `memory.heap-headroom-per-node` | 기본 Xmx×0.3 | JVM 비쿼리 오버헤드 |
| `config.properties` | `query.max-memory-per-node` | ≤ Xmx − headroom | 초과 시 쿼리 OOM |

**JVM 비힙 오버헤드**(컨테이너 limit이 `-Xmx`보다 커야 하는 이유):

```
컨테이너 limit  >  -Xmx  +  ReservedCodeCache(~256M)  +  Metaspace(~400M) + 기타
     6g         >   5G   +          256M               +      ~400M        ≈ 5.7G  (✓ 여유)
```

> `-Xmx`를 컨테이너 limit에 바짝 붙이면(예: 6G 컨테이너에 `-Xmx6G`) 비힙 영역이 밀려 컨테이너
> OOM Kill이 난다. **`-Xmx`는 컨테이너 memory의 70~80%**를 넘기지 않는다.
> 변경 시 `compose.yml`·`jvm.config`·`config.properties` 세 파일을 **함께** 검증한다.

## Dagster (동시성)

- **run 수** — `dagster.yaml`의 `concurrency.runs.max_concurrent_runs`
  : 동시 실행 run 수. 각 run은 별도 프로세스.
  (현재 프로젝트는 구방식 `run_coordinator: QueuedRunCoordinator`의 `max_concurrent_runs: 10` 사용)
- **op/asset 동시성** — `concurrency.pools.default_limit`(풀별 한도) 또는 job multiprocess executor `max_concurrent`
  : 한 run 안에서 병렬 실행되는 op 수. 보통 CPU 코어 수에 맞춘다.
- **dbt 병렬도** — `profiles.yml`의 `threads`. 엔진으로 보내는 동시 쿼리 수이고,
  호스트별 권장은 아래 프로파일 표를 따른다.

```yaml
# dagster.yaml — 최신 동시성 블록
concurrency:
  runs:
    max_concurrent_runs: 10
  pools:
    default_limit: 3
```

> 적재 헬퍼(`load_heavy_csv_gz_to_iceberg`)는 run당 메모리를 `chunk_rows`로 제어한다.
> **run당 메모리 × `max_concurrent_runs` ≤ 호스트 RAM**이 되도록 둘을 함께 낮춘다.

### daemon 메모리 계산 (multiprocess OOM 방지)

`DefaultRunLauncher` + multiprocess executor는 run마다 daemon 컨테이너 안에서 **자식 프로세스를
fork**한다. fork 순간 부모 메모리가 복사(Copy-on-Write)되므로 **피크 = 부모 + 자식 합산**이
컨테이너 `memory` 한도를 넘으면 OOM Kill(SIGKILL)이 난다. 따라서 daemon `memory`는 다음으로 잡는다.

```
daemon 필요 메모리
  = 데몬 기본(~300MB)
  + max_concurrent_runs × run당 피크 메모리 × 1.5(여유율)

예) bronze 적재(청크 스트리밍, 피크 ~500MB), concurrent=2:
    300MB + 2 × 500MB × 1.5 = 1.8g → limit 2g
예) 수백만 행 DataFrame 변환(피크 ~4GB), concurrent=2:
    300MB + 2 × 4GB × 1.5 = 12.3g → limit 16g
```

**결정 절차**: ① 가장 메모리를 많이 쓰는 에셋을 특정 → ② `kubectl top pod -l app=dagster-daemon`
또는 UI run 로그로 피크 추정 → ③ 위 공식 적용 → ④ 매니페스트의 **ConfigMap 값과 `resources`를 함께**
수정 → ⑤ 실측 검증.

**의존성 연동 규칙** — `max_concurrent_runs`와 daemon `memory`는 강결합이다.
한쪽만 바꾸면 OOM 또는 낭비된 한도가 발생한다.
⚠️ in-cluster에서는 **둘이 같은 파일에 있다** — `k8s/dagster/dagster-deploy.yaml`의
ConfigMap `DAGSTER_MAX_CONCURRENT_RUNS`와 daemon `resources.limits.memory`.
`dagster.yaml`은 그 값을 `env:`로 **참조만** 한다(값을 갖지 않는다). 호스트 실행분은 `.env`.
아래 표의 `compose.yml` 축은 **호스트 경로(`--profile host-dagster`)에만** 해당한다.

| 변경 | 연동 필수 | 방향 |
| --- | --- | --- |
| `max_concurrent_runs` 증가 | daemon `memory` 재계산·상향, `cpus` 상향 | `dagster.yaml` → `compose.yml` |
| `max_concurrent_runs` 감소 | daemon `memory`·`cpus` 하향 가능(절약) | `dagster.yaml` → `compose.yml` |
| 데이터 집약 에셋 추가 | 피크 메모리 재추정 → daemon `memory` 재계산 | `assets.py` → `compose.yml` |
| daemon `memory` 변경 | 호스트 가용 RAM·전체 서비스 합계 검증 | `compose.yml` 내부 |

> 새 데이터 집약 에셋(수백만 행 변환·윈도잉 등)을 추가하면 위 계산을 재실행하고 리소스 설정을 갱신한다.
> 단일 daemon이 모든 자식 프로세스의 메모리를 공유하므로, 규모가 커지면 `dagster-celery`(Worker 분리)·
> `dagster-k8s`(run당 Pod)로의 전환을 검토한다.

## Postgres

**두 벌**이고 성격이 다르다 — 자원도 따로 잡는다.

| 대상 | 담는 것 | 위치 | 튜닝 지점 |
| --- | --- | --- | --- |
| 메타 Postgres | Dagster run·이벤트·스케줄 상태 | compose(호스트) | `compose.yml` / `postgresql.conf` |
| 카탈로그 Postgres | Iceberg 테이블 메타(JDBC 카탈로그) | K8s(CNPG `Cluster`) | `k8s/catalog-postgres.yaml`의 `spec.postgresql.parameters` |

- `shared_buffers` ≈ RAM × **0.25**, `work_mem`(정렬/조인 버퍼, 연결당), `max_connections`
- 동시 run·pyiceberg 연결이 늘면 `max_connections`를 상향한다.
- 카탈로그 쪽은 **테이블 메타만** 담아 데이터가 작다 → `shared_buffers 128MB`로 충분하다.
  접속자는 Dagster(pyiceberg)·Spark·Flink·dbt 4종이라 연결 수가 먼저 병목이 된다.
- **메타 PG도 이 클러스터에 있다** — 같은 `catalog-postgres`에 `Database` CR로
  `dagster` DB를 더했다. 구 근거였던 "순환 의존"은 Dagster가 함께 클러스터로 들어오면서 소멸했다
  (둘 다 없으면 둘 다 없는 것이지 순환이 아니다). ⇒ 접속자는 이제 **메타 축까지 5종**이라
  `max_connections`를 볼 때 이 축을 함께 센다.

## SeaweedFS

대체로 I/O 바운드이며, 볼륨 인덱스가 메모리를 사용한다.

- `-volume.max`(볼륨 수), 인덱스 방식(`-volume.index=leveldb`로 메모리 절감)

## 호스트 크기별 권장 프로파일 (출발점)

| 항목                                | 8 GB      | 16 GB     | 32 GB     |
| ----------------------------------- | --------- | --------- | --------- |
| trino 컨테이너 / `-Xmx`             | 2G / 1.5G | 4G / 3G   | 8G / 6G   |
| trino `query.max-memory-per-node`   | 1GB       | 2GB       | 4GB       |
| dagster `max_concurrent_runs`       | 2         | 4         | 8         |
| dbt `threads` (dev)                 | 2         | 4         | 8         |
| postgres `shared_buffers`           | 256MB     | 512MB     | 1GB       |

> 표는 출발점이며 실제 데이터량·쿼리 특성에 맞춰 조정한다.
> 변경 시 `compose.yml`·Trino 설정·`dagster.yaml`·`profiles.yml`을 함께 갱신한다.

## 참고

- Trino — Resource management properties: https://trino.io/docs/current/admin/properties-resource-management.html
- Trino — Deploying Trino(JVM config): https://trino.io/docs/current/installation/deployment.html
- Dagster — Managing concurrency: https://docs.dagster.io/guides/operate/managing-concurrency
- PostgreSQL — Resource Consumption: https://www.postgresql.org/docs/current/runtime-config-resource.html
- Docker Compose — deploy.resources: https://docs.docker.com/reference/compose-file/deploy/#resources
- SeaweedFS — wiki: https://github.com/seaweedfs/seaweedfs/wiki
- Apache Flink Kubernetes Operator — 리소스 설정: https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-main/docs/custom-resource/overview/
- podman machine init(자원 지정): https://docs.podman.io/en/latest/markdown/podman-machine-init.1.html
- kind — Podman provider: https://kind.sigs.k8s.io/docs/user/rootless/
