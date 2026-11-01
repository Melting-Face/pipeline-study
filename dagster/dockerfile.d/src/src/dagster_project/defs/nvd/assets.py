"""NVD CVE·Change History·CISA KEV bronze 적재 에셋 (S3 raw/ → Iceberg).

`raw_assets.py`가 남긴 **수집 시점 스냅샷**을 읽어 올린다 — API를 다시 부르지 않는다.
그래서 파서를 고쳐 bronze를 다시 만들어도 "그때 받은 것"이 그대로 재료가 된다.

**bronze = 핵심 스칼라 + 원본 JSON 문자열(`raw_json`)이다.** 중첩 구조(metrics·
configurations·details 등)의 펼침은 dbt(silver)가 한다 — bronze에서 펼치면 스키마가
원천 변경마다 깨지고, 펼치지 않은 필드는 **조용히 버려진다**.

적재 모드:
- `nvd_cves`·`nvd_cve_history`: 일자 파티션 교체(`replace_partition_in_iceberg`,
  `window_date`). 재실행 멱등이고, 같은 CVE가 다른 날 다시 수정되면 **그 날 파티션에
  또 한 행**이 생긴다 — 버전 이력이 행으로 누적되는 것이 의도다
  (최신 1행으로 접는 것은 silver).
- `nvd_kev`: 테이블 전체 교체(`overwrite_iceberg_table`). 🔴 `append_arrow_to_iceberg`
  `mode="replace"`를 쓰지 않는다 — `drop_table` 후 재생성이라 스냅샷 계보가 끊긴다.

🔴 **결측은 조용히 채우지 않는다.** 필수 필드가 없으면 KeyError로 드러낸다
(`REQUIRED_*`).

🔴 **NVD 시각 문자열에는 오프셋이 없다**(예 `2023-11-07T01:55:06.290`).
**UTC로 해석하되 그 가정은 미확인이다** — API 파라미터는 `Z`(UTC)로 받으므로
응답도 UTC일 개연성이 높지만, 응답 쪽을 명시한 1차 문서를 읽지 못했다(JS 렌더링).
틀리면 모든 시각이 **같은 크기로** 밀려 에러 없이 들어간다. 확인 수단은 파티션
구간과 `last_modified`의 대조다 — 자산이 구간 밖 건수를 메타데이터로 남긴다
(`*_outside_window` — 판정 없이 관측만).

주의: Dagster가 context를 클래스 identity로 검사하므로, 자산 모듈에서는
`from __future__ import annotations`(어노테이션 문자열화)를 사용하지 않는다.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pyarrow as pa
from dagster_aws.s3 import S3Resource
from dagster_iceberg.resource import IcebergTableResource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.fetch import read_verified_gzip
from dagster_project.common.helper import (
    overwrite_iceberg_table,
    parse_s3_uri,
    replace_partition_in_iceberg,
)
from dagster_project.common.nvd import (
    latest_committed_run,
    parse_run_fetched_at,
    read_window_pages,
)
from dagster_project.defs.nvd.constants import (
    CVES_FEED,
    CVES_ITEM_KEY,
    CVES_ITEMS_KEY,
    GROUP_NAME,
    HISTORY_FEED,
    HISTORY_ITEM_KEY,
    HISTORY_ITEMS_KEY,
    KEV_FEED,
    KEV_FILE_NAME,
    SOURCE_BASE,
)
from dagster_project.defs.nvd.raw_assets import (
    CVES_PARTITIONS,
    HISTORY_PARTITIONS,
    raw_nvd_cve_history,
    raw_nvd_cves,
    raw_nvd_kev,
)

BRONZE_KINDS = {"python", "iceberg", "bronze"}

# ── 필드 계약 ─────────────────────────────────────────────────────────
# 🔴 **프로브와 짝을 이루는 상수다.** `scripts/nvd_access_probe.py`의
# `NVD_CVE_FIELDS`·`NVD_CVE_KNOWN_FIELDS`·`NVD_CHANGE_FIELDS`·
# `NVD_CHANGE_KNOWN_FIELDS`·`KEV_TOP_FIELDS`·`KEV_ENTRY_FIELDS`·
# `KEV_ENTRY_KNOWN_FIELDS`와 **같은 집합**이어야 한다.
# 프로브는 PEP 723 단독 실행이라 이 패키지를 import할 수 없어 두 벌이 존재하고, 갈라지면
# **프로브는 통과하고 파서만 깨진다** — `tests/test_nvd_parse.py`의
# `test_probe_and_parser_share_the_field_contract`가 AST 대조로 막는다(의도한 결합).
#
# 필수(`REQUIRED_*`) = 없으면 행이 성립하지 않는 것(KeyError). 아는 전부(`KNOWN_*`) =
# 응답에 새 필드가 생겼는지 프로브가 대조하는 기준. bronze는 `raw_json`에 원본을 통째로
# 담으므로 **모르는 필드도 버려지지 않는다** — KNOWN은 "담을지"가 아니라
# "펼칠지" 판단용이다.
REQUIRED_CVE_FIELDS = ("id", "lastModified", "published", "vulnStatus")
KNOWN_CVE_FIELDS = (
    "affected",
    "cisaActionDue",
    "cisaExploitAdd",
    "cisaRequiredAction",
    "cisaVulnerabilityName",
    "configurations",
    "cveTags",
    "descriptions",
    "evaluatorComment",
    "evaluatorImpact",
    "evaluatorSolution",
    "id",
    "lastModified",
    "metrics",
    "published",
    "references",
    "sourceIdentifier",
    "vendorComments",
    "vulnStatus",
    "weaknesses",
)
REQUIRED_CHANGE_FIELDS = ("created", "cveChangeId", "cveId", "eventName")
KNOWN_CHANGE_FIELDS = (
    "created",
    "cveChangeId",
    "cveId",
    "details",
    "eventName",
    "sourceIdentifier",
)
# KEV 최상위 필수는 스키마의 required를 따른다
# (`title`은 실제 피드에 있으나 required가 아니다).
REQUIRED_KEV_TOP_FIELDS = ("catalogVersion", "count", "dateReleased", "vulnerabilities")
REQUIRED_KEV_ENTRY_FIELDS = (
    "cveID",
    "dateAdded",
    "dueDate",
    "product",
    "requiredAction",
    "shortDescription",
    "vendorProject",
    "vulnerabilityName",
)
# 선택 필드(`knownRansomwareCampaignUse`·`forensicTriage`·`notes`·`cwes`)는
# 없을 수 있다.
KNOWN_KEV_ENTRY_FIELDS = (
    *REQUIRED_KEV_ENTRY_FIELDS,
    "cwes",
    "forensicTriage",
    "knownRansomwareCampaignUse",
    "notes",
)

# ── bronze 스키마 ─────────────────────────────────────────────────────
_TS = pa.timestamp("us", tz="UTC")

NVD_CVES_SCHEMA = pa.schema(
    [
        # 조회한 UTC 하루 = 파티션 키. 교체 필터의 축이다.
        ("window_date", pa.date32()),
        ("cve_id", pa.string()),
        # 🔴 오프셋 없는 원문을 UTC로 해석했다(가정 미확인 — 모듈 독스트링).
        ("published", _TS),
        ("last_modified", _TS),
        ("vuln_status", pa.string()),
        # `vulnerabilities[].cve` 객체 원문(재직렬화 — 키 순서는 보존, 공백은 다르다).
        ("raw_json", pa.string()),
        # raw 수집 회차의 시각(경로 `fetched=`) — 재복사에도 바뀌지 않는다.
        ("fetched_at", _TS),
    ]
)

NVD_CVE_HISTORY_SCHEMA = pa.schema(
    [
        ("window_date", pa.date32()),
        ("cve_change_id", pa.string()),
        ("cve_id", pa.string()),
        ("event_name", pa.string()),
        ("created", _TS),
        # `cveChanges[].change` 객체 원문.
        ("raw_json", pa.string()),
        ("fetched_at", _TS),
    ]
)

NVD_KEV_SCHEMA = pa.schema(
    [
        ("catalog_version", pa.string()),
        ("date_released", _TS),
        ("cve_id", pa.string()),
        ("date_added", pa.date32()),
        ("due_date", pa.date32()),
        # 선택 필드 — 없으면 NULL(빈 문자열로 채우지 않는다: "모름"과 "빈 값"이 섞인다).
        ("known_ransomware_campaign_use", pa.string()),
        # `vulnerabilities[]` 항목 원문.
        ("raw_json", pa.string()),
        ("fetched_at", _TS),
    ]
)

# 소수부 자릿수 정규화용 — Python 3.10 `fromisoformat`은 3·6자리만 받는데 KEV
# `dateReleased`는 다른 자릿수로 올 수 있다.
_FRACTION = re.compile(r"\.(\d+)")


def _parse_timestamp(raw: str) -> datetime:
    """원천 시각 문자열을 tz-aware UTC로 바꾼다.

    🔴 **오프셋이 없으면 UTC로 간주한다 — 이 가정은 미확인이다**(모듈 독스트링).
    `Z`·오프셋이 있으면 그것을 따른다. naive로 담지 않는 이유는 이후 비교가
    naive/aware 혼합으로 터지거나, 더 나쁘게는 조용히 로컬 시각으로 읽히기 때문이다.

    Args:
        raw: ISO-8601 시각(예 `2023-11-07T01:55:06.290`, `2025-01-02T03:04:05.1234Z`).

    Returns:
        UTC로 정규화된 tz-aware datetime.
    """
    text = raw.replace("Z", "+00:00")
    text = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _dumps(obj: Any) -> str:
    """원본 JSON 객체를 문자열로 담는다(비ASCII 보존·공백 없음)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _cves_to_arrow(
    pages: list[dict[str, Any]], window_date: str, fetched_at: datetime
) -> pa.Table:
    """CVE 구간 페이지들을 bronze Arrow 테이블로 편다.

    Args:
        pages: `read_window_pages`가 읽은 페이지 JSON 목록.
        window_date: 파티션 키(`YYYY-MM-DD`).
        fetched_at: raw 수집 회차의 시각(경로 `fetched=`).

    Returns:
        NVD_CVES_SCHEMA를 따르는 테이블(CVE당 1행, 없으면 0행).

    Raises:
        KeyError: CVE에 `REQUIRED_CVE_FIELDS` 중 하나라도 없을 때.
        RuntimeError: 같은 구간에 같은 CVE가 두 번 나올 때(페이지 이동 의심).
    """
    day = date.fromisoformat(window_date)
    rows = []
    for payload in pages:
        for item in payload[CVES_ITEMS_KEY]:
            cve = item[CVES_ITEM_KEY]
            rows.append(
                {
                    "window_date": day,
                    "cve_id": cve["id"],
                    "published": _parse_timestamp(cve["published"]),
                    "last_modified": _parse_timestamp(cve["lastModified"]),
                    "vuln_status": cve["vulnStatus"],
                    "raw_json": _dumps(cve),
                    "fetched_at": fetched_at.astimezone(timezone.utc),
                }
            )
    # 🔴 한 구간 안에서 CVE는 한 번만 나온다(`lastModified`는 하나다). 두 번이면 순회 중
    #    레코드가 이동해 페이지 경계에서 겹친 것이다 — 합계는 맞아도 한 건이 빠졌다.
    #    raw 커밋 전에 같은 가드가 먼저 걸린다(`land_window_pages`) — 여기는 두 번째 벽.
    ids = [row["cve_id"] for row in rows]
    if len(ids) != len(set(ids)):
        message = f"cves dt={window_date}: 같은 CVE가 두 번 나왔다 — 페이지 이동 의심"
        raise RuntimeError(message)
    rows.sort(key=lambda row: row["cve_id"])
    return pa.Table.from_pylist(rows, schema=NVD_CVES_SCHEMA)


def _changes_to_arrow(
    pages: list[dict[str, Any]], window_date: str, fetched_at: datetime
) -> pa.Table:
    """Change History 구간 페이지들을 bronze Arrow 테이블로 편다.

    Args:
        pages: `read_window_pages`가 읽은 페이지 JSON 목록.
        window_date: 파티션 키.
        fetched_at: raw 수집 회차의 시각.

    Returns:
        NVD_CVE_HISTORY_SCHEMA를 따르는 테이블(변경 이벤트당 1행).

    Raises:
        KeyError: 변경에 `REQUIRED_CHANGE_FIELDS` 중 하나라도 없을 때.
        RuntimeError: 같은 `cveChangeId`가 두 번 나올 때.
    """
    day = date.fromisoformat(window_date)
    rows = []
    for payload in pages:
        for item in payload[HISTORY_ITEMS_KEY]:
            change = item[HISTORY_ITEM_KEY]
            rows.append(
                {
                    "window_date": day,
                    "cve_change_id": change["cveChangeId"],
                    "cve_id": change["cveId"],
                    "event_name": change["eventName"],
                    "created": _parse_timestamp(change["created"]),
                    "raw_json": _dumps(change),
                    "fetched_at": fetched_at.astimezone(timezone.utc),
                }
            )
    ids = [row["cve_change_id"] for row in rows]
    if len(ids) != len(set(ids)):
        message = f"cve_history dt={window_date}: 같은 cveChangeId가 두 번 나왔다"
        raise RuntimeError(message)
    rows.sort(key=lambda row: (row["created"], row["cve_change_id"]))
    return pa.Table.from_pylist(rows, schema=NVD_CVE_HISTORY_SCHEMA)


def _kev_to_arrow(payload: dict[str, Any], fetched_at: datetime) -> pa.Table:
    """KEV 카탈로그 전체를 bronze Arrow 테이블로 편다.

    Args:
        payload: KEV JSON 전체.
        fetched_at: raw 수집 회차의 시각.

    Returns:
        NVD_KEV_SCHEMA를 따르는 테이블(항목당 1행).

    Raises:
        KeyError: 최상위·항목 필수 필드가 없을 때.
        RuntimeError: `count` ≠ 항목 수, 또는 `cveID` 중복.
    """
    missing_top = [f for f in REQUIRED_KEV_TOP_FIELDS if f not in payload]
    if missing_top:
        raise KeyError(missing_top[0])
    entries = payload["vulnerabilities"]
    if int(payload["count"]) != len(entries):
        message = f"KEV count={payload['count']}인데 항목 {len(entries)}건 — 잘렸다"
        raise RuntimeError(message)
    catalog_version = str(payload["catalogVersion"])
    date_released = _parse_timestamp(payload["dateReleased"])
    rows = []
    for entry in entries:
        missing = [f for f in REQUIRED_KEV_ENTRY_FIELDS if f not in entry]
        if missing:
            raise KeyError(missing[0])
        rows.append(
            {
                "catalog_version": catalog_version,
                "date_released": date_released,
                "cve_id": entry["cveID"],
                "date_added": date.fromisoformat(entry["dateAdded"]),
                "due_date": date.fromisoformat(entry["dueDate"]),
                "known_ransomware_campaign_use": entry.get(
                    "knownRansomwareCampaignUse"
                ),
                "raw_json": _dumps(entry),
                "fetched_at": fetched_at.astimezone(timezone.utc),
            }
        )
    # 🔴 `cveID`가 키다 — 중복이면 키로 쓸 수 없고 silver 조인이 행을 불린다.
    ids = [row["cve_id"] for row in rows]
    if len(ids) != len(set(ids)):
        message = "KEV cveID 중복 — 키로 쓸 수 없다"
        raise RuntimeError(message)
    rows.sort(key=lambda row: row["cve_id"])
    return pa.Table.from_pylist(rows, schema=NVD_KEV_SCHEMA)


def _count_outside_window(stamps: list[datetime], window_date: str) -> int:
    """UTC 하루 구간 밖에 놓인 시각의 수(관측 셀 — 판정하지 않는다).

    0이 아니면 ⓐ 시각의 UTC 가정이 틀렸거나 ⓑ 원천의 구간 해석이 우리와 다르다는 신호다.
    """
    start = datetime.fromisoformat(window_date).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return sum(1 for stamp in stamps if not start <= stamp < end)


@dg.asset(
    group_name=GROUP_NAME,
    partitions_def=CVES_PARTITIONS,
    kinds=BRONZE_KINDS,
    deps=[raw_nvd_cves],
)
def nvd_cves(
    context: AssetExecutionContext,
    s3: S3Resource,
    nvd_cves_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """하루 구간에 수정된 CVE의 그날 버전을 bronze 파티션으로 적재한다."""
    window_date = context.partition_key
    pages, fetched_at, run_uri = read_window_pages(
        s3,
        source_base=SOURCE_BASE,
        feed=CVES_FEED,
        window_date=window_date,
        items_key=CVES_ITEMS_KEY,
    )
    arrow = _cves_to_arrow(pages, window_date, fetched_at)
    outside = _count_outside_window(
        arrow.column("last_modified").to_pylist(), window_date
    )
    return replace_partition_in_iceberg(
        context,
        iceberg_table=nvd_cves_table,
        arrow=arrow,
        partition_column="window_date",
        partition_value=window_date,
        extra_metadata={
            "source_run": run_uri,
            "pages": len(pages),
            "total_results": int(pages[0]["totalResults"]),
            # 이 자산은 API를 부르지 않는다(raw를 읽는다) — 0이 정상이다.
            "api_requests": 0,
            # 🔴 관측 셀 — UTC 가정·구간 해석의 검산(모듈 독스트링).
            "last_modified_outside_window": outside,
        },
    )


@dg.asset(
    group_name=GROUP_NAME,
    partitions_def=HISTORY_PARTITIONS,
    kinds=BRONZE_KINDS,
    deps=[raw_nvd_cve_history],
)
def nvd_cve_history(
    context: AssetExecutionContext,
    s3: S3Resource,
    nvd_cve_history_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """하루 구간에 생긴 CVE 변경 이벤트를 bronze 파티션으로 적재한다."""
    window_date = context.partition_key
    pages, fetched_at, run_uri = read_window_pages(
        s3,
        source_base=SOURCE_BASE,
        feed=HISTORY_FEED,
        window_date=window_date,
        items_key=HISTORY_ITEMS_KEY,
    )
    arrow = _changes_to_arrow(pages, window_date, fetched_at)
    outside = _count_outside_window(arrow.column("created").to_pylist(), window_date)
    return replace_partition_in_iceberg(
        context,
        iceberg_table=nvd_cve_history_table,
        arrow=arrow,
        partition_column="window_date",
        partition_value=window_date,
        extra_metadata={
            "source_run": run_uri,
            "pages": len(pages),
            "total_results": int(pages[0]["totalResults"]),
            "api_requests": 0,
            "created_outside_window": outside,
            "distinct_cve_ids": len(set(arrow.column("cve_id").to_pylist())),
        },
    )


@dg.asset(group_name=GROUP_NAME, kinds=BRONZE_KINDS, deps=[raw_nvd_kev])
def nvd_kev(
    context: AssetExecutionContext,
    s3: S3Resource,
    nvd_kev_table: IcebergTableResource,
) -> dg.MaterializeResult:
    """가장 최근에 커밋된 KEV 스냅샷으로 bronze 테이블 전체를 교체한다.

    🔴 **drop/recreate가 아니라 overwrite다**(스냅샷 계보 유지 — 모듈 독스트링).
    """
    feed_uri = f"{SOURCE_BASE}/{KEV_FEED}"
    run_key = latest_committed_run(s3, feed_uri, KEV_FILE_NAME)
    if run_key is None:
        message = f"{feed_uri}: 커밋된 KEV 회차가 없다 — raw 자산을 먼저 돌린다"
        raise RuntimeError(message)
    bucket, _ = parse_s3_uri(feed_uri)
    key = f"{run_key}/{KEV_FILE_NAME}"
    payload = json.loads(read_verified_gzip(s3, bucket, key))
    arrow = _kev_to_arrow(payload, parse_run_fetched_at(run_key))
    return overwrite_iceberg_table(
        context,
        iceberg_table=nvd_kev_table,
        arrow=arrow,
        extra_metadata={
            "source": f"s3://{bucket}/{key}",
            "catalog_version": str(payload["catalogVersion"]),
            "total_results": int(payload["count"]),
            "pages": 1,
            "api_requests": 0,
            "with_ransomware_field": sum(
                1
                for value in arrow.column("known_ransomware_campaign_use").to_pylist()
                if value is not None
            ),
        },
    )
