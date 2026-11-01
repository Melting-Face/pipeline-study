{{ config(tags=['silver']) }}

-- 1행 = (변경 이벤트, detail). bronze `nvd_cve_history`의 `details[]`를 펼친 변경 이력.
--
-- 🔴 이력의 정본은 이 모델이다. CVE 피드(`nvd_cves`)는 각 CVE의 「받은 날의 현재 버전」만 주므로
-- 버전 행을 diff해 만든 이력은 증분 시작 이후에만 존재해 과거가 빈다(그래서 SCD2를 두지 않는다).
-- ⚠️ details가 빈 이벤트는 행이 없다 — 이벤트 수는 이 모델이 아니라 bronze에서 센다.
-- ⚠️ 시각은 bronze의 UTC 해석(미확인 가정)을 그대로 물려받는다(docs/dataset_schema/nvd.md §함정).
-- ⚠️ 경로는 NVD CVE Change History API 2.0 스키마 기준이고 실데이터로는 아직 대조하지 않았다.

-- 🔴 `with` CTE로 시작해야 한다. dbt는 ephemeral·unit_test 픽스처 CTE를 SQL의 **첫 `with` 토큰**에
-- 끼워 넣는데, CTE 없이 시작하면 `unnest ... with ordinality`의 `with`를 잡아 SYNTAX_ERROR가 난다
-- (unit_test 실측). 같은 매크로를 쓰는 nvd_cve_cpe는 CTE로 시작해 통과했다.
with history as (
    select * from {{ source('nvd', 'nvd_cve_history') }}
)

select
    h.cve_change_id,
    h.cve_id,
    h.event_name,
    h.created,
    {{ json_value_at('h.raw_json', '$.sourceIdentifier') }} as source_identifier,
    d.detail_idx,
    {{ json_value_at('d.detail', '$.action') }} as action,
    {{ json_value_at('d.detail', '$.type') }} as detail_type,
    {{ json_value_at('d.detail', '$.oldValue') }} as old_value,
    {{ json_value_at('d.detail', '$.newValue') }} as new_value,
    h.window_date
from history as h
{{ unnest_array_ordinal(json_array_at('h.raw_json', '$.details'), 'd', 'detail', 'detail_idx') }}
