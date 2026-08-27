# Kubernetes (아키텍처 · 프로젝트 관점)

## 개요

Kubernetes(K8s)는 **컨테이너 오케스트레이션 플랫폼**이다. 다중 노드 클러스터에서 파드(pod)를
스케줄링하고, 선언적 desired-state로 **자가치유·오토스케일·롤링 업데이트·서비스 디스커버리**를
제공한다. control plane(API server·scheduler·controller·etcd)과 worker(kubelet)로 구성된다.

- 최신 안정: **v1.36**(2026-06). N-2 지원(최근 3개 마이너에 유지보수 제공).

## 이 프로젝트에서의 위치 — 🚧 채택·이행중(PoC 게이트)

- **채택 방향**: 확장성/성능 한계 극복 + 학습·포트폴리오를 위해 **컴퓨트·데이터 서비스를 K8s로 이전**한다.
  **Dagster를 포함해** 전 스택이 클러스터 안에 있다(2026-08-27 — 구 판본은 호스트에 남겼다)
  (오케스트레이터↔컴퓨트 분리).
  전면 이행은 **PoC 성공을 전제**로 단계적으로 진행한다. 전체 로드맵은 [../redesign.md](../redesign.md).
- **로컬 배포판**: **kind on Podman(rootful)** + 로컬 레지스트리. in-cluster Dagster는 ServiceAccount로,
  호스트 실행분은 kubeconfig로 클러스터 API에 접근한다(인증 분기는 코드가 갖는다).
- **핵심 컴포넌트**: **Spark Operator**(배치)·**Flink Operator**(스트림)로
  `SparkApplication`·`FlinkDeployment`(CRD)를 실행하고,
  **CloudNativePG**(카탈로그 Postgres)로 `Cluster`(CRD) 관리,
  Redpanda·SeaweedFS·카탈로그 Postgres를 K8s에 배포한다(**Trino 제거**). Iceberg 테이블은 Spark·Flink가 공유한다.
  **웹 UI 진입점은 ingress-nginx**로 고정 URL화한다(`*.localtest.me:8080`).
- **구축 현황(2026-08-19 실측)**: 클러스터 k8s **v1.36.1** 단일 노드.
  **Spark Operator 1.0.0**(chart 1.8.0) / **Flink Operator 1.15.0**(+cert-manager) 기동,
  **Spark Connect 서버**(dbt 접속용) 상주, SeaweedFS·카탈로그 Postgres 운영 중
  카탈로그 Postgres는 **CloudNativePG 1.30.0**(chart 0.29.0)이 관리하는 `Cluster`로 **교체 완료**
  (operand `postgresql:18.6-standard-trixie`, PVC 5Gi, `catalog-postgres-rw` 접속).
  Spark 잡이 새 카탈로그에 `iceberg.poc.sample`을 등록하는 것까지 확인(2026-08-19).
  **ingress-nginx v1.15.1**(kind provider)로 Spark·Flink UI를 `port-forward` 없이 노출.
  Dagster 자산이 `SparkApplication`을 제출해 Iceberg에 적재하고(Phase 0 게이트 통과),
  **Flink이 같은 Iceberg 카탈로그를 조회**하는 것까지 확인.
- **노출 경로의 분리**: **HTTP(UI·REST)와 gRPC는 Ingress**, **JDBC·S3는 `port-forward`**.
  가르는 기준은 프로토콜 계층이다 — nginx Ingress가 실어 나를 수 있는 것은 HTTP 계열(gRPC 포함,
  단 **TLS 위 HTTP/2 전제**)이고, JDBC·S3 바이너리 경로는 그 대상이 아니다.
  kind는 **공개 포트를 클러스터 생성 시점에만** 정할 수 있어 `extraPortMappings`가 전제다
  (규칙·함정은 [../conventions/k8s.md](../conventions/k8s.md) §10).
- **이행 기준(언제 K8s로)**: 다중 노드 스케일아웃, 무중단 배포, 오토스케일(HPA), 팀 다중 환경, SLA 요구.
- **compose → Kubernetes 매핑**:

  | compose | Kubernetes |
  | --- | --- |
  | service | `Deployment`(+`Service`) / `StatefulSet`(seaweedfs) / **오퍼레이터 CR**(카탈로그 postgres = CNPG `Cluster`) |
  | `deploy.resources` | `resources.requests`·`resources.limits` |
  | healthcheck | `livenessProbe`·`readinessProbe`·`startupProbe` |
  | `depends_on` | initContainers / readiness gating |
  | profiles(옵션) | 오버레이(Kustomize)·values(Helm)로 토글 |
  | `${ENV}`·`.env` | `ConfigMap`·`Secret` 참조 |
  | volume(`:ro`) | `PersistentVolumeClaim` / configMap·secret 볼륨(readOnly) |
  | `ports:`(호스트 퍼블리시) | `Service` + **`Ingress`**(HTTP UI·REST·gRPC/TLS) / **`port-forward`**(JDBC·S3) |

- 배포·보안 **규칙**은 [conventions/k8s.md](../conventions/k8s.md).

## 운영 메모 (이행)

- 패키징은 **Helm 차트**(값 분리·환경별 오버라이드). 이미지 태그 고정(`latest` 금지).
- 상태 저장(SeaweedFS)은 `StatefulSet`+PVC.
- **카탈로그 Postgres는 CloudNativePG(CNPG) 오퍼레이터**가 관리한다(`postgresql.cnpg.io/v1` `Cluster`).
  직접 `StatefulSet`을 쓰지 않는 이유: PVC·failover·백업(PITR)·파라미터 튜닝이 **CR 한 장**에 들어오고,
  Spark·Flink 오퍼레이터와 **같은 선언형 패러다임**으로 통일된다. 서비스는 오퍼레이터가 만드는
  `catalog-postgres-rw`/`-ro`/`-r`이다(접미사 없는 이름은 생기지 않는다).
  **메타 Postgres는 compose(호스트)에 남긴다** — Dagster가 호스트라 순환 의존을 피한다.
- **Spark 실행**: Apache 공식 **Spark Kubernetes Operator**를 Helm으로 설치하고(`ns=spark-operator`),
  Dagster 자산이 `PipesK8sClient`로 `SparkApplication`(CRD)을 제출·폴링한다.
  규칙은 [../conventions/k8s.md](../conventions/k8s.md) §9~11.
- **Dagster 위치**(2026-08-27 개정): Dagster도 **클러스터 안**이다 — 결정과 대안 비교는
  [dagster.md](dagster.md). 아래 문단은 폐기 전 판본의 근거이며 `K8sRunLauncher` 미채택은 여전히 유효하다.
- **구 근거(참고)**: 본 프로젝트는 Dagster를 **호스트에 유지**했다. `dagster-k8s`의 `K8sRunLauncher`는
  Dagster를 **클러스터 내부에 배포**할 때 run을 파드로 실행하는 옵션으로, 본 토폴로지의 Spark 트리거 수단이
  아니다(후속 비교 과제, [../redesign.md](../redesign.md) Phase 4).

## 참고

- Kubernetes 문서: https://kubernetes.io/docs/home/
- 릴리스: https://kubernetes.io/releases/
- dagster-k8s: https://docs.dagster.io/deployment/oss/deployment-options/kubernetes
- Apache Spark Kubernetes Operator: https://apache.github.io/spark-kubernetes-operator/
- Apache Flink Kubernetes Operator: https://nightlies.apache.org/flink/flink-kubernetes-operator-docs-main/
- CloudNativePG(PostgreSQL 오퍼레이터, CNCF): https://cloudnative-pg.io/ · 릴리스: https://cloudnative-pg.io/releases/
- CloudNativePG Barman Cloud 플러그인(백업·PITR): https://cloudnative-pg.io/plugin-barman-cloud/docs/intro/
- CloudNativePG operand 이미지 태그: https://github.com/cloudnative-pg/postgres-containers
- kind(로컬 K8s, Podman provider): https://kind.sigs.k8s.io/
