# Terraform / IaC 규칙 (도입)

> **상태**: 🔎 **도입(학습·확장 경로)**. 첫 스택은 **OCI Always Free A1 + k3s 부트스트랩**
> ([`terraform/oci-k3s/`](../../terraform/oci-k3s/README.md)). 결정 배경·대안 비교는
> [architectures/oci.md](../architectures/oci.md).
> 아래 규칙은 [docker.md](docker.md)·[k8s.md](k8s.md)의 원칙(버전 고정·비밀 참조·최소 개방)을
> **인프라 코드(IaC)** 로 옮긴 것이다.

## 1. 디렉터리·파일 구조 — 스택 단위로 분리

- 인프라는 **스택 단위 서브디렉터리** `terraform/<stack>/`에 둔다(예: `terraform/oci-k3s/`).
- 파일은 **역할별 표준 이름**으로 나눈다(추적성 — grep/점프 용이):
  - `versions.tf` — `required_version`·`required_providers`(버전 고정)
  - `provider.tf` — 프로바이더 설정(인증은 변수 참조)
  - `variables.tf` — 입력 변수(모두 `description`·`type`)
  - `outputs.tf` — 출력
  - 리소스는 **관심사별 파일**(`network.tf`·`compute.tf` …)로 분리
- 템플릿(cloud-init 등)은 `<name>.tftpl`로 두고 `templatefile()`로 렌더링한다.

## 2. 버전 고정 (latest 금지)

- `required_version`(예: `>= 1.5.0`)과 **프로바이더 버전을 `~>`로 핀**한다([docker.md](docker.md) §1-3 승계).
- **`.terraform.lock.hcl`은 커밋**한다(프로바이더 해시 고정 → 재현성). state·tfvars와 달리 **추적 대상**이다.
- 런타임 버전(k3s 등)도 변수로 **핀**하고 릴리스 페이지를 근거로 남긴다.

## 3. 포매터·검증 고정

- **`.tf` 포매터는 `terraform fmt`(2-space)로 고정**한다. Python(ruff)·SQL(sqlfluff)과 동일하게
  "언어의 정규 포매터를 고정"하는 원칙이며, 이 때문에 `.tf`는 **전역 4-space 규칙의 예외**다.
- 커밋 전 게이트: `terraform fmt -check -recursive` → `terraform validate`.
  자격증명이 필요한 `plan`/`apply`는 로컬에서 값 주입 후 실행한다.
- **templatefile 주의**: `.tftpl`에서 `$${...}`가 아닌 `${expr}`는 모두 보간식으로 평가된다.
  주석/문서 문자열에도 `${...}` 리터럴을 쓰지 말 것(파싱 실패). 쉘 변수는 브레이스 없는 `$VAR`로 쓴다.

## 4. 비밀·상태는 커밋 금지 (참조 주입)

- **커밋 금지**: `*.tfstate`(민감정보 평문 저장)·`terraform.tfvars`·API 개인키·회수 kubeconfig.
  `.gitignore`로 강제하고, 예시는 `terraform.tfvars.example`만 커밋한다.
- 인증·비밀값은 **변수/환경변수로 주입**한다(하드코딩 금지, [operations.md](../operations.md) 전파 원칙).
- 장기적으로 **원격 backend**(예: OCI Object Storage) + state 암호화를 검토한다([security.md](../security.md)).

## 5. 변수·기본값

- 모든 입력은 `variable`로 선언하고 `description`·`type`을 명시한다.
- 기본값은 **안전·무료 한도**를 향한다(예: A1 무료 최대 **2 OCPU/12 GB**, SSH/API는 좁힘 권장 주석).
  **과금으로 이어지는 상한은 `validation` 블록으로 막는다** —
  무료 한도는 사업자가 언제든 바꿀 수 있으므로,
  주석이 아니라 **실행 시점에 실패하는 검증**으로 두어야 오래된 기본값이 조용히 과금되지 않는다.

## 6. 프로비저닝은 선언적으로

- 부트스트랩은 **cloud-init(`user_data`)** 선언형을 우선한다(재현성·SSH 비의존). `remote-exec`는 지양한다.
- 다중 노드·복잡 구성이 필요해지면 Terraform(인프라) + Ansible(구성) 분리를 검토한다.

## 7. 네트워크·보안 최소 개방

- 인그레스는 **필요한 포트/소스만** 연다(SSH·API는 본인 IP/32 권장). 규칙은 [security.md](../security.md)·[k8s.md](k8s.md)와 정합.

## 참고

- Terraform 문서: https://developer.hashicorp.com/terraform/docs
- 스타일 가이드: https://developer.hashicorp.com/terraform/language/style
- `terraform fmt`: https://developer.hashicorp.com/terraform/cli/commands/fmt
- state 민감정보: https://developer.hashicorp.com/terraform/language/state/sensitive-data
- OCI Provider: https://registry.terraform.io/providers/oracle/oci/latest/docs
- cloud-init: https://cloudinit.readthedocs.io/
