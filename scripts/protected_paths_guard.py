#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""보호 경로 쓰기 가드 — `Bash` 우회를 막는 PreToolUse hook.

왜 이 스크립트인가:
    `permissions`의 `ask` 규칙은 **도구별**이다. `Edit(.claude/settings.json)`·
    `Write(.env)`를 걸어도 `Bash(python3 - <<'EOF' … write_text …)`나 `sed -i`,
    `>` 리다이렉트로 같은 파일을 쓰면 규칙에 걸리지 않는다(2026-08-18 실측 —
    권한 게이트를 보강하는 작업 자체가 그 경로로 이뤄졌다).

    그래서 `Bash` 명령 문자열을 보고, **보호 경로 + 쓰기 동작**이 함께 나타나면
    사용자에게 확인을 올린다(`ask`). 차단이 아니라 확인이다 — 정당한 편집도
    많고, 판단은 사람이 해야 한다.

    🔴 `permissionDecision`의 유효 값은 **`allow`·`deny`·`ask`·`defer`뿐**이다
    (CLI 2.1.226 실측: zod `Nr(["allow","deny","ask","defer"])`). 예전에 쓰던
    `escalate`는 스키마에 없어 **훅 출력 전체가 검증 실패**하고, 그 훅의 결정이
    폐기된 채 도구가 그냥 진행한다 — 에러 배너만 한 번 뜨고 **게이트는 fail-open**
    이었다(2026-08-19 실측: `cat .env > /dev/null`이 프롬프트 없이 통과).
    `defer`는 print-mode 전용이라 대화형에서는 무시된다.

    보호 경로는 **`.claude/settings.json`의 `ask` 규칙에서 자동 추출**한다.
    목록을 두 곳에 두면 반드시 어긋나므로 단일 출처를 유지한다.

    한계(정직하게): 문자열 휴리스틱이라 **완전하지 않다**. 변수 치환·인코딩·별칭으로
    얼마든 우회된다. 목표는 봉쇄가 아니라 **실수와 무심코를 잡는 것**이다.

두 축을 갖는다:
    `bash-pre`(기본, 인자 없음) — 위의 `Bash` 우회 차단.
    `file-pre` — 도구 경로(`Edit`·`Write`·`NotebookEdit`)로 **규약 정본**을 쓰려 할 때
        "착수 전 의도 탐색을 거쳤나"를 묻는다. 집행은 `permissions`의 `ask`가 하고
        이 훅은 **문구**를 붙인다 — `permissions.ask`는 매처 문자열 배열이라
        커스텀 문구를 담을 자리가 없다(2026-08-21 설계).

사용: `PreToolUse` hook에서 호출한다.
    scripts/protected_paths_guard.py            # matcher: Bash
    scripts/protected_paths_guard.py file-pre   # matcher: Edit|Write|NotebookEdit
"""

import json
import os
import re
import sys
from pathlib import Path

# `ask` 규칙에서 경로를 뽑을 도구들 — 파일을 직접 쓰는 도구만 대상으로 한다.
FILE_TOOL_RE = re.compile(r"^(?:Edit|Write|NotebookEdit)\((.+)\)$")

# 쓰기로 간주하는 신호. 읽기 전용 명령(cat·grep·json.load)은 걸리지 않는다.
# 🔴 여기 없는 신호는 경로 대조에 **도달조차 못 한다**(아래 조기 통과) — 파일을 만드는
#    모든 경로를 열거해야 한다. 2026-08-19 security 재컨펌에서 `install`·`tar`·`rsync`·
#    `ln`·`git checkout`류가 누락돼 절대경로인데도 통과하는 것이 실측됐다.
WRITE_SIGNALS = (
    ">",
    ">>",
    "tee",
    "sed -i",
    "perl -i",
    "cp ",
    "mv ",
    "rm ",
    "truncate",
    "dd ",
    "write_text",
    "writelines",
    "json.dump",
    "yaml.dump",
    "open(",
    "shutil.copy",
    "shutil.move",
    "unlink",
    "Path.write",
    # 아카이브 전개·복사·링크 — 파일을 만들지만 리다이렉트가 없다
    "install ",
    "tar ",
    "unzip ",
    "rsync",
    "ln ",
    "touch ",
    "patch ",
    "chmod ",
    # 네트워크에서 직접 파일로 받는 형태 (`curl * -o *` 규칙의 도구층 밖 보완)
    "--output",
    "-O ",
    "wget ",
    # 워킹트리를 덮어쓰는 git 명령
    "git checkout",
    "git restore",
    "git apply",
    "git stash pop",
    "git clean",
)

# 글롭 조각을 정규식으로 옮길 때 쓰는 치환 (`**/*.tfstate*` 같은 규칙 대응)
# 🔴 선두 `**/`는 **선택적 접두어**여야 한다 — 단순히 `**`→`.*`로 두면 뒤따르는 `/`가
#    리터럴로 남아 `.*`가 빈 문자열일 때도 `/`를 요구한다. 그러면 절대경로·`~` 형태는
#    잡히는데 **프로젝트 상대경로(`.claude/skills/x`)는 원리상 안 잡힌다**(실측).
#    같은 이유로 `terraform/**/*.tfstate*`가 `terraform/foo.tfstate`를 놓치고 있었다.
# 치환은 **순서가 의미를 가진다** — `?`→`.`를 먼저 끝내야 아래에서 넣는 `(?:.*/)?`의
#    `?`가 다시 치환되지 않는다.
GLOB_TO_RE = (
    (".", r"\."),
    ("?", "."),
    ("**/", "\x01"),  # 선택적 디렉터리 접두어
    ("**", "\x00"),  # 경로 구분자를 넘는 와일드카드
    ("*", "[^/]*"),  # 한 세그먼트 안의 와일드카드
    ("\x01", "(?:.*/)?"),
    ("\x00", ".*"),
)

# 설계 게이트 대상 — **규약 정본**이다.
# 🔴 위의 "보호 경로"(`.env`·tfstate·settings.json 등)와 **개념이 다르다**:
#    보호 경로는 "쓰면 위험한가", 여기는 "쓰기 전에 의도 탐색을 거쳤는가"를 묻는다.
#    겹치는 경로가 있어도 묻는 것이 달라 목록을 나눈다(중복이 아니라 다른 축).
#    집행은 `permissions`의 `ask`가 하고, 이 목록은 **어떤 문구를 띄울지**만 고른다 —
#    `permissions.ask`는 매처 문자열 배열이라 커스텀 문구를 담을 자리가 없기 때문이다.
#    🔴 2026-08-22 **축소** — `docs/conventions/**`(14파일)·`docs/architectures/**`
#    (9파일)를 뺐다. 게이트를 **경로 축**으로 걸었더니 `tech-writer`의 쓰기 범위
#    `docs/**`(35파일) 중 **23파일(66%)** 이 대상이 돼, 규칙을 바꾸는 편집과
#    오탈자·링크 교정이 구분 없이 전부 `ask`로 올라왔다. **전원이 매번 위반하는
#    규칙은 규칙이 아니다**(바로 아래 terraform·k8s를 뺀 것과 같은 논리).
#    ⇒ 판단 축을 **경로에서 가역성으로** 옮긴다: 문서 편집은 git이 되돌리고
#    커밋 전 `git diff`가 사람의 관문이며, `permissions.ask`의
#    `Bash(*git*commit*)`가 **최종 승인 1회**를 갖는다.
#    아래 남은 6종은 전부 **실행 규칙·통제 배선**이라 편집이 즉시 행동을 바꾼다
#    (가역이어도 되돌리기 전까지 이미 다르게 동작한다) — 그래서 남긴다.
#    규칙 요약본 `CLAUDE.md`가 남아 규약의 최상위 서술은 계속 게이트를 받는다.
#    문서 정본 2종을 규율로 내린 대가는 `worker_path_guard.py`의 `except` 축으로
#    일부 회수했다(`docs/security.md`·`docs/skills.md` = 판정 근거 문서는 `deny`).
#    🔴 **`ask`에서 규칙을 빼면 `Bash` 축 보호도 함께 사라진다**(2026-08-22 `security`
#    실측 — 선언되지 않은 2차 효과였다). `load_protected_patterns()`가 보호 경로를
#    **`permissions.ask`에서 파생**하기 때문이다(단일 출처 유지의 대가).
#    실제로 `Edit(docs/conventions/**)` 제거 후 `sed -i` 경유 수정이 **통과**한다.
#    ⇒ **`ask` 목록을 줄일 때는 도구 축이 둘 줄어든다**(파일 도구 + `Bash`)는 것을
#    함께 판단한다. 이번 건은 그래도 수용했다 — 완화의 근거가 "문서는 가역이고
#    최종 관문은 커밋"이라 `Bash` 축에도 같은 논거가 적용되기 때문이다.
CANON_PATTERNS = (
    "AGENTS.md",
    "CLAUDE.md",
    ".codex/**",
    ".claude/agents/**",
    "scripts/*_guard.py",
    "scripts/**/*_guard.py",
    "skills-lock.json",
    # 아래 2종은 2026-08-21 추가 — **설계 결정이 박히는 자리**다(규약 정본과 같은 축).
    # 🔴 `terraform/**`·`k8s/**`를 통째로 넣지 않은 이유: 매니페스트 편집은 대부분
    #    설계가 아니라 구현이라 게이트가 상시 발동한다. **전원이 위반하는 규칙은
    #    규칙이 아니다**(docs/skills.md §출처 등급별 통제가 같은 이유로 개정됐다).
    #    비가역 집행 축은 `permissions.ask`의 `*terraform apply*`·
    #    `*kubectl apply*`가 이미 잡는다.
    "compose.yml",  # 뼈대 서비스 구성 — 서비스 추가·제거는 설계 결정
    ".claude/settings.json",  # 권한·hook 배선 = 통제 설계 자체
)

# 설계 게이트 문구. 차단이 아니라 **소통**이다 — 정당한 편집이 다수라 `deny`가 아니다.
CANON_REASON = (
    "[설계 게이트] 규약 정본을 수정하려 한다: {targets}\n"
    "\n"
    "착수 전 의도 탐색을 거쳤는가?\n"
    "  · 무엇을 바꾸는지 한 문장으로 말할 수 있는가\n"
    "  · 왜 지금인가 — 반복 실적(Rule of Three)이 있는가\n"
    "  · 이 규칙이 「죽은 규칙」이 되지 않을 근거는 무엇인가\n"
    "\n"
    "아니라면 취소하고 계획 모드(plan)로 돌아가 사용자에게 설계를 제시하라.\n"
    "\n"
    "🔴 이 게이트는 「소통」이지 「봉쇄」가 아니다 — auto 모드는 `ask`를 흡수하므로\n"
    "   문구가 사람에게 안 뜰 수 있다. 그때도 위 3문항은 네가 스스로 답해야 한다."
)


def compile_globs(raws: tuple[str, ...]) -> list[tuple[str, re.Pattern[str]]]:
    """글롭 문자열들을 `(원문, 컴파일된 정규식)` 쌍으로 옮긴다.

    `load_protected_patterns`와 설계 게이트가 **같은 변환**을 쓰게 해 두 축의
    매칭 동작이 갈리지 않도록 한다(치환 순서가 의미를 갖는다 — `GLOB_TO_RE` 주석 참고).
    """
    compiled = []
    for raw in raws:
        converted = raw
        for src, dst in GLOB_TO_RE:
            converted = converted.replace(src, dst)
        compiled.append((raw, re.compile(converted)))
    return compiled


def load_protected_patterns() -> list[tuple[str, re.Pattern[str]]] | None:
    """`.claude/settings.json`의 `ask` 규칙에서 보호 경로 패턴을 뽑는다.

    읽지 못하면 `None`을 돌려준다 — 호출부는 이를 **통제 소멸**로 보고
    fail-closed 처리한다. 빈 리스트(`[]`, 규칙이 정말 0개)와 구분해야 한다.
    """
    # 🔴 상대경로로 읽으면 cwd가 프로젝트 루트가 아닐 때 패턴이 0개가 되고
    #    **에러도 없이 전부 통과**한다. 이 저장소는 `git worktree` 병렬 세션을
    #    표준으로 쓰고 서브디렉터리에서 세션을 열 수도 있어 실제 위험이다.
    #    hook 배선은 `$CLAUDE_PROJECT_DIR`로 절대경로를 쓰는데 스크립트 내부만
    #    cwd에 의존하면 기준이 어긋난다(2026-08-19 security 실측).
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    settings = root / ".claude/settings.json"
    if not settings.is_file():
        return None
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    patterns = []
    for rule in data.get("permissions", {}).get("ask", []):
        matched = FILE_TOOL_RE.match(rule)
        if not matched:
            continue
        raw = matched.group(1).strip()
        converted = raw
        for src, dst in GLOB_TO_RE:
            converted = converted.replace(src, dst)
        patterns.append((raw, re.compile(converted)))
    return patterns


def emit_ask(reason: str) -> None:
    """`ask` 결정을 내보내고 종료한다.

    🔴 `permissionDecision`의 유효 값은 `allow`·`deny`·`ask`·`defer`뿐이다.
    값 하나가 어긋나면 `hookSpecificOutput` 전체가 검증 실패해 **결정이 폐기된 채
    도구가 진행한다**(fail-open). 두 축이 같은 출력 함수를 쓰게 해 한쪽만 어긋나는
    일을 막는다.
    """
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                },
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


def run_file_guard(payload: dict) -> None:
    """도구 경로(`Edit`·`Write`·`NotebookEdit`)로 규약 정본을 쓰려 하면 설계를 묻는다.

    `permissions`의 `ask`가 집행을 하고, 이 훅은 **왜 묻는지**를 붙인다 —
    `permissions.ask`는 매처 문자열 배열이라 문구를 담을 자리가 없다.
    """
    tool_input = payload.get("tool_input") or {}
    # 🔴 matcher가 `Edit|Write|NotebookEdit` **3개 도구에 걸치는데 경로 키가 갈린다** —
    #    `Edit`·`Write`는 `file_path`, **`NotebookEdit`은 `notebook_path`**다.
    #    하나만 읽으면 그 도구에만 조용히 투명해진다(2026-08-20 `session_sync_guard`
    #    실측: 같은 배선인데 노트북 편집만 뚫려 있었다).
    raw_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if not raw_path:
        sys.exit(0)

    # 프로젝트 상대경로로 정규화한다. 절대경로·`./` 접두어 어느 쪽으로 와도 같은
    # 패턴에 걸려야 한다(`GLOB_TO_RE`의 선두 `**/` 주석과 같은 계열의 함정).
    root = str(Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve())
    path = str(Path(raw_path))
    if path.startswith(root + "/"):
        path = path[len(root) + 1 :]
    # 🔴 여기서 `lstrip("./")`를 쓰면 안 된다 — `lstrip`은 접두어가 아니라
    #    **문자 집합**을 벗겨서 `.claude/agents/x`의 **선두 점까지 먹는다**
    #    (→ `claude/agents/x`). 그러면 `.claude/agents/**` 축만 조용히 통과한다.
    #    2026-08-21 실측: 다른 셀 3개가 통과해 하마터면 그대로 갈 뻔했다.
    #    `Path()`가 이미 `./`를 정규화하므로 이 줄 자체가 불필요했다.

    hits = [
        raw
        for raw, compiled in compile_globs(CANON_PATTERNS)
        if compiled.fullmatch(path)
    ]
    if not hits:
        sys.exit(0)  # 정본이 아니다 — 조용히 통과

    emit_ask(CANON_REASON.format(targets=", ".join(sorted(set(hits)))))


def run_bash_guard(payload: dict) -> None:
    """Bash 명령이 보호 경로를 쓰려 하면 사용자 확인으로 올린다."""
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        sys.exit(0)

    if not any(signal in command for signal in WRITE_SIGNALS):
        sys.exit(0)  # 읽기 전용으로 보이는 명령 — 통과

    patterns = load_protected_patterns()
    hits = []
    tokens = re.findall(r"[\w./*@~-]{3,}", command)
    # 토큰 하나를 여러 형태로 펼친다. 형태를 빠뜨리면 **그 형태만 조용히 통과**하므로
    # 넓게 잡고, 과차단은 대조군 테스트로 관리한다(§5형태 매트릭스).
    candidates: set[str] = set()
    heads: set[str] = set()
    for token in tokens:
        for base in (token, token.lstrip("./")):
            # 🔴 접미어 전개 — 토큰 정규식이 `$`를 제외해 `$VAR/경로`가 `VAR/경로`로
            #    남는다. 접두어가 붙은 채로는 `.claude/agents/**` 같은 **앵커된 패턴**에
            #    걸리지 않는다(`$CLAUDE_PROJECT_DIR/.claude/agents/x`가 통과했다).
            #    `/` 뒤 조각을 전부 후보로 넣어 접두어 종류에 무관하게 만든다.
            parts = base.split("/")
            for i in range(len(parts)):
                suffix = "/".join(parts[i:])
                if not suffix:
                    continue
                candidates.add(suffix)
                # 🔴 디렉터리형 — `tar -C <디렉터리>`처럼 **대상이 디렉터리 자체**면
                #    `.../skills/**` 패턴의 뒷부분이 비어 매칭에 실패한다. `/`를 붙이면
                #    `.*`가 빈 문자열로 매칭된다.
                candidates.add(suffix + "/")
                heads.add(suffix)

    for raw, compiled in patterns or ():
        if any(compiled.fullmatch(c) for c in candidates):
            hits.append(raw)
            continue
        # 🔴 상위 디렉터리 — 보호 경로의 **부모**에 아카이브를 풀거나 동기화하면
        #    보호 대상이 생성·덮어쓰기된다(`tar -C .claude`). 패턴의 글롭 앞
        #    리터럴 머리와 대조한다.
        literal_head = raw.split("*")[0].rstrip("/")
        if literal_head and any(literal_head.startswith(h + "/") for h in heads):
            hits.append(raw)
    if patterns is None:
        # 🔴 fail-closed — 통제가 죽은 채 조용히 통과하는 것보다 낫다.
        reason = (
            "보호 경로 설정을 읽지 못했다(`.claude/settings.json`) — 이 가드의 통제가 "
            "**소멸한 상태**다. cwd나 `CLAUDE_PROJECT_DIR`를 확인하라. "
            "통제 없이 진행할 의도면 승인하라."
        )
    elif hits:
        targets = ", ".join(sorted(set(hits)))
        reason = (
            f"보호 경로에 쓰기 신호가 감지됐다: {targets}. "
            "`permissions`의 `ask`는 도구별이라 `Bash` 경로로는 "
            "걸리지 않는다 — 이 확인이 그 빈틈을 메운다. "
            "의도한 변경이면 승인하라."
        )
    else:
        sys.exit(0)

    emit_ask(reason)


def main() -> None:
    """서브커맨드 분기.

    인자가 없으면 기존 `Bash` 축으로 동작한다(**하위 호환** — 기존 hook 배선은
    인자 없이 이 스크립트를 부른다. 인자를 필수로 만들면 그 배선이 조용히 죽는다).
    """
    mode = sys.argv[1] if len(sys.argv) > 1 else "bash-pre"
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if mode == "file-pre":
        run_file_guard(payload)
    else:
        run_bash_guard(payload)


if __name__ == "__main__":
    main()
