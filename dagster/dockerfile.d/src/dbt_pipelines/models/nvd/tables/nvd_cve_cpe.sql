{{ config(tags=['silver']) }}

-- 1행 = (CVE, configuration, node, cpeMatch). 최신 버전의 적용 대상 조건을 펼친다.
-- 행 키는 세 배열의 위치(1부터)다 — cpeMatch에는 원소를 가르는 자연 키가 없다
-- (같은 matchCriteriaId가 configuration을 달리해 다시 나온다).
--
-- 🔴 vendor·product는 CPE 2.3 형식 문자열에서 **이스케이프를 인지해** 뽑는다. 값 안의 콜론은
-- `\:`로 이스케이프되므로 단순 `:` 분할은 그 자리에서 값을 조용히 자른다 — 미션 ③의 스택
-- 매칭(vendor:product)이 이 값에 직접 기대므로 잘린 값이 가장 위험하다. 뽑은 뒤 이스케이프
-- (`\x` → `x`)를 푼다.
-- ⚠️ 이 행들은 「영향받는 버전 범위」 조건이지 영향받는 제품 목록이 아니다 — operator(AND/OR)·
-- negate·vulnerable을 함께 봐야 뜻이 선다(vulnerable=false는 플랫폼 조건). 해석은 gold.
-- ⚠️ 경로는 NVD CVE API 2.0 스키마 기준이고 실데이터로는 아직 대조하지 않았다(PR 본문).

{%- set cpe_pattern = '^cpe:2\\.3:([aho]):((?:[^:\\\\]|\\\\.)*):((?:[^:\\\\]|\\\\.)*):' %}

with matches as (
    select
        l.cve_id,
        c.config_idx,
        {{ json_value_at('c.config', '$.operator') }} as config_operator,
        n.node_idx,
        {{ json_value_at('n.node', '$.operator') }} as node_operator,
        cast({{ json_value_at('n.node', '$.negate') }} as boolean) as node_negate,
        m.match_idx,
        cast({{ json_value_at('m.cpe_match', '$.vulnerable') }} as boolean) as vulnerable,
        {{ json_value_at('m.cpe_match', '$.criteria') }} as criteria,
        {{ json_value_at('m.cpe_match', '$.matchCriteriaId') }} as match_criteria_id,
        {{ json_value_at('m.cpe_match', '$.versionStartIncluding') }} as version_start_including,
        {{ json_value_at('m.cpe_match', '$.versionStartExcluding') }} as version_start_excluding,
        {{ json_value_at('m.cpe_match', '$.versionEndIncluding') }} as version_end_including,
        {{ json_value_at('m.cpe_match', '$.versionEndExcluding') }} as version_end_excluding
    from {{ ref('nvd_cve_latest') }} as l
    {{ unnest_array_ordinal(
        json_array_at('l.raw_json', '$.configurations'), 'c', 'config', 'config_idx'
    ) }}
    {{ unnest_array_ordinal(json_array_at('c.config', '$.nodes'), 'n', 'node', 'node_idx') }}
    {{ unnest_array_ordinal(
        json_array_at('n.node', '$.cpeMatch'), 'm', 'cpe_match', 'match_idx'
    ) }}
)

select
    cve_id,
    config_idx,
    config_operator,
    node_idx,
    node_operator,
    node_negate,
    match_idx,
    vulnerable,
    criteria,
    match_criteria_id,
    regexp_extract(criteria, '{{ cpe_pattern }}', 1) as part,
    regexp_replace(regexp_extract(criteria, '{{ cpe_pattern }}', 2), '\\(.)', '$1') as vendor,
    regexp_replace(regexp_extract(criteria, '{{ cpe_pattern }}', 3), '\\(.)', '$1') as product,
    version_start_including,
    version_start_excluding,
    version_end_including,
    version_end_excluding
from matches
