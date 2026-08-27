# 보안·데이터 거버넌스 정책 (Security Policy)

이 프로젝트가 지키는 보안·데이터 취급 **정책**을 **ISMS-P 인증기준**과 **다루는 데이터셋에 걸린
규제**에 매핑해 한 곳에서 밝힌다. 후자는 데이터셋에 따라 갈리며, 현재 적용되는 것은
**의료데이터 보안 규제**다([§0](#0-전제--이-프로젝트의-데이터-성격-중요)).

## 취약점 신고 (Reporting a Vulnerability)

이 저장소는 **학습·연구용 데이터 파이프라인 스터디**이며 **배포된 서비스가 없다.**
모든 구성요소는 로컬 개발 환경(Docker Compose · kind 클러스터)에서만 동작한다.

- **저장소 코드·설정의 결함**은 [GitHub Issues](https://github.com/Melting-Face/pipeline-study/issues)로 알려 주기 바란다.
- **원천 데이터 취급·DUA 관련 문의**는 아래 [§0](#0-전제--이-프로젝트의-데이터-성격-중요)의 데이터 성격을 먼저 참고한다.
- 이 저장소에는 **원천 데이터·크리덴셜이 포함되지 않는다**(§0 철칙). 그런 파일을 발견했다면
  공개 Issue 대신 저장소 소유자에게 직접 알려 주기 바란다.

## 0. 전제 — 이 프로젝트의 데이터 성격 (중요)

이 저장소는 도메인이 다른 여러 데이터셋을 같은 파이프라인 패턴으로 다루는 학습 프로젝트다.
**데이터 취급 통제는 프로젝트가 아니라 데이터셋에 걸린다.** 판정 조건은 두 갈래다.

> **데이터셋에 DUA·재배포 제한이 걸려 있거나, 개인정보·가명정보를 포함하면**
> 원천 데이터 커밋 금지·재식별 금지·소규모 셀(관례상 5 미만) 마스킹을 적용한다.

두 갈래인 이유는 **의무의 출처가 다르기 때문**이다. 재배포 제한은 **계약**(DUA)에서 나오지만
재식별 금지는 **데이터의 성격**에서 나온다(개인정보보호법 제28조의5). 계약 축만 조건으로 걸면
DUA 없는 국내 가명정보 데이터셋에서 **법상 의무가 남는데 문서상 통제는 사라진다.**

**적용 대상(현재): MIMIC-IV · eICU-CRD.** 아래가 그 근거이며, 이 절 이하 통제는 전부 이 대상에 대한 것이다.

두 데이터셋은 **비식별(de-identified) 연구용 공개 데이터셋**([MIMIC-IV](https://physionet.org/content/mimiciv/) ·
[eICU-CRD](https://physionet.org/content/eicu-crd/))이다. 미국
**HIPAA Safe Harbor** 기준(18개 식별자 제거·진료일자 이동)으로 비식별되었고, **PhysioNet
Credentialed Health Data License + DUA**(데이터 이용 협약, 재식별 시도 금지·인증 교육 이수)로 배포된다.

따라서 이 프로젝트는 국내 **개인정보/의료데이터 처리 의무 주체가 아니며**, 원천 데이터에는
직접 식별정보가 없다. 다만 아래 두 가지 이유로 ISMS-P·의료데이터 보안 통제를 **매핑·문서화**한다.

1. **DUA 준수 의무** — 재식별 금지·크리덴셜 관리·원천 데이터 비공개는 협약상 실제 의무다.
2. **학습·확장 대비** — 향후 실제 (가명)의료데이터를 다루는 파이프라인으로 확장할 때 통제 공백을
   미리 식별한다. 국내법상 MIMIC/eICU 같은 연구용 데이터는 **개인정보보호법 제28조의2(가명정보의
   처리 특례 — 과학적 연구)** 범주에 대응한다.

🔴 **미판정 데이터셋은 통제 적용이 기본값이다** — 판정해서 여기 적은 뒤에만 해제한다.
기본값이 반대면 *판정을 빠뜨린 데이터셋*과 *제한이 없는 데이터셋*이 같은 결과를 내
"안 걸린다"와 "안 봤다"가 구분되지 않는다. 제한이 없다는 판정도 **결과이므로 적는다.**

> **철칙(governance)**: 원천 데이터(csv.gz)·`.env`·크리덴셜은 **공개 git 저장소에 커밋하지 않는다.**
> 데이터는 SeaweedFS(오브젝트 스토리지)에만 두고, 저장소에는 코드·스키마·문서만 둔다.

## 1. 규제·표준 개요

### 1-1. ISMS-P (정보보호 및 개인정보보호 관리체계 인증)

개인정보보호위원회·과학기술정보통신부가 운영하고 KISA가 심사하는 국내 통합 인증제도.
**2023.11 개정 인증기준** 기준 3개 영역 **101개 인증기준**으로 구성된다.

| 영역 | 인증기준 수 | 세부항목 | 내용 |
| --- | --- | --- | --- |
| 1. 관리체계 수립 및 운영 | 16 | 42 | 관리체계 기반·위험관리·운영·점검개선(라이프사이클) |
| 2. 보호대책 요구사항 | 64 | 195 | 12개 분야: 정책·조직·인적·물리·인증권한·접근통제·암호화·개발보안·운영·사고대응·재해복구 |
| 3. 개인정보 처리 단계별 요구사항 | 21 | 91 | 수집·보유이용·제공·파기·정보주체 권리 등 생명주기별 보호조치 |

> ISMS(정보보호)와 ISMS-P(정보보호+개인정보)로 나뉘며, 개인정보를 다루면 영역 3까지 포함하는 ISMS-P가 대상.

### 1-2. 의료데이터 보안 관련 법·가이드라인 (현행 데이터셋 기준)

| 근거 | 핵심 요지 | 이 프로젝트 관련성 |
| --- | --- | --- |
| **개인정보보호법 제28조의2** (가명정보 처리 특례) | 통계작성·**과학적 연구**·공익적 기록보존 목적은 정보주체 동의 없이 가명정보 처리 가능 | 연구용 MIMIC/eICU 활용의 국내 대응 근거 |
| **개인정보보호법 제28조의4** (안전조치의무) | 추가정보(복원키)를 **분리 보관·관리**, 기술적·관리적·물리적 안전조치 | 크리덴셜/재식별키 분리, 접근통제 원칙 |
| **개인정보보호법 제28조의5** (금지의무) | 특정 개인을 알아보기 위한 가명정보 처리 **금지**(재식별 금지) | DUA의 재식별 금지 조항과 정합 |
| **보건의료데이터 활용 가이드라인** (개인정보위·보건복지부) | 보건의료 가명정보 처리 절차·심의(DRB)·안전조치. 2020.9 최초 → 2022.1·2024.1 개정 → **2025.12.31 시행** | 의료데이터 가명처리 절차의 국내 표준 |
| **HIPAA Safe Harbor** (미국) | 18개 식별자 제거 시 비식별로 간주 | MIMIC/eICU 비식별의 실제 근거 |
| **PhysioNet Credentialed License · DUA** | 인증 교육 이수·재식별 금지·데이터 재배포 제한 | 데이터 접근·취급의 실제 계약상 의무 |

## 2. ISMS-P 인증기준 ↔ 통제 방침 매핑

각 인증기준에 대해 **이 프로젝트가 무엇을 하기로 정했는지**(통제 방침)와
**그 통제가 어디까지를 보증하는지**(보증 범위)를 밝힌다.
통제가 있다는 사실과 그 통제가 무엇까지 보는지는 다르므로 둘을 나란히 적는다.

### 2-1. 영역 2 — 보호대책 요구사항

| 인증기준 | 통제 방침 | 보증 범위 |
| --- | --- | --- |
| **2.5 인증 및 권한관리** | 서비스 계정을 `.env` 크리덴셜로 분리한다. OCI API 키는 로컬 생성·공개키만 업로드하고, SSH 키는 용도별로 분리하며, kubeconfig는 권한 `600`으로 둔다 | 키의 생성·보관·분리까지. 서비스별 RBAC·최소권한 매트릭스는 범위 밖이다([§4-3](#4-3-서비스-rbac최소권한-25--26)) |
| **2.6 접근통제** | 서비스는 내부 네트워크로 격리하고 비밀 설정은 `:ro`로 마운트한다. 공개 노드의 인그레스는 `/32` 화이트리스트로 좁힌다 | 호스트 경계까지. 클러스터 내부 파드 간 통신(`NetworkPolicy`)과 관리 UI 인증은 범위 밖이며, **그 범위 밖에 인증 없는 Dagster UI·GraphQL이 실제로 있다**(§4-3) |
| **2.7 암호화 적용** | 비밀정보는 하드코딩하지 않고 참조로 주입한다(`dg.EnvVar`·`${ENV:...}`). 개인키·`*.tfstate`·`*.tfvars`·kubeconfig는 gitignore + 권한 `600` | 전송 구간과 비밀의 저장소 유입까지. **저장 암호화(at-rest)는 범위 밖**이며 실서비스 확장의 전제다([§4-2](#4-2-저장전송-암호화-27)) |
| **2.8 정보시스템 도입 및 개발 보안** | 같은 게이트(`gitleaks`·`detect-private-key`·`ruff`·`sqlfluff`·`shellcheck`·`hadolint`)를 **로컬 pre-commit과 서버측 CI 양쪽**에 건다. 이미지 `latest` 태그를 금지한다 | 로컬 훅을 우회해도 **서버측에서 다시 걸린다**. 단 커밋 메시지 검사는 **PR 경로에서만** 돌고, 의존성 스캔(SCA)·스킬 설치 경로는 범위 밖이다 |
| **2.9 시스템 및 서비스 운영관리** | Docker 로그를 보존 한도와 함께 남기고(`max-size`×`max-file`), healthcheck와 `depends_on` 조건, `deploy.resources`를 선언한다 | **선언된 서비스에 한한다.** 적용 범위의 실측은 `architectures/monitoring.md`에서 관측 시각과 함께 읽는다 |
| **2.10 시스템 및 서비스 보안관리** | UTC 저장 / KST 표시로 로그 타임스탬프를 정합화한다 | 타임스탬프 일관성까지. 중앙 감사 로그 수집·보관은 범위 밖이다([§4-5](#4-5-감사-로그접속기록-210--32)) |
| **2.11 사고 예방 및 대응** | 관측 수단은 **무엇을 두고 무엇을 안 두는지 선언**한다("안 둔다"도 선언한다 — 빠뜨린 것과 구분하기 위해). 규칙 정본은 `conventions/monitoring.md` | **규칙 문서까지.** 침해 대응 절차·알림 경로는 범위 밖이다. ⚠️ **문서가 생긴 것이 통제가 생긴 것은 아니다** |
| **2.12 재해복구 및 업무연속성** | 카탈로그 Postgres는 CloudNativePG가 관리하며, 백업 경로(Barman Cloud 플러그인)를 opt-in으로 둔다 | 백업 **경로**까지. 활성화는 opt-in이며, **백업 대상이 같은 장애 도메인이면 DR이 아니다**([§4-4](#4-4-백업복구-212)) |

**표에 담기지 않는 단서 2건**

- **2.7의 조건부 예외** — 크리덴셜의 SQL DDL 기재는 원칙적으로 금지하나, Iceberg JDBC 카탈로그의
  `CREATE CATALOG`는 환경변수 자격증명 체인이 없어 회피 불가다. **5개 조건을 전부 충족할 때만**
  허용하며 정본은 `conventions/k8s.md` §9-2다(여기 중복 서술하지 않는다).
- **2.8의 스킬 공급망** — 에이전트 스킬은 **실행 컨텍스트에 주입되는 외부 코드**이므로 출처 등급
  (A 1차 / B 준1차 / C 2차 / D 미상)별로 통제하고, 실행 파일 포함은 등급 무관 `security` 검토를 거친다.
  정본은 `docs/skills.md`. **lock 등재(무결성 고정)와 출처 등급은 다른 축**이라 섞지 않는다.

### 2-2. 영역 3 — 개인정보 처리 단계별 (연구 데이터 대응)

| 인증기준 | 통제 방침 | 보증 범위 |
| --- | --- | --- |
| **3.1 수집 시 보호조치** | 비식별 데이터만 수집한다(원천이 이미 Safe Harbor 비식별). 원천 데이터의 저장소 커밋을 금지한다(§0 철칙) | 수집 대상의 성격까지. 원천의 비식별 품질은 PhysioNet에 의존한다 |
| **3.2 보유 및 이용 시 보호조치** | 데이터는 SeaweedFS에만 상주시키고 코드·문서와 분리한다 | 저장 위치의 분리까지. 접근기록(누가 조회했는가) 로깅은 범위 밖이다 |
| **3.4 파기 시 보호조치** | Iceberg 스냅샷 만료 + orphan 정리를 주간 잡으로 자동화한다(`defs/maintenance.py`) | 잡이 **인자로 받은 테이블**까지. 카탈로그에서 지워진 네임스페이스는 범위 밖이다([§4-1](#4-1-iceberg-snapshot-보존파기-자동화-34--29)) |

> **재식별 금지(제28조의5·DUA)**: 어떤 파이프라인·분석도 특정 개인 재식별을 시도하지 않는다.
> 외부 데이터와의 결합은 DUA·가이드라인 심의 없이는 수행하지 않는다.

### 2-3. 분석·공개 산출물 통제 (노트북·리포트·공개물)

파이프라인은 데이터를 **저장소 밖**(SeaweedFS)에 두지만, **분석 산출물은 저장소 안으로 들어온다.**
`.ipynb` 셀 출력과 리포트의 표·그림은 **조회 결과를 그대로 박제**한다.

여기에 더해 **저장소 밖으로 나가는** 경로가 셋 있다. 방향이 반대라 통제도 갈린다.

1. **공개물** — `docs/posts/**`(블로그·공유 자료). 작성 워커 `tech-writer`.
2. **외부 질의** — `researcher`의 `WebSearch`·`WebFetch`. **질의문 자체가 외부 발신**이다.
3. 🔴 **데이터 반출** — `data-extractor`의 추출물. 착지는 **저장소 밖** `$DATA_EXTRACT_DIR`
   (기본 `~/extracts`)이며, **앞의 둘과 달리 원천 데이터 그 자체**가 나간다.
   경로 강제는 `scripts/worker_path_guard.py` — 저장소 안은 `deny`, 반출 경로 밖도 **`deny`**.
   실행 **전** `security` 사전 컨펌이 필수 게이트다.

> **아래 표는 「점검 대상 목록」이지 설명이 아니다** — 여기 없는 경로는 다음 `security` 점검이
> **보지 않는다**. 반출 경로가 생기면 문서 미화가 아니라 **통제 목록의 정확성** 문제로 여기 먼저 적는다.

작업 규칙 정본은 [`conventions/analysis.md`](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/analysis.md)(분석)와
[`conventions/publishing.md`](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/publishing.md)(공개)이고, 이 절은 그 거버넌스 근거다.

| 통제 | 수단 | 근거 |
| --- | --- | --- |
| 셀 출력 커밋 차단 | `nbstripout` pre-commit 훅(출력·실행횟수 제거) | `.pre-commit-config.yaml` |
| 자동 스냅샷 차단 | `.gitignore`의 `**/.ipynb_checkpoints/` | Jupyter가 출력째로 스냅샷을 남긴다 |
| 원천 데이터 파일 차단 | `.gitignore` + pre-commit 훅 2층(`*.csv`·`*.parquet`) | 커밋 `65345fc` |
| 실행 산출물 잔류 차단 | `nbconvert --execute` 사본을 **검증 직후 삭제** | `docs/test.md` §6 |
| 개별 행 노출 차단 | 리포트에 개별 레코드 금지, **소규모 셀(관례상 5 미만) 마스킹** | 3.3 · DUA |
| 재식별 금지 | 외부 데이터 결합은 심의 없이 하지 않는다 | 제28조의5 · DUA |
| 공개물 반출 차단 | `tech-writer` 쓰기 경로 hook 강제 + `security` 컨펌 게이트 + **사람이 발행**(워커 발행 금지) | `conventions/publishing.md` §5 · DUA 재배포 제한 |
| 판정 근거 문서 개찬 차단 | 같은 가드의 **`except` 축** — 판정 대상이 자기 판정 근거를 못 고친다 | `conventions/agents.md` §권한 매트릭스 |
| 외부 질의 유출 차단 | `researcher` 질의 규율(내부 데이터 금지) | `conventions/publishing.md` §7 |
| 외부 발신(egress) 차단 | `permissions.deny`의 발신 동사 · `ask`의 `gh api`·`git push`·`scp`/`rsync` | `conventions/publishing.md` §7 |

- 🔴 **`gitleaks`는 크리덴셜 패턴을 잡지 원천 데이터를 잡지 못한다.** 자동 검사 통과를 안전으로 읽지
  않는다 — 분석 산출물의 위험은 **비밀값이 아니라 데이터 그 자체**다.
- **훅을 `--no-verify`로 우회해 커밋하지 않는다.** 우회하면 위 차단 층이 동시에 무력화된다.
- **저장소는 공개(public)이고 푸시는 사실상 비가역이다** — force-push해도 캐시·포크·이벤트가 남는다.
  따라서 통제 지점은 푸시가 아니라 **커밋 이전**이며, 분석 산출물은 **공유 직전 수동 관문**
  (`docs/test.md` §6)을 거친다.
- **ISMS-P 대응**: 산출물 공유·반출은 **3.3(제공 시 보호조치)**, 조회 결과의 저장소 유입은
  **3.2(보유·이용 시 보호조치)** 에 대응한다.

## 3. 점검·감사 체계

정책이 지켜지는지 **어떻게 확인하는가**를 정한다. 점검 결과(현행 실태·발견 항목)는
이 문서가 아니라 비공개 posture 기록에 쌓인다.

### 3-1. 점검 수단 — `security` 서브에이전트

준수 여부 점검은 AI 세션의 보안 담당 워커 `.claude/agents/security.md`에 배정한다
(계층 규약은 `docs/conventions/agents.md`).

- **읽기 전용**이다 — 발견을 심각도(높음·중간·낮음)로 **반환만** 한다.
  수정·커밋은 승인 후 별도 워커가 한다(승인 게이트).
- 점검 범위는 이 문서(ISMS-P 매핑·§0 철칙)와 `conventions/general.md`·`operations.md`·
  `conventions/publishing.md`(외부 공개)·인프라 컨벤션(terraform·k8s·docker)이며,
  **규칙을 새로 만들지 않고 정본을 집행**한다.
- 보고 시 **비밀값 원문은 마스킹**한다. 내장 `/security-review`는 변경분 취약점을 보고,
  이 워커는 **거버넌스·컨벤션 준수**를 본다 — 병행한다.
- 권장 시점: 커밋 전, 인프라 변경(terraform·k8s·docker) 리뷰 시, §2 매핑표 갱신 시.

### 3-2. 점검 범위에 관한 규칙

1. **크리덴셜 유출 점검 범위에는 숨김 파일(`.*`)·셸 히스토리·홈 디렉터리를 포함한다.**
   로그 디렉터리만 훑는 점검은 범위 밖을 **조용히 "이상 없음"으로 계상**한다.
2. **방어 도구가 여럿이면 각자 무엇을 보는지 표로 적는다.** 겹의 개수가 아니라
   **겹의 합집합이 대상을 덮는가**가 척도다. 도구마다 보는 대상이 달라 각자 정상 작동해도
   틈이 남을 수 있고, 그때도 화면은 전부 초록불이다.

두 규칙은 처방이 다르다 — 1은 **한 도구의 범위가 빗나간** 경우, 2는
**여러 도구의 범위가 안 겹쳐 틈이 생긴** 경우다.

### 3-3. 새로 건 게이트는 일부러 위반시켜 본다

통제를 추가하면 **의도적으로 위반시켜 실제로 막히는지** 확인하고, **대조군**(막히지 않아야 하는
경로)이 통과하는 것도 함께 본다. 선언만으로는 죽은 규칙과 살아 있는 규칙이 구분되지 않는다.

## 4. 통제 구현 절차

각 절차의 **구현 상태**는 항목마다 다르다 — 이 절은 *무엇을 어떻게 하기로 했는가*를 적고,
현재 어디까지 됐는지는 posture 기록에서 읽는다. 설정 파일·환경변수는 §0 철칙(비밀은 참조·커밋 금지)을 따른다.

### 4-1. Iceberg snapshot 보존·파기 자동화 (3.4 · 2.9)

유지보수를 안 돌리면 작은 파일·스냅샷이 무제한 누적된다(`docs/operations.md` §2).
**안전 순서: compact → expire snapshots → remove orphan files.** 컴팩션이 새 파일·스냅샷을 만든 뒤
만료가 옛 작은 파일 참조를 풀고, orphan 정리가 잔여를 제거한다.

**구현**: `dagster_project/defs/maintenance.py` — Dagster 잡·스케줄(매주 일요일 03:00 KST).
카탈로그 설정 중복 없이 이미 등록된 대용량 테이블 `IcebergTableResource` 바인딩을 단일 출처로
재사용한다. 위 **안전 순서**를 op 의존성(`dg.In(dg.Nothing)`)으로 강제한다.

| 단계 | 실행 | 근거 |
| --- | --- | --- |
| 1. 컴팩션 | **Spark** 프로시저 `rewrite_data_files` | 청크 append로 쌓인 small-files 병합 |
| 2. 스냅샷 만료 | pyiceberg `table.maintenance.expire_snapshots().older_than(dt).commit()` (`older_than`는 tz-aware datetime) | 보존기간 `SNAPSHOT_RETENTION_DAYS`(기본 7일) 경과분 |
| 3. orphan 정리 | **Spark** 프로시저 `remove_orphan_files` | pyiceberg 미지원. 보존기간 `ORPHAN_RETENTION_DAYS`(기본 7일) |

접속은 공식 통합 `dagster-pyspark`의 `LazyPySparkResource`로 Spark Connect에 붙는다
(유지보수는 재설계에서 Trino → Spark로 이관됐다).

> **`remove_orphan_files`는 warehouse를 Hadoop FileSystem으로 나열**한다(카탈로그가 *모르는* 파일을
> 찾는 것이 목적이라 S3FileIO로 대체 불가). Spark Connect 서버에 `spark.hadoop.fs.s3*` 설정이
> 있어야 하며, 없으면 `UnsupportedFileSystemException`으로 죽는다.

> **스캔 진입점은 테이블이다.** 인자로 받은 테이블의 location 하위만 스캔하므로,
> **테이블이 0개인 네임스페이스의 잔재는 이 잡의 사정거리 밖**이다. 네임스페이스를 지울 때는
> **선(先) orphan 정리**를 절차에 넣는다 — *"카탈로그에서 지웠다"는 "지워졌다"가 아니다.*

> 보존기간(`SNAPSHOT_RETENTION_DAYS`) 확정 값은 `docs/operations.md` §2 표에 반영한다.

### 4-2. 저장/전송 암호화 (2.7)

**저장 시 암호화(at-rest)**

| 대상 | 방법 |
| --- | --- |
| SeaweedFS(오브젝트) | **SSE-S3**(AES-256, 서버 관리 키·envelope 암호화). S3 API로 업로드 시 서버 측 암호화 적용 |
| Postgres(Iceberg 카탈로그·Dagster DB) | 커뮤니티 Postgres는 네이티브 TDE 미지원 → **볼륨/디스크 암호화**(LUKS·클라우드 EBS 암호화)로 대체 |
| Iceberg 데이터 파일 | SeaweedFS SSE-S3에 위임(데이터는 warehouse 버킷에 저장) |

**전송 시 암호화(in-transit)**

| 구간 | 방법 |
| --- | --- |
| S3(SeaweedFS) | `security.toml`로 TLS 구성(gRPC·HTTPS 분리). 내부 격리망에서는 평문을 허용하되 **외부 노출 시 HTTPS 필수** |
| Trino | `http-server.https.enabled=true` + 키스토어. 비밀번호 인증은 **TLS 필수** |
| Postgres | `ssl=on` + `server.crt`/`server.key`, 클라이언트 `sslmode=require`. 카탈로그 PG는 CNPG가 자체 CA와 서버 인증서를 자동 발급해 서버 측 TLS를 켠다 |

> 내부 격리망(단일 호스트 compose)에서는 평문이 허용되나, **관리 UI·서비스를 호스트 밖으로 노출하면
> 전 구간 TLS를 적용**한다.

> **CNPG의 CA 개인키는 네임스페이스 Secret에 놓인다** — 같은 네임스페이스의 secret read 권한이
> 곧 CA 키 접근이므로, RBAC을 짤 때 이 등가성을 전제로 한다([§4-3](#4-3-서비스-rbac최소권한-25--26)).

### 4-3. 서비스 RBAC·최소권한 (2.5 · 2.6)

서비스별 계정을 **분리**하고 필요한 권한만 부여한다.

**Dagster(in-cluster)** — Dagster OSS에는 **인증 계층이 없다.** UI와 `/graphql`이 같은 포트로 나가고
거기에 **런 실행·종료·삭제·자산 wipe·스케줄 on/off** 뮤테이션이 포함된다. Ingress 하나로 UI만 여는 것은
불가능하다 — 노출 범위는 포트가 아니라 그 포트가 제공하는 API가 정한다(Flink와 같은 축).

| 통제 | 상태 |
| --- | --- |
| kind `extraPortMappings`의 `listenAddress: 127.0.0.1` | ✅ — **현재 유일한 실효 통제**(LAN 도달 불가) |
| webserver `automountServiceAccountToken: false` | ✅ — UI가 탈취돼도 파드에 k8s 토큰이 없다(기능 손실 0) |
| daemon `Role`을 ns 한정 4 verb로 제한 | ✅ — `sparkapplications`·`pods`·`pods/log`뿐 |
| `dagster-webserver --read-only` | ❌ **쓸 수 있는데 안 쓴다** — UI가 주 조작 수단이라 무력화된다 |
| `NetworkPolicy` · TLS Ingress | ❌ 저장소 전체가 미도입 — Dagster만 예외로 둘 근거가 없다 |

⚠️ **이 판정은 「로컬 단독 실행」 전제 위에 선다.** [architectures/oci.md](architectures/oci.md)의
클라우드 스택을 재개해 **인터넷에 면한 노드**가 생기면 이 절을 **먼저 다시 읽는다** —
그 시점에는 인증 없는 GraphQL 뮤테이션이 즉시 최상위 위험이 된다.
`telemetry`는 껐다(무엇이 나가는지 확인하지 않은 외부 발신은 통제로 볼 수 없다).

**CloudNativePG(카탈로그·메타 PG)** — RBAC이 **2계층**이다.
컨트롤러 `ClusterRole/cloudnative-pg`는 **전 네임스페이스의 `secrets`·`configmaps`·`services`에 RW**를 갖는다
(cluster-wide 설치 · CNPG 아키텍처상 불가피). 반면 인스턴스용 `Role/catalog-postgres`는 `resourceNames`로
**자기 시크릿·configmap·Cluster에만** 한정돼 최소권한을 지킨다.
→ 방어 심화 과제: 차트의 감시 네임스페이스 제한 검토, `NetworkPolicy`로 5432 접근 제한.

**SeaweedFS S3** — `-s3.config=s3.json`의 `identities`로 서비스별 accessKey·최소 action 지정
(`Admin`/`Read`/`Write`/`List`/`Tagging`). *주의: `-s3.iam.config`는 identities 미지원 → `-s3.config` 사용.*

```json
{
  "identities": [
    { "name": "dagster-writer",
      "credentials": [{ "accessKey": "${DAGSTER_S3_KEY}", "secretKey": "${DAGSTER_S3_SECRET}" }],
      "actions": ["Read:warehouse", "Write:warehouse", "List:warehouse", "Tagging:warehouse"] },
    { "name": "trino-reader",
      "credentials": [{ "accessKey": "${TRINO_S3_KEY}", "secretKey": "${TRINO_S3_SECRET}" }],
      "actions": ["Read:warehouse", "List:warehouse"] }
  ]
}
```

**Trino** — file-based access control로 카탈로그·스키마·테이블·컬럼 권한을 rules.json에 선언
(위→아래 첫 매칭 규칙 적용, 약 30초 자동 리로드).

```properties
# etc/access-control.properties
access-control.name=file
security.config-file=/etc/trino/rules.json
```

필요 시 `etc/password-authenticator.properties`(`password-authenticator.name=file`)로 사용자 인증을
추가한다(단, **HTTPS·공유 시크릿 필수**).

**Postgres** — 서비스별 role을 만들고 최소 GRANT(예: 카탈로그 DB는 스키마 사용 권한만).

```sql
CREATE ROLE trino_ro LOGIN PASSWORD :'pw';
GRANT CONNECT ON DATABASE iceberg_catalog TO trino_ro;
GRANT USAGE ON SCHEMA public TO trino_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO trino_ro;
```

> 계정 분리 시 [§4-2](#4-2-저장전송-암호화-27) 전송 암호화와 함께 적용하고,
> 계정·권한 매트릭스를 `docs/operations.md`에 표로 남긴다.

### 4-4. 백업·복구 (2.12)

| 대상 | 백업 | 복구 |
| --- | --- | --- |
| 카탈로그 Postgres(K8s, CNPG) | **Barman Cloud 플러그인**(CNPG-I)으로 base backup + WAL 아카이브 → SeaweedFS S3. `INSTALL_CNPG_BACKUP=true`로 opt-in | CNPG `Cluster`의 `bootstrap.recovery`로 복원(PITR 지원) |
| 메타 Postgres(compose, Dagster DB) | 논리 백업 `pg_dump`(정기 cron) 또는 물리 백업 `pg_basebackup` + **WAL 아카이브**(PITR) | `pg_restore`(논리) / base backup + WAL 재생(물리) |
| SeaweedFS `s3://warehouse` | 버킷 객체 복제(다른 호스트/버킷) 또는 볼륨 백업 | 복제본에서 복원 후 카탈로그 정합 확인 |

> 🔴 **백업 대상이 같은 장애 도메인이면 DR이 아니다** — 클러스터 내부 스토리지로 백업을 보내면
> 원본 PVC와 백업본이 **같은 노드·같은 호스트 디스크**에 놓여 노드 유실 시 함께 사라진다.
> 목적을 **논리 오류·실수 복구**로 한정하고, 진짜 DR이 필요해지면 목적지를 호스트 밖으로 뺀다.
> 카탈로그 DB가 담는 것은 테이블 식별자·메타 포인터라 **PHI 유출 경로는 아니다.**
>
> **정합 주의**: Iceberg는 메타데이터(Postgres 카탈로그)와 데이터(SeaweedFS)가 분리 저장되므로
> **둘의 백업 시점을 맞춘다**. 카탈로그만 복구하면 없는 데이터 파일을 가리켜 읽기 실패가 난다.
> 백업 주기·보존기간은 `docs/operations.md` §2 표에 확정한다.

### 4-5. 감사 로그·접속기록 (2.10 · 3.2)

- 쿼리 이벤트 리스너(누가·언제·무엇을 조회)와 오브젝트 스토리지 접근 로그를 중앙 수집한다.
- UTC 저장/KST 표시 정책으로 로그 타임스탬프를 정합화한다(`conventions/timezone.md`).
- (확장 시) 개인정보 접속기록 보관은 개인정보보호법 시행령상 **최소 보관기간**을 확인해 반영한다.

## 참고

- ISMS-P 인증기준 안내서(2023.11) — 개인정보보호위원회: https://www.privacy.go.kr/front/bbs/bbsView.do?bbsNo=BBSMSTR_000000000049&bbscttNo=20677
- ISMS-P 인증 소개 — KISA: https://isms.kisa.or.kr/
- 개인정보 보호법 제28조의2 (가명정보의 처리 등) — 국가법령정보센터: https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=213857
- 보건의료데이터 활용 가이드라인 — 개인정보보호위원회: https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=D010030000
- HIPAA De-identification (Safe Harbor) — HHS: https://www.hhs.gov/hipaa/for-professionals/privacy/special-topics/de-identification/index.html
- MIMIC-IV (PhysioNet, Credentialed License·DUA): https://physionet.org/content/mimiciv/
- eICU-CRD (PhysioNet): https://physionet.org/content/eicu-crd/

### 통제 구현 절차(§4) 도구 문서

- Apache Iceberg — Maintenance(expire snapshots·remove orphan files): https://iceberg.apache.org/docs/latest/maintenance/
- SeaweedFS — S3 Configuration(identities·SSE-S3): https://github.com/seaweedfs/seaweedfs/wiki/S3-Configuration
- SeaweedFS — Security Configuration(TLS `security.toml`): https://github.com/seaweedfs/seaweedfs/wiki/Security-Configuration
- Trino — File-based access control: https://trino.io/docs/current/security/file-system-access-control.html
- Trino — TLS/HTTPS & Password authentication: https://trino.io/docs/current/security/tls.html
- PostgreSQL — Backup & Restore / SSL: https://www.postgresql.org/docs/current/backup.html

### 저장소 내 관련 문서

- [`conventions/general.md`](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/general.md) — 비밀정보 취급
- [`operations.md`](https://github.com/Melting-Face/pipeline-study/blob/main/docs/operations.md) — 환경변수 전파·보존정책
- [`conventions/publishing.md`](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/publishing.md) — 외부 공개 기준
- [`conventions/analysis.md`](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/analysis.md) — 분석 산출물 규칙
- [`dataset_schema.md`](https://github.com/Melting-Face/pipeline-study/blob/main/docs/dataset_schema.md) — 원천 스키마
