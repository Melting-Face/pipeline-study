"""NVD·CISA KEV 원천 획득 에셋 (공개 HTTP API → S3 raw/).

`assets.py`의 **앞 단계**다 — API 응답을 **받은 바이트 그대로** S3 `raw/`에 스냅샷으로
남기고, `assets.py`가 그것을 읽어 Iceberg bronze로 올린다.

🔴 **왜 API에서 bronze로 바로 가지 않는가.** NVD 일별 구간은 다시 물으면 답이 바뀐다 —
그 사이 다시 수정된 CVE는 `lastModified`가 다른 날로 옮겨간다. 과거 파티션의
"그때 받은 것"은 **재현할 수 없는 관측**이라, 파서를 고쳐 bronze를 다시 만들 때
API를 다시 부르면 다른 데이터가 된다. raw가 그 관측을 보존한다 — 그래서 수집은
**회차(`fetched=`)마다 경로가 갈리고 어떤 실행도 기존 객체를 덮어쓰지 않는다**
(`common/nvd.py` 독스트링).

피드마다 **개별 명시적 자산**이다(프로젝트 컨벤션). 파티션 정의는 bronze 자산이
같은 객체를 쓰도록 여기서 정의하고 `assets.py`가 import한다.

⚠️ **`kinds`에 `bronze`를 넣지 않는다** — Iceberg 테이블을 만들지 않는 pre-bronze
랜딩이다(eicu/raw_assets.py와 같은 규칙).

주의: Dagster context 클래스 identity 검사 때문에 자산 모듈에서는
`from __future__ import annotations`를 사용하지 않는다.
"""

import json
from datetime import datetime, timezone

from dagster_aws.s3 import S3Resource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.fetch import RawFetchConfig, put_gzip_with_sidecar
from dagster_project.common.helper import fetch_bytes, parse_s3_uri
from dagster_project.common.nvd import (
    NVD_API_POOL,
    NvdResource,
    assert_new_run_prefix,
    land_window_pages,
    latest_committed_run,
    run_segment,
)
from dagster_project.defs.nvd.constants import (
    CVES_END_PARAM,
    CVES_FEED,
    CVES_ITEM_KEY,
    CVES_ITEMS_KEY,
    CVES_PAGE_SIZE,
    CVES_PARTITION_START_DATE,
    CVES_PATH,
    CVES_START_PARAM,
    GROUP_NAME,
    HISTORY_END_PARAM,
    HISTORY_FEED,
    HISTORY_ITEM_KEY,
    HISTORY_ITEMS_KEY,
    HISTORY_PAGE_SIZE,
    HISTORY_PARTITION_START_DATE,
    HISTORY_PATH,
    HISTORY_START_PARAM,
    KEV_FEED,
    KEV_FILE_NAME,
    KEV_RETRIES,
    KEV_TIMEOUT_S,
    KEV_URL,
    PARTITION_TIMEZONE,
    SOURCE_BASE,
)

# 수집 자산은 `bronze`가 아니라 랜딩이므로 kind를 따로 둔다.
RAW_KINDS = {"python", "s3"}

# 일자 파티션 — 시작일 리터럴·타임존 UTC의 근거는 constants.py 주석.
# 🔴 전체 백필은 별도 잡 없이 **이 파티션의 백필**로 한다. 하루 구간의 합집합이 전체와
#    같다는 것은 프로브 전구간 스캔(lastMod 120일 창 합계 = 전체 건수)이 근거다.
CVES_PARTITIONS = dg.DailyPartitionsDefinition(
    start_date=CVES_PARTITION_START_DATE,
    timezone=PARTITION_TIMEZONE,
)
HISTORY_PARTITIONS = dg.DailyPartitionsDefinition(
    start_date=HISTORY_PARTITION_START_DATE,
    timezone=PARTITION_TIMEZONE,
)


# 🔴 `pool=NVD_API_POOL` — NVD를 부르는 step은 인스턴스 전체에서 동시에 1개만 돈다.
#    run 수가 아니라 **step 슬롯**으로 막으므로 잡·백필·UI 단건 실행 어느 경로로
#    띄워도 걸린다. 한도 설정과 fail-closed 확인은 `common/nvd.py` `NVD_API_POOL`.
@dg.asset(
    group_name=GROUP_NAME,
    partitions_def=CVES_PARTITIONS,
    kinds=RAW_KINDS,
    pool=NVD_API_POOL,
)
def raw_nvd_cves(
    context: AssetExecutionContext,
    s3: S3Resource,
    nvd: NvdResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """UTC 하루 동안 `lastModified`가 바뀐 CVE를 새 회차로 S3 raw/에 놓는다.

    커밋된 회차가 있으면 기본 스킵한다(수집 시점 스냅샷 — `common/nvd.py` 독스트링).
    """
    return land_window_pages(
        context,
        s3=s3,
        nvd=nvd,
        source_base=SOURCE_BASE,
        feed=CVES_FEED,
        path=CVES_PATH,
        start_param=CVES_START_PARAM,
        end_param=CVES_END_PARAM,
        items_key=CVES_ITEMS_KEY,
        item_key=CVES_ITEM_KEY,
        id_field="id",
        page_size=CVES_PAGE_SIZE,
        fetched_at=datetime.now(tz=timezone.utc),
        force=config.force,
    )


@dg.asset(
    group_name=GROUP_NAME,
    partitions_def=HISTORY_PARTITIONS,
    kinds=RAW_KINDS,
    pool=NVD_API_POOL,
)
def raw_nvd_cve_history(
    context: AssetExecutionContext,
    s3: S3Resource,
    nvd: NvdResource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """UTC 하루 동안 생긴 CVE 변경 이벤트를 새 회차로 S3 raw/에 놓는다.

    CVE 피드는 "지금의 마지막 버전"만 주므로 그 사이의 수정은 이 피드로만 남는다.
    """
    return land_window_pages(
        context,
        s3=s3,
        nvd=nvd,
        source_base=SOURCE_BASE,
        feed=HISTORY_FEED,
        path=HISTORY_PATH,
        start_param=HISTORY_START_PARAM,
        end_param=HISTORY_END_PARAM,
        items_key=HISTORY_ITEMS_KEY,
        item_key=HISTORY_ITEM_KEY,
        id_field="cveChangeId",
        page_size=HISTORY_PAGE_SIZE,
        fetched_at=datetime.now(tz=timezone.utc),
        force=config.force,
    )


def _land_kev(
    context: AssetExecutionContext,
    *,
    s3: S3Resource,
    fetched_at: datetime,
    force: bool,
) -> dg.MaterializeResult:
    """KEV 카탈로그 전체를 `dt=<수집일>/fetched=<수집시각>/kev.json.gz`로 놓는다.

    자산 본문을 함수로 뺀 것은 테스트가 S3 대역으로 직접 부르기 위해서다(재사용 아님).

    🔴 **NVD 리소스를 쓰지 않는다** — KEV는 다른 호스트(cisa.gov)이고 키가 없다.
    리소스를 거치면 NVD `apiKey` 헤더가 남의 호스트로 나간다.

    🔴 **완결성은 착지 전에 본다**(`count == len(vulnerabilities)`·`cveID` 유일).
    잘리거나 중복된 응답을 그날의 스냅샷으로 커밋하면 같은 날 재실행이 그것을 스킵한다.
    필드 계약 전체는 bronze 파서(`assets._kev_to_arrow`)가 본다.

    Args:
        context: 에셋 실행 컨텍스트.
        s3: dagster-aws S3Resource.
        fetched_at: 이번 수집 회차의 시각(UTC).
        force: True면 그날 커밋된 회차가 있어도 새 회차를 추가로 받는다.

    Returns:
        판정·수집 결과를 담은 MaterializeResult.
    """
    run_date = fetched_at.astimezone(timezone.utc).date().isoformat()
    dt_uri = f"{SOURCE_BASE}/{KEV_FEED}/dt={run_date}"
    latest = latest_committed_run(s3, dt_uri, KEV_FILE_NAME)
    if latest and not force:
        context.log.info("%s: 오늘 커밋된 회차가 있다 — 재수집 생략", latest)
        return dg.MaterializeResult(
            metadata={
                "decision": "skipped_committed",
                "latest_run": latest,
                "api_requests": 0,
            }
        )

    bucket, dt_key = parse_s3_uri(dt_uri)
    run_key = f"{dt_key}/{run_segment(fetched_at)}"
    assert_new_run_prefix(s3, bucket, run_key)

    body = fetch_bytes(KEV_URL, {}, timeout_s=KEV_TIMEOUT_S, retries=KEV_RETRIES)
    payload = json.loads(body)
    entries = payload["vulnerabilities"]
    if int(payload["count"]) != len(entries):
        message = (
            f"KEV count={payload['count']}인데 항목 {len(entries)}건 — 잘린 응답이다. "
            "스냅샷으로 커밋하지 않는다."
        )
        raise RuntimeError(message)
    ids = [entry["cveID"] for entry in entries]
    if len(ids) != len(set(ids)):
        message = "KEV cveID 중복 — 스냅샷으로 커밋하지 않는다."
        raise RuntimeError(message)

    written = put_gzip_with_sidecar(s3, bucket, f"{run_key}/{KEV_FILE_NAME}", body)
    context.log.info("%s: KEV %d건 수집·커밋", run_key, len(entries))
    return dg.MaterializeResult(
        metadata={
            "decision": "downloaded_new_run" if latest else "downloaded",
            "target": f"s3://{bucket}/{run_key}/{KEV_FILE_NAME}",
            "previous_run": latest or "없음",
            "catalog_version": str(payload.get("catalogVersion")),
            "rows": len(entries),
            "total_results": int(payload["count"]),
            "pages": 1,
            "api_requests": 1,
            "gzip_bytes": written["gzip_bytes"],
            "commit_sha256": written["sha256"],
        }
    )


# KEV는 NVD가 아니라 cisa.gov라 `nvd_api` pool에 넣지 않는다(요청 한도가 다른 호스트다).
@dg.asset(group_name=GROUP_NAME, kinds=RAW_KINDS)
def raw_nvd_kev(
    context: AssetExecutionContext,
    s3: S3Resource,
    config: RawFetchConfig,
) -> dg.MaterializeResult:
    """CISA KEV 카탈로그 전체를 수집 회차 스냅샷으로 S3 raw/에 놓는다."""
    return _land_kev(
        context,
        s3=s3,
        fetched_at=datetime.now(tz=timezone.utc),
        force=config.force,
    )
