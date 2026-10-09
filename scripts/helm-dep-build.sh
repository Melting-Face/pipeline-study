#!/usr/bin/env bash
# chart 의존성을 빌드한다 — 빈 helm 환경(CI 러너)에서도 동작하도록 repo 를 먼저 등록한다.
#
# 사용법: scripts/helm-dep-build.sh <chart-dir>
#
# 왜 필요한가: `helm dependency build` 는 Chart.yaml 의 `repository:` URL 이 로컬 helm
# repo 목록에 등록돼 있어야 한다. 개발 머신은 과거 `helm repo add` 이력이 있어 통과하지만,
# 깨끗한 CI 러너에는 repo 가 없어 "no repository definition" 으로 실패한다.
#
# 가정: Chart.yaml 의 dependencies 블록은 `repository: <url>` 을 한 줄에 하나씩 쓴다
# (yq 가 없어 grep/sed 로 읽는다). `https://` URL 만 등록하고 `file://`·`oci://`·`@alias`
# 는 건너뛴다. dependencies 가 없는 chart 는 아무것도 하지 않고 성공한다.
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "사용법: $0 <chart-dir>" >&2
    exit 2
fi

chart_dir="$1"
chart_yaml="${chart_dir}/Chart.yaml"

if [ ! -f "${chart_yaml}" ]; then
    echo "오류: ${chart_yaml} 이 없다" >&2
    exit 1
fi

# dependencies 가 없으면 no-op
if ! grep -q '^dependencies:' "${chart_yaml}"; then
    echo "의존성 없음, 건너뜀: ${chart_dir}"
    exit 0
fi

# repository URL 추출(중복 제거) — 주석·따옴표·공백 제거
urls="$(sed -n 's/^[[:space:]]*-\{0,1\}[[:space:]]*repository:[[:space:]]*//p' "${chart_yaml}" |
    sed -e 's/[[:space:]]*#.*$//' -e "s/[\"']//g" -e 's/[[:space:]]*$//' |
    grep '^https://' | sort -u || true)"

while IFS= read -r url; do
    [ -n "${url}" ] || continue
    # https://airflow.apache.org/ -> airflow-apache-org (호스트의 비영숫자를 '-' 로)
    name="$(printf '%s' "${url}" | sed -e 's#^https://##' -e 's#/.*$##' | tr -c 'A-Za-z0-9\n' '-' | sed 's/-*$//')"
    echo "--- helm repo add ${name} ${url} ---"
    helm repo add "${name}" "${url}" --force-update
done <<<"${urls}"

echo "--- helm dependency build ${chart_dir} ---"
helm dependency build "${chart_dir}"
