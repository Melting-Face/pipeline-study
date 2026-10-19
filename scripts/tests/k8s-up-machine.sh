#!/usr/bin/env bash
# k8s-up.sh 머신 선택(#182)·중지 컨테이너 재시작(#244) 검증 — 가짜 podman으로 분기별 호출을 기록해 단언한다.
# 실제 podman·VM은 건드리지 않는다: PATH 앞에 가짜 podman을 두고 상태는 FAKE_MACHINES로 준다.
# 사용: bash scripts/tests/k8s-up-machine.sh   (repo 루트 어디서든)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UP="${SCRIPT_DIR}/../k8s-up.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
mkdir -p "${TMP}/bin"

# 가짜 podman — FAKE_MACHINES="이름:상태:rootful,..." 를 읽고, 모든 호출을 FAKE_LOG에 한 줄씩 남긴다.
# FAKE_CONTAINERS("이름:running|stopped,...")가 없으면 모든 컨테이너가 실행중이라 2·3단계는 아무것도 하지 않는다.
# 있으면 목록에 없는 컨테이너는 부재다(3단계 검증용).
cat > "${TMP}/bin/podman" <<'FAKE'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${FAKE_LOG}"
lookup() {
    # $1 = 머신 이름, $2 = 필드 번호(2=상태, 3=rootful). 없으면 1을 반환한다.
    local entry
    IFS=',' read -ra entries <<< "${FAKE_MACHINES}"
    for entry in "${entries[@]}"; do
        if [ "${entry%%:*}" = "$1" ]; then
            printf '%s\n' "$(cut -d: -f"$2" <<< "${entry}")"
            return 0
        fi
    done
    return 1
}
lookup_container() {
    # $1 = 컨테이너 이름. FAKE_CONTAINERS에서 상태를 찾는다. 없으면 1을 반환한다(부재).
    local entry
    IFS=',' read -ra entries <<< "${FAKE_CONTAINERS}"
    for entry in "${entries[@]}"; do
        [ "${entry%%:*}" = "$1" ] && { printf '%s\n' "${entry#*:}"; return 0; }
    done
    return 1
}
if [ -n "${FAKE_CONTAINERS+x}" ]; then
    case "$*" in
        "inspect -f {{.State.Running}} "*)
            state="$(lookup_container "${@: -1}")" || exit 1
            [ "${state}" = running ] && printf 'true\n' || printf 'false\n'
            exit 0
            ;;
        "container exists "*) lookup_container "$3" >/dev/null; exit $? ;;
    esac
fi
case "$1 $2" in
    "machine list")
        IFS=',' read -ra entries <<< "${FAKE_MACHINES}"
        for entry in "${entries[@]}"; do [ -n "${entry}" ] && printf '%s\n' "${entry%%:*}"; done
        ;;
    "machine inspect")
        case "$*" in
            *State*) lookup "$3" 2 ;;
            *Rootful*) lookup "$3" 3 ;;
            *) lookup "$3" 2 >/dev/null ;;
        esac
        ;;
    "machine "*) ;;  # init·start·stop — 기록만 한다
    "inspect -f") printf 'true\n' ;;
    *) ;;
esac
FAKE
chmod +x "${TMP}/bin/podman"

run_up() {
    # $1 = FAKE_MACHINES, 나머지 = 추가 env. 종료코드를 STATUS에, 호출 기록을 LOG에 남긴다.
    local machines="$1"
    shift
    : > "${TMP}/log"
    STATUS=0
    # -u는 대입보다 앞에 둔다(BSD env는 뒤에 오면 명령 이름으로 읽는다)
    env -u MANAGE_MACHINE -u MACHINE_NAME \
        PATH="${TMP}/bin:${PATH}" FAKE_LOG="${TMP}/log" FAKE_MACHINES="${machines}" "$@" \
        bash "${UP}" >/dev/null 2>"${TMP}/err" || STATUS=$?
    LOG="$(cat "${TMP}/log")"
}

fail() {
    printf 'FAIL(%s): %s\n--- 호출 기록 ---\n%s\n--- stderr ---\n%s\n' "$1" "$2" "${LOG}" "$(cat "${TMP}/err")" >&2
    exit 1
}

# 대조 — 가짜 podman이 실제로 불렸는지(PATH 배선 생존). 이게 비면 아래 「호출 없음」 단언이 전부 거짓 통과한다.
run_up 'podman-machine-default:running:true'
[ -n "${LOG}" ] || fail '대조' '가짜 podman 호출 기록이 비었다 — PATH 배선이 죽었다'

# 1) 실행 중 머신 → 그대로 재사용(시작·생성 없음)
[ "${STATUS}" -eq 0 ] || fail '실행중 재사용' "종료코드 ${STATUS}"
grep -qE '^machine (start|init)' <<< "${LOG}" && fail '실행중 재사용' '시작·생성이 호출됐다'

# 2) 중지된 머신 1개 → 그 머신을 시작해 재사용, 새 머신을 만들지 않는다(#182 본 경로)
run_up 'podman-machine-default:stopped:true'
[ "${STATUS}" -eq 0 ] || fail '중지 1개' "종료코드 ${STATUS}"
grep -qx 'machine start podman-machine-default' <<< "${LOG}" || fail '중지 1개' '기존 머신을 시작하지 않았다'
grep -q '^machine init' <<< "${LOG}" && fail '중지 1개' '새 머신을 생성했다'

# 3) 중지된 머신 2개 이상 → 모호하므로 아무것도 하지 않고 실패
run_up 'vm-alpha:stopped:true,vm-beta:stopped:true'
[ "${STATUS}" -ne 0 ] || fail '중지 2개' '실패하지 않았다'
grep -qE '^machine (start|init)' <<< "${LOG}" && fail '중지 2개' '시작·생성이 호출됐다'
if ! grep -q 'vm-alpha' "${TMP}/err" || ! grep -q 'vm-beta' "${TMP}/err"; then
    fail '중지 2개' 'stderr에 후보 목록이 없다'
fi

# 4) 중지된 머신 1개가 rootless → 시작하지 않고 실패
run_up 'podman-machine-default:stopped:false'
[ "${STATUS}" -ne 0 ] || fail '중지 rootless' '실패하지 않았다'
grep -qE '^machine (start|init)' <<< "${LOG}" && fail '중지 rootless' '시작·생성이 호출됐다'

# 5) 머신 0개 → 첫 설치 경로: 전용 머신 생성 후 시작
run_up ''
[ "${STATUS}" -eq 0 ] || fail '머신 없음' "종료코드 ${STATUS}"
grep -q '^machine init dagster-k8s' <<< "${LOG}" || fail '머신 없음' '전용 머신을 생성하지 않았다'
grep -qx 'machine start dagster-k8s' <<< "${LOG}" || fail '머신 없음' '전용 머신을 시작하지 않았다'

# 6) MANAGE_MACHINE=true는 기존 동작 유지 — 중지된 다른 머신이 있어도 전용 머신을 만든다
run_up 'podman-machine-default:stopped:true' MANAGE_MACHINE=true
[ "${STATUS}" -eq 0 ] || fail '전용 경로' "종료코드 ${STATUS}"
grep -q '^machine init dagster-k8s' <<< "${LOG}" || fail '전용 경로' '전용 머신을 생성하지 않았다'
grep -qx 'machine start podman-machine-default' <<< "${LOG}" && fail '전용 경로' '기존 머신을 시작했다'

# 7) 컨테이너 전부 실행중 → 재시작·생성 없음
REG_UP='kind-registry:running'
run_up 'podman-machine-default:running:true' FAKE_CONTAINERS="${REG_UP},seaweedfs:running,lakehouse-control-plane:running"
[ "${STATUS}" -eq 0 ] || fail '컨테이너 실행중' "종료코드 ${STATUS}"
grep -qE '^(start|run) ' <<< "${LOG}" && fail '컨테이너 실행중' '재시작·생성이 호출됐다'

# 8) VM 재부팅 뒤(#244) — S3·노드 둘 다 중지 → 둘 다 start, S3가 먼저. 7)의 「호출 없음」에 대한 양성 대조이기도 하다.
run_up 'podman-machine-default:running:true' FAKE_CONTAINERS="${REG_UP},seaweedfs:stopped,lakehouse-control-plane:stopped"
[ "${STATUS}" -eq 0 ] || fail '컨테이너 중지' "종료코드 ${STATUS}"
[ "$(grep -E '^start ' <<< "${LOG}")" = "$(printf 'start seaweedfs\nstart lakehouse-control-plane')" ] \
    || fail '컨테이너 중지' 'start가 S3→노드 순서로 정확히 두 번 불리지 않았다'

# 9) 노드 부재(첫 설치 전) → 만들지 않는다(스택 A 소유). 중지된 S3만 켠다.
run_up 'podman-machine-default:running:true' FAKE_CONTAINERS="${REG_UP},seaweedfs:stopped"
[ "${STATUS}" -eq 0 ] || fail '노드 부재' "종료코드 ${STATUS}"
grep -qx 'start seaweedfs' <<< "${LOG}" || fail '노드 부재' '중지된 S3를 켜지 않았다'
grep -qE '^(start|run) .*lakehouse-control-plane' <<< "${LOG}" && fail '노드 부재' '부재 노드를 켜거나 만들었다'
grep -qE '^run ' <<< "${LOG}" && fail '노드 부재' '컨테이너를 생성했다'

printf 'PASS: 대조 + 9경우(머신 6 + 컨테이너 실행중·중지·노드부재) 모두 기대대로\n'
