# 참고 문서 (외부 표준·출처)

이 프로젝트의 규칙·설계가 근거로 삼는 외부 표준과 문서를 모은다. 각 문서의 `참고` 섹션은
여기의 항목을 링크한다(단일 출처 — [`doc-sync.md`](doc-sync.md)).

## 코딩 철학·규칙

| 표준 | 용도 | 참조 문서 |
| --- | --- | --- |
| [PEP 20 — The Zen of Python](https://peps.python.org/pep-0020/) | 단순함·명시적·가독성 | [philosophy.md](philosophy.md) |
| [12-Factor App](https://12factor.net/config) | 설정/비밀정보는 환경변수 참조 | [philosophy.md](philosophy.md) · [operations.md](operations.md) |
| [Rule of Three / DRY](https://en.wikipedia.org/wiki/Rule_of_three_(computer_programming)) | 3회 반복부터 추출 | [philosophy.md](philosophy.md) |
| [PEP 8](https://peps.python.org/pep-0008/) · [PEP 257](https://peps.python.org/pep-0257/) | 스타일·docstring | [conventions/python.md](conventions/python.md) |
| [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) | docstring(Google 스타일) | [conventions/python.md](conventions/python.md) |
| [Conventional Commits](https://www.conventionalcommits.org/) | 커밋 메시지 규약 | [conventions/general.md](conventions/general.md) |

## 도구

| 도구 | 용도 | 참조 문서 |
| --- | --- | --- |
| [ruff](https://docs.astral.sh/ruff/) | Python lint·format | [conventions/python.md](conventions/python.md) |
| [sqlfluff](https://docs.sqlfluff.com/) | SQL lint·format (trino dialect) | [conventions/dbt.md](conventions/dbt.md) |
| [uv](https://docs.astral.sh/uv/) | 의존성·가상환경 | [conventions/python.md](conventions/python.md) |
| [Claude Code — 사용자 정의 subagent](https://code.claude.com/docs/ko/sub-agents) | 워커 프론트매터 규약(`tools`·`skills`·`disallowedTools`·`hooks`) | [skills.md](skills.md) · [conventions/agents.md](conventions/agents.md) |
| [Claude Code — Agent Skills](https://code.claude.com/docs/ko/skills) | 스킬 구조·`SKILL.md`·호출 경로 | [skills.md](skills.md) |

## 플랫폼·프레임워크

| 문서 | 용도 | 참조 문서 |
| --- | --- | --- |
| [Dagster](https://docs.dagster.io/) | 오케스트레이션·에셋·리소스 | [conventions/dagster.md](conventions/dagster.md) · [architectures/overview.md](architectures/overview.md) |
| [dagster-dbt](https://docs.dagster.io/integrations/dbt) | dbt 통합(`@dbt_assets`) | [conventions/dbt.md](conventions/dbt.md) |
| [dbt-trino](https://github.com/starburstdata/dbt-trino) | dbt Trino 어댑터 | [conventions/dbt.md](conventions/dbt.md) |
| [Apache Iceberg](https://iceberg.apache.org/) | 테이블 포맷(JDBC 카탈로그) | [architectures/overview.md](architectures/overview.md) |
| [Trino](https://trino.io/docs/current/) | 쿼리 엔진 | [architectures/overview.md](architectures/overview.md) · [resource-sizing.md](resource-sizing.md) |
| [SeaweedFS](https://github.com/seaweedfs/seaweedfs) | S3 호환 오브젝트 스토리지 | [architectures/overview.md](architectures/overview.md) |

## 처리·배포 기술 (architectures)

| 기술 | 상태 | 참조 문서 |
| --- | --- | --- |
| [Docker Compose](https://docs.docker.com/reference/compose-file/) | ✅ 채택(배포) | [architectures/docker.md](architectures/docker.md) · [conventions/docker.md](conventions/docker.md) |
| [Apache Spark](https://spark.apache.org/docs/latest/) | 🚧 채택·이행중(현행 **3.5.9** · **4.1 상향은 결정만 됨 · 이행 전** — 2026-08-23) | [architectures/spark.md](architectures/spark.md) |
| [Apache Flink](https://flink.apache.org/documentation/flink-stable/) | 🚧 채택·이행중(오퍼레이터 **기본 설치** · 배치 왕복 실증 2026-08-22 · ✅ **스트리밍 왕복 실증 2026-08-23** — 장수명 잡이 **초기 12행 + 증분 3행**을 처리, 삼중 증거(데이터 시차 · `engine-name=flink` · `flink.job-id` 일치)로 확인. 체크포인트 **79회 완료**(S3=SeaweedFS · RocksDB), 러너 **`flink-runner:0.3.0`**. 스트림 소스 **Iceberg bronze changelog로 이행 완료**, **Redpanda 미도입**은 기각이 아니라 **불필요해진 것**. 🔴 체크포인트 **복구 가능성은 `미확인`**) | [architectures/flink.md](architectures/flink.md) |
| [Kubernetes](https://kubernetes.io/docs/home/) | 🚧 채택·이행중 | [architectures/k8s.md](architectures/k8s.md) · [conventions/k8s.md](conventions/k8s.md) |
| [Helm](https://helm.sh/docs/) | 🔎 K8s 패키징 | [conventions/k8s.md](conventions/k8s.md) |
| [Prometheus](https://prometheus.io/docs/introduction/overview/) | 🔎 미채택 | [architectures/monitoring.md](architectures/monitoring.md) · [conventions/monitoring.md](conventions/monitoring.md) |

### 엔진 버전·조합 판정에 쓴 1차 출처 (2026-08-23 확인)

**"최신이 아니라 Iceberg가 지원하는 짝"** 규칙을 실제로 판정할 때 근거로 삼은 A등급 1차 출처다.
🔴 **여기 모은 것은 결정의 근거이지 이행 기록이 아니다.** 🔴 **다만 두 축의 현재 상태는 갈렸다** —
**Spark 4.1 상향은 여전히 🟡 「결정 완료 · 이행 전」**(아무것도 굽지 않았다)이고,
**스트림 소스의 Iceberg changelog 전환은 2026-08-23 실행으로 이행됐다**(위 Flink 행 ·
[architectures/flink.md](architectures/flink.md) §스트리밍 왕복 실증).
**같은 날짜의 「결정」과 「실측」을 한 줄로 뭉개지 않는다.**

| 출처 | 용도 | 참조 문서 |
| --- | --- | --- |
| [Apache Spark 4.1.0 릴리스](https://spark.apache.org/releases/spark-release-4-1-0.html) | 상향 목표 버전의 GA 확인 | [architectures/spark.md](architectures/spark.md) |
| [Apache Spark 4.0.0 릴리스 노트](https://spark.apache.org/releases/spark-release-4-0-0.html) | 4.x 런타임 전제 — **Scala 2.13 전용**(`SPARK-45314`) · **JDK 17+**(`SPARK-45315`) | [architectures/spark.md](architectures/spark.md) |
| [Iceberg Multi-Engine Support](https://iceberg.apache.org/multi-engine-support/) | 엔진 지원 매트릭스 — Spark 상한이 **4.1**인 근거(**4.2 행 자체가 없다**) | [architectures/spark.md](architectures/spark.md) · [architectures/flink.md](architectures/flink.md) |
| [apache/spark-kubernetes-operator Releases](https://github.com/apache/spark-kubernetes-operator/releases) | 저장소가 핀한 chart 1.8.0(appVersion 1.0.0)의 **"drop Spark 3.5"** — 현행 조합이 지원 밖인 근거 | [architectures/spark.md](architectures/spark.md) |
| [Hadoop 3.4.0 릴리스 노트](https://hadoop.apache.org/docs/r3.4.0/hadoop-project-dist/hadoop-common/release/3.4.0/RELEASENOTES.3.4.0.html) | S3A의 **AWS SDK v2 전환**(`HADOOP-18073`)·**v1 bundle 제거**(`HADOOP-18820`) — 버전이 아니라 **좌표가 바뀌는** 근거 | [architectures/spark.md](architectures/spark.md) |
| [Apache Flink CDC 3.6.0 릴리스 공지](https://flink.apache.org/2026/03/30/apache-flink-cdc-3.6.0-release-announcement/) | 지원 Flink가 **1.20.x · 2.2.x** — 현행 **2.1**과 짝이 아니라 DB CDC를 비권장한 근거 | [architectures/flink.md](architectures/flink.md) |
| [Iceberg Flink Writes](https://iceberg.apache.org/docs/latest/flink-writes/) | 싱크가 **체크포인트 완료 시점에 커밋** · upsert는 **format v2 + primary key** 필수 | [architectures/flink.md](architectures/flink.md) |

🔴 **Iceberg 매트릭스는 정본 소스 경로를 틀리기 쉽다.** 위 페이지의 원본은 `apache/iceberg`
저장소의 **`site/docs/multi-engine-support.md`** 다. `docs/multi-engine-support.md`는 **부재**하고,
별도 저장소 `apache/iceberg-docs`의 사본은 **2023-10-06에서 멈춘 구식**이다. 경로를 잘못 잡으면
**살아 있는 구식 사본이 정본 대신 답한다** — 같은 계열의 실패 사례는
[architectures/spark.md](architectures/spark.md) §발견 2(죽은 `dbt-labs/dbt-spark` 저장소가 "0건"으로 답한 건).

## 보안·규제 (의료데이터)

| 표준·법령 | 용도 | 참조 문서 |
| --- | --- | --- |
| [ISMS-P 인증기준(2023.11)](https://www.privacy.go.kr/front/bbs/bbsView.do?bbsNo=BBSMSTR_000000000049&bbscttNo=20677) | 정보보호·개인정보보호 관리체계 101 인증기준 | [security.md](security.md) |
| [개인정보 보호법](https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=213857) | 가명정보 처리 특례(제28조의2·4·5) | [security.md](security.md) |
| [보건의료데이터 활용 가이드라인](https://www.pipc.go.kr/np/cop/bbs/selectBoardArticle.do?bbsId=BS217&mCode=D010030000) | 보건의료 가명정보 처리 절차·심의(DRB) | [security.md](security.md) |
| [HIPAA De-identification (Safe Harbor)](https://www.hhs.gov/hipaa/for-professionals/privacy/special-topics/de-identification/index.html) | 데이터셋 비식별 근거(18식별자) | [security.md](security.md) |
| [PhysioNet Credentialed License·DUA](https://physionet.org/content/mimiciv/) | 데이터 접근·재식별 금지 협약 | [security.md](security.md) |

## 데이터셋·도메인

| 출처 | 용도 | 참조 문서 |
| --- | --- | --- |
| [MIMIC-IV](https://physionet.org/content/mimiciv/) | 원천 데이터셋(icu·hosp 모듈) | [dataset_schema.md](dataset_schema.md) |
| [eICU-CRD](https://physionet.org/content/eicu-crd/) | 원천 데이터셋 | [dataset_schema.md](dataset_schema.md) |
| [mimic-code concepts](https://github.com/MIT-LCP/mimic-code) | 실버 모델(SOFA·Sepsis-3) 원 로직 | [dataset_schema.md](dataset_schema.md) |
| [Sepsis-3 (JAMA 2016)](https://jamanetwork.com/journals/jama/fullarticle/2492881) | Sepsis-3 정의(SOFA≥2 + 감염 의심) | [dataset_schema.md](dataset_schema.md) |
