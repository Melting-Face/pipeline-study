{{ config(tags=['silver']) }}

-- 1행 = (CVE, 출처, CWE). 최신 버전의 `weaknesses[].description[]`을 펼친 1:N 브리지.
--
-- 🔴 CVE마다 대표 CWE 하나로 줄이지 않는다 — 다중 CWE를 그대로 세야 연도별 CWE 추세가
-- 맞는다. `NVD-CWE-Other`·`NVD-CWE-noinfo`도 값 그대로 둔다(제외는 gold).
-- ⚠️ `distinct`는 같은 (출처, 종류, CWE)의 반복만 접는다. 같은 출처가 같은 CWE를 종류만
-- 달리 두 번 주면 접히지 않고 unique_combination 테스트가 드러낸다.
-- ⚠️ 경로는 NVD CVE API 2.0 스키마 기준이고 실데이터로는 아직 대조하지 않았다(PR 본문).

select distinct
    l.cve_id,
    {{ json_value_at('w.weakness', '$.source') }} as source,
    {{ json_value_at('w.weakness', '$.type') }} as source_type,
    {{ json_value_at('d.description', '$.value') }} as cwe_id
from {{ ref('nvd_cve_latest') }} as l
{{ unnest_array(json_array_at('l.raw_json', '$.weaknesses'), 'w', 'weakness') }}
{{ unnest_array(json_array_at('w.weakness', '$.description'), 'd', 'description') }}
where {{ json_value_at('d.description', '$.lang') }} = 'en'
