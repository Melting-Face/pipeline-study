# 리소스 산정 (resource sizing)

호스트(Docker)에 할당된 CPU·메모리에 맞춰 각 서비스의 옵션을 조정한다.
**서비스 메모리 한도의 합 ≤ 호스트 RAM − OS/버퍼 여유(약 1~2 GB)** 를 유지한다.

> 조정 지점은 이 문서에서 한곳으로 관리하고, `compose.yml`의 `deploy.resources`와
> 각 서비스 설정 파일을 함께 맞춘다. (단순함·명시적 — [philosophy.md](philosophy.md))

> **문서 구성**: 아래 "Kubernetes 재설계 시나리오"는 **목표(이행) 배분**([redesign.md](redesign.md)),
> 그 이후 섹션(Trino·Dagster·Postgres…)은 **현행 compose 배분**이다. Trino는 재설계에서 제거되므로
> Trino 섹션은 이행 완료 시 레거시 참조가 된다.

## Kubernetes 재설계 시나리오 (kind + Podman · 8 CPU / 22.3 GiB) 🚧

> 대상: [redesign.md](redesign.md)의 목표 토폴로지. **Dagster는 호스트**(이 예산 밖), 컴퓨트·데이터
> 서비스만 로컬 K8s(kind on Podman)에 둔다. 컴퓨트는 **Spark(배치) / Flink(스트리밍)** 2엔진이며,
> 2026-08-22 실측으로 **시분할 → 동시 기동**으로 규약이 바뀌었다([conventions/k8s.md](conventions/k8s.md) §9-3).

### (A) 예산의 단위 축은 **셋**이다 🔴

메모리를 인용할 때 **어느 축의 값인지 반드시 병기**한다. 셋은 서로 다른 것을 세며, 숫자만 옮기고
단위를 바꾸면 조용히 틀린다([philosophy.md](philosophy.md) §계측 단위).

| 축 | 값 | 무엇을 세는가 | 어디서 읽나 |
| --- | --- | --- | --- |
| **VM 총량** | **8 CPU / 22888 MiB** (= 22.35 GiB) | podman machine에 할당된 전체 | `podman machine inspect` |
| **`MACHINE_MEMORY_MIB`** | `22888` | 위와 **같은 축**(VM 총량)의 선언값 | [`scripts/k8s-env.sh`](../scripts/k8s-env.sh) |
| **노드 Allocatable** | **8000m / `22843508Ki`** (≈ 22308Mi ≈ 21.8 GiB) | 파드가 실제로 예약 가능한 양 | `kubectl describe node` |

- 🔴 **VM 총량(22888 MiB)이 노드 Allocatable(≈22308Mi)보다 581 MiB 많다.** 차이는 노드 OS·kubelet
  예약분이며, **둘은 다른 축이라 섞어 쓰면 안 된다.** 이 문서의 **백분율(%)은 전부 Allocatable 기준**이다.
- 🔴 **단위를 바꾸는 순간이 함정이다** — `22843508Ki`를 "22843Mi"로 옮긴 중간 기록이 있었고, 이는
  **536Mi(2.4%) 과대**다(`Ki→Mi`는 ÷1024이지 접두어 치환이 아니다). 표에는 **raw 값을 남기고
  환산식을 함께** 적는다: `22843508 ÷ 1024 = 22308.1`.
  ⚠️ 같은 이유로 `22307Mi`로 적힌 기록도 1Mi 어긋난 값이다(정확히는 **22308Mi**). 백분율 결론
  (52%·14%)은 어느 쪽이든 동일하지만, **값과 함께 "무엇을 세는가"를 적지 않으면 재검산이 불가능**하다.

```bash
# macOS(Apple Silicon): 자원은 머신 생성 시 확정(사후 변경은 재생성 필요), kind는 rootful 요구
podman machine init dagster-k8s --rootful --cpus 8 --memory 22888 --disk-size 120
podman machine start dagster-k8s
export KIND_EXPERIMENTAL_PROVIDER=podman
kind create cluster --name lakehouse --config kind-cluster.yaml

# 실측 확인 (환산식을 함께 남긴다)
kubectl get node -o jsonpath='{.items[0].status.allocatable}'   # cpu:8, memory:22843508Ki
```

- **호스트 headroom**: VM 22.35 GiB는 **호스트에서 통째로 빠져나간다.** Dagster(webserver+daemon+
  메타 Postgres)가 호스트에서 도므로 호스트 여유를 따로 봐야 한다 → **아래 (D) 호스트 축**.
- **disk 120 GB**: SeaweedFS(원천 csv.gz + Iceberg parquet) + Redpanda 로그 + 이미지 레이어 대비.

### (B) 컴포넌트 배분 (requests / limits) — 동시 기동

원칙: **Σrequests ≤ 노드 Allocatable(8000m / ≈22308Mi)**. 2026-08-22 실측으로 **BATCH+STREAM 동시
피크가 84% / 52%** 에 들어옴이 확인돼 시분할 금지가 **동시 기동 허용**으로 바뀌었다
([conventions/k8s.md](conventions/k8s.md) §9-3).
아래 표의 `req`는 전부 **실제 선언값**이며, 합계 행은 **관측 차분으로 검산**된 값이다.

| 구분 | 워크로드 | req CPU | req Mem | lim CPU | lim Mem |
| --- | --- | --- | --- | --- | --- |
| **상주(baseline)** | kube-system(kind CP·CNI·coredns·local-path) ¹ | 950m | 290Mi | — | — |
| | Spark Operator(Apache, **JVM**) | 250m | 512Mi | 500m | 1Gi |
| | SeaweedFS(master+volume+filer+s3) | 300m | 768Mi | 1 | 1.5Gi |
| | **CloudNativePG 오퍼레이터**(컨트롤러) | 100m | 200Mi | 250m | 384Mi |
| | Catalog Postgres(Iceberg JDBC, CNPG `Cluster` 1인스턴스) | 250m | 512Mi | 500m | 768Mi |
| | **ingress-nginx 컨트롤러**(UI 고정 URL) ² | 100m | 90Mi | — | — |
| | **상주 기준선 소계**(Flink Operator 미설치 상태) | **1950m** | **2372Mi** | | 24% / 11% |
| **Flink Operator** ³ | 컨트롤러 파드 | 200m | 512Mi | 500m | 1Gi |
| | 웹훅(webhook) 컨테이너 | 100m | 256Mi | 200m | 512Mi |
| | **상주 + Flink Operator**(= 회수 후 실측) | **2250m** | **3140Mi** | | **28% / 14%** |
| **온디맨드 상주** | **Spark Connect 서버**(dbt 접속용, 미사용 시 `--replicas=0`) | 500m | 1536Mi | 1 | 2Gi |
| | Flink JobManager(세션 클러스터, 잡 없어도 상주) | 1000m | 2048Mi | 1 | 2Gi |
| | **동시 피크의 기저**(관측 차분) | **3750m** | **6724Mi** | | 47% / 30% |
| **STREAM(일시)** | Flink TaskManager × 1 — **잡 제출 시 온디맨드**, 종료 시 자동 회수 ⁶ | 1000m | 2048Mi | 1 | 2Gi |
| **BATCH(일시)** | Spark driver ⁴ | 1000m | **1433Mi** | 1 | 1.5Gi |
| | Spark executor × **1** ⁴ ⁵ | 1000m | **1433Mi** | 1 | 1.5Gi |
| | 🔴 **동시 기동 피크(3워크로드 상주) — 실측** | **6750m** | **11638Mi** | | ✅ **84% / 52%** |

¹ 🔴 **구 문서의 `750m/250Mi`는 틀렸고 합계만 맞았다.** 실측은 **950m/290Mi**다. 합계가 맞으면
  내역이 틀려도 오래 살아남는다 — 행 단위로 재측정한다.
² ingress-nginx는 **`limits`가 없는 유일한 워크로드**다(외부 매니페스트 그대로,
  [conventions/k8s.md](conventions/k8s.md) §2 예외). 상주 부하가 작아 예산 영향은 미미하지만 상한이 없다.
³ 🔴 **Flink Operator 차트는 자원을 선언하지 않는다** — `helm show values` 실측 결과
  **`operatorPod.resources: {}` · `operatorPod.webhook.resources: {}`** 로 **둘 다 비어 있다.**
  `scripts/k8s-operators.sh`의 helm 호출에 **`--set` 8종**(`operatorPod.resources.{requests,limits}.{cpu,memory}`
  4종 + `operatorPod.webhook.resources.*` 4종)을 넣지 않으면 이 표의 두 행이 **처음부터 거짓**이 된다.
  이는 Spark Operator에서 이미 밟은 함정의 **거울상**이다 — 그쪽은 차트 기본값 `1000m/2048Mi`가
  **조용히 적용**됐고(과대), 이쪽은 **0으로 잡혀 BestEffort가 된다**(과소). 방향만 반대이고
  **"helm은 값을 안 줘도 에러를 내지 않는다"** 는 원인은 같다.
⁴ 🔴 **driver·executor의 실제 request는 `1433Mi`이지 유도값 `1408Mi`가 아니다.**
  `spark.{driver,executor}.memory=1024m`에 JVM overhead가 더해진 값을 오퍼레이터가 request로 환산한다.
  **표에는 실측 1433Mi를 쓰고 유도값을 쓰지 않는다** — 계획 예측이 메모리에서 `+50Mi`(25Mi × 2) 빗나간
  원인이 정확히 이것이었다.
  🔴 **그래서 이 값은 Spark 3.5.9 → 4.1 상향 시 재실측 대상이다**(2026-08-23 결정 · **이행 전** —
  [architectures/spark.md](architectures/spark.md) §Spark 3.5.9 → 4.1 상향 결정). `1433Mi`는
  **선언값이 아니라 JVM overhead가 더해진 환산 결과**라, 런타임이 바뀌면(Scala 2.13 · JDK 17+)
  같은 `1024m` 선언에서도 다른 값이 나올 수 있다. **새 값을 여기 미리 적지 않는다 — 상향 후 실측한다.**
  이것은 §(B) 서두의 교훈("엔진·오퍼레이터를 교체하면 사이징 근거가 통째로 무효가 된다")이
  **엔진 버전 축에서 반복되는 경우**다.
⁵ 🔴 **executor는 1개로 제한**한다. 2개면 `6750m + 1000m = 7750m` = **Allocatable의 97%** 라
  사실상 여유가 없다((C) 다이얼 참조).
⁶ 🔴 **"일시"는 배치 잡 기준이다** — **스트리밍 잡의 TM은 잡 수명 내내 상주**한다(2026-08-23 실측).
  그 구성의 상주 피크는 **`4750m (59%)` / `8772Mi (39%)`** 로 **배치 동시 피크보다 낮지만**,
  §9-3 **경계 ①의 전제**(온디맨드·수명 1분 미만)는 여전히 깨진다 — **규약 개정은 결정 대기**다
  (§(C) "스트리밍 시" 블록 · §(C-2) 하위 §스트리밍 상주 피크). **이 표는 개정 전까지 그대로 둔다.**

> 🔴 **`BestEffort` 파드는 `describe node` 합계에 0으로 잡힌다.** 현재 cert-manager 3파드가 그 상태이며
> 실사용은 약 82MiB인데 표시는 0이다. **"합계에 없다 = 없다"가 아니다.** 예산을 대조할 때 함께 돌린다.
>
> ```shell
> kubectl get pods -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,QOS:.status.qosClass | grep BestEffort
> ```

> 🔴 **Redpanda는 아직 미도입**이라 위 표에 없다. 도입 시 STREAM 피크가 그만큼 올라가므로
> **이 표와 [conventions/k8s.md](conventions/k8s.md) §9-3의 경계를 함께 재계산**한다.

> 🔴 **Spark Operator 행 정정 (2026-08-19)** — 종전 `100m/256Mi req · 250m/512Mi lim`은
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
   🔴 **역방향도 규율이다** — 0으로 내려둔 상태에서는 `port-forward svc/spark-connect`가 붙지 않는다.
   dbt·노트북을 쓰기 전에 `--replicas=1`로 되돌린다([README](../README.md) §2-1).
1. **★★★★★ Flink 세션 클러스터 회수** — 검증·데모가 끝나는 **그 자리에서** `FlinkDeployment`를 삭제한다.
   JM은 **잡이 없어도 1000m/2048Mi를 상주 점유**하며, 이 규율이 깨져 13시간 샌 전례가 있다
   ([conventions/k8s.md](conventions/k8s.md) §9-3).
2. **★★★★★ `spark.executor.instances` ≤ 1 (Flink 세션이 떠 있는 동안)** — 동시 피크 실측이
   `6750m`(84%)인데 executor를 하나 더 붙이면 **`7750m` = 97%** 로 사실상 여유가 사라진다.
   대용량 인제스트로 executor를 늘려야 하면 **Flink 세션을 먼저 내린다**(둘 중 하나만 확장).
3. **★★★★☆ Flink TaskManager slot/개수** — 스트리밍 병렬도. 기본 TM 1개(× 2슬롯).
   TM은 **잡 제출 시 온디맨드**로 뜨고 잡 종료와 함께 회수되므로 유휴 비용은 0이다.
   🔴 **단, 이것은 배치 잡에서 관측된 전제다** — **스트리밍 잡의 TM은 잡 수명 내내 산다**
   (2026-08-23 실측: 상주 피크 `4750m (59%)` / `8772Mi (39%)`). 아래 **"스트리밍 시"** 블록 참조.
   🔴 **스트리밍 잡을 내릴 때는 순서가 있다** — `externalized-checkpoint-retention` 기본값이
   `DELETE_ON_CANCELLATION`이라 `flink cancel` 후 체크포인트가 **지워진다.**
   **증거·상태 수집은 취소 전에** 한다([architectures/flink.md](architectures/flink.md) §순서 함정).
4. **★★★☆☆ Redpanda dev 모드 메모리**(도입 시) — `--memory`/`--smp`로 축소, 데모 후 스케일 0.
   🔴 **2026-08-23 결정으로 Redpanda는 미도입 유지**가 됐다(스트림 소스가 Iceberg bronze 스트리밍
   읽기로 바뀜 — [architectures/flink.md](architectures/flink.md)). 이 다이얼은 **당분간 죽은 항목**이며,
   [conventions/k8s.md](conventions/k8s.md) §9-3 **경계 ③(Redpanda 도입 시 재계산)도 발동하지 않는다.**

> 🔴 **스트리밍 시 — 경계 ①의 전제가 깨진다 (✅ 측정 완료 · 🔴 규약 개정은 결정 대기 · 2026-08-23)**
>
> §9-3 **경계 ①** 은 *"Flink 상주는 JobManager뿐 · TM은 잡 제출 시 온디맨드로 뜨고 수명 46~52초 뒤
> 자동 회수"* 를 **동시 기동 허용의 전제**로 삼는다. 이 관측은 **배치 잡(2026-08-22)** 에서 나왔다.
> **스트리밍 잡은 TM이 상시 생존하므로 그 전제가 성립하지 않는다.**
>
> | 선택지 | 내용 | 이 문서에 미치는 영향 |
> | --- | --- | --- |
> | (가) | 경계 ① 자체를 개정한다(TM 상주를 예산에 반영) | §(B)·§(C-2)를 **TM 상주 기준으로 재실측** |
> | (나) | **시연 창 안에서만 돌리고 그 자리에서 회수** | 현행 표 유지(회수 규율에 그대로 얹힘) |
>
> **기본안은 (나)** 이고 2026-08-23 스트리밍 실증도 **(나)로 진행**했다.
> ✅ **`미측정`이던 칸은 채워졌다** — 스트리밍 상주 피크 **`4750m (59%)` / `8772Mi (39%)`**
> (§(C-2) 하위 §스트리밍 상주 피크). **배치 동시 피크(84% / 52%)보다 낮다.**
> 🔴 **그럼에도 규약 개정 여부는 사용자 결정 대기이며 이 문서는 §9-3을 바꾸지 않는다** —
> **예산 통과와 전제 성립은 다른 축**이다. (가)를 택하면 §(B)를 TM 상주 기준으로 다시 쓰고,
> 재측정 시 **긴 쪽을 먼저 띄우는 순서 규칙**(아래 §(C-2) 주의)을 그대로 적용한다.

### (C-2) 실측 — 배치 동시 피크(2026-08-22) · **스트리밍 상주 피크(2026-08-23)** · kind `lakehouse` 단일 노드

> **모집단**: `kubectl describe node`의 **Allocated resources**(= Σ**requests**, 실사용량이 아니다).
> **분모**: 노드 Allocatable `8000m` / `22843508Ki`(≈22308Mi). **VM 총량(22888MiB) 축이 아니다.**

| 구성 | Requests CPU | Requests Mem |
| --- | --- | --- |
| 상주 기준선(Flink Operator 미설치) | **1950m (24%)** | **2372Mi (11%)** |
| + Flink Operator(컨트롤러 + 웹훅) = **회수 후 정상 상태** | **2250m (28%)** | **3140Mi (14%)** |
| + Spark Connect + Flink JobManager = **피크의 기저** | 3750m (47%) | 6724Mi (30%) |
| 🔴 **+ Flink TM(스트리밍·상주) = 스트리밍 상주 피크** (2026-08-23) | **4750m (59%)** | **8772Mi (39%)** |
| 🔴 **+ Flink TM + Spark driver + executor = 배치 동시 피크** (2026-08-22) | **6750m (84%)** | **11638Mi (52%)** |

**동시 피크 관측(2026-08-22 01:21:30, 지속 9초, 0.5초 간격 폴링)**

```
01:21:01  Flink 배치 잡 제출
01:21:09  TM Running      (+7초)     4750m /  8772Mi
01:21:28  driver Running             5750m / 10205Mi
01:21:30  executor Running        →  6750m / 11638Mi   ← 3워크로드 동시 상주 (피크)
01:21:39  driver·executor 종료
01:21:54  TM 소멸                    3750m /  6724Mi
```

| | 실측 | 계획 예측 | 오차 |
| --- | --- | --- | --- |
| CPU | **6750m (84%)** | 6750m | **0** |
| Mem | **11638Mi (52%)** | 11588Mi | **+50Mi (+0.43%)** |

- **분해(관측 차분)**: 기저 `3750m/6724Mi` + TM `1000m/2048Mi` + driver `1000m/1433Mi`
  + executor `1000m/1433Mi` = `6750m / 11638Mi`.
- 🔴 **오차 50Mi의 출처는 규명됐다** — driver·executor의 실제 request가 **1433Mi**이고 유도값 1408Mi가
  아니다(`1024m` + JVM overhead). **25Mi × 2 = 50Mi.** 표 (B)에는 **1433Mi**를 쓴다.

> 🔴 **이 피크는 세 번 만에 잡혔다 — 앞의 두 번은 타이밍 때문에 놓쳤다.**
> Spark driver+executor의 수명은 **9초**인데 Flink TM은 **46~52초**로 **5배 차이**가 난다.
> **짧은 쪽(Spark)을 먼저 던지면 긴 쪽이 뜨기 전에 끝나 겹치지 않는다.**
> 재측정할 때는 반드시 **긴 쪽(Flink TM)을 먼저 띄우고 그 창 안에 Spark를 넣는다.**
> 겹침 실패는 **"동시 피크가 낮다"로 보이지 "못 잡았다"로 보이지 않는다** —
> 관측 실패가 유리한 결론처럼 위장하는 경로다([philosophy.md](philosophy.md) 원칙 7).

> **유휴 Flink 비용은 JM뿐임이 관측으로 확인됐다** — JM 상주 `1000m/2048Mi`, **TM은 잡 제출 시
> 온디맨드**(제출 +7초 기동, 수명 46~52초, 잡 종료 시 자동 회수). 예산의 핵심 전제가 검증됐다.
> 🔴 **단 이것은 배치 잡의 관측이다** — 스트리밍 잡의 TM은 아래처럼 **잡 수명 내내 산다.**

#### 🔴 스트리밍 상주 피크 — 최초 실측 (2026-08-23 17:1x~17:37 KST)

**스트리밍 TM이 상주한 상태**의 노드 실사용이다. 종전 이 문서가 `미측정`으로 비워 두었던 칸이 채워졌다.

| 상태 | CPU | Mem |
| --- | --- | --- |
| 회수 후 기준선 | 2250m (28%) | 3140Mi (14%) |
| 🔴 **스트리밍 상주 피크**(JM + TM + Spark Connect) | **4750m (59%)** | **8772Mi (39%)** |
| (참고) 2026-08-22 **배치 동시 피크** | 6750m (84%) | 11638Mi (52%) |

**워크로드별 requests 실측**: JM `1 / 2Gi` · TM `1 / 2Gi` · spark-connect `500m / 1536Mi` ·
seaweedfs `300m / 768Mi` · catalog-postgres `250m / 512Mi`.

- **분해(관측 차분)**: 회수 후 기준선 `2250m/3140Mi` + Spark Connect `500m/1536Mi`
  + JM `1000m/2048Mi` + TM `1000m/2048Mi` = **`4750m / 8772Mi`**.
- ⇒ **스트리밍 상주가 배치 동시 피크보다 낮다**(59% < 84%, 39% < 52%). Spark driver·executor가
  없기 때문이며, 이 구성에는 **`spark.executor.instances`가 걸리지 않는다.**
- ✅ **검증 후 회수 완료** — 세션 클러스터·Spark Connect를 내려 **회수 후 기준선으로 정확히 복귀**했다.

> 🔴 **경계 ①과의 충돌 — 측정됐고 「결정 대기」다.**
> [conventions/k8s.md](conventions/k8s.md) §9-3 **경계 ①** 의 전제(*"TM은 온디맨드·수명 1분 미만"*)는
> **스트리밍 잡에서 여전히 깨진다** — TM이 잡 수명 내내 살기 때문이다.
> **"예산이 통과한다"와 "경계 ①의 전제가 성립한다"는 다른 축**이므로, 수치가 낮다는 사실이
> 규약을 자동으로 맞춰 주지 않는다.
> **이 문서는 규약을 바꾸지 않는다** — §9-3 개정 여부는 **사용자 결정 대기**이고,
> 2026-08-23 실증은 **기본안 (나) 시연 창 한정 + 즉시 회수**로 진행했다.
> 실행 맥락은 [architectures/flink.md](architectures/flink.md) §스트리밍 왕복 실증.

> 🔴 **cert-manager는 위 합계에 없지만 존재한다** — 3파드가 `BestEffort`(requests/limits 미선언)라
> `describe node` 합계에 0으로 잡히며 실사용은 약 82MiB다(barman-cloud 플러그인도 동일).
> 현재는 Flink가 아니라 **CNPG barman-cloud 플러그인의 TLS** 발급자다. (B)의 `grep BestEffort` 명령으로
> 함께 대조한다 — **"합계에 없다 = 없다"가 아니다.**

<details>
<summary>이전 실측 이력 (2026-08-18 / 08-19) — 왜 틀렸는지</summary>

| 일자 | 구성 | Requests CPU | Requests Mem |
| --- | --- | --- | --- |
| 2026-08-18 | 상주만(오퍼레이터 2종 + SeaweedFS + 카탈로그 PG + cert-manager) | 3500m | 5538Mi |
| 2026-08-18 | + Flink 세션 클러스터(JM 1 + TM 1) | 4500m | 7586Mi |
| 2026-08-19 | 상주(Spark Operator + CNPG 2종 + SeaweedFS + Spark Connect + ingress) | 3200m | 5444Mi |

🔴 **2026-08-19의 `3200m/5444Mi`는 합계는 맞지만 내역이 틀렸다.** `describe node` 결과를 그대로
옮긴 값이라 **Spark Operator 드리프트**(선언 없이 차트 기본값 `1000m/2048Mi`)가 통째로 포함돼
있었다. 재실측이 드리프트를 교정한 게 아니라 **정상값으로 박제**했고, 그 결과 (B) 표(당시
`100m/256Mi`)와 (C-2)가 서로 모순인데도 실측 쪽만 갱신돼 **모순이 증거가 아니라 잡음처럼 보이게** 됐다.
당시 예측치 `약 2450m / 3908Mi`도 빗나갔다 — **2026-08-22 실측은 `2250m / 3140Mi`**(회수 후)다.

**교훈**: 실측은 그 자체로 정답이 아니다. **드리프트가 있는 상태에서 뜬 실측은 드리프트를 정당화한다.**
선언(표)과 실측을 대조할 때 **불일치를 실측 쪽으로 맞추기 전에 왜 갈리는지를 먼저 찾는다.**

</details>

### (D) 🔴 호스트 축 — VM 밖의 예산 (2026-08-22 실측)

**클러스터 예산만 보면 안 된다.** VM 22.35 GiB는 호스트에서 통째로 빠져나가고, Dagster는 그 **밖**에서
돈다. 종전 서술 *"호스트 총 RAM ≥ 24 GB(권장 32 GB)"* 는 **VM이 16 GiB이던 시절의 숫자**이며,
VM이 22.35 GiB를 가져가는 현재 **그 여유는 소진됐다.**

| 항목 | 값 |
| --- | --- |
| 호스트 총량 | **32.0 GiB / 10 CPU** |
| VM(podman machine) | **22.35 GiB / 8 CPU** |
| 산술상 잔여 | ~9.6 GiB / 2 CPU |
| **Dagster 실사용 RSS** | **856.8 MiB** (8프로세스) |

**Dagster RSS 내역**(8프로세스 합 856.8 MiB): 코드서버 278.4 + `dg` 179.0 + webserver 158.7
+ daemon 142.9 + 코드서버 86.5 + `uv` 26.5 + 기타.

**부하 단계별 호스트 메모리**

| 시점 | swap used | PhysMem unused | compressor | free % |
| --- | --- | --- | --- | --- |
| Dagster 기동 **전** | 0.00M | **197M** | 12G | — |
| Dagster 기동 후 | 0.00M | 183M | 12G | 53% |
| **TM 창(최대 부하)** | **0.00M** | **107M** | 12G | **50%** |
| 최종 | 0.00M | 92M | **13G** | 48% |

- 🔴 **산술상 잔여 9.6 GiB는 실제 여유가 아니다.** Dagster 기동 **전**에 이미
  `31G used / 197M unused`였다. 실질 여유는 사실상 **0**이고, macOS가 **메모리 압축**으로 버티고 있다.
- ✅ **swap 진입은 0**이었다 — 최대 부하(TM 창)에서도 `swap used = 0.00M`.
- ⚠️ **다만 방향은 단조 악화**다: unused `197M → 92M`, compressor `12G → 13G`.
  현 구성은 **성립하지만 여유가 없다.** 워크로드를 더 얹기 전에 이 표를 다시 뜬다.
- 🔴 **`free` 델타를 Dagster 비용으로 쓰지 마라** — 기동 전후 `free` 델타는 **−8.7 MiB**로,
  실제 RSS **856.8 MiB**의 1%에 불과하다. macOS가 purgeable·compressor에서 회수해 free를 일정하게
  유지하기 때문이다. **"free 델타"는 *Dagster가 쓴 양*이 아니라 *OS가 남기기로 한 양*을 센다.**
  → 프로세스 비용은 **RSS**로, 시스템 압박은 **swap·compressor**로 본다(**다른 축**이다).

#### 호스트 압박 판정 지표 — 재정의 🔴

| 신호 | 판정 | 근거 |
| --- | --- | --- |
| **swap `used` > 0** | ✅ **유효 · 최강** | 압축으로 못 버텨 디스크로 밀린 상태 |
| **`swapouts` 누적값** | ✅ **유효** | `used`는 회수되면 0으로 돌아가 **지나간 압박을 못 본다**. 누적값은 사후 판정이 된다 |
| port-forward **15002 · 18333** 사망 | ✅ 유효 | 이번 실측에서 이 둘은 **끝까지 생존**했다 → 죽는다면 상시 원인이 아니라 압박 신호로 읽는다 |
| 🔴 port-forward **15432** 사망 | ❌ **무효 — 지표로 쓰지 마라** | 아래 참조 |

🔴 **15432(`catalog-postgres-rw`)는 호스트 압박과 무관하게 죽는다** — **클라이언트 접속이 끝날 때마다
결정론적으로** 끊긴다(3/3 재현). `KUBECTL_PORT_FORWARD_WEBSOCKETS=true`로 **변인 하나만** 바꾼
대조군도 동일했고, **swap이 0인 상태에서도** 죽었다. 같은 kubectl·같은 클러스터인데 **15002·18333은
생존**해 대조군이 갈렸다. 원인은 Postgres 경로가 FIN이 아닌 **RST**로 끊고 kubectl이 그것을
**터널 전체의 치명 오류**로 취급하는 것이다.

⇒ 종전 서술 *"port-forward가 끊기면 호스트를 의심한다"* 를 **그대로 두면 다음 사람이 정확히 오진한다.**
우회는 **`until` 루프 자동 재기동 + 재기동 시각 로깅**이다 — 조용히 되살리면 지표가 지워지므로
**언제 몇 번 죽었는지를 남긴다.**

```shell
# 재기동 시각을 남긴다(지표를 지우지 않는다)
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

예) 컨테이너 4G → `-Xmx3G` → 기본 per-node 0.9G. 상향 시 `per-node + headroom(0.9G) < 3G` 유지(예: `query.max-memory-per-node=2GB`)

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
- **dbt 병렬도** — `profiles.yml`의 `threads`(현재 프로파일 값은 [architecture](architectures/overview.md) 참조): Trino로 보내는 동시 쿼리 수. 호스트별 권장은 아래 프로파일 표.

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

**결정 절차**: ① 가장 메모리를 많이 쓰는 에셋을 특정 → ② `podman stats dagster-daemon`(🔴 이 환경에 `docker` 바이너리는 **없다** — compose도 `podman compose`) 또는 UI run
로그로 피크 추정 → ③ 위 공식 적용 → ④ `dagster.yaml`·`compose.yml`·`cpus`를 **함께** 수정 → ⑤ 실측 검증.

**의존성 연동 규칙** — `max_concurrent_runs`(`dagster.yaml`)와 daemon `memory`(`compose.yml`)는 강결합.
한쪽만 바꾸면 OOM 또는 낭비된 한도가 발생한다.

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
- 🔴 **메타 PG를 클러스터로 옮기지 않는다** — Dagster는 호스트에 남으므로, 메타 스토리지를 kind에 두면
  클러스터가 없을 때 Dagster 자체가 기동하지 못하는 **순환 의존**이 된다.

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
