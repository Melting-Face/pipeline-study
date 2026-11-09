{{ config(materialized='table', tags=['gold', 'alerts']) }}

-- 1행 = CVE (Rejected 제외). 알림이 읽는 CVE 요약 — 대표 CVSS·KEV 여부·영향 vendor:product.
-- 정의(제외 조건·대표 점수 규칙)의 정본은 schema.yml description이다(analysis.md §2).
--
-- 제외 조건: `nvd_cve_latest.is_rejected` = true (현재 버전 vuln_status = 'Rejected').
-- 🔴 NVD `description_en`을 선택하지 않는다 — 재배포 가능 여부가 미확인이다
-- (docs/security/datasets.md). 설명이 필요하면 발신층이 NVD 링크로 보낸다.
-- 🔴 관심목록(watchlist) 필터를 여기 두지 않는다 — 비공개 입력 없이 재현돼야 하는 gold라서다.
--    필터는 발신층 Python이 cpe_vendor_products에 건다.

with latest as (
    select
        cve_id,
        published,
        last_modified
    from {{ ref('nvd_cve_latest') }}
    where not is_rejected
),

-- 대표 CVSS: 버전 4.0 > 3.1 > 3.0 > 2.0, 같은 버전이면 Primary > Secondary,
-- 그다음 base_score 큰 쪽. 그래도 같으면 source 사전순으로 결정적으로 고른다.
ranked_metrics as (
    select
        cve_id,
        cvss_version,
        base_score,
        base_severity,
        row_number() over (
            partition by cve_id
            order by
                case cvss_version
                    when '4.0' then 1
                    when '3.1' then 2
                    when '3.0' then 3
                    when '2.0' then 4
                    else 5
                end asc,
                case source_type
                    when 'Primary' then 1
                    when 'Secondary' then 2
                    else 3
                end asc,
                base_score desc,
                source asc
        ) as metric_rank
    from {{ ref('nvd_cve_metrics') }}
),

primary_metric as (
    select
        cve_id,
        cvss_version,
        base_score,
        base_severity
    from ranked_metrics
    where metric_rank = 1
),

-- 영향 대상(vulnerable=true)의 'vendor:product' distinct 정렬 배열. 없으면 빈 배열.
-- 🔴 latest에서 left join해 group by한다 — CPE가 없는 CVE도 1행을 갖게 하고,
--    array_agg가 모은 NULL(조인 실패)은 filter로 걸러 빈 배열로 만든다.
cpe_products as (
    select
        l.cve_id,
        array_sort(
            array_distinct(
                filter(array_agg(c.vendor || ':' || c.product), x -> x is not null)
            )
        ) as cpe_vendor_products
    from latest as l
    left join {{ ref('nvd_cve_cpe') }} as c
        on l.cve_id = c.cve_id and c.vulnerable
    group by l.cve_id
)

select
    l.cve_id,
    l.published,
    l.last_modified,
    m.cvss_version,
    m.base_score as cvss_score,
    m.base_severity as cvss_severity,
    k.date_added as kev_date_added,
    k.vendor_project as kev_vendor_project,
    k.product as kev_product,
    p.cpe_vendor_products,
    k.cve_id is not null as is_kev
from latest as l
left join primary_metric as m
    on l.cve_id = m.cve_id
left join {{ ref('nvd_kev_entries') }} as k
    on l.cve_id = k.cve_id
left join cpe_products as p
    on l.cve_id = p.cve_id
