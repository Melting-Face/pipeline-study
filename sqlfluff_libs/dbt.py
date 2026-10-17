"""sqlfluff jinja templater용 dbt 내장 매크로 셰임.

dbt 내장 크로스 어댑터 매크로(`{{ dbt.dateadd(...) }}`)는 dbt 런타임이 주입하는
객체라 jinja templater는 알지 못한다(미설정 시 `TMP: Undefined jinja template
variable: 'dbt'`). 린트 목적에 한해 파싱 가능한 SQL 표현식으로 치환한다.

🔴 이 파일은 **런타임 경로가 아니다** — 실제 컴파일은 dbt가 수행하고, 여기서 뱉는
문자열은 sqlfluff 파서에만 전달된다. 그래서 **의미론이 아니라 파싱 가능성만** 맞으면
된다. 반환 형태는 선언된 dialect(`trino`)에 맞춘다.
상세 근거는 루트 pyproject.toml `[tool.sqlfluff.core]` 주석 참고.

⚠️ 스텁이 **의미론을 재현하지 않는다**는 점이 특히 중요한 사례가 `dateadd`다 —
Spark 구현은 초 이하가 절삭되고 세션 타임존에 의존하는 반면 Trino는 그렇지 않다
(2026-08-21 dbt-spark 이행 미션 발견). 즉 `dbt.dateadd`는 `dbt.datediff`와
**같은 계열의 엔진 간 값 차이**를 갖는다. 이 파일은 그 차이를 재현하지 않으며,
재현할 필요도 없다 — **값 정합은 린터의 관할이 아니다**.

디렉터리에 `__init__.py`를 두지 **않는다** — sqlfluff는 `__init__.py`가 없으면
디렉터리의 각 `.py`를 개별 모듈로 로드하므로, 파일명 `dbt.py`가 곧 jinja
네임스페이스 `dbt`가 된다.
"""


def dateadd(datepart: str, interval: int, from_date_or_timestamp: str) -> str:
    """`dbt.dateadd`를 파싱 가능한 날짜 가감 표현식으로 치환한다.

    Args:
        datepart: 시간 단위 리터럴('day'·'hour' 등).
        interval: 가감할 정수(음수 허용).
        from_date_or_timestamp: 대상 컬럼식.

    Returns:
        `date_add(...)` 표현식 문자열.
    """
    return f"date_add('{datepart}', {interval}, {from_date_or_timestamp})"


def datediff(first_date: str, second_date: str, datepart: str) -> str:
    """`dbt.datediff`를 파싱 가능한 경과 표현식으로 치환한다.

    프로젝트 규약상 모델에서 `dbt.datediff`는 **쓰지 않는다**(엔진별 의미론이 갈려
    `macros/cross_engine.sql`의 `elapsed`로 대체). 그럼에도 셰임을 두는 이유는,
    누가 실수로 쓰면 `TMP` 파싱 오류가 아니라 **규약 위반으로 드러나야** 하기 때문이다.

    Args:
        first_date: 시작 컬럼식.
        second_date: 종료 컬럼식.
        datepart: 시간 단위 리터럴.

    Returns:
        `date_diff(...)` 표현식 문자열.
    """
    return f"date_diff('{datepart}', {first_date}, {second_date})"
