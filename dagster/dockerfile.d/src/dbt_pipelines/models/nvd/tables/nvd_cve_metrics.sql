{{ config(tags=['silver']) }}

-- 1행 = (CVE, CVSS 버전, 채점 출처, 출처 종류). 최신 버전의 `metrics` 객체를 손실 없이 펼친다.
--
-- 🔴 「대표 점수」를 여기서 고르지 않는다. 한 CVE에 v2·v3.0·v3.1·v4.0이 함께 있고 각각
-- Primary(NVD)·Secondary(CNA) 점수가 따로 있다. 어느 것을 쓸지는 질문마다 다르므로 gold가 정한다.
--
-- ⚠️ 버전별 위치 차이: v3.x·v4.0은 심각도가 `cvssData.baseSeverity`에, v2는 원소 바로 아래
-- `baseSeverity`에 있다 — coalesce로 둘 다 본다. v4.0에는 exploitability·impact 점수가 없다(NULL).
-- ⚠️ 경로는 NVD CVE API 2.0 스키마 기준이고 실데이터로는 아직 대조하지 않았다(PR 본문).

{%- set metric_keys = [
    ('cvssMetricV40', '4.0'),
    ('cvssMetricV31', '3.1'),
    ('cvssMetricV30', '3.0'),
    ('cvssMetricV2', '2.0'),
] %}

with unioned as (
    {% for metric_key, cvss_version in metric_keys %}
        select
            l.cve_id,
            '{{ cvss_version }}' as cvss_version,
            {{ json_value_at('m.metric', '$.source') }} as source,
            {{ json_value_at('m.metric', '$.type') }} as source_type,
            {{ json_value_at('m.metric', '$.cvssData.vectorString') }} as vector_string,
            {{ json_value_at('m.metric', '$.cvssData.baseScore') }} as base_score,
            coalesce(
                {{ json_value_at('m.metric', '$.cvssData.baseSeverity') }},
                {{ json_value_at('m.metric', '$.baseSeverity') }}
            ) as base_severity,
            {{ json_value_at('m.metric', '$.exploitabilityScore') }} as exploitability_score,
            {{ json_value_at('m.metric', '$.impactScore') }} as impact_score
        from {{ ref('nvd_cve_latest') }} as l
        {{ unnest_array(json_array_at('l.raw_json', '$.metrics.' ~ metric_key), 'm', 'metric') }}
        {% if not loop.last %}union all{% endif %}
    {% endfor %}
)

select
    cve_id,
    cvss_version,
    source,
    source_type,
    vector_string,
    cast(base_score as double) as base_score,
    base_severity,
    cast(exploitability_score as double) as exploitability_score,
    cast(impact_score as double) as impact_score
from unioned
