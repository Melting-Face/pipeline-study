# 문서 동기화 (doc-sync)

이 프로젝트는 **규칙·결정·작업 패턴을 문서로 남기고 단일 출처(single source of truth)를
유지**한다(프로젝트 [`CLAUDE.md`](../CLAUDE.md) 문서화 원칙). 규칙이 바뀌면 정본 문서와
그 규칙을 요약·참조하는 문서를 **함께** 갱신해 드리프트를 막는다.

## 단일 출처 원칙

- 한 규칙의 **정본은 한 곳**에만 둔다. 다른 문서는 요약하고 정본을 링크한다.
- 🔴 **`docs/`는 규칙을 담고, 진행 상태는 담지 않는다**(§실무 규칙 7 — 재현·시제·위협 3축).
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
| 관측·모니터링 | `docs/conventions/monitoring.md` | `docs/architectures/monitoring.md`(실태) · `CLAUDE.md` · `docs/README.md` |
| 보안·거버넌스 **정책** | `docs/security.md` | `CLAUDE.md` 운영 섹션 · `docs/references.md`(규제 출처) · `.claude/agents/security.md`(점검 항목) · `docs/README.md` 목차 |
| 보안 **현행 실태**(비공개) | `$OBSIDIAN_VAULT/security/posture.md` | `docs/security.md`(정책 — **한 벌로 갱신**: 한쪽만 고치면 정책이 실태를 앞질러 "다 됐다"로 읽힌다) |
| 환경 세팅 **절차** | `docs/setup.md` | 루트 `README.md`(빠른 시작만) · `docs/architectures/overview.md`(순서 요약) |
| 환경변수 추가 | `.env.example` | `compose.yml`(앵커) → 코드(`EnvVar`) → `docs/operations.md` 전파 체인 |
| **열린 작업**(공개 가능) | **GitHub Issues** | 해당 규칙 문서의 **「담지 않는 것」 선언**<br/>등록 전에 §실무 규칙 7 **축 3(위협)** 으로 공개 가능 판정을 먼저 한다. |
| **진행 상태·미해결**(비공개) | `$OBSIDIAN_VAULT/status/`(`backlog.md`·`observations.md`·`_index.md`) | 해당 규칙 문서의 **「이 문서가 담지 않는 것」 선언**(볼트 경로 명시) · `docs/README.md`<br/>**볼트에 먼저 쓰고 `docs/`에서 지운다** — 반대 순서는 손실이 비가역이다. |
| 데이터셋 스키마·피처 | `docs/dataset_schema.md` | 해당 `models/<dataset>/source.yml` · `schema.yml` |
| 분석 규칙(gold·노트북·리포트) | `docs/conventions/analysis.md` | `CLAUDE.md` 분석 섹션 · `docs/conventions/dbt.md`(gold 레이어) · `docs/test.md`(grain 테스트) · `notebooks/README.md` |
| 외부 공개(블로그·공유 자료) | `docs/conventions/publishing.md` | `CLAUDE.md` 운영 섹션 · `docs/README.md` 목차 · `docs/posts/README.md` · `.claude/agents/tech-writer.md`(포맷 프로파일·경계) · `docs/security.md`(반출 통제) |
| 위키 학습 노트 | `wiki/**` | `wiki/_Sidebar.md`·`wiki/Home.md` 목차(페이지 추가 시 **한 벌**) · `docs/conventions/publishing.md` §4-1 |
| Claude Code 스킬 | `docs/skills.md`(허브) + `docs/skills/**` | `skills-lock.json` · `.claude/agents/*.md` 프론트매터 · `CLAUDE.md` · `docs/conventions/agents/permissions.md` |
| 에이전트 오케스트레이션 | `docs/conventions/agents.md` + `agents/**` | `CLAUDE.md` · `docs/README.md` · `.claude/agents/**` · 관련 가드 스크립트 (아래 §가드 배선 체인) |

⚠️ **산문 §참조는 기계가 보지 않는다 — 이 축은 규율이다.** `doc_lint --links`는 마크다운
링크와 앵커까지 보고, `skill_wiring_check`는 표 두 벌을 대조한다. 그러나
*"`docs/skills.md` §③"* 처럼 **산문에 적은 절 이름**은 어느 검사기도 대조하지 않는다.
문서를 쪼개거나 절을 옮길 때는 **`grep -rn '§<이름>'`으로 전수 육안 확인**한다 —
링크가 초록이어도 그 §가 다른 파일로 갔으면 참조는 죽어 있다.

⚠️ **문서 디렉터리를 새로 만들면 `scripts/doc_lint.py`의 검사 대상에 함께 넣는다.**
`DEFAULT_TARGETS`(가독성)와 `LINK_SCAN_DIRS`(링크·앵커)는 **명시 열거**라, 빠뜨리면
새 디렉터리는 어떤 검사도 받지 않은 채 "위반 0건"에 포함되지 않는다 —
**막힌 것이 아니라 세지 않은 것**이다. 넣은 뒤에는 일부러 위반시켜 실제로 걸리는지 본다.
`wiki/`가 이 경로로 추가됐다.

### 가드 배선 체인

**가드를 늘리거나 줄이면 배선처도 함께 본다.** 배선처가 둘이라 한쪽만 보면 놓친다.

| 가드 | 배선처 |
| --- | --- |
| `journal_guard` · `protected_paths_guard` · `session_sync_guard` · `plan_mirror_guard` | `.claude/settings.json` |
| `analyst_path_guard` · `worker_path_guard` · `research_gate_guard` · `skill_gate_guard` | **각 워커 프론트매터** |

- 🔴 **`BOUNDARIES`에 워커를 추가하면 그 워커 정의의 `hooks`도 함께 잇는다.**
  정의만 있고 호출자가 없으면 **한 번도 실행되지 않는다.**
- **반대 방향도 한 벌이다** — 워커를 없애면 `BOUNDARIES` 항목도 함께 지운다.
  남겨도 아무 신호가 나지 않는다(부를 워커가 없어 조용히 죽은 설정이 된다).
- **`.gitignore`도 체인에 있다** — 런타임 상태 디렉터리는 저장소에 커밋하지 않는다.
  ⚠️ **끝 슬래시를 붙이지 않는다** — worktree에서 그 경로가 심볼릭 링크(=파일)라
  슬래시를 붙이면 무시되지 않는다.
- **`.pre-commit-config.yaml`은 무시 규칙의 2층**이다 — `git add -f`와 **이미 추적 중인 파일**을 잡는다.
  로컬 훅이라 `--no-verify`로 우회되므로 **실수 방지이지 봉쇄가 아니다.**

배선을 바꿨으면 **새 세션에서 3셀 대조**로 실발동을 확인한다
([`conventions/agents/enforcement.md`](conventions/agents/enforcement.md)).


## 실무 규칙

1. **정본을 먼저 고치고**, 그 규칙을 요약·참조하는 문서를 뒤이어 맞춘다.
2. 코드·설정과 문서가 어긋나면 **코드/설정이 사실**이다. 문서를 코드에 맞춘다(반대 아님).
3. 새 규칙·결정은 근거(왜)와 함께 남긴다. 외부 표준은 [`references.md`](references.md)에 등록하고 링크한다.
4. 문서는 한국어로 쓰고, 코드 식별자·명령어·경로는 원문 그대로 표기한다.
5. 🔴 **체인에 항목을 추가하는 것과 그 항목이 집행되는지는 다른 축이다.**
   가드·권한 규칙을 체인에 넣을 때는 **어떻게 위반시켜 확인하는지**를 같은 자리에 함께 적는다.
   안 적으면 "설정은 넣었는데 실효가 없는" 상태가 조용히 남는다 —
   이 저장소에는 이미 그런 계열이 셋 있었다(존재하지 않는 hook 결정값 · 매칭기가 무시하는
   경로 규칙 · 헤드리스 세션에서 판정 불가인 설정).
6. **검증 절차 자체는 [`conventions/agents/enforcement.md`](conventions/agents/enforcement.md)가 갖는다** —
   3셀 대조·대조군 우선·차단 문구 출처 구분. 여기서는 **체인에 적으라**는 것만 정한다.
7. 🔴 **`docs/`에 무엇을 남길지는 3축으로 판정한다.** 순서가 있다 — 앞의 둘이
   *남을지*를, 셋째가 *나가면 어디로 갈지*를 정한다.

   | # | 축 | 판정 질문 | 예 → | 아니오 → |
   | --- | --- | --- | --- | --- |
   | 1 | **재현** | 클론한 **남의 환경에서도 참인가?** | 축 2로 | 저장소 밖 |
   | 2 | **시제** | 6개월 뒤 아무도 손대지 않아도 **저절로 거짓이 되는가?** | 저장소 밖 | **`docs/`에 남는다** |
   | 3 | **위협** | 공개하면 **공격자에게 이득인가?** | 볼트 `status/`·`security/` | **GitHub Issue** |

   축 1·2는 **AND**다 — 둘 다 통과해야 남는다. **규칙**(무시제)과 **근거**(과거완료·불변)는
   남고, **상태**(현재진행)와 **내 환경 고유값**만 나간다. 근거를 상태로 오분류해 지우지
   않는다 — 사고 기록의 *교훈*은 규칙의 근거이고, 볼트로 가는 것은 *전말과 수치*뿐이다.

   **축 1 — 재현.** `README.md`와 `docs/`의 독자는 **이 저장소를 가져가 자기 환경에서
   실행하려는 사람 또는 에이전트**다. 판정선은 "고유명인가"가 아니라 **"클론한 사람이
   같은 값을 얻는가"** 다. 호스트 절대경로·개인 머신 실측 스펙·계정명은 나가고,
   `scripts/k8s-env.sh`가 정하는 기본값(클러스터 `lakehouse`·레지스트리 `localhost:5001`)과
   스키마·컬럼명은 **재현에 필요하므로 남는다**. 이 축은 `docs/`에만 걸린다 —
   `docs/posts/**`·`wiki/**`의 독자와 통제는 [`conventions/publishing.md`](conventions/publishing.md)가 따로 갖는다.

   **축 2 — 시제.** 이 축은 사후 대응이다. `conventions/general.md`에 오래 있던
   *"CI는 아직 없다"* 가 `.github/workflows/ci.yml` 신설로 **아무도 손대지 않았는데
   거짓이 됐고**, 같은 시기 pre-commit 훅 개수가 문서마다 갈렸다.
   ⇒ **낡은 문장이 규칙 옆에 있으면 규칙까지 신뢰를 잃는다.**

   축 2에는 **기계로 집행되는 하위 규칙**이 하나 있다.

   > **관측·결정 일자와 실측 수치는 `docs/`에 두지 않는다.**

   *"…일자 실측 CPU 84%"* 같은 문장은 남의 환경에서 참이 아니고(축 1) 저절로 낡는다(축 2).
   **수치와 전말은 볼트**(`observations.md`), **열린 항목은 Issue**로 보내고 `docs/`에는
   **인과와 처방만** 남긴다 — *"언제 재어 몇이었다"* 가 아니라 *"무엇이 상한을 정하고
   어느 표를 따르는가"* 로 쓴다. 판정 기준·임계값처럼 **규칙이 정하는 수**는 수치가 아니라
   규칙이므로 남는다(`5 미만 셀 마스킹`·`포트 8889`).
   **예외는 외부 출처의 발행일·버전 날짜**다([`references.md`](references.md)·인용 표기) —
   낡지 않는 사실이고 [`conventions/publishing.md`](conventions/publishing.md) §3이 요구한다.

   **축 3 — 위협.** 나간 문장의 **행선지**를 가른다. 이 저장소는 공개라 Issue도 공개다.
   인프라 공격 표면·시크릿 스캔 결함·관측 공백 지도·알려진 CVE 표면은 **볼트**,
   규칙·처방·기능 결함·문서 갭·이행 대기는 **Issue**다. 로컬 세션 장악을 전제로만
   유효한 가드 우회는 등급이 낮아 Issue로 본다. **갈리면 볼트가 기본값**이다 —
   공개는 되돌릴 수 없다. 선례는 [`security.md`](security.md) §3(정책은 공개·점검 결과는
   비공개 posture)이며, 이 축은 **그 분리를 저장소 전체로 넓힌 것**이지 새 규칙이 아니다.

   **보증 범위 — 기계가 보는 것은 축 2의 하위 규칙 하나뿐이다.** `doc_lint.py`가 잡는 것은
   **날짜 표기**이고, 그것도 *"이 자리에 관측 일자가 있다"* 까지다. 실측 수치인지 규칙 상수인지,
   문장이 재현 가능한지(축 1), 공개해도 되는지(축 3)는 **보지 않는다**. 세 축의 나머지는
   사람·에이전트가 문장을 쓸 때 **스스로 묻는 테스트**이며 위반을 자동으로 잡는 게이트가 없다.
   "축을 문서화했다"를 "집행된다"로 읽지 않는다([`philosophy.md`](philosophy.md) 원칙 7).

   절차는 §변경 유형별 동기화 체인의 「열린 작업」·「진행 상태·미해결」 행을 따른다.
   🔴 **볼트·Issue에 먼저 쓰고 `docs/`에서 지운다.**

## 참고

- 문서화 원칙: 프로젝트 [`CLAUDE.md`](../CLAUDE.md) · 전역 규칙
- 외부 표준 인덱스: [`references.md`](references.md)
