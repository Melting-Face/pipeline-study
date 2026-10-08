"""`worktree_guard.py`가 루트 워킹트리 쓰기·커밋만 골라 막는지 검증한다.

🔴 **대조군이 핵심이다.** "루트에서 막힌다"만 보면 「선별 차단」과 「전부 차단」이
    구분되지 않는다 — 가드가 무조건 deny를 뱉어도 그 셀은 초록이다. 그래서
    worktree 쪽 통과 셀을 같은 수로 둔다(`git.md` §1-1이 브랜치 삭제 3층을
    검증할 때 쓴 것과 같은 형태).

🔴 **임시 저장소를 실제로 만든다.** 이 가드의 판정은 `git rev-parse`의 실행 결과라
    모킹하면 검증 대상이 사라진다(모킹한 값이 맞는지를 아무도 안 본다).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD = PROJECT_ROOT / "scripts" / "worktree_guard.py"


class WorktreeGuardTest(unittest.TestCase):
    """루트 / worktree / 저장소 밖 세 위치에서 가드의 결정을 본다."""

    def setUp(self):
        """메인 워킹트리와 그에 딸린 worktree를 갖춘 임시 저장소를 만든다."""
        self.temporary_directory = tempfile.TemporaryDirectory()
        base = Path(self.temporary_directory.name)

        # 🔴 `skipTest`가 아니라 **실패**다 — git이 없으면 이 가드의 판정축 자체가
        #    성립하지 않는다. 초록인데 안 돈 상태를 만들지 않는다(Issue #54).
        git = shutil.which("git")
        if git is None:
            self.fail("git 실행 파일이 필요하다 — 이 테스트는 skip하지 않고 실패한다")
        self.git = git

        # 🔴 부모 git이 물려주는 `GIT_*`를 걷어낸다. pre-commit 훅 안에서 돌 때
        #    `GIT_DIR`·`GIT_INDEX_FILE`이 상속돼 아래 임시 저장소의 명령이
        #    **바깥 저장소**를 건드린다(손으로 돌면 영영 안 드러난다).
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.environment.update(
            {
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.com",
            }
        )

        self.main_tree = base / "repo"
        self.main_tree.mkdir()
        self.run_git("init", "-b", "main", cwd=self.main_tree)
        (self.main_tree / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.run_git("add", "seed.txt", cwd=self.main_tree)
        self.run_git("commit", "-m", "seed", cwd=self.main_tree)

        self.worktree = base / "repo-feature"
        self.run_git(
            "worktree", "add", str(self.worktree), "-b", "feat/x", cwd=self.main_tree
        )

        # 🔴 가드는 **이 저장소인가**를 두 축으로 본다 — 가드 파일 자신의 위치와
        #    `CLAUDE_PROJECT_DIR`. 이 값을 안 주면 임시 저장소가 「다른 저장소」로
        #    분류돼 **차단 셀이 전부 통과**한다(초록이 아니라 빨강으로 드러났다).
        self.environment["CLAUDE_PROJECT_DIR"] = str(self.main_tree)

        # 🔴 **저장소 밖 fixture는 「별도 git 저장소」여야 한다.** 평범한 디렉터리로
        #    두면 이 축의 결정적 성질이 빠져 셀이 vacuous해진다 — 실제로 그랬고,
        #    그 사이 가드는 **아무 git 저장소의 메인 워킹트리를 전부 deny**했다.
        #    피해 대상이 `$OBSIDIAN_VAULT`(볼트)였고, 볼트는 git 저장소라
        #    **저널 기록이 통째로 막혔다**(감사기록 정본이다).
        #    fixture가 `.git`을 갖지 않으면 그 사실을 영영 못 본다.
        self.outside_repo = base / "other-repo"
        self.outside_repo.mkdir()
        self.run_git("init", "-b", "main", cwd=self.outside_repo)
        (self.outside_repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.run_git("add", "seed.txt", cwd=self.outside_repo)
        self.run_git("commit", "-m", "seed", cwd=self.outside_repo)

        # git 저장소가 아닌 디렉터리도 따로 둔다 — 두 축은 다르고, 하나로 묶으면
        # 어느 쪽이 통과시킨 것인지 갈리지 않는다.
        self.outside = base / "outside"
        self.outside.mkdir()

    def tearDown(self):
        """임시 트리를 정리한다."""
        self.temporary_directory.cleanup()

    def run_git(self, *args, cwd):
        """테스트 준비용 git 호출. 실패하면 그 자리에서 테스트를 세운다."""
        done = subprocess.run(  # noqa: S603 - 인자는 이 파일의 리터럴뿐이다
            [self.git, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=self.environment,
            check=False,
        )
        if done.returncode != 0:
            self.fail(f"준비용 git 실패: {args} → {done.stderr.strip()}")

    def decide(self, mode, payload):
        """가드를 실제로 돌려 결정값을 돌려준다. 통과면 `None`.

        통과는 **`exit 0` + 무출력**으로 표현된다(이 저장소 hook의 공통 관례).
        """
        done = subprocess.run(  # noqa: S603 - 인자는 이 파일의 리터럴뿐이다
            [sys.executable, str(GUARD), mode],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=self.environment,
            check=False,
        )
        assert done.returncode == 0, f"가드가 비정상 종료했다: {done.stderr}"
        if not done.stdout.strip():
            return None
        return json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"]

    def decide_path(self, path, key="file_path"):
        """파일 도구 페이로드로 경로 하나를 판정시킨다."""
        return self.decide("file-pre", {"tool_input": {key: str(path)}})

    def decide_command(self, cwd, command):
        """`Bash` 페이로드로 명령 하나를 판정시킨다."""
        payload = {"cwd": str(cwd), "tool_input": {"command": command}}
        return self.decide("bash-pre", payload)

    # ── file-pre ────────────────────────────────────────────────────────────

    def test_main_tree_write_is_denied(self):
        """루트 워킹트리의 파일 쓰기는 막힌다 — 이 가드의 존재 이유."""
        assert self.decide_path(self.main_tree / "docs" / "note.md") == "deny"

    def test_worktree_write_passes(self):
        """🔴 대조군 — worktree 쪽은 통과해야 「선별 차단」이 증명된다."""
        assert self.decide_path(self.worktree / "docs" / "note.md") is None

    def test_outside_plain_directory_passes(self):
        """git 저장소가 아닌 디렉터리는 판정 대상이 아니다(계획 파일 등)."""
        assert self.decide_path(self.outside / "plan.md") is None

    def test_other_git_repository_passes(self):
        """🔴 **다른 git 저장소의 메인 워킹트리**도 통과한다.

        이 셀이 없을 때 가드는 「이 저장소인가」를 묻지 않고 **아무 저장소의 메인
        워킹트리를 전부 `deny`**했다. 실제 피해 대상이 `$OBSIDIAN_VAULT`였고
        (볼트는 git 저장소다) **저널 기록이 통째로 막혔다** — 이 세션 체계의
        감사기록 정본이다.

        ⚠️ 게다가 볼트 경로에 *"`worktree-new.sh`로 이주하라"* 는 **성립하지 않는
        처방**을 띄웠다. `worker_path_guard.py`가 적어둔 교훈 그대로다 —
        **강등된 게이트보다 틀린 방향으로 유도하는 게이트가 더 위험하다.**
        """
        assert self.decide_path(self.outside_repo / "agents/2026-01-01/01-x.md") is None
        assert self.decide_path(self.outside_repo / "seed.txt") is None

    def test_notebook_path_key_is_read(self):
        """`NotebookEdit`은 경로 키가 `notebook_path`다.

        🔴 한 키만 읽으면 matcher는 걸리는데 핸들러가 no-op이 돼 **그 도구에만
        조용히 투명**해진다(matcher 함정 — 실제로 밟은 자리다).
        """
        target = self.main_tree / "explore.ipynb"
        assert self.decide_path(target, key="notebook_path") == "deny"

    def test_parent_traversal_is_denied(self):
        """worktree를 경유한 `../` 우회도 막힌다(`resolve()`가 따라간다)."""
        assert self.decide_path(self.worktree / ".." / "repo" / "CLAUDE.md") == "deny"

    def test_claims_is_not_whitelisted(self):
        """`.claims`는 더 이상 공유 레지스트리가 아니다 — 루트 쓰기와 같게 막힌다."""
        target = self.main_tree / ".claude" / ".claims" / "sessions" / "a.json"
        assert self.decide_path(target) == "deny"

    def test_settings_local_is_whitelisted(self):
        """`.claude/settings.local.json`은 worktree에서 루트로 향하는 링크 자산이다.

        🔴 링크라 `resolve()` 결과가 루트 경로가 된다. 화이트리스트가 없으면
        worktree에서조차 막힌다(실측).
        """
        target = self.main_tree / ".claude" / "settings.local.json"
        assert self.decide_path(target) is None

    def test_settings_json_is_not_whitelisted(self):
        """🔴 `settings.json`은 화이트리스트가 **아니다**.

        `settings.local.json`과 이름이 한 조각 다를 뿐인데 정책은 반대다
        (`git.md` §5가 "같은 `.claude/` 아래여도 글롭으로 묶지 않는다"고 적은
        것과 같은 축). 접두어 매칭으로 잘못 넓히면 통제 배선 파일이 함께 열린다.
        """
        assert self.decide_path(self.main_tree / ".claude" / "settings.json") == "deny"

    def test_env_is_not_whitelisted(self):
        """🔴 `.env`는 링크 자산 **2종 중 유일하게** 화이트리스트가 아니다.

        `worktree-new.sh`의 `LINK_ASSETS`는 `.env`·`.claude/settings.local.json`
        **2종**인데 `WHITELIST_RE`는 **1종**이다. 둘 다 루트로 향하는 링크인데
        `.env`만 목록에 없어 `deny`된다 — 비밀정보이고 편집할 대상이 아니기 때문이다.
        누가 `LINK_ASSETS`와 「맞춘다」며 `.env`를 더하면 이 셀이 빨개진다.
        """
        assert self.decide_path(self.main_tree / ".env") == "deny"

    # ── bash-pre ────────────────────────────────────────────────────────────
    # bash 축은 **커밋만** 판정한다. 쓰기 휴리스틱은 오탐(리다이렉트 조회 차단)과
    # fix 연쇄의 근원이라 철거했다(재정비 스펙 §설계 1).

    def test_bash_readonly_in_main_tree_passes(self):
        """🔴 대조군 — 루트의 조회 명령까지 막으면 읽기 전용이 아니라 마비다."""
        assert self.decide_command(self.main_tree, "grep -rn pattern docs/") is None

    def test_readonly_with_redirect_in_main_tree_passes(self):
        """🔴 회귀 — 리다이렉트가 붙은 조회가 루트에서 막히면 안 된다(실측 오탐 2회)."""
        command = "wc -l README.md AGENTS.md 2>/dev/null; ls docs 2>&1"
        assert self.decide_command(self.main_tree, command) is None

    def test_bash_write_in_main_tree_is_not_guarded(self):
        """선언된 공백 — Bash 쓰기는 이 가드가 보지 않는다(스펙 §설계 1에서 수용).

        ⚠️ 이 셀은 「고쳐야 할 빈틈」이 아니라 **결정의 고정**이다. 되살리려면
        스펙의 재검토 트리거를 먼저 충족해야 한다.
        """
        assert self.decide_command(self.main_tree, "echo x > CLAUDE.md") is None

    def test_bash_worktree_creation_passes(self):
        """🔴 규칙을 지킬 경로가 살아 있어야 한다.

        worktree를 만드는 행위 자체는 루트에서 일어난다 — 이걸 막으면 규칙이
        자기 탈출구를 막아 사람이 규칙을 끈다.
        """
        command = "./scripts/worktree-new.sh feat/demo"
        assert self.decide_command(self.main_tree, command) is None

    def test_bash_worktree_remove_passes(self):
        """`git worktree remove`는 `rm ` 신호를 품지만 통과해야 한다.

        🔴 판정 **순서**가 규칙이다 — 허용 검사를 쓰기 신호 뒤에 두면 정리 명령이
        막힌다(`worktree-new.sh`가 점유 축을 앞에 둔 것과 같은 이유).
        """
        command = "git worktree remove ../repo-feature"
        assert self.decide_command(self.main_tree, command) is None

    def test_bash_commit_in_main_tree_is_denied(self):
        """커밋은 파일을 만들지 않아 쓰기 신호에 안 걸린다 — 별도 축이다."""
        assert self.decide_command(self.main_tree, "git commit -m wip") == "deny"

    def test_bash_commit_in_worktree_passes(self):
        """🔴 대조군 — worktree 안에서의 커밋은 통과한다(「선별 차단」 증명)."""
        assert self.decide_command(self.worktree, "git commit -m wip") is None

    def test_relative_cd_into_main_tree_blocks_commit(self):
        """상대경로 `cd`로 루트에 들어가 커밋하는 것도 막는다."""
        command = f"cd ../{self.main_tree.name} && git commit -m x"
        assert self.decide_command(self.worktree, command) == "deny"

    def test_multi_step_cd_chain_blocks_commit(self):
        """🔴 같은 경로로 **커밋**도 막힌다.

        이 셀이 없으면 `git.md` §1의 *"`main` 직접 커밋 경로는 사라졌다"* 가 거짓이 된다
        — 문서가 선언한 보증을 테스트가 받치는 자리다.
        """
        command = f"cd .. ; cd {self.main_tree.name} ; git commit -m x"
        assert self.decide_command(self.worktree, command) == "deny"

    def test_commit_with_path_argument_is_denied(self):
        """🔴 `git -C <루트> commit` 처럼 **경로가 인자로 오는 커밋**도 막는다.

        커밋을 cwd로만 판정하던 수정의 빈틈이었다 — `cd /tmp` 로 cwd를 저장소 밖으로
        옮기면 통과했다. **「대상이 없다」는 전제가 형태마다 다르다.**
        """
        command = f"cd /tmp ; git -C {self.main_tree} commit -m x"
        assert self.decide_command(self.worktree, command) == "deny"

    def test_cd_with_leading_tokens_is_tracked(self):
        """🔴 `cd` 앞에 토큰이 붙어도 따라간다 — `(cd …)`·`pushd`·`cd --`.

        전부 **리터럴 경로**라 문서가 선언한 한계(변수·인코딩) **밖**이었다.
        """
        for form in (
            f"(cd {self.main_tree}; git commit -m x)",
            f"pushd {self.main_tree}; git commit -m x",
            f"cd -- {self.main_tree}; git commit -m x",
        ):
            assert self.decide_command(self.worktree, form) == "deny", form

    def test_write_outside_repo_from_root_cwd_passes(self):
        """🔴 **cwd가 루트여도 대상이 저장소 밖이면 통과한다**(과차단 방지).

        쓰기를 cwd로 판정하던 때는 cwd가 루트면 **대상이 어디든** 막혔고, 그래서
        볼트 저널(감사기록 정본)과 **순수 조회까지** 차단됐다 — `2>/dev/null`·`2>&1`의
        `>`가 쓰기 신호로 잡히기 때문이다. 고친 피해가 **축만 바꿔 재발**한 자리다.
        """
        vault_note = self.outside_repo / "agents/2026-01-01/01-x.md"
        assert self.decide_command(self.main_tree, f"echo x >> {vault_note}") is None
        assert self.decide_command(self.main_tree, "git status 2>/dev/null") is None
        probe = "uv run pytest 2>&1 | tail -5"
        assert self.decide_command(self.main_tree, probe) is None

    def test_allowed_command_alone_still_passes(self):
        """🔴 대조군 — 루트에서 worktree를 만드는 탈출구가 살아 있는가."""
        command = "./scripts/worktree-new.sh feat/demo && echo done"
        assert self.decide_command(self.main_tree, command) is None

    # ── fail 방향 ───────────────────────────────────────────────────────────

    def test_unknown_mode_fails_closed(self):
        """🔴 배선이 어긋나면 **막힌다**(fail-closed).

        통과는 `exit 0` + 무출력이라 **고장과 통과가 관측상 같다**
        (`test_guard_fail_direction.py`의 주제). 모드 오타로 통제가 조용히
        사라지는 것을 여기서 막는다.
        """
        payload = {"tool_input": {"file_path": str(self.main_tree / "docs" / "n.md")}}
        assert self.decide("typo-pre", payload) == "deny"

    def test_unresolvable_path_fails_closed(self):
        """경로 정규화가 깨져도 통과시키지 않는다.

        🔴 이 셀은 실제 결함을 잡았다 — 널 문자에서 `resolve()`가 `ValueError`를
        던지는데 안 잡으면 가드가 **크래시**하고, 크래시는 무출력 + 비-0 종료라
        하네스에게 「결정 없음」 = 통과와 같다(fail-open).
        """
        payload = {"cwd": "\x00bad", "tool_input": {"command": "git commit -m x"}}
        assert self.decide("bash-pre", payload) == "deny"


if __name__ == "__main__":
    unittest.main()
