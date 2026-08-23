#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""조사 URL 일괄 승인 가드 — `researcher` 스코프 PreToolUse hook.

왜 이 스크립트인가:
    `researcher`는 저장소의 **질의 유출(DUA) 축 단일 통제 지점**이자
    **인젝션 격리 지점**인데, 그 통제에 **사람 관측점이 없었다**.
    `.claude/settings.json`의 `permissions.ask`에 맨이름 `WebFetch`·`WebSearch`를
    걸어 뒀지만 2026-08-20 프로브에서 **검색 3회 + 페치 6회가 전부 승인 프롬프트
    없이 통과**했다(허용목록 밖 도메인 포함). 맨이름은 매칭되지 않는 **죽은 규칙**이고,
    그래서 규약은 스스로 "이 조항의 실효는 100% 워커 자기 규율"이라 적고 있었다.

    이 가드는 그 축에 **기계 강제**를 넣는다. 조사는 2왕복으로 나뉜다 —
    `WebSearch`로 후보를 모아 **목록을 반환하고 정지**, 사람이 **일괄 승인**한 뒤
    승인분만 `WebFetch`한다. 승인 목록 밖은 여기서 `deny`된다.

🔴 도구별 «읽는 입력 키» (이 표가 이 가드의 급소다):

    | 도구        | 읽는 키   | 판정                                          |
    | ---------- | -------- | -------------------------------------------- |
    | `WebSearch`| `query`  | 통과 + 질의문을 로그에 남긴다(사후 관측점)        |
    | `WebFetch` | `url`    | 승인 매니페스트에 있으면 통과, 없으면 **`deny`**  |

    matcher 하나가 **두 도구에 걸치는데 읽는 필드 이름이 갈린다.** 한쪽 키만 읽으면
    나머지 도구에서 **조용히 무시된다**(`Edit`=`file_path` ↔ `NotebookEdit`=
    `notebook_path`에서 실제로 겪은 형태). 그래서 셋을 함께 둔다:
      ① 도구별로 키를 분기하고
      ② **키가 없으면 통과가 아니라 `deny`** 로 떨어뜨려 키 드리프트를 시끄럽게 만들고
      ③ 로그에 **`tool_input`의 실제 키 목록**을 남겨 표가 관측과 어긋나면 드러나게 한다
    ②가 핵심이다 — 표가 틀렸을 때 **조용히 통과**하면 "있다고 믿는" 게이트가 된다.

🔴 `ask`를 쓰지 않는다:
    auto 모드 분류기가 `ask`를 흡수한다(CLAUDE.md §강제 수단 실측).
    막을 것은 `deny`여야 막힌다 — `worker_path_guard.py`의 `OUTSIDE_STRICT`와 같은 논리.

🔴 이 가드는 **fail-closed**다 (`worker_path_guard.py`와 반대 방향):
    경로 가드는 다른 층이 받쳐주므로 fail-open이 맞지만, 여기는 **이 층이 유일**하다.
    입력 파싱 실패·키 부재·매니페스트 파손은 전부 `deny`로 떨어진다.
    통과시키면 **파일 하나 깨뜨리는 것이 게이트를 여는 수단**이 된다.
    (`WebSearch`만은 예외로 통과시킨다 — 아래 §한계.)

배선 (`.claude/agents/researcher.md` 프론트매터):
    hooks:
      PreToolUse:
        - matcher: "WebSearch|WebFetch"
          hooks:
            - type: command
              command: "$CLAUDE_PROJECT_DIR/scripts/research_gate_guard.py"

    🔴 `command`의 인용 규칙은 `.claude/settings.json`과 **다르다** — 안쪽 따옴표를
    이스케이프하면 YAML에서 벗겨지지 않아 경로가 깨지고 **에러 없이 통과**한다.
    배선을 바꾸면 실발동 확인(3셀 + 도구 축 1셀)을 다시 돌린다.

한계(정직하게 — 이 게이트가 덮지 **못하는** 축):
    - **`WebSearch`는 막지 않는다.** 검색이 후보를 찾는 수단이라 막으면 1왕복이
      성립하지 않는다. 대신 질의문을 로그에 남겨 **사후 관측점**을 만든다 —
      질의문에 내부 데이터를 넣지 않는 것은 여전히 워커의 규율이다(축이 다르다).
    - **supervisor 본세션은 대상이 아니다.** 프론트매터 hook은 그 서브에이전트에만
      걸린다. 매니페스트가 비어 있다고 "질의 유출 0건"이 아니다 — 세는 모집단이 다르다.
    - **`Bash`의 `curl`·`wget` GET은 이 matcher 밖**이다. `permissions.deny`는 발신 동사
      (`POST`·`PUT`·업로드)만 보므로 GET 유출은 여전히 규율 소관이다.
    - **승인 매니페스트를 쓰는 주체의 정당성은 보지 않는다** — 대조만 한다.
      🔴 `researcher`는 `Bash`를 갖고 있어 원리상 매니페스트를 스스로 쓸 수 있다.
      `permissions.deny`의 `Bash(*.research/approved*)` 계열이 그 축을 막지만,
      명령 문자열 매칭이라 우회 불가능하지는 않다 — **의도적 우회는 규율 위반이지
      기계가 닫는 문이 아니다.** 이 층이 막는 것은 *실수*와 *페이지 지시 추종*이다.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# 승인 목록과 질의 로그가 사는 곳(저장소 안, gitignore + pre-commit 차단).
#
# 🔴 **세션별 파일로 쪼개지 않는다.** hook 페이로드의 `session_id`는 **서브에이전트
#    자신의 것**이라 supervisor가 승인 시점에 알 수 없다 — 파일명에 넣으면 승인자가
#    맞출 수 없는 이름이 되어 매니페스트가 **영원히 안 읽힌다**("막았다고 믿는 상태"의
#    반대판: 늘 막혀 못 쓰는 상태). 한 파일에 모으고 `session_id`는 레코드 안에 남긴다.
#
# 🔴 **저장소 밖으로 빼지 않는다.** `data-extractor`의 `OUTSIDE_STRICT` 선례는 성격이
#    반대다 — 저쪽은 원천 진료 데이터를 저장소에 **안 남기려고** 밖에 쓰지만, 이쪽은
#    **감사 근거라 남아야 하고** `security`가 G2에서 읽는다. 내용도 PHI가 아니다.
RESEARCH_ROOT = Path(".claude/.research")
# 승인 목록과 로그는 **파일을 가른다** — `permissions.deny`가 승인 목록만 정밀하게
# 겨냥할 수 있어야 한다(로그는 hook 프로세스가 쓰므로 `permissions` 밖이다).
APPROVED_FILE = "approved.json"
QUERY_LOG = "queries.jsonl"

# 이 가드가 판정하는 도구와, 그 도구에서 **대상 값이 담기는 키**.
# 🔴 키 이름이 도구마다 갈린다 — 위 §도구별 «읽는 입력 키» 표 참조.
TOOL_KEYS = {"WebSearch": "query", "WebFetch": "url"}

# 이 가드가 통과시키는 도구. 나머지는 전부 대조를 거친다.
PASS_THROUGH = frozenset({"WebSearch"})


def deny(reason: str) -> None:
    """`deny` 결정을 내보내고 끝낸다."""
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    # 🔴 유효 enum은 allow·deny·ask·defer뿐이다. 벗어나면 출력 전체가
                    #    검증 실패해 **결정이 사라진 채 통과**한다(fail-open).
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                },
            },
            ensure_ascii=False,
        )
    )
    sys.exit(0)


def record(root: Path, entry: dict) -> None:
    """관측 로그에 한 줄 남긴다 — 기록 실패가 판정을 막지는 않는다.

    부정 결과(거부)도 남긴다. 관측 경로가 살아 있었음을 함께 보여야 "0건"이
    유효하기 때문이다(철학 원칙 7).
    """
    try:
        root.mkdir(parents=True, exist_ok=True)
        with (root / QUERY_LOG).open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_approved(root: Path) -> tuple[list[str], str]:
    """승인 목록과 매니페스트 **상태**를 읽는다.

    상태를 함께 돌려주는 이유: **"승인이 아직 없다"** · **"승인했는데 이 URL이 빠졌다"**
    · **"파일이 깨졌다"** 는 다음 행동이 서로 다르다. 거부 문구에서 그 셋을 가른다.
    """
    path = root / APPROVED_FILE
    if not path.is_file():
        return [], "none"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return [], "broken"
    entries = payload.get("approved") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return [], "broken"
    return [str(item) for item in entries], "ok"


def normalize(url: str) -> str:
    """대조용 정규화 — 스킴을 버리고 `호스트/경로`로 줄인다.

    쿼리스트링·프래그먼트를 떼는 이유: 같은 문서를 `?utm_source=` 붙여 다시 부르는 것을
    **다른 URL로 보면 승인이 무의미하게 반복**된다. 반대로 경로는 남긴다 —
    호스트만 보면 `github.com` 승인 하나가 그 호스트 전체를 여는 셈이 된다.
    """
    parts = urlsplit(url.strip())
    host = (parts.netloc or "").lower().removeprefix("www.")
    if not host:  # 스킴 없이 온 값 — 통째로 소문자화해 접두어 대조에 맡긴다
        return url.strip().lower().rstrip("/")
    return f"{host}{parts.path.rstrip('/')}"


def matches(target: str, entry: str) -> bool:
    """승인 항목 하나와 대조한다.

    승인 항목은 세 형태를 받는다 — 왕복을 줄이기 위해서다.
      `docs.getdbt.com/reference/resource-configs`  완전일치
      `docs.getdbt.com/reference/*`                 경로 접두어
      `docs.getdbt.com`                             호스트 전체
    """
    item = normalize(entry)
    if item.endswith("*"):
        return target.startswith(item.rstrip("*"))
    if "/" not in item:  # 호스트만 적힌 항목
        return target == item or target.startswith(item + "/")
    return target == item


def main() -> None:
    """researcher의 외부 조회를 승인 매니페스트와 대조해 통과시키거나 거부한다."""
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
    root = project_dir / RESEARCH_ROOT
    stamp = datetime.now(tz=timezone.utc).isoformat()

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # 🔴 fail-closed. matcher가 `WebSearch|WebFetch`에만 걸려 있으므로 여기 온
        #    입력은 둘 중 하나다 — 못 읽었다고 통과시키면 게이트가 조용히 사라진다.
        record(root, {"at": stamp, "event": "payload_unreadable"})
        deny(
            "조사 게이트가 hook 입력을 읽지 못했다. 통과시키지 않는다(fail-closed). "
            "supervisor에 보고하고 `scripts/research_gate_guard.py` 배선을 확인하라."
        )

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    session = str(payload.get("session_id") or "미상")
    key = TOOL_KEYS.get(tool)

    if key is None:
        # matcher가 넓게 걸렸을 뿐 이 가드의 소관이 아니다 — 여기는 통과가 맞다.
        sys.exit(0)

    value = str(tool_input.get(key) or "").strip()
    # 🔴 `input_keys`를 함께 남긴다 — 위 키 표가 관측과 어긋나면 여기서 드러난다.
    #    표가 맞다는 것을 **선언이 아니라 로그로** 확인하기 위한 필드다.
    entry = {
        "at": stamp,
        "session": session,
        "tool": tool,
        "input_keys": sorted(tool_input),
        "value": value,
    }

    if not value:
        # 🔴 키 드리프트를 **시끄럽게** 만든다. 조용히 통과하면 위 표가 틀렸을 때
        #    게이트가 있다고 믿는 상태가 되고, 그게 이 저장소가 반복해 겪은 형태다.
        record(root, {**entry, "decision": "deny", "why": "key_missing"})
        if tool in PASS_THROUGH:
            sys.exit(0)
        deny(
            f"`{tool}` 입력에서 `{key}`를 찾지 못했다(실제 키: "
            f"{', '.join(sorted(tool_input)) or '없음'}). "
            "도구의 입력 스키마가 바뀌었을 수 있다 — 통과시키지 않는다(fail-closed). "
            "supervisor에 보고하고 `TOOL_KEYS` 표를 실측으로 갱신하라."
        )

    if tool in PASS_THROUGH:
        # 검색은 통과시킨다(§한계). 질의문은 로그에 남아 사후 관측점이 된다.
        record(root, {**entry, "decision": "allow"})
        sys.exit(0)

    approved, state = load_approved(root)
    target = normalize(value)
    if state == "ok" and any(matches(target, item) for item in approved):
        record(root, {**entry, "decision": "allow"})
        sys.exit(0)

    detail = {
        "none": (
            "아직 승인된 목록이 없다. `WebSearch`로 후보를 모은 뒤 "
            "URL · 제목 · 도메인 · 출처 등급(A/B/C/D) · 필요 이유 1줄을 표로 "
            "반환하고 **정지**하라. 일괄 승인 후 재개 요청을 받으면 그때 페치한다."
        ),
        "broken": (
            "승인 매니페스트를 읽을 수 없다(JSON 파손). 통과시키지 않는다 — "
            "파일 파손이 게이트를 여는 수단이면 안 된다. supervisor에 보고하라."
        ),
        "ok": (
            f"승인 목록에 없다(현재 {len(approved)}건 승인됨). "
            "계획에 없던 출처가 필요해진 것이면 **그 항목만** 델타로 올려라 — "
            "목록 전체를 다시 올리지 마라."
        ),
    }[state]

    record(root, {**entry, "decision": "deny", "why": state})
    deny(
        f"`researcher`는 승인되지 않은 URL을 페치할 수 없다: {value}. {detail} "
        "정본은 docs/conventions/agents.md §researcher 조사 프로토콜이다."
    )


if __name__ == "__main__":
    main()
