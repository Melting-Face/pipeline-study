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


{#-
    배열을 행으로 펼치면서 **1부터 세는 위치**를 함께 낸다. 원소에 자연 키가 없는 중첩 배열
    (NVD `configurations[].nodes[].cpeMatch[]`·`details[]`)의 행 키를 만드는 데 쓴다.
    `unnest_array`와 같은 1필드 ROW 감싸기(위 주석)를 따른다.
    🔴 `spark__`를 두지 않는다 — 아래 json_* 주석과 같은 이유.
-#}

{% macro unnest_array_ordinal(array_expr, table_alias, column_alias, ordinal_alias) -%}
    {%- set impl = adapter.dispatch('unnest_array_ordinal', 'dbt_pipelines') -%}
    {{ return(impl(array_expr, table_alias, column_alias, ordinal_alias)) }}
{%- endmacro %}

{% macro default__unnest_array_ordinal(array_expr, table_alias, column_alias, ordinal_alias) -%}
    {{ exceptions.raise_compiler_error(
        "unnest_array_ordinal: 이 어댑터용 구현이 없다 — macros/cross_engine.sql에 추가 필요"
    ) }}
{%- endmacro %}

{% macro trino__unnest_array_ordinal(array_expr, table_alias, column_alias, ordinal_alias) -%}
    cross join unnest(transform({{ array_expr }}, e -> row(e))) with ordinality
        as {{ table_alias }} ({{ column_alias }}, {{ ordinal_alias }})
{%- endmacro %}


{#-
    JSON 문자열(또는 JSON 값)에서 경로의 값을 꺼낸다. bronze가 원본을 `raw_json` 문자열로만
    두므로(NVD) 펼침은 전부 이 두 매크로를 거친다.
      - json_value_at : 스칼라 → varchar. 경로가 없거나 값이 스칼라가 아니면 NULL.
      - json_array_at : 배열 → 원소가 JSON인 배열. 경로가 없으면 NULL(→ unnest 0행).
    🔴 `spark__`를 두지 않는다 — dbt-spark가 철거돼 실행해 볼 수 없는 구현이다. Spark가
    돌아오면 그때 쓰고, 그 전에는 `default__`가 컴파일 단계에서 시끄럽게 멈춘다.
-#}

{% macro json_value_at(json_expr, path) -%}
    {{ return(adapter.dispatch('json_value_at', 'dbt_pipelines')(json_expr, path)) }}
{%- endmacro %}

{% macro default__json_value_at(json_expr, path) -%}
    {{ exceptions.raise_compiler_error(
        "json_value_at: 이 어댑터용 구현이 없다 — macros/cross_engine.sql에 추가 필요"
    ) }}
{%- endmacro %}

{% macro trino__json_value_at(json_expr, path) -%}
    json_extract_scalar({{ json_expr }}, '{{ path }}')
{%- endmacro %}

{% macro json_array_at(json_expr, path) -%}
    {{ return(adapter.dispatch('json_array_at', 'dbt_pipelines')(json_expr, path)) }}
{%- endmacro %}

{% macro default__json_array_at(json_expr, path) -%}
    {{ exceptions.raise_compiler_error(
        "json_array_at: 이 어댑터용 구현이 없다 — macros/cross_engine.sql에 추가 필요"
    ) }}
{%- endmacro %}

{% macro trino__json_array_at(json_expr, path) -%}
    cast(json_extract({{ json_expr }}, '{{ path }}') as array(json))
{%- endmacro %}
