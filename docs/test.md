# 테스트 규칙 (testing)

이 문서는 pipeline-study 파이프라인의 **테스트 전략·규칙의 단일 출처**다.
계층별로 *무엇을 · 어디서 · 어떻게* 테스트하는지 정하고, 각 계층의 세부 규약은
[`conventions/dbt.md`](conventions/dbt.md)·[`conventions/python.md`](conventions/python.md)·[`conventions/dagster.md`](conventions/dagster.md)와 교차링크한다.

버전 컨텍스트: `dagster==1.12.12` · `pytest`(dev 의존성) · **`dbt-spark>=1.11,<1.12`(현행 기본)** ·
`dbt-trino>=1.8,<2.0`(값 대조용 존치) — dbt-core 1.8+ = 단위 테스트 지원 계열.
dbt 기본 타깃은 **`spark_connect`** 다. 아래 명령의 `--target dev`는 **trino 경로**를 뜻한다
([conventions/dbt.md](conventions/dbt.md) §dbt-spark 타깃).

## 테스트 계층 (우선순위 순)

전역 규칙(**효율·비용·리스크·정확성**)에 따라 **비용 대비 회귀 방어 효과가 큰 순서**로 나열한다.
위에서부터 채운다.

| 순위 | 계층 | 우선도 | 이유 (효율·비용·리스크·정확성) |
| --- | --- | --- | --- |
| 1 | **dbt 스키마 테스트** | ★★★★★ | 선언 한 줄로 데이터 무결성 회귀를 즉시 포착. 비용 최저, 규약·패키지 이미 존재 |
| 2 | **통합·스모크**(`dg check`·`dbt build`) | ★★★★★ | 정의 로드·빌드 깨짐을 배포 전 차단하는 안전망. CI 게이트의 뼈대 |
| 3 | **dbt 단위 테스트** | ★★★★☆ | SOFA·Sepsis-3 등 복잡 SQL 분기 로직의 정확성 검증. 목킹 셋업 비용 있음 |
| 4 | **Dagster 에셋 pytest** | ★★★☆☆ | 적재 헬퍼·자산 로직 검증. 외부 리소스(S3·dbt) mock 필요로 비용 중간 |
| 5 | **dbt singular 테스트** | ★★☆☆☆ | 스키마 테스트로 표현 못 하는 교차 테이블 불변식만 선별 사용 |
| 6 | **분석 재현성** | ★★☆☆☆ | 파이프라인이 아니라 **결론**을 방어한다. 실인프라가 필요해 비싸고 느리지만, 다른 어떤 계층도 "이 수치가 재현되는가"를 묻지 않는다 |

> **실인프라에 붙는 계층은 넷이고, 넷 다 상시 CI가 아니라 수동 관문이다** — §5-1(Spark Connect
> 어댑터 스모크, *의존성 상한 인상 직전*) · **§5-3**(Iceberg changelog 판독, *changelog 경로를 건드리기
> 직전*) · **§5-5**(S3 호환 스토리지 합격 스위트, *스토어 교체 판단 직전* — 대조군이 현행 스토어라
> 우리 인프라에 붙는다) · §6(분석 재현성, *분석 산출물 공유 직전*).
> 앞의 셋은 [test/manual-gates.md](test/manual-gates.md)에 있다. 나머지는 실인프라 미접속이
> 원칙이다(격리·재현).
>
> **예외는 둘 → 셋 → 넷으로 늘어 왔다.** "분석 재현성만 실인프라에 붙는다"는 초기 서술은
> 이미 §5-1로 깨졌고 §5-3·§5-5가 차례로 더해졌다 — **예외는 계층이 아니라 *관문 시점*으로
> 구분**한다. 넷 다 "무엇을 하기 직전에 통과시키는가"가 다르고, 그것이 이 예외들을 정당화하는
> 유일한 축이다. ⚠️ **관문을 더할 때 이 수를 함께 고친다** — 본문만 늘리면 이 "넷"이 조용히 낡는다.
>
> ⚠️ **§5-4는 이 넷에 더하지 않는다** — 붙는 대상이 *우리 실인프라*가 아니라 **외부 서비스**다.
> "수동 관문"이라는 성질은 같아도 축이 달라 같은 수로 세지 않는다.

**위 표에 §7이 없는 것은 누락이 아니다.** 1~6번은 **파이프라인의 값**을 방어하는 계층이라 한 축에
줄 세울 수 있다. **§7 가드 단위 테스트는 대상이 다르다** — 방어하는 것이 데이터가 아니라
**통제 배선**(hook·경로 경계)이라 같은 축에 순위를 매기면 두 종류를 한 숫자로 세게 된다.
비용이 낮아 **상시 게이트**로 둔다(`always_run`).

---

## 1. dbt 스키마(데이터) 테스트 — ★★★★★

> 상세 규약·패키지는 [`conventions/dbt.md#테스트-필수`](conventions/dbt.md#테스트-필수). 여기서는 **무엇에 다는지**를 정한다.
>
> **품질 *차원*과 실패 시 등급의 정본은 [`conventions/data-quality.md`](conventions/data-quality.md)** 다.
> 이 문서는 *어떻게 테스트하나*를, 그 문서는 *무엇을 품질로 보고 깨지면 무엇을 하나*를 갖는다.

- **모든 모델의 grain 컬럼**(예: `stay_id`·`subject_id`·`hadm_id`·`charttime`)에 `not_null`을 단다.
- **grain을 이루는 키/키 조합**에 `unique`(단일)·`dbt_utils.unique_combination_of_columns`(복합)를 단다.
  - 예: `sepsis3`는 `stay_id` 1건/재실 → `unique`. `sofa`는 `(stay_id, hr)` 조합 → 복합 유니크.
- **원천/상위 모델 참조 키**에 `relationships`를 달아 lineage 무결성을 보장한다
  (예: 개념 모델의 `stay_id` → `icustay_times.stay_id`).
- **유한 범주·점수 컬럼**에 `accepted_values`/범위 테스트를 단다.
  - SOFA 장기 점수(`*_24hours`)는 `0~4`, 총점 `sofa_24hours`는 `0~24`,
    플래그(`sepsis3`·`suspected_infection`·`positive_culture`)는 `[0, 1]`.

```yaml
# models/mimic_iv/tables/schema.yml — grain·범위 테스트 예
models:
  - name: sofa
    columns:
      - name: stay_id
        data_tests: [not_null]
      - name: sofa_24hours
        data_tests:
          - not_null
          - dbt_expectations.expect_column_values_to_be_between:
              min_value: 0
              max_value: 24
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [stay_id, hr]
```

> **키워드 주의**: dbt 1.8+는 `tests:` → **`data_tests:`** 로 이름이 바뀌었다.
> 구 키는 deprecated이므로 신규는 `data_tests:`로 쓴다.

## 2. dbt 단위 테스트 (unit tests) — ★★★★☆

> 입력을 **목(mock)** 으로 고정하고 **기대 출력**을 검증한다. 데이터가 아니라 **SQL 변환 로직**을 테스트한다.
> 작성은 스킬 [`adding-dbt-unit-test`]을 활용한다.

- **대상**: 조건 분기·집계·윈도우가 복잡해 회귀 위험이 큰 모델.
  이 레포에선 **SOFA 점수 산출(`sofa`)·Sepsis-3 판정(`sepsis3`)·환기 상태 분류(`ventilation`)**
  같은 규칙 기반 파생 모델이 1순위다.
- **위치**: 대상 모델과 같은 `models/<dataset>/**/` 아래 `.yml`의 `unit_tests:` 블록.
- **grain 최소 표본**만 목킹한다(경계값·null·범위 밖 값 위주). 전체 컬럼을 채우지 않는다.

```yaml
# 예: SOFA 총점이 6개 장기 합인지 최소 표본으로 검증
unit_tests:
  - name: test_sofa_total_is_sum_of_organs
    model: sofa
    given:
      - input: ref('icustay_hourly')
        rows: [{stay_id: 1, hr: 0, endtime: "2020-01-01 00:00:00"}]
      # ... 각 장기 입력 ref를 경계값으로 목킹 ...
    expect:
      rows: [{stay_id: 1, hr: 0, sofa_24hours: 6}]
```

> 어댑터(`dbt-trino`)의 단위 테스트 지원 여부를 **도입 전 1개 모델로 확인**한다(미지원 시 3번 singular로 대체).

## 3. dbt singular 테스트 — ★★☆☆☆

- 스키마/단위 테스트로 표현하기 어려운 **교차 테이블 불변식**만 `dbt_pipelines/tests/*.sql`에 둔다.
  (테스트 SQL은 **위반 행을 반환**하면 실패 — 통과 시 0행.)
- 예: `sepsis3.suspected_infection_time`이 항상 `sofa` 관측 구간 안에 드는지 등 조인 기반 규칙.
- 남용하지 않는다 — 단일 컬럼 규칙은 1번(스키마 테스트)으로 충분하다.

## 4. Dagster 에셋 pytest — ★★★☆☆

> 상세는 [`conventions/python.md#테스트`](conventions/python.md#테스트). 테스트는 `src/tests/` 하위, `pytest`로 작성한다.

- **적재 헬퍼**(`common/helper.py`의 `read_csv_gz_table`·`load_heavy_csv_gz_to_iceberg`)의 **순수 변환 로직**을
  작은 인메모리 표본으로 검증한다(파싱·스키마·행 수).
- **`@asset` 함수**는 `materialize`로 검증하되 **외부 리소스(S3·dbt·Trino)는 mock/stub**으로 주입한다
  (실제 SeaweedFS·Trino에 붙지 않는다 — 단위 테스트는 격리·재현이 원칙).
- Dagster 정의 무결성(자산키·의존성·리소스 매핑)은 4번보다 5번 스모크(`dg check`)로 싸게 잡는다.

```python
# src/tests/test_assets.py — 리소스 mock + materialize 예 (권장 패턴)
from unittest.mock import MagicMock

import pyarrow as pa
from dagster import materialize

from dagster_project.defs.eicu.assets import patient


def test_patient_loads_table() -> None:
    s3 = MagicMock()  # S3Resource stub — 실제 SeaweedFS 미접속
    # helper가 읽을 표본을 반환하도록 구성...
    result = materialize([patient], resources={"s3": s3, "io_manager_eicu": MagicMock()})
    assert result.success
```

> 자산 정의 모듈은 `from __future__ import annotations` 금지 규칙([dagster.md](conventions/dagster.md#자산-모듈에서는-from-__future__-import-annotations-금지))이 테스트 임포트에도 그대로 적용된다.

> **원천 획득**(`test_physionet_fetch.py`)은 판정 로직을 순수 함수로 떼어 외부 접속 없이 센다 —
> 멱등 판정 7갈래·gzip 본문 가드·traversal·해시 리더·마스킹. 스킵 테스트에는 **업로드가 불리지
> 않았음**을 함께 단언한다(음성 대조). 축별 근거는 그 파일 독스트링, 실제 다운로드는 §5-4.

## 5. 통합·스모크 — ★★★★★

배포·CI의 **최소 게이트**. 개별 로직이 아니라 **파이프라인이 로드·빌드되는지**를 싸게 검증한다.

| 명령 | 검증 내용 |
| --- | --- |
| `dg check` | Dagster 정의(자산·리소스·잡·스케줄) 로드·타입 정합성 |
| `dbt build --target dev` | dbt 모델 컴파일 + run + 스키마/단위/singular 테스트 일괄 실행 |
| `ruff check` · `sqlfluff lint` | 정적 검사 — **pre-commit 훅으로 자동 실행**(`.pre-commit-config.yaml`) |
| `mypy` | 타입 정합성 — **훅 미포함, 수동 실행**(의존성 환경 필요) |

> **"정적 검사"를 한 덩어리로 읽지 않는다.** 한때 이 표는 세 도구를 함께 적어
> 셋 다 커밋 시 도는 것처럼 보였으나, 실제로는 `sqlfluff`·`mypy` **둘 다 훅에 없었다**.
> 지금 `sqlfluff`는 들어갔고 **`mypy`는 여전히 수동**이다 — 목록에 있다는 것과 실행된다는 것은 다르다.

> `dbt build`는 `run`+`test`를 합쳐 실행하므로 **1~3번 dbt 테스트가 이 명령에 자동 포함**된다.
> Dagster 경유 실행 시엔 `dbt.cli(["build"])`가 `dbt_assets`에서 동일하게 테스트를 태운다([dagster.md](conventions/dagster.md#dbt-통합-pythonic-dbt_assets)).

### 5-1 ~ 5-5. 수동 관문 — 본문은 [test/manual-gates.md](test/manual-gates.md)

실인프라·외부 서비스에 붙어 **사람이 시점을 골라 돌리는 관문**은 별도 문서로 분리했다
(이 문서가 doc_lint 500줄 상한에 닿았다). **절 번호는 그대로 쓴다** — `§5-1`·`§5-3` 참조가
가리키는 절은 같고 **바뀐 것은 경로뿐**이다.

| 절 | 관문 | 무엇을 하기 직전에 통과시키나 |
| --- | --- | --- |
| §5-1 | Spark Connect 어댑터 스모크 | 의존성 상한 인상 직전 |
| §5-2 | — (관문 아님) | `iceberg_maintenance_job`이 구조적으로 못 하는 것 |
| §5-3 | Iceberg changelog 판독 + 소스 적격성 진단 | changelog 경로 변경 직전 |
| §5-4 | PhysioNet 접근 판정 | 수집 자산을 처음 켜기 직전 |
| §5-5 | S3 호환 스토리지 합격 스위트 | 스토어 교체를 판단하기 직전 |

요지만: 넷 다 **상시 CI 게이트가 아니고**, 각자 *무엇을 보증하지 않는지*를 본문에 달고 있다.
🔴 **종료 코드의 의미가 관문마다 같지 않다** — §5-1·§5-3·§5-4는 `0`=통과 / `1`=회귀 /
`2`=판정 불가이지만 **§5-5는 `0`이 "관측 경로가 살아 있었다"** 이고 `1`을 쓰지 않는다.
표가 닮았다고 같은 뜻으로 읽으면 FAIL이 섞인 `0`을 전부 통과로 읽는다.

---

## 6. 분석 재현성 검증 — ★★☆☆☆

> 규칙 정본은 [`conventions/analysis.md`](conventions/analysis.md). 여기서는 **무엇을 검증하는지**를 정한다.
> 1~5번이 "파이프라인이 옳게 도는가"를 묻는다면, 이 계층은 **"내린 결론이 재현되는가"** 를 묻는다.

- **노트북은 실행 가능해야 한다.** 위→아래 1회 실행으로 끝까지 도는지 `nbconvert`로 확인한다.
  중간에 죽는 노트북은 고치거나 지운다(탐색이 끝난 노트북은 지우는 게 기본이다).

```shell
kubectl port-forward svc/spark-connect 15002:15002   # 별도 터미널 (실인프라 필요)

cd dagster/dockerfile.d/src
uv run --group notebook jupyter nbconvert --to notebook --execute \
    --output /tmp/_verify.ipynb ../../../notebooks/00-lakehouse-connect.ipynb
```

- **실행 산출물은 즉시 지운다.** `--output` 사본과 `.ipynb_checkpoints/`에는 조회 결과가 그대로
  박제된다(DUA — [`security.md`](security.md)). 검증 직후 삭제하고 저장소로 들이지 않는다.
- **리포트의 인용 수치는 재현 경로를 갖는다.** `docs/analyses/`의 각 수치가 gold 또는 dbt 모델을
  경유하는지 확인한다. 노트북 임시 SQL로 낸 숫자는 리포트에 실리지 않는다(analysis.md §4).
- **gold 모델 자체의 무결성은 1번(스키마 테스트)이 맡는다** — grain 유니크·범위·`relationships`.
  이 계층에 중복해서 두지 않는다.

> **격리 원칙의 의도된 예외**: 1~4번은 실인프라에 붙지 않는 것이 원칙이지만, 이 계층은
> **실제로 붙어야만 의미가 있다**(접속·권한·데이터 존재가 검증 대상이다). 그래서 CI 상시 게이트가
> 아니라 **분석 산출물을 커밋·공유하기 직전의 수동 관문**으로 쓴다.
>
> 실제로 이 검증이 잡은 사례: 호스트에서 pyiceberg로 붙을 때 `list_tables()`는 성공하고
> `load_table()`만 `ACCESS_DENIED`로 죽는 **부분 성공**(카탈로그는 Postgres, `metadata.json`은 S3).
> 컴파일·로드 검증으로는 절대 드러나지 않는 층이다([`operations.md`](operations.md) §1-2).

## 7. 가드 단위 테스트 — ★★★★☆

**대상이 데이터가 아니라 통제 배선이다.** 1~6번이 파이프라인의 값을 방어한다면 이 계층은
**가드가 실제로 막는가**를 방어한다. `scripts/tests/test_journal_guard.py`가 임시 Git 저장소와
임시 볼트를 만들어 hook의 입출력을 실제로 주고받는다.

```bash
# repo 루트에서 — pre-commit 훅 `guard-tests`가 부르는 것과 같은 명령이다
python3 scripts/tests/run_guard_tests.py
```

**어디서 도는가**: 로컬 훅 `guard-tests` 하나로 두 곳이 덮인다 — 로컬은 `files:`가 매치할 때
(가드·테스트를 고칠 때만), CI는 `lint` 잡의 `pre-commit run --all-files`가 모집단을 전체로
넘기므로 **항상** 돈다. 선언이 한 곳이라 두 게이트가 갈리지 않는다.

**왜 `python3 -m unittest`를 직접 걸지 않는가**: `unittest`의 종료 코드는 **0건 실행과 skip을
통과로 읽는다.** 실측 대조 — 테스트 하나에 `skipTest`를 넣은 같은 상태에서
`python3 -m unittest scripts.tests.test_journal_guard`는 **exit 0**, 러너는 **exit 1**이었다.
게이트에 걸어 둔 채 skip되면 **게이트가 없는 지금과 같은 상태인데 초록으로 보인다**.
그래서 러너가 `실행 N개 · skip M개 · 실패 K개`를 갈라 찍고 앞의 둘을 각각 실패로 친다.
같은 이유로 `setUp`의 `git` 미검출은 `skipTest`가 아니라 **실패**다(*"면제는 검증이 아니다"*).

**계측 단위**: 위 `실행 N개`는 **테스트 메서드 수**이지 *검증되는 가드 수*가 아니다.
현재 가드 3종을 13개 메서드가 나눠 본다. 러너는 이 수를 **고정값과 대조하지 않는다** —
막고 싶은 것은 「13이 아님」이 아니라 **「0건」** 이다.

**이 계층이 보증하지 않는 것 — 가드 9종 중 3종만 본다.**

| 축 | 가드 |
| --- | --- |
| 테스트 있음(이 계층) | `journal_guard` · Claude/Codex `worker_path_guard` · `plan_mirror_guard`(일부) |
| **테스트 0건** | `analyst_path_guard` · `skill_gate_guard` · `research_gate_guard` · `protected_paths_guard` · `session_sync_guard` · `commit_manifest_guard` |

이 훅의 초록을 **「가드 전부가 검증됐다」로 읽지 않는다**. 잔여 6종은 별도 항목이다(Issue #55).

### 게이트가 언제 도는가도 검사 대상이다

로컬 훅의 `files:`에 그 훅이 실행하는 스크립트가 빠져 있으면, **그 스크립트만 고치는 커밋에서
훅이 통째로 `Skipped`** 가 된다 — 하필 게이트를 바꾸는 커밋에서 게이트가 안 돈다.
`scripts/hook_files_check.py`(훅 `hook-files`)가 `.pre-commit-config.yaml`을 파싱해 이것을 전수한다.
사례가 셋이고 **관측자도 셋**이라(각각 하나씩 찾았다) 개별 수정이 아니라 검사기로 닫았다.

`always_run: true`인 훅은 **면제가 아니라 모집단이 전체**라 사각이 없다 — 검사기는 이것을
통과와 합치지 않고 **`전체모집단`** 으로 따로 센다. 「사각 없음」을 한 숫자로 적으면
다음 사람이 *"면제받은 훅이 있다"* 로 읽는다.

⚠️ 이 검사기가 보는 축은 **「모집단에 자기 검사기가 빠짐」 하나**다(훅 *전체*가 안 돎).
「모집단에 gitignore 경로가 섞임」(규칙 *일부*가 안 돎)은 **다른 축**이고 감사 축(`/skill-audit`)이 본다.
`.claude/settings.json`의 hook은 `files:` 축 자체가 없어 여기서 보지 않는다.

## 위치·네이밍 규칙

| 테스트 유형 | 위치 | 네이밍 |
| --- | --- | --- |
| dbt 스키마/단위 테스트 | 모델과 같은 `models/<dataset>/**/schema.yml`(또는 `_<dataset>__models.yml`) | dbt 표준 키(`data_tests:`·`unit_tests:`) |
| dbt singular 테스트 | `dbt_pipelines/tests/*.sql` | 검증 대상이 드러나게 (`assert_<불변식>.sql`) |
| Dagster pytest | `dagster/dockerfile.d/src/tests/` | `test_*.py` · 함수 `test_*` |
| 가드 단위 테스트 | `scripts/tests/` (러너 `run_guard_tests.py`) | `test_*.py` — 러너의 글롭 패턴이 이 이름을 전제한다 |

- 테스트 코드는 ruff `per-file-ignores`로 `ANN`·`D`·`S101`(assert)·`ARG` 면제
  (설정은 루트 `pyproject.toml`의 `"**/tests/**"`, 상세 [python.md](conventions/python.md#테스트)).

## 실행 명령

```bash
# dbt (스키마·단위·singular 테스트 일괄) — src/dbt_pipelines 에서
dbt build --target dev      # run + test
dbt test                    # 테스트만

# Dagster 에셋 pytest — src/ 에서
pytest                      # tests/ 하위 수집

# 정의 스모크 (정적 로드 검증)
dg check

# 정적 검사 (pre-commit이 커밋 시 자동 실행 — repo 루트에서)
ruff check . && sqlfluff lint dagster/dockerfile.d/src/dbt_pipelines/

# 가드 단위 테스트 (§7) — 훅 `guard-tests`가 부르는 것과 같은 명령
python3 scripts/tests/run_guard_tests.py

# 로컬 훅의 files: 자기 검사기 포함 검사 (§7) — 훅 `hook-files`가 부르는 것과 같은 명령
python3 scripts/hook_files_check.py

# 타입 정합성 (훅에 없다 — 수동)
uv run --project dagster/dockerfile.d/src --with mypy mypy dagster/dockerfile.d/src/src

# 수동 관문 (실인프라 필요 — 상시 CI 아님. 규약은 test/manual-gates.md)
uv run scripts/spark_connect_smoke.py          # §5-1 의존성 상한 인상 직전
uv run scripts/iceberg_changelog_probe.py      # §5-3 changelog 경로 변경 직전
uv run scripts/physionet_access_probe.py       # §5-4 수집 자산을 처음 켜기 직전
uv run scripts/storage_conformance_probe.py    # §5-5 스토어 교체 판단 직전
```

## 무엇을 테스트하고, 무엇을 안 하나

- **테스트한다**: 파생 로직(SOFA·Sepsis-3 등)의 정확성, grain 무결성(유니크·not_null),
  값 범위·범주, lineage 참조 무결성, 정의 로드·빌드.
- **테스트하지 않는다**: 외부 시스템 자체(SeaweedFS·Trino·Postgres의 동작), 원천 데이터의 임상적 타당성
  (데이터셋 제공자 책임), 서드파티 라이브러리 내부. **단위 테스트에서 실인프라 접속 금지**(격리·재현).

## 누가 쓰고, 누가 채점하나

테스트는 **전담 워커(`tester`)를 두지 않는다** — 행위가 이미 3축에 분해돼 있다
(정본 [`agents/workers.md`](conventions/agents/workers.md#전문-워커-3종-세트의-경계-중첩-금지)).

| 행위 | 워커 | 축 |
| --- | --- | --- |
| 테스트를 **쓴다** | `data-engineer` | 구현 |
| 통과했는데 **값이 이상하다** | `data-verifier` | 실측 |
| **커버리지·게이트**를 감사하고, 작성된 테스트를 **사후 채점**한다 | `data-qa` | 체계 |

🔴 **사후 채점은 생략하지 않는다** — 판정자가 쓰지 않으므로 구현자가 자기 코드의 테스트를 쓴다.
작성자와 채점자를 가르는 유일한 지점이고, 채점의 핵심 물음은 **"이 테스트를 일부러 위반시키면
실패하는가"** 다(통과 확인만으로는 [철학 원칙 7](philosophy.md)의 *실행됐다*뿐인 통과와 구분되지 않는다).

## PDCA

- **Plan**: 계층을 우선순위(★)대로 채운다 — 1) 핵심 grain에 스키마 테스트
  → 2) CI에 `dg check`+`dbt build` 게이트 → 3) SOFA·Sepsis-3 단위 테스트.
- **Do**: `schema.yml`에 `data_tests` 추가부터(비용 최저·효과 최대), 이후 상위 계층으로.
- **Check**: `dbt build --target dev`·`pytest`·`dg check`가 로컬·CI에서 녹색인지.
- **Act**: 회귀가 난 지점에 테스트를 **추가**해 재발을 막는다(테스트는 버그 재현부터).

## 참고

- dbt 데이터 테스트(Data tests): https://docs.getdbt.com/docs/build/data-tests
- dbt 단위 테스트(Unit tests): https://docs.getdbt.com/docs/build/unit-tests
- dbt_utils: https://github.com/dbt-labs/dbt-utils
- dbt_expectations: https://github.com/metaplane/dbt-expectations
- Dagster 자산 테스트(Testing assets): https://docs.dagster.io/guides/test/unit-testing-assets-and-ops
- pytest: https://docs.pytest.org/
