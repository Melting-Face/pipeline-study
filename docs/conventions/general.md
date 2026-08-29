# 공통 코딩 규칙

모든 언어·파일에 공통으로 적용되는 규칙이다.
언어별 세부 규칙은 [Python](python.md) · [Dagster](dagster.md) · [dbt](dbt.md) 문서를 참고한다.

## 언어 (Language)

| 대상                               | 언어                                   |
| ---------------------------------- | -------------------------------------- |
| 코드 주석                          | **한국어**                             |
| 변수명 · 함수명 · 모델명 등 식별자 | **영어**                               |
| 문서(docs)                         | **한국어** (식별자·명령어·경로는 원문) |
| 커밋 메시지                        | **한국어**                             |

## 들여쓰기 (Indentation)

- **스페이스 4칸**으로 통일한다. (Python · YAML · SQL 공통)
- 탭 문자는 사용하지 않는다.

## 포매터 / 린터

| 대상 | 도구 | 설정 위치 | 게이트 |
| --- | --- | --- | --- |
| Python | [`ruff`](https://docs.astral.sh/ruff/) (lint + format) | 루트 `pyproject.toml` `[tool.ruff]` | pre-commit |
| SQL | [`sqlfluff`](https://docs.sqlfluff.com/) | 루트 `pyproject.toml` + `.sqlfluffignore` | pre-commit |
| 문서(Markdown) | [`scripts/doc_lint.py`](../../scripts/doc_lint.py) | 스크립트 상단 상수 | 수동 (§문서 작성 규약) |
| 커밋 메시지 | [`gitlint`](https://jorisroovers.github.io/gitlint/) | `.gitlint` (루트) | pre-commit (`commit-msg`) |
| YAML | [`yamllint`](https://yamllint.readthedocs.io/) | `.yamllint.yaml` (루트) | pre-commit |
| Dockerfile | [`hadolint`](https://github.com/hadolint/hadolint) | `.hadolint.yaml` (루트) | pre-commit (로컬 바이너리) |
| 셸 스크립트 | [`shellcheck`](https://www.shellcheck.net/) | 설정 없음(기본 룰셋) | pre-commit |
| 시크릿 스캔 | [`gitleaks`](https://github.com/gitleaks/gitleaks) | `.gitleaks.toml` (루트) | pre-commit |
| 노트북 출력 | [`nbstripout`](https://github.com/kynan/nbstripout) | 설정 없음 | pre-commit |
| Python 타입체크 | [`mypy`](https://mypy-lang.org/) | 루트 `pyproject.toml` `[tool.mypy]` | **없음(수동)** |
| EOL 정규화 | git | `.gitattributes` (루트) | git 체크인/체크아웃 |

커밋 전 포매터·린터를 통과시킨다. (`mypy`는 어노테이션이 아닌 **타입 정합성**을 검사)

> **로컬 훅만으로는 강제되지 않는다** — `git commit --no-verify` 한 번이나 훅 미설치 클론으로
> 전량 우회된다. 그래서 같은 검사를 **서버측에서 다시 돌린다**(`.github/workflows/ci.yml`).
> 🔴 **두 층은 모집단이 다르다** — 로컬 훅은 **스테이징된 파일만**, CI는 `--all-files`로 **저장소 전체**를 본다.
> "로컬에서 통과했다"를 "저장소가 깨끗하다"로 읽지 않는다.

### 실행 (pre-commit)

커밋 시 [`pre-commit`](https://pre-commit.com/)이 린터·포매터·시크릿 스캔을 자동 실행한다(`.pre-commit-config.yaml`).
규칙의 단일 출처는 위 표의 "설정 위치"인 도구 네이티브 파일이다.
pre-commit은 **'언제 무엇을 실행할지'만** 정의하며 스테이징된 파일만 검사한다.

```bash
uv tool install pre-commit
brew install hadolint                 # hadolint 훅만 로컬 바이너리를 요구한다(아래 참고)
pre-commit install --install-hooks    # pre-commit + commit-msg 훅 설치
pre-commit run --all-files            # 전체 수동 검사
```

- **포함 훅의 정본은 [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml)** 이다 —
  🔴 **개수·목록을 여기 옮겨 적지 않는다**(과거 문서마다 값이 갈렸다). 계열은
  포매터·린터(`ruff`·`sqlfluff`·`yamllint`·`hadolint`·`shellcheck`) · 시크릿·데이터 반출 차단
  (`gitleaks`·`nbstripout`·로컬 `no-*-files` 훅) · 커밋 메시지(`gitlint`, commit-msg 스테이지) ·
  기본 위생 훅이다.
- **미포함**: `mypy` — 의존성 환경이 필요해 격리 venv에서 마찰이 크다. 로컬은 수동 실행이고,
  **CI(`defs` 잡)가 서버측에서 돌린다**:
  `uv run --project dagster/dockerfile.d/src --with mypy mypy dagster/dockerfile.d/src/src`

**`hadolint`만 로컬 바이너리를 쓴다.** 업스트림 `hadolint-docker` 훅은 `docker` CLI를 요구하는데
현행 환경은 podman뿐이라 `hadolint` 훅(`language: system`)을 쓰고 `brew install hadolint`를 선행한다.
훅 `rev`(`v2.15.1`)를 설치 바이너리 버전과 같게 맞춰 둔다 — 다르면 "훅 정의의 버전"과
"실제 실행본의 버전"이 갈려 재현이 안 된다.

직접 실행도 가능하다:

```bash
ruff check . && ruff format .        # Python
sqlfluff lint dagster/dockerfile.d/src/dbt_pipelines/   # SQL — repo 루트에서 실행할 것
yamllint .                           # YAML
hadolint <Dockerfile>                # Dockerfile
shellcheck scripts/*.sh              # 셸
gitleaks detect                      # 시크릿 스캔
# Python 타입 정합성: repo 루트에서, src 프로젝트 의존성 환경으로 실행
uv run --project dagster/dockerfile.d/src --with mypy mypy dagster/dockerfile.d/src/src
```

> 🔴 `sqlfluff`는 `library_path`를 **CWD 기준 상대경로**로 해석하므로 `mypy`와 마찬가지로
> **repo 루트에서** 실행해야 한다. 하위 디렉터리에서 돌리면 dbt 셰임을 못 찾아
> `TMP: Undefined jinja template variable: 'dbt'`로 죽는다. 상세는 [dbt.md](dbt.md).

### 설정 위치 원칙

- `pyproject.toml`을 지원하는 도구(`ruff`·`sqlfluff`·`mypy`)의 설정은 **repo 루트 `pyproject.toml`**에 모은다.
  pre-commit이 repo 루트에서 실행되므로 설정도 루트에 둬 단일 출처를 맞춘다.
  CI도 같은 이유로 **repo 루트에서** `pre-commit run --all-files`를 돌린다.
  - `ruff`·`sqlfluff`는 대상 파일에서 상위로 올라가며 설정을 탐색해 루트 설정을 자동으로 잡는다.
  - `mypy`는 상위 탐색을 하지 않고 **CWD의 설정만** 읽으므로 반드시 repo 루트에서 실행한다.
- 패키징(`[project]`·`[build-system]`)과 Dagster `dg` 설정은 빌드 컨텍스트인
  `dagster/dockerfile.d/src/pyproject.toml`에 남긴다.
- pyproject 미지원 도구는 **루트의 도구 네이티브 설정 파일**에 둔다.
  - `yamllint` → `.yamllint.yaml` (pyproject 미지원)
  - `hadolint` → `.hadolint.yaml`
  - `gitleaks` → `.gitleaks.toml`
  - `gitlint` → `.gitlint` (repo 루트에서 실행 → 하위 `pyproject.toml` 자동탐지 불가)
- **예외 하나**: `sqlfluff`의 제외 목록은 `pyproject.toml`이 아니라 루트 `.sqlfluffignore`에 둔다.
  훅에도 같은 범위를 `exclude:`로 **이중으로** 건다 — pre-commit은 스테이징 경로를 **명시 전달**하므로
  ignore 파일만으로 걸러진다는 보장이 없다(ruff가 `force-exclude`를 필요로 하는 것과 같은 계열).
- **`sqlfluff`의 dbt 셰임**(`sqlfluff_libs/`)만 루트의 **디렉터리**로 둔다 —
  `library_path`가 CWD 기준이라 루트가 아니면 찾지 못한다. 상세는 [dbt.md](dbt.md).
- 세부 예시는 [Python](python.md) · [dbt](dbt.md) 문서 참고.

## 커밋 메시지 (Conventional Commits)

[Conventional Commits](https://www.conventionalcommits.org/) 규약을 따른다.
gitlint `contrib-title-conventional-commits` 룰로 강제한다(루트 `.gitlint`, pre-commit `commit-msg` 훅).

> 브랜치 전략·커밋 단위·병렬 세션(git worktree)·AI 세션 git 규칙 등 **워크플로**는 [git.md](git.md).

- **형식**: `type(scope): 설명` — `scope`는 선택, **설명은 한국어**.
- 제목은 **72자 이내**(`title-max-length`).
- 파괴적 변경은 `type!: ...` 또는 본문에 `BREAKING CHANGE:` 표기.

### type 종류 (표준 11종)

| type | 용도 | SemVer |
| --- | --- | --- |
| `feat` | 새 기능 | MINOR ↑ |
| `fix` | 버그 수정 | PATCH ↑ |
| `docs` | 문서만 변경 | — |
| `style` | 포맷·세미콜론 등(동작 불변) | — |
| `refactor` | 리팩터링(기능·버그 변화 없음) | — |
| `perf` | 성능 개선 | PATCH |
| `test` | 테스트 추가·수정 | — |
| `build` | 빌드 시스템·의존성 | — |
| `ci` | CI 설정·스크립트 | — |
| `chore` | 잡무(설정·도구 등, src·test 무관) | — |
| `revert` | 커밋 되돌리기 | — |

> 기존 커스텀 type(`mod`·`add`·`del`)은 **폐지**하고 아래로 매핑한다.

| 기존 | → 전환 |
| --- | --- |
| `mod` | 상황별 `feat`(기능) / `fix`(수정) / `refactor` |
| `add` | `feat`(기능) 또는 `chore`·`build`(설정·의존성) |
| `del` | `refactor`(코드 정리) 또는 `chore` |

```text
feat(dagster): Iceberg 컴팩션 op 추가(Trino optimize)
refactor: DuckDB→Trino 전환, SeaweedFS standalone 및 dlt·bronze 리셋
chore: ruff·gitlint 등 린터 설정 추가
docs: 코딩 규칙 문서 추가
```

## 릴리스 / 태그

- **버전**: [SemVer](https://semver.org/) — `vMAJOR.MINOR.PATCH`.
- **정책: 태그·릴리스는 `main`에 반영될 때만 적용한다.**
  - 피처 브랜치에는 태그·릴리스를 만들지 않는다.
  - `main`에 머지된 뒤, `main` 커밋에 `v*` 태그를 push 해서 만든다.
- **자동화**: [`.github/workflows/release.yml`](../../.github/workflows/release.yml) —
  `v*` 태그 push 시 그 커밋이 `main`에 포함된 경우에만 GitHub Release를 생성한다(아니면 건너뜀).
- **릴리스 노트**: Conventional Commits 기반 자동 생성(`gh release --generate-notes`).

### 릴리스 절차

```bash
# 1) main 머지 후 최신화
git switch main && git pull

# 2) annotated 버전 태그 생성·push
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0

# 3) 워크플로우가 main 포함을 확인하고 Release를 자동 생성
```

> 워크플로우는 **`main`(기본 브랜치)에 있어야** 태그 이벤트로 동작한다.

## 문서 작성 규약

`README.md`·`docs/**`·`CLAUDE.md`는 **AI와 사람이 함께 읽는다.** 두 독자는 같은 이유로 막힌다 —
사람은 가로 스크롤을 하게 되고, AI는 구조 없는 텍스트 덩어리를 받는다.

### 정량 상한

검사는 [`scripts/doc_lint.py`](../../scripts/doc_lint.py)가 한다.
상한의 정본은 그 스크립트 상단 상수이고, 아래는 **왜 그 값인지**다.

| 항목 | 상한 | 이유 |
| --- | --- | --- |
| 한 줄 | 120자 | 가로 스크롤 없이 읽힌다 |
| 표 셀 | 200자 | 표는 **대조용**이다. 서술이 들어가면 표가 아니다 |
| 강조 마커 | 문서당 5개 | 강조는 **희소성으로만** 작동한다 |
| 문서 | 500줄 | 넘으면 주제별로 나눈다 |
| 괄호 중첩 | 금지 | 한 문장에 한 생각 |

```bash
uv run scripts/doc_lint.py                 # 저장소 전체
uv run scripts/doc_lint.py --summary       # 파일별 위반 수(진척 측정)
uv run scripts/doc_lint.py docs/setup.md   # 특정 파일
uv run scripts/doc_lint.py --links         # 링크·앵커 + 볼트 참조 (전역)
```

🔴 **링크 검사는 반드시 저장소 전역 1회로 돈다.** 디렉터리를 나눠 검사하면
**각자 0건인데 합집합에 깨진 링크가 남는다** — 실제로 두 관측자가 각자
`.claude/agents/**`와 `docs/**`를 검사해 둘 다 0건을 보고했는데 경계에 2건이 있었다.

**볼트 참조도 같이 검사한다.** 상태 서술을 `$OBSIDIAN_VAULT/status/`로 옮기면
`docs/`에는 **저장소 밖을 가리키는 참조**만 남는데, 링크 검사는 이를 보지 못한다.
`--links`가 볼트 파일·절의 실재까지 확인한다.
⚠️ **볼트가 없는 환경에서는 「검사 안 함」으로 출력된다** — `0건`과 구분해 읽는다
(안 본 것과 통과한 것은 다른 상태다). 개인 환경을 게이트의 전제로 만들지 않기 위한 선택이다.

⚠️ **앵커 슬러그를 손으로 계산하지 마라.** 구두점이 제거되면 그 자리의 공백이
**합쳐지지 않고 각각 하이픈**이 된다(`RBAC·최소권한 (2.5 · 2.6)` → `…rbac최소권한-25--26`).
이 규칙을 틀리면 **정상 링크를 위반으로 잡아 "고치다가" 실제로 깨뜨린다.**

### 서술 규칙

- **한 문장에 한 생각.** 규칙·근거·반례·예외를 한 문장에 겹치지 않는다.
  규칙을 먼저 쓰고, 근거는 다음 문장에 둔다.
- **표에는 대조되는 것만** 넣는다. 문단이 되면 본문으로 내린다.
- **긴 문서에는 소제목을 촘촘히** 둔다. 스캔이 안 되면 길이보다 구조가 문제다.

### 시제 축 — 규칙과 상태를 가른다

🔴 **`docs/`는 규칙을 담고 진행 상태는 담지 않는다.** 판정 테스트는 한 줄이다.

> **6개월 뒤 이 문장이 아무도 손대지 않아도 저절로 거짓이 될 수 있는가?**

| 분류 | 시제 | 목적지 |
| --- | --- | --- |
| **규칙** | 무시제 — "~한다" | `docs/` |
| **근거** | 과거완료 — "그래서 이 규칙이다" | `docs/`, 규칙 옆에 짧게 |
| **상태** | 현재진행 — "아직 ~않다 / 미해소 / 실측 피크" <!-- tense-ok --> | `$OBSIDIAN_VAULT/status/` |

근거를 상태로 오분류해 지우지 않는다. 이 저장소는 실패·번복 이력을 남기는 것이 정책이고,
사고 기록의 **교훈은 규칙의 근거**다. 볼트로 가는 것은 **전말과 수치**뿐이다.

이 규약을 어긴 실례가 있다. 이 문서에 오래 적혀 있던 *"CI는 아직 없다"* 는 <!-- tense-ok -->
`.github/workflows/ci.yml`이 생기면서 **저절로 거짓이 됐고**, 훅 개수는 문서마다 17·19·20으로 갈렸다.
상세는 [`../doc-sync.md`](../doc-sync.md) §동기화 체인.

#### 기계로 집행되는 것은 어휘까지다

[`scripts/doc_lint.py`](../../scripts/doc_lint.py)가 축 2의 하위 규칙 둘을 잡는다 —
**관측·결정 일자**와 **시제 어휘**다. 시제 어휘는 두 모드로 갈린다.

| 모드 | 패턴 |
| --- | --- |
| **기본 검사**(커밋 차단) | `TODO`·`TBD`·`FIXME` / `아직까지`·`지금까지` / `예정이다`·`할 예정`·`될 예정` <!-- tense-ok --> |
| **`--tense` 진단 모드** | `아직` + 부정 서술(`없다`·`않다`·`못한다` 등) / `미해소` / `이번에` <!-- tense-ok --> |

**단계적 편입이 설계다** — 잔여가 0이 된 패턴부터 기본 검사로 올린다. 잔여가 있는 패턴을
기본에 넣으면 전부 빨간불이 되고, 고칠 수 없는 위반이 쌓이면 도구가 통째로 무시된다.
날짜 축이 그 경로를 밟았다(`--dates` 전용 → 정리로 0건 → 기본 편입). 시제 축도 같다.

규칙 문서가 자기 규칙을 설명하려고 **위반 예시를 인용**하는 줄에는 **`<!-- tense-ok -->`** 를 붙인다.
⚠️ 날짜 축의 `<!-- date-ok -->`와 같이 **면제이지 검증이 아니다** — 붙이면 그 줄은 아무도 안 본다.
**인용에만** 쓴다.

**두 축은 예외 범위가 다르다 — 통일하지 않는다.** 날짜 축은 `docs/references.md`와 `wiki/`를
파일·디렉터리 단위로 빼지만, **시제 축은 아무것도 빼지 않는다.** 날짜를 뺀 이유는 "출처 발행일"과
"독자·통제가 다르다"인데, 시제 축이 막는 것은 **낡는 문장이 저장소 밖으로 나가는 것**이라
위키야말로 대상이기 때문이다(미러는 단방향이라 저장소 쪽에서 못 막으면 막을 곳이 없다).
같은 이유로 **영문 어휘의 경계 규칙도 두 축이 반대다** — 시제 축은 조사가 이어 붙은 형태를
잡아야 해서 경계를 ASCII로만 세우고, 날짜 축은 숫자 인접이 오탐이라 숫자를 뺀다. <!-- tense-ok -->
어긋나 보인다고 맞추면 한쪽이 조용히 샌다. 근거는 [`doc_lint.py`](../../scripts/doc_lint.py) 상수 주석에 있다.

이 검사가 보증하지 않는 것 — 어휘 검사는 **문장의 시제를 판정하지 못한다.** 날짜 없이 쓴 실측 수치도,
어휘를 피해 쓴 상태 문장도 통과한다. 잡는 것은 *"이 자리에 그 낱말이 있다"* 까지다.

## 비밀정보 (Secrets)

- 키·토큰·비밀번호를 코드·설정 파일에 **하드코딩하지 않는다.**
- `.env` + 환경변수로 주입한다. (예: Trino 카탈로그의 `${ENV:AWS_ACCESS_KEY_ID}`)
- `.env`는 절대 커밋하지 않는다.

## 디렉토리 규칙

- 서비스별 컨테이너 빌드 컨텍스트는 `<service>/dockerfile.d/` 하위에 둔다.
- Dagster 프로젝트 소스는 `dagster/dockerfile.d/src/` 하위.
- dbt 프로젝트는 `dagster/dockerfile.d/src/dbt_pipelines/`.

## 작업 원칙

규칙을 정하거나 바꿀 때는 **작업 분해 → PDCA(Plan-Do-Check-Act)** 순으로 진행하고,
다음 관점을 함께 점검한다:

- 관점: 데이터 파이프라인 / 데이터 사이언스 / 데이터 분석 / 데이터베이스 / 데이터 거버넌스 / 데이터 보안
- 항목: 효율성(efficiency) / 비용(cost) / 위험(risk) / 정확성(accuracy)
