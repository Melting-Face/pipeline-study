# 문서 동기화 (doc-sync)

이 프로젝트는 **규칙·결정·작업 패턴을 문서로 남기고 단일 출처(single source of truth)를
유지**한다(프로젝트 [`CLAUDE.md`](../CLAUDE.md) 문서화 원칙). 규칙이 바뀌면 정본 문서와
그 규칙을 요약·참조하는 문서를 **함께** 갱신해 드리프트를 막는다.

## 단일 출처 원칙

- 한 규칙의 **정본은 한 곳**에만 둔다. 다른 문서는 요약하고 정본을 링크한다.
- [`CLAUDE.md`](../CLAUDE.md)는 **핵심 컨벤션의 요약/인덱스**, 상세 배경·흐름은 `docs/`에 둔다.
- 도구로 강제 가능한 규칙(lint·format)의 정본은 **도구 설정 파일**(repo 루트 `pyproject.toml`의
  `[tool.ruff.*]`·`[tool.sqlfluff.*]` 등)이다. 문서는 그 설정의 의도를 설명할 뿐 값을 중복 정의하지 않는다.

## 변경 유형별 동기화 체인

| 변경 | 정본(먼저 수정) | 함께 갱신 |
| --- | --- | --- |
| 코딩 규칙 | `docs/conventions/<topic>.md` | `CLAUDE.md` 요약 · `docs/README.md` 목차 |
| 코딩 철학(핵심 가치) | `docs/philosophy.md` | `CLAUDE.md` §코딩 철학 **번호까지 일치**시킨다(요약 목록이 원칙 표와 1:1) · `docs/references.md`(출처) |
| 아키텍처·데이터 흐름 | `docs/architectures/overview.md` | `CLAUDE.md` · 관련 `conventions/*` 링크 |
| 처리·배포 기술(개별) | `docs/architectures/<tech>.md`(trino·docker·spark·flink·k8s) | `docs/architectures/README.md` 목차 · `docs/references.md`(기술 출처) |
| 프로젝트 구조 | `docs/conventions/dagster.md` | `CLAUDE.md` 구조 섹션 |
| 운영·리소스 | `docs/operations.md` · `docs/resource-sizing.md` | `CLAUDE.md` · `compose.yml` 주석 |
| 관측·모니터링 | `docs/conventions/monitoring.md` | `docs/architectures/monitoring.md`(현행 실태·기술 결정) · `CLAUDE.md` 운영 섹션 · `docs/README.md` 목차 · `docs/security.md`(2.11 사고 예방·대응, 2.10 감사 로그)<br/>🔴 **규칙 정본과 실태 문서를 가른다** — 관측 *수단*의 작성법은 이미 소유자가 있다(compose healthcheck는 `docs/conventions/docker.md`, K8s probe는 `docs/conventions/k8s.md`, 로그 보존은 `docs/operations.md`, 자원 실측 수치는 `docs/resource-sizing.md`). 새 문서에 **다시 쓰지 말고 링크**한다. |
| 보안·거버넌스 **정책** | `docs/security.md` | `CLAUDE.md` 운영 섹션 · `docs/references.md`(규제 출처) · `.claude/agents/security.md`(점검 항목) · `docs/README.md` 목차 |
| 보안 **현행 실태**(비공개) | `$OBSIDIAN_VAULT/security/posture.md` | `docs/security.md`(정책 — 🔴 **한 벌로 갱신**: 한쪽만 고치면 정책이 실태를 앞질러 "다 됐다"로 읽힌다) |
| 환경변수 추가 | `.env.example` | `compose.yml`(앵커) → 코드(`EnvVar`) → `docs/operations.md` 전파 체인 |
| 데이터셋 스키마·피처 | `docs/dataset_schema.md` | 해당 `models/<dataset>/source.yml` · `schema.yml` |
| 분석 규칙(gold·노트북·리포트) | `docs/conventions/analysis.md` | `CLAUDE.md` 분석 섹션 · `docs/conventions/dbt.md`(gold 레이어) · `docs/test.md`(grain 테스트) · `notebooks/README.md` |
| 외부 공개(블로그·공유 자료) | `docs/conventions/publishing.md` | `CLAUDE.md` 운영 섹션 · `docs/README.md` 목차 · `docs/posts/README.md` · `.claude/agents/tech-writer.md`(포맷 프로파일·경계) · `docs/security.md`(반출 통제) |
| Claude Code 스킬 | `docs/skills.md` | `skills-lock.json`(등재·`computedHash`) · `.claude/agents/*.md` 프론트매터 `skills:`(프리로드는 **lock 등재분만**) · `.claude/agents/skill-matcher.md`(채점 루브릭) · `CLAUDE.md` 운영 섹션 |
| 에이전트 오케스트레이션·기록관 | `docs/conventions/agents.md` | `CLAUDE.md` 운영 섹션 · `docs/README.md` 목차 · `.claude/agents/*.md` · `.claude/commands/journal.md` · **`scripts/journal_guard.py`·`scripts/protected_paths_guard.py`·`scripts/session_sync_guard.py`·`scripts/analyst_path_guard.py`(← 배선처가 `settings.json`이 아니라 `.claude/agents/analyst.md` 프론트매터)·`scripts/worker_path_guard.py`(← 배선처가 `settings.json`이 아니라 `.claude/agents/{tech-writer,researcher,data-engineer,devops-engineer,archivist,data-extractor}.md` 프론트매터 — ⚠️ **2026-08-23 `director` 계층 폐기로 이 목록에서 `director`가 빠졌다**. 🔴 **`BOUNDARIES`에 워커를 추가하면 그 워커 정의의 `hooks`도 함께 잇는다.** 2026-08-20까지 7종 중 3종이 미배선이라 **정의만 있고 실행된 적이 없었다**. 🔴 **반대 방향도 한 벌이다 — 워커를 없애면 `BOUNDARIES` 항목도 함께 지운다**(남겨도 아무 신호가 나지 않는다: 부를 워커가 없어 조용히 죽은 설정이 된다). 🔴 **`OUTSIDE_ALLOW`·`OUTSIDE_STRICT`(저장소 밖 경로)도 같은 규칙**이다 — `data-extractor`는 셋 다에 걸린다. 🔴 **`except`(2026-08-22 신설·`allow`/`deny`보다 먼저 평가)는 이미 배선된 워커의 `BOUNDARIES` 안에서 바뀌므로 새 배선이 필요 없고, 그래서 「배선 변경 → 새 세션」 법칙의 대상이 아니다** — **스크립트 본문은 매 호출 즉시 반영**된다. 대신 **경계가 늘면 그 경계를 겨냥한 대조 셀을 추가**한다)·**`scripts/research_gate_guard.py`(2026-08-23 신설 — 배선처가 `settings.json`이 아니라 `.claude/agents/researcher.md` 프론트매터 `hooks.PreToolUse`의 matcher `"WebSearch|WebFetch"`. `WebSearch`는 **통과 + 질의문 로깅**, `WebFetch`는 승인 매니페스트 `.claude/.research/approved.json` 대조 후 없으면 **`deny`** → 조사는 「후보 URL 반환 → 일괄 승인 → 승인분만 페치」 2왕복이 된다. 🔴 **덮지 못하는 축을 같은 자리에 적는다** — ⓐ `WebSearch` **질의문 자체**(로깅만 하고 막지 않는다) ⓑ **supervisor 본세션**(워커 프론트매터라 상위 계층엔 안 걸린다) ⓒ `Bash`의 `curl`/`wget` **GET**(matcher 밖). 이 셋은 규율로 남는다)**·`.claude/settings.json`(기계 강제 가능한 규약은 hook·권한 규칙에 반영)·`.claude/settings.json`의 가드 스크립트 보호 규칙(`Edit(scripts/*_guard.py)`·`Edit(scripts/**/*_guard.py)`)·`docs/conventions/git.md`(커밋 대상·금지)·`.gitignore`(런타임 상태 무시 — 저널 원문은 볼트 `$OBSIDIAN_VAULT/agents/`, `.claude/.claims`·**`.claude/.research`** 는 repo 미커밋. 🔴 **끝 슬래시를 붙이지 않는다** — worktree에서 이 경로가 심볼릭 링크(=파일)라 슬래시를 붙이면 무시되지 않는다)·**`.pre-commit-config.yaml`**(무시 규칙의 **2층** — `git add -f`와 **이미 추적 중인 파일**을 잡는다: `no-health-data-files`와 같은 형태의 `no-research-state-files` 훅. ⚠️ 로컬 훅이라 `--no-verify`로 우회되므로 **실수 방지이지 봉쇄가 아니다**)**<br/>🔴 **가드 배선을 바꿨으면 3셀 대조**(위반 2 + 대조군 1)로 실발동을 확인한다 — 프론트매터 `hooks`는 **정의 로드 시점 스냅샷**이라 편집한 세션의 음성 결과는 근거가 아니고 **새 세션에서** 돌린다(§실무 규칙 5). |

## 실무 규칙

1. **정본을 먼저 고치고**, 그 규칙을 요약·참조하는 문서를 뒤이어 맞춘다.
2. 코드·설정과 문서가 어긋나면 **코드/설정이 사실**이다. 문서를 코드에 맞춘다(반대 아님).
3. 새 규칙·결정은 근거(왜)와 함께 남긴다. 외부 표준은 [`references.md`](references.md)에 등록하고 링크한다.
4. 문서는 한국어로 쓰고, 코드 식별자·명령어·경로는 원문 그대로 표기한다.
5. 🔴 **체인에 항목을 추가하는 것과 그 항목이 집행되는지는 다른 축이다.** 가드·권한 규칙을 체인에
   넣을 때는 **어떻게 위반시켜 확인하는지**(위반 2 + 대조군 1의 3셀 대조)를 같은 행·문단에 함께
   적는다. 안 적으면 "설정은 넣었는데 실효가 없는" 상태가 조용히 남는다 — 2026-08-20까지 실측된
   계열이 이미 셋이다(존재하지 않는 hook 결정값 `escalate`·매칭기가 무시하는 `Write(<경로>)` 규칙·
   헤드리스 세션에서 판정 불가인 `cleanupPeriodDays`).
6. 🔴 **3셀을 적고 돌렸는데도 관측이 안 되는 경우가 있다 — 프로브가 「판정층에 도달하는지」를
   먼저 확인한다.** 규칙 5는 *"어떻게 위반시킬지 적어라"* 인데, 2026-08-22 그것을 적고 실행했는데도
   **판정 불가**가 났다. 원인은 가드가 아니라 **프로브 설계**였다 — `Edit` 도구에 **존재하지 않는
   `old_string`** 을 주면 도구가 문자열 매칭에서 **hook보다 먼저** 실패해, 경계 **안이든 밖이든
   출력이 같아진다**(대조군으로 로직층 `deny`가 확인된 `pyproject.toml`에 같은 프로브를 걸어 원인을 닫았다).
   원문을 지키려고 넣은 fail-safe가 곧 **관측 불능의 원인**이었다.
   ⇒ 3셀에는 **통과가 기대되는 대조군을 반드시 넣고, 그것이 *먼저* 성공하는지 본다.** 대조군이
   성공하지 않으면 위반 셀의 `deny`는 근거가 못 된다("막혔다"와 "도구가 아예 안 돌았다"가 구분되지 않는다).
   그리고 **차단 문구가 어느 분기의 원문인지**까지 대조한다 — 통과 후 다른 이유로 걸린 것과
   의도한 축이 발동한 것은 문구로만 갈린다.
   🔴 **경계가 「넓은 허용 안의 좁은 금지」면 프로브 경로 선택이 관측 가능성을 좌우한다.**
   `worker_path_guard.py`의 `except` 축이 그렇다 — 프로브를 `except`에 올리지 않으면 `allow`가
   먼저 통과시켜 **두 분기가 갈리지 않고, 이 축은 원리상 관측되지 않는다.**
   (한시적으로 프로브 경로를 `except`에 올려 돌린 뒤 내렸다 — **내리는 것까지가 절차**다.)

## 참고

- 문서화 원칙: 프로젝트 [`CLAUDE.md`](../CLAUDE.md) · 전역 규칙
- 외부 표준 인덱스: [`references.md`](references.md)
