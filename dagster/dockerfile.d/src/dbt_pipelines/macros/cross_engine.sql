{#-
    엔진별 SQL 방언 차이를 흡수하는 dispatch 매크로.

    왜 dbt 내장 크로스 어댑터 매크로(`dbt.datediff`)를 쓰지 않는가:
        어댑터 구현의 **의미론이 다르다**(2026-08-19 설치본 소스 실측).
          - Trino 네이티브 `date_diff('hour', a, b)` = 경계교차(Joda field difference)
          - `trino__datediff(...,'hour')`  = day*24 + hour(b) - hour(a)  → 네이티브와 동일
          - `spark__datediff(...,'hour')`  = ceil((unix(b)-unix(a))/3600) → 경과시간 올림
        예) 11:00 → 12:59 은 경계교차 1, ceil 2.
        `ventilation`의 `>= 14`, `urine_output_rate`의 `<= 5`처럼 **임계값 비교**에 쓰이므로
        엔진마다 값이 갈리면 silver 결과가 달라진다. 이행의 목표는 "도는 것"이 아니라 **같은 값**이다.
        → **Trino 네이티브를 정본**으로 두고 Spark에 같은 수식을 재현한다.
-#}

{% macro elapsed(datepart, from_ts, to_ts) -%}
    {{ return(adapter.dispatch('elapsed', 'dbt_pipelines')(datepart, from_ts, to_ts)) }}
{%- endmacro %}

{% macro default__elapsed(datepart, from_ts, to_ts) -%}
    {{ exceptions.raise_compiler_error(
        "elapsed: 이 어댑터용 구현이 없다 — macros/cross_engine.sql에 <adapter>__elapsed 추가 필요"
    ) }}
{%- endmacro %}

{% macro trino__elapsed(datepart, from_ts, to_ts) -%}
    date_diff('{{ datepart }}', {{ from_ts }}, {{ to_ts }})
{%- endmacro %}

{% macro spark__elapsed(datepart, from_ts, to_ts) -%}
    {#- Trino 경계교차 수식을 그대로 옮긴다. Spark `datediff(end, start)`는 날짜 차(일)라
        Trino `datediff(..., 'day')`(date 캐스트 차)와 같다. -#}
    {%- set hour_diff -%}
        (datediff(to_date({{ to_ts }}), to_date({{ from_ts }})) * 24
            + hour({{ to_ts }}) - hour({{ from_ts }}))
    {%- endset -%}
    {%- if datepart == 'hour' -%}
        {{ hour_diff }}
    {%- elif datepart == 'minute' -%}
        ({{ hour_diff }} * 60 + minute({{ to_ts }}) - minute({{ from_ts }}))
    {%- else -%}
        {{ exceptions.raise_compiler_error(
            "spark__elapsed: 지원하지 않는 단위 '" ~ datepart ~ "' (hour·minute만 구현)"
        ) }}
    {%- endif -%}
{%- endmacro %}


{#-
    배열을 행으로 펼친다. 크로스 어댑터 내장 매크로가 **없는** 영역이다.
      - Trino: CROSS JOIN UNNEST(arr) AS t (col)
      - Spark: LATERAL VIEW explode(arr) t AS col
    `from` 절 뒤에 그대로 삽입해 쓴다.
-#}

{% macro unnest_array(array_expr, table_alias, column_alias) -%}
    {%- set impl = adapter.dispatch('unnest_array', 'dbt_pipelines') -%}
    {{ return(impl(array_expr, table_alias, column_alias)) }}
{%- endmacro %}

{% macro default__unnest_array(array_expr, table_alias, column_alias) -%}
    {{ exceptions.raise_compiler_error(
        "unnest_array: 이 어댑터용 구현이 없다 — macros/cross_engine.sql에 추가 필요"
    ) }}
{%- endmacro %}

{% macro trino__unnest_array(array_expr, table_alias, column_alias) -%}
    {#- 🔴 원소를 1필드 ROW로 감싼다. Trino는 ROW 배열을 **필드 수만큼의 컬럼**으로 펼쳐서
        별칭 1개로는 `MISMATCHED_COLUMN_ALIASES`로 죽는다(483 실측 — `insights`가 ROW 배열).
        감싸면 별칭 1개가 원소 전체(ROW)를 받아 Spark `explode`와 같은 모양이 된다.
        스칼라·`sequence`·빈 배열·NULL 배열에서는 감싸기 전과 값·타입이 같다(483 실측). -#}
    cross join unnest(transform({{ array_expr }}, e -> row(e))) as {{ table_alias }} ({{ column_alias }})
{%- endmacro %}

{% macro spark__unnest_array(array_expr, table_alias, column_alias) -%}
    lateral view explode({{ array_expr }}) {{ table_alias }} as {{ column_alias }}
{%- endmacro %}
