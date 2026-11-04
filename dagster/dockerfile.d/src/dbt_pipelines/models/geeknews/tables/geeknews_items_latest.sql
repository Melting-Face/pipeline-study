{{ config(tags=['silver']) }}

with ranked as (
    select
        topic_id,
        title,
        link,
        published,
        updated,
        cve_ids,
        fetched_at,
        row_number() over (
            partition by topic_id
            order by fetched_at desc, updated desc
        ) as fetch_rank
    from {{ source('geeknews', 'geeknews_items') }}
)

select
    topic_id,
    title,
    link,
    published,
    updated,
    cve_ids,
    fetched_at
from ranked
where fetch_rank = 1
