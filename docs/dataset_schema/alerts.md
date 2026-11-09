# Discord 발신 기록 (alerts)

원천 데이터셋 색인은 [`../dataset_schema.md`](../dataset_schema.md)다. 이 문서는 Airflow DAG `alerts_digest`·`alerts_kev`가
쓰는 **발신 기록 한 테이블**과 두 DAG가 읽는 입력을 갖는다. 발신 자체의 통제(수신자·필드·상한·게이트)는
[`../risk.md`](../risk.md) §4 예외 ②가 정본이고, 필드별 원천 판정은
[`../security/datasets.md`](../security/datasets.md) 「Discord 발신 판정」이 갖는다.

Iceberg 네임스페이스 `alerts`. 오케스트레이터는 **Airflow뿐**이다(Dagster 정의 없음). 로직은
`images/airflow/dags/lakehouse/alerts.py`(통제 배선 파일)에 있고 DAG는 배선만 한다.

## 입력

| DAG | 스케줄(기본 일시정지) | 후보 |
|------|------|------|
| `alerts_digest` | 매일 08:30 KST(`CronTriggerTimetable`) | `geeknews.geeknews__cve_links` **전 행**. KEV 벤더/제품은 `nvd.nvd__cve_digest`에서 left join한다. 관심목록과 일치하는 행이 앞에 온다 |
| `alerts_kev` | gold Asset(`alerts_dbt`의 outlet) | `nvd.nvd__cve_digest`에서 `is_kev`이면서 관심목록과 일치하는 행 |

- 관심목록은 YAML 파일이다. 경로는 env `ALERTS_WATCHLIST_PATH`로 받고, 형식 예제는 `scripts/watchlist.example.yaml`이다
  (가짜 값). **실제 목록은 저장소 밖에 둔다.**
  - 스키마가 어긋나거나 목록이 비면(빈 파일 포함) `load_watchlist`가 ValueError를 낸다(fail-closed).
  - 🔴 배포(차트)에서는 이 env가 **항상 설정**돼 Secret 마운트 파일을 가리킨다. 그래서 Secret이 비었으면
    **두 DAG 모두 실패한다** — `alerts_digest`도 「목록 없이 돈다」가 아니다.
  - env 자체가 없을 때(차트 밖 로컬 실행)만 `alerts_digest`가 정렬을 건너뛰고 돈다(건수를 로그에 남긴다).
    `alerts_kev`는 env가 없어도 실패한다.
- 비교는 소문자로 한다. CPE의 `vendor:product`와 KEV의 벤더/제품은 표기가 다르다.
  두 표기를 모두 목록에 적어야 양쪽에서 잡힌다.
- CVE ID 형식·GeekNews 링크 scheme(http/https)에 어긋나는 행은 **버리고 센다**(`invalid_rows`).
  그 값은 메시지에 싣지 않는다.

## alerts.discord_sends

발신 1항목(embed 1개)의 상태 변화마다 1행이다. 두 DAG가 함께 쓴다. append만 한다.

| 컬럼 | 타입 | 규칙 |
|------|------|------|
| `send_key` | string (required) | 다이제스트 `<topic_id>:<cve_id>`, KEV `kev:<cve_id>`. 시드 표지는 아래 |
| `kind` | string | `digest` · `kev` |
| `status` | string | 아래 표 |
| `sent_at` | timestamp(UTC) | 기록 시각 |
| `kst_date` | date | `sent_at`의 KST 날짜. 일일 상한이 이 값을 센다 |

| `status` | 뜻 | 상한에 드나 |
|------|------|------|
| `pending` | 발신 직전 예약. `sent`·`rejected` 행이 짝으로 닫는다 | 든다 |
| `sent` | 200(`?wait=true`)으로 생성이 확인됐다 | 든다(같은 key의 pending과 합쳐 1건) |
| `rejected` | 400으로 거절됐다(발신 안 됨). 다시 보내지 않는다 | 안 든다 |
| `baseline` | 시드 — 보내지 않음 | 안 든다 |

시드 표지는 `digest:__seed__`와 `kev:__seed__:<관심목록 해시 12자>`다. 해시는 정규화한(소문자·정렬) 목록의 sha256
접두이고, **목록 원문은 기록·로그에 남기지 않는다**.

**원천 값을 담지 않는다** — 제목·링크·CVSS는 없고 키·종류·상태·시각만 있다.

### 판정 규칙

- 🔴 **처음 켤 때는 보내지 않는다.** 그 kind의 시드 표지가 기록에 없으면(처음 켤 때·테이블 재생성 뒤) 기록에 없는
  후보 전부를 `baseline`으로 시드하고 표지 1행도 남긴다.
  - 판정이 kind 단위인 이유: 테이블을 두 DAG가 함께 쓴다. 테이블 전체로 보면, 먼저 켠 DAG가 시드한 뒤
    나중에 켠 DAG가 기존 대상 행을 전부 신규로 보고 보낸다.
  - 표지가 있는 이유: 후보 0건으로 시작하면 「시드했다」는 흔적이 없다. 그러면 나중에 생긴 첫 후보가
    baseline으로 묻힌다.
  - **kev는 관심목록이 바뀌면 다시 시드한다**(표지 키의 해시가 바뀐다). 목록에 제품을 더한 순간 그 제품의
    기존 KEV가 한꺼번에 나가지 않게 하기 위해서다. 새로 더한 제품의 **그 뒤** KEV만 나간다.
    ⚠️ 재시드는 「기록에 없는 일치 행 전부」를 baseline으로 묻는다. 그래서 **관심목록을 바꾼 run에서는
    기존 제품의 신규 KEV도 baseline으로 묻혀 누락될 수 있다**(누락 방향 — 의도). 목록을 바꾼 뒤에는
    그 run의 `baseline` 건수를 확인한다.
- 기록에 있는 `send_key`(어느 status든)는 다시 보내지 않는다.
- **일일 상한** — 오늘 KST의 `pending`·`sent` 행을 가진 key 수를 두 DAG 합산으로 센다(`rejected`로 닫힌 key 제외).
  상한값은 `lakehouse/alerts.py`의 리터럴이다. 단위는 메시지(POST)가 아니라 **항목**이다. 남은 상한을 넘는 후보는
  잘리고 `truncated`로 센다. 잘린 후보는 다음 run의 후보로 남는다.
- **순서는 pending 기록 → 발신 → 확정**이다. 메시지 1개(embed ≤ 10, 글자 합계 ≤ 6000)마다 key를 `pending`으로
  append하고, 보낸 뒤 `sent`로 닫는다. 400이면 `rejected`로 닫고 다음 메시지로 넘어간다.
  - pending append가 실패하면 그 메시지는 보내지 않는다.
  - 그 밖의 발신 실패(401·403·404·3xx·200 아닌 2xx·429·5xx 소진·연결 오류·타임아웃)와 확정 append 실패는
    태스크를 실패시킨다. 그 key는 **짝 없는 pending**으로 남는다.
  - 🔴 **짝 없는 pending이 있으면 그 kind는 보내지 않고 실패한다**(메시지에는 건수와 키 해시 접두만).
    발신이 나갔는지 모르는 상태라 사람이 판정한다 — 그 사이에도 pending은 상한에 든다.
    ⇒ 기록·발신 실패가 반복돼도 **run 사이 중복 발신과 상한 초과는 없다**. 대가는 그 kind의 정지다.
    ⚠️ **run 안에서는** `post_json`의 5xx 재시도(재시도 3회)로 같은 메시지가 **최대 4회** 나갈 수 있다
    (5xx가 처리 뒤의 실패일 때). 상한은 항목 단위라 이 중복을 다시 세지 않는다.
  - ⚠️ **400 거절은 메시지 단위다.** 한 항목 때문에 거절돼도 **같은 메시지의 최대 10항목이 함께 `rejected`로
    닫혀 영구 누락**된다(rejected key는 다시 보내지 않는다).
- **pending 닫기(사람)** — 채널에서 그 메시지가 나갔는지 확인하고 `lakehouse.alerts.close_pending(table, kind,
  status, now)`로 닫는다. 나갔으면 `sent`, 안 나갔으면 `rejected`다. `rejected`로 닫은 key는 다시 보내지 않는다.
  호출 경로(카탈로그 접속)는 이 문서 밖이다 — 실행은 사람이 한다.
- 두 DAG의 발신 태스크는 Pool `discord_webhook`(slots 1)을 공유한다. 기록 조회부터 기록 append까지가 겹치면
  상한·중복 판정이 서로를 못 보기 때문이다.
- **gold 정의 변경(백필)은 의도된 동작이다.** `geeknews__cve_links`의 정의가 바뀌어 기존 글의 (topic, CVE) 행이
  새로 생기면, digest는 그것을 신규 후보로 보고 상한 안에서 보낸다(관심목록 해시 같은 재시드 장치가 없다).
  재검토 조건: gold 정의를 바꾸는 PR이 기존 글의 행을 한 번에 상한보다 많이 만들 때.
  그 PR은 머지 전에 digest를 pause하고 재시드 방법을 함께 정한다.

### 관측

Asset event는 내지 않는다(소비자가 없다). 태스크 반환값(XCom)과 로그에는 **건수와 키 해시 접두만** 남긴다.

| 키 | 출처 | 뜻 |
|------|------|------|
| `kind` | `run_send` | `digest` · `kev` |
| `candidates` | `run_send` | 중복 제거 뒤 후보 수 |
| `seeded`·`baseline` | `run_send` | 이번 run이 시드했는지, 시드한 항목 수(표지 제외) |
| `already_logged` | `run_send` | 후보 중 기록에 이미 있던 수 |
| `sent`·`rejected` | `run_send` | 발신 확정 항목 수, 400으로 닫은 항목 수 |
| `truncated` | `run_send` | 상한에 걸려 이번에 안 보낸 수 |
| `sent_today_before`·`daily_limit` | `run_send` | 이번 run 전 오늘 상한 사용량, 상한값 |
| `sent_keys_sha256` | `run_send` | 이번에 고른 key 집합의 sha256 접두 12자 |
| `gold_rows`·`invalid_rows`·`watchlist_matched` | DAG | gold 행 수, 검증 탈락 행 수, 관심목록 일치 행 수 |
| `watchlist_loaded` | `alerts_digest`만 | 관심목록을 읽었는지 |

웹훅 URL은 로그·예외에서 `https://discord.com/api/webhooks/***`로 가린다.

## 함정

- ⚠️ **테이블을 지우면 다음 run이 다시 시드한다**(발신 없음). 그 사이에 생긴 항목은 영영 안 나간다. 지우지 않는다.
- ⚠️ 글자 수는 칸별 자르기까지 모두 UTF-16 코드 유닛으로 센다. Discord가 무엇으로 세는지는 **미확인**이라
  큰 쪽으로 센다.
- ⚠️ `alerts_kev`는 gold가 갱신될 때마다 돈다(`alerts_dbt`는 상류 셋의 OR라 매시일 수 있다). 새 KEV가 없으면
  `sent=0`으로 끝난다.
- 리다이렉트는 따라가지 않는다(3xx는 실패). 성공은 200만이다 — `?wait=true`의 생성 확인이 200이라서다.
- 요청 예외(연결 실패·타임아웃)는 재시도하지 않는다. 요청이 이미 닿았는지 알 수 없어 재시도가 중복 발신이 될 수 있다.
  5xx는 지수 백오프, 429는 `retry_after`만큼(상한 60초 — 넘으면 실패), 401·403·404는 즉시 실패, 400은 `rejected`다.
