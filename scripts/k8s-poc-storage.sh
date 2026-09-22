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

# 0-3) `spec.plugins` 배선의 **형태** 검사 — 판정 불가면 멈춘다(fail-closed).
#    §4의 가드는 `^    plugins:` 리터럴로 배선 유무를 가르는데, 그 패턴이 실제로 가르는 것은
#    「주석 / 활성」이 아니라 **「정확히 그 형태 / 그 밖 전부」**다. 그래서 **활성 배선이라도**
#    행말 주석·다른 들여쓰기·flow 표기면 「배선 없음」과 **같은 출력**이 된다(거짓 음성).
#    🔴 그 방향의 결과가 무겁다 — §5는 Cluster를 **무조건** 적용하므로 `isWALArchiver`는
#    살아 있는데 `ObjectStore`만 없는 상태가 된다. 그러면 아카이빙 못 한 WAL이 PVC(5Gi)를 채워
#    DB가 서고, `ALLOWVOLUMEEXPANSION=false`(§5 주석)라 확장도 못 해 클러스터 재생성이다.
#    ⚠️ 뒷문장은 **추정이다** — 2026-08-19 실측(`k8s/catalog-postgres.yaml` §plugins)은
#    `ObjectStore`가 **있는** 상태의 거동이고, 없는 상태는 플러그인이 참조 해결에 실패해
#    아카이빙 시도 이전에 막힐 수도 있다(방향이 다르다). PVC 포화 자체도 실측 기록이 없다.
#    확실한 것은 「배선 있음이 배선 없음으로 처리된다」까지이고, **그것만으로 중단 사유는 된다.**
#    ⇒ 「못 읽었다」를 「없다」와 같이 처리하지 않고 **중단**으로 바꾼다.
#    🔴 **위치가 곧 설계다.** 여기는 Secret 생성(§1)·구 리소스 삭제(§2-1)·SeaweedFS 기동(§3)
#    **전부보다 앞**이라 멈춰도 **클러스터에 만들어 둔 것이 없다.** 이것이 롤백을 넣지 않는
#    이유이기도 하다 — 이 스크립트가 만드는 것은 전부 stateful이라 되돌리기가 곧 데이터
#    삭제이고(PVC·카탈로그), 타임아웃은 「실패」가 아니라 「아직 안 끝남」인 경우가 많아
#    되돌리는 쪽이 틀린 대응이 된다.
#    ⚠️ 단 **「부작용 0」은 아니다** — 위 `kubectl config use-context`(§상단)가 이미 돌아
#    현재 컨텍스트가 `kind-${CLUSTER_NAME}`로 바뀌어 있고 여기서 멈춰도 되돌아가지 않는다.
#    되돌릴 대상이 없다는 것은 **클러스터 자원 축**에 대한 말이지 환경 전체가 아니다.
#    🔴 매칭은 **키 위치에 앵커**한다(`^[[:space:]]*plugins:`). 앵커 없이 `/plugins:/`로 두면
#    활성 줄의 **행말 주석**(`imageName: foo  # plugins: 아래 참고`)까지 활성 배선으로 집계돼
#    **아무 배선도 없는데 부트스트랩이 멈춘다**(2026-09-21 security 실측). 이 파일은 주석 밀도가
#    높고 산문에서 `plugins`를 반복 언급해 개연성이 낮지 않다.
CR_PLUGINS_ACTIVE="$(awk '/^[[:space:]]*#/ {next} /^[[:space:]]*plugins:/ {print}' \
    "${REPO_ROOT}/k8s/catalog-postgres.yaml")"
CR_PLUGINS_LINES="$(printf '%s' "${CR_PLUGINS_ACTIVE}" | grep -c . || true)"
#    🔴 `|| true`는 grep의 **모든** 실패를 삼킨다. grep이 실행조차 못 하면 값이 비는데,
#    `[ "" -gt 1 ]`은 rc=2를 내면서도 **`if` 조건은 `set -e` 면제**라 중단되지 않아
#    두 검사가 **조용히 거짓**이 된다 — fail-closed 게이트 안의 fail-open이다(security 실측).
#    같은 블록의 `awk` 실패는 명령 치환이라 `set -e`에 걸려 중단되는데, 이쪽만 방향이 달랐다.
case "${CR_PLUGINS_LINES}" in
    '' | *[!0-9]*)
        printf 'plugins 줄 수 판정에 실패했다(값=[%s]) — 검사기가 돌지 않았다.\n' \
            "${CR_PLUGINS_LINES}" >&2
        exit 1
        ;;
esac
if [ "${CR_PLUGINS_LINES}" -gt 1 ]; then
    printf 'k8s/catalog-postgres.yaml의 활성 plugins 줄이 %s개다 — 하나여야 한다.\n' \
        "${CR_PLUGINS_LINES}" >&2
    printf '%s\n' "${CR_PLUGINS_ACTIVE}" >&2
    exit 1
fi
if [ "${CR_PLUGINS_LINES}" -eq 1 ] \
    && ! printf '%s\n' "${CR_PLUGINS_ACTIVE}" | grep -q '^    plugins:[[:space:]]*$'; then
    printf 'k8s/catalog-postgres.yaml의 plugins 배선 형태가 §4 가드와 맞지 않는다:\n' >&2
    printf '  [%s]\n' "${CR_PLUGINS_ACTIVE}" >&2
    printf '기대 형태(4칸 들여쓰기 + 행말 즉시 종료): "    plugins:"\n' >&2
    printf '이대로 두면 배선이 있는데도 ObjectStore가 적용되지 않아 WAL이 PVC를 채운다.\n' >&2
    exit 1
fi
#    🔴 형태가 맞으면 **값**까지 대조한다(§0·§0-2와 같은 형태). 형태만 보면 `plugins:` 아래가
#    비었거나 다른 이름을 가리켜도 통과하는데, 그러면 §4가 ObjectStore·ScheduledBackup을 적용해
#    **WAL 아카이버 없이 백업 잡만 매일 도는** 상태가 된다 — 이 가드가 막으려던 바로 그 상태다.
if [ "${CR_PLUGINS_LINES}" -eq 1 ]; then
    CR_BARMAN_OBJ="$(awk '/^[[:space:]]*#/ {next} /^[[:space:]]*barmanObjectName:/ {print $2; exit}' \
        "${REPO_ROOT}/k8s/catalog-postgres.yaml")"
    OBJECTSTORE_NAME="$(awk '/^kind: ObjectStore/ {f=1} f && /^[[:space:]]*name:/ {print $2; exit}' \
        "${REPO_ROOT}/k8s/catalog-pg-backup.yaml")"
    if [ -z "${CR_BARMAN_OBJ}" ] || [ "${CR_BARMAN_OBJ}" != "${OBJECTSTORE_NAME}" ]; then
        printf 'plugins 배선은 살아 있는데 barmanObjectName이 ObjectStore와 맞지 않는다.\n' >&2
        printf '  catalog-postgres.yaml  barmanObjectName : [%s]\n' "${CR_BARMAN_OBJ}" >&2
        printf '  catalog-pg-backup.yaml ObjectStore name : [%s]\n' "${OBJECTSTORE_NAME}" >&2
        printf 'WAL 아카이버 없이 ScheduledBackup만 매일 돌게 된다.\n' >&2
        exit 1
    fi
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

# 1-2) PhysioNet credentialed 원천 접근 (수집 자산 — defs/<dataset>/raw_assets.py).
#
# 🔴 **기본값(placeholder)을 두지 않는다.** 위 세 Secret과 갈리는 지점이다 —
#    저것들은 우리가 발급하는 로컬 PoC 크리덴셜이라 기본값이 의미를 갖지만,
#    이것은 **외부 기관(PhysioNet)의 개인 계정**이다. 아무 값이나 넣으면
#    Secret은 생기고 파드는 뜨는데 수집에서 401로 죽는 **부분 성공**이 된다.
#    값이 없으면 만들지 않고, 매니페스트 쪽은 `optional: true`로 받는다
#    (= 수집 자산만 실패하고 나머지 파이프라인은 계속 돈다).
# 🔴 **전용 Secret으로 분리한다** — 회전 주기가 다르고(우리가 통제하지 못한다)
#    읽는 주체가 Dagster daemon 하나뿐이다.
if [ -n "${PHYSIONET_USERNAME:-}" ] && [ -n "${PHYSIONET_PASSWORD:-}" ]; then
    log "Secret 생성/갱신: physionet-creds (원천 다운로드 계정)"
    kubectl create secret generic physionet-creds -n default \
        --type=kubernetes.io/basic-auth \
        --from-literal=username="${PHYSIONET_USERNAME}" \
        --from-literal=password="${PHYSIONET_PASSWORD}" \
        --dry-run=client -o yaml | kubectl apply -f -
else
    log "Secret 건너뜀: physionet-creds (PHYSIONET_USERNAME/PASSWORD 미설정)"
    log "  → 수집 자산(raw_*)만 실패한다. 적재 자산은 기존 S3 객체로 계속 돈다."
fi

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
#    🔴 **재시도 소진은 에러다.** 종전에는 12회가 모두 실패해도 루프가 그냥 끝나 §4·§5로
#    진행했다 — `warehouse` 버킷 없이 Iceberg가 올라가는데 스크립트는 성공으로 보였다(fail-open).
#    재시도는 「filer gRPC가 아직 안 열렸다」를 흡수하려는 것이지 **실패를 삼키려는 것이 아니다.**
#    ⚠️ **이 게이트가 닫는 것은 「프로세스가 실패한다」 축까지다.** 판정 근거는 파이프 끝단
#    `weed shell`의 종료코드인데, REPL이라 **명령이 실패해도 메시지만 찍고 0으로 끝날 수 있다**.
#    그러면 버킷이 없는데도 `break`로 빠져 「준비 완료」가 찍힌다 — 그 축은 **`미확인`이다**
#    (종료코드 의미론이 저장소에 기록된 바 없고 클러스터 미기동. 2026-09-21 security 지적).
#    닫으려면 종료코드가 아니라 **상태**를 봐야 한다(`s3.bucket.list` 대조). 그건 클러스터에서
#    출력 형식을 확인한 뒤 할 일이라 여기 넣지 않았다 — **검증 못 한 파서를 차단 게이트로
#    승격시키는 것**이 바로 이 지적의 요지이기 때문이다.
for bucket in warehouse pg-backup dagster-logs; do
    log "${bucket} 버킷 생성"
    bucket_ready=""
    for attempt in $(seq 1 12); do
        if kubectl -n default exec statefulset/seaweedfs -- \
            sh -c "echo 's3.bucket.create -name ${bucket}' | weed shell -master localhost:9333 -filer localhost:8888" \
            >/dev/null 2>&1; then
            log "${bucket} 버킷 준비 완료 (시도 ${attempt})"
            bucket_ready="yes"
            break
        fi
        sleep 5
    done
    if [ -z "${bucket_ready}" ]; then
        printf '버킷 생성 실패: %s (12회 재시도 60초 소진)\n' "${bucket}" >&2
        printf 'SeaweedFS filer가 안 떴거나 weed shell이 실패한다. 아래로 원인을 본다:\n' >&2
        printf '  kubectl -n default get pod -l app=seaweedfs\n' >&2
        printf '  kubectl -n default logs statefulset/seaweedfs --tail=50\n' >&2
        exit 1
    fi
done

# 4) 백업 구성 — ObjectStore + ScheduledBackup.
#    🔴 **Cluster CR의 `spec.plugins` 배선과 한 벌이다.** 배선이 없으면 적용하지 않는다 —
#    적용만 하면 ScheduledBackup이 매일 배선 없이 돌아 **아무도 안 읽는 실패**를 쌓는다.
#    판정은 클러스터가 아니라 **파일**을 읽으므로 아래 순서 근거에 영향이 없다.
#    🔴 Cluster보다 먼저 적용한다. Cluster가 뜨는 즉시 WAL 아카이빙이 시작되는데
#    그때 ObjectStore(와 pg-backup 버킷)가 없으면 아카이빙이 실패한다.
#    🔴 들여쓰기를 **`spec` 직속(4칸)에 고정**한다. `^ *plugins:`처럼 깊이를 열어두면
#    무관한 `plugins:` 키가 다른 깊이에 들어올 때 가드가 "배선됨"으로 **뒤집혀**,
#    배선 없이 ScheduledBackup만 매일 도는 상태 — 이 가드가 막으려던 바로 그 상태 — 로 복귀한다.
#    주석 줄(`    # plugins:`)은 4칸 뒤가 `#`이라 걸리지 않는다. 다만 이 패턴이 가르는 것은
#    「주석 / 활성」이 아니라 **「정확히 이 리터럴 형태 / 그 밖 전부」**다 — 활성 줄이어도
#    행말 주석(`plugins:  # …`)·다른 들여쓰기(2칸)·flow 표기(`plugins: [{…}]`)면 걸리지 않는다
#    (2026-09-21 security 실측, 편집 변형 7종). 즉 거짓 양성이 아니라 **거짓 음성 쪽으로 기운다** —
#    배선을 되살릴 때는 위 리터럴 형태 그대로 쓴다.
#    ✅ **그 세 형태는 §0-3이 앞에서 차단한다**(2026-09-21 신설) — 여기까지 도달하지 못하고
#    부작용 전에 `exit 1`이 난다. 즉 위 「거짓 음성」은 **닫힌 갭**이고, 이 문단은 §4 패턴
#    단독의 성질을 적은 것이다. 이 분기를 손볼 때 §0-3을 함께 보지 않으면 없는 구멍을 다시 막게 된다.
CR_PLUGINS="$(awk '/^    plugins:[[:space:]]*$/ {print "yes"; exit}' \
    "${REPO_ROOT}/k8s/catalog-postgres.yaml")"
if [ "${CR_PLUGINS}" = "yes" ]; then
    log "백업 구성 적용 (ObjectStore + ScheduledBackup)"
    kubectl apply -f "${REPO_ROOT}/k8s/catalog-pg-backup.yaml"
else
    # 🔴 이 로그가 보증하는 범위는 **선언 파일까지**다. 스크립트는 클러스터를 조회하지 않으므로
    #    라이브 Cluster에 이전 배선이 남아 있으면 아카이빙이 시도될 수 있다("미수행"으로 단정하지 않는다).
    log "백업 구성 건너뜀 — Cluster CR 선언에 plugins 배선이 없다(선언 기준 — 라이브 Cluster는 별도 확인)"
    log "  → 되살리려면 k8s/catalog-postgres.yaml의 plugins 블록 주석을 해제한다"
fi

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
