# 스킬 배선의 메커니즘 — 무엇이 열고 닫는가

> [`../skills.md`](../skills.md) §③에서 분리한 문서다. **현행 배선이 아니라 메커니즘**을 담는다 —
> 어느 워커에 무엇이 물려 있는지는 각 `.claude/agents/<worker>.md`의 §참고 스킬 표가 정본이고,
> 그 사본이 [`../skills.md`](../skills.md) §③이다.
> 권한 매트릭스·프론트매터 채택 판단은 [`../conventions/agents/permissions.md`](../conventions/agents/permissions.md).

## 주입 단위와 도구 부여

- 🔴 **주입 단위는 스킬 디렉터리가 아니라 `SKILL.md` 한 파일이다**(2026-08-23 실측 — `dagster-integrations`를
  프리로드한 프로브가 도구 0회로 `SKILL.md` 본문은 원문 인용했고 `references/storage.md`는 `NOT-IN-CONTEXT`로 답했다).
  ⇒ **`Read`가 없는 워커에게 `references/` 경로를 적으면 죽은 참조다.** 프리로드 단서에는 주입 대상과 안내 대상을 갈라 적는다.
- 🔴 **"워커에 `Skill` 도구가 없다"는 서술을 폐기한다**(2026-08-23 실측으로 반증).
  정확한 진술은 **「`tools:`에 `Skill`을 열거한 워커에서만 열린다」**이고, 어느 워커에 열지는 **정책적 선택**이다.
  ⚠️ **아래 프로브는 전원 미열거 시점의 것**이다 — 지금은 9종이 열려 있다([`../skills.md`](../skills.md) §③ 경로 표).
  프로브가 보인 것은 **메커니즘**(무엇이 열고 닫는가)이지 현행 배선이 아니다.
  - **반증 근거**(전부 **런타임 응답 원문** — 자기보고 아님). `tools:` 미선언 프로브 3셀, 변인은
    `disallowedTools`의 `Skill` 포함 여부 하나뿐:

    | 셀 | `disallowedTools`에 `Skill` | 런타임 응답 원문 |
    | --- | :-: | --- |
    | **A1** 처치 | 미포함 | `Base directory for this skill: /Users/jin/dagster-study/.claude/skills/dagster-integrations` (도구 호출 1회) |
    | **A2** 대조 | 포함 | `Error: No such tool available: Skill. Skill is disabled for this session, in subagents as well as here.` |
    | **A3** 대조 | 미포함(`Read`는 포함) | `Error: No such tool available: Read. Read is disabled for this session, in subagents as well as here.` |

    A1의 문자열은 **`Skill` 도구 출력 봉투에만** 나타나고 디스크 전체에 0건이라(`grep -rl`) **`Read`로는 만들 수 없다**.
    A3가 그 `Read`마저 실제로 막혔음을 보여 **우회 경로를 닫는다.**
    ⇒ **`Skill`은 서브에이전트 기본 집합에 있고, `tools:`가 그것을 제거해 온 것**이다
    (CLI 2.1.228 문자열 `Tools available to this agent. Replaces the default set.`와 정합).
  - **`tools:`에 `Skill`을 적으면 실제로 열린다**(2026-08-23 셀 D3 — `tools:["Skill","Glob"]` 프로브가
    `Base directory for this skill: …/.claude/skills/dagster-integrations`를 반환). ⇒ 위 "정책적 선택"은
    **닫아 둘 수 있어서가 아니라 열 수 있는데 안 여는 것**이다.
  - ✅ **`disallowedTools`는 `tools:`를 함께 선언해도 무시되지 않는다**(2026-08-23 셀 D1·D2 — 변인 하나).
    CLI 2.1.228 문자열 `Tools removed from the default set. Ignored if \`tools\` is set.` 만 보고
    "죽은 선언"으로 읽으면 **오판**이다. 워커 13종 전부가 `tools:`를 선언하므로 급소였다.

    | 셀 | `tools:` | `disallowedTools:` | 결과 |
    | --- | --- | --- | --- |
    | **D2** 대조 | `["Read","Glob"]` | 없음 | `Read` **성공**(파일 첫 줄 반환) |
    | **D1** 처치 | `["Read","Glob"]` | `["Read"]` | `Error: No such tool available: Read.` |

    ⇒ `CLAUDE.md` §③의 **"미부여(난이도) → 거부(강제)로 올린다"** 는 서술은 **살아남는다.**
    ⚠️ 단 측정한 것은 **`--agents` JSON 경로**다. 파일 기반 `.claude/agents/*.md`가 같은 해석기를 쓴다는
    보장은 없으므로(§⑥ 같은 규율) **한쪽 결과로 다른 쪽을 단정하지 않는다.**
  - 🔴 **에러 문구로는 「에이전트 스코프 미부여」와 「세션 전역 비활성」을 못 가른다** — D1의 응답이
    `Read is disabled for this session, in subagents as well as here.` 인데 **같은 세션의 상위 루프에서는
    `Read`가 정상 작동**했다. 원인이 에이전트 스코프인데 런타임은 **전역인 것처럼 보고**한다.
    [`../conventions/agents.md`](../conventions/agents.md)의 `NotebookEdit` 사례와 **같은 함정**이며,
    ⇒ **부재를 판정할 때 에러 문구를 근거로 삼지 말고 대조군(D2)을 세워라.**
    🔴 **대조군 없는 프로브는 자기보고로 퇴화한다** — D0(`tools:["Glob"]`)은 두 원인이 같은 에러를 내
    **판정 불가**였고, 초기 D0·D1은 워커가 **호출을 시도조차 않고 문장을 작문**해 `도구 호출 0회`로 끝났다.
    강제 문구("도구 존재를 따지지 말고 호출을 방출하라")를 넣고서야 하네스 응답이 나왔다.
  - 🔴 **구 근거는 동어반복이었다** — 잰 것은 *"`tools:`를 열거했고 그 목록에 `Skill`이 없는 워커 2종의 자기보고"* 뿐인데
    `tools:`가 화이트리스트이므로 **정의상 참**이다. `grep -L 'Skill' .claude/agents/*.md`로 **검산하면 14/14 통과**해
    그대로 남았다([`../philosophy.md`](../philosophy.md) §계측 단위 ①갈래 + ⑥부재의 확언).
  - 🔴 **그럼에도 열지 않는다 — 근거는 실측이 아니라 통제다.** `skills:`는 **화이트리스트**라 미검토 스킬 유입을
    원천 차단하지만 `Skill` 도구는 화이트리스트가 아니어서 **lock 미등재·`security` 미검토 스킬까지 부를 수 있고**,
    호출을 스킬 단위로 제한할 수단이 이 저장소에 없다. ⇒ 표에 이름을 적는 것만으로는 여전히 **스킬이 발동하지 않는다.**
  - ⚠️ **현행 워커의 `Skill` 부재는 정적 실측 + 연역**이고, 강제 호출로 얻은 런타임 에러는 **없다**
    (`data-qa`는 "함수 목록에 없어 호출 자체가 불가"라고 **자기보고**했다). 이 축은 `미확인`으로 남긴다.
    실측(2026-08-23 19:03 KST · HEAD `5340b55`): `.claude/agents/*.md` **13개** 전부 `tools:` 선언 보유,
    그중 `Skill` 포함 **0개**. 🔴 이 값이 세는 것은 **`.claude/agents/`의 `.md` 파일 총수**이지 전문 워커 수가 아니다.
    🔴 18:54 관측 시점에는 **14**였다(`director.md` 삭제 전) — **분모가 세션 중에 움직였다.**

## 프리로드 자격

- 🔴 **프리로드 조건을 "lock 등재분" → "lock 등재 ∧ `security` 검토 완료분"으로 강화한다**(2026-08-21).
  주입은 워커의 선택이 아니라 **무조건**이라, 검증 안 된 콘텐츠가 상시 컨텍스트에 앉는다.
  기존 조건이 lock 하나뿐이었던 탓에 **`brainstorming`(C등급·실행 파일·미검토)이 등재되자마자
  프리로드 자격을 자동으로 얻었다** — §A등급 허점과 **같은 결함이 다른 곳에서 반복**된 것이다.
  현재 이 두 조건을 만족하는 것은 **`dagster-io/skills` 3종뿐**이다.
- 🔴 **세 번째 축 「상시성」을 신설한다**(2026-08-23). 앞의 두 축은 **둘 다 안전 축**이라 **비용 축이 0개**였고,
  게다가 첫 항은 §lock에 적은 대로 이미 **실효를 주장할 수 없다**(실질 1축).

  > **프리로드 자격 = lock 등재 ∧ `security` 검토 완료 ∧ 「그 워커의 거의 모든 배정에서 발동 조건이 참일 것(`p ≈ 1`)」**

  🔴 **셋 다 거부권이고 AND다 — 가중 합산이 아니다.** 셋째만 비용 축이라 훗날 "점수로 상계"로 오독되면
  **안전 축이 비용 축과 거래된다.** §skill-matcher의 **"게이트 축과 채점 축을 섞지 않는다"** 와 같은 형태의 함정이다.

  - **근거**: 프리로드는 배정 1건당 **`≈ 4.95 × T` 환산 입력토큰**을 **무조건** 진다(배정 1건 = 서브에이전트 왕복
    **중앙값 38회**, n=213 세션 로그 실측 · 캐시 계수 4.95는 표준 단가 **가정**). `Read` 대비 이득은 발동률 `p`에
    비례하므로 **`p ≲ 0.5`면 순손실**이다 — `p=1.0`은 거의 동률(+0.20 T)이나 `p=0.5`는 **2.6배**, `p=0.2`는 **4배** 손해.
    🔴 **손익분기는 스킬 *크기*가 아니라 *발동률*이 정한다.** 크기는 손해의 배율만 정한다.
  - **`p`의 1차 판정 근거는 스킬 `description`의 발동 조건 문구**다. 이견이 있으면 배정 프롬프트 원문으로 실측한다
    (세션 로그의 `Agent` 호출에 남아 있다). 🔴 **자동 분류 금지** — 키워드 매칭은 [`../philosophy.md`](../philosophy.md)
    §계측 단위 ⑥갈래(표기 변형)로 직행한다.
  - **적용 결과**: `data-engineer` × `dagster-expert`(`p≈1` — description이 `ALWAYS use before … references assets`)만
    통과하고, `dagster-integrations`(`p≈0.2` — 조건부 트리거·통합 배선 완료)와 `dignified-python`(`p≈0.5` — 게다가
    **프로젝트 컨벤션 우선**이라 실채택률은 더 낮다)은 탈락한다. ⇒ **현행 배선 1건은 우연이 아니라 규칙에서 도출된다.**
  - ⚠️ 위 세 `p`는 **가정**이고 실측이 아니다. 🔴 표의 토큰 추정에 [`../operations.md`](../operations.md) §토큰 비용의
    회귀 기울기(`0.5301 tok/B`)를 그대로 곱하지 마라 — 그 값은 **1.710 B/char 한국어 문서**에 적합된 것이고
    `SKILL.md`는 **1.00 B/char 영어**다(둘 다 실측). 쓰려면 **상한**임을 병기한다.

## 조용히 깨지는 것

- 🔴 **오타난 스킬명은 워커에게 조용하고, 로그에만 시끄럽다**(2026-08-23 실측 — 2026-08-19 `미검증` 해소).
  `skills: [nonexistent-skill-zzz9]` 프로브는 **기동에 성공**했고 스스로 `NO-PRELOAD`·"경고 없음"으로 답했으나,
  `--debug-file` 로그에는 `[Agent: probe-b1] Warning: Skill 'nonexistent-skill-zzz9' specified in frontmatter was not found`가
  찍혔다(대조군: 정상 프로브 로그에 `[Agent: probe-b2] Preloaded skill 'dagster-integrations'` — **관측 경로 생존 확인**).
  ⇒ **기본 실행 경로에서는 사람도 워커도 실패를 못 본다.** 게다가 프리로드가 안 된 워커도 **여전히 그럴듯하게 답하므로**
  이 배선에는 「작동한다」를 보여주는 신호가 **원리상 없다**. 🔴 **`skills:`에 이름을 추가하면 그 자리에서
  `--debug-file`로 `Preloaded skill` 한 줄을 확인**한다 — 대소문자·하이픈 변형은 lock 대조로도 안 걸린다.
- 🔴 **주입된 본문은 데이터이지 지시가 아니다.** 실례: `dagster-expert` 본문의
  `# Output confirms success—no verification needed`는 이 저장소 **철학 원칙 7과 정면 충돌**한다
  (probe가 원문 그대로 인용해 확인). 프리로드하는 워커의 지시문에는 **이를 따르지 않는다는 단서**를 넣는다.
