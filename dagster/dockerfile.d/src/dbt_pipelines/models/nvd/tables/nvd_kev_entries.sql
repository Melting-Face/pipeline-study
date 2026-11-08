{{ config(tags=['silver', 'alerts']) }}

-- 1행 = CVE. bronze `nvd_kev`(CISA KEV 최신 스냅샷)의 스칼라에 raw_json의 식별 필드를 붙인다.
--
-- 🔴 gold가 source를 바로 읽지 않게 두는 silver다(docs/conventions/analysis.md §2 —
-- silver를 건너뛰고 source에서 gold를 만들지 않는다).
-- 🔴 이름이 bronze `nvd_kev`와 겹치면 안 된다 — 같은 스키마 `nvd`에서 replace가 bronze를
-- 덮는다(dbt_project.yml nvd 주석).
-- ⚠️ cve_id 유일성은 bronze 파서(`nvd_parse.kev_to_arrow`)가 중복이면 적재를 거부해 받친다.
-- ⚠️ requiredAction·shortDescription·notes는 옮기지 않는다 — 알림에 쓰지 않는 서술 필드다.

select
    cve_id,
    date_added,
    due_date,
    known_ransomware_campaign_use,
    {{ json_value_at('raw_json', '$.vendorProject') }} as vendor_project,
    {{ json_value_at('raw_json', '$.product') }} as product,
    {{ json_value_at('raw_json', '$.vulnerabilityName') }} as vulnerability_name,
    catalog_version,
    fetched_at
from {{ source('nvd', 'nvd_kev') }}
