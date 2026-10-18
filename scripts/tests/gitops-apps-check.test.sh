#!/usr/bin/env bash
# scripts/gitops-apps-check.sh 의 음성·양성 대조 — 임시 디렉터리에 설정 파일을 만들어 넘긴다.
# 음성 셀은 **기대한 FAIL 문구가 출력에 있는지**까지 본다(종료코드 1만 보면 다른 이유의 실패도 통과로 읽힌다).
# 양성 셀이 함께 있어야 음성 셀의 실패가 「검사기가 원래 다 실패한다」와 갈린다.
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
checker="${root}/scripts/gitops-apps-check.sh"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

failed=0
cells=0

# case_dir <셀 이름> — 셀마다 빈 apps 디렉터리
case_dir() {
    mkdir -p "${work}/$1"
    echo "${work}/$1"
}

# write <dir> <app> <본문>
write() {
    mkdir -p "$1/$2"
    printf '%s\n' "$3" >"$1/$2/config.yaml"
}

# expect_fail <셀 이름> <dir> <출력에 있어야 할 문구>
expect_fail() {
    cells=$((cells + 1))
    if out="$("${checker}" "$2" 2>&1)"; then
        echo "FAIL [$1] 통과했다(실패해야 한다)"
        failed=1
    elif ! grep -qF -- "$3" <<<"${out}"; then
        echo "FAIL [$1] 실패했지만 기대 문구 '$3' 가 없다:"
        echo "${out}"
        failed=1
    else
        echo "PASS [$1]"
    fi
}

# expect_pass <셀 이름> <dir>
expect_pass() {
    cells=$((cells + 1))
    if out="$("${checker}" "$2" 2>&1)"; then
        echo "PASS [$1]"
    else
        echo "FAIL [$1] 실패했다(통과해야 한다):"
        echo "${out}"
        failed=1
    fi
}

ok_umbrella='name: cnpg-operator
namespace: cnpg-system
tier: "0"
source:
  type: umbrella
  path: gitops/charts/cnpg-operator'

# 양성: umbrella 1개 + upstream 1개(values.yaml 동반)
d="$(case_dir positive)"
write "${d}" cnpg-operator "${ok_umbrella}"
write "${d}" trino 'name: trino
namespace: trino
tier: "2"
source:
  type: upstream
  repoURL: https://trinodb.github.io/charts
  chart: trino
  targetRevision: 1.2.3'
touch "${d}/trino/values.yaml"
expect_pass "양성 umbrella+upstream" "${d}"

d="$(case_dir empty)"
expect_fail "빈 디렉터리" "${d}" "검사한 앱: 0개"

d="$(case_dir missing-key)"
write "${d}" cnpg-operator 'name: cnpg-operator
namespace: cnpg-system
source:
  type: umbrella
  path: gitops/charts/cnpg-operator'
expect_fail "(a) 필수 키 누락" "${d}" "(a) 필수 키 tier 없음"

d="$(case_dir unknown-key)"
write "${d}" cnpg-operator "${ok_umbrella}
wave: 1"
expect_fail "(a) 알 수 없는 키" "${d}" "(a) 알 수 없는 키 wave"

# path 는 files generator 가 설정 파일 위치(객체)로 주입하는 예약 파라미터 — 최상위에 두면 충돌한다
d="$(case_dir reserved-path)"
write "${d}" cnpg-operator "${ok_umbrella}
path: gitops/charts/cnpg-operator"
expect_fail "(a) 예약 키 path" "${d}" "(a) 알 수 없는 키 path"

d="$(case_dir name-mismatch)"
write "${d}" cnpg-operator "${ok_umbrella/name: cnpg-operator/name: cnpgoperator}"
expect_fail "(b) 디렉터리와 다른 name" "${d}" "(b) name 'cnpgoperator' 이 디렉터리 이름과 다르다"

d="$(case_dir bad-name)"
write "${d}" Bad_App "${ok_umbrella/name: cnpg-operator/name: Bad_App}"
expect_fail "(b) DNS-1123 아닌 name" "${d}" "(b) name 'Bad_App' 이 DNS-1123 label 이 아니다"

d="$(case_dir int-tier)"
write "${d}" cnpg-operator "${ok_umbrella/tier: \"0\"/tier: 0}"
expect_fail "(d) 숫자 tier" "${d}" "(d) tier 0 는"

d="$(case_dir bad-type)"
write "${d}" cnpg-operator "${ok_umbrella/type: umbrella/type: kustomize}"
expect_fail "(e) 알 수 없는 source.type" "${d}" "(e) source.type 'kustomize'"

d="$(case_dir outside-path)"
write "${d}" cnpg-operator "${ok_umbrella/path: gitops\/charts\/cnpg-operator/path: k8s/spark}"
expect_fail "(e) gitops/charts 밖 경로" "${d}" "가 gitops/charts/ 로 시작하지 않는다"

d="$(case_dir missing-chart)"
write "${d}" cnpg-operator "${ok_umbrella/path: gitops\/charts\/cnpg-operator/path: gitops/charts/no-such}"
expect_fail "(e) Chart.yaml 없는 경로" "${d}" "gitops/charts/no-such/Chart.yaml 이 없다"

d="$(case_dir range-version)"
write "${d}" trino 'name: trino
namespace: trino
tier: "2"
source:
  type: upstream
  repoURL: https://trinodb.github.io/charts
  chart: trino
  targetRevision: ">=1.0.0"'
touch "${d}/trino/values.yaml"
expect_fail "(e) 범위 버전" "${d}" "는 정확 버전이어야 한다"

d="$(case_dir no-values)"
write "${d}" trino 'name: trino
namespace: trino
tier: "2"
source:
  type: upstream
  repoURL: https://trinodb.github.io/charts
  chart: trino
  targetRevision: 1.2.3'
expect_fail "(e) upstream values.yaml 없음" "${d}" "values.yaml 이 있어야 한다"

d="$(case_dir dup-chart)"
write "${d}" cnpg-operator "${ok_umbrella}"
write "${d}" cnpg-operator-2 "${ok_umbrella/name: cnpg-operator/name: cnpg-operator-2}"
expect_fail "(f) 같은 차트 이중 참조" "${d}" "(f) gitops/charts/cnpg-operator 를"

echo "셀: ${cells}개"
exit "${failed}"
