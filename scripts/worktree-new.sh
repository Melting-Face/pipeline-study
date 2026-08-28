#!/usr/bin/env bash
# 병렬 세션용 git worktree 생성 + **비커밋 자산 배선**
# 사용: ./scripts/worktree-new.sh <type>/<kebab-요약> [--venv]
# 예:   ./scripts/worktree-new.sh feat/spark-thrift-poc --venv
#
# 왜 스크립트인가: `git worktree add` 자체는 한 줄이다. 실제 마찰은 **gitignore된 자산**이
# 새 worktree에 없다는 것이다(git.md §7 "비커밋 파일은 worktree마다 별도 준비"). 그 준비를
# 사람이 매번 기억해야 하면 규칙은 조용히 샌다 — 그래서 배선을 스크립트에 박는다.
#
# 🔴 **브랜치 상태는 4축이고 넷 다 처리한다**(§1 참고). "새로 만든다"만 다루면 병렬 세션이
#    서로의 브랜치를 밀어내는 상황 — 즉 이 스크립트가 존재하는 이유인 그 상황 — 에서
#    정작 못 쓰게 된다. 축을 다 세지 않으면 분기 하나로 닫고 **고쳤다고 믿는데 안 고쳐진**
#    상태가 된다(원칙 7).
#
# 🔴 **`.claude/.claims`는 복사가 아니라 심볼릭 링크다.** 이게 이 스크립트의 핵심이다.
#    worktree는 **파일**을 격리하지만 **클러스터·컨테이너**는 격리하지 못한다. 그런데
#    세션 간 충돌 감지(`session_sync_guard.py`)는 `$CLAUDE_PROJECT_DIR/.claude/.claims`를
#    보므로, worktree마다 레지스트리가 따로 생기면 **모든 세션이 "나 혼자"로 보인다**.
#    즉 worktree 도입이 피어 감지를 **조용히 끈다**(에러 없음 — 원칙 7 계열).
#    링크로 두면 가드 수정 없이 레지스트리가 공유된다(`Path.resolve()`가 링크를 따라가
#    `/.claude/.claims/` 매칭도 그대로 성립한다).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC_DIR="dagster/dockerfile.d/src"

# 링크로 공유할 비커밋 자산(단일 출처 유지가 이득인 것들).
#   .env                        — 비밀정보. 복사하면 사본이 늘고 회전 시 어긋난다.
#   .claude/.claims             — 세션 레지스트리. 공유되지 않으면 피어 감지가 죽는다(위 참고).
#   .claude/settings.local.json — 권한 오버라이드. 갈라지면 worktree마다 프롬프트가 달라진다.
LINK_ASSETS=(".env" ".claude/.claims" ".claude/settings.local.json")

log() { printf '\033[1;34m[worktree]\033[0m %s\n' "$1"; }
die() { printf '\033[1;31m[worktree]\033[0m %s\n' "$1" >&2; exit 1; }

BRANCH="${1:-}"
WITH_VENV="${2:-}"
[ -n "${BRANCH}" ] || die "브랜치명이 필요하다. 예: ./scripts/worktree-new.sh feat/spark-thrift-poc"

# 브랜치명 규약(git.md §1): <type>/<kebab-요약>
case "${BRANCH}" in
    feat/*|fix/*|docs/*|style/*|refactor/*|perf/*|test/*|build/*|ci/*|chore/*|revert/*) ;;
    *) die "브랜치명은 <type>/<kebab-요약> 형식이어야 한다(git.md §1): ${BRANCH}" ;;
esac

SLUG="${BRANCH#*/}"
WORKTREE_DIR="$(cd "${REPO_ROOT}/.." && pwd)/$(basename "${REPO_ROOT}")-${SLUG}"
[ ! -e "${WORKTREE_DIR}" ] || die "이미 존재한다: ${WORKTREE_DIR}"

# 1) worktree 생성. 같은 브랜치는 한 worktree에만 체크아웃되므로 중복 작업이 자연 차단된다.
#
#    브랜치 상태를 **4축**으로 가른다. **판정 순서가 규칙이다 — 점유 여부를 먼저 본다.**
#    로컬 존재 검사를 앞에 두면 축 3이 축 2로 흡수돼 `worktree add`가 raw git 에러를 뱉는다.
#    막히기는 하지만 **왜 막혔고 다음에 뭘 할지**를 알 수 없다 — 그게 이 분기의 목적이다.
#
#      축 1  어디에도 없음           → `-b`로 새로 만든다
#      축 2  로컬에 있고 미점유       → `-b`를 **빼고** 그 브랜치를 붙인다
#      축 3  다른 worktree가 점유 중  → 분기가 아니라 **안내**(git이 어차피 거부한다)
#      축 4  원격에만 있음           → `origin/<B>`를 시작점으로 추적 브랜치를 만든다
#
#    🔴 축 4에서 시작점을 **명시**하는 이유: `git worktree add <dir> <branch>`의 원격 추측
#       (DWIM)은 `--guess-remote` 설정과 remote 개수에 좌우돼 환경마다 갈린다. 명시하지 않으면
#       원격을 **추적하지 않는 별개 브랜치**가 조용히 생긴다(에러 없음 — 원칙 7 계열).
#       remote 이름은 `origin` 가정이다.
#
#    점유 경로는 `--porcelain`에서 뽑는다 — `branch` 줄 **직전의** `worktree` 줄이 그 경로다.
CHECKED_OUT_AT="$(
    git -C "${REPO_ROOT}" worktree list --porcelain \
        | awk -v want="branch refs/heads/${BRANCH}" '
            /^worktree / { wt = substr($0, 10) }
            $0 == want   { print wt; exit }
        '
)"

if [ -n "${CHECKED_OUT_AT}" ]; then
    # 축 3 — `worktree add`를 부르지 않고 여기서 끝낸다.
    die "브랜치 ${BRANCH} 는 이미 다른 worktree가 쓰고 있다: ${CHECKED_OUT_AT}
      한 브랜치는 한 worktree에만 체크아웃된다 — 이게 중복 작업의 암묵적 lock이다(git.md §7).
      선택지: ① 그 디렉터리에서 이어 작업한다 ② 다른 브랜치명을 쓴다 ③ 그쪽을 먼저 정리한다.
        git -C \"${REPO_ROOT}\" worktree list"
elif git -C "${REPO_ROOT}" show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    # 축 2 — 이미 있는 브랜치이므로 `-b`를 붙이면 "already exists"로 죽는다.
    log "생성: ${WORKTREE_DIR} (기존 로컬 브랜치 ${BRANCH} 를 붙인다)"
    ADD_ARGS=("${WORKTREE_DIR}" "${BRANCH}")
elif git -C "${REPO_ROOT}" show-ref --verify --quiet "refs/remotes/origin/${BRANCH}"; then
    # 축 4 — 로컬에는 없고 원격에만 있다.
    log "생성: ${WORKTREE_DIR} (원격 origin/${BRANCH} 를 추적하는 브랜치를 만든다)"
    ADD_ARGS=("-b" "${BRANCH}" "${WORKTREE_DIR}" "origin/${BRANCH}")
else
    # 축 1 — 신규.
    log "생성: ${WORKTREE_DIR} (새 브랜치 ${BRANCH})"
    ADD_ARGS=("${WORKTREE_DIR}" "-b" "${BRANCH}")
fi

# 호출은 **한 곳**에 둔다 — 분기마다 복사하면 옵션이 갈렸을 때 grep으로 못 찾는다.
git -C "${REPO_ROOT}" worktree add "${ADD_ARGS[@]}"

# 2) 비커밋 자산 링크. 원본이 없으면 건너뛴다(있는 것만 잇는다).
for asset in "${LINK_ASSETS[@]}"; do
    source_path="${REPO_ROOT}/${asset}"
    target_path="${WORKTREE_DIR}/${asset}"
    if [ ! -e "${source_path}" ]; then
        log "건너뜀(원본 없음): ${asset}"
        continue
    fi
    mkdir -p "$(dirname "${target_path}")"
    rm -rf "${target_path}"
    ln -s "${source_path}" "${target_path}"
    log "링크: ${asset} -> ${source_path}"
done

# 3) Python 환경. **링크하지 않는다** — venv에는 editable 설치
#    (`_editable_impl_dagster_project.pth`)가 들어 있어 **메인 트리의 소스**를 가리킨다.
#    링크하면 SQL·문서만 격리되고 파이썬 코드는 메인 트리 것이 도는 **반쪽 격리**가 된다.
#    (실측 1.2GB — 문서·SQL만 만지는 작업이면 생략하는 편이 옳다.)
if [ "${WITH_VENV}" = "--venv" ]; then
    log "uv sync — 약 1.2GB, 수 분 소요"
    (cd "${WORKTREE_DIR}/${SRC_DIR}" && uv sync)
    log "dbt deps"
    (cd "${WORKTREE_DIR}/${SRC_DIR}/dbt_pipelines" && ../.venv/bin/dbt deps)
else
    log "venv 생략(--venv 미지정) — python/dbt 실행이 필요하면:"
    log "  cd ${WORKTREE_DIR}/${SRC_DIR} && uv sync"
fi

cat <<EOF

✓ 준비 완료

  cd ${WORKTREE_DIR}

작업이 끝나면 (§7 정리):
  git -C "${REPO_ROOT}" worktree remove [--force] ${WORKTREE_DIR}
  git -C "${REPO_ROOT}" worktree prune
  git -C "${REPO_ROOT}" branch -D ${BRANCH}      # 병합 완료 후

  \`remove\`가 "contains modified or untracked files"로 거부하면 \`--force\`를 붙인다.
  링크가 무시되지 않는 리비전을 체크아웃한 worktree에서 그렇다(.gitignore의
  \`.claude/.claims\` 패턴이 슬래시 없이 들어간 커밋 이후로는 불필요).
  \`--force\`가 링크 **원본**을 지우지 않는다는 것은 실측으로 확인했다.

🔴 링크된 자산(.env·.claims·settings.local.json)은 **메인 트리와 같은 실체**다.
   링크를 **통해 쓴 내용**은 원본에 그대로 반영된다(그게 목적이다).
   반면 링크 자체를 지우는 것(\`rm\`·\`git worktree remove\`)은 원본을 건드리지 않는다 —
   \`rm -rf\`는 심볼릭 링크를 따라 들어가지 않고 링크만 끊는다.
   다만 \`rm -rf ${WORKTREE_DIR}/.env/\` 처럼 **끝에 슬래시**를 붙이면 링크를 따라간다. 붙이지 마라.
EOF
