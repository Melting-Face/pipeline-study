#!/usr/bin/env bash
# 외부 오브젝트 스토리지(compose SeaweedFS) 기동: s3.json → compose up → healthy → 버킷 → kind 네트워크 연결
# 사용: ./scripts/storage-up.sh
#
# 클러스터와 독립이다 — kind가 없어도 끝까지 돌고 연결 단계만 경고로 건너뛴다.
# 컨테이너가 **재생성**되면(설정 변경 후 up·--force-recreate·down→up) kind 네트워크 연결이 풀린다 — 그 뒤엔
# 이 스크립트를 다시 돌린다(멱등). `restart`는 연결을 유지한다(둘 다 실측으로 확인).
# 클러스터를 새로 만들 때의 연결은 terraform/cluster/kind 의 seaweedfs_network 가 맡는다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${STORAGE_ENV_FILE:-${REPO_ROOT}/.env}"   # 시험용 override
S3_CONFIG="${REPO_ROOT}/seaweedfs/s3.json"
CONTAINER="seaweedfs"
KIND_NETWORK="${KIND_NETWORK:-kind}"   # 시험용 override
# 파드 쪽 Service seaweedfs(ExternalName)가 가리키는 이름 — gitops/charts/storage-external 의 기본값과 같아야 한다.
# Service와 같은 이름(seaweedfs)으로 두면 클러스터 DNS 검색 경로에서 CNAME이 자기 자신을 가리킬 수 있다.
KIND_ALIAS="seaweedfs-ext"
BUCKETS=(warehouse pg-backup dagster-logs)

log() { printf '\033[1;34m[storage]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[storage]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[storage]\033[0m %s\n' "$*" >&2; exit 1; }

command -v podman >/dev/null 2>&1 || die "필수 CLI 없음: podman"
# 데이터·s3.json 은 compose 의 상대경로(./seaweedfs/)라 **체크아웃마다 다른 사본**이 된다. 컨테이너 이름은 하나라
# linked worktree 에서 돌리면 그 worktree 의 사본으로 정본이 바뀌어 답한다 → 메인 체크아웃에서만 돌린다.
if [ "$(git -C "${REPO_ROOT}" rev-parse --git-dir)" != "$(git -C "${REPO_ROOT}" rev-parse --git-common-dir)" ] \
    && [ "${STORAGE_ALLOW_WORKTREE:-}" != "1" ]; then
    die "linked worktree 에서는 돌리지 않는다(데이터 사본이 갈린다) — 메인 체크아웃에서 실행한다(시험만 STORAGE_ALLOW_WORKTREE=1)"
fi
[ -f "${ENV_FILE}" ] || die ".env 가 없다: ${ENV_FILE} (.env.example 을 복사해 채운다)"

# 1) 키 — `.env`의 ICEBERG_S3_* 가 단일 출처(K8s Secret lakehouse-creds 와 한 벌).
#    source 하지 않는다: compose env 파일은 셸 문법이 아니고(값 뒤 인라인 주석) 다른 값까지 셸에 퍼진다.
#    값은 출력하지 않는다.
env_value() {
    local raw
    raw="$(grep -E "^$1=" "${ENV_FILE}" | tail -n 1 | cut -d= -f2-)"
    raw="${raw%%[[:space:]]#*}"
    raw="${raw%"${raw##*[![:space:]]}"}"
    raw="${raw#\"}"
    printf '%s' "${raw%\"}"
}
ACCESS_KEY="$(env_value ICEBERG_S3_ACCESS_KEY)"
SECRET_KEY="$(env_value ICEBERG_S3_SECRET_KEY)"
[ -n "${ACCESS_KEY}" ] && [ -n "${SECRET_KEY}" ] || die ".env 의 ICEBERG_S3_ACCESS_KEY/ICEBERG_S3_SECRET_KEY 가 비었다"
# 인증을 켜면 키 출처가 둘이 될 수 있다 — compose 의 Dagster·Trino 와 호스트 compute log(boto 기본 체인)는
# AWS_* 를 읽는다. 값이 있는데 다르면 그 소비자만 403 인 부분 성공이 되므로 여기서 막는다(값은 출력하지 않는다).
for pair in "AWS_ACCESS_KEY_ID:${ACCESS_KEY}" "AWS_SECRET_ACCESS_KEY:${SECRET_KEY}"; do
    other="$(env_value "${pair%%:*}")"
    if [ -n "${other}" ] && [ "${other}" != "${pair#*:}" ]; then
        die ".env 의 ${pair%%:*} 가 ICEBERG_S3_* 와 다르다 — 같은 값으로 맞춘다(compose·Trino·compute log 가 AWS_* 를 쓴다)"
    fi
done

log "s3.json 생성: ${S3_CONFIG#"${REPO_ROOT}"/}"
mkdir -p "$(dirname "${S3_CONFIG}")"
before="$(shasum -a 256 "${S3_CONFIG}" 2>/dev/null | cut -d' ' -f1 || true)"
(
    umask 077
    printf '{"identities":[{"name":"lakehouse","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin","Read","Write","List","Tagging"]}]}\n' \
        "${ACCESS_KEY}" "${SECRET_KEY}" > "${S3_CONFIG}"
)

after="$(shasum -a 256 "${S3_CONFIG}" | cut -d' ' -f1)"

# 2) 기동. compose 는 바인드 **파일 내용**의 변경을 감지하지 못한다 — 키를 회전해 s3.json 이 바뀌었으면
#    직접 restart 한다(restart 는 kind 네트워크 연결을 유지한다). Secret lakehouse-creds 는 get||create 라
#    따로 지우고 k8s-secrets.sh 를 다시 돌려야 바뀐다.
log "compose seaweedfs 기동 (profile storage)"
podman compose -f "${REPO_ROOT}/compose.yml" --profile storage up -d seaweedfs
if [ -n "${before}" ] && [ "${before}" != "${after}" ]; then
    log "s3.json 이 바뀌었다 — 새 키를 읽도록 restart (Secret lakehouse-creds 는 별도 회전)"
    podman restart "${CONTAINER}" >/dev/null
fi

log "healthy 대기"
healthy=""
for _ in $(seq 1 30); do
    if [ "$(podman inspect -f '{{.State.Health.Status}}' "${CONTAINER}" 2>/dev/null || true)" = "healthy" ]; then
        healthy="yes"
        break
    fi
    sleep 2
done
[ -n "${healthy}" ] || die "60초 안에 healthy 가 안 됐다 — podman logs ${CONTAINER} --tail 50"

# 3) 버킷(멱등) — weed shell 은 REPL 이라 명령이 실패해도 0 으로 끝날 수 있다.
#    그래서 생성의 종료코드가 아니라 **목록(s3.bucket.list)에 이름이 있는지**로 판정한다.
weed_shell() {
    podman exec "${CONTAINER}" sh -c "echo '$1' | weed shell -master localhost:9333 -filer localhost:8888" 2>&1
}
for bucket in "${BUCKETS[@]}"; do
    ready=""
    for _ in $(seq 1 12); do
        weed_shell "s3.bucket.create -name ${bucket}" >/dev/null || true
        if weed_shell "s3.bucket.list" | grep -qw -- "${bucket}"; then
            ready="yes"
            break
        fi
        sleep 5
    done
    [ -n "${ready}" ] || die "버킷 ${bucket} 이 s3.bucket.list 에 안 나온다(60초) — filer 기동을 확인한다"
    log "버킷 준비: ${bucket}"
done

# 4) kind 네트워크 연결(멱등) — 없으면 경고만 하고 성공으로 끝낸다(스토리지는 클러스터와 독립).
if ! podman network exists "${KIND_NETWORK}"; then
    warn "kind 네트워크가 없다 — 클러스터 연결은 건너뛴다(클러스터 생성 시 terraform 이 연결한다)"
    exit 0
fi
aliases="$(podman inspect -f "{{json .NetworkSettings.Networks.${KIND_NETWORK}.Aliases}}" "${CONTAINER}" 2>/dev/null || echo null)"
if [ "${aliases}" = "null" ]; then
    log "kind 네트워크 연결: 별칭 ${KIND_ALIAS}"
    podman network connect --alias "${KIND_ALIAS}" "${KIND_NETWORK}" "${CONTAINER}"
elif ! printf '%s' "${aliases}" | grep -q "\"${KIND_ALIAS}\""; then
    # 별칭 없이 붙어 있으면(수동 연결 등) 파드가 이름을 못 푼다 — 끊고 별칭으로 다시 붙인다.
    log "kind 네트워크 재연결: 별칭 ${KIND_ALIAS} 누락"
    podman network disconnect "${KIND_NETWORK}" "${CONTAINER}"
    podman network connect --alias "${KIND_ALIAS}" "${KIND_NETWORK}" "${CONTAINER}"
else
    log "kind 네트워크 연결됨: 별칭 ${KIND_ALIAS}"
fi
