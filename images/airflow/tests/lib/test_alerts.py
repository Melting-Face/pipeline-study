"""Discord 발신 통제(`lakehouse.alerts`·`lakehouse.http.post_json`·`lakehouse.trino`).

🔴 **통제 배선 파일**이다(docs/risk.md §4 예외 ②). 화이트리스트·상한·마스킹을
지키는 셀이 여기 있다. 기대값을 고치는 PR은 예외의 범위를 바꾸는 PR로 다룬다.

- 실웹훅·Discord에 붙지 않는다. `requests.post`는 monkeypatch로 바꾸고,
  urllib3 로그 셀만 **루프백(127.0.0.1) 스텁 서버**에 보낸다(외부 발신 아님).
- 웹훅 토큰·제목은 합성 값이다. 토큰은 시크릿 스캐너가 잡지 않게
  저엔트로피 문자열로 둔다(경로 꼴 `[A-Za-z0-9_-]+`에는 맞는다).
- 위반 대조: 셀마다 중화·마스킹을 끈 대조군을 함께 두어 「0건」이 관측 경로가
  죽어서 나온 0이 아님을 보인다.
"""

import ast
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import traceback
import types
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
import requests
from lakehouse import alerts
from lakehouse.alerts import (
    ALLOWED_FIELDS,
    DAILY_SEND_LIMIT,
    EMBED_KEYS,
    EMBED_SOURCES,
    KIND_DIGEST,
    KIND_KEV,
    MAX_EMBED_CHARS_PER_MESSAGE,
    MAX_EMBEDS_PER_MESSAGE,
    PAYLOAD_KEYS,
    SEND_LOG_IDENTIFIER,
    SEND_LOG_SCHEMA,
    STATUS_BASELINE,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SENT,
    TITLE_MAX,
    AlertItem,
    Candidate,
    build_candidates,
    build_payloads,
    chunk_embeds,
    close_pending,
    count_today,
    digest_candidate,
    embed_chars,
    kev_candidate,
    load_watchlist,
    mask_webhook,
    matches_watchlist,
    neutralize_mentions,
    plan_send,
    read_send_log,
    run_send,
    seed_key,
    select_unsent,
    today_kst,
    unpaired_pending,
    validate_webhook_url,
    watchlist_digest,
)
from lakehouse.http import MAX_RETRY_AFTER_S, PayloadRejectedError, post_json
from lakehouse.iceberg import ensure_table
from pyiceberg.catalog.sql import SqlCatalog

LAKEHOUSE_DIR = Path(alerts.__file__).parent
DAGS_DIR = LAKEHOUSE_DIR.parent
REPO_ROOT = Path(__file__).resolve().parents[4]
TOKEN = "synthetic-token-sentinel"  # noqa: S105 — 합성 값(저엔트로피)
WEBHOOK = f"https://discord.com/api/webhooks/123456789/{TOKEN}"
TODAY = date(2026, 11, 8)
# 2026-11-08 09:00 KST
NOW = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
HOSTILE_TITLES = [
    "@everyone 긴급 패치",
    "@here 확인",
    "<@123> 님 보세요",
    "<@!123> 님",
    "<@&1> 역할",
    "<#99> 채널",
    "[x](javascript:alert(1))",
    "**굵게** `코드` > 인용 ||스포||",
]


def _digest_row(topic_id: int, cve: str = "CVE-2026-10001", **extra) -> dict:
    row = {
        "topic_id": topic_id,
        "title": f"글 {topic_id}",
        "link": f"https://news.hada.io/topic?id={topic_id}",
        "cve_id": cve,
        "cvss_score": 9.8,
        "cvss_severity": "CRITICAL",
        "is_kev": False,
        "kev_vendor_project": None,
        "kev_product": None,
        "cpe_vendor_products": [],
    }
    row.update(extra)
    return row


def _kev_row(n: int, **extra) -> dict:
    row = {
        "cve_id": f"CVE-2026-{20000 + n}",
        "cvss_score": 7.5,
        "cvss_severity": "HIGH",
        "is_kev": True,
        "kev_vendor_project": "ExampleCorp",
        "kev_product": "Widget Server",
        "cpe_vendor_products": ["examplecorp:widget_server"],
    }
    row.update(extra)
    return row


def _digests(n: int, start: int = 1) -> list[Candidate]:
    return [digest_candidate(_digest_row(i)) for i in range(start, start + n)]


def _kevs(n: int, start: int = 1) -> list[Candidate]:
    return [kev_candidate(_kev_row(i)) for i in range(start, start + n)]


def _log(*rows: tuple[str, str, str, date]) -> list[dict]:
    return [
        {"send_key": k, "kind": kind, "status": s, "kst_date": d}
        for k, kind, s, d in rows
    ]


# 이 kind의 기록이 이미 있다(시드 끝남) — 상한 셀의 공통 전제
SEEDED_DIGEST = _log((seed_key(KIND_DIGEST), KIND_DIGEST, STATUS_BASELINE, TODAY))
# kev 표지는 관심목록 해시가 필수다(plan_send) — 셀 공통 합성 해시
KEV_SEED = seed_key(KIND_KEV, "0" * 12)


# ── 화이트리스트 ─────────────────────────────────────────────────────────


def test_item_fields_are_exactly_the_whitelist() -> None:
    fields = set(AlertItem.__dataclass_fields__)
    assert fields == ALLOWED_FIELDS
    assert set().union(*EMBED_SOURCES.values()) == ALLOWED_FIELDS


def _import_copy(tmp_path: Path, mutate: bool) -> subprocess.CompletedProcess:
    pkg = tmp_path / "lakehouse"
    shutil.copytree(LAKEHOUSE_DIR, pkg, ignore=shutil.ignore_patterns("__pycache__"))
    source = (pkg / "alerts.py").read_text(encoding="utf-8")
    anchor = "    geeknews_link: str | None = None\n"
    assert source.count(anchor) == 1  # 변이 자리가 정확히 1곳(빗나가면 대조 무효)
    if mutate:
        source = source.replace(anchor, anchor + "    description: str | None = None\n")
        (pkg / "alerts.py").write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-c", "import lakehouse.alerts"],
        cwd=tmp_path,
        env={"PYTHONPATH": str(tmp_path), "PATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )


def test_import_fails_when_item_gains_description(tmp_path) -> None:
    """원본 사본은 import되고, description 필드를 더한 사본은 import가 실패한다."""
    original = _import_copy(tmp_path / "orig", mutate=False)
    mutated = _import_copy(tmp_path / "mut", mutate=True)
    assert original.returncode == 0, original.stderr
    assert mutated.returncode != 0
    assert "ALLOWED_FIELDS" in mutated.stderr
    assert "description" in mutated.stderr


def test_nvd_url_is_built_from_cve_only() -> None:
    item = AlertItem(cve_id="CVE-2026-12345")
    assert item.nvd_url == "https://nvd.nist.gov/vuln/detail/CVE-2026-12345"
    with pytest.raises(TypeError):
        AlertItem(cve_id="CVE-2026-12345", nvd_url="https://evil.example/")


def _mixed_payloads() -> list[tuple[dict, list[str]]]:
    # KEV인 글(벤더/제품 칸까지 채운다) + KEV 아닌 글
    digest = [digest_candidate(_digest_row(1, **_kev_row(0)))]
    digest.append(digest_candidate(_digest_row(2)))
    kev = [kev_candidate(_kev_row(1))]
    return build_payloads(digest) + build_payloads(kev)


def test_payload_and_embed_keys_are_whitelisted() -> None:
    payloads = _mixed_payloads()
    assert payloads
    for payload, keys in payloads:
        assert set(payload) == PAYLOAD_KEYS
        assert keys
        for embed in payload["embeds"]:
            assert set(embed) == EMBED_KEYS
            names = {field["name"] for field in embed["fields"]}
            assert names <= set(EMBED_SOURCES)


def test_kev_embed_uses_every_whitelisted_field_and_nothing_else() -> None:
    (payload, _), *_ = build_payloads([kev_candidate(_kev_row(1))])
    embed = payload["embeds"][0]
    labels = {"title", "url"} | {field["name"] for field in embed["fields"]}
    assert set().union(*(EMBED_SOURCES[label] for label in labels)) == ALLOWED_FIELDS


# 필드마다 서로 다른 센티넬 — 어느 값이 어느 칸에 갔는지 한 번에 판정한다
SENTINEL_ROW = {
    "topic_id": 987654,
    "title": "T-TITLE-SENTINEL",
    "link": "https://links.example/L-LINK-SENTINEL",
    "cve_id": "CVE-2031-424242",
    "cvss_score": 6.1,
    "cvss_severity": "S-SEV-SENTINEL",
    "is_kev": True,
    "kev_vendor_project": "V-VENDOR-SENTINEL",
    "kev_product": "P-PRODUCT-SENTINEL",
    "cpe_vendor_products": ["c-cpe-sentinel:c-cpe-sentinel"],
    "description_en": "D-DESCRIPTION-SENTINEL",
}
SENTINEL_FIELDS = [
    {"name": "CVE", "value": "CVE-2031-424242", "inline": True},
    {"name": "CVSS", "value": "6.1 (S-SEV-SENTINEL)", "inline": True},
    {"name": "KEV", "value": "예", "inline": True},
    {"name": "벤더/제품", "value": "V-VENDOR-SENTINEL / P-PRODUCT-SENTINEL"},
    {
        "name": "상세",
        "value": "[상세](https://nvd.nist.gov/vuln/detail/CVE-2031-424242)",
    },
]


def test_digest_payload_is_exactly_expected() -> None:
    """칸마다 정확히 어느 값이 들어가는지 고정한다(키·topic_id·설명문은 없다)."""
    payloads = build_payloads([digest_candidate(SENTINEL_ROW)])
    assert payloads == [
        (
            {
                "content": "GeekNews × CVE 다이제스트",  # noqa: RUF001
                "embeds": [
                    {
                        "title": "T-TITLE-SENTINEL",
                        "url": "https://links.example/L-LINK-SENTINEL",
                        "fields": SENTINEL_FIELDS,
                    }
                ],
                "allowed_mentions": {"parse": []},
            },
            ["987654:CVE-2031-424242"],
        )
    ]
    dumped = json.dumps(payloads[0][0], ensure_ascii=False)
    for absent in ["987654", "D-DESCRIPTION", "c-cpe-sentinel"]:
        assert absent not in dumped


def test_kev_payload_is_exactly_expected() -> None:
    payloads = build_payloads([kev_candidate(SENTINEL_ROW)])
    assert payloads == [
        (
            {
                "content": "관심 제품 신규 KEV",
                "embeds": [
                    {
                        "title": "CVE-2031-424242",
                        "url": "https://nvd.nist.gov/vuln/detail/CVE-2031-424242",
                        "fields": SENTINEL_FIELDS,
                    }
                ],
                "allowed_mentions": {"parse": []},
            },
            ["kev:CVE-2031-424242"],
        )
    ]
    dumped = json.dumps(payloads[0][0], ensure_ascii=False)
    for absent in ["T-TITLE", "L-LINK", "kev:", "D-DESCRIPTION", "c-cpe-sentinel"]:
        assert absent not in dumped


def test_description_and_source_wording_never_reach_payload() -> None:
    row = _digest_row(7, description_en="SENTINEL-NVD-DESCRIPTION")
    payload_json = json.dumps(
        build_payloads([digest_candidate(row)]), ensure_ascii=False
    )
    assert "SENTINEL-NVD-DESCRIPTION" not in payload_json
    assert "description" not in payload_json
    assert "NVD" not in payload_json  # 출처 표기 문구 없음(링크 호스트는 소문자)
    assert "제공" not in payload_json
    assert "[상세](https://nvd.nist.gov/vuln/detail/CVE-2026-10001)" in payload_json


def test_allowed_mentions_is_always_empty_parse() -> None:
    for payload, _ in _mixed_payloads():
        assert payload["allowed_mentions"] == {"parse": []}


def test_content_is_fixed_text_only() -> None:
    hostile = digest_candidate(_digest_row(1, title="@everyone"))
    (payload, _), *_ = build_payloads([hostile])
    assert payload["content"] == "GeekNews × CVE 다이제스트"  # noqa: RUF001
    (payload, _), *_ = build_payloads([kev_candidate(_kev_row(1))])
    assert payload["content"] == "관심 제품 신규 KEV"


# ── 멘션·마크다운 중화 ───────────────────────────────────────────────────


def _payload_json_for(title: str) -> str:
    candidate = digest_candidate(_digest_row(1, title=title))
    return json.dumps(build_payloads([candidate]), ensure_ascii=False)


@pytest.mark.parametrize("title", HOSTILE_TITLES)
def test_hostile_title_is_not_left_verbatim(title) -> None:
    assert title not in _payload_json_for(title)


@pytest.mark.parametrize("title", HOSTILE_TITLES)
def test_control_without_neutralize_leaves_title_verbatim(title, monkeypatch) -> None:
    """대조군 — 중화를 끄면 원문이 그대로 남는다(위 셀의 0건이 관측 결과임)."""
    monkeypatch.setattr(
        "lakehouse.alerts.neutralize_mentions", lambda text, limit=None: text
    )
    assert title in _payload_json_for(title)


def test_neutralize_breaks_every_mention_form() -> None:
    for raw in ["@everyone", "@here", "<@1>", "<@!1>", "<@&1>", "<#1>"]:
        out = neutralize_mentions(raw)
        assert raw not in out
        assert "​" in out


def test_neutralize_escapes_markdown_link() -> None:
    out = neutralize_mentions("[x](javascript:alert(1))")
    assert out == "\\[x\\]\\(javascript:alert\\(1\\)\\)"


def test_neutralize_flattens_newlines() -> None:
    assert "\n" not in neutralize_mentions("첫 줄\n> 인용")


def _title_of(raw: str) -> str:
    payloads = build_payloads([digest_candidate(_digest_row(1, title=raw))])
    return payloads[0][0]["embeds"][0]["title"]


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_title_is_clipped_after_escape() -> None:
    """원문 길이는 한도 안이어도 escape 뒤 길이가 넘으면 자른다."""
    raw = "*" * 200  # 원문 200자 → escape 뒤 400자
    assert len(raw) <= TITLE_MAX < len(neutralize_mentions(raw))
    title = _title_of(raw)
    assert _utf16(title) <= TITLE_MAX
    assert title.endswith("…")
    body = title[:-1]
    # 자른 자리에 외톨이 역슬래시가 없다(말줄임표가 escape되지 않는다)
    assert (len(body) - len(body.rstrip("\\"))) % 2 == 0


def test_title_is_clipped_in_utf16_units() -> None:
    """코드 포인트 수는 한도 안이어도 UTF-16 유닛이 넘으면 자른다(이모지 = 2)."""
    raw = "😀" * 200  # 200 코드 포인트, 400 유닛
    assert len(raw) <= TITLE_MAX < _utf16(raw)
    title = _title_of(raw)
    assert _utf16(title) <= TITLE_MAX
    assert title.endswith("…")


def test_kev_name_is_clipped_in_utf16_units() -> None:
    row = _kev_row(1, kev_vendor_project="😀" * 400, kev_product="😀" * 400)
    (payload, _), *_ = build_payloads([kev_candidate(row)])
    value = next(
        f["value"] for f in payload["embeds"][0]["fields"] if f["name"] == "벤더/제품"
    )
    assert _utf16(value) <= alerts.FIELD_VALUE_MAX


# ── 입력 검증 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cve",
    ["CVE-2026-123", "cve-2026-12345", "CVE-2026-12345\n", "CVE-2026-1234 <@1>", ""],
)
def test_bad_cve_id_is_rejected(cve) -> None:
    with pytest.raises(ValueError, match="cve_id"):
        AlertItem(cve_id=cve)


@pytest.mark.parametrize(
    "link",
    ["javascript:alert(1)", "ftp://h/x", "//news.hada.io/x", "news.hada.io/x", ""],
)
def test_non_http_link_is_rejected(link) -> None:
    with pytest.raises(ValueError, match="geeknews_link"):
        AlertItem(cve_id="CVE-2026-12345", geeknews_link=link)


@pytest.mark.parametrize("link", ["https://news.hada.io/x", "http://news.hada.io/x"])
def test_http_link_is_accepted(link) -> None:
    assert AlertItem(cve_id="CVE-2026-12345", geeknews_link=link).geeknews_link


# ── 묶음 한도(embed 10개·6000자) ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("count", "sizes"), [(10, [10]), (11, [10, 1]), (20, [10, 10]), (21, [10, 10, 1])]
)
def test_payloads_split_at_ten_embeds(count, sizes) -> None:
    payloads = build_payloads(_digests(count))
    assert [len(p["embeds"]) for p, _ in payloads] == sizes
    assert [len(keys) for _, keys in payloads] == sizes


def test_chunk_boundary_at_6000_chars() -> None:
    half = {"title": "a" * 3000, "fields": []}
    over = {"title": "a" * 3001, "fields": []}
    assert chunk_embeds([half, half]) == [[0, 1]]
    assert chunk_embeds([half, over]) == [[0], [1]]
    with pytest.raises(ValueError, match="한도"):
        chunk_embeds([{"title": "a" * 6001, "fields": []}])


def test_long_embeds_split_by_chars_before_ten() -> None:
    rows = [
        _kev_row(i, kev_vendor_project="v" * 2000, kev_product="p" * 2000)
        for i in range(9)
    ]
    payloads = build_payloads([kev_candidate(r) for r in rows])
    assert len(payloads) > 1
    for payload, _ in payloads:
        assert len(payload["embeds"]) <= MAX_EMBEDS_PER_MESSAGE
        total = sum(embed_chars(e) for e in payload["embeds"])
        assert total <= MAX_EMBED_CHARS_PER_MESSAGE


def test_embed_chars_counts_utf16_units() -> None:
    assert embed_chars({"title": "😀", "fields": []}) == 2


# ── 상한·중복·baseline ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("count", "sent", "truncated"),
    [
        (DAILY_SEND_LIMIT - 1, DAILY_SEND_LIMIT - 1, 0),
        (DAILY_SEND_LIMIT, DAILY_SEND_LIMIT, 0),
        (DAILY_SEND_LIMIT + 1, DAILY_SEND_LIMIT, 1),
    ],
)
def test_daily_limit_boundary(count, sent, truncated) -> None:
    plan = plan_send(_digests(count), SEEDED_DIGEST, TODAY, kind=KIND_DIGEST)
    assert len(plan.to_send) == sent
    assert plan.truncated == truncated


def test_limit_reached_today_sends_nothing() -> None:
    log = SEEDED_DIGEST + _log(
        *[
            (f"kev:CVE-2026-{30000 + i}", KIND_KEV, STATUS_SENT, TODAY)
            for i in range(DAILY_SEND_LIMIT)
        ]
    )
    plan = plan_send(_digests(3), log, TODAY, kind=KIND_DIGEST)
    assert plan.sent_today == DAILY_SEND_LIMIT  # 두 DAG 합산
    assert plan.to_send == ()
    assert plan.truncated == 3


def test_limit_counts_only_today_kst_sent_rows() -> None:
    yesterday = TODAY - timedelta(days=1)
    log = SEEDED_DIGEST + _log(
        *[(f"old{i}", KIND_DIGEST, STATUS_SENT, yesterday) for i in range(20)],
        *[(f"base{i}", KIND_DIGEST, STATUS_BASELINE, TODAY) for i in range(20)],
        ("today", KIND_DIGEST, STATUS_SENT, TODAY),
    )
    plan = plan_send(_digests(30), log, TODAY, kind=KIND_DIGEST)
    assert plan.sent_today == 1
    assert len(plan.to_send) == DAILY_SEND_LIMIT - 1


def test_limit_counts_pending_once_and_skips_rejected() -> None:
    """pending→sent 두 행은 1건, rejected로 닫힌 key는 상한에 세지 않는다."""
    log = _log(
        ("a", KIND_DIGEST, STATUS_PENDING, TODAY),
        ("a", KIND_DIGEST, STATUS_SENT, TODAY),
        ("b", KIND_DIGEST, STATUS_PENDING, TODAY),
        ("c", KIND_DIGEST, STATUS_PENDING, TODAY),
        ("c", KIND_DIGEST, STATUS_REJECTED, TODAY),
    )
    assert count_today(log, TODAY) == 2  # a, b
    assert unpaired_pending(log, KIND_DIGEST) == ["b"]
    assert unpaired_pending(log, KIND_KEV) == []


def test_other_kind_unpaired_pending_eats_the_shared_limit() -> None:
    """kev에 짝 없는 pending 15건이 남으면 digest의 남은 상한은 5다(나갔을 수 있다)."""
    log = SEEDED_DIGEST + _log(
        *[
            (f"kev:CVE-2026-{40000 + i}", KIND_KEV, STATUS_PENDING, TODAY)
            for i in range(15)
        ]
    )
    plan = plan_send(_digests(10), log, TODAY, kind=KIND_DIGEST)
    assert plan.sent_today == 15
    assert len(plan.to_send) == DAILY_SEND_LIMIT - 15


def test_logged_keys_are_not_resent() -> None:
    candidates = _digests(4)
    log = SEEDED_DIGEST + _log(
        (candidates[0].key, KIND_DIGEST, STATUS_SENT, TODAY - timedelta(days=3)),
        (candidates[1].key, KIND_DIGEST, STATUS_BASELINE, TODAY),
        (candidates[2].key, KIND_DIGEST, STATUS_PENDING, TODAY),
        (candidates[2].key, KIND_DIGEST, STATUS_REJECTED, TODAY),
    )
    plan = plan_send(candidates, log, TODAY, kind=KIND_DIGEST)
    assert [c.key for c in plan.to_send] == [candidates[3].key]
    assert plan.already_logged == 3


def test_empty_log_seeds_baseline_and_sends_nothing() -> None:
    candidates = _digests(5)
    plan = plan_send(candidates, [], TODAY, kind=KIND_DIGEST)
    assert plan.seeded
    assert plan.to_send == ()
    assert set(plan.baseline_keys) == {c.key for c in candidates} | {
        seed_key(KIND_DIGEST)
    }


def test_seed_is_per_kind() -> None:
    """digest만 시드된 기록에서 KEV를 처음 켜면 KEV도 시드만 한다(보내지 않는다)."""
    plan = plan_send(_kevs(1), SEEDED_DIGEST, TODAY, kind=KIND_KEV, seed=KEV_SEED)
    assert plan.seeded
    assert plan.to_send == ()


def test_seed_marker_lets_later_candidates_through() -> None:
    """후보 0건으로 시드한 뒤 생긴 후보는 baseline이 아니라 발신 대상이다."""
    first = plan_send([], [], TODAY, kind=KIND_KEV, seed=KEV_SEED)
    assert first.baseline_keys == (KEV_SEED,)
    log = _log((KEV_SEED, KIND_KEV, STATUS_BASELINE, TODAY))
    second = plan_send(_kevs(1), log, TODAY, kind=KIND_KEV, seed=KEV_SEED)
    assert not second.seeded
    assert len(second.to_send) == 1


@pytest.mark.parametrize("seed", [None, "", seed_key(KIND_KEV), "kev:__seed__"])
def test_kev_plan_requires_watchlist_seed(seed) -> None:
    """kev는 관심목록 해시가 든 표지 없이 계획하지 않는다(재시드가 꺼진다)."""
    with pytest.raises(ValueError, match="관심목록 해시"):
        plan_send(_kevs(1), [], TODAY, kind=KIND_KEV, seed=seed)


def test_kev_run_send_without_seed_stops_before_any_write(send_log) -> None:
    post = _Recorder()
    with pytest.raises(ValueError, match="관심목록 해시"):
        run_send(KIND_KEV, _kevs(2), table=send_log, post=post, now=lambda: NOW)
    assert read_send_log(send_log) == []
    assert post.calls == 0


def test_digest_plan_needs_no_watchlist_seed() -> None:
    """대조군 — digest는 해시 없는 기본 표지로 계획한다(검사가 kev에만 걸린다)."""
    assert plan_send(_digests(1), [], TODAY, kind=KIND_DIGEST).seeded


def test_kev_dag_passes_watchlist_seed_to_run_send() -> None:
    """alerts_kev.py의 run_send 호출이 seed= 를 넘긴다(DAG 배선의 정적 대조)."""
    tree = ast.parse((DAGS_DIR / "alerts_kev.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "run_send"
    ]
    assert len(calls) == 1  # 탐색 대상이 실제로 있다
    seed = next((kw.value for kw in calls[0].keywords if kw.arg == "seed"), None)
    assert isinstance(seed, ast.Call)
    assert getattr(seed.func, "id", "") == "seed_key"
    assert ast.unparse(seed.args[1]).startswith("watchlist_digest(")


def test_unpaired_pending_blocks_the_kind_only() -> None:
    log = (
        SEEDED_DIGEST
        + _log((KEV_SEED, KIND_KEV, STATUS_BASELINE, TODAY))
        + _log(("kev:CVE-2026-29999", KIND_KEV, STATUS_PENDING, TODAY))
    )
    kev = plan_send(_kevs(3), log, TODAY, kind=KIND_KEV, seed=KEV_SEED)
    assert kev.unpaired == ("kev:CVE-2026-29999",)
    assert kev.to_send == ()
    assert not kev.seeded
    digest = plan_send(_digests(3), log, TODAY, kind=KIND_DIGEST)
    assert len(digest.to_send) == 3


def test_select_unsent_dedupes_and_orders_by_rank_then_key() -> None:
    a = digest_candidate(_digest_row(3), rank=1)
    b = digest_candidate(_digest_row(2), rank=0)
    c = digest_candidate(_digest_row(1), rank=1)
    selected, truncated = select_unsent([a, b, c, a], set(), 10)
    assert [x.key for x in selected] == [b.key, c.key, a.key]
    assert truncated == 0


def test_plan_rejects_mixed_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        plan_send(_kevs(1), [], TODAY, kind=KIND_DIGEST)


@pytest.mark.parametrize(
    ("utc", "kst"),
    [
        (datetime(2026, 11, 8, 14, 59, 59, tzinfo=timezone.utc), date(2026, 11, 8)),
        (datetime(2026, 11, 8, 15, 0, 0, tzinfo=timezone.utc), date(2026, 11, 9)),
    ],
)
def test_today_kst_turns_at_15_utc(utc, kst) -> None:
    assert today_kst(utc) == kst


def test_today_kst_rejects_naive() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        today_kst(datetime(2026, 11, 8, 15, 0))  # noqa: DTZ001 — 의도한 naive


# ── 발신 기록(sqlite SqlCatalog) ─────────────────────────────────────────


@pytest.fixture
def send_log(tmp_path):
    catalog = SqlCatalog(
        "probe",
        uri=f"sqlite:///{tmp_path}/catalog.db",
        warehouse=f"file://{tmp_path}/warehouse",
    )
    return ensure_table(catalog, SEND_LOG_IDENTIFIER, SEND_LOG_SCHEMA)


class _Recorder:
    """가짜 웹훅. `fail_on`번째 호출에서 `error`를 올린다(None이면 항상 성공)."""

    def __init__(
        self, fail_on: int | None = None, error: Exception | None = None
    ) -> None:
        self.payloads: list[dict] = []
        self.calls = 0
        self.fail_on = fail_on
        self.error = error or RuntimeError("합성 발신 실패")

    def __call__(self, payload: dict) -> int:
        index = self.calls
        self.calls += 1
        if self.fail_on is not None and index >= self.fail_on:
            raise self.error
        self.payloads.append(payload)
        return 200

    @property
    def items(self) -> int:
        return sum(len(p["embeds"]) for p in self.payloads)


def _run(kind, candidates, table, post, now=NOW, seed=None):
    return run_send(
        kind, candidates, table=table, post=post, now=lambda: now, seed=seed
    )


def _statuses(table) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in read_send_log(table):
        out.setdefault(row["send_key"], []).append(row["status"])
    return out


def test_send_log_roundtrip_excludes_sent_keys(send_log) -> None:
    assert send_log.schema().find_field("send_key").required
    post = _Recorder()

    first = _run(KIND_DIGEST, _digests(3), send_log, post)
    assert first["seeded"]
    assert first["baseline"] == 3
    assert first["sent"] == 0
    assert post.payloads == []

    second = _run(KIND_DIGEST, _digests(5), send_log, post)
    assert second["sent"] == 2
    assert len(post.payloads) == 1

    statuses = _statuses(send_log)
    for candidate in _digests(2, start=4):
        # 스캔 순서는 보장되지 않는다 — 집합으로 본다
        assert sorted(statuses[candidate.key]) == [STATUS_PENDING, STATUS_SENT]
    assert all(r["kst_date"] == TODAY for r in read_send_log(send_log))

    third = _run(KIND_DIGEST, _digests(5), send_log, post)
    assert third["sent"] == 0
    assert len(post.payloads) == 1


def test_result_holds_counts_not_values(send_log) -> None:
    _run(KIND_DIGEST, [], send_log, _Recorder())
    result = _run(KIND_DIGEST, _digests(2), send_log, _Recorder())
    dumped = json.dumps(result, ensure_ascii=False)
    assert "글 1" not in dumped
    assert "news.hada.io" not in dumped
    assert "CVE-" not in dumped
    assert len(result["sent_keys_sha256"]) == 12
    assert set(result) == {
        "kind",
        "candidates",
        "seeded",
        "baseline",
        "already_logged",
        "sent",
        "rejected",
        "truncated",
        "sent_today_before",
        "daily_limit",
        "sent_keys_sha256",
    }


def test_send_failure_leaves_pending_and_blocks_next_run(send_log) -> None:
    """두 번째 메시지 발신이 실패하면 그 key는 짝 없는 pending으로 남는다."""
    _run(KIND_DIGEST, [], send_log, _Recorder())
    post = _Recorder(fail_on=1)
    with pytest.raises(RuntimeError, match="합성"):
        _run(KIND_DIGEST, _digests(15), send_log, post)
    statuses = _statuses(send_log)
    sent = [k for k, s in statuses.items() if STATUS_SENT in s]
    pending = unpaired_pending(read_send_log(send_log), KIND_DIGEST)
    assert len(sent) == MAX_EMBEDS_PER_MESSAGE  # 첫 메시지분은 확정
    assert len(pending) == 5

    again = _Recorder()
    with pytest.raises(RuntimeError, match="짝 없는 pending 5건") as exc:
        _run(KIND_DIGEST, _digests(30), send_log, again)
    assert again.calls == 0
    for candidate in _digests(15):  # 메시지에 키 원문이 없다(해시 접두만)
        assert candidate.key not in str(exc.value)


def test_record_failure_after_send_blocks_next_run(send_log, monkeypatch) -> None:
    """발신은 됐고 sent 기록이 실패하면 pending이 남아 다음 run이 보내지 않는다."""
    _run(KIND_DIGEST, [], send_log, _Recorder())
    real_append = alerts.append_rows

    def broken_sent(table, arrow, *args, **kwargs):
        if STATUS_SENT in arrow.column("status").to_pylist():
            message = "합성 append 실패"
            raise OSError(message)
        return real_append(table, arrow, *args, **kwargs)

    monkeypatch.setattr("lakehouse.alerts.append_rows", broken_sent)
    post = _Recorder()
    with pytest.raises(OSError, match="append"):
        _run(KIND_DIGEST, _digests(2), send_log, post)
    assert post.calls == 1
    with pytest.raises(RuntimeError, match="짝 없는 pending"):
        _run(KIND_DIGEST, _digests(5), send_log, post)
    assert post.calls == 1  # 중복 발신 없음


@pytest.mark.parametrize("failure", ["sent_append", "all_append", "timeout"])
def test_repeated_failures_never_exceed_daily_limit(
    send_log, monkeypatch, failure
) -> None:
    """기록 실패·타임아웃이 N회 반복돼도 하루 발신 항목은 상한을 넘지 않는다."""
    _run(KIND_DIGEST, [], send_log, _Recorder())  # 시드
    real_append = alerts.append_rows

    def flaky(table, arrow, *args, **kwargs):
        statuses = set(arrow.column("status").to_pylist())
        if failure == "all_append" or (
            failure == "sent_append" and STATUS_SENT in statuses
        ):
            message = "합성 append 실패"
            raise OSError(message)
        return real_append(table, arrow, *args, **kwargs)

    monkeypatch.setattr("lakehouse.alerts.append_rows", flaky)
    error = requests.Timeout("합성 타임아웃") if failure == "timeout" else None
    post = _Recorder(fail_on=0 if failure == "timeout" else None, error=error)
    for run in range(10):
        new = _digests(DAILY_SEND_LIMIT, start=1 + run * DAILY_SEND_LIMIT)
        with pytest.raises((OSError, RuntimeError, requests.Timeout)):
            _run(KIND_DIGEST, new, send_log, post)
    # 실제로 수신 측에 닿았을 수 있는 시도 = 성공 + 타임아웃(닿았는지 모른다)
    attempted = post.items + (MAX_EMBEDS_PER_MESSAGE if failure == "timeout" else 0)
    assert attempted <= DAILY_SEND_LIMIT
    assert post.calls <= 1


def test_many_runs_respect_daily_limit(send_log) -> None:
    """정상 경로 — 새 후보가 계속 와도 하루 합계는 상한에서 멈춘다(두 kind 합산)."""
    post = _Recorder()
    _run(KIND_DIGEST, [], send_log, post)
    _run(KIND_KEV, [], send_log, post, seed=KEV_SEED)
    for run in range(4):
        _run(KIND_DIGEST, _digests(7, start=1 + run * 7), send_log, post)
        _run(KIND_KEV, _kevs(7, start=1 + run * 7), send_log, post, seed=KEV_SEED)
    assert post.items == DAILY_SEND_LIMIT
    tomorrow = NOW + timedelta(days=1)
    _run(KIND_DIGEST, _digests(3, start=500), send_log, post, now=tomorrow)
    assert post.items == DAILY_SEND_LIMIT + 3


def test_rejected_400_is_closed_and_skipped(send_log, monkeypatch) -> None:
    """400이면 그 메시지 key를 rejected로 닫고 다음 메시지로 넘어간다."""
    _run(KIND_DIGEST, [], send_log, _Recorder())
    calls: list[dict] = []

    def post(payload):
        calls.append(payload)
        if len(calls) == 1:
            message = "https://discord.com/api/webhooks/*** POST 거절: HTTP 400"
            raise PayloadRejectedError(message)
        return 200

    result = _run(KIND_DIGEST, _digests(15), send_log, post)
    assert result["rejected"] == 10
    assert result["sent"] == 5
    assert len(calls) == 2
    log = read_send_log(send_log)
    assert unpaired_pending(log, KIND_DIGEST) == []
    assert count_today(log, TODAY) == 5  # rejected는 상한에 세지 않는다
    again = _run(KIND_DIGEST, _digests(15), send_log, post)
    assert again["sent"] == 0  # rejected도 다시 보내지 않는다
    assert len(calls) == 2


def test_close_pending_unblocks_kind(send_log) -> None:
    _run(KIND_DIGEST, [], send_log, _Recorder())
    with pytest.raises(RuntimeError):
        _run(KIND_DIGEST, _digests(2), send_log, _Recorder(fail_on=0))
    assert close_pending(send_log, KIND_DIGEST, STATUS_SENT, lambda: NOW) == 2
    result = _run(KIND_DIGEST, _digests(3), send_log, _Recorder())
    assert result["sent"] == 1
    with pytest.raises(ValueError, match="닫는 상태"):
        close_pending(send_log, KIND_DIGEST, STATUS_PENDING, lambda: NOW)


def test_watchlist_change_reseeds_kev_without_sending(send_log) -> None:
    """관심목록이 바뀌면 새로 일치한 기존 KEV는 baseline이 되고 보내지 않는다."""
    first_list = frozenset({("examplecorp", "widget server")})
    second_list = first_list | {("sampleco", "gate")}
    seed_a = seed_key(KIND_KEV, watchlist_digest(first_list))
    seed_b = seed_key(KIND_KEV, watchlist_digest(second_list))
    assert seed_a != seed_b
    post = _Recorder()

    _run(KIND_KEV, _kevs(2), send_log, post, seed=seed_a)  # 시드
    assert _run(KIND_KEV, _kevs(3), send_log, post, seed=seed_a)["sent"] == 1

    old_gate = [
        kev_candidate(_kev_row(i, kev_vendor_project="SampleCo", kev_product="Gate"))
        for i in range(100, 104)
    ]
    reseed = _run(KIND_KEV, _kevs(3) + old_gate, send_log, post, seed=seed_b)
    assert reseed["seeded"]
    assert reseed["sent"] == 0
    assert reseed["baseline"] == 4  # 이미 기록된 3건은 다시 시드하지 않는다
    after = _run(KIND_KEV, [*_kevs(4), *old_gate], send_log, post, seed=seed_b)
    assert after["sent"] == 1  # 그 뒤 새 KEV만
    assert post.items == 2

    keys = {row["send_key"] for row in read_send_log(send_log)}
    assert seed_b in keys
    for word in ["sampleco", "gate", "examplecorp", "widget"]:
        assert all(word not in key.lower() for key in keys)


def test_watchlist_digest_is_normalized() -> None:
    a = frozenset({("a", "b"), ("c", "d")})
    assert watchlist_digest(a) == watchlist_digest(frozenset({("c", "d"), ("a", "b")}))
    assert watchlist_digest(a) != watchlist_digest(frozenset({("a", "b")}))
    assert len(watchlist_digest(a)) == 12


def test_load_watchlist_normalizes_before_hashing(tmp_path) -> None:
    upper = tmp_path / "u.yaml"
    upper.write_text("products:\n  - {vendor: ' A ', product: B}\n", encoding="utf-8")
    lower = tmp_path / "l.yaml"
    lower.write_text("products:\n  - {vendor: a, product: b}\n", encoding="utf-8")
    left = watchlist_digest(load_watchlist(upper))
    assert left == watchlist_digest(load_watchlist(lower))


# ── 관심목록 ─────────────────────────────────────────────────────────────


def test_example_watchlist_loads() -> None:
    watchlist = load_watchlist(REPO_ROOT / "scripts" / "watchlist.example.yaml")
    assert ("examplecorp", "widget_server") in watchlist
    assert ("examplecorp", "widget server") in watchlist


@pytest.mark.parametrize(
    "text",
    [
        "",  # 빈 Secret이 마운트된 경우
        "products: []\n",
        "products:\n",
        "{}\n",
        "items:\n  - {vendor: a, product: b}\n",
        "products:\n  - {vendor: a}\n",
        "products:\n  - {vendor: a, product: b, note: c}\n",
        "products:\n  - {vendor: 1, product: b}\n",
        "products:\n  - {vendor: '  ', product: b}\n",
        "products: [unclosed\n",
        "products:\n  - {vendor: a, product: b}\nextra: 1\n",
    ],
)
def test_bad_watchlist_fails_closed(tmp_path, text) -> None:
    path = tmp_path / "w.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="관심목록"):
        load_watchlist(path)


def test_missing_watchlist_fails_closed(tmp_path) -> None:
    with pytest.raises(ValueError, match="관심목록"):
        load_watchlist(tmp_path / "없음.yaml")


def test_watchlist_matches_cpe_or_kev_case_insensitive() -> None:
    watch = frozenset({("examplecorp", "widget_server"), ("sampleco", "gate")})
    assert matches_watchlist(["ExampleCorp:Widget_Server"], None, None, watch)
    assert matches_watchlist([], "SampleCo", "Gate", watch)
    assert not matches_watchlist(["examplecorp:other"], "x", "y", watch)
    assert not matches_watchlist(["examplecorp"], None, None, watch)


def test_build_candidates_filters_ranks_and_counts_rejects() -> None:
    watch = frozenset({("examplecorp", "widget_server")})
    rows = [
        _digest_row(1),
        _digest_row(2, cpe_vendor_products=["examplecorp:widget_server"]),
        _digest_row(3, cve_id="CVE-26-1"),
        _digest_row(4, link="javascript:alert(1)"),
    ]
    candidates, counts = build_candidates(
        rows, digest_candidate, watch, watchlist_only=False
    )
    assert counts == {"rows": 4, "rejected": 2, "matched": 1}
    assert [c.rank for c in candidates] == [1, 0]

    kev_rows = [_kev_row(1), _kev_row(2, cpe_vendor_products=[], kev_product="X")]
    kev, kev_counts = build_candidates(
        kev_rows, kev_candidate, watch, watchlist_only=True
    )
    assert [c.key for c in kev] == ["kev:CVE-2026-20001"]
    assert kev_counts["matched"] == 1


# ── 웹훅 URL 마스킹·검증 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "masked"),
    [
        (WEBHOOK, "https://discord.com/api/webhooks/***"),
        (WEBHOOK + "?wait=true", "https://discord.com/api/webhooks/***"),
        (f"https://u:{TOKEN}@discord.com/x/{TOKEN}", "https://discord.com/***"),
        ("not a url", "***"),
    ],
)
def test_mask_webhook_hides_path(url, masked) -> None:
    assert mask_webhook(url) == masked
    assert TOKEN not in mask_webhook(url)


# 정상 꼴 — 이 셀이 통과해야 아래 거부 셀의 ValueError가 「모두 거부」가 아님을 보인다
GOOD_WEBHOOKS = [
    WEBHOOK,
    "https://discord.com/api/webhooks/1/a-b_C9",
]
# 수신자 검사(k8s-secrets.sh 형식 가드와 같은 호스트 하나 + 경로 꼴)
BAD_WEBHOOKS = {
    "discordapp_host": f"https://discordapp.com/api/webhooks/123456789/{TOKEN}",
    "http_scheme": f"http://discord.com/api/webhooks/123456789/{TOKEN}",
    "other_host": f"https://evil.example/api/webhooks/123456789/{TOKEN}",
    "suffix_host": f"https://discord.com.evil.example/api/webhooks/1/{TOKEN}",
    "subdomain_host": f"https://canary.discord.com/api/webhooks/1/{TOKEN}",
    "upper_host": f"https://DISCORD.COM/api/webhooks/1/{TOKEN}",
    "port": f"https://discord.com:8443/api/webhooks/1/{TOKEN}",
    "userinfo": f"https://u:p@discord.com/api/webhooks/1/{TOKEN}",
    "path_other": f"https://discord.com/other/1/{TOKEN}",
    "path_non_numeric_id": f"https://discord.com/api/webhooks/abc/{TOKEN}",
    "path_no_token": "https://discord.com/api/webhooks/123456789",
    "path_empty_token": "https://discord.com/api/webhooks/123456789/",
    "path_extra_segment": f"https://discord.com/api/webhooks/1/{TOKEN}/github",
    "path_bad_token_char": "https://discord.com/api/webhooks/1/tok.en",
    "query": f"{WEBHOOK}?thread_id=1",
    "fragment": f"{WEBHOOK}#x",
    "empty": "",
}


@pytest.mark.parametrize("url", GOOD_WEBHOOKS)
def test_validate_webhook_url_accepts_discord_webhook(url) -> None:
    validate_webhook_url(url)


@pytest.mark.parametrize("case", sorted(BAD_WEBHOOKS))
def test_validate_webhook_url_rejects(case) -> None:
    with pytest.raises(ValueError, match="discord_webhook") as exc:
        validate_webhook_url(BAD_WEBHOOKS[case])
    assert TOKEN not in str(exc.value)


def test_webhook_hosts_match_secrets_guard() -> None:
    """허용 호스트는 k8s-secrets.sh 형식 가드의 접두 하나와 같다(읽기 전용 대조)."""
    guard = (REPO_ROOT / "scripts" / "k8s-secrets.sh").read_text(encoding="utf-8")
    assert "https://discord.com/api/webhooks/*" in guard  # 가드 문자열이 실재한다
    assert frozenset({"discord.com"}) == alerts.WEBHOOK_HOSTS


# ── post_json(재시도·마스킹·리다이렉트) ──────────────────────────────────


class _Resp:
    def __init__(
        self, status: int, body: Any = None, headers: dict | None = None
    ) -> None:
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        # requests와 같다 — 3xx도 ok=True다(성공 판정에 ok를 쓰면 안 되는 이유)
        self.ok = status < 400

    def json(self) -> Any:
        if self._body is None:
            message = "본문 없음"
            raise ValueError(message)
        return self._body


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr("lakehouse.http.time.sleep", recorded.append)
    return recorded


def _install(monkeypatch, responses: list[Any]) -> list[dict]:
    calls: list[dict] = []
    queue = list(responses)

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("lakehouse.http.requests.post", fake_post)
    return calls


def test_post_json_adds_wait_and_disables_redirects(monkeypatch, sleeps) -> None:
    calls = _install(monkeypatch, [_Resp(200, {"id": "1"})])
    assert post_json(WEBHOOK, {"content": "x"}, mask=mask_webhook) == 200
    assert calls[0]["url"] == WEBHOOK + "?wait=true"
    assert calls[0]["json"] == {"content": "x"}
    assert calls[0]["allow_redirects"] is False
    assert sleeps == []


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_fails_once_without_follow(monkeypatch, sleeps, status) -> None:
    calls = _install(
        monkeypatch, [_Resp(status, None, {"Location": "https://e.example/"})]
    )
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        post_json(WEBHOOK, {}, mask=mask_webhook)
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [201, 202, 204])
def test_non_200_success_is_not_confirmation(monkeypatch, sleeps, status) -> None:
    """`?wait=true`의 확인은 200뿐이다 — 다른 2xx는 생성 확인이 없어 실패다."""
    calls = _install(monkeypatch, [_Resp(status), _Resp(200, {})])
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        post_json(WEBHOOK, {}, mask=mask_webhook)
    assert len(calls) == 1


def test_400_raises_payload_rejected_without_retry(monkeypatch, sleeps) -> None:
    calls = _install(monkeypatch, [_Resp(400, {"message": WEBHOOK}), _Resp(200, {})])
    with pytest.raises(PayloadRejectedError, match="HTTP 400") as exc:
        post_json(WEBHOOK, {}, mask=mask_webhook)
    assert len(calls) == 1
    assert TOKEN not in str(exc.value)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_and_missing_fail_once_without_retry(monkeypatch, sleeps, status) -> None:
    calls = _install(monkeypatch, [_Resp(status), _Resp(200, {})])
    with pytest.raises(RuntimeError, match=f"HTTP {status}") as exc:
        post_json(WEBHOOK, {}, mask=mask_webhook)
    assert not isinstance(exc.value, PayloadRejectedError)  # rejected로 닫지 않는다
    assert len(calls) == 1
    assert sleeps == []


def test_429_honors_body_retry_after(monkeypatch, sleeps) -> None:
    calls = _install(monkeypatch, [_Resp(429, {"retry_after": 2.5}), _Resp(200, {})])
    assert post_json(WEBHOOK, {}, mask=mask_webhook) == 200
    assert len(calls) == 2
    assert sleeps == [2.5]


def test_429_honors_retry_after_header(monkeypatch, sleeps) -> None:
    _install(monkeypatch, [_Resp(429, None, {"Retry-After": "3"}), _Resp(200, {})])
    post_json(WEBHOOK, {}, mask=mask_webhook)
    assert sleeps == [3.0]


def test_429_over_cap_fails_without_waiting(monkeypatch, sleeps) -> None:
    calls = _install(
        monkeypatch, [_Resp(429, {"retry_after": MAX_RETRY_AFTER_S + 1}), _Resp(200)]
    )
    with pytest.raises(RuntimeError, match="429"):
        post_json(WEBHOOK, {}, mask=mask_webhook)
    assert len(calls) == 1
    assert sleeps == []


def test_5xx_backs_off_exponentially(monkeypatch, sleeps) -> None:
    calls = _install(monkeypatch, [_Resp(502), _Resp(503), _Resp(200, {})])
    post_json(WEBHOOK, {}, mask=mask_webhook)
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_connection_error_is_not_retried(monkeypatch, sleeps) -> None:
    calls = _install(monkeypatch, [requests.ConnectionError(WEBHOOK), _Resp(200)])
    with pytest.raises(RuntimeError, match="ConnectionError"):
        post_json(WEBHOOK, {}, mask=mask_webhook)
    assert len(calls) == 1


FAILURES = {
    "302": [_Resp(302, None, {"Location": WEBHOOK})],
    "400": [_Resp(400, {"message": WEBHOOK})],
    "403": [_Resp(403, {"message": WEBHOOK})],
    "404": [_Resp(404, {"message": WEBHOOK})],
    "429": [_Resp(429, {"retry_after": 0.1})] * 4,
    "5xx": [_Resp(500)] * 4,
    "ConnectionError": [requests.ConnectionError(f"Max retries url: {WEBHOOK}")],
}


def _token_hits(exc: BaseException, log_text: str) -> int:
    rendered = "".join(traceback.format_exception(exc))
    parts = [str(exc), repr(exc), rendered, log_text]
    parts += [repr(exc.__cause__), repr(exc.__context__)]
    return sum(part.count(TOKEN) for part in parts)


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_failures_never_carry_token(monkeypatch, sleeps, caplog, case) -> None:
    caplog.set_level(logging.DEBUG)
    logging.getLogger("probe").debug("관측 경로 생존")
    _install(monkeypatch, list(FAILURES[case]))
    with pytest.raises(RuntimeError) as exc:
        post_json(WEBHOOK, {}, mask=mask_webhook)
    assert "관측 경로 생존" in caplog.text  # 캡처가 살아 있었다
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert _token_hits(exc.value, caplog.text) == 0
    assert "https://discord.com/api/webhooks/***" in str(exc.value)


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_control_unmasked_failure_carries_token(monkeypatch, sleeps, case) -> None:
    """대조군 — 마스킹을 끄면 같은 경로에서 토큰이 1건 이상 나온다."""
    _install(monkeypatch, list(FAILURES[case]))
    with pytest.raises(RuntimeError) as exc:
        post_json(WEBHOOK, {}, mask=lambda url: url)
    assert _token_hits(exc.value, "") >= 1


class _Ok(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = b'{"id": "1"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


@pytest.fixture
def loopback_url():
    server = HTTPServer(("127.0.0.1", 0), _Ok)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/api/webhooks/1/{TOKEN}"
    server.shutdown()
    server.server_close()


def test_urllib3_debug_log_does_not_carry_token(loopback_url, caplog) -> None:
    """실제 urllib3 경로(루프백 스텁)에서 DEBUG 요청 줄에 토큰이 남지 않는다.

    대조: 같은 URL을 requests로 직접 보내면 urllib3 DEBUG 줄에 토큰이 남는다.
    """
    caplog.set_level(logging.DEBUG)
    requests.post(loopback_url, json={}, timeout=5)
    assert TOKEN in caplog.text  # 대조군 — 관측 경로가 산다
    caplog.clear()
    assert post_json(loopback_url, {}, mask=mask_webhook) == 200
    assert TOKEN not in caplog.text


# ── 상수 고정(환경·재대입으로 덮어쓰지 않는다) ───────────────────────────


FORBIDDEN_NAMES = {"environ", "getenv", "Variable", "Param", "putenv"}
PINNED = {"DAILY_SEND_LIMIT", "ALLOWED_FIELDS"}


def _env_reads(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.add(node.attr)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            modules = [a.name for a in node.names]
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            found |= {m for m in modules if m.split(".")[0] in {"os", "airflow"}}
            found |= {a.name for a in node.names if a.name in FORBIDDEN_NAMES}
    return found


def test_alerts_source_reads_no_environment() -> None:
    source = (LAKEHOUSE_DIR / "alerts.py").read_text(encoding="utf-8")
    assert _env_reads(source) == set()
    # 대조군 — 검사기가 실제로 잡는다
    assert _env_reads("import os\nX = os.environ.get('L')") == {"os", "environ"}
    assert _env_reads("from airflow.sdk import Variable") >= {"Variable"}


def _target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Attribute):
        return {target.attr}
    if isinstance(target, ast.Tuple | ast.List):
        return set().union(*(_target_names(e) for e in target.elts))
    return set()


def _rebind_kind(node: ast.AST) -> str | None:
    """노드 하나가 PINNED 이름을 바꾸는 형태면 그 이름(Assign은 "Assign")."""
    if isinstance(node, ast.Assign):
        hit = any(_target_names(t) & PINNED for t in node.targets)
        return "Assign" if hit else None
    if isinstance(node, ast.AugAssign | ast.AnnAssign):
        return type(node).__name__ if _target_names(node.target) & PINNED else None
    if isinstance(node, ast.Global | ast.Nonlocal):
        return type(node).__name__ if set(node.names) & PINNED else None
    is_call = isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    if is_call and node.func.id in {"globals", "vars", "setattr"}:
        return f"{node.func.id}()"
    return None


def _rebinds(source: str) -> list[str]:
    """PINNED 이름의 재대입·우회 수단을 찾는다(모듈 수준 Assign 1회씩만 허용)."""
    kinds = [k for k in map(_rebind_kind, ast.walk(ast.parse(source))) if k]
    problems = [k for k in kinds if k != "Assign"]
    assigns = kinds.count("Assign")
    if assigns != len(PINNED):
        problems.append(f"Assign x{assigns}")
    return problems


def test_limit_and_whitelist_are_literal_assignments() -> None:
    source = (LAKEHOUSE_DIR / "alerts.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    limit = assigned["DAILY_SEND_LIMIT"]
    assert isinstance(limit, ast.Constant)
    assert isinstance(limit.value, int)
    fields = assigned["ALLOWED_FIELDS"]
    assert isinstance(fields, ast.Call)
    assert all(isinstance(e, ast.Constant) for e in fields.args[0].elts)
    assert _rebinds(source) == []


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("DAILY_SEND_LIMIT += 9", "AugAssign"),
        ("ALLOWED_FIELDS: frozenset = frozenset()", "AnnAssign"),
        ("globals()['DAILY_SEND_LIMIT'] = 99", "globals()"),
        ("def f():\n    global DAILY_SEND_LIMIT\n    DAILY_SEND_LIMIT = 1", "Global"),
        ("DAILY_SEND_LIMIT = 30", "Assign x3"),
    ],
)
def test_rebind_detector_catches_each_form(snippet, expected) -> None:
    """대조군 — 원본 소스에 각 형태를 덧붙이면 검사기가 잡는다."""
    source = (LAKEHOUSE_DIR / "alerts.py").read_text(encoding="utf-8")
    assert expected in _rebinds(source + "\n" + snippet + "\n")


def _dag_files() -> list[Path]:
    files = sorted(p for p in DAGS_DIR.rglob("*.py") if "__pycache__" not in p.parts)
    # 모집단이 DAG 폴더 전체다(하위 lakehouse 포함) — 비면 판정 무효
    assert DAGS_DIR / "nvd_kev.py" in files
    assert LAKEHOUSE_DIR / "iceberg.py" in files
    return files


def _rel(path: Path) -> str:
    return path.relative_to(DAGS_DIR).as_posix()


def test_pinned_names_appear_only_in_alerts_module() -> None:
    """두 상수 이름은 lakehouse/alerts.py 밖(DAG 포함)에 나오지 않는다(재할당 방지)."""
    hits = {
        _rel(path)
        for path in _dag_files()
        if any(name in path.read_text(encoding="utf-8") for name in PINNED)
    }
    assert hits == {"lakehouse/alerts.py"}


# ── 발신 경로·Connection·Pool 사용처(risk.md §4 예외 ② 조건 1) ──────────

# 따옴표·API와 무관한 부분 문자열이다. `webhook`은 `"discord_" + "webhook"`처럼
# 이름을 쪼갠 우회도 잡는다(쪼갠 조각이 이 문자열을 남기는 한).
SEND_NEEDLES = (
    "discord_webhook",
    "AIRFLOW_CONN_DISCORD",
    "webhook",
    "post_json",
    "run_send",
)
# 근거: 두 DAG(사용처), alerts.py(검증·마스킹·run_send 정의), http.py(post_json 정의)
SEND_FILES = {
    "alerts_digest.py",
    "alerts_kev.py",
    "lakehouse/alerts.py",
    "lakehouse/http.py",
}


def _files_with(needle: str) -> set[str]:
    lowered = needle.lower()
    return {
        _rel(path)
        for path in _dag_files()
        if lowered in path.read_text(encoding="utf-8").lower()
    }


@pytest.mark.parametrize("needle", SEND_NEEDLES)
def test_send_path_strings_only_in_send_files(needle) -> None:
    hits = _files_with(needle)
    # AIRFLOW_CONN_DISCORD는 dags/에 0건이 정상이다(값은 차트 env에만 있다).
    # 나머지는 반드시 있어야 할 문자열이라 0건이면 탐색이 죽은 것이다
    if needle != "AIRFLOW_CONN_DISCORD":
        assert hits
    assert hits <= SEND_FILES


def test_send_path_needles_control() -> None:
    """대조군 — 같은 탐색이 흔한 Connection 이름은 여러 파일에서 찾는다."""
    assert len(_files_with("iceberg_catalog")) > len(SEND_FILES)


def test_send_files_have_no_callback() -> None:
    """발신 파일에 콜백이 없다(`callback` 부분 문자열 — on_*_callback·callbacks= 등)."""
    for name in SEND_FILES:
        assert "callback" not in (DAGS_DIR / name).read_text(encoding="utf-8").lower()


def test_discord_pool_used_only_by_send_dags() -> None:
    pattern = re.compile(r"pool\s*=\s*['\"]discord_webhook['\"]")
    hits = {
        _rel(path)
        for path in _dag_files()
        if pattern.search(path.read_text(encoding="utf-8"))
    }
    assert hits == {"alerts_digest.py", "alerts_kev.py"}


# 발신 기록 테이블을 다른 파일이 직접 쓰면(이름 문자열 또는 상수 import) 상한·중복
# 판정을 우회하는 기록이 생긴다. 허용 파일과 근거:
# - lakehouse/alerts.py — 테이블 이름 정의·기록 함수
# - alerts_digest.py — 독스트링이 테이블 이름을 적는다(코드 아님, 문구 이동은 범위 밖)
#   · 상수 SEND_LOG_IDENTIFIER로 ensure_table을 부른다
# - alerts_kev.py — 상수 SEND_LOG_IDENTIFIER로 ensure_table을 부른다
SEND_LOG_ALLOWED = {
    "discord_sends": {"lakehouse/alerts.py", "alerts_digest.py"},
    "SEND_LOG_IDENTIFIER": {"lakehouse/alerts.py", "alerts_digest.py", "alerts_kev.py"},
}


@pytest.mark.parametrize("needle", sorted(SEND_LOG_ALLOWED))
def test_send_log_table_only_in_allowed_files(needle) -> None:
    hits = _files_with(needle)
    # 대조군 — 정의 파일에서는 출현을 실제로 잡는다(탐색이 살아 있다)
    assert "lakehouse/alerts.py" in hits
    assert hits <= SEND_LOG_ALLOWED[needle]


def test_close_pending_is_not_called_from_dags() -> None:
    """`close_pending`은 사람의 수동 절차다 — 정의(alerts.py) 밖 dags/ 전체에서 0건이다.

    pending을 자동으로 닫으면 「나갔는지 모르는」 항목을 사람 판정 없이 확정하게 된다.
    """
    hits = _files_with("close_pending")
    # 대조군 — 같은 탐색이 정의 파일에서는 출현을 실제로 잡는다
    assert "lakehouse/alerts.py" in hits
    assert hits == {"lakehouse/alerts.py"}
    # 정의 파일 안에서도 호출은 없다(정의 1개만). 안내 문자열은 호출이 아니다
    tree = ast.parse((LAKEHOUSE_DIR / "alerts.py").read_text(encoding="utf-8"))
    names = [
        getattr(node.func, "id", getattr(node.func, "attr", ""))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    ]
    defs = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "close_pending"
    ]
    assert defs == ["close_pending"]
    assert "close_pending" not in names


# ── trino 헬퍼 ──────────────────────────────────────────────────────────


def test_query_rows_maps_columns_and_closes(monkeypatch) -> None:
    closed: list[bool] = []

    class _Cursor:
        description = (("cve_id",), ("is_kev",))

        def execute(self, sql: str) -> None:
            self.sql = sql

        def fetchall(self) -> list[tuple]:
            return [("CVE-2026-1", True)]

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

        def close(self) -> None:
            closed.append(True)

    seen: dict = {}

    def connect(**kwargs):
        seen.update(kwargs)
        return _Conn()

    fake = types.ModuleType("trino")
    fake.dbapi = types.SimpleNamespace(connect=connect)
    monkeypatch.setitem(sys.modules, "trino", fake)
    monkeypatch.setenv("TRINO_HOST", "trino.test")
    monkeypatch.delenv("TRINO_PORT", raising=False)

    from lakehouse.trino import query_rows

    assert query_rows("select 1") == [{"cve_id": "CVE-2026-1", "is_kev": True}]
    assert seen == {
        "host": "trino.test",
        "port": 8080,
        "user": "airflow",
        "catalog": "iceberg",
    }
    assert closed == [True]
