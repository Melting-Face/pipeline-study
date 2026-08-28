# Apache Spark (아키텍처 · 프로젝트 관점)

## 개요

Spark는 **범용 분산 데이터 처리 엔진**이다. driver가 DAG를 스케줄링하고 여러 executor가 파티션을
병렬 처리한다. RDD/DataFrame API, 배치·마이크로배치 스트리밍(Structured Streaming), SQL, ML(MLlib)을
아우르며, 셔플·인메모리 캐시로 대규모 변환에 강하다.

- 최신 안정: **Spark 4.2.0**(2026-07). Arrow 최적화 Python UDF 기본화, CDC(`CHANGES`), 지오공간·
  벡터/AI 함수, Real-Time Mode 등.

## 이 프로젝트에서의 위치 — 🚧 채택·이행중(PoC 게이트)

- **채택 방향**: 재설계로 **K8s의 Apache Spark Operator**([apache/spark-kubernetes-operator](https://github.com/apache/spark-kubernetes-operator),
  GA 1.0.0 2026-07-26) <!-- date-ok --> 를 컴퓨트로 도입한다(Kubeflow spark-operator에서 이전). 확장성 확보와 함께,
  오케스트레이터↔원격 컴퓨트 분리를 시연하는 **학습/포트폴리오** 목적이다. 전체 로드맵은 [../redesign.md](../redesign.md).
- **컴퓨트 분업(급소)**: Spark가 장식이 되지 않도록 역할을 분리한다.
  lineage는 **Spark(bronze·인제스트) → Iceberg → dbt-on-Trino(silver/gold)**.
  - **대용량 bronze 적재를 Spark로**: `mimiciv.chartevents`·`labevents`·`eicu.nurse_charting`의 적재를
    `SparkApplication`으로 옮겨 기존 `load_heavy_csv_gz_to_iceberg`(boto3 청크 append)를 **대체**한다.
    "대용량 CSV.gz 분산 읽기 → Iceberg write"는 Spark의 교과서적 유스케이스로, **커스텀 코드 은퇴 +
    Spark 존재이유 확보**를 동시에 달성한다([../redesign.md](../redesign.md) 급소①).
  - **silver/gold SQL 마트를 dbt-spark로**: Trino 제거에 따라 dbt 어댑터를 **`dbt-trino`→`dbt-spark`** 로 이관한다.
    22모델(SOFA→Sepsis-3)과 스키마테스트 자산은 보존하고, SQL 방언 차이만 교정한다([../redesign.md](../redesign.md) Phase 1).
  - **Iceberg 유지보수를 Spark로**: Trino `optimize` 대신
    `rewrite_data_files`·`remove_orphan_files`를 Spark 프로시저로 실행.
  - **(후속) ML/윈도우 피처** — SQL로 표현이 어려운 계층(PySpark).
  - **실시간 스트리밍은 Flink 담당** — 배치=Spark, 스트림=Flink로 역할 분리([flink.md](flink.md)).
- **실행 방식**: 네이티브 `spark-submit`(명령형) 대신 **선언형 `SparkApplication`(CRD, `spark.apache.org/v1`)** 을 쓴다.
  오퍼레이터가 spark-submit을 대행하고 재시도·상태를 표면화해 GitOps/감사에 유리하다.
  스펙은 `sparkConf` 중심이다([../conventions/k8s.md](../conventions/k8s.md) §9).
- **Trino 대비**: Spark=범용·상태 있는 처리·코드 API / Trino=SQL 연합 쿼리·무상태·낮은 오버헤드([trino.md](trino.md)).

## 운영 메모

- **트리거**: Dagster(호스트) 자산이 `PipesK8sClient`로 `SparkApplication`을 제출·폴링하고
  로그·materialization을 회수한다([../conventions/k8s.md](../conventions/k8s.md) §9~11).
- **Iceberg 접속**: `iceberg-spark-runtime`으로 **Flink와 동일 JDBC 카탈로그**를 공유한다(낙관적 동시성).
  스냅샷 메타데이터에 커밋 엔진이 남아 **같은 카탈로그에 두 서명이 공존**한다.

### S3 경로가 둘이고 역할이 다르다

| 경로 | 역할 |
| --- | --- |
| **Iceberg `S3FileIO`**(AWS SDK v2) | **테이블 데이터 I/O** 전담 — 현재 적재 경로 |
| **S3A**(`hadoop-aws`) | `s3a://`로 **원본 파일**(csv.gz)을 읽을 때 |

- 둘 다 SeaweedFS라 **path-style을 강제**한다(`s3.path-style-access` / `fs.s3a.path.style.access`).
- ⚠️ **S3A 직접 쓰기(`df.write.parquet("s3a://…")`)는 실패한다** — 기본 committer가 rename에 의존한다.
  쓰기는 전부 Iceberg(S3FileIO)로 보낸다.
- 🔴 **"경로가 둘"이라는 사실 자체가 오진의 씨앗이다** — 한 경로만 보고 "무관하다"고 닫으면
  다른 경로의 결함을 놓친다.

### Spark Connect — 상주 SQL 엔드포인트

`SparkApplication`은 잡이 끝나면 사라져 dbt가 붙을 수 없다. 그래서 **Spark Connect 서버**를
Deployment로 상주시키고 dbt-spark가 `spark.remote`로 접속한다.
Thrift(HiveServer2) 대비 클라이언트가 가볍고 어댑터 변경이 없다.

**상주 자원을 쓰므로 안 쓸 때는 `--replicas=0`으로 내린다**(회수 규율).
**"시분할"은 폐지된 규약이다** — 동시 기동이 허용됐고 남은 것은 **회수 의무**다.
규약이 바뀐 이유가 "샜던 게 괜찮아져서"가 아니라 **"예산이 늘어서"** 이므로 회수는 그대로 산다.

**접속 경로는 TLS Ingress**이고 `port-forward`는 **폴백으로 남긴다**(CA 미배포 환경·컨트롤러 장애).
⚠️ **데이터 경로를 Ingress에 묶으면 컨트롤러 가용성에 종속된다** — port-forward에는 없던 결합이다.

### Spark Connect는 `--master local[2]`로 돈다

**클러스터가 아니라 파드 한 개 안의 로컬 모드다.** `SparkApplication`과 달리 executor를 따로 띄우지 않는다.

| 항목 | 값 | 결과 |
| --- | --- | --- |
| 병렬도 | **≈ 1** | `local[2]`라고 적혀 있어도 CPU 한도가 2 스레드를 못 준다 |
| driver 힙 | 1g(기본값) | 이 파드가 곧 driver이자 executor다 |
| `shuffle.partitions` | 200(기본값) | 데이터 대비 과다 파티션 |

🔴 **급소는 "느린 것"을 "도는 것"으로 오독하기 쉽다는 점이다.** 셔플이 커지면 파드가 OOMKilled →
재시작되는데, **dbt는 gRPC 재시도 때문에 에러가 아니라 무한 대기처럼 보인다.**

`k8s://` 전환에 남은 것은 둘이다 — ⓐ **driver 도달 주소**(headless Service + `spark.driver.host`)
ⓑ **executor 크리덴셜 전파**(현재 driver env로만 주입된다). RBAC·SA·러너 이미지는 이미 준비돼 있다.

📌 **발현하지 않은 성능 문제를 미리 고치지 않는다** — 고치면 전환이 옳았는지 판정할 관측 근거가 없다.
다음에 느릴 때 여기를 먼저 본다.

### `createOrReplace`는 계보를 끊는다

`df.writeTo(TABLE).createOrReplace()`는 이름과 달리 **Iceberg 스냅샷 이력을 지우지 않는다.**
그런데 **`parent_id`가 전부 `NULL`** 이라 선형 체인이 아니라 **독립 루트가 쌓인다.**

- **결과 데이터는 멱등하다** — 몇 번을 돌려도 테이블 내용은 같다.
- 🔴 **그러나 계보는 매번 끊긴다.** 실행마다 새 루트가 서고 직전까지의 스냅샷이 통째로
  `is_current_ancestor = false`가 된다.
- ⇒ **두 축을 "멱등"이라는 한 말로 덮으면 「증분·changelog 읽기가 구조적으로 불가능해진 것」을 못 본다.**
  "멱등하다"는 문장은 이 손실에 대해 아무 말도 하지 않는데 읽는 사람은 안심하고 지나간다.
- ⇒ **이 쓰기 모드로 만든 테이블은 changelog·스트리밍 소스가 될 수 없다.**
  대용량 인제스트를 Spark로 옮길 때 그대로 쓰면 같은 제약이 따라온다.

**창은 `.history`의 `parent_id`로 계보를 걸어 잡는다** — `.snapshots`를 `committed_at`으로 정렬한
인접 두 행이 부모-자식이라는 보장이 없고, 아니면 `is not a parent ancestor`로 죽는다.

📌 **쓰기 모드의 문제이지 카탈로그·스토리지의 문제가 아니다** — 같은 카탈로그의 다른 테이블은
`parent_id` 연쇄가 정상이다.

### `create_changelog_view` 의미론

| 창 / 옵션 | 결과 |
| --- | --- |
| append 창 | `{INSERT: n}` |
| 갱신 창, `identifier_columns` **없음** | `{DELETE, INSERT}` |
| 갱신 창, `identifier_columns => array('id')` | `{UPDATE_BEFORE, UPDATE_AFTER}` |

🔴 **`UPDATE`는 `append`가 아니라 `overwrite` 스냅샷을 남긴다** —
Flink 스트리밍 읽기 제약이 실제로 물리는 지점이다([flink.md](flink.md) §급소).

게이트 규약·종료 코드는 [../test.md](../test.md) §5-3.

### SeaweedFS는 aws-chunked 체크섬을 못 푼다

최신 AWS SDK는 본문을 `aws-chunked`로 감싸는데 SeaweedFS가 그것을 벗기지 못해
**객체가 손상된다.** 업로드는 성공한 것처럼 보인다.

⇒ **`AWS_REQUEST_CHECKSUM_CALCULATION=when_required` ·
`AWS_RESPONSE_CHECKSUM_VALIDATION=when_required`를 유지한다.**

**"고쳤다"로 닫히지 않는 결함이다** — SDK를 올리면 기본값이 되살아나 **재발한다.**
SDK v2를 쓰는 경로가 새로 열릴 때마다 같은 축을 다시 본다.
확인 방법은 **일부러 위반시키는 것**이다 — env를 뺀 채 한 번 써서 손상이 재현되는지 보고 되돌린다.
그래야 "안 났다"가 **관측 경로 생존**과 함께 유효해진다.

### dbt-spark on Spark Connect — 계약이 아니라 구현에 얹혀 있다

dbt-spark가 공식 지원하는 method는 **thrift / http / odbc / session 넷뿐**이고 connect는 없다.
그런데도 도는 것은 pyspark 빌더가 `spark.remote`를 보고 위임하는 **내부 동작** 때문이다.

⇒ **minor 업그레이드가 에러 없이 깨뜨릴 수 있다.** 그래서 상한을 minor로 묶고
**올리기 직전에 `scripts/spark_connect_smoke.py`를 통과**시킨다.

**"미지원"과 "동작 안 함"은 다른 축이다.** 필요한 것은 Thrift 배포가 아니라 **업그레이드 회귀 감시**다.

### 버전 선택 규칙

🔴 **최신이 아니라 Iceberg가 지원하는 짝을 고른다.** Iceberg의 multi-engine 매트릭스와
런타임 아티팩트 실재를 **1차 출처로** 확인하고 정한다.

읽을 때 지킬 것:

- **`Deprecated`와 `drop`은 결정이 갈리는 차이다** — 전자는 쓸 수 있고 후자는 없어졌다.
  **C·D 등급 요약만으로 단정하지 않는다.**
- **"라이브러리가 지원한다"와 "기본으로 쓴다"는 다른 축이다** — 지원 여부만 보고 기본값을 추정하면
  존재하지 않는 위험에 대비하느라 상향을 미루게 된다.
- ⚠️ **컨테이너 태그는 "있다"가 아니라 *이름*으로 판정한다** — 같은 버전 계열에 `preview` 접두가
  섞여 있어 개수만 보고 고르면 프리뷰를 굽는다.
- **요약을 관측으로 읽지 않는다.** 존재/부재처럼 이분법으로 떨어지는 사실일수록
  **원문에서 직접 판정**한다 — 요약 계층은 같은 입력에도 답이 흔들리고 흔들린 티가 나지 않는다.

📌 **"돌고 있다"를 지원 근거로 읽지 않는다.** 오퍼레이터가 버전을 관대하게 다루면
지원 목록에 없어도 돈다. 이 함정은 dbt-spark 축과 오퍼레이터 축에서 **두 번** 났다.

**변인은 하나씩 올린다** — 엔진 상향과 새 기능 도입을 붙이면 문제가 생겼을 때 원인을 못 가른다.

📌 **현재 결정 상태와 남은 확인 항목은 저장소 밖에 있다** —
`$OBSIDIAN_VAULT/status/backlog.md`.

## 심화: Iceberg 파일 컴팩션 (Spark vs Trino) — 이 프로젝트 관점

### 문제: small-files (파일 폭증)

이 프로젝트의 대용량 테이블(`mimiciv.chartevents`·`labevents`·`eicu.nurse_charting`)은
`load_heavy_csv_gz_to_iceberg`가 **청크(기본 100만 행) 단위로 `append`** 하며 적재한다
([overview.md](overview.md) 대용량 경로). append마다 데이터 파일이 생겨 **작은 파일이 다수**
쌓이고, 이는 메타데이터 팽창·파일 오픈 비용 증가로 쿼리를 느리게 한다. **컴팩션**(작은 파일을
큰 파일로 bin-packing)이 필요하다.

### 두 가지 컴팩션 수단

| 수단 | 호출 | 특징 | 이 프로젝트 적합성 |
| --- | --- | --- | --- |
| **Trino `optimize`** | `ALTER TABLE iceberg.<ns>.<t> EXECUTE optimize(file_size_threshold => '100MB')` | threshold 미만 파일을 파티션별 병합. 별도 인프라 불필요 | ✅ 현행 스택(추가 인프라 0). 단, 쿼리용 Trino와 자원 경합 |
| **Spark `rewrite_data_files`** | `CALL catalog.system.rewrite_data_files(...)` (binpack/sort, 목표 512MB~1GB) | Spark 잡으로 병렬 rewrite, 유지보수 전용 분리 가능 | 🔎 대규모·상시 컴팩션에서 쿼리 경합을 피하려는 경우 |

### 프로젝트 결정

- **지금**: **Spark `rewrite_data_files`** 로 처리한다(Trino에서 이관 — Trino 제거의 선행조건①).
  `remove_orphan_files`도 함께 Spark로 옮겨 **유지보수 엔진을 하나로** 모았다.
  유지보수 잡의 **1·3단계 op로 구현**했다
  ([maintenance.py](../../dagster/dockerfile.d/src/src/dagster_project/defs/maintenance.py)).
  접속은 **공식 통합 `dagster-pyspark`의 `LazyPySparkResource`** 를 쓴다(커스텀 리소스를 만들지 않는다 —
  [conventions/dagster.md](../conventions/dagster.md)의 "불필요한 서브클래싱 지양"). Spark Connect로 붙이는
  방법은 **`spark_config={"spark.remote": ...}`** 한 줄이다 — 내부 `builder.config(k, v)`가 이 키를 받아
  `pyspark.sql.connect` 세션을 만든다(실측). 카탈로그 설정은 **서버 측**에 있어
  Dagster는 주소만 갖는다(비밀정보 비노출).
  - **`Lazy~`를 쓰는 이유**: 세션을 `spark_session` **접근 시점**에 만든다. 비-Lazy(`PySparkResource`)는
    리소스 초기화에서 즉시 연결해, 유지보수와 무관한 run까지 Spark Connect 가용성(=port-forward)에 묶인다.
  - **`dagster-spark`는 직접 쓰지 않는다** — `spark-submit` 래퍼(`create_spark_op`)라 용도가 다르다.
    `dagster-pyspark`가 설정 스키마를 가져다 쓰므로 전이 의존으로만 설치된다.
  - **제약**: Spark Connect 세션은 `sparkContext`를 지원하지 않는다(`NOT_IMPLEMENTED`).
    RDD·`sc.parallelize`가 필요한 코드는 Connect로 못 옮긴다 — 유지보수는 SQL만 써서 무관하다.
- **Trino와 다른 지점(값에 영향)**: Spark bin-pack은 `min-input-files`(기본 5) 미만이면 그룹을
  **통째로 건너뛴다**. Trino `optimize`에는 이 문턱이 없다 → 파일이 몇 개뿐인 테이블에서
  "0건 재작성"이 나오는 건 정상이며, 같은 임계값을 줘도 **두 엔진의 결과가 같지 않다**.
- **`remove_orphan_files`는 Hadoop FileSystem을 쓴다** — Iceberg의 `S3FileIO`(`io-impl`)는
  카탈로그가 아는 파일만 다루는데, 이 프로시저는 카탈로그가 **모르는** 파일을 찾는 게 목적이라
  warehouse 디렉터리를 직접 나열해야 한다. Spark Connect 서버에 `spark.hadoop.fs.s3*`(S3A) 설정이
  없으면 `UnsupportedFileSystemException: No FileSystem for scheme "s3"`로 죽는다(실측).
  jar(`hadoop-aws`·`aws-java-sdk-bundle`)는 러너 이미지에 이미 있고 **설정만** 필요했다.
  - ⚠️ **그 배선이 실제로 통했는지는 `미검증`이다**(재판정). 실행에서
    `No FileSystem for scheme "s3"`가 **0건**이었지만, 프로시저가 **테이블 해석 단계에서 먼저 죽어
    Hadoop FS 나열에 도달조차 못 했다.** **에러가 안 났다는 것을 "배선이 통과했다"로 읽으면 안 된다** —
    그 코드 경로가 실행되지 않았을 뿐이다([philosophy.md](../philosophy.md) 원칙 7: 부정 결과는
    **관측 경로가 살아 있었음을 함께 확인**해야 유효하다). 상세는 [../test.md](../test.md) §커버리지 공백.
- **안전 순서**: **compact(`rewrite_data_files`) → expire snapshots → remove orphan files**(현행 잡이
  op 의존성으로 강제). 컴팩션이 새 파일·스냅샷을 만든 뒤 만료가 옛 작은 파일 참조를 풀고,
  orphan 정리가 잔여를 제거한다.

## 참고

- Spark 문서: https://spark.apache.org/docs/latest/
- Spark 4.2.0 릴리스: https://spark.apache.org/releases/spark-release-4-2-0.html
- Spark 4.1.0 릴리스(상향 목표): https://spark.apache.org/releases/spark-release-4-1-0.html
- Spark 4.0.0 릴리스(Scala 2.13 전용·JDK 17+ 근거): https://spark.apache.org/releases/spark-release-4-0-0.html
- Iceberg multi-engine 지원 매트릭스(Spark 4.1 상한 근거): https://iceberg.apache.org/multi-engine-support/
- Hadoop 3.4.0 릴리스 노트(S3A의 AWS SDK v2 전환): https://hadoop.apache.org/docs/r3.4.0/hadoop-project-dist/hadoop-common/release/3.4.0/RELEASENOTES.3.4.0.html
- Apache Spark Kubernetes Operator: https://apache.github.io/spark-kubernetes-operator/ · 릴리스: https://github.com/apache/spark-kubernetes-operator/releases
- Iceberg + Spark: https://iceberg.apache.org/docs/latest/spark-getting-started/
- Iceberg Spark 프로시저(`rewrite_data_files`): https://iceberg.apache.org/docs/latest/spark-procedures/
- Trino Iceberg `optimize`(컴팩션): https://trino.io/docs/current/connector/iceberg.html
