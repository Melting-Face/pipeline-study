# GeekNews 피드 원천 (geeknews)

원천 데이터셋 색인은 [`../dataset_schema.md`](../dataset_schema.md)이고, 이 문서는 Airflow DAG `geeknews_items`가
적재하는 **raw 레이아웃**과 **bronze 한 테이블**, dbt silver 한 모델과 gold 한 모델을 갖는다. 이용 조건 판정은
[`../security/datasets.md`](../security/datasets.md) `geeknews` 근거 절이 정본이다.

Iceberg 네임스페이스 `geeknews`. 오케스트레이터는 **Airflow뿐**이다 — Dagster 정의와 패리티 테스트가 없다
([`../conventions/airflow.md`](../conventions/airflow.md) §2).

## 원천

- 피드 URL `https://news.hada.io/rss/news`는 **302로 FeedBurner Atom 피드**(`feeds.feedburner.com`)로 넘어간다.
  수집기는 리다이렉트를 따라가므로 실제 요청 상대는 FeedBurner다.
- 형식은 Atom이다. 루트는 `feed`(Atom 네임스페이스)이고 `entry`마다 `title`·`link rel='alternate'`·`id`·`updated`·
  `published`·`author`·`content`(HTML)를 갖는다.
- 수집은 **매시 17분(KST)에 1회**다. DAG는 만들어질 때 **일시정지**이고 켜는 것은 사람이다. 고유 `User-Agent`를 붙인다.
- 피드만 받는다(페이지 스크래핑 없음). 백필 없음.

## raw 스냅샷

`s3://warehouse/raw/geeknews/feed/dt=<UTC 수집일>/fetched=<YYYYMMDDTHHMMSSZ>/feed.xml.gz` — 피드 원문 gz와
`<key>.sha256` 사이드카. **쓰기 순서는 데이터 → 사이드카**다. `<회차>`는 UTC 수집 시각이다(NVD와 같은 표기).

- raw 원문에는 본문 HTML과 작성자(name·uri)가 그대로 남는다. 비공개 레이크 안에서만 보관한다.
- 파싱·검증을 raw 쓰기보다 **먼저** 한다 — 깨진 응답(HTML 오류 페이지 등)을 raw에 쌓지 않는다.
  대가로 진단용 원문이 남지 않으므로 실패 메시지에는 크기 상한과 검증 사유만 담긴다.
  파싱 상한은 5MiB이고 전체를 내려받은 뒤에 검사한다.

### 스킵 규칙 — 직전과 같으면 쓰지 않는다

피드 해시가 **직전 bronze 적재분의 해시와 같으면** raw·bronze를 쓰지 않고 `AirflowSkipException`으로 끝낸다.
비교 대상은 bronze **최신 스냅샷 속성 `geeknews.feed_sha256`** 이다(`geeknews.raw_key`가 raw 키를 가리킨다).

- **raw 사이드카를 기준으로 삼지 않는 이유.** raw 쓰기가 성공하고 bronze append가 실패하면, 다음 회차가 사이드카와
  「같은 해시」로 스킵해 그 스냅샷이 bronze에 영영 들어가지 않는다. 스냅샷 속성은 append와 원자적이라
  실패 뒤에는 다르게 보여 다시 적재한다(중복 raw 1벌은 낭비이되 정확하다).
- 스킵한 태스크가 **Asset event를 내는지는 미확인**이다(수동 관측 대기). 내면 silver가 매시 재빌드된다.

## geeknews.geeknews_items

bronze. 변경이 감지된 회차마다 피드의 모든 entry를 **append**한다(회차 스냅샷 누적).

| 컬럼 | 타입 | 유도 규칙 |
|------|------|-----------|
| `topic_id` | bigint | entry `id`(`.../topic?id=N`)의 정수 N |
| `title` | string | entry `title` |
| `link` | string | `rel='alternate'` 링크에서 추적 파라미터(`utm_*`)를 제거한 값 |
| `published` | timestamp(UTC) | entry `published`를 UTC로 환산 |
| `updated` | timestamp(UTC) | entry `updated`를 UTC로 환산 |
| `cve_ids` | array(string) | 본문 HTML에서 `CVE-\d{4}-\d{4,}`를 추출해 대문자·중복 제거·정렬한 목록 |
| `fetched_at` | timestamp(UTC) | 수집 시각(경로 `fetched=`와는 초 단위까지만 같다. 컬럼은 마이크로초를 가진다) |

**본문과 작성자는 적재하지 않는다**(라이선스 판정, [`../security/datasets.md`](../security/datasets.md)). 한 피드 안에서
`topic_id`가 중복되면 그 회차는 검증 실패다.

## silver (dbt)

`geeknews.geeknews_items_latest` — grain은 **`topic_id`당 1행**이다. 같은 `topic_id` 중 `fetched_at` 내림차순,
같으면 `updated` 내림차순으로 첫 행을 고른다. Airflow DAG `geeknews_dbt`(Cosmos)가 bronze Asset으로 트리거돼 빌드한다.

## gold (dbt)

`geeknews.geeknews__cve_links`(`tags=['gold','alerts']`) — grain은 **(`topic_id`, `cve_id`)당 1행**이다. 글이 본문에서
언급한 CVE와 NVD 요약(`nvd.nvd__cve_digest`, [nvd.md](nvd.md) §gold)을 잇는다. 빌드 소유자는 Airflow DAG `alerts_dbt`이고
`geeknews_dbt`는 `tag:alerts`를 `exclude`한다. 정의의 정본은 `models/geeknews/tables/schema.yml`의 description이다.

| 컬럼 | 규칙 |
|------|------|
| `topic_id`·`title`·`link`·`published`·`fetched_at` | `geeknews_items_latest`에서 가져온다 |
| `cve_id` | `cve_ids`를 펼쳐 `upper()`로 정규화하고 같은 글 안의 중복을 접는다 |
| `in_nvd` | `cve_id`가 digest(Rejected 제외)에 있으면 true |
| `cvss_score`·`cvss_severity`·`is_kev` | digest 값. `in_nvd = false`이면 **NULL**(「KEV 아님」이 아니라 「모름」) |

- `cve_ids`가 빈 글은 **행이 없다**(의도). 글 수는 이 모델이 아니라 silver에서 센다.
- 🔴 **NVD `description_en`은 싣지 않는다** — digest에 없다(재배포 `미확인`, [security/datasets.md](../security/datasets.md)).
- 관심목록 필터는 이 모델에 없다 — 발신층 Python이 건다.

## 함정

- 🔴 **피드는 최근 글만 담는 창이다.** 어떤 `topic_id`가 없는 것은 원천에 없다는 뜻이 아니라 **창 밖**일 수 있다.
  수집 주기(1시간)가 창보다 촘촘하다는 가정에 기대며, 수집이 오래 멈추면 그 구간은 **복구되지 않는다**(백필 없음).
- **피드 바이트에 매번 바뀌는 요소가 있으면 스킵이 안 걸린다**(**미확인**). 그러면 매시 같은 내용이 append되고
  silver가 매시 재빌드된다. 실피드로 연속 두 번 받아 바이트가 같은지 보기 전에는 스킵을 보장으로 읽지 않는다.
- **`cve_ids`는 본문 정규식 추출**이다. 본문에 언급만 된 CVE도 잡히므로 「관련」이지 「영향」이 아니고,
  표기가 다른 언급은 놓친다(오탐·누락 둘 다). 분석에서 NVD와 조인하기 전에 존재를 확인한다.
- bronze는 append라 같은 `topic_id`가 회차 수만큼 쌓인다. 최신 값이 필요하면 bronze가 아니라 silver를 읽는다.
- 변경 회차마다 파일 1개가 append돼 소파일이 쌓인다. 컴팩션 배선은 이 데이터셋에 아직 없다.

## 건너뛴 것

- **Dagster 정의와 패리티 테스트가 없다** — 새 데이터셋은 Airflow 전용이다.
- **Discord 발신 DAG가 없다.** gold(`geeknews__cve_links`)까지만 있고 발신층은 후속 단계다.
- 수집물은 **모델 학습에 쓰지 않는다**(robots `Content-Signal: ai-train=no`).
