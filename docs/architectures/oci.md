# OCI + Terraform + k3s (아키텍처 · 프로젝트 관점)

## 개요

**OCI(Oracle Cloud Infrastructure) Always Free** 등급의 **Ampere A1(ARM64)** 컴퓨트에 **k3s**
(경량 CDN 인증 Kubernetes 배포판)를 **Terraform + cloud-init**으로 올려, 로컬 kind 재설계를
**클라우드로 이행**하는 경로다. Dagster는 여전히 **호스트(컨트롤 플레인)**, k3s는 **원격 컴퓨트**다
([redesign.md](../redesign.md)의 토폴로지 유지).

- **k3s**: 단일 바이너리 K8s. etcd 대신 기본 SQLite, containerd 내장, 인증받은 배포판(CNCF conformant).
  엣지·소규모·홈랩·CI에 적합.
- **Always Free A1**: 테넌시당 **월 1,500 OCPU시간 + 9,000 GB시간**(≈ **2 OCPU/12 GB** 상시), 블록스토리지 200 GB
  무료. ARM64이므로 컨테이너 이미지는 **arm64 빌드**가 필요하다.
  > **한도 축소**(2026-06-15 · 초과분 정리 기한 2026-08-18): <!-- date-ok -->
  > 4 OCPU/24 GB → **2 OCPU/12 GB**(절반). 기한까지 축소하지 않으면
  > 종료된다고 Oracle이 통보했다. 초과 설정은 **과금**되므로 `variables.tf`에 `validation`으로 상한을 걸어뒀다.
  > 출처: [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) ·
  > [Oracle Cloud Customer Connect 공지](https://community.oracle.com/customerconnect/discussion/970310/oci-always-free-updated-ampere-a1-compute-allocation)

## 이 프로젝트에서의 위치 — 🔎 학습·확장 경로(로컬 이후)

- **채택 방향**: 로컬 **kind on Podman**(자원은 [`scripts/k8s-env.sh`](../../scripts/k8s-env.sh)가 **정본**)으로 검증한
  K8s 재설계를, **비용 0**의 상시 클러스터(**2 OCPU/12 GB**)에서 재현한다. 포트폴리오상
  "IaC(Terraform)로 클라우드 K8s 프로비저닝" 경험을 더한다.
  > 한도 축소 이후 **클라우드 쪽이 로컬보다 작다.** "더 큰 클러스터"가 아니라
  > **상시 가동·IaC 경험**이 채택 이유로 남는다.
  > ⚠️ **로컬 상한은 그동안 올랐으므로 이 격차는 좁혀진 것이 아니라 벌어졌다** —
  > 결론(클라우드가 로컬보다 작다)은 그대로이고 근거만 강해졌다.
  > 🔴 **로컬 수치를 여기 적지 않는다.** 이전 판본이 값을 박아 두는 바람에
  > `k8s-env.sh`가 상향된 뒤 **「정본은 저 파일」이라고 적으면서 정본과 다른 값을 말하는** 상태가 됐다.
  > **비교는 정본을 열어 그때 값으로 한다.**
- **범위(현재)**: VCN·보안·A1 인스턴스·k3s 부트스트랩·kubeconfig 회수까지. 데이터스택(Spark Operator 등)은 후속.
- **코드 위치**: [`terraform/oci-k3s/`](../../terraform/oci-k3s/README.md), 회수 스크립트 `scripts/oci-k3s-kubeconfig.sh`.

### 결정 근거 — 대안 비교 (선호 순 ★)

**1) 관리형 vs 자체설치 K8s** — *자체설치 k3s 채택*

| 선택지 | 평가 | 비고 |
| --- | --- | --- |
| **k3s(자체설치)** ★★★★★ | 무료·경량·학습가치 높음 | Always Free VM에 그대로. 운영 책임은 본인 |
| OKE(관리형) ★★★☆☆ | control plane 편의 | Basic OKE control plane은 무료지만 워커는 A1, 학습상 "직접 부트스트랩" 가치가 줄어듦 |
| kubeadm ★★☆☆☆ | 표준에 가까움 | 무겁고 부트스트랩 수고 큼, 단일노드 학습엔 과함 |

**2) 자원 등급** — *Always Free A1(ARM) 채택*

| 선택지 | 평가 | 비고 |
| --- | --- | --- |
| **A1 Flex(ARM) 4/24** ★★★★★ | 비용 0·자원 최대 | **arm64 이미지 필요**. Apple Silicon 로컬과 arch 일치 |
| x86 마이크로 2대 ★★☆☆☆ | amd64 | 1 GB RAM×2로 데이터스택 부적합 |
| 유료 소형 x86 ★★★☆☆ | 자원 자유 | 과금 발생, 학습엔 과투자 |

**3) 프로비저닝** — *cloud-init 채택*

| 선택지 | 평가 | 비고 |
| --- | --- | --- |
| **cloud-init(user_data)** ★★★★★ | 선언적·재현성·SSH 비의존 | 부팅 시 1회 실행. IaC 원칙 부합 |
| remote-exec ★★★☆☆ | 직관적 | SSH 연결·순서 의존, 재현성 약함 |
| Terraform+Ansible ★★★☆☆ | 관심사 분리 | 다중노드·복잡 구성엔 유리, 단일노드엔 과함 |

**4) 토폴로지** — *단일 노드 채택* (다중/HA는 자원상 후속)

## 현황 — ⏸ 보류

**로컬 K8s(kind on Podman)로 방향을 되돌렸다.** OCI 이행은 중단이 아니라 **보류**이며, 코드·state를 그대로 둔다.

- **보류 사유**: A1 인스턴스가 **Out of host capacity로 생성되지 않는다**(아래 §운영 메모 — shape 축소·AD 우회
  모두 무효). 용량 폴링 재시도([`scripts/oci_k3s_retry_apply.py`](../../scripts/oci_k3s_retry_apply.py))를 돌려도
  재고가 열리지 않아, **검증 가능한 로컬 환경**([k8s.md](k8s.md) · `kind` 클러스터 `lakehouse`)을 우선하기로 했다.
- **프로비저닝 상태**: `terraform.tfstate`(serial 33 — 🔴 **관측 시점 스냅샷이다. `apply` 한 번이면
  낡으므로 재개 시 갱신한다**)에 **네트워크 5종이 실재**한다 —
  `oci_core_vcn` · `oci_core_subnet` · `oci_core_internet_gateway` · `oci_core_route_table` · `oci_core_security_list`.
  **`oci_core_instance.k3s`(A1 컴퓨트)는 미생성**이다.
  - **과금 없음**: 위 5종은 모두 Always Free 대상이며, 과금 요인인 컴퓨트·블록스토리지가 없다.
  - **state를 지우지 않는다** — 지우면 위 5종이 orphan이 되어 terraform으로 관리·삭제할 수 없고
    OCI 콘솔에서 수동 삭제해야 한다. 보류 중 유지 비용은 0이므로 유지가 안전하다.
- 🔴 **재개는 「Δ 트리거」다 — `apply` 실행 *전에* `security` 재판정을 1회 받는다.**
  재개는 **공인 IP 노드를 새로 세우는 비가역 작업**이라 [conventions/agents.md](../conventions/agents.md)
  §게이트의 Δ 조건(ⓑ 비가역·ⓒ 외부 노출)에 그대로 걸린다. **보류 기간 동안 저장소의 노출 실태가
  바뀌었으므로 보류 시점의 판정을 재사용하지 않는다.** 재판정 대상 넷:
  1. **공개 노출면** — Security List `/32` 화이트리스트가 **현재** 공인 IP와 맞는지(보류 중 바뀌었을
     가능성이 높다), 호스트 iptables의 **kubelet 10250 소스 무제한**이 그대로인지
     ([security.md §2.6](../security.md) — SL이 앞단 방어라 SL이 느슨해지면 즉시 노출된다).
  2. **관리 UI 인증 재현 여부** — 로컬 kind에서 **관리 UI를 Ingress로 낼 때 인증이 전제되지 않는**
     사례가 확인됐다(대상·상태는 비공개 posture 기록 `$OBSIDIAN_VAULT/security/posture.md` §2).
     🔴 **같은 매니페스트를 공인 IP
     노드에 올리면 그대로 인터넷 노출이 된다** — 로컬에서 "내부망이라 괜찮다"고 넘긴 판정은
     OCI에서 성립하지 않는다. 보류 이후 클러스터에 **추가된 워크로드 전체**가 대상이다.
  3. **크리덴셜 유입 경로** — 회수한 `kubeconfig-oci`·ephemeral 공인 IP·엔드포인트가 문서·저널·
     **플랜 미러**(자동 푸시되는 볼트)로 새지 않는지. 보류 시점에는 플랜 미러가 없었다.
  4. **과금 상한** — **무료 한도는 사업자가 바꾼다**(2026-06 A1 4/24 → **2/12**).
     `variables.tf`의 `validation` 블록이 **현행** 한도를 반영하는지
     ([conventions/terraform.md](../conventions/terraform.md)).

  판정 결과는 **날짜 + 이전 판정 + 새 판정**을 병기해 이 절에 **누적**한다 — 이전 판정을 지우지
  않는다. 무엇이 언제 왜 뒤집혔는지가 다음 재개의 입력이다.
- **재개 방법**: 위 재판정을 통과한 뒤, `terraform/oci-k3s/`에서 `terraform apply`(또는 용량 폴링 스크립트)
  재실행. 네트워크는 이미 있으므로
  **컴퓨트만 추가 생성**된다. 재개 전 [`terraform.tfvars`](../../terraform/oci-k3s/terraform.tfvars.example) 값과
  무료 한도(**2 OCPU/12 GB**)를 재확인한다.
- **정리하려면**: `terraform destroy`로 5종을 지운다(코드·문서는 보존). 무료라서 서둘 이유는 없다.

## 운영 메모

- **A1 용량 부족(Out of host capacity, HTTP 500)**: 무료 A1은 인기가 높아 `apply`가 용량 오류로 실패한다.
  **시간차 재시도가 유일한 무료 해법**이다 → [`scripts/oci_k3s_retry_apply.py`](../../scripts/oci_k3s_retry_apply.py)
  (기본 60초 간격·720회 = 약 12시간, 용량 부족 외 오류는 즉시 중단).
  - **재시도는 `apply` 반복이 아니라 용량 폴링이다** — `apply` 1회는 plan 재계산까지 포함해 무거워
    간격을 좁힐 수 없는데, A1 재고는 초 단위로 열렸다 닫힌다. 읽기 전용·경량인
    [CreateComputeCapacityReport](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/core/models/oci.core.models.CapacityReportShapeAvailability.html)로
    폴링하고 `availability_status == "AVAILABLE"`인 순간에만 `apply`를 던진다.
    실패하는 `LaunchInstance` 반복은 API 스로틀링(429)도 자초하므로 이 편이 두 배로 유리하다.
  - **판정은 `availability_status`로만 한다** — 같은 응답의 `available_count`는 무료 테넌시에서
    비어(`null`) 온다(도쿄 AD-1 실측). 카운트를 조건에 넣으면 재고가 열려도 건너뛴다.
  - enum은 `AVAILABLE` / `OUT_OF_HOST_CAPACITY` / `HARDWARE_NOT_SUPPORTED` 세 가지다.
    `HARDWARE_NOT_SUPPORTED`는 재고가 아니라 **설정** 문제이므로 즉시 중단한다.
  - **shape을 줄여도 소용없다** — 실측: 4/24·2/12·1/6 **모두 동일 실패**. 크기가 아니라 호스트 재고 문제다.
  - **쿼터와 용량은 다른 축이다** — 같은 시점 `oci_limits_resource_availability` 조회 결과 `standard-a1-core-count`
    한도 41·사용 0으로 **쿼터는 여유**였다. 500이 나와도 한도를 의심할 필요는 없다.
  - **AD·리전 우회는 사실상 불가** — Always Free는 **홈 리전 전용**이고 홈 리전은 **변경 불가**
    ([Managing Regions](https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/managingregions.htm)).
    `ap-tokyo-1`은 AD도 1개라 AD 변경 여지도 없다.
- **호스트 방화벽**: OCI Ubuntu 기본 iptables가 ingress를 막는다 → cloud-init에서 6443·10250·VXLAN(8472)·
  파드/서비스 CIDR 허용(누락 시 노드 NotReady). 규칙은 [conventions/k8s.md](../conventions/k8s.md)와 정합.
- **공인 IP**: ephemeral 공인 IP는 재시작 시 변경 → kubeconfig TLS SAN 어긋남. 안정화 시 **예약 공인 IP**로 승격.
- **비밀·상태**: `terraform.tfvars`·`*.tfstate`·API 개인키·`kubeconfig-oci`는 커밋 금지([security.md](../security.md)).
  state에 민감정보가 저장되므로 장기적으로 원격 백엔드(OCI Object Storage) + 암호화 검토.
- **arm64**: 후속 데이터스택 이행 시 Spark/Flink 러너 이미지를 **arm64로 재빌드**해야 한다([redesign.md](../redesign.md)).

## 참고

- Oracle Cloud Free Tier: https://www.oracle.com/cloud/free/
- OCI Terraform Provider: https://registry.terraform.io/providers/oracle/oci/latest/docs
- oci_core_instance: https://registry.terraform.io/providers/oracle/oci/latest/docs/resources/core_instance
- k3s (공식): https://docs.k3s.io/
- k3s 릴리스(버전 핀): https://github.com/k3s-io/k3s/releases
- cloud-init: https://cloudinit.readthedocs.io/
- garutilorenzo/k3s-oci-cluster(참고 사례): https://github.com/garutilorenzo/k3s-oci-cluster
