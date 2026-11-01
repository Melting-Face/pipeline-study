{{ config(tags=['silver']) }}

-- 1행 = CVE. bronze `nvd_cves`의 버전 행들 중 **현재 버전 1행**과 그 스칼라.
--
-- 🔴 최신은 `last_modified`로 고른다(window_date가 아니다). 백필 파티션은 각 CVE의
-- 「받은 날의 현재 버전」을 담아, 늦게 받은 옛 날짜 파티션이 더 새 버전일 수 있다.
-- 같은 last_modified가 두 파티션에 있으면 window_date가 큰 쪽(나중 조회)을 고른다 —
-- 그래도 둘이면 `row_number`가 임의로 하나를 고르므로 bronze 계약(파티션 안 cve_id 유일)이
-- 받친다.
--
-- ⚠️ Rejected는 지우지 않고 `is_rejected`로 둔다 — 제외는 질문마다 다른 코호트 조건이라
-- gold가 attrition과 함께 건다(docs/conventions/analysis.md).
-- ⚠️ 시각은 bronze의 UTC 해석(미확인 가정)을 그대로 물려받는다(docs/dataset_schema/nvd.md §함정).

with ranked as (
    select
        cve_id,
        published,
        last_modified,
        vuln_status,
        raw_json,
        window_date,
        fetched_at,
        row_number() over (
            partition by cve_id
            order by last_modified desc, window_date desc
        ) as version_rank
    from {{ source('nvd', 'nvd_cves') }}
),

latest as (
    select * from ranked
    where version_rank = 1
),

-- 영어 설명. NVD는 언어마다 설명 1개를 둔다 — 같은 언어가 둘이면 min이 하나를 고른다.
-- 🔴 unnest는 배열이 없으면 행을 지우므로 latest에 left join한다(설명 없는 CVE 보존).
description_en as (
    select
        l.cve_id,
        min({{ json_value_at('d.description', '$.value') }}) as description_en
    from latest as l
    {{ unnest_array(json_array_at('l.raw_json', '$.descriptions'), 'd', 'description') }}
    where {{ json_value_at('d.description', '$.lang') }} = 'en'
    group by l.cve_id
)

select
    l.cve_id,
    {{ json_value_at('l.raw_json', '$.sourceIdentifier') }} as source_identifier,
    l.published,
    l.last_modified,
    l.vuln_status,
    coalesce(l.vuln_status = 'Rejected', false) as is_rejected,
    d.description_en,
    l.window_date as version_window_date,
    l.fetched_at,
    -- 브리지 모델(metrics·cwe·cpe)이 같은 최신 버전에서 펼치도록 원문을 넘긴다
    l.raw_json
from latest as l
left join description_en as d
    on l.cve_id = d.cve_id
