#!/usr/bin/env bash
# 클러스터 Secret 생성(없을 때만) — ArgoCD 밖에 두는 유일한 층(비밀값은 Git에 두지 않는다)
# 사용: ./scripts/k8s-secrets.sh        (클러스터 생성 후, terraform/platform apply 전후 어느 때든 멱등)
#   lakehouse-creds  : S3 접속 키 — Spark·Flink 파드가 읽는다. 값은 `.env`의 ICEBERG_S3_* (스택 A의 TF_VAR_s3_*와 한 벌)
#   catalog-pg-app   : 카탈로그 Postgres 계정 — CNPG `bootstrap.initdb.secret`·`managed.roles`가 읽는다
#   physionet-creds  : 원천 다운로드 계정(값이 있을 때만)
#   airflow 네임스페이스 Secret 7종 + default/airflow-pg-app : Airflow 메타 DB·브로커·웹 서버 비밀(랜덤 생성)
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

# S3 키 — 단일 출처는 `.env`의 ICEBERG_S3_*(스택 A가 TF_VAR_s3_*로 같은 값을 받는다).
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

require_cli kubectl openssl jq
require_cluster_context

# Secret 하나를 **없을 때만** 만든다(get || create — docs/argocd-gitops.md §8).
# 있으면 건드리지 않는다: 덮어쓰기(apply)는 재실행마다 값을 바꿔 **의도하지 않은 회전**이 되고,
# 회전은 Secret·DB 롤·`.env`·워크로드 재기동을 한 벌로 해야 한다. 바꾸려면 지우고 다시 돌린다.
# 첫 인자는 네임스페이스다.
ensure_secret() {
    local ns="$1" name="$2"
    shift 2
    if kubectl -n "${ns}" get secret "${name}" >/dev/null 2>&1; then
        log "Secret 있음(건드리지 않음): ${ns}/${name}"
        return 0
    fi
    log "Secret 생성: ${ns}/${name}"
    kubectl -n "${ns}" create secret generic "${name}" "$@" >/dev/null
}

# 한 값에서 파생되는 Secret 쌍의 존재를 판정한다: 둘 다 있음 → 0 / 둘 다 없음 → 1 / 반쪽 → 메시지 후 exit 1.
# 반쪽이면 **쌍의 어느 쪽도 만들기 전에** 멈춘다 — 한쪽만 새 값으로 만들면 서로 다른 비밀번호가 돼
# 나열은 되고 인증에서 죽는 부분 성공이 된다. (호출부가 `if`로 받으므로 반쪽은 exit로 끊는다)
ensure_secret_pair() {
    local ns1="$1" name1="$2" ns2="$3" name2="$4" has1=0 has2=0
    kubectl -n "${ns1}" get secret "${name1}" >/dev/null 2>&1 && has1=1
    kubectl -n "${ns2}" get secret "${name2}" >/dev/null 2>&1 && has2=1
    if [ "${has1}" -eq 1 ] && [ "${has2}" -eq 1 ]; then
        log "Secret 쌍 있음(건드리지 않음): ${name1} / ${name2}"
        return 0
    fi
    if [ "${has1}" -eq 0 ] && [ "${has2}" -eq 0 ]; then
        return 1
    fi
    if [ "${has1}" -eq 1 ]; then
        printf '반쪽 Secret 쌍: %s 있음 / %s 없음\n' "${name1}" "${name2}" >&2
    else
        printf '반쪽 Secret 쌍: %s 있음 / %s 없음\n' "${name2}" "${name1}" >&2
    fi
    printf '한 벌로 회전해야 한다 — 남은 쪽을 지우고 다시 실행하거나 두 쪽을 함께 맞춘다(docs/conventions/k8s/cnpg.md)\n' >&2
    exit 1
}

# 랜덤 값 생성기. 명령 치환이 다른 명령의 인자 안에 있으면 실패해도 `set -e`가 발동하지 않아
# **빈 값 Secret**이 만들어지고 get-or-create라 영구히 남는다 → 반드시 단독 대입으로 받아 nonempty로 검사한다.
rand_hex() {
    openssl rand -hex 24
}
rand_fernet() {
    openssl rand -base64 32 | tr '+/' '-_'
}
# nonempty <이름> <값> — 빈 값이면 만들기 전에 멈춘다(값은 출력하지 않는다).
nonempty() {
    [ -n "$2" ] || {
        printf '랜덤 값 생성 실패(빈 값): %s\n' "$1" >&2
        exit 1
    }
}
# 단독 랜덤 Secret: 없을 때만 값을 만든다. ensure_random_secret <ns> <name> <key> <생성기 함수>
ensure_random_secret() {
    local ns="$1" name="$2" key="$3" generator="$4" value
    if kubectl -n "${ns}" get secret "${name}" >/dev/null 2>&1; then
        log "Secret 있음(건드리지 않음): ${ns}/${name}"
        return 0
    fi
    value="$("${generator}")"
    nonempty "${ns}/${name}" "${value}"
    ensure_secret "${ns}" "${name}" --from-literal="${key}=${value}"
}

# 1) 용도별로 **분리**한다. 같은 비밀번호를 두 시크릿에 중복 보관하지 않는다
#    ("값이 갈려 부분 성공하는" 드리프트의 씨앗). PG 크리덴셜의 in-cluster 단일 출처는 catalog-pg-app.
#    예외는 아래 3)의 쌍 두 개뿐이다: CNPG `passwordSecret`은 Cluster 네임스페이스(default)에,
#    차트 `connection`은 앱 네임스페이스(airflow)에 있어야 해 **교차 참조가 불가능**하다.
#    그래서 한 랜덤 값에서 쌍으로 만들고 반쪽은 거부한다(ensure_secret_pair).
ensure_secret default lakehouse-creds \
    --from-literal=s3-access-key="${S3_ACCESS_KEY}" \
    --from-literal=s3-secret-key="${S3_SECRET_KEY}"

ensure_secret default catalog-pg-app --type=kubernetes.io/basic-auth \
    --from-literal=username="${PG_USER}" \
    --from-literal=password="${PG_PASSWORD}"

# 2) PhysioNet credentialed 원천 접근 (수집 자산 — defs/<dataset>/raw_assets.py).
#    기본값을 두지 않는다 — 외부 기관의 개인 계정이라 아무 값이나 넣으면 수집에서 401로 죽는 부분 성공이 된다.
#    전용 Secret으로 분리한다(회전 주기를 우리가 통제하지 못한다).
if [ -n "${PHYSIONET_USERNAME:-}" ] && [ -n "${PHYSIONET_PASSWORD:-}" ]; then
    ensure_secret default physionet-creds --type=kubernetes.io/basic-auth \
        --from-literal=username="${PHYSIONET_USERNAME}" \
        --from-literal=password="${PHYSIONET_PASSWORD}"
else
    log "Secret 건너뜀: physionet-creds (PHYSIONET_USERNAME/PASSWORD 미설정)"
fi

# 3) Airflow — 메타 DB·Celery 브로커·웹 서버 비밀. 전부 랜덤이라 기본값이 없다(값은 출력하지 않는다).
#    네임스페이스를 먼저 만든다(멱등: dry-run 매니페스트를 apply). ArgoCD 앱의 CreateNamespace와 겹쳐도 무해하다.
kubectl create namespace airflow --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# PG 쌍: default/airflow-pg-app(CNPG managed.roles의 passwordSecret) ↔ airflow/airflow-metadata(차트 connection)
if ! ensure_secret_pair default airflow-pg-app airflow airflow-metadata; then
    AIRFLOW_PG_PW="$(rand_hex)"
    nonempty airflow-pg-app "${AIRFLOW_PG_PW}"
    ensure_secret default airflow-pg-app --type=kubernetes.io/basic-auth \
        --from-literal=username=airflow \
        --from-literal=password="${AIRFLOW_PG_PW}"
    ensure_secret airflow airflow-metadata \
        --from-literal=connection="postgresql+psycopg2://airflow:${AIRFLOW_PG_PW}@catalog-postgres-rw.default.svc:5432/airflow"
    unset AIRFLOW_PG_PW
fi

# Redis 쌍: airflow/airflow-redis-password(Redis 인증) ↔ airflow/airflow-broker-url(Celery 브로커 URL)
if ! ensure_secret_pair airflow airflow-redis-password airflow airflow-broker-url; then
    AIRFLOW_REDIS_PW="$(rand_hex)"
    nonempty airflow-redis-password "${AIRFLOW_REDIS_PW}"
    ensure_secret airflow airflow-redis-password \
        --from-literal=password="${AIRFLOW_REDIS_PW}"
    ensure_secret airflow airflow-broker-url \
        --from-literal=connection="redis://:${AIRFLOW_REDIS_PW}@airflow-redis:6379/0"
    unset AIRFLOW_REDIS_PW
fi

# 단독 Secret — 쌍이 아니라 get-or-create만 한다. Fernet 키는 urlsafe base64 32바이트다.
ensure_random_secret airflow airflow-fernet-key fernet-key rand_fernet
ensure_random_secret airflow airflow-jwt-secret jwt-secret rand_hex
ensure_random_secret airflow airflow-api-secret api-secret-key rand_hex
ensure_random_secret airflow airflow-admin password rand_hex

# DAG 용 S3(SeaweedFS) Connection — values.yaml `secret:` 가 env AIRFLOW_CONN_SEAWEEDFS_S3 로 주입한다.
# 🔴 키는 1)의 default/lakehouse-creds 와 **한 벌**(같은 S3_ACCESS_KEY/SECRET_KEY)이다. Secret 은 ns 를 넘어
#    참조할 수 없어 사본이 생긴다 — 회전 시 두 Secret 을 함께 지우고 이 스크립트를 다시 돌린다.
#    JSON 은 jq 로 만든다(키에 따옴표·백슬래시가 섞여도 깨지지 않게). 엔드포인트는 ExternalName 의 FQDN.
#    env 주입이라 Secret 을 바꿔도 Airflow 워크로드를 재기동해야 반영된다.
#
# 🔴 두 사본의 **값 일치**를 검사한다 — get-or-create 는 존재만 보므로, 한쪽이 옛 값으로 남으면
#    Secret 은 있고 파드도 뜨는데 S3 쓰기에서만 ACCESS_DENIED 가 나는 부분 성공이 된다.
#    기준은 지금 쓰는 S3_ACCESS_KEY/SECRET_KEY 다. 값은 출력하지 않고 같음/다름만 판정한다.
secret_field() {  # secret_field <ns> <name> <key> — 없으면 빈 문자열
    kubectl -n "$1" get secret "$2" -o "jsonpath={.data.$3}" 2>/dev/null | base64 -d 2>/dev/null || true
}
s3_mismatch() {  # s3_mismatch <설명> — 회전 안내 후 중단
    printf 'S3 키 사본 불일치: %s 가 현재 S3_ACCESS_KEY/SECRET_KEY 와 다르다\n' "$1" >&2
    printf '한 벌로 회전한다 — 옛 사본을 지우고 다시 실행한 뒤 해당 워크로드를 재기동한다\n' >&2
    exit 1
}
LH_AK="$(secret_field default lakehouse-creds s3-access-key)"
LH_SK="$(secret_field default lakehouse-creds s3-secret-key)"
if [ "${LH_AK}" != "${S3_ACCESS_KEY}" ] || [ "${LH_SK}" != "${S3_SECRET_KEY}" ]; then
    s3_mismatch default/lakehouse-creds
fi
unset LH_AK LH_SK
if kubectl -n airflow get secret airflow-conn-seaweedfs >/dev/null 2>&1; then
    CONN_EXISTING="$(secret_field airflow airflow-conn-seaweedfs connection)"
    if [ "$(printf '%s' "${CONN_EXISTING}" | jq -r .login)" != "${S3_ACCESS_KEY}" ] \
        || [ "$(printf '%s' "${CONN_EXISTING}" | jq -r .password)" != "${S3_SECRET_KEY}" ]; then
        s3_mismatch airflow/airflow-conn-seaweedfs
    fi
    unset CONN_EXISTING
    log "Secret 있음(건드리지 않음, 키 일치): airflow/airflow-conn-seaweedfs"
else
    # 명령 치환은 단독 대입으로 받는다 — 인자 안이면 실패해도 set -e 가 안 걸려 빈 값 Secret 이 남는다(rand_hex 주석)
    CONN_JSON="$(jq -cn --arg k "${S3_ACCESS_KEY}" --arg s "${S3_SECRET_KEY}" \
        '{conn_type: "aws", login: $k, password: $s,
          extra: {endpoint_url: "http://seaweedfs.default.svc:8333", region_name: "us-east-1"}}')"
    nonempty airflow-conn-seaweedfs "${CONN_JSON}"
    ensure_secret airflow airflow-conn-seaweedfs --from-literal=connection="${CONN_JSON}"
    unset CONN_JSON
fi

log "완료. 카탈로그 Cluster는 ArgoCD(catalog-postgres)가, S3는 클러스터 밖 스택 A(terraform/cluster/kind)가 맡는다"
