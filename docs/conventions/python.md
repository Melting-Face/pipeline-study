# Python 코딩 규칙

대상 버전: **Python 3.10 ~ 3.14** (`pyproject.toml`의 `requires-python = ">=3.10,<3.15"`)

## 포매팅 / 린팅

- [`ruff`](https://docs.astral.sh/ruff/)로 lint와 format을 모두 처리한다.
- 들여쓰기 스페이스 4칸.
- 커밋 전 실행:

```bash
ruff format .
ruff check --fix .
```

### 설정은 repo 루트 `pyproject.toml`에서 관리한다

ruff 룰·옵션 명세는 별도 `.ruff.toml`을 만들지 않고 **repo 루트 `pyproject.toml`의 `[tool.ruff]`** 에 둔다.
(ruff는 대상 파일에서 상위로 올라가며 `[tool.ruff]`가 있는 pyproject를 탐색하므로 루트 설정이 자동 적용된다.)

```toml
# pyproject.toml (repo 루트)
[tool.ruff]
line-length = 88
indent-width = 4
target-version = "py310"

[tool.ruff.lint]
select = [
    "E", "F", "I", "UP", "B", "D", "ANN", "FA", "RUF",
    "S", "DTZ", "SIM", "C4", "C90", "PIE", "COM", "EM", "PD", "NPY",
    "Q", "ARG",   # Q: 따옴표 스타일, ARG: 미사용 인자
    # 2026-08-21 확장
    "N", "FURB", "PERF", "PT", "TRY", "G", "LOG", "T20",
]
# docstring 미요구, 동적 Any 허용, 포매터 충돌(COM812/819) 제외, 예외 메시지 인라인 허용
ignore = ["D100", "D104", "ANN401", "COM812", "COM819", "TRY003"]
# TC(flake8-type-checking)는 Dagster 런타임 타입 introspection과 충돌해 보류

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.lint.mccabe]
max-complexity = 10   # C90 순환 복잡도 상한

[tool.ruff.lint.per-file-ignores]
# 설정이 루트라 글롭은 임의 깊이의 tests/ 를 잡도록 **/tests/** 사용
# ARG: pytest fixture는 본문 미참조여도 인자로 받으므로 면제
"**/tests/**" = ["ANN", "D", "S101", "ARG"]   # 테스트는 어노테이션·docstring·assert·미사용인자 면제
"scripts/**"   = ["C901", "T201"]             # 절차형 CLI — stdout이 인터페이스
"notebooks/**" = ["S608", "T201"]             # 탐색 노트북 — 리터럴 SQL 조립·print 출력
"k8s/spark/poc_ingest.py" = ["T201"]          # Spark 드라이버 잡 — context.log가 없다

[tool.ruff.format]
indent-style = "space"
```

> ⚠️ 이 블록은 실제 설정의 **사본**이다(문서–설정 이중 관리). 정본은 루트 `pyproject.toml`이며,
> 룰을 바꾸면 **양쪽을 함께** 고친다.

#### 선택 룰 그룹

| 그룹 | 내용 |
| --- | --- |
| `E`·`F`·`I`·`UP`·`B`·`RUF` | pycodestyle·pyflakes·isort·pyupgrade·bugbear·Ruff |
| `D`·`ANN`·`FA` | docstring·어노테이션·future-annotations |
| `S` | bandit 보안(eval·subprocess·SQL injection 등) |
| `DTZ` | tz-aware `datetime` 강제(naive 금지) |
| `SIM`·`C4`·`PIE` | 코드 단순화·컴프리헨션·잡다 개선 |
| `C90` | 순환 복잡도(mccabe, 상한 10) |
| `COM`·`EM` | 트레일링 콤마·예외 메시지 |
| `PD`·`NPY` | pandas·numpy 안티패턴(해당 라이브러리 사용 시) |
| `N` | pep8-naming(클래스·함수·변수 네이밍) |
| `FURB` | refurb — 최신 Python 관용 표현 |
| `PERF` | perflint — 성능 안티패턴(대용량 데이터 처리) |
| `PT` | flake8-pytest-style — pytest 스타일 통일 |
| `TRY` | tryceratops — 예외 처리 패턴 |
| `G`·`LOG` | 로깅 포맷·사용 규칙(f-string 로깅 금지 → 지연 포매팅) |
| `T20` | flake8-print — **실행 경로에서** print 금지 |

> **충돌·제외**: `COM812`/`COM819`(포매터가 트레일링 콤마 처리)는 ignore.
> `TC`(type-checking)는 Dagster 충돌로 켜지 않는다. `PD901`(df 네이밍)은 ruff 0.13에서 제거됨.
> `TRY003`(예외 메시지를 클래스 밖에 작성)은 ignore — 파이프라인 디버깅 메시지 가독성 우선.

> 🔴 **`T20`의 면제 범위가 곧 "실행 경로"의 정의다.** `scripts/**`는 hook 가드가 결정 사유를
> **stdout으로 내보내고 하네스가 그것을 읽으므로** print가 인터페이스이고, 로거로 바꾸면 기능이 깨진다.
> `k8s/spark/poc_ingest.py`는 Spark 드라이버로 제출돼 컨테이너 안에서 단독 실행되므로 `context.log`가 없다.
> 면제 근거는 "귀찮아서"가 아니라 **"그 파일에서는 stdout이 출력 계약이라서"** 이고,
> 그 조건이 아닌 곳(`defs/`·`common/`)에는 적용하지 않는다.
>
> **도입 비용(실측 · `ruff 0.15.20`)**: 확장 룰 8종을 켜자 위반 60건이 나왔고
> 그중 57건이 `T201`(위 3개 경로)이라, 실제 코드 수정은 `G004`×2 + `TRY300`×1 = **3건**이었다.

### 매직 트레일링 콤마로 줄바꿈 유도

함수 호출·정의의 인자가 한 줄 **88자를 넘으면, 마지막 인자 뒤에 `,`(트레일링 콤마)를
추가해 인자를 한 줄에 하나씩 펼치도록 줄바꿈을 유도**한다.
ruff 포매터는 매직 트레일링 콤마가 있으면 인자를 **세로로 펼친 형태로 강제**한다.
(콤마가 줄바꿈을 유발하는 트리거 — 콤마를 빼면 다시 한 줄로 합쳐진다.)
이 동작은 ruff 기본값(`skip-magic-trailing-comma = false`)이라 별도 설정이 필요 없다.

**1) 함수 호출**

```python
# ❌ Before — 한 줄이 88자를 초과
materialize_result = load_csv_gz_to_iceberg(context, identifier=identifier, source_glob=source_glob, chunk_rows=1_000_000)

# ⚠️ 지양 — 인자를 괄호 안 한 줄에 몰아쓰기
materialize_result = load_csv_gz_to_iceberg(
    context, identifier=identifier, source_glob=source_glob, chunk_rows=1_000_000
)

# ✅ After — 마지막 인자에 ',' → 한 줄에 하나씩 펼침
materialize_result = load_csv_gz_to_iceberg(
    context,
    identifier=identifier,
    source_glob=source_glob,
    chunk_rows=1_000_000,
)
```

**2) 함수 정의**

```python
# ❌ Before — 88자 초과
def stream_csv_gz_to_iceberg(catalog, table_identifier, source_path, chunk_rows=1_000_000, mode="replace"):
    ...

# ✅ After — 마지막 파라미터 뒤 ',' → 세로 펼침
def stream_csv_gz_to_iceberg(
    catalog,
    table_identifier,
    source_path,
    chunk_rows=1_000_000,
    mode="replace",
):
    ...
```

**3) 리스트·딕셔너리 리터럴도 동일**

```python
TABLES = [
    ("mimiciv_hosp_patients", "mimiciv.patients", "s3://warehouse/raw/.../patients.csv.gz"),
    ("mimiciv_hosp_admissions", "mimiciv.admissions", "s3://warehouse/raw/.../admissions.csv.gz"),
]
```

> 88자 안에 들어가는 짧은 호출은 콤마 없이 한 줄로 둔다.

## 주석 / 네이밍

- 주석은 **한국어**로 작성한다.
- 식별자(변수·함수·클래스)는 **영어**, `snake_case`(함수·변수) / `PascalCase`(클래스).
- 변수·함수명은 **약어 사용을 지양**하고 의미가 드러나는 **전체 단어**를 쓴다.
  - 예: `message`(O) / `msg`(X), `config`(O) / `cfg`(X), `index`(O) / `idx`(X)
  - 단, 널리 통용되는 import 별칭·표준 약어는 허용: `dg`(dagster)·`pa`(pyarrow)·`pd`·`np`·`df`·`id`·`uri` 등.

## `_` 로 시작하는 이름은 중첩 함수에만

- **모듈 레벨의 변수·상수·함수는 `_`로 시작하지 않는다.**
  Python의 module-private `_name` 관례 대신 `_` 없는 이름을 쓴다.
- `_` 접두어는 **다른 함수 안에 정의된 중첩 함수**에만 허용하며,
  그 enclosing 함수 내부에서만 호출한다.
- 여러 함수가 공유하는 보조 로직은 `_` 없는 **일반 함수/상수로 분리**한다(DRY).

```python
# ✅ enclosing 함수 안에서만 쓰는 보조 함수 → 중첩 + '_'
def build_report(rows: list[dict]) -> str:
    """행 목록을 리포트 문자열로 만든다."""

    def _format_row(row: dict) -> str:
        return f"{row['k']}={row['v']}"

    return "\n".join(_format_row(r) for r in rows)


# ❌ 모듈 레벨 '_' 함수를 여러 곳에서 호출 → 규칙 위반
def _format_row(row: dict) -> str:   # 모듈 레벨엔 '_'를 두지 않는다
    ...


def build_report(rows: list[dict]) -> str: ...
def build_csv(rows: list[dict]) -> str: ...   # 둘 다 _format_row 사용
```

> 공유 보조 함수는 `format_row`처럼 **`_` 없는 일반 함수**로 분리한다.
> (에셋 정의 시 "공통 로직만 일반 함수로 분리" 원칙과 같은 선상)

## 스크립트 스타일 (`scripts/`) — 절차형

`scripts/`의 **실행형 유틸리티**(1회성·운영 스크립트)는 **절차형**으로 쓴다.
라이브러리·에셋 코드(`common/`·`defs/`)와는 규칙이 다르다.

- **호이스팅은 적용**: import·상수·(단일) 함수 정의는 **상단**에, 실행 진입은 **하단**
  (`if __name__ == "__main__": main()`)에 둔다. 위에서 선언 → 아래에서 실행 순서가 드러난다.
- **캡슐화는 최소화**: 상태를 담는 **클래스를 만들지 않는다**. 콜백이 필요하면 지역 변수로 최소화.
- **함수화는 최소화**: 로직을 보조 함수로 잘게 쪼개지 않고 **하나의 `main()`** 안에서 위→아래로
  실행한다(단계는 `# 1) … # 2) …` 번호 주석으로 표시).
- **근거(왜) = 가독성 / Locality of Behaviour**: 스크립트는 **재사용 단위가 아니라 한 번 읽고
  실행하는 절차**라, **실행 순서 = 읽는 순서**일 때 가장 명확하다. 조각난 헬퍼로 점프를 늘리면
  오히려 추적성이 떨어진다. (철학 [philosophy.md](../philosophy.md) 3·6 — 가독성·추적 용이성)
- **경계(Rule of Three는 유효)**: 동일 로직이 **3회 이상** 반복되면 그때 함수로 추출한다.
  라이브러리·에셋 코드에는 이 절차형 규칙을 적용하지 않는다 — 거기선 **관심사 분리·명시적 함수**를
  유지한다([dagster.md](dagster.md) "각 에셋 명시적 분리").
- **린트 면제**: 단일 `main()`은 순환 복잡도 상한과 부딪히므로 `scripts/**`는 **C901 면제**
  (루트 `pyproject.toml`의 `per-file-ignores`). 다른 룰(D·ANN 등)은 그대로 적용된다.
- **의존성**: 외부 패키지를 쓰면 **PEP 723 인라인 메타데이터**로 선언하고 `uv run`으로 실행한다
  (아래 [의존성 관리](#의존성-관리)). 루트 `pyproject.toml`은 도구 설정 전용이라 런타임 의존성을 두지 않는다.

```python
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3"]        # PEP 723: uv run이 자동 provisioning
# ///
"""..."""

import argparse
import sys

CONST = ...                       # 선언은 상단(호이스팅)


def main() -> int:
    """... (절차형: 클래스·보조 함수 없이 위→아래로 실행)."""
    # 1) 인자  → 2) 로드  → 3) 검증  → 4) 실행 ...
    return 0


if __name__ == "__main__":        # 진입은 하단
    raise SystemExit(main())
```

> 실제 예: [`scripts/upload_raw_to_seaweedfs.py`](../../scripts/upload_raw_to_seaweedfs.py)
> (SeaweedFS 원천 업로드 — 단일 `main()`, PEP 723 의존성).

## Docstring (Google 스타일)

ruff `pydocstyle`(`convention = "google"`)로 강제한다.

### 규칙

- **언어**: docstring의 산문은 전부 **한국어**로 쓴다 — 요약 줄과
  `Args`·`Returns`·`Raises`의 설명까지. 식별자·타입·예시 코드만 영어다.
- **요약 줄**: 첫 줄은 **한 줄 요약**, 평서문, **마침표(`.`)** 로 끝낸다.
  여는 `"""`와 같은 줄에서 시작한다.
- **모듈/패키지** docstring은 강제하지 않는다(`D100`·`D104` ignore). 필요할 때만 작성.
- **public 함수·클래스·메서드**에는 docstring을 단다(`D101`·`D102`·`D103`).
- **타입은 docstring에 중복 기재하지 않는다** — 타입 힌트가 단일 출처. `Args`엔 이름·설명만.
- **출처 명시**: 알고리즘·수식·스펙 등 **참고한 출처가 있으면 docstring에 남긴다.**
  `References:` 섹션에 **제목과 URL**을 적는다.

#### 섹션은 조건부 (Google 원전 규칙)

세 섹션은 표준이지만 **항상 강제되는 것은 아니다.** 필요한 섹션만 쓰고 아래 경우엔 생략한다.

| 섹션 | 생략 조건 |
| --- | --- |
| `Args` | 파라미터가 없거나, 이름·시그니처만으로 충분한 한 줄 docstring일 때 |
| `Returns` / `Yields` | `None`을 반환하거나, 요약 줄이 반환값을 충분히 설명할 때 |
| `Raises` | 인터페이스상 의미 있는 예외가 없을 때 |

> `Args` 섹션을 **쓰는 경우** 모든 파라미터를 빠짐없이 기재한다(ruff `D417`이 검사).
> 단, 섹션의 **존재 자체**는 `D` 룰이 강제하지 않는다(누락까지 강제하려면 `DOC` preview 룰 필요).

### 한 줄 docstring

설명이 짧으면 한 줄로 끝낸다.

```python
def slugify(name: str) -> str:
    """문자열을 URL-safe 슬러그로 변환한다."""
    ...
```

### 여러 줄 docstring

```python
def stream_csv_gz_to_iceberg(
    catalog: SqlCatalog,
    table_identifier: str,
    source_path: str,
    chunk_rows: int = 1_000_000,
    mode: str = "replace",
) -> int:
    """S3의 csv.gz를 Iceberg 테이블로 스트리밍 적재한다.

    pyarrow로 블록 단위 스트리밍하며 chunk_rows 단위로 모아 append 한다.

    Args:
        catalog: pyiceberg SqlCatalog 인스턴스.
        table_identifier: "<namespace>.<table>" 형식의 대상 테이블.
        source_path: 원본 csv.gz의 s3 경로.
        chunk_rows: 한 번에 append 할 행 수.
        mode: "replace"(재적재) 또는 "append"(누적).

    Returns:
        적재한 총 행 수.

    Raises:
        FileNotFoundError: source_path가 존재하지 않을 때.
    """
    ...
```

### 출처 표기 (References)

알고리즘·수식·스펙 등 외부 출처를 참고했다면 **기본으로** `References:` 섹션에 제목과 URL을 남긴다.

```python
def winsorize(values: list[float], limits: tuple[float, float]) -> list[float]:
    """양극단 값을 한계치로 절단(winsorize)한다.

    Args:
        values: 입력 수치 리스트.
        limits: (하한, 상한) 절단 비율.

    Returns:
        절단된 수치 리스트.

    References:
        Tukey, J. W. (1962). The Future of Data Analysis.
        https://en.wikipedia.org/wiki/Winsorizing
    """
    ...
```

### Dagster 에셋

에셋 함수의 docstring은 **Dagster UI의 description으로 노출**되므로, 에셋엔 요약 docstring을 권장한다.

```python
@asset(group_name="bronze")
def raw_patients() -> None:
    """MIMIC-IV hosp.patients 원본을 bronze Iceberg 테이블로 적재한다."""
    ...
```

## 타입 힌트

- 새 코드에는 타입 힌트를 단다.
- 모던 문법 사용 (3.10+):
  - `list[str]`, `dict[str, int]` (대문자 `List`/`Dict` 지양)
  - `X | None` (`Optional[X]` 지양), `X | Y` (`Union` 지양)

```python
from pathlib import Path


def load_config(path: Path, retries: int = 3) -> dict[str, str] | None:
    # 설정 파일을 읽어 dict로 반환한다. 없으면 None.
    ...
```

### 강제 규칙 (ruff)

- `ANN` — 함수 인자·반환 어노테이션 강제 (`ANN401` Any는 허용).
- `FA` — 필요 시 `from __future__ import annotations` 권장.
- `RUF013` — 암묵적 Optional(`x: int = None`) 금지.
- `UP` — `Optional`/`List` 등 구문법을 모던 문법으로 자동 수정.
- `TC`(type-checking import 분리)는 **켜지 않는다** — Dagster 런타임 타입 introspection과 충돌한다.
- 테스트(`**/tests/**`)는 `ANN`·`D`·`S101`(assert)·`ARG`(미사용 인자) 면제(`per-file-ignores`).
- `Q`(따옴표 스타일)는 포매터가 강제하는 double-quote와 동일 기본값이라 충돌 없이 명시적으로 둔다.
- `ARG`(미사용 인자)는 Dagster 자산 함수의 `context` 등 실제 미사용 인자를 잡는다 — 필요 시 `_` 접두사로 의도 표시.

## 타입 체커 (mypy)

ruff는 어노테이션의 **존재·스타일**만 본다. **타입 정합성**(값과 타입 불일치)은
[`mypy`](https://mypy-lang.org/)가 검사한다.

```toml
[tool.mypy]
python_version = "3.10"          # 단일 MAJOR.MINOR만 가능 (범위 X)
ignore_missing_imports = true    # dagster·dbt·pyiceberg 등 외부 스텁 부재 대응
disallow_untyped_defs = true     # 함수에 타입 강제 (ruff ANN과 짝)
no_implicit_optional = true
# 전체 강화: strict = true
```

> ⚠️ `python_version`은 **단일 버전 문자열**(`"3.10"`)만 받는다.
> `requires-python = ">=3.10,<3.15"` 같은 **범위 지정자는 쓸 수 없다.**
> 가장 낮은 지원 버전(`3.10`)으로 두어 구버전 호환성까지 검사한다.

`mypy`는 `ruff`·`sqlfluff`와 달리 상위 디렉터리를 탐색하지 않고 **CWD의 설정만** 읽는다.
설정이 repo 루트 `pyproject.toml`에 있으므로 **repo 루트에서 실행**한다.
또한 mypy는 `dagster` 등 **실제 의존성이 설치된 환경**에서 돌려야 속성을 정확히 해석한다
(격리 환경에서 돌리면 `Module "dagster" has no attribute ...` 오탐이 난다).
deps는 src 프로젝트에서 끌어오되 CWD는 루트로 둔다:

```bash
# repo 루트에서: 루트 [tool.mypy] 설정 + src 프로젝트 의존성으로 타입 체크
uv run --project dagster/dockerfile.d/src --with mypy \
    mypy dagster/dockerfile.d/src/src
```

## 예외 처리 (LBYL 우선)

- 가능하면 **사전 점검(Look Before You Leap)** 으로 흐름을 명확히 한다.
- 광범위한 `except Exception` 남발을 피하고, 구체적 예외를 잡는다.

```python
# 권장: 사전 점검
if not path.exists():
    raise FileNotFoundError(path)
data = path.read_text(encoding="utf-8")
```

## 파일 경로

- 문자열 경로 조작 대신 [`pathlib.Path`](https://docs.python.org/3/library/pathlib.html)를 사용한다.
  (현 코드도 `common/dbt.py`에서 `Path(__file__).parents[...]` 사용)

## 의존성 관리

- 패키지 관리는 [`uv`](https://docs.astral.sh/uv/) 사용 (`uv sync`).
- 의존성은 `pyproject.toml`의 `[project].dependencies`에 선언한다.
- **버전 고정(pin)** 을 기본으로 한다. Dagster 관련 패키지는 **버전을 일치**시킨다.

```toml
# 예: dagster 코어/플러그인 버전 일치
dagster        == 1.12.12
dagster-dbt    == 0.28.12
dagster-postgres == 0.28.12
```

- 개발용 의존성은 `[dependency-groups].dev`에 둔다 (`pytest` 등).
- **위 두 규칙은 `dagster/dockerfile.d/src/pyproject.toml`(패키징 프로젝트) 기준**이다.
  repo 루트 `pyproject.toml`은 **도구 설정 전용**이라 런타임 의존성을 두지 않는다.
- **`scripts/`의 단독 스크립트**는 프로젝트 의존성 대신 **PEP 723 인라인 메타데이터**로 선언하고
  `uv run <script>.py`로 실행한다(uv가 격리 환경에 자동 설치). 위 [스크립트 스타일](#스크립트-스타일-scripts--절차형) 참고.

## 테스트

- 테스트는 [`pytest`](https://docs.pytest.org/)로 작성하고 `tests/` 하위에 둔다.
- 테스트 전략 전반(계층·우선순위·Dagster 에셋 테스트 패턴)은 [`../test.md`](../test.md) 참고.

## 참고

- ruff: https://docs.astral.sh/ruff/
- ruff pydocstyle(D) 규칙: https://docs.astral.sh/ruff/rules/#pydocstyle-d
- Google Python Style Guide — Docstrings: https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings
- uv: https://docs.astral.sh/uv/
- PEP 604 (`X | Y` 타입): https://peps.python.org/pep-0604/
- PEP 257 (docstring 규약): https://peps.python.org/pep-0257/
