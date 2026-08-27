# Terraform (아키텍처 · 프로젝트 관점)

## 개요

Terraform은 **선언형 인프라 프로비저닝** 도구다. 리소스의 목표 상태를 HCL로 선언하면 실제 상태를
읽어(refresh) 차이를 계산하고(plan) 그만큼만 수렴시킨다(apply). 셸 스크립트와 갈리는 지점은 셋이다 —
**멱등성이 리소스 모델에 내장**되고, **의존 그래프가 실행 순서를 대체**하며, **선언과 실측의 차이를
`plan`이 보여준다**.

- 이 저장소 고정: Terraform **1.15.8** · 스택별 프로바이더는 `versions.tf`에 `~>`로 핀

## 이 프로젝트에서의 위치 — 🚧 채택·이행중

스택은 둘이다. 클라우드 축 [`terraform/oci-k3s/`](oci.md)는 **⏸ 보류**(A1 용량 부족)이고,
이 문서가 다루는 것은 **로컬 K8s 플랫폼을 셸에서 Terraform으로 옮기는** 축이다.

### 왜 옮기는가 — 근거는 관념이 아니라 사고 2건이다

2026-08-27 `scripts/k8s-up.sh`에서 같은 부류의 버그가 **두 번** 나왔다. 레지스트리 컨테이너와
kind 노드를 각각 *실행 / 부재* 이분법으로 보다가, 실제 상태가 셋(**실행 / 중지 / 부재**)이라
중지 상태에서 죽었다. 셸로 멱등성을 손으로 짜는 한 이 부류는 계속 나온다 —
Terraform의 리소스 모델은 create/read/update가 기본이라 같은 실수가 성립하지 않는다.

목적은 **멱등성·drift 감지**와 **일괄 생성·파괴 재현성** 둘이다.
버전 중앙 관리는 이미 `scripts/k8s-env.sh`가 하고 있어 이득이 작고, 학습은 목적이 아니다 —
그래서 **어색한 계층을 억지로 Terraform에 넣지 않는다.**

### 분할 축은 부트스트랩이 아니라 **폭발반경**이다

처음에는 "프로바이더 부트스트랩 닭-달걀"을 기준으로 2스택을 검토했으나,
스파이크에서 나온 사실 하나가 축을 바꿨다 — **`kind_cluster`는 import를 지원하지 않는다.**
기존 클러스터를 파괴 없이 인수할 수 없다는 뜻이고, 그러면 "Terraform 채택"이 곧
"클러스터 재생성"이 된다. 그 비용은 2026-08-27 실측으로 **PVC 2개 / SeaweedFS 실데이터 10.0G**다.

⇒ 그래서 **destroy가 무엇을 파괴하는가**로 층을 가른다.

| 스택 | 내용 | `destroy` 시 | 현재 |
| --- | --- | --- | --- |
| **A. cluster** | podman machine · kind · 로컬 레지스트리 · ingress-nginx | 10G 유실 | 셸 유지 |
| **B. data** | SeaweedFS · CNPG Cluster · Secret · 버킷 | 10G 유실 | 셸 유지 |
| **C. platform** | 오퍼레이터 5종 · 로컬 CA · 워크로드 RBAC · Dagster | 안전(재생성 가능) | **Terraform** |

C만 옮겨도 목적 둘이 **데이터 위험 없이** 충족된다. A는 클러스터를 재생성해야 할 일이
자연히 생기는 시점에 합류시킨다 — 그때는 재생성이 비용이 아니라 **이미 치를 값**이기 때문이다.

### 대안 비교

| 선택지 | 판정 | 이유 |
| --- | --- | --- |
| **C 스택만 Terraform** | ✅ 채택 | 목적 둘을 충족하면서 폭발반경이 데이터에 닿지 않는다 |
| 전체(A+B+C) 일괄 이행 | 🔎 미채택 | `kind_cluster` import 불가 → 즉시 10G 재적재. 비용이 이득을 넘는다 |
| helm 계층만 이행 | 🔎 미채택 | 사고 2건이 난 kind·레지스트리 축을 **정작 안 고친다** |
| 셸 유지 + 멱등성 보강 | 🔎 미채택 | 3상태 분기를 손으로 계속 짜야 한다. 다음 상태 축이 나오면 또 샌다 |

### 매니페스트는 YAML로 남기고 Terraform은 적용자만 한다

`k8s/**`의 YAML에는 "왜 이 값인가"가 주석으로 들어 있다(aws-chunked 함정 · S3FileIO와 S3A의
역할 분담 · probe가 보증하지 않는 것 …). HCL 타입 리소스로 재작성하면 **그 지식이 사라진다.**

⇒ `kubernetes_manifest { manifest = yamldecode(file(...)) }` 형태로 **YAML을 정본으로 유지**하고
HCL은 배선만 담는다. 2026-08-27 스파이크에서 CNPG `Database` CR로 `plan` 통과를 확인했다.

### 예정 구성

```text
terraform/lakehouse-platform/
├── versions.tf     required_version · 프로바이더 핀 · .terraform.lock.hcl 커밋
├── provider.tf     kubernetes·helm — config_context 고정
├── variables.tf    차트 버전·이미지 태그·Ingress 호스트(k8s-env.sh 값 이관)
├── operators.tf    helm_release ×5
├── security.tf     로컬 CA · 워크로드 RBAC
├── dagster.tf      k8s/dagster/*.yaml 을 yamldecode 로 적용
└── outputs.tf
```

`scripts/k8s-operators.sh`는 사라지고 `terraform apply`가 그 자리를 대신한다.
`k8s-dagster.sh`는 **이미지 빌드·push만** 남는다.

## 운영 메모

### 🔴 폭발반경은 스택 경계를 새어 나간다 — CRD

C를 "안전하게 destroy 가능"으로 두려면 **오퍼레이터 uninstall이 CRD를 지우지 않아야 한다.**
CNPG 차트가 CRD를 함께 제거하면 `Cluster` CR이 사라지고 **B의 PVC가 따라간다.**
선언으로 닫지 말고 **일부러 `destroy`를 돌려 PVC가 살아남는지 확인**한다.

### 인수(import)의 성공 판정은 "에러 없음"이 아니라 "diff 0"이다

기존 자원을 `terraform import`로 state에 넣은 뒤 **`plan`이 `0 to change`여야** 인수가 끝난 것이다.
diff가 뜨면 HCL이 실제 설치 값과 어긋난 것이고, 모르고 apply하면 **오퍼레이터가 재설치된다.**
가장 위험한 것은 Flink Operator다 — `scripts/k8s-operators.sh`가 `--set` 8종을 주는데,
그 값이 HCL에 그대로 옮겨지지 않으면 차트 기본값(`resources: {}`)으로 되돌아가
[resource-sizing.md](../resource-sizing.md)의 배분표가 **처음부터 거짓**이 된다.

### `conventions/terraform.md` §3의 게이트는 아직 집행 수단이 없다

규약은 `terraform fmt -check -recursive` → `terraform validate`를 커밋 전 게이트로 요구하지만
`.pre-commit-config.yaml`에 **terraform 훅이 하나도 없다**(2026-08-27 확인). 즉 기존
`terraform/oci-k3s/`도 **한 번도 검사받은 적이 없다.** 훅을 넣을 때는 그 스택이 먼저 통과하는지
확인한다 — 새 스택만 보고 넣으면 기존 스택이 커밋을 막는다.

### 프로바이더 실측 (2026-08-27)

| 프로바이더 | 버전 | 확인된 것 |
| --- | --- | --- |
| `hashicorp/kubernetes` | 2.38 계열 | CR을 `yamldecode`로 `plan` 통과 · 타입 리소스 apply/destroy 왕복 |
| `hashicorp/helm` | 3.x | 해석·설치. 릴리스 관리는 미검증 |
| `kreuzwerker/docker` | 3.9.0 | **podman 소켓과 실통신** — 실 `kind` 네트워크 ID 회수 |
| `tehcyx/kind` | 0.11.0 | 해석·설치. **import 미지원**(에러 메시지로 확인) |

### 관측 경로를 엉뚱한 도구로 확인한 사례

"클러스터가 불통일 때 `plan`이 도는가"를 죽은 kubeconfig로 시뮬레이션했으나 **판정에 실패했다.**
네 번의 결과가 어느 가설로도 일관되게 설명되지 않았고, 원인은 관측 경로 확인의 대상이 틀린 것이었다 —
**`kubectl`이 그 파일로 실패한다는 것은 확인했지만, Terraform이 그 파일을 읽는다는 것은
확인한 적이 없다.** 도구 A로 검증한 조건을 도구 B에 그대로 적용한 셈이다.

⇒ 이 축은 `미확인`으로 남긴다. 우회(`terraform plan -refresh=false`)가 있어 설계를 막지 않는다.
확정하려면 **kubeconfig를 조작하지 말고 노드를 실제로 정지**시켜야 한다(변인이 하나로 준다).

### `scripts/worktree-new.sh`는 기존 브랜치를 붙이지 못한다

48행이 `git worktree add "$DIR" -b "$BRANCH"`로 **항상 새 브랜치를 만든다.** 이미 있는 브랜치를
다른 worktree로 여는 경로가 없어, 병렬 세션이 서로의 브랜치를 트리에서 밀어내는 상황에서
정작 규약이 권하는 수단을 못 쓴다. 맨손 `git worktree add`로 우회하려면
`LINK_ASSETS` 3종(`.env`·`.claude/.claims`·`.claude/settings.local.json`)을 **직접 링크**해야 한다 —
`.claims`가 빠지면 피어 감지가, `settings.local.json`이 빠지면 권한 범위가 **조용히** 달라진다.

## 참고

외부 공식 문서는 [`../references.md`](../references.md)에 단일 관리한다 — URL을 여기 복제하지 않는다.
이 문서와 직접 관련된 항목: Terraform(providers · import · state) · Kubernetes/Helm 프로바이더 ·
kind · CloudNativePG. 규칙 정본은 [`../conventions/terraform.md`](../conventions/terraform.md).
