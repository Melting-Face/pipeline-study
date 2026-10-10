# 모니터링 · 관측 (아키텍처 · 프로젝트 관점)

## 개요

**Prometheus**는 **pull(스크레이프) 모델**의 시계열 수집기다. 서버가 설정된 타깃의
`/metrics` 엔드포인트를 주기적으로 긁어 라벨 붙은 시계열로 저장하고, PromQL로 질의한다.
서비스가 직접 메트릭을 내지 못하면 **exporter**(노드·DB·큐 등 전용 어댑터)를 앞에 두어
`/metrics`를 대신 노출시킨다. 임계 조건은 **rule_files**의 알림 규칙으로 평가하고,
발화된 알림의 묶음·중복 제거·라우팅은 별도 컴포넌트인 **Alertmanager**가 맡는다.

즉 관측이 성립하려면 **① 타깃(무엇을 볼지) ② 수집기(긁는 주체) ③ 규칙·라우팅(무엇을 알릴지)**
셋이 이어져야 하고, 하나만 있어도 나머지가 없으면 데이터는 생기지 않는다.

## 이 프로젝트에서의 위치 — 🔎 미채택 (선언 잔존 · 규칙 0건)

**상태 마커 근거**: `compose.yml`에 Prometheus **정의가 있다**. 그래서 ✅(채택)로 읽히기 쉽다.
`--profile monitoring`으로 띄우면 `seaweedfs`가 함께 떠(의존 profile 상속) `-metricsPort=9324`로 응답한다.
**수집기가 죽어 있는 상태가 아니다.**

**수집 대상과 정본은 다시 일치한다.** 한동안 오브젝트 스토리지 정본이 K8s StatefulSet이었고 그쪽엔
메트릭 포트가 없어, 수집기가 **살아 있는 채로 정본이 아닌 compose 사본을 보던** 시기가 있었다(아래 §운영 메모).
정본이 클러스터 밖 compose `seaweedfs`로 돌아와 그 갈림은 해소됐다 — 다만 **해소된 것은 대상 정합 하나**다.
알림 규칙(`rule_files`)은 0건이고 상시 기동도 아니어서, 켜도 **아무것도 알리지 않는다**.
그래서 표기는 **🔎 미채택 + "선언은 남아 있고 규칙이 없다"** 다
([../conventions/monitoring.md](../conventions/monitoring.md) §2 — 수집기가 만들어내는 거짓 신호).

### 현행 사실

📌 **선언 vs 실제 대조표와 그 수치는 저장소 밖에 있다** —
`$OBSIDIAN_VAULT/status/observations.md` §관측·모니터링 실태.
관측 시각·모집단·계측 도구·대조군이 그쪽에 병기돼 있다.

여기 두지 않는 이유는 **그 표가 가장 빨리 낡는 종류**이기 때문이다. healthcheck 하나를 붙이거나
probe 하나를 지우면 값이 바뀌는데, 규칙 문서에 박아 두면 아무도 손대지 않아도 거짓이 된다.

### 대안 비교 — 왜 지금 쓰지 않는가

⚠️ 아래는 **현재 미채택인 이유**를 적은 것이지 도입 계획이 아니다. 상시 컴포넌트를 하나 올리는 것은
컴퓨트 예산을 직접 깎는 선택이고, 예산의 단위·배분은 [../resource-sizing.md](../resource-sizing.md)가
정본이다(수치는 여기 옮기지 않는다).

| 후보 | 무엇을 주나 | 현재 안 쓰는 이유 |
| --- | --- | --- |
| **Grafana** | 대시보드·시각화 | 🔴 **볼 만한 데이터가 없다** — 비어서가 아니라 **채워지는 것이 이 스택의 실제 상태가 아니어서**다. 이 상태로 붙이면 초록 화면이 관측을 대신한다 |
| **kube-prometheus-stack** | Operator·Prometheus·Alertmanager·Grafana 일괄 | 상주 컴포넌트가 한 번에 여럿 붙어 **가장 비싼 선택**이고 회수 규율과 충돌한다 |
| **metrics-server** | `kubectl top` 수준의 사용량 | 🔴 **kind에 없다** — 그래서 자원 실측이 `/proc`·cgroup 병행으로 굳어 있다 |
| **Alertmanager** | 알림 묶음·중복 제거·라우팅 | **알릴 규칙이 없다**(`rule_files` 0건). 발화원 없이 라우터만 두면 §2의 거짓 신호가 하나 더 늘어난다. 단일 사용자 학습 환경이라 수신 채널·당직 개념도 없다 |
| **exporter 계열**(node·postgres 등) | 서비스별 `/metrics` | 수집기는 살아 있으나 **정본 워크로드를 스크레이프하도록 배선돼 있지 않다**. 그 상태에서 exporter부터 붙이면 **내보내는 쪽만 늘고 읽는 쪽이 없다**(순서가 거꾸로다) |
| **Loki** | 로그 집계·LogQL 질의 | §2를 통과하는 유일한 후보이나 **monolithic이 가볍지 않다** — 아래 §Loki |
| **Robusta** | 알림 보강·자동 조사·K8s 이벤트 | 자원이 아니라 **데이터 거버넌스**에서 먼저 걸린다 — 아래 §Robusta |

#### Loki — §2는 통과하지만 «monolithic = 가볍다»가 아니다

먹일 로그가 이미 흐르므로 §2를 통과하는 **유일한 후보**다. 그럼에도 미채택인 이유는 규모다 —
공식 문서 기준 **단일 replica가 파드 5종**을 띄운다(본체 · Canary DaemonSet · Gateway ·
Chunks cache · Results cache). 끄는 옵션은 그 문서에 없다.
그리고 차트가 `resources`를 **전부 빈 오브젝트**로 두어 예산을 직접 다 선언해야 한다.
기본 스토리지가 `s3`라 기존 SeaweedFS와는 맞물린다. 수집 에이전트는
**Promtail이 EOL**이라 Alloy를 쓴다(공지는 §참고).

#### Robusta — 걸리는 순서가 자원보다 앞이다

`rule_files`가 0건이라 **보강할 알림 자체가 없어** §2에 걸린다. 그런데 그보다 앞에
**데이터 반출 축**이 있다(아래 §운영 메모). 자원만 보면 오히려 준비가 나은 편이다 —
차트가 컴포넌트별 `resources.requests`를 채워 두어 BestEffort 함정이 없다.

### 후보를 세우는 축은 자원이 아니라 «먹일 것이 이미 있는가»

비교에서 순서가 **자원 비용으로 갈리지 않았다.** [../conventions/monitoring.md](../conventions/monitoring.md)
§2가 금지하는 것은 *타깃 없는 수집기*이므로, 첫 질문은 "얼마나 드는가"가 아니라
**"그 수집기가 먹을 것이 지금 흐르고 있는가"** 다.

| | Loki | Prometheus(현행) | Robusta |
| --- | --- | --- | --- |
| 무엇을 먹나 | 로그 | 메트릭 | Prometheus **알림** |
| 그 먹이가 지금 있나 | ✅ 있다 | △ 정본 SeaweedFS 1개만 낸다 | ❌ `rule_files` 0건 |
| 붙이면 즉시 보이는 것 | Flink 오퍼레이터 메트릭(현재 로그에만 있어 질의 불가)·Dagster 스텝 로그 | 없음 | 없음 |
| 자원 선언 | ❌ 전부 `{}` | 선언됨 | ✅ 채워져 있음 |

⇒ **권고 순서는 ① Prometheus 정리 ② Loki ③ Robusta 보류**다.
①이 먼저인 이유는 그것이 *도구 추가*가 아니라 **규칙 없는 수집기 정리**여서다(위 §이 프로젝트에서의 위치).

⚠️ **자원 선언 유무는 비용의 부속이 아니라 판정의 선결 조건이다.** 차트가 `resources`를 비워 두면
파드가 **BestEffort로 떠 `Σrequests` 합계에 0으로 잡히고**, 그러면 어떤 스택을 올려도
"예산 안에 들어왔다"는 답이 나온다 — **통과가 통과가 아니게 된다**
([../resource-sizing.md](../resource-sizing.md) §BestEffort). 그래서 **비용을 재기 전에 선언 유무를 먼저** 본다.

**미확인으로 남은 것**(추측으로 채우지 않는다): Loki 캐시 2종의 `resources` 원문 ·
Robusta의 기존 Prometheus 연동 모드 파드 차분 · `grafana/loki`와 `grafana-community/helm-charts`의
차트 정본 관계. 인용 수치는 **`grafana/loki` 기준**이며 그 경로는 이관 발표 **이후로도**
커밋을 받고 있어 동결이 아님을 확인했다.

## 운영 메모

- 🔴 **수집 대상과 정본이 갈렸던 기록**(정본이 compose로 돌아와 지금은 해소). SeaweedFS 메트릭
  (`-metricsPort=9324`)은 compose 정의에만 있었고, 정본이 K8s StatefulSet이던 동안 그쪽엔 인자도 포트도
  없었다. 수집기는 응답을 받았지만 그 응답은 레거시 쪽에서 왔다.
  **이것은 이 저장소에서 두 번, 방향만 바꿔 실현된 실패 양식이다.**
  한때 "원천 데이터가 어디에도 없다"고 판정한 적이 있고 **다음 날 오진으로 정정**됐다
  ([../redesign.md](../redesign.md) Phase 2 정정 · [../philosophy.md](../philosophy.md)
  §*#7의 근거 — 실패가 실패로 보이지 않는다*의 사례표).
  그때는 **사람이 K8s만 조회하고 compose를 놓쳤다** → **있는 것을 없다고** 판정.
  그 뒤엔 **수집기가 compose를 보고 K8s를 놓쳤다** → **안 보는 것을 본다고** 판정.
  **원인은 같다 — 같은 서비스가 두 환경에 이중으로 존재했다.** 그래서 정본을 되돌리며 K8s 사본
  (`k8s/seaweedfs.yaml`)은 **정의째 철거**했다 — 남겨 두면 같은 이름으로 답하는 레거시가 다시 생긴다.
  📌 **같은 축이 하나 더 생겼다 — Dagster다.** in-cluster로 옮기면서 compose의
  `dagster-webserver`·`dagster-daemon`·`postgres`를 **`profiles`로 내려** 기본 `up`에서 뺐다.
  정의를 남긴 것은 롤백을 위해서지만, 그것이 곧 **동시에 띄울 수 있다는 뜻**이다.
  판정 시 확인할 것은 셋 — 호스트 3000 리스너 부재 · compose 컨테이너 부재 ·
  Ingress `/server_info`가 **버전 JSON**을 돌려주는 것(200만으로는 판별이 안 된다).
  ⚠️ 그리고 갈리는 것이 하나 더 있다. **발견 경로**다. 그때는 사람이 그 자리에서 한 번
  데었기 때문에 드러났지만, 지금 그 자리에서 답하는 것은 **초록불을 띄우는 수집기**다.
  ⚠️ 이것을 *결정*으로 적어 둔 문장은 찾지 못했다.
  ⚠️ **그러나 "기록이 없다"는 "결정이 없었다"가 아니다** — 검색 결과를 의도의 부재로 읽지 않는다.
  검색 모집단과 hit 수는 `$OBSIDIAN_VAULT/status/observations.md` §기록의 부재 vs 결정의 부재.
- 🔴 **버전 고정과 방치는 겉모습이 같다.** 태그가 박혀 있으면 규칙에는 맞지만
  ([../conventions/docker.md](../conventions/docker.md) §1-3), 그것이 *관리되는 고정*인지
  *한 번 적고 잊은 것*인지는 태그만 봐서 알 수 없다.
  ⇒ 판별 기준은 **갱신 이력**이다. 메이저 계열이 통째로 뒤처져 있으면 방치로 읽는다.
  현재 격차는 `$OBSIDIAN_VAULT/status/observations.md` §Prometheus 버전 격차.
- **Prometheus 자신에 healthcheck가 없다.** 수집기가 죽어도 compose는 정상으로 보고한다 —
  "메트릭이 0"과 "수집기가 죽었다"를 구분할 수단이 그 서비스 자체에 없다
  ([../conventions/monitoring.md](../conventions/monitoring.md) §3).
- **kind에는 metrics-server가 없다.** 클러스터 자원 관측은 `kubectl top`이 아니라
  파드 내부 `/proc`·cgroup 판독으로 하고 있으며, 실측 절차와 수치는
  [../resource-sizing.md](../resource-sizing.md)가 정본이다.
- **Flink 오퍼레이터 메트릭은 로그로만 나간다**(slf4j 리포터, 5분 간격). 값은 남지만
  시계열로 질의할 수 없고, 로그 보존 정책([../operations.md](../operations.md) §2)의 수명을 따른다.
- **Dagster 쪽 관측은 실행 기록에 의존한다** — `run_monitoring` 부재의 이유가
  "안 켰다"에서 **"`DefaultRunLauncher`가 지원하지 않는다"** 로 확정됐다(켜면 `NotImplementedError`).
  ⇒ **부재가 결정**이고, 대가는 daemon 재시작 시 진행 중 run이 `STARTED`로 고아가 되는 것이다.
  `compute_logs`는 기본값(Local)에서 **`S3ComputeLogManager`로 바뀌었다** — webserver와 daemon이
  다른 파드라 로컬 디스크로는 UI에 스텝 로그가 영영 안 보였다.
  ⚠️ **그 업로드 실패는 run 상태에 나타나지 않는다** — Dagster가 예외를 삼키므로 `RUN_SUCCESS`인 채
  로그만 사라진다(실발생). 관측 경로 확인은 **버킷을 직접 보는 것**이다.
  자산 단위 관측은 머티리얼라이즈 메타데이터가 담당한다([../conventions/dagster.md](../conventions/dagster.md)).
- 🔴 **관측 도구가 데이터 반출 경로가 될 수 있다.** Robusta 평가에서 드러난 축이다 —
  OSS는 CLI/API까지이고 웹 UI·봇·자동 triage는 **Platform(SaaS 또는 Self-Hosted) 전용**인데,
  **어떤 데이터가 나가는지 공식 문서에 명시가 없다**(2회 재확인으로 부재 확인. 있는 것은
  *"SOC 2 compliant · US/EU/APAC"* 뿐이며 **컴플라이언스·리전 정보이지 전송 범위가 아니다**).
  **"명시 없음"을 "안 나간다"로 읽지 않는다.** 이 저장소는 DUA가 걸린 임상 데이터를 다루고,
  그런 도구의 핵심 기능은 **파드 로그를 읽어 조사**하는 것이라 로그에 쿼리문·테이블명·행 수가
  섞이는 경로가 실재한다. ⇒ 관측 도구 도입 검토의 선결 조건은 자원 산정이 아니라
  **`reviewer` 보안 점검(체크리스트 E)과 벤더 확인**이다([../security.md](../security.md)).
- 관측 수단을 더하거나 뺄 때의 **규칙**(등록 의무·수집기 정리·생존 확인·수치 기재)은
  [../conventions/monitoring.md](../conventions/monitoring.md)가 정본이다.
- ⚠️ **이 문서의 무게는 실행 환경에 걸려 있다.** 위 공백들이 지금 수용 가능한 것은 현행 검증 환경이
  **로컬 단독**이기 때문이다 — kind는 `listenAddress: "127.0.0.1"`(스택 A `terraform/cluster/kind`)이라
  LAN에서 도달할 수 없고, [oci.md](oci.md)의 OCI 스택은 **⏸ 보류**로 컴퓨트가 서 있지 않다.
  **OCI를 재개해 인터넷에 면한 노드가 생기면 이 판단이 그대로 살아나지 않는다** — 같은 공백이
  **탐지 공백**으로 성격이 바뀌고, 위 표는 그 노드에서 무엇을 못 보는지의 목록이 된다.
  [../security.md](../security.md) 2.6·2.11과 함께 다시 읽어야 하는 지점이다.

## 참고

- Prometheus — Overview: https://prometheus.io/docs/introduction/overview/
- Prometheus — Configuration(`scrape_configs`·`rule_files`): https://prometheus.io/docs/prometheus/latest/configuration/configuration/
- Prometheus — 릴리스 이력(버전·릴리스일 1차 출처): https://github.com/prometheus/prometheus/releases
- Prometheus Operator(ServiceMonitor·PodMonitor 제공 주체): https://prometheus-operator.dev/
- Kubernetes SIGs — metrics-server: https://github.com/kubernetes-sigs/metrics-server
- Apache Flink Kubernetes Operator(메트릭 리포터 설정): https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-main/
- Dagster 문서(`run_monitoring`·`compute_logs` 설정): https://docs.dagster.io/
- Grafana Loki — 배포 모드(monolithic 권장 규모·SSD 제거 예정): https://grafana.com/docs/loki/latest/get-started/deployment-modes/
- Grafana Loki — monolithic Helm 설치(단일 replica가 띄우는 컴포넌트 목록): https://grafana.com/docs/loki/latest/setup/install/helm/install-monolithic/
- Grafana Loki — Helm `values.yaml`(`resources` 기본값 원문): https://github.com/grafana/loki/blob/main/production/helm/loki/values.yaml
- Grafana Loki — Promtail EOL 공지(2026-03-02 <!-- date-ok --> · Alloy 대체): https://grafana.com/docs/loki/latest/send-data/promtail/
- Robusta — Open Source vs SaaS(기능 경계): https://docs.robusta.dev/master/how-it-works/oss-vs-saas.html
- Robusta — Helm `values.yaml`(컴포넌트별 `resources`): https://github.com/robusta-dev/robusta/blob/master/helm/robusta/values.yaml
- 관측 **규칙** 정본: [../conventions/monitoring.md](../conventions/monitoring.md)
