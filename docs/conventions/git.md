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
- **`main` 직접 커밋 경로는 사라졌다**(§7 worktree 의무화의 **선언된 대가**다).
  루트 워킹트리는 쓰기·커밋이 `deny`되고 `main`은 그 루트가 점유하므로, 오타 하나도
  피처 브랜치를 거친다. **조용히 죽은 규칙으로 두지 않으려고 여기 적는다** — 예전 규칙은
  *"사소·저위험에 한정"* 이었고, 그 "사소"의 판정을 기계가 못 해서 규칙이 새던 자리다.
  ⚠️ **범위는 AI 세션의 도구 호출까지다.** 사람이 셸에서 직접 치는 커밋·CI·외부 클론은
  이 가드의 사정권 밖이므로, 위 문장을 **저장소 레벨 브랜치 보호로 읽지 마라** — 다른 층이다.

## 1-1. 브랜치 정리 — 머지 후 자동 삭제

**스위치는 하나가 아니라 3층**이고 서로를 대체하지 않는다 — 하나만 켜고 "정리했다"로 읽지 않는다
(신설 시점에 **열린 PR이 0건인데 원격·로컬 브랜치는 여럿 남아 있었다**).

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
`fetch`가 낡은 `origin/refactor/trino-lakehouse`를 제거). **①③의 실집행은 미확인**이었다 — 당시
열린 PR이 0건이라 머지시킬 대상이 없었다. 정리 후 `gone` 상태는 **0건**으로 수렴했다.

**①은 이후 실집행까지 확인됐다**(PR #69). `gh pr merge`에 **`-d`를 일부러 붙이지 않고** 머지하니
원격 head가 404로 사라졌고, **대조군인 다른 열린 PR의 브랜치는 그대로 살아 있었다** — 대조군이
없으면 *"머지된 것만 지운다"* 와 *"전부 지운다"* 가 구분되지 않는다.
🔴 **`-d`를 붙였으면 이 귀속은 영구히 흐려진다** — 편의 플래그가 층을 겹쳐 실행해 *어느 층이
지웠는지* 갈 수 없게 만든다. **게이트마다 단독 작동 경로를 하나 남긴다.**

**#69에서 ③을 못 잰 것은 누락이 아니라 교환이다.** 같은 머지에서 `-d`를 안 썼으므로 로컬 브랜치는
남았고 `: gone]`이 붙었다(위 *"②의 부수 효과가 ③의 안전망"* 이 이때 실증됐다).
그 대가로 ①이 **단독 측정**됐다. 쌓인 `gone`은 위 점검 명령으로 그때그때 센다.

**③도 이후 실집행까지 확인됐다**(PR #70). `gh pr merge --squash --delete-branch`로 머지하니
머지한 브랜치의 **로컬 ref가 소멸**했고, **대조군은 전부 살아남았다** — `: gone]`이 붙어 있던 다른
브랜치들과 별도 worktree에 체크아웃된 브랜치가 그대로였다. ⇒ ③은 **그 브랜치만** 지운다.
**대조군이 없으면 「선별 삭제」와 「전부 지운다」가 구분되지 않는다**(①을 잴 때와 같은 형태).

⇒ **3층이 전부 실집행 확인됐다.** 귀속은 갈라서 읽는다 — **① = #69**(`-d` **없이** 머지) ·
**② = 신설 시**(`--prune` 없는 `fetch`) · **③ = #70**(`--delete-branch`로 머지).
🔴 **순서가 있었기에 둘 다 귀속된다.** #69가 `-d`를 안 써서 ①이 단독으로 잡혔고, 그 다음 머지에서
`-d`를 써서 ③이 잡혔다. **한 번에 했으면 위 「귀속이 영구히 흐려진다」에 걸려 둘 다 잃었다** —
게이트를 겹쳐 켜지 말고 **한 번에 하나씩 단독 작동시킨다**가 이 절의 결론이다.

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

🔴 **이 규칙은 조건부가 아니라 의무다.** 예전에는 *"main 워킹트리에 **다른 세션이 있으면** 옮긴다"* 였고
**그래서 지켜지지 않았다** — 세션은 그냥 저장소 루트에서 시작한다. 같은 날 실측이 두 번(도입 시점과
개정 시점) **worktree 0개 · 세션 4개 · 전원 `main`** 으로 똑같이 나왔다.
⇒ **문서 규칙은 이미 있었고 읽혔고 지켜지지 않았으므로**, 같은 층에 문장을 더하는 대신 기계 강제를 만든다.

- **원칙**: **세션/작업 = 브랜치 = worktree** 1:1:1. 한 브랜치는 **한 worktree에만** 체크아웃
  가능하므로 중복 작업이 자연스럽게 차단된다(암묵적 lock).
- **집행은 [`scripts/worktree_guard.py`](../../scripts/worktree_guard.py)** 가 한다(`PreToolUse`).
  저장소 **루트 워킹트리**에서의 파일 쓰기·`Bash` 쓰기·`git commit`이 **`deny`** 된다.
  판정축은 `git rev-parse --absolute-git-dir` **==** `--git-common-dir`(같으면 메인 워킹트리).
  - **근거 등급은 축마다 다르다.** 파일 쓰기·`Bash` 쓰기 두 축은 **라이브 프로브로 실호출
    확인**됐고(`cwd=워크트리` 헤드리스 세션 5셀 · `deny` 3건 전부 가드 원문 `[worktree 의무]` ·
    auto 모드 분류기 귀속 0건), **`git commit` 축은 단위 테스트 3종뿐이라 `미측정`** 이다.
    ⚠️ **가드가 커밋을 안 막는다는 뜻이 아니다** — `COMMIT_RE`가 따로 있고 테스트는 통과한다.
    갈리는 것은 **근거의 등급**이지 동작의 유무다. 축별 표는 [`git/worktree.md`](git/worktree.md).
  - **`ask`가 아니라 `deny`인 이유**: auto 모드 분류기가 **파일 도구의 `ask`를 경로 민감도와
    무관하게 흡수**한다([`agents/parallel.md`](agents/parallel.md) §「`ask`는 하드 스톱이 아니다」).
  - **예외는 고정 2종뿐이고 탈출구는 없다** — `.claude/.claims/**` · `.claude/settings.local.json`.
    둘은 worktree에서 루트로 향하는 **심볼릭 링크**라 `resolve()` 결과가 루트 경로가 된다
    (화이트리스트가 없으면 **worktree에서조차 막힌다** — 실측). 환경변수 스위치는 두지 않았다.
    ⚠️ 부수 효과: 이 둘은 **메인 워킹트리에서 직접 편집해도 통과**한다(경로로만 판정하므로
    링크 경유인지 아닌지 갈리지 않는다). 세션 레지스트리·권한 오버라이드라 등급이 낮지만,
    **선언되지 않은 통과**로 남기지 않으려고 적는다.
  - **판정 대상은 「이 저장소」뿐이다** — 다른 git 저장소의 메인 워킹트리는 통과한다.
    이 조건이 없을 때 `$OBSIDIAN_VAULT`(볼트도 git 저장소다)가 통째로 막혀
    **저널 기록이 끊겼다**. 감사기록 정본이고, 게다가 볼트 경로에 *"worktree로 이주하라"* 는
    **성립하지 않는 처방**을 띄웠다 — 강등된 게이트보다 **틀린 방향으로 유도하는 게이트가
    더 위험하다**(`worker_path_guard`가 대소문자 축에서 같은 교훈을 적어뒀다).
  - **읽기·조회는 그대로다.** `worktree-new.sh`·`git worktree add|remove|prune`도 통과한다 —
    **규칙이 자기 탈출구를 막으면 사람은 규칙을 끈다**(판정 순서상 허용을 먼저 본다).
    ⚠️ **한때 거짓이었다**: 쓰기를 cwd로 판정하던 구현에서는 루트에서의 `git status 2>/dev/null`
    같은 **순수 조회까지 막혔다**(`2>/dev/null`의 `>`가 쓰기 신호로 잡힌다). 지금은 쓰기를
    **대상 경로**로 판정해 해소됐지만, **이 문장이 한동안 실태와 반대였다**는 사실을 남긴다 —
    *"읽기는 된다"* 는 선언은 **읽기를 실제로 쏴 보기 전에는 쓸 수 없다.*
- **생성은 `./scripts/worktree-new.sh <type>/<kebab-요약> [--venv]`** 로 한다. 맨손 `git worktree add`는
  비커밋 자산이 안 따라와 **피어 감지가 조용히 꺼진다**.
- 🔴 **공유 트리에서는 `git commit -- <경로…>`로 pathspec을 못 박는다.** 인덱스는 세션 간 공유
  자원이라 `git add` 한 것만 인덱스에 있다는 보장이 없다. **`git add`와 섞지 않는다** — 섞으면
  에러 없이 **반만 커밋**된다.
- **세션 도중 이주는 가능하다**(정정). 예전 서술 *"실행 중인 세션은 이주할 수 없다"* 는
  하네스 **`EnterWorktree`** 도입 전의 것이다. 절차는 **`worktree-new.sh`로 만들고
  `EnterWorktree`의 `path`로 들어간다** — 순서가 규칙이다. `EnterWorktree`가 직접 만든
  worktree에는 비커밋 자산 링크가 없어 **피어 감지가 조용히 꺼진다**.
- **다만 가드 배선은 다음 세션부터 듣는다** — `hooks`는 **정의 로드 시점 스냅샷**이다
  ([`agents/parallel.md`](agents/parallel.md) §반영 시점). 배선 이전에 시작한 세션에는
  **pathspec 의무가 유일한 방어선**이다. ⚠️ **이주 가능성과 배선 발효는 다른 축**이라
  하나로 묶어 읽으면 "옮겼으니 걸린다"는 오독이 된다.

📖 **상세 정본은 [`git/worktree.md`](git/worktree.md)** — 도입 절차와 부작용(피어 감지·`.venv`
반쪽 격리·`.gitignore` 끝 슬래시), 사고 사례, **겹침 4축**(파일/hunk/전역 상태/인덱스), 귀속·리뷰
주체 명시, 파일 소실 판정까지 여기 있다. 병렬 세션으로 작업한다면 **읽고 시작한다**.

## 8. 세션 표준 흐름

```bash
git -C <루트> fetch --prune                       # 1) 최신화 (루트는 읽기 전용이다)
./scripts/worktree-new.sh <type>/<요약> [--venv]  # 2) 격리 worktree+브랜치 — 맨손 add 금지
# 3) EnterWorktree 로 그 경로에 들어간 뒤 작업 → 논리 단위로 스테이징·커밋
git add <path> && git commit                   # (pre-commit·gitlint 통과)
git push -u origin <branch>                     # 4) push → PR/머지 → main에서 태그·릴리스
git worktree remove ../<repo>-<요약>            # 5) 정리
```

## 참고

- Conventional Commits: https://www.conventionalcommits.org/
- Pro Git (한국어): https://git-scm.com/book/ko/v2
- pre-commit: https://pre-commit.com/
