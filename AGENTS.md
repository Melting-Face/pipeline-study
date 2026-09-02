# 프로젝트 AGENTS.md (pipeline-study)

> 이 파일은 **Codex 전용 프로젝트 지침**이다. Claude Code는 `CLAUDE.md`와
> `.claude/**`를, Codex는 이 파일과 `.codex/**`를 사용한다. 두 런타임의 설정은
> 서로 덮어쓰지 않는다.

## 작성 기준

- `AGENTS.md`의 **공통 프로젝트 규칙은 `CLAUDE.md`를 기준 자료로 작성**한다.
- `CLAUDE.md`의 프로젝트 목적·문서화·코딩 철학·Python·Dagster·dbt·분석·테스트·
  타임존·운영 규칙을 Codex가 실행하기 좋은 길이로 요약하고 상세 정본을 링크한다.
- `CLAUDE.md`에 공통 규칙이 추가되거나 바뀌면 `AGENTS.md`의 해당 요약도 갱신한다.
- `tools`, `disallowedTools`, `permissions`, `hooks`, `model`, auto/plan mode처럼
  Claude Code 런타임에 종속된 설명은 복사하지 않고 Codex 형식으로 다시 설계한다.
- `AGENTS.md`에 세부 규칙이 없으면 `CLAUDE.md`의 관련 공통 섹션과 연결된 `docs/`
  정본을 읽되, Claude 런타임 전용 문장은 Codex 지침으로 해석하지 않는다.

## 정본과 동기화 범위

- 공통 프로젝트 규칙의 요약 기준은 `CLAUDE.md`, 상세 정본은 `docs/`다. 작업 영역에
  맞는 문서를 먼저 읽는다.
- Codex 실행 규칙과 워커 구성은 `AGENTS.md`·`.codex/**`가 정본이다.
- Claude Code 실행 규칙과 워커 구성은 `CLAUDE.md`·`.claude/**`가 정본이다.
- 공통 규칙을 바꾸면 `AGENTS.md`·`CLAUDE.md`·관련 `docs/`를 함께 갱신한다.
- 런타임 고유 설정만 바꾸면 해당 런타임 파일만 갱신하고, 차이는
  `docs/conventions/codex.md`에 기록한다.
- Claude 전용 프론트매터·도구명·권한 의미를 Codex에 그대로 적용하지 않는다.

## 프로젝트 목적

이 저장소는 **파이프라인(수단) + 분석(목적)** 두 축이다. 여러 데이터셋을 같은
레이크하우스 패턴으로 적재·변환하는 이유는 데이터셋별 질문에 재현 가능하게 답하기
위해서다. 분석 규칙의 정본은 `docs/conventions/analysis.md`다.

## 작업 방식

모든 변경 작업은 다음 PDCA 순서로 진행한다.

1. **Plan** — 목표·성공 조건·영향 범위·검증 방법을 먼저 정한다.
2. **Do** — 관련 없는 리팩터링 없이 최소 변경으로 구현한다.
3. **Check** — 실제 명령으로 검사하고, 성공 신호가 무엇을 검증했는지 구분한다.
4. **Act** — 문서 동기화·잔여 위험·후속 작업을 정리한다.

계획과 검토에는 다음 관점을 빠뜨리지 않는다.

- Data Pipeline
- Data Science
- Data Analysis
- Database
- Data Governance
- Data Security

대안은 **정확성 → 위험 → 효율 → 비용** 순으로 판단한다. 순위를 제시할 때는
5점 별점과 이유를 함께 쓴다.

## 문서화 원칙

- 문서는 한국어로 작성하고 코드 식별자·명령어·경로는 원문 그대로 표기한다.
- `AGENTS.md`는 Codex 규칙의 요약과 라우팅만 담고, 상세 배경은 `docs/`에 둔다.
- 새 규칙이나 구조 결정을 만들면 관련 문서와 README를 함께 갱신한다.
- 링크로 연결할 수 있는 내용을 여러 파일에 전문 복제하지 않는다.

## 코드와 커밋

- 코드 주석은 한국어, 변수명과 함수명은 영어를 사용한다.
- 기본 들여쓰기는 스페이스 4칸이다. Terraform은 `terraform fmt` 결과를 따른다.
- Python은 `ruff`, SQL은 `sqlfluff`로 검사한다.
- 커밋과 푸시는 사용자가 명시적으로 요청한 경우에만 수행한다.
- 커밋 메시지는 `type(scope): 한국어 설명` 형식을 따른다.
- type은 `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`,
  `ci`, `chore`, `revert` 중에서 고른다.
- 사용자 변경과 관련 없는 dirty worktree 파일을 수정하거나 되돌리지 않는다.

## 구현 핵심 규칙

- Dagster 에셋은 함수와 데코레이터로 명시적으로 정의한다. 불필요한 클래스·팩토리
  동적 생성을 피한다.
- 공통 로직은 `dagster_project/common/`, 정의는 `dagster_project/defs/`에 둔다.
- 일반 파일은 `pa.Table` + Iceberg IO manager, 대용량 파일은 스트리밍 청크 append
  경로를 사용한다.
- dbt 적재분은 `source()`와 `meta.dagster.asset_key`로 lineage를 연결한다.
- `@dbt_assets`는 `select="fqn:<dataset>"`을 사용한다.
- 저장은 UTC, 표시와 스케줄은 `Asia/Seoul`을 사용한다.
- 비밀값은 환경변수 참조로만 주입하고 코드·문서·응답에 값을 노출하지 않는다.
- 같은 로직의 추출은 세 번째 반복부터 검토한다(Rule of Three).
- 상세 규칙은 `docs/conventions/dagster.md`, `dbt.md`, `python.md`,
  `timezone.md`, `docker.md`, `k8s.md`, `terraform.md`를 따른다.

## 데이터 분석과 거버넌스

- 분석은 gold 모델 → 탐색 노트북 → 분석 리포트 3층으로 구분한다.
- 리포트 수치는 gold/dbt 모델을 경유하고 코호트 attrition과 결측 처리를 남긴다.
- 원천 데이터·추출물·소규모 셀을 저장소, 로그, 프롬프트, 저널에 복사하지 않는다.
- DUA·개인정보·가명정보 데이터의 재식별을 시도하지 않는다.
- `.ipynb` 출력과 체크포인트는 검증 직후 제거한다.
- 추출물은 `$DATA_EXTRACT_DIR` 밖에 쓰지 않고, 저널은 `$OBSIDIAN_VAULT`에만 쓴다.
- 중요 작업(저장소 수정·위임·결정·비가역)은 사용자 최종 보고 전에 `archivist`로
  `$OBSIDIAN_VAULT/agents/<KST 날짜>/<NN>-<mission>.md`와 공용
  `agents/_MOC.md`를 함께 갱신한다. 태그는 `runtime/codex`다.
- 외부 검색 질의에는 내부 데이터·테이블 값·비밀정보를 넣지 않는다.

## 테스트와 검증

변경 영역에 필요한 최소 검증을 실제로 실행한다. 실행하지 못한 검사는 `미실행`으로
명시하며 통과했다고 표현하지 않는다.

1. dbt 스키마 테스트
2. `dg check`·`dbt parse`·필요한 스모크 테스트
3. dbt 단위 테스트
4. Dagster pytest
5. dbt singular test
6. 분석 재현성 검사

Python 변경은 `ruff check`, SQL 변경은 `sqlfluff lint`, Terraform 변경은
`terraform fmt -check`와 `terraform validate`를 우선한다. 새 게이트는 가능하면
일부러 위반시켜 실제 차단 여부까지 확인한다.

## Codex 워커 구성

프로젝트 전용 워커는 `.codex/agents/*.toml`에 있다.

| 워커 | 책임 | 쓰기 정책 |
| --- | --- | --- |
| `analyst` | EDA·분석 리포트·gold 승격 제안 | `notebooks/**`, `docs/analyses/**` |
| `data-engineer` | Dagster·dbt·적재 구현 | 인프라 선언 제외 workspace write |
| `data-extractor` | 읽기 전용 조회 후 승인된 외부 추출 경로에 저장 | 저장소 쓰기 금지 |
| `data-verifier` | 실제 값·grain·lineage 대조 | read-only |
| `data-qa` | 테스트 체계·커버리지 감사 | read-only |
| `devops-engineer` | Docker·Compose·Kubernetes·Terraform 구현 | 파이프라인·분석 영역 제외 |
| `devops-verifier` | 실행 중 인프라와 선언 대조 | read-only |
| `devops-qa` | 인프라 선언·게이트 체계 감사 | read-only |
| `security` | 비밀·노출·거버넌스 검토 | read-only |
| `researcher` | 외부 1차 출처 조사 | read-only |
| `tech-writer` | `docs/**`·`README.md` 문서화 | 제한된 workspace write |
| `archivist` | 저장소 밖 미션 저널·MOC 정합성 | 저장소 쓰기 금지 |

### 위임 규칙

- 간단하고 한 영역에 국한된 작업은 메인 에이전트가 직접 수행한다(YAGNI).
- 둘 이상의 독립적인 읽기·조사·검증 작업은 관련 커스텀 워커에 위임할 수 있다.
- 구현은 한 시점에 한 워커만 같은 파일 집합을 소유한다. 병렬 쓰기를 피한다.
- 판정자는 자기 판정 대상을 수정하지 않는다. 발견과 근거만 반환한다.
- 워커는 다른 워커를 다시 배정하지 않는다. 오케스트레이션은 메인 에이전트가 맡는다.
- 위임할 때 범위·성공 조건·쓰기 허용 경로·검증 기대값을 명시한다.
- 모든 결과를 기다린 뒤 메인 에이전트가 직접 재검산하고 하나의 답으로 취합한다.
- 서브에이전트는 추가 토큰을 사용하므로 독립성이 분명할 때만 사용한다.

## 스킬

- 프로젝트 스킬 정본은 `.agents/skills/<name>/SKILL.md`다.
- 사용자 요청이나 작업 설명이 스킬의 trigger와 일치하면 해당 스킬을 사용한다.
- 스킬 지침보다 프로젝트 규칙과 사용자 지시가 우선한다.
- 외부 출처의 스킬은 `skills-lock.json`의 출처·해시와 `docs/skills.md`의 보안 판정을
  확인한다.
- 스킬 설치·삭제·lock 변경은 공급망 변경이므로 사용자 요청과 보안 검토 없이 하지
  않는다.

## 권한과 비가역 작업

- 기본은 `workspace-write` 샌드박스와 네트워크 비활성이다.
- 다음 작업은 사용자 명시 요청 또는 런타임 승인 없이 실행하지 않는다:
  커밋·푸시·PR/이슈 작성, 패키지 설치, 외부 다운로드/업로드, `terraform apply/destroy`,
  `kubectl apply/delete`, Helm 변경, 볼륨 삭제, `dbt --full-refresh`, 파괴적 SQL,
  Iceberg 유지보수, `.env`·state·크리덴셜 수정.
- 금지된 HTTP mutation과 비밀·state 파일 쓰기는 Codex hook이 차단한다.
- `.codex/rules/*.rules`는 샌드박스 밖 명령의 승인 정책이며, 파일 경계는 sandbox와
  워커별 hook/instructions가 함께 담당한다.

## Codex 런타임 한계

- Codex `PreToolUse`는 `deny`는 지원하지만 Claude의 대화형 `ask` 결정을 동일하게
  지원하지 않는다. 그래서 Codex hook은 확정 금지에는 `deny`, 판단이 필요한 경우에는
  모델 컨텍스트 경고와 execpolicy `prompt`를 사용한다.
- hosted `WebSearch`는 로컬 tool hook 경로를 통과하지 않는다. 외부 질의의 데이터
  거버넌스는 `researcher` 지침과 메인 에이전트의 재검토로 보강한다.
- 외부 저널·추출 경로는 기본 workspace sandbox 밖이다. 해당 작업은 별도 승인 없이는
  수행하지 않는다.

## 완료 보고

- 결과를 먼저 말하고 변경 파일·실행한 검사·미실행 검사·잔여 위험을 짧게 정리한다.
- 근거가 필요한 외부 사실은 공식 1차 출처의 제목과 링크를 함께 제시한다.
- 후속 행동은 `A`, `B`, `C` 단축키와 `else` 선택지를 제공한다.
