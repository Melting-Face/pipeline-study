# 그 밖의 데이터셋 판정 (security §0-1)

[`../security.md`](../security.md) §0의 판정 조건(DUA·재배포 제한 / 개인정보·가명정보)을
MIMIC-IV·eICU 밖의 적재 데이터셋에 적용한 결과다. 본문의 `§2`·`§0` 등 절 번호는
**`security.md`의 절**을 가리킨다. 이 문서가 §0-1의 정본이다.

위 두 갈래(DUA·재배포 제한 / 개인정보·가명정보)에 비추어 나머지 적재 데이터셋을 판정한다.
**판정하지 못한 축은 `미확인`으로 적고 통제를 유지한다** — 공백으로 두지 않는다.

| 데이터셋 | 개인정보·가명정보 | 재배포 조건 | 결론 |
| --- | --- | --- | --- |
| `frankfurter_fx` | **없음** — 통화쌍 환율은 기관 집계치이고 개인 단위 관측이 아니다. 재식별 축 자체가 성립하지 않는다 | **자유 이용 허용, 단 출처 표기 의무** (아래) | §2 이하 재식별·마스킹 통제 **비적용**. **출처 표기 의무는 남는다** |
| `usgs_water` | **없음** — 하천 관측소 계측값이고 개인 단위 관측이 아니다 | **공공영역(U.S. Public Domain)** — 재배포 제한 **없음**. 출처 표기는 **권고**(아래) | §2 이하 재식별·마스킹 통제 **비적용**. 원천 값·파생 수치를 저장소·공개물에 **담을 수 있다**. **출처 표기를 붙인다**(의무는 아니나 권고를 따른다) |
| `polygon_market` | **없음** — 종목 시세와 기사 메타데이터이고 개인 단위 관측이 아니다 | **재배포 금지** — 무료 플랜이 개인 사용 한정이다 | §2 이하 재식별 통제 **비적용**. 그러나 원천 값을 저장소·공개물에 **담지 않는다**. 기사 본문도 적재하지 않는다 |
| `us_equity_krw` | **없음** — `polygon_market` 시세 × `frankfurter_fx` 환율의 파생이다 | **엄격한 쪽을 상속** — 원화 종가는 Polygon 종가의 파생이라 **재배포 금지**, 환율 열은 ECB **출처 표기 의무**까지 함께 진다 | 파생은 원천 조건을 **풀지 않는다**. 원천 값·환산 값을 저장소·공개물에 **담지 않고**, 환율만 인용할 때도 ECB를 표기한다 |
| `fred_calendar` | **없음** — 거시 집계 지표다 | 연방기관 산출물이나 일부 시리즈가 상류 저작권을 물려받을 수 있어 **미확인** | §2 이하 비적용. 미확인이 의무를 줄이는 쪽으로 작용하지 않게 **담지 않는 쪽**을 기본값으로 둔다 |
| `github_trending` | **미확인** — `full_name`에 **개인 계정 login**이 섞일 수 있다(아래) | 일반 재배포 금지는 관측 안 됨. 연구 이용 허용은 **비개인 정보**에 한정(아래) | §2 이하 마스킹 **비적용**. 그러나 **저장소 단위 원천 값**은 저장소·공개물에 **담지 않고**, login을 사람 단위로 묶는 가공은 하지 않는다 |
| `nvd` (CVE·Change History) | **없음** — 소프트웨어 취약점 레코드이고 개인 단위 관측이 아니다 | **수집·분석 허용, 조건 3** — 고지 문구 **요청**, 가공 값의 NVD 출처 표기 **금지**, 키 비공유·한도 준수. 공개 재배포는 `미확인`(아래) | §2 이하 재식별 통제 **비적용**. **raw 수집 허용**(스케줄·백필 가능). 원천 값은 저장소·공개물에 **아직 담지 않는다**. 고지·출처 조건은 아래 |
| `nvd` (`nvd_kev` — CISA KEV) | **없음** — 악용 확인 취약점 카탈로그이고 개인 단위 관측이 아니다 | **CC0 1.0** — 권리 포기, 재배포 제한 **없음**(아래) | §2 이하 재식별·마스킹 통제 **비적용**. 원천 값·파생 수치를 저장소·공개물에 **담을 수 있다** |
| `geeknews` | **bronze 없음** — 작성자를 적재하지 않는다. raw 원문에는 공개 닉네임(author name·uri)이 남는다(비공개 레이크) | **라이선스 고시 없음** — 약관에 저작권·라이선스·RSS 조항이 없다. 재배포 근거 **미확인** | 수집 허용(피드 1시간 1회). 본문 HTML을 저장소·공개물에 **담지 않는다**. 공개물에는 제목·링크까지. 수집물을 **모델 학습에 쓰지 않는다** |

**`frankfurter_fx` 근거.** 상류에 유럽중앙은행이 있고 그 이용조건은 자유 이용을 허용하되
조건을 붙인다 — *"users of this website may make free use of the information obtained directly
from it subject to the following conditions"* 이고, 핵심 조건이 둘이다.

- *"When such information is distributed or reproduced, it must appear accurately and the ECB
  must be cited as the source."* — **`must`다. 요청이 아니라 의무**로 읽는다.
- 정보를 **판매되는 매체**에 실을 때는 구매자에게 해당 정보를 무료로 얻을 수 있음을 고지해야 한다.

⚠️ **상류 구성은 `미확인`이다.** 서비스 문서가 *"rates are blended across all providers"* 와
*"84 central banks"* 를 말해 응답이 유럽중앙은행 값 그 자체인지 갈리지 않는다. 그래서
데이터셋 이름을 기관이 아니라 **호출하는 서비스**로 붙였다. 출처 표기 의무는 상류에 그 기관이
포함되는 한 성립하므로, 이 미확인이 의무를 **줄이는 쪽으로 작용하지 않게** 한다.

**현재 이 의무는 발동하지 않는다** — 저장소·위키 어디에도 원천 환율 값을 두지 않기 때문이다
(테스트 픽스처는 **합성값**이고 그 사실을 파일 상단에 적었다). 값을 담는 순간 의무가 발동한다.

**시장 데이터 두 데이터셋 근거.** 플랜 조건·발급 절차·폴백 원천의 정책 요구사항은
[setup/market-data-keys.md](../setup/market-data-keys.md)가 정본이다. 테스트 픽스처는 **합성값**이고
그 사실을 각 파일 상단에 적었다 — 구조만 실측을 따르고 값은 담지 않는다.

**`usgs_water` 근거.** 미국 연방기관 산출물이라 저작권이 성립하지 않는다는 것이 기관 정책으로 명시돼 있다.

- *"USGS-authored or produced data and information are considered to be in the U.S. Public Domain."*
- *"When using information from USGS information products, publications, or websites, we **ask**
  that proper credit be given."* — ⚠️ **조동사가 `ask`다. `must`가 아니라 요청**이다.
  `frankfurter_fx`(ECB)의 `must`와 **강도가 다르므로 둘을 같은 취급으로 묶지 않는다.**

재배포를 금지하거나 조건을 붙이는 문장은 **없었다**(부재 확인 — 정책 페이지 전문을
`redistribut`·`must`·`shall`·`restrict`로 훑어 매칭 0). 수문 서비스 **고유의 추가 제한도 찾지 못했고**,
API 문서 푸터가 기관 일반 정책과 같은 문구를 반복한다.

⚠️ **`미확인` 잔여 3건**(조사 범위 밖이라 남은 것이지 1차가 침묵한 것이 아니다 — 둘은 다른 축이다):
ⓐ **파생 수치**를 따로 다룬 문장은 어디에도 없었다(공공영역 원칙에 포섭되는 것으로 읽는다)
ⓑ 수문 전용 인용 문구 원문(`waterdata.usgs.gov/citation` 미열람)
ⓒ `help.waterdata.usgs.gov`는 **도메인 전체가 `301`로 이전**돼 열지 못했다.
⇒ 셋 다 **결론(공공영역·표기 권고)을 뒤집는 축이 아니라** 연장하지 않았다.

⚠️ **단, 「담을 수 있다」는 *값*에 대한 것이고 *파일 형태*는 데이터셋과 무관하게 차단된다.**
`no-health-data-files` 훅과 `.gitignore`는 데이터셋을 구분하지 않고 **경로 + 확장자**만 본다
(`^(notebooks|docs|wiki)/.*\.(csv|parquet|…)$`). 그러니 `usgs_water`라도 `.csv`를 `notebooks/`에
두려 하면 막힌다 — **과차단이지 오류가 아니다.** 값은 **본문·gold 모델·`metrics.json`** 으로 담는다.
⚠️ 이 구분을 놓치면 *"문서는 된다는데 훅이 막네"* 가 되어 **`--no-verify` 우회를 유인**하고,
그 우회는 이 데이터셋만이 아니라 **다른 데이터셋의 방어까지 함께** 깎는다.

**`github_trending` 근거.** 원천은 GitHub REST API이고 이용조건은 이용약관 *H. API Terms*와
Acceptable Use Policies *Information Usage Restrictions*가 정한다.

- *"Researchers may use public, non-personal information from the Service for research purposes,
  only if any publications resulting from that research are open access."* — 허용 범위가
  **비개인 정보**로 한정되고 **공개 접근 조건**이 붙는다.
- *"You may not use the API to download data or Content from GitHub for spamming purposes, including
  for the purposes of selling GitHub users' personal information, such as to recruiters, headhunters,
  and job boards."* — 금지는 스팸·개인정보 판매 목적에 걸린다.
- *"Your use of information from the Service must comply with the GitHub Privacy Statement."* —
  그 방침은 개인정보를 *"information that is directly linked or can be linked to you"* 로 넓게 정의하나,
  **공개 login이 여기 드는지는 명시하지 않는다**(1차가 침묵 — 열지 못한 것이 아니다).

⚠️ login이 개인정보인지는 **해석**이라 `미확인`으로 둔다. 연구 허용이 *non-personal*로 한정되므로,
이 미확인이 의무를 **줄이는 쪽으로 작용하지 않게** login이 섞인 저장소 단위 값은 담지 않는다.
DAG가 `owner` 객체를 버리는 것(수집 최소화)은 이 판정과 같은 방향이지만 `full_name`에 login이 남는다.

**`nvd` 근거.** CVE·Change History는 NVD REST API에서 받는다. 이용 조건은 NVD *Terms of Use*
(`nvd.nist.gov` → Developers → Terms of Use)다. 페이지가 스크립트 렌더링이라 기계로 읽지 못해
**사람이 브라우저에서 옮긴 본문**으로 판정했다.

- **Use** — *"intended to be used to develop a service or services to search, display, analyze, retrieve,
  view and otherwise "get" information from NVD data."* — 수집·적재·분석이 이용 목적 안에 든다.
- **Attribution** — *"are asked to display the following notice prominently within the application:
  "This product uses the NVD API but is not endorsed or certified by the NVD.""* — `asked`라 **요청**으로
  읽되 따른다. NVD 이름으로 보증을 암시하지 않는다.
- **Modification** — *"If you modify the content accessed through the API, you may not attribute the
  source as the NVD."* — bronze 이후 **가공 값은 NVD를 출처로 표기하지 않는다**(원천 열 그대로만 NVD 유래).
- **Use Limitations** — 한도 초과·우회 시 차단될 수 있고, 키는 *"should not be used by, or shared with,
  individuals or organizations other than the original requestor."* — `NVD_API_KEY`는 `.env` 참조로만 쓰고
  Connection `nvd_api`의 `apiKey` 헤더로만 보내며, Airflow Pool `nvd_api`(slots 1)로 호출을 직렬화한다.

⚠️ **재배포 축은 여전히 `미확인`이다.** TOU 본문에는 재배포를 금지하는 조항도 허용하는 조항도 없다(**침묵**이다).
그런데 TOU는 *"NIST Website Policies"* 를 함께 편입하고, 그 문서는 아직 열람하지 않았다(**미열람**이다).
침묵과 미열람은 서로 다른 축이다. 미확인이 의무를 줄이는 쪽으로 작용하지 않도록,
**원천 값을 저장소·공개물에 담는 것은 보류**한다. 수집 금지는 해제한다. KEV는 CISA가 라이선스 파일
(`cisa.gov/sites/default/files/licenses/kev/license.txt`)로 **CC0 1.0**을 고시한다. KEV 판정이 `nvd`
네임스페이스 전체로 번지지 않는다 — 같은 네임스페이스라도 테이블마다 원천이 다르다.

**`geeknews` 근거.** 원천은 GeekNews(`news.hada.io`)의 Atom 피드 `/rss/news`다.

- **robots.txt**(`https://news.hada.io/robots.txt`) — `User-agent: *`에 `Allow: /`이고
  Disallow는 `/rss/favorites/`뿐이다. `/rss/news`는 허용이고 Crawl-delay는 없다.
  파일 상단 `Content-Signal: ai-train=no, search=yes, ai-input=yes`를 **학습 이용 금지**로 따른다.
  학습용 크롤러 UA는 전면 Disallow라 수집기는 **고유 UA**(`USER_AGENT` 상수)를 명시한다.
- **이용약관**(`https://news.hada.io/terms`) 제6조는
  *"서비스에 과도한 부하를 주는 자동화 접근, 크롤링, 스크립트 실행 행위"* 를 금지하나 **수치 기준이 없다**.
  그래서 **1시간 1회 피드 GET + 변경 없으면 쓰기 스킵**을 허용 범위로 판정했다.
  제5조(무단 복제·배포·전재 게시 금지)는 **이용자의 게시 행위** 규정이다.
  제10조는 정확성·완전성·최신성을 보증하지 않는다.
- ⚠️ 약관 1·3·4·7·8·11·12조 본문은 **미인용**이다(조 제목만 확인했고 소형 모델을 거친 인용이다).
  그 범위의 조건은 `미확인`이고 통제를 유지한다.
- 피드는 `feeds.feedburner.com`으로 302된다 — **실제 요청 상대는 Google FeedBurner**다.
  FeedBurner 자체의 이용 조건은 `미확인`이다.
- 저장은 비공개 레이크의 raw(원문 gz)까지 원문 그대로이고, bronze는 본문과 작성자를 버린다.
  스키마 정본 [`dataset_schema/geeknews.md`](../dataset_schema/geeknews.md).

**재검토 트리거**(시점이 아니라 **조건**이다 — [risk.md](../risk.md) §대응):

- `frankfurter_fx` — **원천 값·그 파생 수치를 저장소나 공개물에 담으려 할 때** 출처를 표기한다.
  (`usgs_water`는 이 트리거가 **해소됐다** — 위 근거로 담을 수 있고, 표기는 권고라 붙이되 의무는 아니다.)
- `frankfurter_fx` — 산출물을 **유상 배포**하는 형태가 생길 때(무료 입수 고지 의무가 추가된다).
- 상류 구성이 확정될 때(현재 `미확인`) — 표기 대상 기관이 갈릴 수 있다.
- `github_trending` — **저장소 단위 값이나 그 파생물을 공개물에 담으려 할 때**, 또는 **수집 필드에
  사람 단위 정보(`owner` 객체·커미터 등)를 더할 때** — 개인정보 판정(현재 `미확인`)을 먼저 닫는다.
  언어·토픽별 **집계 수치**만 담는 경우도 이 트리거로 판정한다(자동 허용으로 읽지 않는다).
- `nvd`(CVE·Change History) — 수집 금지 트리거는 **해소됐다**(위 TOU 근거). 남은 트리거는
  **원천 값이나 그 파생을 저장소·공개물에 담으려 할 때**다 — NIST Website Policies를 열람해 재배포 `미확인`을
  먼저 닫고, 담는 공개물에는 고지 문구를 붙인다. **TOU가 개정될 때**도 다시 판정한다.
- `geeknews` — **제목·링크 밖의 값**(본문 발췌, CVE 추출 결과를 넘는 파생)을 공개물·Discord에 실으려 할 때,
  **약관 개정 또는 robots.txt `Content-Signal` 변경 시**, **수집 주기를 1시간보다 줄이려 할 때**,
  **미인용 조문을 열람할 때** — 재배포·부하 판정을 다시 한다.

⚠️ **이 트리거에는 기계 감지 수단이 없다.** `no-health-data-files` 훅은 `files` 정규식이
**확장자**(`.csv`·`.parquet` 등)를 보므로 `.md`·`.py` **본문에 값을 옮겨 적으면 통과**하고,
`gitleaks`는 원천 데이터를 대상으로 하지 않는다. 관측점은 **`reviewer` 보안 점검(체크리스트 E)과 사람뿐**이다.
노트북 셀 출력에 대해 [publishing.md](../conventions/publishing.md)가 이미 명명한 것과 같은 형태의
공백이며, [risk.md](../risk.md)가 *"감지 축은 의도적 공백"* 으로 선언한 범위 안이다.
⇒ 위 트리거를 **자동으로 걸리는 것으로 읽지 않는다.**
