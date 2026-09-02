#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""워커 지시문의 §참고 스킬 표와 `docs/skills.md` §③의 **배선 정합**을 검사한다.

사용법:
    uv run scripts/skill_wiring_check.py     # 또는 python3 (의존성 없음)

왜 이 스크립트인가:
    스킬↔워커 배선은 두 곳에 적혀 있다. **각 워커 지시문의 §참고 스킬 표가 집행
    정본**이고 `docs/skills.md` §③은 **파생 인덱스**다(런타임에 §③은 워커 컨텍스트에
    없다). 두 곳이 갈리면 문서를 읽고 판단하는 사람과 실제로 집행되는 것이 어긋나는데,
    **어긋나도 아무것도 깨지지 않아** 조용히 드리프트한다. 그래서 커밋 전에 대조한다.
    🔴 검사 방향은 **워커 → 문서**다. 지시문이 사실이고 문서가 따라간다.

    🔴 이 검사기는 「표의 정합」을 지키지 「표가 지켜지는지」를 지키지 않는다.
    표가 지켜지는지는 런타임에 `scripts/skill_gate_guard.py`가 `Skill` 도구 호출을
    가로채 본다. 같은 표를 읽지만 **시점도 실패 모드도 다르다** — 저쪽은 fail-closed
    `deny`, 이쪽은 exit 1이다. 한쪽이 초록이라고 다른 쪽이 초록인 것이 아니다.

    🔴 프론트매터에 새 키를 만들지 않는다. 배선 사실은 하네스가 실제로 해석하는
    기존 `tools:`·`skills:`에서 읽는다 — 검사기만 아는 키를 새로 두면 그 키가 비어도
    **항상 통과**하는 죽은 규칙이 된다.

계측 단위(무엇을 세는가):
    - 「위반」은 **규칙과 대상의 조합 1건**이다(워커 1명의 스킬 2종이 갈리면 2건).
    - 「등재 스킬」은 표 행 수이지 고유 스킬 수가 아니다 — 둘을 함께 출력한다.
    - 4상태(S0~S3)는 **워커 수**를 센다.

검사에서 빼는 것(빠뜨린 것과 구분하기 위해 명시한다):
    - **표의 3열(제약·단서·대체)** — 코드스팬이 많지만 죽은 참조 이름·금지 명령이라
      스킬 등재가 아니다. 2열만 본다.
    - **스킬 본문(`.claude/skills/<name>/SKILL.md`)의 내용** — 등재 여부만 보고
      단서가 지켜지는지는 보지 않는다(그건 `/skill-audit` 감사 축이다).
    - **`docs/skills.md`의 출처 등급(A~D)·프리로드 조건·lock 관리** — 그 정본은
      `docs/skills.md` 자신이고 워커 지시문에 사본이 없다. 대조할 상대가 없다.
    - **3상태(S0/S1/S2)의 옳고 그름** — 「빠뜨린 것」과 「안 둔다」의 구분은 설계
      선택이다. 한 형태로 강제하면 검사기가 그 축을 지운다. 상태는 출력만 한다.

검증 (2026-08-24, 프로덕션 경로 그대로 — 각 프로브 후 원상 복구):
    대조군  워커 13 / §③ 행 13 / 등재 25행(11종 고유) / lock 16 · 디스크 16 · exit 0
            🔴 대조군만으로는 아무것도 증명되지 않는다(빈 집합끼리 비교해도 0건이다).
            손으로 센 25행과 일치하는 것이 「검사했다」의 첫 증거다.
    P1 지시문에서 `sql-optimization` 행 1줄 삭제
       → `R2 data-engineer: ... §③에만 있다` · exit 1
    P2 §③ `data-verifier` 셀에 `running-dbt-commands` 추가
       → `R2 data-verifier: ... §③에만 있다` · exit 1
    P4 같은 셀에 `docker-expert`(어휘 밖) 추가
       → `R6 ... 죽은 참조이거나 스킬이 아니다` — P2와 **따로** 잡힌다
    P3 §③ `skill-matcher`의 `**없음**` → `**없 음**`(오탐 방어 1겹 파괴)
       🔴 어휘 필터가 워커 측에만 걸렸을 때: `R2 ... `researcher`이 §③에만 있다`
          — **틀린 처방**이다(워커 이름을 지시문 표에 추가하라고 말한다).
       ✅ 어휘 필터를 양쪽에 걸어 고친 뒤: `R6 ... 스킬이 아니다` — 진단이 맞는다.
          ⇒ 1겹은 발견을 막고 2겹은 **틀린 처방**을 막는다(막는 것이 다르다).
    P6b `Skill` 없는 `tech-writer`의 §참고 스킬에 표 1행 추가
       → `R4 ... 물릴 수 없는 죽은 등재다` + `R2` + `R3`가 **각각** 발동
       ⚠️ 반대 방향(`Skill`은 있고 등재 0건)은 **미확인** — 프론트매터 `tools:`에서
          도구를 빼는 프로브는 통제 배선을 건드리므로 돌리지 않았다.
    P5 `security.md` 3열의 `\|` → `|`(셀 경계 파손)
       → `표 셀 수가 3이 아니다` · **exit 2**. 🔴 0건으로 조용히 통과하지 않는다.
    P7 §참고 스킬 절 없는 워커 파일 추가 → `R1 ... §③에 행이 없다` · exit 1
    P8 `skill_gate_guard.py`를 stdin 페이로드로 직접 호출해 **두 파서를 대조**:
       `analyst`+`sql-optimization` → allow / `analyst`+`kubernetes-specialist` → deny
       (등재분 나열이 이 검사기의 `analyst` 집합과 일치) / `tech-writer` → 빈 표 deny
       🔴 이 대조가 없으면 "두 파서가 같은 표를 읽는다"는 **주장일 뿐**이다.

R7·R8 검증 (전용 worktree · 합성 디렉터리로 격리):
    🔴 실제 설치분을 건드리면 공유 트리의 피어에게 그대로 보인다. 그래서 합성했다.
    P-b R7 — `LOCK_REQUIRED_KEYS`에 `skillPath`를 한시 추가(실데이터에 결손 1건)
       → `R7 dagster-integrations: ... `skillPath`가 없다` · exit 1. 원복 확인.
       🔴 lock 파일을 프로브 대상으로 삼지 않았다 — 공급망 정본이라
          변인을 검사기 쪽에 뒀다.
    P-c R8 불일치 — 합성 19종 중 1종 rename
       → `lock 등재인데 디스크에 없다` + `디스크에 있는데 lock 밖이다` · exit 1
    P-c2 R8 역방향 — lock 밖 디렉터리 1종 추가 → `고정되지 않은 설치다` · exit 1
    P-d R8 **오탐 방어** — `.claude/skills/` 부재
       → `R8 미확인(디스크 부재)` **stderr** · findings 0 · **exit 0**
       ✅ worktree가 이 상태라 별도 조작 없이 실증됐다. 19건 오탐이 아니다.

⚠️ R8의 실효 범위 — **커밋 축에서는 거의 안 돈다.**
    `.claude/skills/`는 gitignore라 **디스크 설치분만 바뀌면 pre-commit 훅의 `files:`
    트리거가 안 걸려 훅 자체가 실행되지 않는다**(`.pre-commit-config.yaml`이 이미 자인).
    R8이 실제로 발동하는 경로는 ① `/skill-audit` 수동 실행 ② `files:`에 걸린 다른
    파일을 함께 커밋할 때 뿐이다.
    🔴 이걸 적지 않으면 "디스크 드리프트를 기계화했다"가 거짓이 된다.
"""

import json
import re
import sys
from pathlib import Path

# --- 정본 경로 ---
AGENTS_DIR = Path(".claude") / "agents"
SKILLS_DOC = Path("docs") / "skills.md"
LOCK_FILE = Path("skills-lock.json")
SKILLS_DISK = Path(".claude") / "skills"

# §참고 스킬 절을 집어내는 패턴 — 헤딩부터 다음 `## ` 또는 EOF까지.
# 🔴 `scripts/skill_gate_guard.py`와 **같은 패턴을 복사**해 둔 것이다. 공용 모듈로
#    빼지 않는 이유: 저쪽은 PreToolUse hook이고 fail-closed라, 의존 파일이 사라지면
#    **전 워커의 스킬 호출이 deny**된다. 훅의 의존 표면을 늘리지 않는다.
#    대가는 두 곳이 갈릴 수 있다는 것이고, 그건 아래 §검증의 대조 프로브가 막는다.
SECTION_RE = re.compile(r"^## 참고 스킬.*?(?=^## |\Z)", re.DOTALL | re.MULTILINE)

# 표 행에서 셀을 가른다. 🔴 이스케이프 `\|`를 경계로 읽지 않는다 —
# `security.md` 3열의 `` `\| sh` `` 가 실제 사례다(순진한 split은 셀이 밀린다).
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")

# 셀에서 코드스팬을 뽑는다. 스킬명 어휘는 소문자·숫자·하이픈이다.
CODESPAN_RE = re.compile(r"`([a-z0-9][a-z0-9-]*)`")

# §③ 매핑 표를 잡는 앵커. 🔴 섹션 범위로 잡지 않는다 — §③ 안에 다른 표가 3개 더
#    있어 경로 표·재채점 표를 함께 먹는다. 헤더 행 자체를 앵커로 쓰고,
#    매치가 정확히 1회가 아니면 **판정 불가(exit 2)** 로 낸다.
MAPPING_HEADER = "| 워커 | 주 스킬 | 제약 |"

# 등재 0건을 뜻하는 셀 표기. 이것으로 **시작하면** 코드스팬을 스캔하지 않는다.
# 🔴 오탐 방어 1겹이다 — `researcher` 행은 `**없음** — 후보 조사 요청은 `skill-audit`가
#    낸다` 라 셀 안에 코드스팬이 있는데 그것은 스킬이 아니라 **커맨드 이름**이다.
#    ⚠️ 이 사례는 **의도적으로 유지한다.** 원래 실사례는 폐기된 `skill-matcher` 행이었고
#    (셀 안 코드스팬이 **워커 이름**이었다) 그 행이 사라지면 방어를 검증할 데이터가
#    저장소에서 없어져 **코드는 남고 프로브만 재현 불가**가 된다.
# 🔴 2겹은 어휘 필터(lock 등재분 + 디스크 설치분)이고 **두 겹이 막는 것이 다르다**:
#    1겹은 발견 자체를 막고, 2겹은 **틀린 처방을 막는다**. 1겹이 깨지면 `researcher`가
#    유령 등재가 되는데, 어휘에 없으므로 "§③에만 있다 → 지시문에 추가하라"(틀린 처방)가
#    아니라 "스킬이 아니다"(R6)로 떨어진다. 붕괴 축이 문안 표기 / 디스크 데이터로
#    갈려 동시에 죽지 않는다. ⚠️ 어휘 필터를 **양쪽에** 걸어야 성립한다 —
#    워커 측에만 걸었을 때 P3에서 실제로 틀린 처방이 나왔다(2026-08-24 프로브).
NONE_MARKERS = ("**없음**", "없음")

# R7이 요구하는 lock 항목의 필수 키.
# 🔴 `skillPath`는 **일부러 뺐다.** 19종 중 `dagster-integrations` 1종이 이 키가 없는데,
#    `skillPath`는 로컬 경로가 아니라 **출처 저장소 내부 경로**라 디스크에서 역산할 수
#    없고 그 스킬은 업스트림이 소멸해 확인하러 갈 원본도 없다. 형제 항목의 패턴을
#    복사해 넣으면 **검산을 통과하는 틀린 값**이 된다 — 없는 것보다 나쁘다.
#    ⇒ 확정 가능한 3키만 게이트로 걸고, 결손 1건은 Issue로 연다.
LOCK_REQUIRED_KEYS = frozenset({"source", "sourceType", "computedHash"})

# R9가 추적하는 루브릭 어휘 — 게이트 2축 + 채점 3축 + 임계.
AUDIT_CMD = Path(".claude") / "commands" / "skill-audit.md"
RUBRIC_TOKENS = (
    "권한 정합",
    "정본 무충돌",
    "스택 일치",
    "호출 빈도",
    "대체 불가",
    "★3",
)

# 워커 지시문이 「루브릭을 재등장시켰다」고 볼 임계.
# 🔴 축 이름 1개 인용은 위반이 아니다 — `data-verifier`·`researcher`·`data-extractor`는
#    *"내 등재가 0건인 이유는 축1 탈락"* 처럼 **자기 판정 사유**를 적으며 축을 부른다.
#    문제는 **판정 절차 전체의 복제**다. 관측 분포가 이 둘을 명확히 가른다 —
#    폐기 전 `skill-matcher.md`가 6/6이었고 나머지 셋은 각 1/6이었다.
#    ⇒ 임계 3. 값이 아니라 **분포의 간격**이 근거다(6 vs 1 사이 어디를 잘라도 같다).
RUBRIC_ECHO_THRESHOLD = 3


def cells_of(row: str) -> list[str]:
    """표 행을 셀 목록으로 가른다(양끝 빈 칸 제거·공백 정리)."""
    parts = CELL_SPLIT_RE.split(row)
    return [p.strip() for p in parts[1:-1]] if len(parts) >= 3 else []


def skills_in(cell: str) -> list[str]:
    """스킬 열 1칸에서 등재 스킬명을 뽑는다(0건이면 빈 목록)."""
    if cell.startswith(NONE_MARKERS):
        return []  # 🔴 1겹 — 「없음」 셀은 코드스팬을 아예 보지 않는다
    found = []
    for item in cell.split(" · "):  # 접미 라벨 `(A)`·`**(C)**` 는 첫 스팬 뒤에 온다
        span = CODESPAN_RE.search(item)
        if span:
            found.append(span.group(1))
    return found


def main() -> int:
    """워커 지시문과 skills.md §③의 배선을 대조하고 불일치를 출력한다."""
    root = Path(__file__).resolve().parent.parent
    findings: list[str] = []

    # 1) 워커 지시문을 읽어 워커별 (등재 스킬, 상태, tools, skills) 를 만든다
    worker_files = sorted((root / AGENTS_DIR).glob("*.md"))
    if not worker_files:
        print(f"검사 대상 없음: {AGENTS_DIR}/*.md", file=sys.stderr)
        return 2

    listed: dict[str, list[str]] = {}
    state: dict[str, str] = {}
    has_skill_tool: dict[str, bool] = {}
    preloaded: dict[str, list[str]] = {}

    for path in worker_files:
        name = path.stem
        text = path.read_text(encoding="utf-8")

        # 프론트매터 — 하네스가 해석하는 키만 읽는다(새 키를 만들지 않는다)
        tools_line = re.search(r"^tools:(.*)$", text, re.MULTILINE)
        tools = [
            t.strip() for t in (tools_line.group(1) if tools_line else "").split(",")
        ]
        has_skill_tool[name] = "Skill" in tools
        block = re.search(r"^skills:\n((?:\s+-\s+\S+\n)+)", text, re.MULTILINE)
        preloaded[name] = re.findall(r"-\s+(\S+)", block.group(1)) if block else []

        section = SECTION_RE.search(text)
        if section is None:
            listed[name], state[name] = [], "S0"  # 절 자체가 없다
            continue

        rows = [ln for ln in section.group(0).splitlines() if ln.startswith("|")]
        header = next(
            (r for r in rows if cells_of(r)[:2] == ["상황", "스킬"]),
            None,
        )
        if header is None:
            listed[name], state[name] = [], "S1"  # 절은 있고 표가 없다
            continue

        skills: list[str] = []
        for row in rows[rows.index(header) + 2 :]:  # 헤더·구분행 다음부터
            cs = cells_of(row)
            if len(cs) != 3:
                print(f"{AGENTS_DIR}/{name}.md: 표 셀 수가 3이 아니다 — {row[:60]}")
                return 2  # 🔴 판정 불가. 0건으로 적지 않는다
            skills.extend(skills_in(cs[1]))
        listed[name] = skills
        state[name] = "S3" if skills else "S2"

    # 2) docs/skills.md §③ 매핑 표를 읽는다 — 헤더 행 앵커, 정확히 1회여야 한다
    doc_text = (root / SKILLS_DOC).read_text(encoding="utf-8")
    doc_lines = doc_text.splitlines()
    anchors = [i for i, ln in enumerate(doc_lines) if ln.strip() == MAPPING_HEADER]
    if len(anchors) != 1:
        print(
            f"{SKILLS_DOC}: §③ 매핑 표 헤더가 {len(anchors)}회 매치 "
            f"(기대 1회) — 표를 특정할 수 없다"
        )
        return 2  # 🔴 판정 불가

    documented: dict[str, list[str]] = {}
    for line in doc_lines[anchors[0] + 2 :]:
        if not line.startswith("|"):
            break
        cs = cells_of(line)
        if len(cs) != 3:
            print(f"{SKILLS_DOC}: §③ 행의 셀 수가 3이 아니다 — {line[:60]}")
            return 2
        who = CODESPAN_RE.search(cs[0])
        if who is None:
            print(f"{SKILLS_DOC}: §③ 1열에서 워커명을 못 읽었다 — {line[:60]}")
            return 2
        documented[who.group(1)] = skills_in(cs[1])

    # 3) 어휘 목록 — lock 등재분 + 디스크 설치분(오탐 방어 2겹의 재료)
    lock = json.loads((root / LOCK_FILE).read_text(encoding="utf-8"))
    lock_entries = lock.get("skills", {})
    lock_names = set(lock_entries)
    disk = root / SKILLS_DISK
    disk_present = disk.is_dir()
    disk_names = (
        {p.name for p in disk.iterdir() if p.is_dir()} if disk_present else set()
    )
    vocabulary = lock_names | disk_names

    # 3-1) R7 lock 스키마 — 항목마다 필수 키가 있는가
    findings += [
        f"R7 {name}: `skills-lock.json` 항목에 `{key}`가 없다"
        for name in sorted(lock_entries)
        for key in sorted(LOCK_REQUIRED_KEYS - set(lock_entries[name]))
    ]

    # 3-2) R8 디스크 드리프트 — lock 등재분 ↔ 실제 설치분
    # 🔴 「디렉터리 부재」와 「불일치」를 가른다. 부재는 worktree·CI·클론 직후의
    #    정상 상태이고, 순진하게 대칭차를 내면 lock 전종이 오탐으로 뜬다. 그러면
    #    "전원이 매번 위반하는 규칙"이 되어 훅이 통째로 무시된다.
    if not disk_present:
        print(
            f"R8 미확인(디스크 부재) — {SKILLS_DISK}/ 가 없다"
            "(worktree·CI·클론 직후). 드리프트를 판정하지 않는다.",
            file=sys.stderr,
        )
    else:
        findings += [
            f"R8 `{s}`: lock 등재인데 디스크에 없다"
            for s in sorted(lock_names - disk_names)
        ]
        findings += [
            f"R8 `{s}`: 디스크에 있는데 lock 밖이다 — 고정되지 않은 설치다"
            for s in sorted(disk_names - lock_names)
        ]

    # 3-3) R9 루브릭 문안 드리프트 — 정본 2곳에 다 있고, 워커 지시문에는 없어야 한다
    # 🔴 폐기된 `skill-matcher` 워커가 루브릭 정본이던 시절, 같은 루브릭이 4곳에
    #    서술돼 있었고 **문안 정합을 대조하는 기계가 없었다.** 그래서 감사자를
    #    없애면서 이 축을 기계로 내린다.
    cmd_path = root / AUDIT_CMD
    if not cmd_path.is_file():
        findings.append(f"R9 {AUDIT_CMD} 가 없다 — 루브릭 정본이 사라졌다")
    else:
        cmd_text = cmd_path.read_text(encoding="utf-8")
        findings += [
            f"R9 루브릭 어휘 `{t}`가 {where}에 없다 — 두 정본이 갈렸다"
            for t in RUBRIC_TOKENS
            for where, body in ((AUDIT_CMD, cmd_text), (SKILLS_DOC, doc_text))
            if t not in body
        ]
    for path in worker_files:
        echoed = [t for t in RUBRIC_TOKENS if t in path.read_text(encoding="utf-8")]
        if len(echoed) >= RUBRIC_ECHO_THRESHOLD:
            findings.append(
                f"R9 {path.stem}: 워커 지시문에 루브릭이 재등장했다 "
                f"({len(echoed)}/{len(RUBRIC_TOKENS)}: {' '.join(echoed)}) "
                f"— 판정 절차의 정본은 {AUDIT_CMD} 하나다"
            )

    # 4) 어휘 필터를 **양쪽에** 걸어 「스킬」과 「스킬이 아닌 코드스팬」을 가른다.
    #    🔴 R2(집합 일치) 판정에는 어휘 안의 것만 넣는다 — 유령 이름을 R2에 흘리면
    #    "지시문에 추가하라"는 **틀린 처방**이 나온다(§NONE_MARKERS 주석의 P3).
    unknown: list[tuple[str, str, str]] = []  # (워커, 스킬명, 어느 쪽)
    for name, names in listed.items():
        unknown += [(name, s, "지시문") for s in sorted(set(names) - vocabulary)]
        listed[name] = [s for s in names if s in vocabulary]
    for name, names in documented.items():
        unknown += [(name, s, "§③") for s in sorted(set(names) - vocabulary)]
        documented[name] = [s for s in names if s in vocabulary]

    # 5) R1 워커 커버리지 — 양방향 대칭차
    findings += [
        f"R1 {n}: 워커 파일은 있는데 §③에 행이 없다"
        for n in sorted(set(listed) - set(documented))
    ]
    findings += [
        f"R1 {n}: §③에 행이 있는데 워커 파일이 없다"
        for n in sorted(set(documented) - set(listed))
    ]

    # 6) R2 스킬 집합 일치 · R3 등재 0건 정합
    for name in sorted(set(listed) & set(documented)):
        mine, theirs = set(listed[name]), set(documented[name])
        findings += [
            f"R2 {name}: `{s}`이 지시문에만 있다 — §③에 추가한다"
            for s in sorted(mine - theirs)
        ]
        findings += [
            f"R2 {name}: `{s}`이 §③에만 있다 — 지시문이 정본이다"
            for s in sorted(theirs - mine)
        ]
        if bool(mine) != bool(theirs):
            findings.append(
                f"R3 {name}: 등재 0건 표기가 갈린다 "
                f"(지시문 {state[name]}={len(mine)}종 / §③={len(theirs)}종)"
            )

    # 7) R6 실재성 — 양쪽의 어휘 밖 코드스팬(죽은 참조 또는 스킬이 아닌 이름)
    findings += [
        f"R6 {n}: {side} 2열의 `{s}`이 lock·디스크 어디에도 없다 "
        "— 죽은 참조이거나 스킬이 아니다"
        for n, s, side in unknown
    ]

    # 8) R4 `Skill` 도구 배타 · R5 프리로드 부분집합
    for name in sorted(listed):
        if has_skill_tool[name] and not listed[name]:
            findings.append(
                f"R4 {name}: `tools:`에 `Skill`이 있는데 등재 0건 "
                "— 온디맨드 호출 경로가 표 없이 열려 있다"
            )
        if listed[name] and not has_skill_tool[name]:
            findings.append(
                f"R4 {name}: 등재 {len(listed[name])}종인데 `tools:`에 `Skill`이 없다 "
                "— 물릴 수 없는 죽은 등재다"
            )
        findings += [
            f"R5 {name}: `skills:`의 `{s}`이 §참고 스킬 표에 없다 "
            "— 프리로드는 등재를 전제한다"
            for s in sorted(set(preloaded[name]) - set(listed[name]))
        ]

    # 9) 상태·모집단을 항상 출력한다 — 부정 결과는 모집단과 함께여야 유효하다
    buckets = {
        code: sorted(n for n in state if state[n] == code)
        for code in ("S0", "S1", "S2", "S3")
    }
    print(
        f"워커 {len(listed)} / §③ 행 {len(documented)} / "
        f"등재 {sum(len(v) for v in listed.values())}행"
        f"({len({s for v in listed.values() for s in v})}종 고유) / "
        f"lock {len(lock_names)} · 디스크 {len(disk_names)}",
        file=sys.stderr,
    )
    labels = {
        "S0": "절 없음",
        "S1": "절 있고 표 없음",
        "S2": "표에 「없음」",
        "S3": "등재 ≥1",
    }
    for code, names in buckets.items():
        print(
            f"{code} {labels[code]:16s}: {' '.join(names) or '(없음)'}", file=sys.stderr
        )

    for line in findings:
        print(line)
    print(f"\n배선 위반 {len(findings)}건", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
