#!/usr/bin/env bash
# 클러스터 Secret 생성(없을 때만) — ArgoCD 밖에 두는 유일한 층(비밀값은 Git에 두지 않는다)
# 사용: ./scripts/k8s-secrets.sh        (클러스터 생성 후, terraform/platform apply 전후 어느 때든 멱등)
#   lakehouse-creds  : S3 접속 키 — Spark·Flink 파드가 읽는다. 값은 `.env`의 ICEBERG_S3_* (storage-up.sh와 한 벌)
#   catalog-pg-app   : 카탈로그 Postgres 계정 — CNPG `bootstrap.initdb.secret`·`managed.roles`가 읽는다
#   physionet-creds  : 원천 다운로드 계정(값이 있을 때만)
# 카탈로그 Cluster CR 자체는 ArgoCD 앱 gitops/charts/catalog-postgres 가 만든다. Secret이 늦으면 CNPG가 기다린다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/k8s-env.sh
source "${SCRIPT_DIR}/k8s-env.sh"
ENV_FILE="${REPO_ROOT}/.env"
CR_FILE="${REPO_ROOT}/gitops/charts/catalog-postgres/templates/cluster.yaml"

# `.env`에서 값 하나를 읽는다(source 하지 않는다 — compose env 파일은 셸 문법이 아니다). 값은 출력하지 않는다.
env_value() {
    local raw
    [ -f "${ENV_FILE}" ] || return 0
    raw="$(grep -E "^$1=" "${ENV_FILE}" | tail -n 1 | cut -d= -f2-)"
    raw="${raw%%[[:space:]]#*}"
    raw="${raw%"${raw##*[![:space:]]}"}"
    raw="${raw#\"}"
    printf '%s' "${raw%\"}"
}

# S3 키 — 단일 출처는 `.env`의 ICEBERG_S3_*(compose SeaweedFS의 s3.json도 이 값으로 만든다).
# 기본값을 두지 않는다: 아무 값이나 넣으면 Secret은 생기고 파드는 뜨는데 `load_table`에서
# ACCESS_DENIED로 죽는 부분 성공이 된다. 환경변수로 넘기면 그 값이 이긴다.
S3_ACCESS_KEY="${S3_ACCESS_KEY:-$(env_value ICEBERG_S3_ACCESS_KEY)}"
S3_SECRET_KEY="${S3_SECRET_KEY:-$(env_value ICEBERG_S3_SECRET_KEY)}"
# 로컬 PoC 카탈로그 계정(placeholder — 실값은 env로 주입). 주석은 gitleaks 문법이다.
# PG_USER는 Cluster CR의 `bootstrap.initdb.owner`와 **반드시 같아야 한다**(아래 가드).
PG_USER="${PG_USER:-iceberg}"
PG_PASSWORD="${PG_PASSWORD:-iceberg-local}"           # gitleaks:allow

# 0) 가드 — 파일만 읽으므로 클러스터 접속 전에 돈다(멈춰도 만들어 둔 것이 없다).
[ -n "${S3_ACCESS_KEY}" ] && [ -n "${S3_SECRET_KEY}" ] || {
    printf 'S3 키가 비었다 — .env 의 ICEBERG_S3_ACCESS_KEY/ICEBERG_S3_SECRET_KEY 를 채운다\n' >&2
    exit 1
}
# CNPG는 시크릿의 username과 CR의 owner가 같아야 한다. owner는 CR의 리터럴이라 PG_USER override와
# 자동으로 맞지 않는다 → 적용 전에 막는다.
CR_OWNER="$(awk '/^ *owner:/ {print $2; exit}' "${CR_FILE}")"
if [ "${CR_OWNER}" != "${PG_USER}" ]; then
    printf 'PG_USER(%s) != %s 의 owner(%s)\n' "${PG_USER}" "${CR_FILE#"${REPO_ROOT}"/}" "${CR_OWNER}" >&2
    printf 'CNPG bootstrap이 정의되지 않은 동작에 빠진다. 둘을 맞춘 뒤 다시 실행하라.\n' >&2
    exit 1
fi

require_cli kubectl
require_cluster_context

# Secret 하나를 **없을 때만** 만든다(get || create — docs/argocd-gitops.md §8).
# 있으면 건드리지 않는다: 덮어쓰기(apply)는 재실행마다 값을 바꿔 **의도하지 않은 회전**이 되고,
# 회전은 Secret·DB 롤·`.env`·워크로드 재기동을 한 벌로 해야 한다. 바꾸려면 지우고 다시 돌린다.
ensure_secret() {
    local name="$1"
    shift
    if kubectl -n default get secret "${name}" >/dev/null 2>&1; then
        log "Secret 있음(건드리지 않음): ${name}"
        return 0
    fi
    log "Secret 생성: ${name}"
    kubectl -n default create secret generic "${name}" "$@" >/dev/null
}

# 1) 용도별로 **분리**한다. 같은 비밀번호를 두 시크릿에 중복 보관하지 않는다
#    ("값이 갈려 부분 성공하는" 드리프트의 씨앗). PG 크리덴셜의 in-cluster 단일 출처는 catalog-pg-app.
ensure_secret lakehouse-creds \
    --from-literal=s3-access-key="${S3_ACCESS_KEY}" \
    --from-literal=s3-secret-key="${S3_SECRET_KEY}"

ensure_secret catalog-pg-app --type=kubernetes.io/basic-auth \
    --from-literal=username="${PG_USER}" \
    --from-literal=password="${PG_PASSWORD}"

# 2) PhysioNet credentialed 원천 접근 (수집 자산 — defs/<dataset>/raw_assets.py).
#    기본값을 두지 않는다 — 외부 기관의 개인 계정이라 아무 값이나 넣으면 수집에서 401로 죽는 부분 성공이 된다.
#    전용 Secret으로 분리한다(회전 주기를 우리가 통제하지 못한다).
if [ -n "${PHYSIONET_USERNAME:-}" ] && [ -n "${PHYSIONET_PASSWORD:-}" ]; then
    ensure_secret physionet-creds --type=kubernetes.io/basic-auth \
        --from-literal=username="${PHYSIONET_USERNAME}" \
        --from-literal=password="${PHYSIONET_PASSWORD}"
else
    log "Secret 건너뜀: physionet-creds (PHYSIONET_USERNAME/PASSWORD 미설정)"
fi

log "완료. 카탈로그 Cluster는 ArgoCD(catalog-postgres)가, S3는 클러스터 밖 storage-up.sh가 맡는다"
