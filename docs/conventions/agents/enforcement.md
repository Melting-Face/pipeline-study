# 가드 배선과 실발동 확인

> 에이전트 규약 인덱스는 [`../agents.md`](../agents.md).
> 통제 5층·경로 경계는 [`permissions.md`](permissions.md), hook 결정값은 [`parallel.md`](parallel.md).

규약 중 **기계가 판정할 수 있는 것**은 문서가 아니라 hook이 강제한다.
규약은 각 세션의 컨텍스트 안에만 있고 **파일시스템은 하나**이기 때문이다.

## 가드 5종

| 가드 | 막는 것 | 배선처 |
| --- | --- | --- |
| [`journal_guard.py`](../../../scripts/journal_guard.py) | 런타임별 저널 `NN` 경합 · 규약 위반 생성 · 기록 누락 | Claude `settings.json` + Codex `hooks.json` |
| [`protected_paths_guard.py`](../../../scripts/protected_paths_guard.py) | 보호 경로의 `Bash` 우회 쓰기 | `settings.json` |
| [`session_sync_guard.py`](../../../scripts/session_sync_guard.py) | 세션 간 중복 작업 · 워킹트리 전역 git | `settings.json` |
| [`analyst_path_guard.py`](../../../scripts/analyst_path_guard.py) | `analyst` 경로 경계 | **`analyst.md` 프론트매터** |
| [`worker_path_guard.py`](../../../scripts/worker_path_guard.py) | 워커별 경로 경계(워커명을 인자로) | **각 워커 프론트매터** |
| [`research_gate_guard.py`](../../../scripts/research_gate_guard.py) | 미승인 `WebFetch` · 질의문 로깅 | **`researcher.md` 프론트매터** |
| [`plan_mirror_guard.py`](../../../scripts/plan_mirror_guard.py) | (통제 아님) 계획서 볼트 미러 | `settings.json` |

전부 의존성 없음·PEP 723. 경로 가드는 **fail-open**(가드 실패가 작업을 막지 않는다),
조사 게이트만 **fail-closed**다.

🔴 **배선처가 둘이라 "가드가 N종 있다"는 서술로는 강제 여부를 알 수 없다** —
강제되는 것은 **배선된 (가드 × 워커) 쌍**이다.
워커별 범위는 `settings.json`으로 걸 수 없어 프론트매터에 둔다.

### 인용 규칙이 배선처마다 다르다

🔴 **`settings.json` 표기를 프론트매터로 복사하면 조용히 죽는다.**

| 배선처 | `command` 표기 |
| --- | --- |
| `.claude/settings.json`(JSON) | `"\"$CLAUDE_PROJECT_DIR\"/scripts/….py"` |
| 에이전트 정의 프론트매터(YAML) | `"$CLAUDE_PROJECT_DIR/scripts/….py"` |

프론트매터에서는 이스케이프된 안쪽 따옴표가 벗겨지지 않는다.
**명령이 깨지면 에러가 아니라 그냥 통과**하므로 도구 결과만으로는 "막힌 것"과 구분되지 않는다.

## hook 이벤트

| 이벤트 | 서브커맨드 | 하는 일 | 실패 시 |
| --- | --- | --- | --- |
| `SessionStart` | `session-start` | 런타임별 다음 `NN`·열린 미션을 주입하고 Codex는 세션 Git 기준점을 저장 | 주입·기준점 없음 |
| `PreToolUse`(`Write`) | `pre-write` | 신규 저널의 런타임 경로·`NN`·파일명·날짜 폴더 위반을 차단 | 통과 |
| `Stop` | `stop` | 변경 세션의 오늘자 저널 부재·`updated` 미갱신을 보정 요청 | 경고 없음 |
| `PreToolUse`(`Bash`) | — | 보호 경로 + 쓰기 신호 동시 감지 시 `ask` | 통과 |
| `PreToolUse`(`Agent`) | `agent-pre` | 같은 작업을 다른 세션이 실행 중이면 `ask`, 완료했으면 결과 요약 주입 | 통과 |
| `PostToolUse`(`Agent`) | `agent-post` | 내 claim을 `done`으로 바꾸고 결과 요약을 남긴다 | 기록 없음 |
| `PreToolUse`(파일 3종) | `file-pre` | 다른 세션이 최근 20분 내 고친 파일이면 `ask` | 통과 |
| `PostToolUse`(파일 3종) | `file-post` | 파일 리스를 내 세션으로 갱신·인계 | 리스 미갱신 |
| `PreToolUse`(`Bash`) | `bash-pre` | 워킹트리 전역 git 명령을 다른 세션이 살아 있을 때 실행하면 `ask` | 통과 |
| `SessionEnd` | `session-end` | 내 리스·실행 중 claim·생존 신호를 회수(완료 claim은 보존) | TTL이 회수 |

- Claude `Stop`은 exit 0 + JSON `systemMessage` 경고다. Codex는 SessionStart의 HEAD·상태·
  변경 해시와 Stop 시점을 비교하고, 기록이 없으면 `decision: block`으로 **한 번만** 대화를 이어
  `archivist` 보정을 요청한다. 두 번째 Stop(`stop_hook_active: true`)은 `systemMessage`만 반환해
  무한 반복을 막는다.
- **기존 저널 수정·`_` 접두 파일·볼트 밖 경로는 검사하지 않는다** — 가드는 넘버링에만 관여한다.
- Codex CLI 0.149.1에서 파일 수정은 최상위 `exec` 안의 `tools.apply_patch(...)`로 기록된다.
  `.codex/hooks/worker_path_guard.py`와 `journal_pre_write.py`는 직접 `apply_patch` 입력과
  freeform `exec` 문자열을 모두 정규화해 patch 헤더를 추출한다. patch가 아닌 일반 `Bash`와
  읽기 `exec`는 통과시킨다.
- 워커 역할은 transcript 첫 `session_meta.payload.agent_role`을 우선 사용한다. 구버전
  developer 지시문 표식은 fallback이고, 서브에이전트 patch에서 역할을 식별하지 못하면
  fail-closed한다. Codex archivist 경계는 기존 날짜 저널의 `agent: codex`까지 확인해
  Claude 이력 수정을 막는다.
- 세 가드는 기존 hook trust 인덱스를 보존하도록 하나의 `PreToolUse` 그룹에 두고
  `Bash|exec|apply_patch`를 함께 받는다. hook 파일 변경 후 현재 세션의 자식 워커는 시작 시
  스냅샷을 계속 쓰므로, 새 세션에서 `/hooks` 신뢰 또는 검토된
  `--dangerously-bypass-hook-trust` 프로브로 실발동을 확인한다.
- 경로는 **두 런타임 모두 `agents/<날짜>/`** 다(평탄화됨). `NN`은 그날의
  **단일 수열**이라 런타임이 갈려도 번호가 겹치지 않는다 — 갈라 두었을 때 실제로
  **같은 날 `01`이 두 개** 생겼다.
- ⚠️ **폴더를 공유하므로 경계 축이 경로에서 내용으로 옮겨졌다.** 양쪽 archivist 모두
  기존 저널의 frontmatter `agent:`를 읽어 **남의 런타임 기록 수정**을 막고, 신규는
  **다음 번호일 때만** 통과시킨다(Claude: `scripts/worker_path_guard.py`
  §`is_claude_journal_path` / Codex: `.codex/hooks/worker_path_guard.py`
  §`is_codex_journal_path`). **둘은 짝이라 한쪽만 고치면 경계가 비대칭이 된다.**
  ⚠️ **그 경계의 결정값은 `ask`이고, 파일 도구의 `ask`는 auto 모드 분류기가 흡수한다**
  ([`parallel.md`](parallel.md) §hook 결정값) — **뜨지만 멈추지는 않는다.** 경로 축이던 때와
  **동등이지 강화가 아니다.** 그리고 판정이 frontmatter `agent:` **한 필드**에 걸려 있어
  그 값이 역할명 등으로 오염되면 경계가 함께 흔들린다(실발생 — 오늘자 저널 하나가
  `agent: archivist`로 적혔다). 오염의 방향은 안전하지만(더 막힌다) **그 "더"가 `ask`라서
  실효는 규율이 진다.** ⇒ **"내용으로 막는다"를 "확실히 막힌다"로 읽지 않는다.**
- `$OBSIDIAN_VAULT`가 없는 환경에서는 **조용히 통과**한다 —
  가드가 개인 환경 의존성을 세션의 전제조건으로 만들면 안 된다.

## 실발동 확인 (배선을 바꿨으면 필수)

**세 층은 서로 다르고, 셋 다 확인해야 강제가 선다.**

| 층 | 확인 | 통과 기준 |
| --- | --- | --- |
| ① 로직 | 각 서브커맨드에 stdin JSON을 넣어 기대 출력 확인 | 위반 `deny` · 대조군 통과 |
| ② 배선 | 배선 파일 파싱 + `hooks` 키·matcher 확인 | 인용 규칙 준수 |
| ③ **실발동** | **새 세션**에서 일부러 위반시켜 본다 | 아래 3셀 |

🔴 **①②만으로 "막힌다"고 쓰지 않는다.** 그 전까지 hook은 **"배선됨"이지 "작동 확인됨"이 아니다.**

### 3셀 대조

**위반 2 + 대조군 1**을 돌리고, **대조군이 먼저 성공하는지** 본다.

경로 가드라면 이런 모양이다.

| 셀 | 대상 | 기대 |
| --- | --- | --- |
| 대조군 | 그 워커의 `allow` 안 경로 | **성공** — 먼저 돌린다 |
| 위반 ① | `allow` 밖 경로 | `deny` |
| 위반 ② | `except`에 걸린 파일 | `deny` — **①과 다른 문구**여야 한다 |

- 🔴 **대조군이 성공하지 않으면 위반 셀의 `deny`는 근거가 못 된다** —
  "막혔다"와 "도구가 아예 안 돌았다"가 구분되지 않는다.
- **차단 문구가 어느 분기의 원문인지까지 대조한다.** 통과 후 다른 이유로 걸린 것과
  의도한 축이 발동한 것은 문구로만 갈린다([`parallel.md`](parallel.md) §차단 문구의 출처).
- **프로브가 판정층에 도달하는지 먼저 확인한다.** `Edit`에 **존재하지 않는 `old_string`** 을 주면
  도구가 문자열 매칭에서 **hook보다 먼저** 실패해 경계 안이든 밖이든 출력이 같아진다.
  원문을 지키려 넣은 fail-safe가 곧 **관측 불능의 원인**이 된다.
- **경계가 「넓은 허용 안의 좁은 금지」면 프로브 경로 선택이 관측 가능성을 좌우한다.**
  `except` 축이 그렇다 — 프로브를 `except`에 올리지 않으면 `allow`가 먼저 통과시켜
  **두 분기가 갈리지 않고 이 축은 원리상 관측되지 않는다.**
  한시적으로 프로브 경로를 `except`에 올려 돌린 뒤 **내리는 것까지가 절차**다.

### 실발동 이력

**확인했다는 사실 자체를 남긴다.** 남기지 않으면 다음 사람이 ①②만 보고 「배선됨」을
「작동 확인됨」으로 읽는다. 워커 지시문에는 적지 않는다 — 지시문에는 **적용법**만 두고
이력은 여기 모은다.

| 워커 · 축 | 프로브 | 결과 |
| --- | --- | --- |
| `data-extractor` · 저장소 안 쓰기 | `Write`로 `notebooks/`에 쓰기 | `deny`. 차단 문구가 `worker_path_guard.py` f-string의 **완전 치환**임을 소스 대조로 확정 |

⚠️ 마지막 칸이 이 표의 존재 이유다 — **문구 출처를 대조하지 않으면 "가드가 막았다"가
거짓이 될 수 있다.** 분류기가 막은 것과 가드가 막은 것은 **결과가 같고 원인이 다르다**
(문구 판별은 [`parallel.md`](parallel.md) §차단 문구의 출처).

### 반영 시점을 헷갈리지 않는다

| 대상 | 반영 |
| --- | --- |
| 프론트매터 `hooks`의 **배선**(matcher·`command`) | **정의 로드 시점 스냅샷** → 새 세션 |
| 가드 **스크립트 본문** | **매 호출 시 실행** → 즉시 |
| `permissions`(`settings.json`) | **세션 도중 즉시** |

**배선을 고친 세션의 음성 결과는 근거가 아니다.** 반대로 스크립트 로직만 고쳤으면
같은 세션에서 바로 대조할 수 있다.

### 판정자의 거부와 훅의 거부는 다르다

워커에게 금지 경로 쓰기를 시키면 **도구를 호출하지 않고 지시문 근거로 거부**할 수 있다.
그건 규율 층이 먼저 작동한 것이라 **훅 테스트로는 무효**다 —
**허용 경로 쓰기**로 바꿔 훅 발동만 관측한다.
🔴 **워커의 규율이 강할수록 강제층 검증이 가려진다.**

### 가드가 못 하는 것

- **`Bash` 경유 쓰기는 파일 가드 matcher 밖**이다. 보호 경로만 별도 가드가 잡는다.
- 가드가 판정할 수 없는 것(내용의 진실성·결정 근거·계층 기록)은 **supervisor의 책임**이다.
  hook은 규율을 대체하지 않고 **경합만** 없앤다.
