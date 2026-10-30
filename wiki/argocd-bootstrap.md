# ArgoCD로 클러스터를 수렴시키며 — 앱 목록은 Git이, 순서는 tier가 쥔다

> 스터디 장. 빈 클러스터에 ArgoCD 하나만 설치하고, 그 위의 전부를 `git push`로 수렴시키는 구성.
> 결론부터 쓰면 — **한 리소스는 한 주인만 갖는다. Terraform은 ArgoCD까지, 그 위는 Git이 소유한다.**

## 개념

Argo CD는 Git을 원하는 상태의 단일 출처로 삼는 선언형 GitOps 컨트롤러다.
저장소의 매니페스트와 클러스터 실상태를 계속 대조해 차이를 수렴시킨다.
ApplicationSet은 Application을 일괄로 만들고, RollingSync는 그 생성물을 단계별로 sync한다.

- 공식 문서: [Argo CD — Sync Options](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/) ·
  [Argo CD — ApplicationSet 리소스 변경 통제](https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/Controlling-Resource-Modification/) ·
  [Argo CD — Helm](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/)

## 이 저장소에서의 쓰임

설치는 Terraform 스택 B가, 앱 목록은 Git이 맡는다.

| 무엇 | 경로 |
| --- | --- |
| ArgoCD 설치 | [`terraform/platform/argocd.tf`](https://github.com/Melting-Face/pipeline-study/blob/main/terraform/platform/argocd.tf) |
| ingress-nginx 설치 | [`terraform/platform/ingress.tf`](https://github.com/Melting-Face/pipeline-study/blob/main/terraform/platform/ingress.tf) |
| ApplicationSet 템플릿 | [`terraform/platform/charts/appset/templates/applicationset.yaml`](https://github.com/Melting-Face/pipeline-study/blob/main/terraform/platform/charts/appset/templates/applicationset.yaml) |
| ArgoCD 값 | [`terraform/platform/values/argocd.yaml.tftpl`](https://github.com/Melting-Face/pipeline-study/blob/main/terraform/platform/values/argocd.yaml.tftpl) |
| 앱 목록 | [`gitops/apps/`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/apps) — 디렉터리 하나가 앱 하나 |
| 앱 본체 | [`gitops/charts/`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/charts) — 앱 하나가 umbrella 차트 하나 |

흐름은 다음과 같다.

1. 스택 B가 `helm_release` 셋을 만든다 — `ingress-nginx`, `argo-cd`, `appset`.
2. `appset`이 ApplicationSet `apps` 하나를 만든다. git files generator가 `gitops/apps/*/config.yaml`을 읽는다.
3. 설정 파일마다 Application 하나가 생긴다. 앱을 더하는 것은 커밋 하나이고 `terraform apply`가 필요 없다.
4. 순서는 설정 파일의 `tier`가 정한다 — `"0"` 오퍼레이터, `"1"` 데이터 층, `"2"` 워크로드.
   RollingSync가 앞 단계가 Healthy가 된 뒤 다음 단계를 sync한다.

예를 들어 Airflow의 설정은 다음 한 장이다.
메타 DB가 카탈로그 Postgres의 롤·DB라서 tier `"2"`로 뒤에 선다.

```yaml
name: airflow
namespace: airflow
tier: "2"
source:
  type: umbrella
  path: gitops/charts/airflow
```

- `appset`이 별도 `helm_release`인 이유: argo-cd 차트가 CRD를 `templates/`에 담아, 한 릴리스에 CR을 같이 두면
  Helm이 GVK를 해석하다 실패한다. 정본은 [terraform-stacks.md](terraform-stacks.md) 「이 저장소에서의 쓰임」이다.
- Secret은 앱보다 먼저 `scripts/k8s-secrets.sh`가 만든다. Git에는 이름만 있고 값은 없다.
- 설계 전문은 [docs/argocd-gitops.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/argocd-gitops.md)이고,
  결정 근거와 대안 비교는
  [docs/architectures/argocd.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/argocd.md)다.

## 실습

명령은 저장소 **루트 체크아웃**에서 친다. 클러스터는 [k8s-bootstrap.md](k8s-bootstrap.md) 「실습」으로 이미 올라와 있어야 한다.

**띄우기** — 스택 B를 apply하면 ArgoCD와 ApplicationSet이 선다. 이 장은 그 뒤의 확인만 다룬다.

출처: [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 4단계 「플랫폼」

```shell
terraform -chdir=terraform/platform init
terraform -chdir=terraform/platform plan
terraform -chdir=terraform/platform apply
```

`plan`·`apply`의 출력은 [k8s-bootstrap.md](k8s-bootstrap.md)에 있다.
스택 B는 `Apply complete! Resources: 3 added`로 끝나고, 3은 위 `helm_release` 셋이다.

**확인** — ApplicationSet과 접속 주소를 본다.

출처: [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 4·5단계 「확인」

```shell
kubectl get applicationset -n argocd          # apps
terraform -chdir=terraform/platform output -raw argocd_url
```

```text
# 관측: kubectl get applicationset -n argocd
NAME   AGE
apps   <경과>
```

- 성공 기준: ApplicationSet `apps`가 하나 보인다.

```text
# 관측: terraform -chdir=terraform/platform output -raw argocd_url
http://argocd.localtest.me:8080
```

- 성공 기준: 주소가 한 줄로 나온다. 호스트 포트 8080은 스택 B의 기본값이다.

접속 주소가 실제로 응답하는지는 아래로 본다. 이 명령은 출처 문서에 없는 읽기 전용 확인이다.

```text
# 관측: curl -s -o /dev/null -w '%{http_code}\n' http://argocd.localtest.me:8080/
200
```

- 성공 기준: 상태 코드가 `200`이다.

앱이 수렴했는지는 Application 표로 본다.
수렴에는 몇 분이 걸리며, 8행이 전부 `Synced` · `Healthy`이면 성공이다.
관측 출력은 [k8s-bootstrap.md](k8s-bootstrap.md) 「실습」의 `kubectl get applications -n argocd`에 있다.
Application 수는 `gitops/apps/*/config.yaml` 수와 같다.

로그인 정보를 꺼내는 명령은 [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-1 「로그인·비밀값」에 있다.
값이 화면에 남으므로 이 장은 출력을 싣지 않는다.

**내리기** — 출처 문서에 따라 위에서부터 거꾸로 내린다.

출처: [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 「내리기」

```shell
terraform -chdir=terraform/platform destroy
terraform -chdir=terraform/cluster/kind destroy
```

⚠️ 이 순서는 클러스터를 지운다. CNPG 카탈로그도 함께 사라져 재적재가 필요하다 —
[cnpg.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/k8s/cnpg.md).
이번 관측에서는 내리지 않았으므로 두 명령의 출력은 미관측이다.

## 고장 대응

| 증상 | 먼저 볼 것 | 처방 |
| --- | --- | --- |
| `Healthy`인데 `OutOfSync`가 남는다 | 어느 필드가 다른가 | `scripts/argocd-core.sh app diff <앱>` — 종료코드 1이 차이 있음이다 |
| 손으로 바꾼 replicas가 되돌아오지 않는다 | Application의 `automated.enabled` | 설계상 그렇다. `scripts/argocd-core.sh app sync <앱>`으로 손 sync한다 |
| `argocd ... --core`가 `configmap "argocd-cm" not found`로 죽는다 | 전용 kubeconfig의 기본 네임스페이스 | `scripts/argocd-core.sh` 래퍼를 쓴다 |
| 머지 직후 Synced인데 반영이 없다 | `status.sync.revisions`에 머지 커밋이 보이는가 | 수 분 기다린다. 그 사이의 Synced는 이전 revision 기준이다 |
| `airflow` 파드가 `CreateContainerConfigError` | Secret이 있는가 | `scripts/k8s-secrets.sh`를 돌린다. 멱등이다 |
| `airflow` 파드가 `ImagePullBackOff` | values 태그의 이미지가 발행됐는가 | 앱 결함이 아니다. 발행 워크플로를 본다 |

앞 세 행과 머지 행은
[docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 5단계·「운영 — 바꾸기·되돌리기」,
나머지 둘은 [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §8에서 옮겼다.

## 함정

### RollingSync는 자동 sync를 끈다

출처: [docs/argocd-gitops.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/argocd-gitops.md) §5 「D5 개정 기록」

앱 사이 순서를 선언으로 얻으려고 RollingSync를 골랐다. 대가로 생성된 Application의 자동 sync가 꺼진다.

- 컨트롤러가 `automated.enabled: false`를 덧붙이는 방식이라 `selfHeal`·`prune` 값은 그대로 남는다.
  `kubectl get`으로는 켜진 것처럼 보이므로 `enabled`를 함께 읽는다.
- git 변경은 ApplicationSet 컨트롤러가 sync한다. 라이브 drift를 되돌리는 selfHeal은 돌지 않는다.
- 이 대가는 수용했다. 재검토 조건은 같은 절에 적혀 있다.

### 설정 하나가 에러 없이 무시된다

출처: 같은 문서 §5 「앱 사이 — tier」

컨트롤러 파라미터 `applicationsetcontroller.enable.progressive.syncs`가 없으면 strategy가 **에러 없이 무시**된다.
그래서 `terraform test`가 렌더를 고정한다.

### 삭제가 번지지 않게 막은 겹

출처: [docs/architectures/argocd.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/argocd.md) 「데이터 보호 — 4겹」

재적재가 공짜가 아니므로 네 겹을 둔다. 설정 파일 삭제나 generator 0건이 Application 삭제로 번지지 않게
`applicationsSync: create-update`를 쓰고, 그래서 앱을 내릴 때는 설정 파일 삭제 뒤 Application을 손으로 지운다.
Spark·Flink 오퍼레이터는 CRD 어노테이션을 넣을 values 키가 없어 한 겹이 선언된 공백이다.
수용 근거는 [docs/argocd-gitops.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/argocd-gitops.md) §4·§9에 있다.
