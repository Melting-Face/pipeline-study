# Codex 에이전트 구성

이 문서는 Claude Code 구성을 보존하면서 Codex가 같은 역할 체계를 **별도 설정으로**
사용하는 방법과 두 런타임의 차이를 설명한다.

## 결론

- Claude Code 정본: `CLAUDE.md`, `.claude/agents`, `.claude/settings.json`,
  `.claude/commands`, `.claude/skills`
- Codex 정본: `AGENTS.md`, `.codex/agents`, `.codex/config.toml`,
  `.codex/hooks.json`, `.codex/rules`, `.agents/skills`
- 공통 도메인 규칙: `docs/`
- 스킬 출처·무결성: `skills-lock.json`, `docs/skills.md`

`AGENTS.md`는 `CLAUDE.md`의 공통 프로젝트 섹션을 기준 자료로 작성한다. 기존 Claude
워커·스킬·hook 동작은 덮어쓰거나 삭제하지 않는다. 단, 양쪽 구성의 분리 사실을 알리고
Codex 설정 경로를 보호하기 위한 링크·보호 규칙만 Claude 설정에 추가한다. 공통 도메인
규칙은 `docs/`에 한 번만 두고, 런타임별 도구·권한·hook 차이만 각 설정에 둔다.
`CLAUDE.md` 전문을 복사하지 않는 이유는 52KB로 Codex 기본 프로젝트 지침 한도 32KiB를
넘고, Claude 전용 도구·권한 의미까지 Codex 문맥에 섞이기 때문이다.

## 구성 지도

| 목적 | Claude Code | Codex |
| --- | --- | --- |
| 프로젝트 지침 | `CLAUDE.md` | `AGENTS.md` |
| 전문 워커 | `.claude/agents/*.md` | `.codex/agents/*.toml` |
| 프로젝트 설정 | `.claude/settings.json` | `.codex/config.toml` |
| hook | `settings.json`의 `hooks` | `.codex/hooks.json` + `.codex/hooks/*.py` |
| 명령 정책 | `permissions.allow/ask/deny` | sandbox + `.codex/rules/*.rules` + hook |
| 스킬 | `.claude/skills` | `.agents/skills` |
| 저널 | `agents/<날짜>/` + `/journal` | `agents/<날짜>/` + Stop 보정 |

OpenAI는 Claude Code의 지침·설정·스킬·hook·슬래시 명령·subagent를 Codex로
가져오는 `/import`를 제공한다. 이 저장소는 이미 세밀한 경로 가드와 데이터 거버넌스
규칙이 있으므로 자동 import 결과에 의존하지 않고 별도 파일을 명시적으로 관리한다.
[OpenAI Docs — Import from another agent](https://learn.chatgpt.com/docs/import)

## 워커와 모델

13개 역할은 양쪽 런타임에 같은 이름으로 존재한다. Claude의 `sonnet`은 Codex 모델명이
아니므로 작업 난이도와 비용에 따라 다음처럼 매핑한다.

| Codex 설정 | 워커 | 이유 |
| --- | --- | --- |
| 부모 모델 상속 | `analyst`, `data-engineer`, `data-extractor`, `devops-engineer`, `security`, `tech-writer` | 구현·판정·모호한 작업의 정확성 우선 |
| `gpt-5.6-terra`, `high` | `data-qa`, `data-verifier`, `devops-qa`, `devops-verifier`, `researcher` | 읽기·대조 중심이며 정확성과 비용 균형 |
| `gpt-5.6-luna`, `medium` | `archivist`, `skill-matcher` | 반복적 기록·인벤토리 작업의 효율 우선 |

판정·감사 워커는 `sandbox_mode = "read-only"`다. 구현 워커는
`workspace-write`를 사용하고 프로젝트 `PreToolUse` hook이 `transcript_path`에서 활성 역할을
식별해 `apply_patch` 경계를 적용한다. 서브에이전트는 각자 토큰을 사용하므로 독립된 읽기
작업 또는 명확히 분리된 역할에만 사용한다.
[OpenAI Docs — Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)

## 권한 계층

Codex 권한은 다음 순서로 겹쳐 쓴다.

1. `sandbox_mode = "workspace-write"`와 `network_access = false`
2. 읽기 전용 워커의 `sandbox_mode = "read-only"`
3. `.codex/rules/default.rules`의 샌드박스 밖 명령 승인 정책
4. `.codex/hooks/policy_guard.py`의 확정 금지와 추가 문맥
5. `.codex/hooks/worker_path_guard.py`의 워커별 patch 경계
6. `AGENTS.md`와 워커 `developer_instructions`의 행위 규율

`.rules`는 명령 인자 prefix를 평가하며 `allow`, `prompt`, `forbidden` 중 가장 엄격한
결정을 적용한다. shell 문자열을 완전하게 해석하는 방화벽이 아니므로 sandbox와 hook을
대체하지 않는다. [OpenAI Docs — Rules](https://learn.chatgpt.com/docs/agent-configuration/rules)

## Hook 차이와 보강

Codex와 Claude는 `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop` 같은 이름을
공유하지만 실행 계약이 완전히 같지는 않다.

- Codex CLI 0.149.1의 파일 수정은 최상위 `exec` 안에서 `tools.apply_patch(...)`를 호출한다.
  경로 가드는 freeform `exec` 입력과 직접 `apply_patch` 입력을 모두 정규화한 뒤
  `*** Add/Update/Delete File:` 헤더를 파싱한다.
- 워커 경로 가드는 각 워커 TOML의 인라인 hook이 아니라 `.codex/hooks.json`에 한 번
  배선한다. 서브에이전트 transcript의 `thread_source = "subagent"`와 `agent_role`을
  확인하므로 메인 세션의 patch에는 역할 경계를 잘못 적용하지 않는다.
- Codex `PreToolUse`는 `deny`와 `additionalContext`를 사용한다. Claude 구성에서 쓰던
  대화형 `ask`를 그대로 반환하면 Codex가 hook 실패로 처리하고 도구를 계속 실행한다.
- 확정적으로 금지할 HTTP mutation과 `.env`·Terraform state 쓰기는 `deny`한다.
- 판단이 필요한 커밋·배포·설치 명령은 `.rules`의 `prompt`와 hook의
  `additionalContext`를 함께 사용한다.
- hosted `WebSearch`는 로컬 function-tool hook 경로를 지나지 않으므로 Claude의
  `research_gate_guard.py`를 그대로 연결할 수 없다. 검색 질의 비노출은 `researcher`
  지침과 메인 에이전트 재검토가 담당한다.
- Codex `SessionStart`는 세션별 HEAD·`git status --porcelain`·Git diff 해시를
  `.codex/.claims/journals/`에 저장한다. 원문 diff는 저장하지 않는다. `Stop`에서 기준점이
  달라졌는데 현재 `session_id`의
  `agents/<KST 날짜>/` 저널이 없거나 마감되지 않았으면 `decision: block`으로 한 번만
  이어서 기록하게 한다. 두 번째 Stop은 경고만 반환해 반복을 막는다.
- 저널 본문은 원천 데이터·비밀값을 복사하지 않고 관측된 결정·경로·검증만 요약한다.

프로젝트 hook은 처음 또는 변경 후 자동 신뢰되지 않는다. 새 Codex 세션에서 `/hooks`로
`.codex/hooks.json`의 현재 정의를 검토하고 신뢰해야 실행된다. 검토된 일회성 자동화 시험은
`--dangerously-bypass-hook-trust`를 사용할 수 있지만 일상 실행 기본값으로 저장하지 않는다.
[OpenAI Docs — Hooks](https://learn.chatgpt.com/docs/hooks)

## 스킬 관리

Codex는 저장소의 `.agents/skills/<name>/SKILL.md`를 자동 탐색한다. 현재 Claude 전용으로만
있던 스킬은 `.agents/skills`에 별도 사본을 두며, 기존 Codex 스킬은 유지한다.

- 같은 스킬을 양쪽에서 수정할 때는 두 사본의 diff와 `skills-lock.json` 해시를 확인한다.
- 외부 스킬 설치·업데이트는 공급망 변경이므로 `security` 검토와 사용자 승인을 거친다.
- 상세 본문을 `AGENTS.md`에 복제하지 않고 trigger metadata만 상시 노출한다.

Codex는 먼저 스킬 metadata를 보고 작업과 일치할 때 `SKILL.md` 전문을 읽는 progressive
disclosure 방식을 사용한다. [OpenAI Docs — Build skills](https://learn.chatgpt.com/docs/build-skills)

## 시작 절차

1. 저장소를 trusted project로 연다. 프로젝트 `.codex` 설정은 신뢰된 저장소에서만
   활성화된다.
2. 새 세션에서 `/hooks`를 열어 `.codex/hooks.json`의 프로젝트 hook 정의와 참조 스크립트를
   검토한 뒤 현재 정의를 신뢰한다.
3. Codex에 활성 지침 출처와 커스텀 에이전트 목록을 요약하게 한다.
4. `/agent`로 실행 중인 워커와 완료 결과를 확인한다.
5. 외부 저널 또는 추출 경로 쓰기가 필요하면 정확한 경로와 목적에 대해서만 별도
   승인을 준다.

## 변경 절차

- 공통 도메인 규칙 변경: `docs/` → `AGENTS.md`와 `CLAUDE.md` 요약 동기화
- Claude 런타임 변경: `.claude/**`만 수정하고 이 문서의 차이 표를 확인
- Codex 런타임 변경: `.codex/**`만 수정하고 이 문서의 차이 표를 확인
- 상대 런타임의 실행 규칙을 무심코 바꾸지 않도록 Claude의 보호 경로에는
  `AGENTS.md`·`.codex/**`, Codex의 보호 문맥에는 `CLAUDE.md`·`.claude/**`를 둔다.
- 워커 역할 변경: 같은 역할의 Claude MD와 Codex TOML을 대조하되 런타임 어댑터
  문구는 억지로 같게 만들지 않는다.
- 스킬 변경: `.claude/skills`와 `.agents/skills`의 사본·symlink 관계를 확인하고 lock을
  재검증한다.

## 검증 체크리스트

1. `AGENTS.md`가 Codex 기본 32KiB보다 작은지 확인한다.
2. `.codex/config.toml`과 `.codex/agents/*.toml`을 TOML parser로 전부 읽는다.
3. `.codex/hooks.json`을 JSON parser로 읽는다.
4. `.codex/hooks/*.py`를 `ruff check`와 `py_compile`로 검사한다.
5. `codex execpolicy check`로 각 `.rules`의 `prompt`·`forbidden` 대조군을 확인한다.
6. `policy_guard.py`에 허용·거부 합성 payload를 각각 넣어 결과가 갈리는지 확인한다.
7. `worker_path_guard.py`에 메인·서브에이전트 transcript와 허용·거부 patch를 조합해
   역할 추론과 경계 판정이 실제로 갈리는지 확인한다.
8. 새 Codex 세션에서 프로젝트 지침·13개 워커·16개 스킬이 발견되는지 확인한다.
9. hook을 신뢰한 새 세션 또는 검토된 일회성 자동화 세션에서 쓰기 워커가 경계 밖 파일을
   만들도록 시도하고, `PreToolUse` 거부와 파일 부재를 모두 확인한다.
