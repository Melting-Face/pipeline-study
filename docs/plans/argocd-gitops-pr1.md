# ArgoCD GitOps PR1 — 오퍼레이터 층 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> ⚠️ 이 문서는 **PR1이 머지되면 삭제**한다(spec §8) — 낡는 실행 문서를 `docs/`에 남기지 않는다.

**Goal:** Terraform 스택 A·B로 `lakehouse-next` 클러스터에 ArgoCD를 세우고, 오퍼레이터 4종이 `git push`만으로 수렴하게
한다.

**Architecture:** 스택 A(`terraform/cluster/kind`)가 kind 클러스터와 전용 kubeconfig를, 스택 B(`terraform/platform`)가
ingress-nginx·argo-cd·appset(두 번째 `helm_release`)을 설치한다. ApplicationSet(List generator)이 `gitops/charts/<app>`
umbrella 차트 4개를 Application으로 펼친다. 구조·패턴은 `../argocd-study`에서 이식한다.

**Tech Stack:** Terraform 1.15.8 · tehcyx/kind 0.11.0 · hashicorp/helm 3.0.2 · hashicorp/kubernetes 2.38.0 ·
hashicorp/external 2.4.2 · Helm v4.2.0 · argo-cd 차트 10.9.6 · ingress-nginx 차트 4.15.1 · kind on podman(rootful)

**Spec:** [`docs/argocd-gitops.md`](../argocd-gitops.md) — 결정 D1~D10, §3 구조, §4 보호, §5 sync, §6
클러스터·kubeconfig, §7 검증.

## Global Constraints

- 한 리소스는 한 주인만(spec §3 소유 표). Terraform 스택은 `kubernetes_manifest`를 쓰지 않는다(plan 시점 CRD 해석 실패).
- Provider는 정확 고정(`required_version`만 `>=`), `.terraform.lock.hcl` 커밋, `*.tfstate`·`*.tfvars` 커밋 금지.
- provider `config_context`는 반드시 고정한다 — 비우면 현재 컨텍스트(기존 `lakehouse`)에 조용히 apply한다.
- 클러스터 기본값 `cluster_name = "lakehouse"`, 포트 8080/8443. **8081/8444는 validation으로 금지**(argocd-study 몫).
- 검증 실행값: `-var cluster_name=lakehouse-next -var http_host_port=8082 -var https_host_port=8445`.
- kubeconfig 기본 경로 `~/.kube/<cluster_name>.config`, 컨텍스트 `kind-<cluster_name>`.
- `var.target_revision` 기본 `main`, 검증 중엔 `-var`로만 브랜치를 넘긴다(tfvars 금지).
- `var.repo_url` 기본 `https://github.com/Melting-Face/pipeline-study.git`(https 검증).
- 차트 버전: cert-manager `v1.21.1`, cloudnative-pg `0.29.0`, spark-kubernetes-operator `1.8.0`,
  flink-kubernetes-operator `1.15.0`(저장소 `https://archive.apache.org/dist/flink/flink-kubernetes-operator-1.15.0/`).
- 오퍼레이터 자원값은 `terraform/lakehouse-platform/variables.tf`의 `operator_resources`를 그대로 옮긴다
  (spark 250m/512Mi·500m/1Gi, flink 200m/512Mi·500m/1Gi, flink_webhook 100m/256Mi·200m/512Mi, cnpg
  100m/200Mi·250m/384Mi).
- 모든 컨테이너에 requests/limits(conventions/k8s.md §2). 예외는 ingress-nginx 하나(기존 기록된 예외).
- 코드 주석 한국어, 셸 들여쓰기 4칸(`terraform fmt`·YAML 2칸은 예외), 문서는 `doc_lint` 0건.
- 커밋은 **사용자 요청 시에만**, Conventional Commits 한국어, worktree에선 `git commit -F <파일>`.

## Review Focus

1. **기존 `lakehouse`가 살아 있는 상태에서 `lakehouse-next` apply** — 기존 클러스터·`~/.kube/config`를 건드리지 않아야
   한다.
   → Task 8 Step 3에서 apply 전후 `kind get clusters`와 `~/.kube/config` sha256을 대조한다.
2. **`-var target_revision`을 빠뜨리고 apply** — `main`에 `gitops/charts/`가 없어 Application이 path 오류로 남는다.
   사람은 "ArgoCD가 안 된다"로 오독한다. → Task 8 Step 4가 Application의 `targetRevision`을 먼저 읽고 진행한다.
3. **스택 A·B의 `http_host_port` 불일치** — `argocd_url` 출력이 조용히 틀린 포트를 가리킨다.
   → Task 8 Step 4에서 `curl -fsS "$(terraform output -raw argocd_url)"`가 200인지 본다.
4. **`KUBECONFIG` 미설정 셸에서 스크립트 실행** — 기존 `~/.kube/config`의 클러스터를 조용히 조작한다.
   → Task 6의 컨텍스트 가드 음성 대조.
5. **레지스트리 컨테이너가 kind 네트워크에 안 붙은 채 클러스터 생성** — 이미지 pull이 `ErrImagePull`로만 보인다.
   → Task 1 Step 6에서 노드 안에서 `crictl pull localhost:5001/<기존 이미지>`가 성공하는지 본다.

---

### Task 1: 스택 A — kind 클러스터 + 전용 kubeconfig + 레지스트리 배선

**Files:**
- Create: `terraform/cluster/kind/{versions,variables,main,outputs}.tf`,
  `terraform/cluster/kind/scripts/detect-runtime.sh`,
  `terraform/cluster/kind/registry/hosts.toml`, `terraform/cluster/kind/tests/validation.tftest.hcl`,
  `terraform/cluster/kind/.terraform.lock.hcl`
- Source: `../argocd-study/terraform/cluster/kind/*` 이식

**Interfaces:**
- Produces: outputs `kubeconfig_path`(절대 경로), `kube_context`(`kind-<cluster_name>`), `ingress_profile`(`"kind"`),
  `storage_class`(`"standard"`). 변수 `cluster_name`·`http_host_port`·`https_host_port`·`kubeconfig_path`(기본 `null`).

- [ ] **Step 1: 실패하는 validation 테스트 작성** — `tests/validation.tftest.hcl`. argocd-study 테스트를 옮기되 금지
  포트를
  뒤집는다: `http_host_port = 8081` → `expect_failures = [var.http_host_port]`, `https_host_port = 8444` → 동일.
  추가 run `kubeconfig_default`: `cluster_name = "lakehouse-next"`, `command = plan`,
  `assert { condition = output.kubeconfig_path == pathexpand("~/.kube/lakehouse-next.config") }`,
  `assert { condition = output.kube_context == "kind-lakehouse-next" }`.
- [ ] **Step 2: 실패 확인** —
  `terraform -chdir=terraform/cluster/kind init && terraform -chdir=terraform/cluster/kind test`
  → 기대: 변수·출력 미정의로 FAIL.
- [ ] **Step 3: 구현** — argocd-study `main.tf`·`versions.tf`·`detect-runtime.sh`를 옮기고 다음을 바꾼다.
  - 변수 기본값 `cluster_name="lakehouse"`, 포트 8080/8443, validation은 `!contains([8081, 8444], ...)`.
  - `kubeconfig_path` 변수는 기본 `null`이다. `local.kubeconfig_path`가 그 값을 쓰되, 비었으면
    `~/.kube/${var.cluster_name}.config`로 채우고 `pathexpand`로 펼친다(`coalesce` 사용).
    `kind_cluster.kubeconfig_path`와 output이 이 local 하나를 함께 쓴다.
  - `kind_config`에 `containerd_config_patches`(현행 `k8s/kind-cluster.yaml`의
    `config_path = "/etc/containerd/certs.d"`)와
    node `extra_mounts { host_path = abspath("${path.module}/registry") container_path =
    "/etc/containerd/certs.d/localhost:5001" read_only = true }`.
  - `registry/hosts.toml` 내용: `[host."http://kind-registry:5000"]`(현행 `k8s-up.sh` heredoc과 동일).
  - `terraform_data "registry_network"`(`depends_on = [kind_cluster.this]`): local-exec로
    `podman network connect kind kind-registry`를 **이미 연결됐으면 건너뛰게**(현행 `k8s-up.sh`의
    `NetworkSettings.Networks.kind` 판정 그대로) 실행한다.
- [ ] **Step 4: 테스트 통과** — `terraform -chdir=terraform/cluster/kind test` → 기대: 전 run PASS.
  `terraform fmt -check` 0건.
- [ ] **Step 5: 락 파일 생성** —
  `terraform -chdir=terraform/cluster/kind providers lock -platform=darwin_arm64 -platform=linux_amd64`.
- [ ] **Step 6: 커밋**(사용자 요청 시) — `feat(terraform): kind 클러스터 스택 A를 추가한다`.
  레지스트리 pull 검증(Review Focus 5)은 실클러스터가 필요해 Task 8 Step 3에서 한다.

### Task 2: 스택 B — ingress-nginx · argo-cd · appset

**Files:**
- Create: `terraform/platform/{versions,provider,variables,ingress,argocd,outputs}.tf`,
  `terraform/platform/values/{argocd.yaml.tftpl,ingress-nginx.kind.yaml}`, `terraform/platform/charts/appset/**`,
  `terraform/platform/tests/validation.tftest.hcl`, `terraform/platform/.terraform.lock.hcl`
- Source: `../argocd-study/terraform/platform/*` 이식(`ingress-nginx.loadbalancer.yaml`·`local.auto.tfvars*`는 옮기지
  않는다)

**Interfaces:**
- Consumes: Task 1 output과 같은 이름·값의 변수 `kubeconfig_path`·`kube_context`·`ingress_profile`·`http_host_port`
  (기본값 `~/.kube/lakehouse.config`·`kind-lakehouse`·`kind`·`8080`).
- Produces: `var.apps`(list of `{name, path, namespace}`), `var.target_revision`, outputs `argocd_url`·
  `argocd_initial_admin_password_command`. ApplicationSet 이름 `apps`(ns `argocd`).

- [ ] **Step 1: 실패하는 테스트 작성**
  - `tests/validation.tftest.hcl`: argocd-study 것을 옮기고 기본 `apps` 4개를 기대값으로 —
    `assert { condition = length(var.apps) == 4 }`, `[for a in var.apps : a.name] ==
    ["cert-manager", "cnpg-operator", "spark-operator", "flink-operator"]`. 음성: path `k8s/foo` → `expect_failures =
    [var.apps]`.
  - `charts/appset/tests/render.test.sh`: argocd-study 것을 옮기되 ruby 대신
    `uv run --with pyyaml python -c`로 요약하고 다음 단언을 더한다 —
    `spec.syncPolicy.preserveResourcesOnDeletion == true`,
    template `syncOptions` ⊇ {`CreateNamespace=true`,`ServerSideApply=true`,`SkipDryRunOnMissingResource=true`},
    `retry.limit == 10`, `retry.backoff == {duration: 10s, factor: 2, maxDuration: 3m}`,
    `--set targetRevision=feat/x`일 때 렌더된 `targetRevision == "feat/x"`.
- [ ] **Step 2: 실패 확인** — `bash terraform/platform/charts/appset/tests/render.test.sh` → 새 단언 FAIL.
- [ ] **Step 3: 구현**
  - `applicationset.yaml`: spec §5 syncPolicy 블록(블록 스타일)과 `spec.syncPolicy.preserveResourcesOnDeletion: true`.
    `targetRevision`은 values에서(`argocd.tf`가 `var.target_revision`을 넘긴다). 주석에 `{{ }}` 금지.
  - `argocd.yaml.tftpl`: argocd-study 값 + `dex.enabled: false`, `notifications.enabled: false`,
    5개 컴포넌트(`controller`·`repoServer`·`server`·`applicationSet`·`redis`)의 `resources`
    — 값은 `helm show values argo/argo-cd --version 10.9.6`로 기본값을 확인한 뒤 정하고, 근거를 주석으로 남긴다.
  - `variables.tf`: `http_host_port` 기본 8080, `target_revision`(string, 기본 `"main"`, 빈 문자열 금지 validation).
  - `provider.tf`: helm provider만(`kubernetes` 블록에
    `config_path = pathexpand(var.kubeconfig_path)`·`config_context`).
- [ ] **Step 4: 테스트 통과** — `bash .../render.test.sh` 전부 PASS, `terraform -chdir=terraform/platform test` PASS.
- [ ] **Step 5: 락 파일** — Task 1 Step 5와 같은 명령을 `terraform/platform`에.
- [ ] **Step 6: 커밋**(사용자 요청 시) — `feat(terraform): ArgoCD 플랫폼 스택 B를 추가한다`.

### Task 3: gitops 차트 검사기 + `cert-manager` 차트

**Files:**
- Create: `scripts/helm-dep-build.sh`(argocd-study 이식), `scripts/gitops-charts-check.sh`,
  `gitops/charts/cert-manager/{Chart.yaml,Chart.lock,values.yaml,templates/local-ca.yaml}`
- Modify: `.gitignore`(+`gitops/charts/*/charts/`)
- Move: `k8s/local-ca.yaml` → `gitops/charts/cert-manager/templates/local-ca.yaml`(각 문서에 sync-wave 주석 추가)

**Interfaces:**
- Produces: `scripts/gitops-charts-check.sh [chart-dir...]` — 인자가 없으면 `gitops/charts/*` 전부.
  차트마다 `helm-dep-build.sh` → `helm template <app명> <dir> -n <ns>` 후 아래 단언. 실패 시 exit 1, 마지막 줄
  `검사한 차트: N개`(0개면 exit 1). Task 4·5가 이 스크립트를 쓴다.

- [ ] **Step 1: 실패하는 검사 작성** — `gitops-charts-check.sh`의 단언(렌더 결과 YAML을 `uv run --with pyyaml`로 파싱):
  - (a) 모든 Pod 템플릿 컨테이너(`initContainers` 포함)에 `resources.requests`와 `resources.limits`가 있다.
  - (b) 모든 `CustomResourceDefinition`에 `helm.sh/resource-policy: keep` 또는
    `argocd.argoproj.io/sync-options`에 `Delete=false`가 있다(spec §4 겹 3).
  - (c) 차트별 기대 파일 `gitops/charts/<app>/tests/expect.yaml`이 있으면 그 단언도 본다
    (`kinds_with_wave: {Issuer: "1", Certificate: "1"}` 형식 — 해당 kind 전부가 그 wave 주석을 갖는다).
  - cert-manager 기대: `tests/expect.yaml`에 Issuer·Certificate wave `"1"`.
- [ ] **Step 2: 실패 확인** — `scripts/gitops-charts-check.sh gitops/charts/cert-manager` → 차트 없음으로 FAIL.
- [ ] **Step 3: 구현** — `Chart.yaml` dependency `cert-manager` `v1.21.1` `https://charts.jetstack.io`.
  `values.yaml`의 `cert-manager:` 아래 `crds.enabled: true`, `crds.keep: true`(기본값이지만 명시),
  컨트롤러·webhook·cainjector·startupapicheck에 resources. `local-ca.yaml` 각 문서에
  `argocd.argoproj.io/sync-wave: "1"`. `helm dependency build`로 `Chart.lock` 생성.
- [ ] **Step 4: 통과 확인** — 같은 명령 → `검사한 차트: 1개`, exit 0.
- [ ] **Step 5: 음성 대조** — 임시로 `crds.keep: false`로 바꿔 (b)가 FAIL하는지, local-ca 한 문서의 wave를 지워 (c)가
  FAIL하는지 본 뒤 되돌린다(되돌린 뒤 `git diff --stat`이 비었는지 확인).
- [ ] **Step 6: 커밋**(사용자 요청 시) — `feat(gitops): cert-manager 차트와 검사기를 추가한다`.

### Task 4: `cnpg-operator` · `spark-operator` · `flink-operator` 차트

**Files:**
- Create: `gitops/charts/{cnpg-operator,spark-operator,flink-operator}/{Chart.yaml,Chart.lock,values.yaml}`
- Move: `k8s/spark/spark-workload-cleanup-rbac.yaml` → `gitops/charts/spark-operator/templates/`,
  `k8s/flink/flink-operator-webhook-rbac.yaml`·`flink-workload-rbac.yaml` → `gitops/charts/flink-operator/templates/`,
  `k8s/flink/operator-values.yaml` 내용 → `gitops/charts/flink-operator/values.yaml`의 `flink-kubernetes-operator:` 아래
- Create: `gitops/charts/flink-operator/tests/expect.yaml`

**Interfaces:**
- Consumes: Task 3 `scripts/gitops-charts-check.sh`.

- [ ] **Step 1: 기대값 작성** — `flink-operator/tests/expect.yaml`에 `service_accounts: [flink-operator]`
  (webhook RBAC가 `flink-operator/flink-operator` SA를 참조 — Application 이름이 helm 릴리스 이름이 되어 SA 이름이
  바뀌면 RBAC가 조용히 빗나간다). 검사기 (c)에 `service_accounts` 키 지원을 더한다.
- [ ] **Step 2: 실패 확인** —
  `scripts/gitops-charts-check.sh gitops/charts/{cnpg-operator,spark-operator,flink-operator}` → FAIL.
- [ ] **Step 3: 구현** — 버전·저장소는 Global Constraints. values는 `terraform/lakehouse-platform/operators.tf`의
  `set`을
  values 트리로 옮긴다(spark: `workloadResources.namespaces.create=false`·`data=[default]`·`serviceAccount.name=spark`,
  flink: `watchNamespaces=[default]`, 자원값 그대로).
  Spark·Flink CRD에 keep이 없어 (b)가 FAIL하면 분기는 둘뿐이다.
  ① `helm show values`에 CRD 주석을 넣는 키가 있으면 그 키로 `helm.sh/resource-policy: keep`을 단다.
  ② 없으면 **이 Step에서 멈추고 보고한다**. 업스트림 CRD를 복사해 직접 소유하는 것은 spec 범위 밖이다.
- [ ] **Step 4: 통과 확인** — 위 명령 → `검사한 차트: 3개`. 이어서 인자 없이 → `검사한 차트: 4개`.
- [ ] **Step 5: 커밋**(사용자 요청 시) — `feat(gitops): 오퍼레이터 차트 3종을 추가한다`.

### Task 5: CI — helm 스텝 · Terraform 재귀 탐색 · tftest

**Files:**
- Modify: `.github/workflows/ci.yml`(lint 잡, terraform 잡)

- [ ] **Step 1: 구현**
  - `env.HELM_VERSION: "v4.2.0"`. lint 잡에 "helm 설치" 스텝 — `get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz`와
    `.sha256sum`을 받아 `sha256sum -c`로 대조 후 `/usr/local/bin`(terraform 설치 스텝과 같은 형태).
  - lint 잡 마지막에 `scripts/gitops-charts-check.sh`와 `bash terraform/platform/charts/appset/tests/render.test.sh`
    (`astral-sh/setup-uv` 스텝 추가 — 검사기가 `uv run`을 쓴다).
  - terraform 잡 루프를 `find terraform -name versions.tf -not -path '*/.terraform/*'`로 바꾸고,
    `tests/` 디렉터리가 있는 스택은 `terraform test`도 돈다. 마지막 줄 `검증한 스택: N개`는 유지.
- [ ] **Step 2: 로컬 대조** — 루프 본문을 로컬 셸에서 그대로 실행 → `검증한 스택: 3개`
  (`cluster/kind`·`platform`·`oci-k3s`, `lakehouse-platform`은 Task 6에서 삭제된 뒤 기준).
  `actionlint`가 있으면 `.github/workflows/ci.yml`에 0건.
- [ ] **Step 3: 커밋**(사용자 요청 시) — `ci: helm 차트 검사와 terraform 스택 재귀 탐색을 추가한다`.
  CI 실통과 확인은 PR을 연 뒤 Task 8 Step 7.

### Task 6: 철거 · 스크립트 축소 · 컨텍스트 가드

**Files:**
- Delete: `terraform/lakehouse-platform/`, `scripts/k8s-operators.sh`, `scripts/k8s-dagster.sh`, `k8s/dagster/`,
  `k8s/kind-cluster.yaml`(스택 A로 대체), Task 3·4가 옮긴 원본(`k8s/local-ca.yaml` 등 — `git mv`였으면 이미 없음)
- Modify: `scripts/k8s-env.sh`, `scripts/k8s-up.sh`

**Interfaces:**
- Produces: `k8s-env.sh`를 source하면 `KUBECONFIG`가 export되고 `require_cluster_context` 함수가 정의된다.
  기존 `ensure_cert_manager` 등 cert-manager·Barman 함수는 삭제(차트로 이관).

- [ ] **Step 1: 실패하는 음성 대조 작성** — `scripts/tests/k8s-env-guard.sh`:
  `env -u KUBECONFIG HOME=<임시 디렉터리> bash -c 'source scripts/k8s-env.sh && require_cluster_context'`가
  **0이 아닌 코드**로 끝나고 stderr에 `kind-lakehouse`가 나오는지 단언. 대조군: 임시 HOME에
  `current-context: kind-lakehouse`인 최소 kubeconfig를 `.kube/lakehouse.config`로 두면 0으로 끝나야 한다.
- [ ] **Step 2: 실패 확인** — `bash scripts/tests/k8s-env-guard.sh` → `require_cluster_context` 미정의로 FAIL.
- [ ] **Step 3: 구현** — `k8s-env.sh`: `export KUBECONFIG="${KUBECONFIG_PATH:-$HOME/.kube/${CLUSTER_NAME}.config}"`,
  `require_cluster_context()`는 `kubectl config current-context`가 `kind-${CLUSTER_NAME}`이 아니면 이유를 출력하고 1을
  반환.
  `k8s-up.sh`: podman 머신 + 레지스트리 컨테이너(3상태 처리 유지)만 남기고 kind 생성·certs.d·network connect·
  ConfigMap·ingress-nginx 단계를 삭제한다. `local-registry-hosting` ConfigMap은 저장소에서 읽는 곳이 없어 옮기지 않는다
  (삭제 전 `grep -rn local-registry-hosting`이 `k8s-up.sh`만 내는지 확인).
- [ ] **Step 4: 통과 확인** — `bash scripts/tests/k8s-env-guard.sh` PASS. `shellcheck scripts/*.sh` 0건.
- [ ] **Step 5: 죽은 참조 탐색** —
  `grep -rn "lakehouse-platform\|k8s-operators.sh\|k8s-dagster.sh\|k8s/dagster\|kind-cluster.yaml"`
  를 저장소 전체(`.git` 제외)에 돌려 **0건이 될 때까지** Task 7에서 고칠 목록으로 넘긴다. 대조군으로 같은 명령에
  `argocd-gitops`를 넣어 1건 이상이 나오는지 함께 센다(grep 경로 생존).
- [ ] **Step 6: 커밋**(사용자 요청 시) — `refactor(k8s)!: 스크립트·Terraform 배포 경로를 ArgoCD로 넘긴다`
  (본문에 `BREAKING CHANGE: 기존 부트스트랩 6단계와 Dagster k8s 배포가 사라진다`).

### Task 7: 문서 단일 출처 갱신

**Files:**
- Modify: `docs/setup.md` §3, `docs/architectures/terraform.md`, `docs/conventions/k8s.md` §7,
  `docs/resource-sizing.md`,
  `docs/README.md`, `docs/architectures/README.md`, `CLAUDE.md`(인프라 요약의 기동 순서 줄), `AGENTS.md`, Task 6 Step 5
  목록
- Create: `docs/architectures/argocd.md`(채택 ✅ — D5 근거, 기각 대안, 출처는 spec 참고 출처 재인용)

- [ ] **Step 1: 구현** — setup.md §3을 spec §6 부트스트랩 5줄로 교체. terraform.md 스택 표를 A(cluster)·B(platform)로
  다시 쓰고 "셸 유지" 결정을 뒤집은 근거(spec §1)를 남긴다. resource-sizing.md 표에서 Dagster 2행 삭제,
  ArgoCD 5행 추가(Task 2 값). CLAUDE.md는 **줄 수를 늘리지 않는다**(기동 순서 문장을 교체만).
- [ ] **Step 2: 검증** — `uv run scripts/doc_lint.py` 위반 0건. Task 6 Step 5 grep 0건(대조군 1건 이상).
- [ ] **Step 3: 커밋**(사용자 요청 시) — `docs: ArgoCD 부트스트랩으로 문서를 갱신한다`.

### Task 8: 실클러스터 관문 ①②③ (비가역 — 승인 필요)

> 🔴 Step 2~3은 비가역이다. 실행 전 `reviewer` 보안 체크리스트 1회 + **사용자 승인**(risk.md §4). 승인 없이 진행하지
> 않는다.

- [ ] **Step 1: 사전 기록** — `date`, `kind get clusters`, `sha256sum ~/.kube/config`, `podman machine inspect` 자원을
  볼트에 박제.
- [ ] **Step 2: argocd-study 클러스터 내리기**(승인 후) —
  `terraform -chdir=../argocd-study/terraform/cluster/kind destroy`.
- [ ] **Step 3: 스택 A apply**(승인 후) — Global Constraints의 검증 실행값으로 apply. 기대:
  `kind get clusters`에 `lakehouse`와 `lakehouse-next`가 함께 있고 `~/.kube/config` sha256이 Step 1과 같다(Review Focus
  1).
  노드에서 `podman exec lakehouse-next-control-plane crictl pull localhost:5001/spark-runner:0.5.0` 성공(Review Focus
  5).
- [ ] **Step 4: 스택 B apply** — 같은 kubeconfig·context 변수 +
  `-var http_host_port=8082 -var target_revision=<PR1 브랜치>`.
  기대: `kubectl -n argocd get applicationset apps -o jsonpath='{.spec.template.spec.source.targetRevision}'`가
  브랜치명(Review Focus 2), `curl -fsS "$(terraform -chdir=terraform/platform output -raw argocd_url)"` 200(Review Focus
  3).
- [ ] **Step 5: 관문 ①** — `kubectl -n argocd get applications --no-headers | wc -l` == 4 **이고** 전부
  `Synced`·`Healthy`.
  소요 시간(apply 끝 → 마지막 Healthy)을 볼트에 기록. 이 수치는 Application **객체 수**를 센다.
- [ ] **Step 6: 관문 ②③**
  - ② `kubectl -n cnpg-system scale deploy -l app.kubernetes.io/name=cloudnative-pg --replicas=0` → 원래 replica로
    복원되는지.
  - ③ `var.apps`에서 `spark-operator`를 뺀 `-var-file`(임시, 커밋 금지)로 스택 B apply → Application은 사라지고
    `spark-operator` ns의 Deployment와 Spark CRD가 **남는지**. 결과(겹 1이 List 원소 제거에 적용되는가)를 spec §4에
    반영할
    문장으로 기록한 뒤 기본 `var.apps`로 다시 apply해 4개로 복귀.
- [ ] **Step 7: PR** — `gh pr create`(head 브랜치 명시, upstream이 `origin/main`이 아닌지 먼저 확인). CI는 PR head SHA의
  run으로 통과를 판정한다. `lakehouse-next`는 PR2 검증을 위해 **유지**한다.

---

## Self-Review 기록

- spec 대응: §3 구조 → Task 1·2·3·4, §3 철거표(①) → Task 6, §4 겹 1·2·3 → Task 2(appset)·Task 3(b)·Task 8 ③,
  §5 sync·wave·리비전 → Task 2·3, §6 클러스터·kubeconfig·가드 → Task 1·6, §7 정적 게이트·음성 대조 ⓐⓑⓒ →
  Task 2·3·5·6, 관문 ①②③ → Task 8, 자원 → Task 2·7, §8 문서 → Task 7. 관문 ⓪·④와 겹 2·Secret은 PR2 범위.
- Task 4 Step 3은 Spark·Flink CRD keep이 미확인(spec §4)이라 분기를 남겼다 — 차트가 지원하지 않으면 멈추고 보고한다.
