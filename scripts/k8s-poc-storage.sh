#!/usr/bin/env bash
# PoC 스토리지 배포: Secret(크리덴셜) → SeaweedFS(S3) + Catalog Postgres(CNPG) → warehouse 버킷
# 사용: ./scripts/k8s-poc-storage.sh
# 전제: CRD 두 개가 **출처를 달리해** 먼저 있어야 한다(2026-08-28 이관).
#         clusters.postgresql.cnpg.io  ← CNPG 차트 = **terraform apply**(lakehouse-platform)
#         objectstores.barmancloud…    ← Barman 플러그인 = **./scripts/k8s-operators.sh**
#       아래 §2 가 둘을 갈라서 확인하고 각각 맞는 명령을 안내한다.
# 크리덴셜은 로컬 PoC 기본값(env override 가능). 실인프라는 외부 시크릿 매니저 사용.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/k8s-env.sh
source "${SCRIPT_DIR}/k8s-env.sh"

require_cli kubectl
kubectl config use-context "kind-${CLUSTER_NAME}"

# 로컬 PoC 크리덴셜(placeholder — 실값은 env로 주입)
# 주석은 **gitleaks 문법**(`gitleaks:allow`)이다. 이 레포의 스캐너는 gitleaks이고
# `pragma: allowlist secret`은 detect-secrets 문법이라 여기서는 아무것도 억제하지 못한다
# ("예외 처리를 해뒀다"는 거짓 안심 — 2026-08-19 security 감사).
S3_ACCESS_KEY="${S3_ACCESS_KEY:-poc-access}"          # gitleaks:allow
S3_SECRET_KEY="${S3_SECRET_KEY:-poc-local-secret}"    # gitleaks:allow
# PG_USER는 k8s/catalog-postgres.yaml의 `bootstrap.initdb.owner`와 **반드시 같아야 한다**(아래 0번 가드).
PG_USER="${PG_USER:-iceberg}"
PG_PASSWORD="${PG_PASSWORD:-iceberg-local}"           # gitleaks:allow
# Dagster 메타 스토리지 계정 — 카탈로그와 **같은 CNPG 클러스터의 다른 DB·다른 롤**이다.
# 롤 선언은 k8s/catalog-postgres.yaml의 managed.roles, DB 선언은 k8s/dagster/dagster-meta-db.yaml.
# 🔴 카탈로그 비밀번호를 재사용하지 않는다(폭발반경·회전 경로 분리).
DAGSTER_PG_USER="${DAGSTER_PG_USER:-dagster}"
DAGSTER_PG_PASSWORD="${DAGSTER_PG_PASSWORD:-dagster-local}"   # gitleaks:allow

S3_JSON="$(cat <<JSON
{"identities":[{"name":"poc","credentials":[{"accessKey":"${S3_ACCESS_KEY}","secretKey":"${S3_SECRET_KEY}"}],"actions":["Admin","Read","Write","List","Tagging"]}]}
JSON
)"

# 0) 계정명 대조 — CNPG는 시크릿의 username과 CR의 `bootstrap.initdb.owner`가 **같아야 한다**(공식 문서).
#    owner는 CR의 리터럴이라 PG_USER override와 자동으로 맞지 않는다 → 적용 전에 막는다.
#    (2026-08-19 devops-qa·security 감사 공통 지적: 지금 일치하는 건 기본값이 같아서일 뿐이다)
CR_OWNER="$(awk '/^ *owner:/ {print $2; exit}' "${REPO_ROOT}/k8s/catalog-postgres.yaml")"
if [ "${CR_OWNER}" != "${PG_USER}" ]; then
    printf 'PG_USER(%s) != k8s/catalog-postgres.yaml의 owner(%s)\n' "${PG_USER}" "${CR_OWNER}" >&2
    printf 'CNPG bootstrap이 정의되지 않은 동작에 빠진다. 둘을 맞춘 뒤 다시 실행하라.\n' >&2
    exit 1
fi

# 0-2) Dagster 메타 롤·DB 대조 — 같은 형태의 가드를 한 벌 더 건다.
#      managed.roles의 롤 이름과 Database CR의 owner가 DAGSTER_PG_USER와 **셋 다 같아야** 한다.
#      어긋나면 롤이 안 만들어지거나 Database CR의 owner 참조가 깨지는데, 둘 다
#      "Secret은 생겼고 파드는 떴는데 접속에서 죽는" 부분 성공으로 나타난다.
CR_ROLES="$(awk '/^ *- name: / {print $3}' "${REPO_ROOT}/k8s/catalog-postgres.yaml")"
if ! printf '%s\n' "${CR_ROLES}" | grep -qx "${DAGSTER_PG_USER}"; then
    printf 'DAGSTER_PG_USER(%s)가 k8s/catalog-postgres.yaml의 managed.roles에 없다.\n' \
        "${DAGSTER_PG_USER}" >&2
    printf '현재 선언된 롤: %s\n' "$(printf '%s' "${CR_ROLES}" | tr '\n' ' ')" >&2
    exit 1
fi
DB_OWNER="$(awk '/^ *owner:/ {print $2; exit}' "${REPO_ROOT}/k8s/dagster/dagster-meta-db.yaml")"
if [ "${DB_OWNER}" != "${DAGSTER_PG_USER}" ]; then
    printf 'DAGSTER_PG_USER(%s) != dagster-meta-db.yaml의 owner(%s)\n' \
        "${DAGSTER_PG_USER}" "${DB_OWNER}" >&2
    exit 1
fi

# 1) Secret — 용도별로 **분리**한다.
#    lakehouse-creds : SeaweedFS s3.json + S3 접속 키 (S3 전용)
#    catalog-pg-app  : 카탈로그 Postgres 계정 (CNPG `bootstrap.initdb.secret`이 요구하는
#                      type=kubernetes.io/basic-auth · 키 이름 username/password 고정)
#    🔴 같은 비밀번호를 두 시크릿에 중복 보관하지 않는다 — 이 레포가 반복해 밟은
#    "값이 갈려 부분 성공하는" 드리프트의 씨앗이다. PG 크리덴셜의 in-cluster 단일 출처는 catalog-pg-app.
log "Secret 생성/갱신: lakehouse-creds (S3 전용)"
kubectl create secret generic lakehouse-creds -n default \
    --from-literal=s3-access-key="${S3_ACCESS_KEY}" \
    --from-literal=s3-secret-key="${S3_SECRET_KEY}" \
    --from-literal=s3.json="${S3_JSON}" \
    --dry-run=client -o yaml | kubectl apply -f -

log "Secret 생성/갱신: catalog-pg-app (카탈로그 PG 계정)"
kubectl create secret generic catalog-pg-app -n default \
    --type=kubernetes.io/basic-auth \
    --from-literal=username="${PG_USER}" \
    --from-literal=password="${PG_PASSWORD}" \
    --dry-run=client -o yaml | kubectl apply -f -

log "Secret 생성/갱신: dagster-meta-pg-app (Dagster 메타 DB 계정)"
kubectl create secret generic dagster-meta-pg-app -n default \
    --type=kubernetes.io/basic-auth \
    --from-literal=username="${DAGSTER_PG_USER}" \
    --from-literal=password="${DAGSTER_PG_PASSWORD}" \
    --dry-run=client -o yaml | kubectl apply -f -

# 2) 선행 조건 — 오퍼레이터·플러그인 CRD.
#    카탈로그 PG는 **CNPG Cluster CR**이고 그 CR이 barman 플러그인을 참조한다.
#    🔴 두 CRD의 **출처가 다르다**(2026-08-28 이관).
#      clusters.postgresql.cnpg.io    ← CNPG 차트 = **Terraform** (lakehouse-platform)
#      objectstores.barmancloud...    ← Barman 플러그인 = **k8s-operators.sh**
#    그래서 안내도 갈라야 한다 — 예전처럼 "k8s-operators.sh 를 실행하라"로 뭉치면
#    CNPG 쪽에서 **틀린 안내**가 된다(그 스크립트는 이제 CNPG 를 설치하지 않는다).
for crd in clusters.postgresql.cnpg.io objectstores.barmancloud.cnpg.io; do
    if ! kubectl get crd "${crd}" >/dev/null 2>&1; then
        case "${crd}" in
            clusters.postgresql.cnpg.io)
                printf 'CRD 없음(%s) — 먼저 다음을 실행하라:\n' "${crd}" >&2
                printf '  terraform -chdir=%s/terraform/lakehouse-platform apply\n' "${REPO_ROOT}" >&2
                ;;
            *)
                printf 'CRD 없음(%s) — 먼저 ./scripts/k8s-operators.sh 를 실행하라\n' "${crd}" >&2
                ;;
        esac
        exit 1
    fi
done

# 2-1) 구 구성(Deployment + emptyDir) 잔존분 정리 — 같은 이름의 Service가 CNPG 서비스와 헷갈린다.
#      카탈로그 데이터는 emptyDir였고 재적재 전제라 보존 대상이 아니다(2026-08-19 확인).
log "구 catalog-postgres Deployment/Service 정리(있으면)"
kubectl -n default delete deploy/catalog-postgres --ignore-not-found
kubectl -n default delete svc/catalog-postgres --ignore-not-found

log "SeaweedFS 배포"
kubectl apply -f "${REPO_ROOT}/k8s/seaweedfs.yaml"
kubectl -n default rollout status statefulset/seaweedfs --timeout=180s

# 3) S3 버킷 생성(멱등) — weed shell은 filer 자동발견 실패가 있어 -filer 명시
#    파드가 Ready여도 filer의 gRPC(포트+10000)는 아직 안 열려 있을 수 있다
#    (2026-08-19 실측: `dial tcp [::1]:18888 connect: connection refused`) → 재시도한다.
#    버킷은 3개다: `warehouse`(Iceberg) / `pg-backup`(카탈로그 PG 백업) /
#    `dagster-logs`(run step 로그 — S3ComputeLogManager).
#    백업을 안 켜도 빈 버킷 하나는 비용이 없으므로 항상 만든다(분기 없는 단순함).
#    🔴 분리하는 이유: 같은 버킷에 두면 Iceberg `remove_orphan_files`의 나열 대상과 섞인다.
#    compute log는 특히 그렇다 — 카탈로그가 모르는 파일이라 orphan으로 지워질 수 있다.
for bucket in warehouse pg-backup dagster-logs; do
    log "${bucket} 버킷 생성"
    for attempt in $(seq 1 12); do
        if kubectl -n default exec statefulset/seaweedfs -- \
            sh -c "echo 's3.bucket.create -name ${bucket}' | weed shell -master localhost:9333 -filer localhost:8888" \
            >/dev/null 2>&1; then
            log "${bucket} 버킷 준비 완료 (시도 ${attempt})"
            break
        fi
        sleep 5
    done
done

# 4) 백업 구성 — ObjectStore + ScheduledBackup.
#    🔴 **Cluster보다 먼저** 적용한다. Cluster가 뜨는 즉시 WAL 아카이빙이 시작되는데
#    그때 ObjectStore(와 pg-backup 버킷)가 없으면 아카이빙이 실패한다.
log "백업 구성 적용 (ObjectStore + ScheduledBackup)"
kubectl apply -f "${REPO_ROOT}/k8s/catalog-pg-backup.yaml"

# 5) 카탈로그 Postgres(CNPG Cluster) — 백업 목적지가 준비된 뒤에 띄운다.
log "Catalog Postgres(CNPG) 배포"
kubectl apply -f "${REPO_ROOT}/k8s/catalog-postgres.yaml"
# CR은 rollout status 대상이 아니다 → Cluster의 Ready 조건을 기다린다(initdb 포함이라 넉넉히).
kubectl -n default wait --for=condition=Ready \
    cluster.postgresql.cnpg.io/catalog-postgres --timeout=300s

log "완료. 다음: terraform -chdir=${REPO_ROOT}/terraform/lakehouse-platform apply"
log "  (매니페스트 18종 — 로컬 CA·워크로드 RBAC·Dagster. 그 뒤 ./scripts/k8s-dagster.sh 로 이미지)"
# Flink는 오퍼레이터(terraform apply)까지만 부트스트랩이고 세션 클러스터는 **워크로드**라 여기서 띄우지 않는다
# — 부트스트랩에 넣으면 클러스터를 올릴 때마다 JM이 자동 상주해 "안 쓰는 컴퓨트 유출"을 구조적으로 재생산한다.
log "Flink: kubectl apply -f k8s/flink/flinkdeployment-session.yaml (필요할 때만, 사용 후 delete)"
