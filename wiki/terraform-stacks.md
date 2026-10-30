# Terraform 스택을 둘로 가르며 — 분할 축은 destroy가 무엇을 파괴하는가다

> 스터디 장. 로컬 클러스터를 Terraform 스택 A와 B로 나눈 이유와, 두 스택의 state를 다루는 법.
> 결론부터 쓰면 — **스택은 의존이 아니라 폭발반경으로 가른다. 그리고 state는 apply한 트리에 산다.**

## 개념

Terraform은 선언형 인프라 프로비저닝 도구다.
리소스의 목표 상태를 HCL로 선언하면 실제 상태를 읽어 차이를 계산하고(plan), 그만큼만 수렴시킨다(apply).
셸 스크립트와 갈리는 점은 셋이다 — 멱등성이 리소스 모델에 내장되고, 의존 그래프가 실행 순서를 대체하며,
선언과 실측의 차이를 `plan`이 보여준다.

- 공식 문서: [Terraform 문서](https://developer.hashicorp.com/terraform/docs) ·
  [state 민감정보](https://developer.hashicorp.com/terraform/language/state/sensitive-data)

## 이 저장소에서의 쓰임

스택은 클러스터 쪽 둘이다. 클라우드 축 `terraform/oci-k3s/`는 보류라 이 장에서 다루지 않는다.

| 스택 | 경로 | 내용 | `destroy` 시 |
| --- | --- | --- | --- |
| A. cluster | [`terraform/cluster/kind/`](https://github.com/Melting-Face/pipeline-study/blob/main/terraform/cluster/kind) | kind 클러스터 · 레지스트리 설정 · SeaweedFS 컨테이너 · 버킷 | 클러스터와 그 안의 PVC가 소멸한다 |
| B. platform | [`terraform/platform/`](https://github.com/Melting-Face/pipeline-study/blob/main/terraform/platform) | ingress-nginx · argo-cd · 부트스트랩 ApplicationSet | ArgoCD와 앱 정의가 소멸한다 |
| 그 아래 | [`gitops/charts/`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/charts) | 오퍼레이터 이하 전부 | ArgoCD가 소유한다. Terraform이 아니다 |

- 스택 B는 appset을 **두 번째 `helm_release`** 로 분리한다. argo-cd 차트가 CRD를 `crds/`가 아닌 `templates/`에 담아,
  한 릴리스에서 CRD와 CR을 함께 만들면 Helm이 GVK를 해석하다 실패한다.
  `kubernetes_manifest`는 plan 시점에 CRD 스키마를 조회해 최초 plan이 죽으므로 쓰지 않는다.
- S3 컨테이너는 스택 A에 속하지만 데이터는 호스트 바인드라 destroy해도 남는다.
- 스택 A의 변수와 precondition은
  [`variables.tf`](https://github.com/Melting-Face/pipeline-study/blob/main/terraform/cluster/kind/variables.tf)와
  [`main.tf`](https://github.com/Melting-Face/pipeline-study/blob/main/terraform/cluster/kind/main.tf)에 있다.
- 규칙은 [docs/conventions/terraform.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/terraform.md),
  결정 근거는 [docs/architectures/terraform.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/terraform.md)가 정본이다.
- 위층이 하는 일은 [argocd-bootstrap.md](argocd-bootstrap.md), 클러스터를 올리는 순서는 [k8s-bootstrap.md](k8s-bootstrap.md)에 있다.

## 실습

명령은 저장소 **루트 체크아웃**에서 친다. 스택 A는 linked worktree에서 precondition이 멈춘다.

**띄우기** — 스택 A에 이어 스택 B를 올린다.

출처: [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 3·4단계

```shell
terraform -chdir=terraform/cluster/kind init
terraform -chdir=terraform/cluster/kind plan
terraform -chdir=terraform/cluster/kind apply

terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform plan
terraform -chdir=terraform/platform apply
```

스택 A는 변수 셋이 필요하다(S3 키 둘 · podman 소켓).
값을 화면에 찍지 않는 준비 명령은 `docs/setup/local-k8s.md` 2단계에 있다.
`apply`의 끝줄은 [k8s-bootstrap.md](k8s-bootstrap.md) 「실습」에 실려 있다.

**확인** — state에 무엇이 들어 있는지 읽는다. 아래 두 `state list`는 출처 문서에 없는 읽기 전용 명령이다.

스택 A의 리소스 목록이다.

```text
# 관측: terraform -chdir=terraform/cluster/kind state list
data.external.checkout
data.external.runtime
docker_container.seaweedfs
docker_image.seaweedfs
kind_cluster.this
terraform_data.registry_certs
terraform_data.registry_network
terraform_data.seaweedfs_buckets
```

- 성공 기준: `kind_cluster.this`와 `docker_container.seaweedfs`가 보인다.
  `terraform_data` 셋은 호스트 podman을 부르는 `local-exec`다.

스택 B의 리소스 목록이다.

```text
# 관측: terraform -chdir=terraform/platform state list
helm_release.appset
helm_release.argo_cd
helm_release.ingress_nginx
```

- 성공 기준: `helm_release` 셋이 보인다. `helm list -A`가 같은 셋을 `deployed`로 보인다 —
  [k8s-bootstrap.md](k8s-bootstrap.md) 「실습」.

**내리기** — 위에서부터 거꾸로, 스택 B 다음에 스택 A다.

출처: [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 「내리기」

```shell
terraform -chdir=terraform/platform destroy
terraform -chdir=terraform/cluster/kind destroy
```

`kind delete cluster`로 먼저 지우면 state가 남아 다음 `apply`가 꼬인다. Terraform이 만든 것은 `destroy`로 내린다.
⚠️ 이 순서는 클러스터를 지운다. 비가역이므로 실행 전
[risk.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/risk.md) §4 게이트를 거친다.
이번 관측에서는 내리지 않았으므로 출력은 미관측이다.

## 고장 대응

| 증상 | 먼저 볼 것 | 처방 |
| --- | --- | --- |
| 스택 A `plan`이 `could not locate any control plane nodes for cluster named 'lakehouse'`로 멈춘다 | state에 `kind_cluster.this`가 있는데 `kind get clusters`와 노드 컨테이너는 없는가 | state 백업 후 `terraform -chdir=terraform/cluster/kind state rm kind_cluster.this`, 다시 `plan`한다. state 편집이라 비가역 게이트를 거친다 |
| 스택 A `apply`가 linked worktree를 거부한다 | 실행한 체크아웃 | 루트 체크아웃에서 실행한다. state도 거기 있다 |
| 다른 트리에서 apply했더니 `already exists`로 멈춘다 | 그 트리의 state가 비어 있는가 | 정상 동작이다. 루트로 돌아가 실행한다 |
| 판정용 `plan`이 `No changes`가 아니라 교체를 낸다 | apply 때와 같은 `-var`를 넘겼는가 | 같은 변수로 다시 `plan`한다 |
| 클러스터가 내려간 상태에서 `plan`이 `Planning failed`로 멈춘다 | 노드가 떠 있는가 | 노드를 올린다. 선언만 보려면 `terraform show`를 쓴다 |

첫 행은 실습 관측에서 왔고, 같은 증상이 [k8s-bootstrap.md](k8s-bootstrap.md) 「고장 대응」에도 있다.
둘째 행은 [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 「스택 B 바꾸기」,
셋째·넷째 행은 [docs/conventions/terraform.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/terraform.md)
「state는 정규 워크트리에서만」,
마지막 행은 [docs/architectures/terraform.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/terraform.md)
「불통 시 plan은 실패한다」에서 옮겼다.

## 함정

### state는 apply한 트리에 산다

출처: [docs/conventions/terraform.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/terraform.md) 「state는 정규 워크트리에서만 다룬다」

backend 블록이 없으면 state는 실행 디렉터리에 놓인다.
그런데 `.gitignore` 대상이라 `git worktree`로 만든 트리에는 따라오지 않는다.
state가 트리마다 갈리고 그중 하나만 진짜가 된다. 그 트리를 지우면 유일본이 사라져 리소스를 통째로 다시 import해야 한다.

- `.gitignore`가 막아 주는 것이 아니라 오히려 gitignore 때문에 생기는 문제다.
- 기계적 방어는 스택 A 하나다. 나머지는 규율이다. 다른 트리에서 apply하면 `already exists`로 **에러를 내고 멈춘다**.

### 판정 `plan`은 apply 때와 같은 변수로 돌린다

같은 문서의 같은 절이다. 변수를 빼면 state가 아니라 변수 기본값과 비교되어, state가 멀쩡해도 교체가 나온다.
실제로 `-var cluster_name`을 빼고 돌린 `plan`이 `kind_cluster` 교체를 냈고, 같은 `-var`를 넘기자 `No changes.`였다.

### `kubernetes_manifest`는 plan이 라이브 클러스터를 요구한다

출처: [docs/architectures/terraform.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/terraform.md) 「불통 시 plan은 실패한다」

죽은 클러스터에서 `plan`을 돌리면 조용히 오도하지 않고 `Planning failed`로 멈춘다.
`-refresh=false`는 state 리소스의 refresh 실패만 없애고, `kubernetes_manifest`의 GVK 해석 실패는 그대로 남는다.
그래서 CI에서 `plan`을 드라이런 게이트로 쓸 수 없다.
문법과 포맷 게이트는 `terraform fmt -check`와 `validate`까지이고, 둘은 클러스터 없이 돈다.
이 절은 철거된 스택에서 얻은 실측이며, 현행 스택 A·B는 `kubernetes_manifest`를 쓰지 않는다.
교훈의 근거만 남겼다.
