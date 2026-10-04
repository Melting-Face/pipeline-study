"""DAG 정의의 로드 무결성을 검사한다 — `dg check defs`가 떠난 자리에 거는 게이트.

🔴 **「잃은 것을 메우는」 게이트가 아니다.** `dg check defs`가 주던 보증 중
    「리소스 키 미해결」은 Airflow에 리소스 시스템이 없어 **소멸**하고 되살릴 수 없다.
    그 대신 **현행이 못 잡던 축**에 새로 건다.

    현행 CI 주석이 스스로 한계를 적는다 — `@dbt_assets` 셀렉터가 모델을 0개 수집해도
    로드는 성공하므로 `dg check defs`가 통과시키고, 그래서 **"세는 주체는 여전히
    사람"** 이라고. 🔴 그 사고는 **실재한다**: `eicu_dbt_models`가 `select="fqn:eicu"`로
    선언됐는데 `models/eicu/`의 `.sql`은 **0개**다. 지금도 에러 없이 통과한다.

🔴🔴 **`safe_mode=False`는 장식이 아니다 — 지우면 게이트가 조용히 좁아진다.**
    Airflow는 파일을 import하기 **전에** 내용에서 `airflow`·`dag` 문자열을 grep하고,
    없으면 **건너뛴다**(`safe_mode=True`가 기본). 2026-10-01 실측(Airflow 3.3.2):

        safe_mode=True   → `import nonexistent_module` 한 줄 파일:
                           import_errors **0건**
                           (로그에 "assumed to contain no DAGs. Skipping.")
        safe_mode=False  → 같은 파일: import_errors **1건**

    ⇒ 기본값으로는 **「DAG 없는 파일」과 「깨진 파일」이 구분되지 않는다**(둘 다
    `dag_ids=[]`·`errors=0`·같은 로그). 휴리스틱의 목적은 거대 DAG 폴더 스캔
    성능이고, **배선 파일 몇 개를 보는 테스트에는 그 전제가 성립하지 않는다.**
    `test_broken_file_is_caught_without_heuristic_words`가 이 설정을 **고정**한다.

    ⚠️ 이 사실이 드러난 경위를 적어 둔다: 계획의 위반 프로브가 "DAG 파일에 고의로
    `import nonexistent_module`을 넣으면 임포트 에러 테스트가 실패해야 한다"였는데,
    기본값에서는 **초록으로 통과**한다. **프로브가 작동하지 않는 게이트를 승인할
    수 있었다** — 프로브 설계 자체도 검증 대상이라는 실례다.

🔴 **계측 단위 — 각 수치가 무엇을 세는가**:
    · `EXPECTED_DAG_COUNT`   = *DagBag에 로드된 `dag_id` 개수* (파일 수가 아니다)
    · `dags/**/*.py` 파일 수 = *디스크의 배선 파일 수* (DAG 수가 아니다 — 한 파일이
      여러 DAG을 낼 수도, 0개를 낼 수도 있다)

🔴 **지금 모집단이 비어 있다 — 숨기지 않는다.** P0 시점 `dags/`에는 `.gitkeep`뿐이고
    `.py`가 0개다. 따라서 아래 처음 세 셀은 **공허하게 참**이다(0 == 0). 그것은
    「검사했다」가 아니라 「셀 것이 없었다」다. 단계 2에서 첫 DAG이 들어오는 순간
    처음으로 선다. ⚠️ 그래서 각 셀이 **분모를 함께 단정문에 넣는다.**
    ✅ 단 **마지막 셀은 지금도 실효**다 — 자기 임시 파일을 만들어 판정하므로
    `dags/`가 비어 있어도 게이트 설정을 검사한다.

Airflow 3 API 변경 (실측 — 초안이 Airflow 2 API를 기억으로 썼다가 전부 깨졌다):
    · `airflow.models.dagbag`는 **deprecated** → `airflow.dag_processing.dagbag`
    · `DagBag(include_examples=...)` **인자가 없다**(3.3.2 시그니처에서 사라졌다)
    · `DAG.fileloc` **속성이 없다** → 「이 파일이 DAG을 내는가」는 **파일 단위
      DagBag**으로 판정한다(`dag_folder`에 파일 경로를 줄 수 있음을 실측 확인)

범위 밖 (의도적 공백 — 빠뜨린 것과 구분해 선언한다):
    · **dbt 태스크 수 ↔ 모델 수 대조는 단계 11(Cosmos 이관)의 몫**이다. 지금은
      Cosmos도 dbt DAG도 없어 모집단이 없다. 기대값만 박제해 잃지 않게 한다:
      `mimic_iv` **22** · `stock_forecast` **10** (합 **32**) · `eicu` **0**.
      이는 *`models/<dataset>/**/*.sql` 파일 수*이고, Cosmos가 전개한 태스크 수와
      **갈리면 셀렉터가 깨진 것**이다.
    · 태스크 수준 정합(의존 순서·파티션)은 데이터셋별 이관 단계가 진다.
"""

import tempfile
from pathlib import Path

from airflow.dag_processing.dagbag import DagBag

# DAG 배선 파일이 사는 곳. 🔴 `src/`와 **형제**다 — dag-processor가 주기적으로 전체를
# 재파싱하므로 로직을 여기 두면 **파싱이 곧 실행 경로의 import**가 된다.
DAGS_DIR = Path(__file__).resolve().parents[1] / "dags"

# 🔴 *DagBag에 로드된 `dag_id` 개수*. 파일 수가 아니다(위 §계측 단위).
#    P0 시점 0 — 단계 2에서 첫 DAG과 함께 올린다. 0인 동안 이 셀은 공허하게 참이다.
EXPECTED_DAG_COUNT = 0

# 단계 11에서 쓸 기대값. *`models/<dataset>/**/*.sql` 파일 수*이고 Cosmos가 전개한
# 태스크 수와 같아야 한다 — 갈리면 셀렉터가 깨진 것이다(`eicu`의 실물 전례 참조).
EXPECTED_DBT_TASKS = {"mimic_iv": 22, "stock_forecast": 10, "eicu": 0}


def _bag(folder: Path) -> DagBag:
    """DagBag을 만든다 — 🔴 **`safe_mode=False`가 이 함수의 요점**(위 §safe_mode).

    경로는 디렉터리든 **단일 파일**이든 받는다(실측 확인). 후자를 쓰는 셀이
    「이 파일이 DAG을 내는가」를 판정한다 — `DAG.fileloc`이 사라져 대체한 수단이다.
    """
    return DagBag(dag_folder=str(folder), safe_mode=False)


def test_dagbag_has_no_import_errors() -> None:
    """DAG 파일이 전부 임포트된다.

    🔴 **로드된 DAG 수를 같은 단정에 끼워 넣는다.** "에러 0건"만으로는 *검사했다*인지
    *아무것도 로드하지 않았다*인지 갈리지 않는다(원칙 7 — 부정 결과에는 관측 경로
    생존을 함께 제시한다).
    """
    bag = _bag(DAGS_DIR)
    assert bag.import_errors == {}, (
        f"임포트 에러 {len(bag.import_errors)}건: {bag.import_errors}"
    )
    loaded = sorted(bag.dag_ids)
    assert len(loaded) == len(set(loaded)), f"dag_id 중복: {loaded}"


def test_every_dag_file_defines_at_least_one_dag() -> None:
    """🔴 **핵심 셀** — 「파일은 있는데 DAG 0개」를 잡는다.

    `eicu_dbt_models`가 `select="fqn:eicu"`로 선언됐는데 모델이 0개인 **실물 전례**가
    근거다. 그쪽은 로드가 성공하므로 `dg check defs`가 통과시켰고, 아무도 세지 않아
    지금도 에러 없이 산다.

    ⚠️ 파일 수와 DAG 수는 **다른 단위**다(한 파일이 여러 DAG을 낼 수 있다). 그래서
    「같다」가 아니라 **「DAG을 하나도 안 내는 파일이 없다」**를 단정한다.
    """
    files = sorted(p for p in DAGS_DIR.rglob("*.py") if not p.name.startswith("_"))
    barren = [
        str(path.relative_to(DAGS_DIR)) for path in files if not _bag(path).dag_ids
    ]
    assert not barren, (
        f"DAG을 정의하지 않는 배선 파일 {len(barren)}건: {barren} "
        f"(검사한 파일 {len(files)}개)"
    )


def test_expected_dag_count_is_current() -> None:
    """기대값이 실태와 일치한다 — DAG이 **조용히 사라지는** 축.

    ⚠️ `EXPECTED_DAG_COUNT`가 0인 동안 공허하게 참이다. 단계 2에서 첫 DAG과 함께
    올리고, 그 갱신을 단계 2의 DoD에 넣는다.
    """
    bag = _bag(DAGS_DIR)
    assert len(bag.dag_ids) == EXPECTED_DAG_COUNT, (
        f"로드된 DAG {len(bag.dag_ids)}개(기대 {EXPECTED_DAG_COUNT}) — "
        f"{sorted(bag.dag_ids)}. DAG을 추가·삭제했으면 "
        "`EXPECTED_DAG_COUNT`를 함께 고친다"
    )


def test_broken_file_is_caught_without_heuristic_words() -> None:
    """🔴 **`safe_mode=False`를 고정하는 셀** — 이 파일에서 유일하게 지금도 실효다.

    `airflow`·`dag` 문자열이 **없는** 깨진 파일을 만들어 잡히는지 본다.
    `safe_mode`가 기본값(`True`)으로 돌아가면 Airflow가 그 파일을 **import조차 하지
    않아** `import_errors`가 0건이 되고, 이 셀이 **빨개진다**.

    ⚠️ 대조군을 함께 둔다 — 깨지지 않은(단지 DAG이 없는) 파일은 **에러가 아니다.**
    둘을 같은 셀에서 보지 않으면 「전부 에러로 잡는다」와 구분되지 않는다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # 🔴 `airflow`·`dag` 어느 문자열도 포함하지 않는다 — 그것이 이 셀의 요점이다.
        (root / "broken.py").write_text(
            "import nonexistent_module_for_gate_probe\n", encoding="utf-8"
        )
        (root / "barren.py").write_text("CONSTANT = 1\n", encoding="utf-8")

        bag = _bag(root)
        names = {Path(path).name for path in bag.import_errors}
        assert "broken.py" in names, (
            "깨진 파일이 잡히지 않았다 — `safe_mode=False`가 풀렸는지 확인하라. "
            f"import_errors={bag.import_errors}"
        )
        # 대조군: 멀쩡하지만 DAG이 없는 파일은 **에러가 아니다**(위 핵심 셀의 소관).
        assert "barren.py" not in names, (
            f"DAG 없는 파일을 에러로 잡았다 — 두 축이 섞였다. names={names}"
        )
