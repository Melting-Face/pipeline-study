# Git 워크플로 규칙

> **범위**: 이 문서는 **작업 흐름**(브랜치·커밋 단위·세션 협업)을 다룬다. 세션마다 반복되는 git 작업의
> **일관성** 확보가 목적이다.
> 커밋 메시지 규약(Conventional Commits)·릴리스/태그·pre-commit·비밀정보의 **단일 출처는 [general.md](general.md)** 이며,
> 여기서는 중복 없이 링크한다.

## 1. 브랜치 전략

- **`main` = 배포·릴리스 기준.** 태그·릴리스는 `main`에서만 만든다([general.md](general.md#릴리스--태그)).
- **피처 브랜치 우선**: 다중 파일·기능·리스크 있는 변경은 브랜치에서 작업한 뒤 병합한다.
  - 브랜치명 `<type>/<kebab-요약>` — type은 커밋 type과 같은 계열이다.
    예: `feat/oci-k3s-terraform` · `fix/iceberg-orphan` · `docs/git-convention`.
- **`main` 직접 커밋**은 오타·문서 소폭 등 **사소·저위험**에 한정한다.

## 1-1. 브랜치 정리 — 머지 후 자동 삭제

**스위치는 하나가 아니라 3층**이고 서로를 대체하지 않는다 — 하나만 켜고 "정리했다"로 읽지 않는다
(신설 시점에 열린 PR **0건인데 원격 head 8개·로컬 9개**가 남아 있었다).

| 층 | 수단 | 지우는 것 |
| --- | --- | --- |
| ① 원격 | `gh repo edit <owner>/<repo> --delete-branch-on-merge` | PR 머지 시 **서버 측** head 브랜치 |
| ② remote-tracking | `git config --global fetch.prune true` | 원격에 없는 `origin/<name>` 참조 |
| ③ 로컬 | `gh alias set prm 'pr merge --squash --delete-branch'` | 로컬 브랜치 — **git엔 자동 수단이 없다** |

②는 로컬 브랜치를 안 건드려 전역이 안전하고, 그 부수 효과가 ③의 안전망이다 — upstream이 사라진
브랜치가 `git branch -vv`에 `[origin/x: gone]`으로 **보이게** 된다. ③을 별칭으로 두는 이유는
매번 `-d`를 붙이는 것이 규율 의존이기 때문이다. **③이 못 덮는 구멍 셋**: ⓐ 웹 UI 머지(원격만
지워지고 **로컬은 남는다**) ⓑ 다른 worktree에 체크아웃된 브랜치(`git branch -d` 거부 —
`git worktree remove` 선행) ⓒ `-d`의 기본 브랜치 switch가 `main`이 다른 worktree에 있으면 실패
(머지는 성공하므로 로컬만 수동 삭제). 점검은 `git fetch --prune && git branch -vv | grep ': gone]'`.

> **목록 조회이지 삭제가 아니며, 자동 삭제 별칭으로 만들지 않는다.** `: gone]`은 *"머지됐다"* 가 아니라
> *"원격이 없다"* 다 — 남이 원격만 지운 경우도 같은 표시고, squash분은 `-d`가 거부해 `-D`가 필요해진다.

🔴 **squash 저장소에서 `git branch --merged`는 머지 판정 근거가 아니다** — squash는 내용만 `main`에
넣고 SHA는 바꿔 브랜치 tip이 `origin/main`의 조상이 되지 않는다. 이 저장소 실측에서 두 축의 답이
갈렸다: `git branch --merged origin/main`은 **2개**, `gh pr list --state merged`는 **6개**
(#4·#5·#6·#7·#9·#10). ⇒ **판정 축은 PR 상태**로 잡고, `--merged`를 쓰려면 *"머지됐는데 안 잡히는
것이 모집단에서 빠진다"* 를 함께 적는다. 유실 여부는 **별개 축**이며
`git merge-base --is-ancestor <branch> origin/<branch>`로 본다(yes=뒤처짐만).

**검증 — "켰다"를 "작동한다"로 읽지 않는다(원칙 7).** 설정값 조회는 **켜졌다**까지다. 실집행은 다음
PR 머지 직후 **층을 갈라서** 본다 — 한꺼번에 보면 ①만 켜져도 ②가 작동한 것처럼 보인다(로컬 ref가
그냥 낡은 것일 뿐). ⓐ원격 head 소멸=① ⓑ`--prune` **없는** `git fetch`의 `origin/<브랜치>` 제거=②
ⓒ로컬 브랜치 소멸=③. **신설 시 관측**: ①②는 선언 확인 완료, **②는 실작동까지**(`--prune` 없는
`fetch`가 낡은 `origin/refactor/trino-lakehouse`를 제거). **①③의 실집행은 미확인** — 당시 열린 PR이
0건이라 머지시킬 대상이 없었다. 정리 결과는 원격 2개·로컬 2개·`gone` 0건.

## 2. 커밋 단위 — 논리적으로 쪼갠다

- **한 커밋 = 한 관심사.** 서로 다른 type(`feat`/`fix`/`docs`/`refactor`)을 한 커밋에 섞지 않는다.
- 기능과 **그 기능 전용 문서**는 함께 커밋해도 되지만, 무관한 변경은 분리한다.
- 리뷰·`revert` 용이성을 위해 큰 변경은 **의미 단위**로 나눈다.
- 스테이징은 경로 단위로 고른다(`git add <path>`). 이 저장소는 **대화형 플래그(`-i`/`-p`)를 쓰지 않으므로**,
  헝크 분리가 필요하면 **파일이 여러 관심사를 담지 않게 작성**해 파일 단위로 커밋을 설계한다.

## 3. 커밋 메시지

- **Conventional Commits** `type(scope): 설명`(한국어, 72자). 상세·type 표·기존 type 매핑은
  [general.md](general.md#커밋-메시지-conventional-commits). gitlint `commit-msg` 훅으로 강제된다.

## 4. 커밋 전 게이트 (pre-commit)

- 커밋 시 pre-commit이 `ruff`·`yamllint`·`gitleaks`·`gitlint` 등을 자동 실행한다([general.md](general.md#실행-pre-commit)).
- 훅 실패는 **수정 후 재커밋**한다. 우회(`--no-verify`)는 원칙적으로 금지(불가피하면 사유를 커밋 본문에 남긴다).

## 5. 커밋 금지 / 커밋 대상

- **커밋 금지**(비밀·상태·아티팩트):
  - `.env`·크리덴셜([general.md](general.md#비밀정보-secrets)),
  - Terraform `*.tfstate`·`terraform.tfvars`·API 개인키·`kubeconfig-oci`([terraform.md](terraform.md)),
  - 원천 데이터([../security.md](../security.md)).
  - `.gitignore`로 강제하고, 예시는 `*.example`만 커밋한다.
  - **`.claude/settings.local.json`** — 세션 중 승인한 `allow` 누적(개인 설정).
- **커밋 대상**(재현성): 락 파일 — `.terraform.lock.hcl`·`skills-lock.json`.
  - ⚠️ **`uv.lock`은 예외로 커밋하지 않는다.** 락 파일을 커밋하는 이유는 **재현성**인데,
    이 저장소에서 `uv.lock`은 그 값을 주지 않는다 — 이미지 빌드가 `pip install -e`를 쓰고 락을
    참조하지 않으며, 루트 `pyproject.toml`은 `[project]`가 없는 **도구 설정 전용**이라 루트 락에는
    잠기는 의존성이 0개다. "락 파일이니 커밋" 규칙을 기계적으로 적용하지 않는다.
  - **`.claude/settings.json`** — 프로젝트 공유 권한 게이트·hook 배선
    ([agents/permissions.md §통제 5층](agents/permissions.md#통제-5층)). 같은 `.claude/` 아래여도
    `settings.local.json`과 정책이 **반대**이므로 글롭으로 묶지 않는다.

## 6. AI 보조 세션에서의 git (Claude Code)

- **커밋·푸시는 사용자가 요청할 때만** 수행한다(임의 커밋·푸시 금지).
- 어시스턴트가 만든 커밋은 **`Co-Authored-By` 트레일러**를 남긴다(`Co-Authored-By: Claude ... <noreply@anthropic.com>`).
- **되돌리기 어려운 작업**(force push·history 재작성·브랜치/태그 삭제)은 **사전 확인** 후 진행한다.
- 세션 간 인계는 코드가 아니라 **문서·커밋 메시지**로 남긴다(추적성 — [philosophy.md](../philosophy.md)).

## 7. 병렬 세션 — git worktree (충돌 회피)

여러 세션/에이전트가 동시에 작업하면 **하나의 워킹트리·인덱스를 공유**해 충돌·오염이 발생한다.
**git worktree**로 브랜치마다 **독립 디렉터리**를 두어 물리적으로 격리한다.

- **원칙**: **세션/작업 = 브랜치 = worktree** 1:1:1. 한 브랜치는 **한 worktree에만** 체크아웃
  가능하므로 중복 작업이 자연스럽게 차단된다(암묵적 lock).
- **생성은 `./scripts/worktree-new.sh <type>/<kebab-요약> [--venv]`** 로 한다. 맨손 `git worktree add`는
  비커밋 자산이 안 따라와 **피어 감지가 조용히 꺼진다**.
- 🔴 **공유 트리에서는 `git commit -- <경로…>`로 pathspec을 못 박는다.** 인덱스는 세션 간 공유
  자원이라 `git add` 한 것만 인덱스에 있다는 보장이 없다. **`git add`와 섞지 않는다** — 섞으면
  에러 없이 **반만 커밋**된다.
- **격리는 다음 세션부터 듣는다** — `CLAUDE_PROJECT_DIR`가 세션 시작 시 고정되므로 실행 중인
  세션은 이주할 수 없고, 그들에겐 **pathspec 의무가 유일한 방어선**이다.

📖 **상세 정본은 [`git/worktree.md`](git/worktree.md)** — 도입 절차와 부작용(피어 감지·`.venv`
반쪽 격리·`.gitignore` 끝 슬래시), 사고 사례, **겹침 4축**(파일/hunk/전역 상태/인덱스), 귀속·리뷰
주체 명시, 파일 소실 판정까지 여기 있다. 병렬 세션으로 작업한다면 **읽고 시작한다**.

## 8. 세션 표준 흐름

```bash
git switch main && git pull                    # 1) 최신화
git worktree add ../repo-<요약> -b <type>/<요약>   # 2) 격리 worktree+브랜치 (또는 git switch -c)
# 3) 작업 → 논리 단위로 스테이징·커밋
git add <path> && git commit                   # (pre-commit·gitlint 통과)
git push -u origin <branch>                     # 4) push → PR/머지 → main에서 태그·릴리스
git worktree remove ../repo-<요약>              # 5) 정리
```

## 참고

- Conventional Commits: https://www.conventionalcommits.org/
- Pro Git (한국어): https://git-scm.com/book/ko/v2
- pre-commit: https://pre-commit.com/
