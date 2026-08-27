# 모니터링 · 관측 (아키텍처 · 프로젝트 관점)

## 개요

**Prometheus**는 **pull(스크레이프) 모델**의 시계열 수집기다. 서버가 설정된 타깃의
`/metrics` 엔드포인트를 주기적으로 긁어 라벨 붙은 시계열로 저장하고, PromQL로 질의한다.
서비스가 직접 메트릭을 내지 못하면 **exporter**(노드·DB·큐 등 전용 어댑터)를 앞에 두어
`/metrics`를 대신 노출시킨다. 임계 조건은 **rule_files**의 알림 규칙으로 평가하고,
발화된 알림의 묶음·중복 제거·라우팅은 별도 컴포넌트인 **Alertmanager**가 맡는다.

즉 관측이 성립하려면 **① 타깃(무엇을 볼지) ② 수집기(긁는 주체) ③ 규칙·라우팅(무엇을 알릴지)**
셋이 이어져야 하고, 하나만 있어도 나머지가 없으면 데이터는 생기지 않는다.

## 이 프로젝트에서의 위치 — 🔎 미채택 (선언 잔존 · 수집 대상이 정본과 갈림)

**상태 마커 근거**: `compose.yml`에 Prometheus **정의가 있다**. 그래서 ✅(채택)로 읽히기 쉽다.
그리고 ⚠️ **`--profile monitoring`으로 띄우면 실제로 타깃 2개가 다 산다** — `seaweedfs`에
`monitoring` profile이 함께 걸려 있어(`compose.yml:162-165`, 바로 위 `:159-161` 주석이 그 이유를
"prometheus가 `seaweedfs:9324` 메트릭을 수집"으로 적어 뒀다) 그 컨테이너가 같이 뜨고
`-metricsPort=9324`(`:173`)로 응답한다. **수집기가 죽어 있는 상태가 아니다.**

**단절은 수집기가 아니라 대상에 있다.** 오브젝트 스토리지 **정본은 2026-08-19에 K8s로 이전**됐고
compose 쪽은 `legacy-storage` profile로 **상시 기동만 끊긴 레거시**다. 그런데 정본인
`k8s/seaweedfs.yaml`에는 메트릭 포트가 없다. 즉 수집기는 **살아 있는 채로 정본이 아닌 대상을 보고 있다.**
이것이 더 나쁜 종류의 고장이다 — **켜면 초록불이 뜨기 때문에 검산을 통과하며 남는다.**
"수집이 되고 있다"는 관측은 참이지만 그 문장이 세고 있는 대상이 이미 정본이 아니다
([../conventions/monitoring.md](../conventions/monitoring.md) §3 — 경로 생존과 판별력은 다른 축이다).

정의가 있으니 🔎(미도입)이라고만 하기도 어렵고, 이행 작업이 진행 중이지 않으니 🚧도 아니다.
그래서 **🔎 미채택 + "선언은 남아 있고 수집 대상이 정본과 갈렸다"** 로 표기한다.
이것은 [../conventions/monitoring.md](../conventions/monitoring.md) §2가 막으려는 것 —
**수집기가 만들어내는 거짓 신호** — 의 실제 사례이되, 형태가 한 단계 고약하다.
§2의 전형은 *타깃이 없는* 수집기이고 여기는 **타깃이 있는데 그 타깃이 정본이 아닌** 경우다.
전자는 켜 보면 비어 있어 들키지만, 후자는 **켜면 채워진다.**

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
| **Loki**(2026-08-27 평가) | 로그 집계·LogQL 질의 | §2를 통과하는 유일한 후보이나 **monolithic이 가볍지 않다** — 아래 §Loki |
| **Robusta**(2026-08-27 평가) | 알림 보강·자동 조사·K8s 이벤트 | 자원이 아니라 **데이터 거버넌스**에서 먼저 걸린다 — 아래 §Robusta |

#### Loki — §2는 통과하지만 «monolithic = 가볍다»가 아니다

먹일 로그가 이미 흐르므로 §2를 통과하는 **유일한 후보**다. 그럼에도 미채택인 이유는 규모다 —
공식 문서 기준 **단일 replica가 파드 5종**을 띄운다(본체 · Canary DaemonSet · Gateway ·
Chunks cache · Results cache). 끄는 옵션은 그 문서에 없다.
그리고 차트가 `resources`를 **전부 빈 오브젝트**로 두어 예산을 직접 다 선언해야 한다.
기본 스토리지가 `s3`라 기존 SeaweedFS와는 맞물린다. 수집 에이전트는
**Promtail이 2026-03-02 EOL**이라 Alloy를 쓴다.

#### Robusta — 걸리는 순서가 자원보다 앞이다

`rule_files`가 0건이라 **보강할 알림 자체가 없어** §2에 걸린다. 그런데 그보다 앞에
**데이터 반출 축**이 있다(아래 §운영 메모). 자원만 보면 오히려 준비가 나은 편이다 —
차트가 컴포넌트별 `resources.requests`를 채워 두어 BestEffort 함정이 없다.

### 후보를 세우는 축은 자원이 아니라 «먹일 것이 이미 있는가»

2026-08-27 비교에서 순서가 **자원 비용으로 갈리지 않았다.** [../conventions/monitoring.md](../conventions/monitoring.md)
§2가 금지하는 것은 *타깃 없는 수집기*이므로, 첫 질문은 "얼마나 드는가"가 아니라
**"그 수집기가 먹을 것이 지금 흐르고 있는가"** 다.

| | Loki | Prometheus(현행) | Robusta |
| --- | --- | --- | --- |
| 무엇을 먹나 | 로그 | 메트릭 | Prometheus **알림** |
| 그 먹이가 지금 있나 | ✅ 있다 | ❌ 정본 워크로드 0개가 낸다 | ❌ `rule_files` 0건 |
| 붙이면 즉시 보이는 것 | Flink 오퍼레이터 메트릭(현재 로그에만 있어 질의 불가)·Dagster 스텝 로그 | 없음 | 없음 |
| 자원 선언 | ❌ 전부 `{}` | 선언됨 | ✅ 채워져 있음 |

⇒ **권고 순서는 ① Prometheus 정리 ② Loki ③ Robusta 보류**다.
①이 먼저인 이유는 그것이 *도구 추가*가 아니라 **거짓 신호 제거**여서다(위 §이 프로젝트에서의 위치).

⚠️ **자원 선언 유무는 비용의 부속이 아니라 판정의 선결 조건이다.** 차트가 `resources`를 비워 두면
파드가 **BestEffort로 떠 `Σrequests` 합계에 0으로 잡히고**, 그러면 어떤 스택을 올려도
"예산 안에 들어왔다"는 답이 나온다 — **통과가 통과가 아니게 된다**
([../resource-sizing.md](../resource-sizing.md) §BestEffort). 그래서 **비용을 재기 전에 선언 유무를 먼저** 본다.

**미확인으로 남은 것**(추측으로 채우지 않는다): Loki 캐시 2종의 `resources` 원문 ·
Robusta의 기존 Prometheus 연동 모드 파드 차분 · `grafana/loki`와 `grafana-community/helm-charts`의
차트 정본 관계. 인용 수치는 **`grafana/loki` 기준**이며 그 경로는 이관 발표 5개월 뒤에도
커밋을 받고 있어 동결이 아님을 확인했다(2026-08-19).

## 운영 메모

- 🔴 **수집 대상과 정본이 갈린 것이 이 문서의 핵심 사실이다.** SeaweedFS 메트릭
  (`-metricsPort=9324`)은 compose 정의에 **그대로 살아 있다** — 사라진 것은 메트릭이 아니라
  **정본의 자리**다. 오브젝트 스토리지 정본이 K8s로 이전됐는데(2026-08-19)
  `k8s/seaweedfs.yaml`에는 해당 인자도 포트도 **만들어지지 않았다**. 그래서 수집기는 계속 응답을
  받지만 그 응답은 레거시 쪽에서 온다. **compose와 K8s의 관측 수준이 갈린 지점**이다.
  **이 상태는 가설이 아니라 이 저장소에서 한 번 실현된 실패 양식이고, 방향만 거울상이다.**
  2026-08-18에 "원천 데이터가 어디에도 없다"고 판정한 적이 있고 **다음 날 오진으로 정정**됐다
  ([../redesign.md](../redesign.md) Phase 2 정정 · [../philosophy.md](../philosophy.md)
  §*#7의 근거 — 실패가 실패로 보이지 않는다*의 사례표).
  그때는 **사람이 K8s만 조회하고 compose를 놓쳤다** — compose 컨테이너가 `Exited`라 S3 API가 죽어
  있었고 그 조회 실패가 "버킷 0개"로 읽혔다 → **있는 것을 없다고** 판정.
  지금은 **수집기가 compose를 보고 K8s를 놓친다** → **안 보는 것을 본다고** 판정.
  **원인은 같다 — SeaweedFS가 compose와 K8s에 이중으로 존재한다.** 방향만 뒤집혔다.
  ⚠️ 그리고 갈리는 것이 하나 더 있다. **발견 경로**다. 2026-08-18은 사람이 그 자리에서 한 번
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
- **Dagster 쪽 관측은 실행 기록에 의존한다** — `run_monitoring` 미설정이라 워커가 죽은 런의
  자동 판정이 없고, `compute_logs`가 기본값이라 스텝 로그는 컨테이너 로컬에 남는다.
  자산 단위 관측은 머티리얼라이즈 메타데이터가 담당한다([../conventions/dagster.md](../conventions/dagster.md)).
- 🔴 **관측 도구가 데이터 반출 경로가 될 수 있다.** Robusta 평가에서 드러난 축이다 —
  OSS는 CLI/API까지이고 웹 UI·봇·자동 triage는 **Platform(SaaS 또는 Self-Hosted) 전용**인데,
  **어떤 데이터가 나가는지 공식 문서에 명시가 없다**(2회 재확인으로 부재 확인. 있는 것은
  *"SOC 2 compliant · US/EU/APAC"* 뿐이며 **컴플라이언스·리전 정보이지 전송 범위가 아니다**).
  **"명시 없음"을 "안 나간다"로 읽지 않는다.** 이 저장소는 DUA가 걸린 임상 데이터를 다루고,
  그런 도구의 핵심 기능은 **파드 로그를 읽어 조사**하는 것이라 로그에 쿼리문·테이블명·행 수가
  섞이는 경로가 실재한다. ⇒ 관측 도구 도입 검토의 선결 조건은 자원 산정이 아니라
  **`security` 판정과 벤더 확인**이다([../security.md](../security.md)).
- 관측 수단을 더하거나 뺄 때의 **규칙**(등록 의무·수집기 정리·생존 확인·수치 기재)은
  [../conventions/monitoring.md](../conventions/monitoring.md)가 정본이다.
- ⚠️ **이 문서의 무게는 실행 환경에 걸려 있다.** 위 공백들이 지금 수용 가능한 것은 현행 검증 환경이
  **로컬 단독**이기 때문이다 — kind는 `listenAddress: "127.0.0.1"`(`k8s/kind-cluster.yaml`)이라
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
- Grafana Loki — Promtail EOL 공지(2026-03-02 · Alloy 대체): https://grafana.com/docs/loki/latest/send-data/promtail/
- Robusta — Open Source vs SaaS(기능 경계): https://docs.robusta.dev/master/how-it-works/oss-vs-saas.html
- Robusta — Helm `values.yaml`(컴포넌트별 `resources`): https://github.com/robusta-dev/robusta/blob/master/helm/robusta/values.yaml
- 관측 **규칙** 정본: [../conventions/monitoring.md](../conventions/monitoring.md)
