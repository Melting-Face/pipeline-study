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
"클러스터 재생성"이 된다. 그 비용은 **PVC 2개 / 실데이터 약 125MB**다.

🔴 **이 문서 초판은 그 비용을 "실데이터 10.0G"라고 적었다. 약 100배 틀린 값이다**(2026-08-28 교정).
`du -sh /data`가 10.0G를 보고한 것은 맞지만, 그것은 SeaweedFS가 **preallocate한 sparse 볼륨
파일의 예약 공간**이다. 같은 파일을 `ls -la`로 보면 62KB다. 실제 오브젝트는 버킷 합계
**106.8MB**(warehouse 107MB · dagster-logs 6.5KB · pg-backup 56B)이고, 여기에 카탈로그 DB
7.9MB와 Dagster 메타 9.6MB를 더해 약 125MB다.

**값이 아니라 라벨이 틀렸다** — `du`는 정확히 보고했고, 그 명령이 *무엇을 세는지* 확인하지 않은 채
"실데이터"라는 이름을 붙인 것이 오류였다. 그리고 이 수치가 **분할 축을 정당화하는 근거로 쓰였다.**
디스크 점유(10G)와 데이터량(125MB)은 **다른 축**이고, 어느 쪽도 틀리지 않았으나 섞으면 판단이 바뀐다.
⇒ 결론(A·B를 셸에 남긴다)은 유지된다. `kind_cluster` import 불가와 재적재 필요성은 그대로이고,
125MB라도 원천 csv.gz 없이는 `raw/{eicu,mimiciv}`를 복원할 수 없기 때문이다.

⇒ 그래서 **destroy가 무엇을 파괴하는가**로 층을 가른다.

| 스택 | 내용 | `destroy` 시 | 방침 |
| --- | --- | --- | --- |
| **A. cluster** | podman machine · kind · 로컬 레지스트리 · ingress-nginx | 실데이터 유실 | 셸 유지 |
| **B. data** | SeaweedFS · CNPG Cluster · Secret · 버킷 | 실데이터 유실 | 셸 유지 |
| **C. platform** | 오퍼레이터·컨트롤러 5종 · 로컬 CA · 워크로드 RBAC · Dagster | 안전(재생성 가능) | **Terraform으로 이행**(미구현) |

⚠️ **현재 구현된 것은 없다.** 이 문서는 설계와 스파이크 실측이며, 스택 C는 아직 만들지 않았다 —
지금 도는 것은 전부 `scripts/k8s-*.sh`다. 진행 상태를 이 문서에서 읽지 않는다.

C만 옮겨도 목적 둘이 **데이터 위험 없이** 충족된다. A는 클러스터를 재생성해야 할 일이
자연히 생기는 시점에 합류시킨다 — 그때는 재생성이 비용이 아니라 **이미 치를 값**이기 때문이다.

### 대안 비교

| 선택지 | 판정 | 이유 |
| --- | --- | --- |
| **C 스택만 Terraform** | ✅ 채택 | 목적 둘을 충족하면서 폭발반경이 데이터에 닿지 않는다 |
| 전체(A+B+C) 일괄 이행 | 🔎 미채택 | `kind_cluster` import 불가 → 즉시 재적재. 원천 없이는 복원 불가한 부분이 있다 |
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
├── operators.tf    helm_release ×3 (아래 주의)
├── security.tf     로컬 CA · 워크로드 RBAC
├── dagster.tf      k8s/dagster/*.yaml 을 yamldecode 로 적용
└── outputs.tf
```

이행이 끝나면 `scripts/k8s-operators.sh`는 사라지고 `terraform apply`가 그 자리를 대신한다.
`k8s-dagster.sh`에는 **이미지 빌드·push만** 남는다.

⚠️ **C의 5종이 전부 helm은 아니다**(2026-08-28 실측 — 이 문서 초판이 "helm 5종"으로 적었던 것은 틀렸다).

| 대상 | 설치 방식 | Terraform 대응 |
| --- | --- | --- |
| Spark Operator · Flink Operator · CloudNativePG | `helm upgrade --install` | `helm_release` — import 가능 |
| cert-manager · Barman Cloud 플러그인 | **원격 멀티도큐먼트 매니페스트 `kubectl apply`** | ⚠️ 대응이 자명하지 않다 |

원격 매니페스트 둘은 ingress-nginx와 **같은 문제**다 — URL 하나가 수십 개 오브젝트를 담고 있어
`kubernetes_manifest` 하나로 못 받는다. 선택지는 셋이고 어느 것도 공짜가 아니다:
공식 helm 차트로 갈아타기(설치 산출물이 달라진다) · `http` 데이터소스로 받아 `yamldecode` 분해
(멀티도큐먼트 분해가 HCL에서 지저분하다) · **셸에 남기기**(A 스택과 같은 취급).
⇒ 초기 이행에서는 **셸에 남긴다**. 이 둘은 버전 고정이 URL에 박혀 있어 drift 위험이 낮다.

Flink Operator는 `--set` 8종(자원)에 더해 **`--values k8s/flink/operator-values.yaml`도 함께** 쓴다.
import 시 둘 다 HCL로 옮겨야 `plan`이 `0 to change`가 된다.

## 운영 메모

### 🔴 폭발반경은 스택 경계를 새어 나간다 — CRD

C를 "안전하게 destroy 가능"으로 두려면 **오퍼레이터 uninstall이 CRD를 지우지 않아야 한다.**
CNPG 차트가 CRD를 함께 제거하면 `Cluster` CR이 사라지고 **B의 PVC가 따라간다.**
선언으로 닫지 말고 **일부러 `destroy`를 돌려 PVC가 살아남는지 확인**한다.

### `helm_release` 인수는 `0 to change`에 도달하지 못한다

🔴 **이 문서 초판이 "`plan`이 `0 to change`여야 인수가 끝난 것"이라 적은 것은 틀렸다**
(2026-08-28 실측). helm은 릴리스에 **병합된 값만** 저장하고 *어느 저장소에서 받았는지*와
*값이 `--set`으로 왔는지 `--values`로 왔는지*는 저장하지 않는다. 그래서 import 직후
`repository`·`set`·`values`가 **항상 diff로 남는다** — HCL이 틀려서가 아니라 복원 불가능한
설정 전용 속성이기 때문이다. 세 릴리스 모두 같은 형태였다.

**그래서 판정 기준은 둘로 나뉜다.**

1. **기능 속성에 diff가 없을 것** — `chart`·`version`·`namespace`·`name`. 여기가 움직이면
   진짜로 어긋난 것이다.
2. **렌더 결과가 같을 것** — `helm get manifest <r> -n <ns>`(현재)와
   `helm template …`(제안)을 비교한다. Terraform의 `values` diff는 `metadata`를 통째로
   `known after apply`로 그리느라 **판정에 쓸 수 없다** — 현재 값이 제거되는 것처럼 보인다.

⚠️ **`create_namespace = true`를 먼저 걷어내야 이 판정이 가능하다.** import는 이 플래그를
state에 기록하지 않으므로 `true`면 인수 직후 항상 update-in-place가 계획되고, 그 여파로
**metadata 전체가 `known after apply`로 덮여 진짜 diff가 묻힌다.**
네임스페이스를 셸이 이미 만들었다면 `false`가 사실에 맞다.

실측 대조 결과(2026-08-28) — 셋 다 **기능적으로 동일**했다:
CNPG는 EOF 빈 줄 1개, Flink는 후행 빈 줄 2개, Spark은 `helm.sh/hook: test` Pod만 차이였다
(`helm template`은 test 훅을 렌더하지만 릴리스 매니페스트에는 들어가지 않는다).
그 뒤 `apply` 1회로 정합을 맞췄고 **파드 재시작 0**(이름·재시작 횟수·생성시각 불변),
helm revision만 1씩 올랐다. 이후 `plan`은 `No changes`다.

**정합을 맞추는 `apply`를 생략하면 안 된다** — diff를 방치하면 `plan`이 영구히
`3 to change`를 보여주고 **진짜 drift가 그 잡음에 묻힌다**(이 이행의 주목적이 무력화된다).

가장 위험한 것은 Flink Operator다 — `--set` 8종에 더해 `--values`도 쓰는데, 그 값 파일에
**공급망 통제**(`user.artifacts.allowed-schemes`에서 https 제거)가 들어 있다. 빠뜨리면
차트 기본값으로 되돌아가 [resource-sizing.md](../resource-sizing.md) 배분표가 거짓이 되고
**런타임 외부 jar fetch 경로가 조용히 열린다.**

### 게이트는 `fmt`와 `validate`를 **다른 자리에** 둔다

규약(`conventions/terraform.md` §3)은 둘을 함께 커밋 전 게이트로 요구했으나 집행 수단이
없었고(2026-08-27까지 terraform 훅 0개 — `terraform/oci-k3s/`는 한 번도 검사받은 적이 없다),
넣으려 보니 **같은 자리에 둘 수 없었다.**

- **`fmt` → pre-commit.** 네트워크가 필요 없다.
- **`validate` → CI 잡.** `validate`는 `init`이 선행돼야 하는데 `.terraform/providers`는
  gitignore라 저장소에 없다. 훅에서 `init`을 돌리면 **커밋이 프로바이더 레지스트리
  가용성에 묶인다** — `sqlfluff`의 `templater = "dbt"`에서 이미 겪은 함정이다.
  2026-08-28 실측: worktree에서 `validate`가
  `no package for oracle/oci 6.37.0 cached in .terraform/providers`로 실패했다.
- **`plan`은 어디에도 두지 않는다.** 위 §불통 시 `plan` 참조.

CI 잡은 `terraform/*/`를 순회해 스택이 늘어도 자동으로 집고, **0건이면 통과가 아니라 실패**로
떨어뜨린다(디렉터리 구조가 바뀌었을 때 잡이 조용히 무의미해지는 것을 막는다).

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

⇒ 그래서 **노드를 실제로 정지시켜 다시 쟀다**(변인이 하나로 준다). 결과는 아래.

### 불통 시 `plan`은 실패한다 — 우회는 절반만 듣는다

2026-08-27 실측(`podman stop lakehouse-control-plane` 후). 조용히 오도하지 **않는다** —
`Planning failed`로 시끄럽게 죽는다. 다만 실패 지점이 **둘**이고 성질이 다르다.

| 축 | 증상 | `-refresh=false` |
| --- | --- | --- |
| state에 있는 리소스의 refresh | `Get .../namespaces/...: connection refused` | ✅ 사라진다 |
| **`kubernetes_manifest`의 GVK 해석** | `Invalid configuration for API client` — `Get .../apis` | ❌ **그대로 남는다** |

🔴 **이 문서가 앞서 적었던 "우회가 있어 설계를 막지 않는다"는 틀렸다.**
`kubernetes_manifest`는 refresh와 무관하게 `/apis`로 GVK를 해석해야 하므로,
**YAML을 `yamldecode`로 적용하는 이 설계에서는 `plan` 자체가 라이브 클러스터를 요구한다.**

실무상 치명적이지는 않다 — 죽은 클러스터에는 어차피 `apply`할 수 없다. 그러나 다음 둘이 따라온다.

- **CI에서 `plan`을 드라이런 게이트로 쓸 수 없다**(클러스터 없는 러너에서 돈다).
  문법·포맷 게이트는 `terraform fmt -check` + `validate`까지이고, 그 둘은 클러스터 없이 돈다.
- 클러스터가 내려간 상태에서 "선언이 뭐였더라"를 `plan`으로 확인할 수 없다. `terraform show`로 본다.

### `scripts/worktree-new.sh`는 기존 브랜치를 붙이지 못한다

48행이 `git worktree add "$DIR" -b "$BRANCH"`로 **항상 새 브랜치를 만든다.** 이미 있는 브랜치를
다른 worktree로 여는 경로가 없어, 병렬 세션이 서로의 브랜치를 트리에서 밀어내는 상황에서
정작 규약이 권하는 수단을 못 쓴다.

**축은 셋이고 현재 처리되는 것은 하나뿐이다.** 이걸 다 세지 않으면 "분기 하나 넣으면 된다"로
닫히는데, 그러면 세 번째에서 여전히 raw git 에러가 나서 **고쳤다고 믿는데 안 고쳐진** 상태가 된다.

| 상태 | 필요한 것 | 현재 |
| --- | --- | --- |
| 브랜치 미존재 | `-b` | ✅ 유일하게 처리됨 |
| 브랜치 존재 + 어디에도 미체크아웃 | `-b`를 **빼야** 함 | ❌ |
| 브랜치 존재 + **다른 worktree에 체크아웃됨** | 분기가 아니라 **안내** — `git worktree add`가 거부한다 | ❌ |

맨손 `git worktree add`로 우회하려면 `LINK_ASSETS` 3종
(`.env`·`.claude/.claims`·`.claude/settings.local.json`)을 **직접 링크**해야 한다 —
`.claims`가 빠지면 피어 감지가, `settings.local.json`이 빠지면 권한 범위가 **조용히** 달라진다.
링크 후에는 `ListAgents`로 피어가 여전히 보이는지 **확인하고** 작업을 시작한다(선언으로 닫지 않는다).

## 참고

외부 공식 문서는 [`../references.md`](../references.md)에 단일 관리한다 — URL을 여기 복제하지 않는다.
이 문서와 직접 관련된 항목: Terraform(providers · import · state) · Kubernetes/Helm 프로바이더 ·
kind · CloudNativePG. 규칙 정본은 [`../conventions/terraform.md`](../conventions/terraform.md).
