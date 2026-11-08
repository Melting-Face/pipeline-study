{{ config(materialized='table', tags=['gold', 'alerts']) }}

-- 1행 = (topic_id, cve_id). GeekNews 글이 본문에서 언급한 CVE와 그 NVD 요약.
-- 정의의 정본은 schema.yml description이다(analysis.md §2).
--
-- ⚠️ cve_ids가 빈 글은 행이 없다(의도) — 글 수는 이 모델이 아니라 silver에서 센다.
-- ⚠️ in_nvd = digest(Rejected 제외)에 있음. 아직 NVD에 없거나 Rejected인 CVE는 false이고
--    그때 cvss_*·is_kev는 NULL이다(「KEV 아님」이 아니라 「모름」).
-- 🔴 관심목록(watchlist) 필터는 두지 않는다 — 발신층 Python이 건다(nvd__cve_digest 주석).

-- 🔴 `with` CTE로 시작한다 — unit_test 픽스처 CTE가 첫 `with` 토큰에 끼워진다(nvd_cve_changes 주석).
with mentions as (
    select distinct
        i.topic_id,
        i.title,
        i.link,
        i.published,
        i.fetched_at,
        upper(c.cve_id_raw) as cve_id
    from {{ ref('geeknews_items_latest') }} as i
    {{ unnest_array('i.cve_ids', 'c', 'cve_id_raw') }}
)

select
    m.topic_id,
    m.title,
    m.link,
    m.published,
    m.fetched_at,
    m.cve_id,
    d.cvss_score,
    d.cvss_severity,
    d.is_kev,
    d.cve_id is not null as in_nvd
from mentions as m
left join {{ ref('nvd__cve_digest') }} as d
    on m.cve_id = d.cve_id
