"""Discord 발신 — 화이트리스트 메시지·멘션 중화·상한·발신 기록 판정.

Airflow를 import하지 않는다(DAG `alerts_digest`·`alerts_kev`는 배선만 한다).

🔴 **통제 배선 파일**이다(docs/risk.md §4 예외 ②, docs/conventions/agents.md).
`ALLOWED_FIELDS`·`DAILY_SEND_LIMIT`를 바꾸는 PR은 예외의 범위를 바꾸는
PR이다 — 일반 리뷰가 아니라 `reviewer` 체크리스트 E와 사용자 승인을 거친다.

- 필드 정본은 risk.md §4 예외 ② 조건 2다. 이 모듈은 그 목록을 코드로 옮겼다.
- 두 상수는 **코드 리터럴**이다. env·Airflow Variable·Param으로 덮어쓰지
  않는다. 이 모듈은 환경을 읽지 않는다(`test_alerts.py`가 소스를 검사한다).
  두 이름은 이 모듈 밖(DAG 포함)에 나오지 않는다(같은 테스트가 센다).
- NVD `description`은 싣지 않는다. NVD를 출처로 표기하는 문구도 없다.
  링크 문구는 「상세」로 고정한다.
- 원천 값(제목·링크)·웹훅 URL·관심목록 원문은 로그·반환값·기록에 남기지
  않는다. 건수와 키 해시 접두만 남긴다.
"""

import dataclasses
import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pyarrow as pa
import yaml
from pyiceberg.table import Table

from lakehouse.http import PayloadRejectedError
from lakehouse.iceberg import append_rows

# 🔴 메시지에 실을 수 있는 필드의 전부다(risk.md §4 예외 ② 조건 2와 같은 집합).
ALLOWED_FIELDS = frozenset(
    {
        "cve_id",
        "cvss_score",
        "cvss_severity",
        "is_kev",
        "kev_vendor_project",
        "kev_product",
        "geeknews_title",
        "geeknews_link",
        "nvd_url",
    }
)
# 🔴 단위: KST 하루(0시~24시)에 두 DAG(`alerts_digest`·`alerts_kev`)가
#    **합해서** 발신을 시도한 **항목**(send_key) 수다. 항목 1개 = embed 1개.
#    세는 행은 오늘 KST의 `pending`·`sent`이고, `rejected`(400)로 닫힌 키와
#    `baseline`은 세지 않는다. 메시지(POST) 수가 아니다.
DAILY_SEND_LIMIT = 20

KIND_DIGEST = "digest"
KIND_KEV = "kev"
# 메시지 본문은 고정 문자열뿐이다 — 외부 텍스트를 content에 넣지 않는다
CONTENT_BY_KIND = {
    KIND_DIGEST: "GeekNews × CVE 다이제스트",  # noqa: RUF001 — 의도한 곱셈 기호
    KIND_KEV: "관심 제품 신규 KEV",
}
CVE_ID_PATTERN = re.compile(r"^CVE-\d{4}-\d{4,}$")
NVD_DETAIL_BASE = "https://nvd.nist.gov/vuln/detail/"
DETAIL_LABEL = "상세"
UNKNOWN_LABEL = "모름"
ALLOWED_LINK_SCHEMES = frozenset({"https", "http"})
# 🔴 수신자 검사 — `scripts/k8s-secrets.sh` 형식 가드와 같은 호스트 하나만 받는다.
#    넓히면(예: discordapp.com) 수신자 검사가 느슨한 쪽으로 어긋난다(예외 범위 변경)
WEBHOOK_HOSTS = frozenset({"discord.com"})
WEBHOOK_PATH_PREFIX = "/api/webhooks/"
# `/api/webhooks/<숫자 id>/<토큰>` — 토큰 문자는 영숫자·`-`·`_`
WEBHOOK_PATH_PATTERN = re.compile(r"/api/webhooks/\d+/[A-Za-z0-9_-]+")

# Discord Embed Limits — embed 수·글자 합계는 메시지 단위다.
# 글자는 모두 UTF-16 코드 유닛(`_units`)으로 센다(칸별 자르기 포함).
MAX_EMBEDS_PER_MESSAGE = 10
MAX_EMBED_CHARS_PER_MESSAGE = 6000
TITLE_MAX = 256
FIELD_VALUE_MAX = 1024
SEVERITY_MAX = 32
# 벤더/제품은 「벤더 / 제품」 한 칸이라 각각을 절반 아래로 자른다
KEV_NAME_MAX = 500

# embed의 각 칸이 어느 허용 필드에서 오는지.
# 칸을 더하면 이 표와 ALLOWED_FIELDS 대조(아래 import 게이트)에 걸린다.
EMBED_SOURCES: dict[str, frozenset[str]] = {
    "title": frozenset({"geeknews_title", "cve_id"}),
    "url": frozenset({"geeknews_link", "nvd_url"}),
    "CVE": frozenset({"cve_id"}),
    "CVSS": frozenset({"cvss_score", "cvss_severity"}),
    "KEV": frozenset({"is_kev"}),
    "벤더/제품": frozenset({"kev_vendor_project", "kev_product"}),
    DETAIL_LABEL: frozenset({"nvd_url"}),
}
EMBED_KEYS = frozenset({"title", "url", "fields"})
PAYLOAD_KEYS = frozenset({"content", "embeds", "allowed_mentions"})

SEND_LOG_IDENTIFIER = "alerts.discord_sends"
# 발신 직전에 남기는 예약 — sent·rejected 행이 짝으로 닫는다
STATUS_PENDING = "pending"
STATUS_SENT = "sent"
# Discord가 400으로 거절한 항목 — 다시 보내지 않고 상한에도 세지 않는다
STATUS_REJECTED = "rejected"
STATUS_BASELINE = "baseline"
CLOSING_STATUSES = frozenset({STATUS_SENT, STATUS_REJECTED})
# kind별 「처음 켰다」 표지. kev는 관심목록 해시를 덧붙인다(seed_key)
SEED_KEY_SUFFIX = ":__seed__"
KST = ZoneInfo("Asia/Seoul")
SEND_LOG_SCHEMA = pa.schema(
    [
        pa.field("send_key", pa.string(), nullable=False),
        ("kind", pa.string()),
        ("status", pa.string()),
        ("sent_at", pa.timestamp("us", tz="UTC")),
        ("kst_date", pa.date32()),
    ]
)
KEY_DIGEST_PREFIX = 12

# 멘션 시작 문자 뒤에 끼우는 폭 없는 공백(U+200B)
ZWSP = "​"
# 마크다운 특수문자. 역슬래시를 맨 앞에 둔다(먼저 escape해야 이중이 안 된다)
MARKDOWN_SPECIAL = "\\*_~`|>[]()#"
ELLIPSIS = "…"


@dataclass(frozen=True)
class AlertItem:
    """메시지 1항목 — 필드 집합이 정확히 `ALLOWED_FIELDS`다(import 때 검사).

    `nvd_url`은 외부 입력을 받지 않는다 — `cve_id`로만 만든다(`init=False`).
    """

    cve_id: str
    cvss_score: float | None = None
    cvss_severity: str | None = None
    is_kev: bool | None = None
    kev_vendor_project: str | None = None
    kev_product: str | None = None
    geeknews_title: str | None = None
    geeknews_link: str | None = None
    nvd_url: str = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        """CVE ID·링크 scheme을 검증하고 NVD 링크를 만든다. 어긋나면 ValueError."""
        # fullmatch — `match`는 `$`가 끝 개행 앞에서도 맞아 "CVE-…\n"을 통과시킨다
        cve_ok = isinstance(self.cve_id, str) and CVE_ID_PATTERN.fullmatch(self.cve_id)
        if not cve_ok:
            message = "cve_id 형식이 CVE-YYYY-NNNN+가 아니다"
            raise ValueError(message)
        if self.geeknews_link is not None:
            parts = urlsplit(self.geeknews_link)
            if parts.scheme not in ALLOWED_LINK_SCHEMES or not parts.netloc:
                message = "geeknews_link는 http(s) 절대 URL만 허용한다"
                raise ValueError(message)
        object.__setattr__(self, "nvd_url", NVD_DETAIL_BASE + self.cve_id)


# 🔴 화이트리스트 드리프트 게이트. `assert`가 아니라 raise다
#    (`python -O`에서도 꺼지지 않는다). 필드를 더하면 import가 실패한다.
_ITEM_FIELDS = frozenset(f.name for f in dataclasses.fields(AlertItem))
if _ITEM_FIELDS != ALLOWED_FIELDS:
    _drift = sorted(_ITEM_FIELDS ^ ALLOWED_FIELDS)
    raise ImportError(f"AlertItem 필드가 ALLOWED_FIELDS와 다르다: {_drift}")  # noqa: EM102
if frozenset().union(*EMBED_SOURCES.values()) != ALLOWED_FIELDS:
    raise ImportError("EMBED_SOURCES가 ALLOWED_FIELDS와 다르다")  # noqa: EM101


@dataclass(frozen=True)
class Candidate:
    """발신 후보 — 메시지 항목에 발신 키·종류·정렬 순위를 붙인다.

    `key`·`kind`·`rank`는 **메시지에 싣지 않는다**(기록·정렬용).
    `rank`는 작을수록 앞이다(관심목록 일치 = 0).
    """

    key: str
    kind: str
    item: AlertItem
    rank: int = 1

    def __post_init__(self) -> None:
        """종류별 필수 값을 검사한다. 어긋나면 ValueError."""
        if self.kind == KIND_DIGEST:
            if not (self.item.geeknews_title and self.item.geeknews_link):
                message = "digest 후보는 GeekNews 제목·링크가 있어야 한다"
                raise ValueError(message)
        elif self.kind == KIND_KEV:
            if self.item.is_kev is not True:
                message = "kev 후보는 is_kev가 참이어야 한다"
                raise ValueError(message)
        else:
            message = f"알 수 없는 kind: {self.kind}"
            raise ValueError(message)


@dataclass(frozen=True)
class SendPlan:
    """한 run의 발신 계획(값 없이 키만 담는다)."""

    kind: str
    candidates: int
    seeded: bool
    baseline_keys: tuple[str, ...]
    to_send: tuple[Candidate, ...]
    truncated: int
    already_logged: int
    sent_today: int
    unpaired: tuple[str, ...] = ()


def digest_key(topic_id: int, cve_id: str) -> str:
    """다이제스트 발신 키 `<topic_id>:<cve_id>`."""
    return f"{int(topic_id)}:{cve_id}"


def kev_key(cve_id: str) -> str:
    """KEV 발신 키 `kev:<cve_id>`."""
    return f"kev:{cve_id}"


def seed_key(kind: str, watchlist_hash: str | None = None) -> str:
    """kind별 시드 표지 키. 관심목록 해시가 있으면 덧붙인다(kev).

    실제 키와 겹치지 않는다(topic_id는 정수, CVE ID는 형식 검증).
    해시가 바뀌면 표지가 새 키가 되어 다시 시드한다(plan_send).
    """
    key = kind + SEED_KEY_SUFFIX
    return f"{key}:{watchlist_hash}" if watchlist_hash else key


def watchlist_digest(watchlist: frozenset[tuple[str, str]]) -> str:
    """관심목록(정규화된 소문자 쌍)의 sha256 접두. 순서·중복·대소문자와 무관하다."""
    lines = "\n".join(f"{v}\t{p}" for v, p in sorted(watchlist)).encode()
    return hashlib.sha256(lines).hexdigest()[:KEY_DIGEST_PREFIX]


def _units(text: str) -> int:
    """Discord 글자 수의 보수적 상한 — UTF-16 코드 유닛 수.

    ⚠️ Discord가 무엇으로 세는지는 미확인이다. UTF-16 유닛 수는 코드 포인트
    수 이상이라 어느 쪽이어도 한도를 넘지 않는다.
    """
    return len(text.encode("utf-16-le")) // 2


def _clip_units(text: str, limit: int) -> str:
    """UTF-16 유닛 `limit` 이하로 자른다. 자르면 끝에 말줄임표(1유닛)를 붙인다.

    자른 자리가 escape 한가운데면 외톨이 역슬래시를 버린다.
    """
    if _units(text) <= limit:
        return text
    budget = limit - _units(ELLIPSIS)
    kept: list[str] = []
    used = 0
    for ch in text:
        width = _units(ch)
        if used + width > budget:
            break
        kept.append(ch)
        used += width
    cut = "".join(kept)
    trailing = len(cut) - len(cut.rstrip("\\"))
    if trailing % 2 == 1:
        cut = cut[:-1]
    return cut + ELLIPSIS


def neutralize_mentions(text: str, limit: int | None = None) -> str:
    """외부 텍스트를 멘션·마크다운이 동작하지 않는 문자열로 바꾼다.

    `allowed_mentions={"parse": []}`(API 의미론)와 **독립인** 두 번째 방어다.

    - 제어 문자(개행·탭 등)는 공백으로 바꾼다.
    - 마크다운 특수문자를 역슬래시로 escape한다(링크 `[x](…)`·강조·인용·코드).
    - 모든 `@`와 `<` 뒤에 U+200B를 넣는다. `@everyone`·`@here`·`<@…>`·
      `<@!…>`·`<@&…>`·`<#…>`를 패턴으로 고르지 않고 시작 문자에서 끊는다
      (패턴 누락이 없게).
    - `limit`이 있으면 **escape한 뒤의** UTF-16 유닛 수로 자른다.
    """
    cleaned = "".join(" " if ch.isspace() or ord(ch) < 32 else ch for ch in text)
    escaped = "".join("\\" + ch if ch in MARKDOWN_SPECIAL else ch for ch in cleaned)
    out = escaped.replace("@", "@" + ZWSP).replace("<", "<" + ZWSP)
    return out if limit is None else _clip_units(out, limit)


def embed_chars(embed: dict[str, Any]) -> int:
    """Embed 1개가 메시지 6000자 한도에 보태는 글자 수(title + 필드)."""
    total = _units(embed.get("title", ""))
    for field in embed.get("fields", []):
        total += _units(field["name"]) + _units(field["value"])
    return total


def _kev_label(is_kev: bool | None) -> str:
    if is_kev is None:
        # NVD에 아직 없는 CVE — 「KEV 아님」이 아니라 「모름」
        return UNKNOWN_LABEL
    return "예" if is_kev else "아니오"


def _cvss_label(score: float | None, severity: str | None) -> str:
    if score is None:
        return UNKNOWN_LABEL
    label = f"{float(score):.1f}"
    if severity:
        label += f" ({neutralize_mentions(severity, SEVERITY_MAX)})"
    return label


def build_embed(candidate: Candidate) -> dict[str, Any]:
    """후보 1개를 embed 1개로 만든다(칸 구성은 `EMBED_SOURCES`)."""
    item = candidate.item
    if candidate.kind == KIND_DIGEST:
        title = neutralize_mentions(item.geeknews_title or "", TITLE_MAX)
        url = item.geeknews_link
    else:
        title = item.cve_id
        url = item.nvd_url
    cvss = _cvss_label(item.cvss_score, item.cvss_severity)
    fields = [
        {"name": "CVE", "value": item.cve_id, "inline": True},
        {"name": "CVSS", "value": cvss, "inline": True},
        {"name": "KEV", "value": _kev_label(item.is_kev), "inline": True},
    ]
    if item.is_kev:
        vendor = neutralize_mentions(item.kev_vendor_project or "", KEV_NAME_MAX)
        product = neutralize_mentions(item.kev_product or "", KEV_NAME_MAX)
        # 500 + 3 + 500 유닛 < FIELD_VALUE_MAX(1024) — 자르지 않아도 한도 안이다
        fields.append({"name": "벤더/제품", "value": f"{vendor} / {product}"})
    # 링크 문구는 「상세」 고정. URL은 형식 검증된 cve_id로만 만든 것이다
    detail = f"[{DETAIL_LABEL}]({item.nvd_url})"
    fields.append({"name": DETAIL_LABEL, "value": detail})
    return {"title": title, "url": url, "fields": fields}


def chunk_embeds(embeds: list[dict[str, Any]]) -> list[list[int]]:
    """Embed 목록을 메시지 단위 인덱스 묶음으로 나눈다.

    메시지당 embed 10개 이하·글자 합계 6000 이하.
    """
    chunks: list[list[int]] = []
    current: list[int] = []
    current_chars = 0
    for index, embed in enumerate(embeds):
        size = embed_chars(embed)
        if size > MAX_EMBED_CHARS_PER_MESSAGE:
            message = "embed 1개가 메시지 글자 한도를 넘는다"
            raise ValueError(message)
        full = len(current) >= MAX_EMBEDS_PER_MESSAGE
        over = current_chars + size > MAX_EMBED_CHARS_PER_MESSAGE
        if current and (full or over):
            chunks.append(current)
            current, current_chars = [], 0
        current.append(index)
        current_chars += size
    if current:
        chunks.append(current)
    return chunks


def build_payloads(
    candidates: Iterable[Candidate],
) -> list[tuple[dict[str, Any], list[str]]]:
    """후보를 웹훅 payload 목록으로 만든다.

    반환: (payload, 그 메시지에 담긴 candidate key 목록) — key는 발신 기록용이다.

    - 메시지 하나는 한 종류만 담는다(content가 종류별 고정 문자열이라서다).
    - `allowed_mentions={"parse": []}`를 항상 넣는다.
    """
    ordered = list(candidates)
    kinds = list(dict.fromkeys(c.kind for c in ordered))
    result: list[tuple[dict[str, Any], list[str]]] = []
    for kind in kinds:
        group = [c for c in ordered if c.kind == kind]
        embeds = [build_embed(c) for c in group]
        for indexes in chunk_embeds(embeds):
            payload = {
                "content": CONTENT_BY_KIND[kind],
                "embeds": [embeds[i] for i in indexes],
                "allowed_mentions": {"parse": []},
            }
            result.append((payload, [group[i].key for i in indexes]))
    return result


def dedupe(candidates: Iterable[Candidate]) -> list[Candidate]:
    """같은 key는 처음 것만 남긴다."""
    seen: dict[str, Candidate] = {}
    for candidate in candidates:
        seen.setdefault(candidate.key, candidate)
    return list(seen.values())


def select_unsent(
    candidates: Iterable[Candidate],
    sent_keys: set[str] | frozenset[str],
    remaining: int,
) -> tuple[list[Candidate], int]:
    """기록에 없는 후보를 (rank, key) 순으로 상한까지 고른다.

    반환: (선택, 잘린 건수).
    """
    unsent = [c for c in dedupe(candidates) if c.key not in sent_keys]
    unsent.sort(key=lambda c: (c.rank, c.key))
    allowed = max(0, remaining)
    return unsent[:allowed], max(0, len(unsent) - allowed)


def today_kst(now: datetime) -> date:
    """tz-aware 시각의 KST 날짜."""
    if now.tzinfo is None:
        message = "now는 tz-aware여야 한다"
        raise ValueError(message)
    return now.astimezone(KST).date()


def unpaired_pending(rows: Iterable[dict[str, Any]], kind: str) -> list[str]:
    """`pending`만 있고 `sent`·`rejected`로 닫히지 않은 이 kind의 key(정렬)."""
    pending: set[str] = set()
    closed: set[str] = set()
    for row in rows:
        if row["kind"] != kind:
            continue
        if row["status"] == STATUS_PENDING:
            pending.add(row["send_key"])
        elif row["status"] in CLOSING_STATUSES:
            closed.add(row["send_key"])
    return sorted(pending - closed)


def count_today(rows: Iterable[dict[str, Any]], today: date) -> int:
    """오늘 KST 상한에 드는 key 수(두 DAG 합산).

    오늘 날짜의 `pending`·`sent` 행을 가진 key를 한 번씩 센다
    (pending → sent 두 행이 있어도 1). `rejected`로 닫힌 key는 뺀다.
    """
    counted: set[str] = set()
    rejected: set[str] = set()
    for row in rows:
        if row["status"] == STATUS_REJECTED:
            rejected.add(row["send_key"])
        elif (
            row["status"] in {STATUS_PENDING, STATUS_SENT} and row["kst_date"] == today
        ):
            counted.add(row["send_key"])
    return len(counted - rejected)


def plan_send(
    candidates: Iterable[Candidate],
    log_rows: Iterable[dict[str, Any]],
    today: date,
    *,
    kind: str,
    seed: str | None = None,
) -> SendPlan:
    """발신 기록과 오늘 KST 날짜로 이번 run의 발신·시드 대상을 정한다(순수 함수).

    - 이 kind에 **짝 없는 `pending`** 이 있으면 아무것도 계획하지 않는다
      (`unpaired`). 발신이 나갔는지 모르는 상태라 사람이 닫는다(run_send가 실패).
    - 시드 표지 `seed`(기본 `seed_key(kind)`)가 기록에 없으면(처음 켰을 때·
      테이블 재생성 뒤·kev 관심목록 변경 뒤) 기록에 없는 후보 전부를
      baseline으로 시드하고 **발신하지 않는다**(risk.md §4 예외 ② 조건 3).
      표지를 함께 남겨, 후보 0건으로 시작해도 「시드했다」가 남게 한다.
    - ⚠️ 판정이 kind 단위인 이유: 기록 테이블은 두 DAG가 함께 쓴다. 테이블
      전체로 보면 먼저 켠 DAG가 시드한 뒤, 나중에 켠 DAG가 기존 대상 행을
      전부 신규로 보고 보낸다.
    - 아니면 기록에 없는(어느 status든) key만 고르고 남은 상한으로 자른다.
      남은 상한 = `DAILY_SEND_LIMIT` - `count_today`(두 DAG 합산).
    """
    # 🔴 kev는 관심목록 해시가 든 표지가 필수다 — 빠지면 목록을 바꿔도 재시드가
    #    안 돼, 새로 더한 제품의 기존 KEV가 한꺼번에 나간다(fail-closed)
    if kind == KIND_KEV and not (seed or "").startswith(seed_key(KIND_KEV) + ":"):
        message = "kev 계획에는 관심목록 해시가 든 seed(seed_key(kev, hash))가 필요하다"
        raise ValueError(message)
    seed = seed or seed_key(kind)
    pool = dedupe(candidates)
    if any(c.kind != kind for c in pool):
        message = "후보의 kind가 계획 kind와 다르다"
        raise ValueError(message)
    rows = list(log_rows)
    logged_keys = {row["send_key"] for row in rows}
    sent_today = count_today(rows, today)
    already = sum(1 for c in pool if c.key in logged_keys)
    unpaired = tuple(unpaired_pending(rows, kind))
    if unpaired:
        return SendPlan(
            kind, len(pool), False, (), (), 0, already, sent_today, unpaired
        )
    if seed not in logged_keys:
        fresh = tuple(c.key for c in pool if c.key not in logged_keys)
        baseline = (*fresh, seed)
        return SendPlan(kind, len(pool), True, baseline, (), 0, already, sent_today)
    remaining = DAILY_SEND_LIMIT - sent_today
    selected, truncated = select_unsent(pool, logged_keys, remaining)
    return SendPlan(
        kind, len(pool), False, (), tuple(selected), truncated, already, sent_today
    )


def send_log_arrow(
    keys: Iterable[str], kind: str, status: str, now: datetime
) -> pa.Table:
    """발신 기록 행(키·종류·상태·시각만 — 원천 값 없음)."""
    sent_at = now.astimezone(timezone.utc)
    kst_date = today_kst(now)
    return pa.Table.from_pylist(
        [
            {
                "send_key": key,
                "kind": kind,
                "status": status,
                "sent_at": sent_at,
                "kst_date": kst_date,
            }
            for key in keys
        ],
        schema=SEND_LOG_SCHEMA,
    )


def read_send_log(table: Table) -> list[dict[str, Any]]:
    """발신 기록 전 행(키·종류·상태·KST 날짜). 막 만든 테이블은 빈 목록이다."""
    fields = ("send_key", "kind", "status", "kst_date")
    return table.scan(selected_fields=fields).to_arrow().to_pylist()


def deliver(
    selected: Iterable[Candidate],
    *,
    post: Callable[[dict[str, Any]], Any],
    table: Table,
    now: Callable[[], datetime],
) -> dict[str, int]:
    """메시지마다 **pending 기록 → 발신 → 확정(sent | rejected)** 순으로 처리한다.

    - pending append가 실패하면 그 메시지는 보내지 않고 예외를 올린다.
    - 발신이 400(`PayloadRejectedError`)이면 그 key를 `rejected`로 닫고 다음
      메시지로 넘어간다(다시 보내지 않고 상한에도 세지 않는다).
      ⚠️ 거절 단위는 메시지라, 한 항목 때문에 **같은 메시지의 최대 10항목이 함께
      rejected로 닫혀 영구 누락**된다(rejected key는 다시 보내지 않는다).
    - 그 밖의 발신 실패(401·403·404·429·5xx 소진·타임아웃·연결 오류)와 sent
      append 실패는 예외를 그대로 올린다. 그 key는 **짝 없는 pending**으로 남고,
      다음 run은 이 kind를 보내지 않은 채 실패한다(사람이 채널을 보고 닫는다).
      ⇒ **run 사이** 중복 발신과 상한 초과는 없다. 대가는 그 kind의 정지다.
      ⚠️ **run 안에서는** `post_json`의 5xx 재시도(재시도 3회)로 같은 메시지가
      최대 4회 나갈 수 있다(5xx가 처리 후 실패일 때). 상한은 항목 단위라 이 중복은
      상한에 다시 세지 않는다.

    반환: {"sent": 발신 확정 항목 수, "rejected": 거절 항목 수}.
    """
    ordered = list(selected)
    kind_by_key = {c.key: c.kind for c in ordered}
    counts = {"sent": 0, "rejected": 0}
    for payload, keys in build_payloads(ordered):
        # 메시지 하나는 한 종류만 담는다(build_payloads)
        kind = kind_by_key[keys[0]]
        append_rows(table, send_log_arrow(keys, kind, STATUS_PENDING, now()))
        try:
            post(payload)
        except PayloadRejectedError:
            closing = STATUS_REJECTED
        else:
            closing = STATUS_SENT
        append_rows(table, send_log_arrow(keys, kind, closing, now()))
        counts[closing] += len(keys)
    return counts


def close_pending(
    table: Table, kind: str, status: str, now: Callable[[], datetime]
) -> int:
    """짝 없는 pending을 사람이 판정한 상태(sent | rejected)로 닫는다. 반환: 건수.

    🔴 사람의 수동 절차용이다(DAG는 부르지 않는다). 채널에서 그 메시지가
    나갔는지 본 뒤 나갔으면 `sent`, 안 나갔으면 `rejected`로 닫는다.
    `rejected`로 닫은 key는 다시 보내지 않는다(다시 보내려면 행을 지우는 대신
    새 후보 키가 생겨야 한다 — 기록은 append만 한다).
    """
    if status not in CLOSING_STATUSES:
        message = f"닫는 상태는 {sorted(CLOSING_STATUSES)} 중 하나다"
        raise ValueError(message)
    keys = unpaired_pending(read_send_log(table), kind)
    if keys:
        append_rows(table, send_log_arrow(keys, kind, status, now()))
    return len(keys)


def key_digest(keys: Iterable[str]) -> str:
    """키 집합의 sha256 접두(로그·반환값용 — 키 자체는 남기지 않는다)."""
    joined = "\n".join(sorted(keys)).encode()
    return hashlib.sha256(joined).hexdigest()[:KEY_DIGEST_PREFIX]


def run_send(
    kind: str,
    candidates: Iterable[Candidate],
    *,
    table: Table,
    post: Callable[[dict[str, Any]], Any],
    now: Callable[[], datetime],
    seed: str | None = None,
) -> dict[str, Any]:
    """기록 조회 → 계획 → (시드 | 발신·확정). 반환: 건수와 키 해시 접두만.

    짝 없는 pending이 있으면 아무것도 보내지 않고 RuntimeError(키 해시 접두만).
    """
    log_rows = read_send_log(table)
    plan = plan_send(candidates, log_rows, today_kst(now()), kind=kind, seed=seed)
    if plan.unpaired:
        message = (
            f"{kind}: 짝 없는 pending {len(plan.unpaired)}건"
            f"(키 해시 {key_digest(plan.unpaired)}) — 발신하지 않는다."
            " 채널을 확인하고 close_pending으로 닫는다"
        )
        raise RuntimeError(message)
    if plan.seeded:
        baseline = send_log_arrow(plan.baseline_keys, kind, STATUS_BASELINE, now())
        append_rows(table, baseline)
    delivered = deliver(plan.to_send, post=post, table=table, now=now)
    return {
        "kind": kind,
        "candidates": plan.candidates,
        "seeded": plan.seeded,
        # 시드 표지 1행은 뺀다
        "baseline": len(plan.baseline_keys) - 1 if plan.seeded else 0,
        "already_logged": plan.already_logged,
        "sent": delivered["sent"],
        "rejected": delivered["rejected"],
        "truncated": plan.truncated,
        "sent_today_before": plan.sent_today,
        "daily_limit": DAILY_SEND_LIMIT,
        "sent_keys_sha256": key_digest(c.key for c in plan.to_send),
    }


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _score(value: Any) -> float | None:
    return None if value is None else float(value)


def digest_candidate(row: dict[str, Any], rank: int = 1) -> Candidate:
    """`geeknews__cve_links`(+ digest의 KEV 벤더/제품) 1행 → 다이제스트 후보.

    행의 다른 컬럼(설명문 등)은 읽지 않는다. 이름으로 고른 값만 들어간다.
    """
    item = AlertItem(
        cve_id=str(row["cve_id"]),
        cvss_score=_score(row.get("cvss_score")),
        cvss_severity=_text(row.get("cvss_severity")),
        is_kev=row.get("is_kev"),
        kev_vendor_project=_text(row.get("kev_vendor_project")),
        kev_product=_text(row.get("kev_product")),
        geeknews_title=_text(row.get("title")),
        geeknews_link=_text(row.get("link")),
    )
    key = digest_key(row["topic_id"], item.cve_id)
    return Candidate(key, KIND_DIGEST, item, rank)


def kev_candidate(row: dict[str, Any], rank: int = 0) -> Candidate:
    """`nvd__cve_digest` 1행(is_kev) → KEV 후보."""
    item = AlertItem(
        cve_id=str(row["cve_id"]),
        cvss_score=_score(row.get("cvss_score")),
        cvss_severity=_text(row.get("cvss_severity")),
        is_kev=row.get("is_kev"),
        kev_vendor_project=_text(row.get("kev_vendor_project")),
        kev_product=_text(row.get("kev_product")),
    )
    return Candidate(kev_key(item.cve_id), KIND_KEV, item, rank)


def build_candidates(
    rows: Iterable[dict[str, Any]],
    make: Callable[..., Candidate],
    watchlist: frozenset[tuple[str, str]] | None,
    *,
    watchlist_only: bool,
) -> tuple[list[Candidate], dict[str, int]]:
    """Gold 행을 후보로 바꾼다. 반환: (후보, 건수 요약).

    - 검증에 실패한 행(CVE 형식·링크 scheme)은 **버리고 센다**. 한 행 때문에
      전 발신이 멈추지 않게 하되, 그 값을 메시지에 싣지는 않는다.
    - `watchlist_only`면 관심목록 일치 행만 남긴다. 아니면 전 행을 두고
      일치 행을 rank 0으로 앞에 둔다.
    """
    candidates: list[Candidate] = []
    counts = {"rows": 0, "rejected": 0, "matched": 0}
    for row in rows:
        counts["rows"] += 1
        matched = watchlist is not None and matches_watchlist(
            row.get("cpe_vendor_products") or [],
            row.get("kev_vendor_project"),
            row.get("kev_product"),
            watchlist,
        )
        counts["matched"] += int(matched)
        if watchlist_only and not matched:
            continue
        try:
            candidates.append(make(row, rank=0 if matched else 1))
        except ValueError:
            counts["rejected"] += 1
    return candidates, counts


def load_watchlist(path: str | Path) -> frozenset[tuple[str, str]]:
    """관심목록 YAML을 읽어 소문자 (vendor, product) 집합으로 돌려준다.

    형식: `{"products": [{"vendor": str, "product": str}, ...]}`.
    스키마가 어긋나거나 목록이 비거나(빈 파일 포함) 파일이 없으면 ValueError다
    (fail-closed). 실제 목록은 저장소 밖에 둔다 — 예제는
    `scripts/watchlist.example.yaml`.
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        message = f"관심목록을 읽지 못했다: {type(exc).__name__}"
        raise ValueError(message) from None
    if not isinstance(data, dict) or set(data) != {"products"}:
        message = "관심목록 최상위는 products 키 하나여야 한다"
        raise ValueError(message)
    products = data["products"]
    if not isinstance(products, list) or not products:
        message = "관심목록 products가 비었거나 목록이 아니다"
        raise ValueError(message)
    pairs: set[tuple[str, str]] = set()
    for entry in products:
        if not isinstance(entry, dict) or set(entry) != {"vendor", "product"}:
            message = "관심목록 항목은 vendor·product 두 키여야 한다"
            raise ValueError(message)
        vendor, product = entry["vendor"], entry["product"]
        valid = isinstance(vendor, str) and isinstance(product, str)
        if not (valid and vendor.strip() and product.strip()):
            message = "관심목록 vendor·product는 빈 값 아닌 문자열이어야 한다"
            raise ValueError(message)
        pairs.add((vendor.strip().lower(), product.strip().lower()))
    return frozenset(pairs)


def matches_watchlist(
    vendor_products: Iterable[str],
    kev_vendor: str | None,
    kev_product: str | None,
    watchlist: frozenset[tuple[str, str]],
) -> bool:
    """CPE `vendor:product` 또는 KEV 벤더/제품이 관심목록에 있으면 참(소문자 비교).

    ⚠️ 두 원천의 표기가 다르다(CPE `microsoft:windows_10` /
    KEV `Microsoft`·`Windows`). 관심목록에 둘 다 적어야 양쪽에서 잡힌다.
    이 함수는 표기를 서로 바꾸지 않는다.
    """
    for pair in vendor_products:
        vendor, sep, product = str(pair).partition(":")
        if sep and (vendor.strip().lower(), product.strip().lower()) in watchlist:
            return True
    if kev_vendor and kev_product:
        pair = (kev_vendor.strip().lower(), kev_product.strip().lower())
        return pair in watchlist
    return False


def validate_webhook_url(url: str) -> None:
    """웹훅 URL이 Discord 웹훅 모양인지 본다. 어긋나면 ValueError.

    메시지에 URL을 싣지 않는다. Connection 값이 엉뚱한 곳(다른 호스트·http)을
    가리키면 보내지 않는다(fail-closed).

    규칙은 `scripts/k8s-secrets.sh`의 형식 가드(`https://discord.com/api/webhooks/`
    접두)와 같은 수신자를 받는다. 여기서는 더 좁게 본다.
    - netloc이 정확히 `discord.com`이다(대소문자·포트·userinfo를 받지 않는다).
    - 경로가 `/api/webhooks/<숫자>/<토큰>` 꼴과 완전히 맞는다.
    - 쿼리·프래그먼트가 없다(`?wait=true`는 `post_json`이 붙인다).
    """
    try:
        parts = urlsplit(url or "")
    except ValueError:
        parts = urlsplit("")
    if (
        parts.scheme != "https"
        or parts.netloc not in WEBHOOK_HOSTS
        or not WEBHOOK_PATH_PATTERN.fullmatch(parts.path)
        or parts.query
        or parts.fragment
    ):
        message = "Connection discord_webhook이 Discord 웹훅 URL 모양이 아니다"
        raise ValueError(message)


def mask_webhook(url: str) -> str:
    """웹훅 URL을 로그·예외용으로 가린다. 토큰이 **경로**에 있어 경로까지 지운다.

    `https://discord.com/api/webhooks/<id>/<token>?…`
    → `https://discord.com/api/webhooks/***`.
    웹훅 경로가 아니면 호스트 뒤를 전부 `***`로 바꾼다.
    파싱이 안 되면 `***`만 남긴다.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return "***"
    if not (parts.scheme and host):
        return "***"
    on_webhook_path = parts.path.startswith(WEBHOOK_PATH_PREFIX)
    prefix = WEBHOOK_PATH_PREFIX if on_webhook_path else "/"
    return f"{parts.scheme}://{host}{prefix}***"
