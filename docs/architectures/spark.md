# Apache Spark (아키텍처 · 프로젝트 관점)

## 개요

Spark는 **범용 분산 데이터 처리 엔진**이다. driver가 DAG를 스케줄링하고 여러 executor가 파티션을
병렬 처리한다. RDD/DataFrame API, 배치·마이크로배치 스트리밍(Structured Streaming), SQL, ML(MLlib)을
아우르며, 셔플·인메모리 캐시로 대규모 변환에 강하다.

- 최신 안정: **Spark 4.2.0**(2026-07). Arrow 최적화 Python UDF 기본화, CDC(`CHANGES`), 지오공간·
  벡터/AI 함수, Real-Time Mode 등.

## 이 프로젝트에서의 위치 — 🚧 채택·이행중(PoC 게이트)

- **채택 방향**: 재설계로 **K8s의 Apache Spark Operator**([apache/spark-kubernetes-operator](https://github.com/apache/spark-kubernetes-operator),
  GA 1.0.0 2026-07-26)를 컴퓨트로 도입한다(Kubeflow spark-operator에서 이전). 확장성 확보와 함께,
  오케스트레이터↔원격 컴퓨트 분리를 시연하는 **학습/포트폴리오** 목적이다. 전체 로드맵은 [../redesign.md](../redesign.md).
- **컴퓨트 분업(급소)**: Spark가 장식이 되지 않도록 역할을 분리한다.
  lineage는 **Spark(bronze·인제스트) → Iceberg → dbt-on-Trino(silver/gold)**.
  - **대용량 bronze 적재를 Spark로**: `mimiciv.chartevents`·`labevents`·`eicu.nurse_charting`의 적재를
    `SparkApplication`으로 옮겨 기존 `load_heavy_csv_gz_to_iceberg`(boto3 청크 append)를 **대체**한다.
    "대용량 CSV.gz 분산 읽기 → Iceberg write"는 Spark의 교과서적 유스케이스로, **커스텀 코드 은퇴 +
    Spark 존재이유 확보**를 동시에 달성한다([../redesign.md](../redesign.md) 급소①).
  - **silver/gold SQL 마트를 dbt-spark로**: Trino 제거에 따라 dbt 어댑터를 **`dbt-trino`→`dbt-spark`** 로 이관한다.
    22모델(SOFA→Sepsis-3)과 스키마테스트 자산은 보존하고, SQL 방언 차이만 교정한다([../redesign.md](../redesign.md) Phase 1).
  - **Iceberg 유지보수를 Spark로**: Trino `optimize` 대신 `rewrite_data_files`·`remove_orphan_files`를 Spark 프로시저로 실행.
  - **(후속) ML/윈도우 피처** — SQL로 표현이 어려운 계층(PySpark).
  - **실시간 스트리밍은 Flink 담당** — 배치=Spark, 스트림=Flink로 역할 분리([flink.md](flink.md)).
- **실행 방식**: 네이티브 `spark-submit`(명령형) 대신 **선언형 `SparkApplication`(CRD, `spark.apache.org/v1`)** 을 쓴다.
  오퍼레이터가 spark-submit을 대행하고 재시도·상태를 표면화해 GitOps/감사에 유리하다. 스펙은 `sparkConf` 중심이다([../conventions/k8s.md](../conventions/k8s.md) §9).
- **Trino 대비**: Spark=범용·상태 있는 처리·코드 API / Trino=SQL 연합 쿼리·무상태·낮은 오버헤드([trino.md](trino.md)).

## 운영 메모 (이행)

- **트리거**: Dagster(호스트) 자산이 `PipesK8sClient`로 `SparkApplication`을 제출·폴링하고 로그·materialization을 회수한다([../conventions/k8s.md](../conventions/k8s.md) §9~11).
- **Iceberg 접속**: `iceberg-spark-runtime`으로 Trino·**Flink와 동일 JDBC 카탈로그** 공유(낙관적 동시성).
  메타 테이블(`iceberg_tables`·`iceberg_namespace_properties`) 스키마 정합 유지.
  2026-08-22 **Spark ↔ Flink 배치 왕복**(Spark 적재 → Flink 읽기 → Flink 쓰기 → Spark 되읽기)까지
  실증했다([flink.md](flink.md) §Iceberg 배치 왕복 실증). 스냅샷 메타데이터에 커밋 엔진이 남아
  **같은 카탈로그에 `spark`·`flink` 두 서명이 공존**한다.
- **S3 경로가 둘이고 역할이 다르다**(혼동 주의):
  - **Iceberg `S3FileIO`**(AWS SDK v2, `iceberg-aws-bundle`) — **테이블 데이터 I/O** 전담. 현재 적재 경로가 이것.
  - **S3A**(`hadoop-aws` + `aws-java-sdk-bundle`) — `s3a://`로 **원본 파일**(csv.gz)을 읽을 때 필요(Phase 2).
    2026-08-18까지 러너 이미지에 **없었다**(Iceberg만 쓰는 잡은 돌아서 부재를 눈치채기 어려움).
  - 둘 다 SeaweedFS라 **path-style 강제**(`s3.path-style-access` / `fs.s3a.path.style.access`).
  - ⚠️ **S3A 직접 쓰기(`df.write.parquet("s3a://…")`)는 실패한다** — 기본 committer가 rename에 의존.
    본 설계는 쓰기를 전부 Iceberg(S3FileIO)로 보내므로 영향 없음([../conventions/k8s.md](../conventions/k8s.md) §9).
  - 🔴 **"경로가 둘"이라는 사실 자체가 오진의 씨앗이 됐다** — 아래 §체크섬 결함 참고.
    한 경로만 보고 "무관하다"고 닫은 주석이 실제 결함의 원인이었다.
- **상시 SQL 엔드포인트 — Spark Connect**(Phase 1): `SparkApplication`은 잡이 끝나면 사라져 dbt가 붙을 수 없다.
  그래서 **Spark Connect 서버**를 Deployment로 상주시키고 dbt-spark가 `spark.remote`로 접속한다
  (`k8s/spark/spark-connect-server.yaml`). Thrift(HiveServer2) 대비 클라이언트가 가볍고 어댑터 변경이 없다.
  **상주 자원을 쓰므로** 쓰지 않을 때는 `kubectl scale deploy/spark-connect --replicas=0`
  (**회수 규율** [../conventions/k8s.md](../conventions/k8s.md) §9-3, 배분 수치는
  [../resource-sizing.md](../resource-sizing.md)). 🔴 **"시분할"은 폐지된 규약이다** —
  2026-08-22 예산 상향(8 CPU/22.35 GiB)과 동시 피크 실측으로 **동시 기동이 허용**됐고,
  남은 것은 시분할이 아니라 **안 쓰면 내린다는 회수 의무**다(규약이 바뀐 이유는 "샜던 게
  괜찮아져서"가 아니라 "예산이 늘어서"이므로 회수는 그대로 산다).
- **접속 경로는 TLS Ingress**(2026-08-22 개정): `sc://spark-grpc.localtest.me:8443/;use_ssl=true`.
  종전 port-forward 전제를 뒤집은 근거는 **CA 신뢰 축이 실측으로 닫혔기 때문**이며, 규칙 정본은
  [../conventions/k8s.md](../conventions/k8s.md) §10 §gRPC다. 실측 체인: `openssl` 검증 0 →
  `curl` `http_ver=2`·`code=415` → pyspark 질의 왕복 → **`scripts/spark_connect_smoke.py` 전 항목 통과**(dbt 포함).
  🔴 **port-forward를 지우지는 않는다** — 폴백으로 남긴다(CA 미배포 환경·Ingress 컨트롤러 장애 시).
  실제로 이 검증 중 ingress-nginx가 **liveness 재시작 루프**(`/healthz` 타임아웃, 재시작 8회)에 빠져
  Ingress 생성이 admission webhook 거부로 실패했다 — **자체 회복했지만, 데이터 경로를 Ingress에
  묶으면 컨트롤러 가용성에 종속된다**는 뜻이다(port-forward에는 없던 결합).

### 🔴 Spark Connect는 `--master local[2]`로 돈다 — 실상과 한계 (2026-08-22)

**클러스터가 아니라 파드 한 개 안의 로컬 모드다.** `SparkApplication`(오퍼레이터 경로)과 달리
상주 Spark Connect 서버는 executor를 따로 띄우지 않는다.

🔴 **이것은 의도된 결정이 아니라 *기록되지 않은 기본값*일 가능성이 높다.**
`docs/**` 전체를 `local[`·`k8s://`·`--master`로 grep한 결과 **매니페스트 2건뿐, 문서 0건**이었다.
설계 문서 어디에도 "왜 local인가"가 없다 — 즉 **논의된 적이 없다.**

#### 한계 (성능이 아니라 오독의 위험이다)

| 항목 | 현재 값 | 결과 |
| --- | --- | --- |
| 병렬도 | **≈ 1** (`limits.cpu: 1`) | `local[2]`라고 적혀 있어도 CPU 한도가 2 스레드를 못 준다 |
| driver 힙 | **1g**(`--driver-memory` 미지정 = 기본값) | 이 파드가 곧 driver이자 executor다 |
| `spark.sql.shuffle.partitions` | **200**(기본값) | 데이터 대비 과다 파티션 |

🔴 **급소는 "느린 것"을 "도는 것"으로 오독하기 쉽다는 점이다.** 셔플이 커지면 파드가
OOMKilled → 재시작되는데, **dbt는 gRPC 재시도 때문에 에러가 아니라 무한 대기처럼 보인다**
(2026-08-19의 "600초 초과·무출력 종료" 관측과 같은 계열).

#### `k8s://` 전환에 필요한 것 — 2가지

RBAC·ServiceAccount·러너 이미지는 **이미 준비돼 있다.** 남은 것은 둘이다.

| # | 필요한 것 | 현재 상태 |
| --- | --- | --- |
| ⓐ | **driver 도달 주소** — headless Service + `spark.driver.host` | ClusterIP에 `15002`·`4040`만 노출. executor가 driver를 되찾아올 경로가 없다 |
| ⓑ | **executor 크리덴셜 전파** | 현재 **driver env로만** 주입 — executor 파드에는 안 간다 |

📌 **이번 범위에서는 전환하지 않고 문서화만 한다.** 22모델을 아직 `dbt build`로 못 돌리므로
(위 §계측 단위 교정) **성능 문제가 아직 발현하지 않았다.** 발현하지 않은 문제를 미리 고치면
전환이 옳았는지 판정할 관측 근거가 없다 — 다만 **다음에 느릴 때 여기를 먼저 보라**는 기록은 남긴다.

### `createOrReplace`는 이력을 지우지 않는다 (2026-08-22 실측)

`k8s/spark/poc_ingest.py:54`가 `df.writeTo(TABLE).createOrReplace()`를 쓴다. 이름만 보면
테이블을 갈아엎을 것 같지만 **Iceberg 스냅샷 이력은 유지된다.**

- 실측: 재실행에 따라 스냅샷이 **`4 → 5 → 6`으로 누적**됐고, Iceberg **`1.6.1` 시절 스냅샷 4건이
  전부 보존**돼 있었다.
- 🔴 **다만 `parent_id`가 전부 `None`이다.** 선형 체인이 아니라 **독립 루트가 쌓이는** 형태이며,
  이것이 replace 계열 연산의 특징이다.
- ⇒ **"이력이 있다"와 "이력이 이어진다"는 다르다.** 스냅샷 수만 세면 시간 여행이 되는 것처럼
  보이지만, 부모 링크가 없으므로 **스냅샷 간 증분(diff) 의미론에 기대는 조회는 성립하지 않는다.**
- 부수 효과로 스냅샷이 단조 증가하므로 **expire snapshots 유지보수가 실제로 필요하다**(아래 §안전 순서).

### 🔴 `createOrReplace`는 계보를 끊는다 (2026-08-23 실측)

> 위 §`createOrReplace`는 이력을 지우지 않는다(2026-08-22)의 **후속 실측**이다. 그때 `parent_id`가
> 전부 `None`이라는 것까지는 봤고, 이번에 **그 결과가 무엇을 불가능하게 만드는지**를 실행으로 확인했다.
> 관측 수단은 `scripts/iceberg_changelog_probe.py`의 진단 파트이며 엔진은 **Spark 3.5.9 / Iceberg 1.11.0**이다.

`poc.sample`의 `.history` 실측 — **스냅샷 8개가 전부 `parent_id = NULL`**, 그중
**`is_current_ancestor = true`는 마지막 1개뿐**이고 나머지 **7개는 고아**다.
원인은 `k8s/spark/poc_ingest.py:54`의 `df.writeTo(TABLE).createOrReplace()`이고,
같은 파일의 주석은 이를 ***"createOrReplace로 멱등"*** 이라 적는다.

#### 📌 "멱등"은 데이터 축이다

- **결과 데이터는 실제로 멱등하다** — 몇 번을 돌려도 테이블 내용은 같다. 주석은 그 뜻으로 참이다.
- **그러나 계보는 매번 끊긴다.** 실행마다 `parent_id = NULL`인 **새 루트**가 서고 직전까지의
  스냅샷이 통째로 `is_current_ancestor = false`가 된다.
- ⇒ 🔴 **두 축을 같은 말로 덮으면 「증분·changelog 읽기가 구조적으로 불가능해진 것」을 못 본다.**
  "멱등하다"는 문장은 이 손실에 대해 아무 말도 하지 않는데, 읽는 사람은 안심하고 지나간다.
  ([philosophy.md](../philosophy.md) §계측 단위 — 값이 아니라 *무엇에 대한 값인지*가 어긋난 경우다.)

#### 실증 — 시간순 인접이 부모-자식을 뜻하지 않는다

`.snapshots`를 `committed_at`으로 정렬해 **인접 두 행(6→7)** 에 창을 잡자 이렇게 죽었다.

```text
IllegalArgumentException: Starting snapshot (exclusive) ... is not a parent ancestor of end snapshot
```

- ⇒ 🔴 **창은 `.history`의 `parent_id`로 계보를 걸어 잡아야 한다.** `committed_at` 정렬은
  changelog 창의 근거가 되지 못한다.
- **대비군**: 같은 카탈로그의 `poc.sample_flink`는 `parent_id` 연쇄가 정상이다
  (스냅샷 4개 전부 current ancestor). 즉 카탈로그·스토리지의 문제가 아니라 **쓰기 모드의 문제**다.

#### 🔴 Phase 2 함의 — 미결

대용량 bronze 인제스트를 Spark로 옮길 때([../redesign.md](../redesign.md) Phase 2)
**이 쓰기 모드를 그대로 쓰면 그 테이블은 changelog·스트리밍 소스가 될 수 없다.**

- 스트림 소스 후보를 고르는 축은 [flink.md](flink.md) §급소(append 전용)와 같은 축이다.
- ⚠️ **`poc_ingest.py`의 쓰기 모드 수정 여부는 `미결`이다** — 코드 변경이라 별도 결정이 필요하고
  이번 실측 범위에서는 **관측만** 했다.

### Iceberg `create_changelog_view` 의미론 — 실측 (2026-08-23)

> 🔴 **이 절은 실측이다**(아래 §Spark 3.5.9 → 4.1 상향 결정은 여전히 *결정* 단계다 — 섞어 읽지 않는다).
> 엔진은 **Spark 3.5.9 / Iceberg 1.11.0**, 수단은 `scripts/iceberg_changelog_probe.py`,
> 게이트 규약·종료 코드는 [../test.md](../test.md) **§5-3**이다.

프로브 테이블에 **append 3행 → append 2행 → update 1행**을 만든 뒤 창과 옵션을 바꿔 가며 읽었다.

| 창 / 옵션 | 변경분 |
| --- | --- |
| append 창 `s1→s2` | `{INSERT: 2}` |
| 갱신 창 `s2→s3`, `identifier_columns` **없음** | `{DELETE: 1, INSERT: 1}` |
| 갱신 창 `s2→s3`, `identifier_columns => array('id')` | `{UPDATE_BEFORE: 1, UPDATE_AFTER: 1}` |

- 🔴 **`UPDATE`는 `append`가 아니라 `overwrite` 스냅샷을 남긴다** — 프로브 계보의 ops가
  `append, append, overwrite`였다. 같은 "한 행을 고쳤다"가 **스냅샷 연산 종류를 바꾼다.**
- 📌 **`identifier_columns`는 성능 옵션이 아니라 표현 옵션이다.** 주지 않으면 갱신이
  `DELETE`+`INSERT`로 풀리고, 주면 `UPDATE_BEFORE`/`UPDATE_AFTER`로 접힌다. 하류에서 이 두 표현을
  같게 다루면 **건수가 2배로 세어진다.**

#### 🔴 축 분리가 실증됐다 — "변경분을 읽는다"는 엔진마다 범위가 다르다

| 축 | 실측/근거 | 상태 |
| --- | --- | --- |
| **Spark `create_changelog_view`** | overwrite 스냅샷 테이블(`poc.sample`)에서도 **뷰 생성 성공** | ✅ 2026-08-23 실측 |
| **Flink 스트리밍 읽기** | `IncrementalAppendScan` 기반이라 **append만** 본다 | 📄 문서화된 제약 · **이번에 Flink 잡으로는 `미검증`** |

⇒ **같은 "변경분"이라는 말 아래 두 엔진의 허용 범위가 다르다.** Spark 축의 통과를 Flink 축의
보증으로 읽지 않는다(그 축은 Flink 잡으로만 닫힌다 — [flink.md](flink.md)).

#### 카탈로그 현황과 소스 적격성 진단 (읽기 전용 · 2026-08-23)

Spark Connect와 카탈로그 Postgres 양쪽에서 확인했다. 네임스페이스는 `poc`·`chk`·`eicu`·`mimiciv`이고
🔴 **`eicu`·`mimiciv`는 테이블 0개** — "원천 미확보"가 카탈로그 수준에서 재확인됐다.

| 테이블 | 행수 | 계보 | ops | `spark_changelog` | `flink_stream` |
| --- | --- | --- | --- | --- | --- |
| `poc.sample` | 3 | 1 (고아 7) | `overwrite` | 가능 | **불가** |
| `poc.sample_flink` | 12 | 4 | `append` | 가능 | **가능** |

⇒ 🔴 **현재 카탈로그에서 Flink 스트림 소스로 쓸 수 있는 테이블은 `poc.sample_flink` 하나뿐이다.**
이 표는 **관문이 아니라 관측**이며 종료 코드를 바꾸지 않는다([../test.md](../test.md) §5-3).

### 🔴 SeaweedFS 체크섬 결함 — aws-chunked 프레이밍이 안 벗겨진다 (2026-08-22)

이 미션 최대의 발견이다. **Iceberg 상향이 만든 결함이 아니라, 이미 잠재해 있던 결함을 상향이
드러내 준 것**이다.

#### 증상

Iceberg **1.11.0** 러너로 테이블을 만들면 `metadata.json`이 **aws-chunked 전송 프레이밍이
벗겨지지 않은 채** 저장된다.

- 파일 선두 8바이트가 `b'703;chun'` — JSON이 아니라 **청크 길이 헤더**다.
- 그래서 그 테이블은 읽을 수 없고, 🔴 **`DROP TABLE`조차 실패한다**(드롭도 메타데이터를 먼저 읽는다).

#### 원인 — SDK 기본값 변경

| Iceberg | 번들된 AWS SDK v2 |
| --- | --- |
| `iceberg-aws-bundle:1.6.1` | **2.26.20** |
| `iceberg-aws-bundle:1.11.0` | **2.44.4** |

이 구간에서 **flexible checksum의 기본 동작이 바뀌었다**. SeaweedFS는 그 프레이밍을 풀지 못하고
**본문을 그대로 객체에 기록**한다.

#### 🔴 진단이 어려웠던 이유 — 「잠재 × 트리거」의 결합

**"설정 누락" 단독 가설은 기각되는 구조였다.** 1.6.1로 쓴 `poc.sample`이 멀쩡했기 때문이다.
설정이 빠져 있는데 왜 옛 테이블은 정상인가 — 여기서 조사가 한 번 막혔다.

- **결합 가설로 보면 그 정상 테이블이 오히려 증거였다**:
  **설정 누락(잠재 조건) × SDK 기본값 변경(트리거)** 이 둘 다 있어야 발현한다.
  1.6.1은 트리거가 없어 잠재 조건만으로는 조용했다.
- 📌 **"반례가 하나 있으니 가설이 틀렸다"고 닫기 전에, 그 반례가 *조건 하나가 빠진 대조군*은
  아닌지 본다.** 단일 원인 가설로만 보면 정상 사례가 반증이지만, 결합 가설에서는 증거다.

#### 🔴 주석 한 줄이 오진의 원인이었다

매니페스트에 ***"S3A는 SDK v1이라 체크섬 이슈와 무관하다"*** 는 주석이 있었다.
**이 문장은 참이다. 그런데 결론이 틀렸다.**

- 이 파드에는 **S3 경로가 둘**이다(위 §S3 경로가 둘이고 역할이 다르다).
- `metadata.json`을 쓰는 것은 **S3A가 아니라 `S3FileIO` = SDK v2 = 영향받는 쪽**이다.
- ⇒ **주석이 두 경로 중 하나만 보고 사안을 닫았다.** 참인 명제로 잘못된 결론을 봉인한 사례라,
  주석을 읽는 사람이 재검토하지 않게 만든다는 점에서 틀린 주석보다 위험하다.
- 📌 **경로가 여럿인 곳에서 "무관하다"를 적을 때는 *어느 경로에 대해* 무관한지 적는다.**

#### 해법

`k8s/spark/**`·`k8s/flink/**` 매니페스트에 아래를 **driver·executor 양쪽** 모두 추가한다.

```yaml
- name: AWS_REQUEST_CHECKSUM_CALCULATION
  value: when_required
- name: AWS_RESPONSE_CHECKSUM_VALIDATION
  value: when_required
```

- ⚠️ **`s3.checksum-enabled`와 혼동하지 않는다.** 이름만 비슷하고 **방향이 반대**다 —
  그쪽은 S3FileIO가 체크섬을 *더 붙이게* 하는 Iceberg 옵션이며 기본값이 `false`다.
  끄려고 그 옵션을 만지면 아무 일도 일어나지 않는다.

#### 🔴 이 결함은 미션과 무관하게 이미 존재했다

Iceberg를 올리지 않았다면, **다음에 SDK가 올라가는 어느 시점에 아무 예고 없이** 같은 일이
났을 것이다. 즉 상향은 원인이 아니라 **관측 계기**였다.

- ⚠️ **잔여물**: 손상된 `smoke`/`smoke_seed` 테이블은 카탈로그에서 지웠으나 **객체 53개가 orphan으로
  남아 있다**. 🔴 **현행 유지보수 잡으로는 정리되지 않는다** — 이유는
  [../test.md](../test.md) §`iceberg_maintenance_job` 커버리지 공백 ⓑ.

### dbt-spark on Spark Connect — 엔드투엔드 PoC (2026-08-19 실측)

Thrift 서버를 띄울지 정하려고 **어댑터 전 수명주기를 실제로 돌렸다**. 결론은 **Thrift 불필요**다.

| 검증 항목 | 결과 | 소요 |
| --- | --- | --- |
| `dbt debug` | ✅ All checks passed | 3.8s |
| `dbt build` — **전용 픽스처 3모델** + 스키마 테스트 3 (threads=1) | ✅ PASS=6 ERROR=0 | 4.5s |
| 동일 build, **threads=4** | ✅ 로그상 **실제 병렬** 실행 | 4.2s |
| incremental 2회차 | ✅ `merge into … as DBT_INTERNAL_DEST` **실발행** | — |
| 산출물 실물 | ✅ `Provider = iceberg`, 행·값 일치 | — |
| `dbt docs generate`(카탈로그 메타데이터) | ✅ `catalog.json` 생성 | 5.9s |
| `dbt compile` 전체(mimic_iv 22모델) | ✅ exit=0 | 4.9s |

#### 🔴 계측 단위 교정 — "PASS=6"이 센 것은 22모델이 아니다 (2026-08-22)

`manifest.json` 실측 결과, 위 `dbt build`가 돌린 것은 **`models/_poc_spark_connect/`의 전용
픽스처 3모델**(`poc_seed_a`·`poc_seed_b`·`poc_incremental`)이었다. 표의 *"table 2 + incremental 1 +
스키마 테스트 3 = PASS 6"* 이 정확히 이 디렉터리의 내용이다. 그 디렉터리는 **`git log` 이력 0건에
현재는 삭제**된 상태다(스모크 전용으로 만들고 지웠다).

🔴 **따라서 `dbt build`로 돌아본 실 프로젝트 모델은 0개다.** 22모델에 대해 확인된 것은
**`dbt compile` exit 0** 하나뿐이다.

- **"스키마 테스트 3"이 22모델의 것일 가능성은 원천적으로 0**이다 —
  `models/mimic_iv/tables/schema.yml`은 **`description` 244건**을 담고 있지만
  **`data_tests` 항목은 0건**이다(파일 자체는 626줄). 없는 테스트가 통과할 수는 없다.
- ⚠️ **"3"과 "22"가 같은 표에서 서로 다른 것을 센다.** 3은 *픽스처 모델 수*, 22는 *mimic_iv 실 모델 수*,
  6은 *픽스처 노드 + 그 스키마 테스트의 합*이다. 단위를 떼면
  "22모델이 빌드까지 통과했다"로 검산을 통과하며 읽힌다([philosophy.md](../philosophy.md) §계측 단위).
- 이 교정의 파급은 [../test.md](../test.md) §5-1 **B9**에 있다 — 픽스처와 실 모델은
  `file_format` 설정이 달라 **다른 코드 경로**를 탄다.

🔴 **"미지원"과 "동작 안 함"은 다른 축이다.** dbt-spark 1.11.0이 공식 지원하는 method는
`dbt/adapters/spark/connections.py`의 `SparkConnectionMethod` 기준 **thrift/http/odbc/session 4개뿐**이고
connect는 없다. 그런데도 도는 이유는 `session.py`가 `builder.config(k, v)` → `getOrCreate()`를 타서,
pyspark classic 빌더가 `spark.remote`를 보고 RemoteSparkSession으로 위임하는 **내부 동작**에 얹히기
때문이다. 즉 **지원 여부는 업그레이드 리스크의 축이고, 동작 여부는 기능 확보의 축**이다.
두 축을 섞으면 "미지원이니 Thrift를 띄우자"는 잘못된 결론이 나온다 — 실제로 필요한 건
**Thrift가 아니라 업그레이드 회귀 감시**다.

- **그래서 의존성 상한을 minor로 좁혔다** — `dbt-spark[session]>=1.11,<1.12`,
  `pyspark[connect]>=3.5.9,<3.6`. 계약이 아니라 구현에 의존하므로 minor 업그레이드가
  **에러 없이** 깨뜨릴 수 있다.
- **상한을 올릴 때는 `scripts/spark_connect_smoke.py`를 먼저 통과시킨다**
  ([../test.md](../test.md) §5-1). 이 스크립트가 이 경로의 **유일한 관측 수단**이다.
- **남은 조용한 실패**: 커넥션마다
  `Cannot modify the value of a static config: spark.sql.catalogImplementation` 경고가 난다
  (`enableHiveSupport()` 유래, `pyspark/sql/connect/session.py`의 `_apply_options`가
  `conf.set()` 예외를 `warnings.warn()`으로 **강등**). 지금은 무해하지만(테이블이 전부
  Iceberg JDBC 카탈로그에 있고 Hive 카탈로그를 안 쓴다) **`spark_connect` 타깃에 conf를
  추가하면 조용히 무시된다**. 카탈로그 설정의 단일 출처는 **서버 측** 매니페스트다.
- **Thrift는 선언만 유지한다**(`k8s/spark/spark-thrift-server.yaml`, 미배포) — `trino`·`flink`와 같은
  **"중단과 삭제의 분리"**. 상주 JVM +500m/1.5Gi를 물고, `method: thrift`의 클라이언트 의존성
  `pyhive`·`thrift`·`thrift_sasl`이 **미설치**라 지금 타깃만 바꾸면 접속 시점에 죽는다
  (`connections.py`가 ImportError를 삼켜 `hive=None`으로 둔다). **대피로로만 둔다.**
- **부수 해소**: "`dbt compile --target spark_connect`가 600초 초과·무출력 종료"(이전 세션 관측)는
  **재현되지 않는다**(4.9s, exit 0). Spark Connect gRPC 클라이언트는 UNAVAILABLE에 백오프 재시도를
  하므로 **port-forward가 끊긴 상태에서는 에러가 아니라 무한 대기처럼 보인다** — 정황상 원인이다(미확정).
- **러너 이미지 버전**: `hadoop-aws`는 베이스 이미지의 `hadoop-client-*`와 **정확히 같은 버전**이어야
  한다. 이미지 태그는 **`0.4.0` → `0.5.0`**(Iceberg 상향분).
  🔴 **아래 표의 두 열은 축이 다르다** — 왼쪽은 **실측 고정값**, 오른쪽은 **2026-08-23에 결정만 된
  목표값**이며 **아직 굽지 않았다**(아래 §Spark 3.5.9 → 4.1 상향 결정).

  | 항목 | 현행(실측 고정) | 목표(결정 완료 · **이행 전**) |
  | --- | --- | --- |
  | Spark | **3.5.9** | **4.1.0** |
  | Scala | **2.12** | **2.13** (4.x는 2.13 전용 — `SPARK-45314`) |
  | JDK | `미확인`(베이스 이미지 기본값) | **17+** (4.x가 JDK 8/11 drop — `SPARK-45315`) |
  | Iceberg | **1.11.0** | 1.11.0 유지 — 런타임 좌표만 `iceberg-spark-runtime-4.1_2.13` |
  | Hadoop S3A | `org.apache.hadoop:hadoop-aws` **3.3.4** | `org.apache.hadoop:hadoop-aws` **3.4.2** |
  | AWS SDK | `com.amazonaws:aws-java-sdk-bundle` **1.12.262**(v1) | 🔴 `software.amazon.awssdk:bundle` **2.29.52**(v2) |

  🔴 **AWS SDK는 버전만 오르는 게 아니라 *좌표 자체가 바뀐다*.** Hadoop 3.4.0이 S3A를 SDK v2로
  옮기면서 **v1 `aws-java-sdk-bundle` JAR을 제거**했다(`HADOOP-18820`). 버전 문자열만 갈아끼우면
  **의존성이 통째로 빠진 채로도 빌드가 통과**할 수 있으니 groupId·artifactId부터 바꾼다.
- executor 메모리·셔플 파티션 튜닝이 성능 핵심.

### Iceberg 1.6.1 → 1.11.0 상향 근거 (2026-08-22 실측)

Flink 쪽 Iceberg가 1.11.0이라 Spark를 맞출지 결정해야 했다. **`pyspark<3.6` 핀을 건드리지 않고
Iceberg만 올릴 수 있다**는 것을 1차 소스로 확인하고 올렸다. 아래는 **다음에 같은 조사를 반복하지
않기 위한 기록**이다.

| 확인 항목 | 결과 | 출처 |
| --- | --- | --- |
| `iceberg-spark-runtime-3.5_2.12:1.11.0` 아티팩트 | ✅ 실재 | Maven Central |
| Iceberg 1.11.0의 **Spark 3.5** 지원 상태 | ✅ **`Maintained`** | Iceberg 공식 매트릭스 `multi-engine-support.md` |
| `iceberg-aws-bundle:1.11.0` 아티팩트 | ✅ 실재 | Maven Central |

⇒ **Spark 3.5 계열을 유지한 채 Iceberg만 상향**할 수 있었다. `pyspark` 상한 인상은 불필요했다.

- ⚠️ **C등급 검색 요약이 틀렸고 1차 소스로 뒤집혔다.** 2차 요약은 *"Iceberg 1.11.0이 Spark 3.4
  지원을 **drop**했다"* 고 했으나 실제 매트릭스는 **`Deprecated`** 이고 3.4 런타임 아티팩트도
  Maven에 실재한다. `Deprecated`(쓸 수 있으나 권장 안 함)와 `drop`(없어짐)은 **결정이 갈리는 차이**다 —
  C·D 등급만으로 단정하지 않는다.

#### 🔴 format-version 오해 해소 — 신규 테이블 기본은 아직 **V2**다

Iceberg 상향을 검토하며 *"Flink(1.11.0)가 만든 **V3** 테이블을 Spark(1.6.1)가 못 읽는다"* 는
우려가 있었다. **이 시나리오는 성립하지 않는다.**

- Iceberg **1.11.0의 `TableMetadata.java`가 `DEFAULT_TABLE_FORMAT_VERSION = 2`** 다.
- ⇒ **`format-version`을 명시적으로 3으로 지정하지 않는 한** 신규 테이블은 V3로 만들어지지 않는다.
- 📌 **"라이브러리가 V3를 지원한다"와 "V3를 기본으로 쓴다"는 다른 축이다.** 지원 여부만 보고
  기본값을 추정하면, 이번처럼 존재하지 않는 위험에 대비하느라 상향 자체를 미루게 된다.

### Spark 3.5.9 → 4.1 상향 결정 (2026-08-23) — 🟡 **결정 완료 · 이행 전**

> 🔴 **상태를 먼저 읽어라.** 이 절은 **조사와 결정의 기록이며 아직 아무것도 올리지 않았다.**
> 러너 이미지는 여전히 Spark **3.5.9**이고(위 §러너 이미지 버전), 아래 표의 "결과" 열은
> **실행 실측이 아니라 1차 출처로 실재를 확인한 좌표·문구**다. 조사 관측 시각은 **2026-08-23**이다.

**동기와 근거가 갈린다.** 출발점은 *"버전을 최신화하고 싶다"* 였다. 그런데 이 저장소 규약은
**"최신이 아니라 Iceberg가 지원하는 짝"** 이고(위 §Iceberg 1.6.1 → 1.11.0 상향 근거와 같은 규칙),
확인해 보니 그 상한이 **정확히 4.1**이었다. 지목한 버전은 맞았지만 **맞은 이유는 최신이라서가 아니다** —
최신은 **4.2**이고, 4.2는 Iceberg가 `-4.2` 런타임을 발행하지 않아 이 규약에서 탈락한다.

#### 확인 항목 (전부 A등급 1차 출처)

| 확인 항목 | 결과 | 출처 |
| --- | --- | --- |
| Spark 4.1.0 GA | **2025-12-16** (4.x 두 번째) | Spark 공식 릴리스 페이지 |
| Spark 4.2.0 GA | 2026-07-14 | Spark 공식 news |
| Spark 4.x 런타임 전제 | **Scala 2.13 전용**(`SPARK-45314` — 2.12 drop) · **JDK 17+**(`SPARK-45315` — JDK 8/11 drop) | Spark 4.0.0 릴리스 노트 |
| **Iceberg multi-engine 매트릭스** | Spark **3.5 = `Maintained`**(initial 1.4.0) · **4.0 = `Maintained`**(initial 1.10.0) · **4.1 = `Maintained`**(initial **1.11.0**). 🔴 **4.2 행 자체가 없다** | `apache/iceberg` `site/docs/multi-engine-support.md`(main, 최종 커밋 2026-07-02 `da8ff447`) |
| Iceberg 런타임 아티팩트 | `iceberg-spark-runtime-3.5_2.13` ✅ · `4.0_2.13` ✅ · **`4.1_2.13` ✅**(1건, `v=1.11.0`) · **`4.2_2.13` ❌ 0건** | Maven Central Solr API |
| **Spark Operator 1.0.0**(= 저장소가 핀한 chart **1.8.0**) | **"Support Apache Spark 4.0, 4.1, and 4.2 and drop Spark 3.5"** (2026-07-26) | apache/spark-kubernetes-operator Releases |
| 러너 베이스 이미지 태그 | `apache/spark:4.1.0-scala2.13-java17-python3-ubuntu` **실재**(java21 변형도 실재) | Docker Hub v2 API **원문** |
| Spark 번들 Hadoop | **4.1.0 = `hadoop-client-* 3.4.2`** (4.2.0은 3.5.0) | Spark 4.2.0 릴리스 노트 라이브러리 업그레이드 표 |
| Hadoop 3.4.2의 AWS SDK v2 | `<aws-java-sdk-v2.version>2.29.52</aws-java-sdk-v2.version>` | `apache/hadoop` `rel/release-3.4.2` `hadoop-project/pom.xml` |
| 좌표 실재 | `software.amazon.awssdk:bundle:2.29.52` ✅ · `org.apache.hadoop:hadoop-aws:3.4.2` ✅ | Maven Central |
| **Hadoop 3.4.0 S3A** | **AWS SDK v2로 전환**(`HADOOP-18073` — "The S3A connector now uses the V2 AWS SDK") · **v1 `aws-java-sdk-bundle` JAR 제거**(`HADOOP-18820`) | Hadoop 3.4.0 릴리스 노트 |
| dbt-spark 1.11.0 | CHANGELOG **"Add support for Spark v4.x"**(PR #1537) | `dbt-labs/dbt-adapters` CHANGELOG · PyPI |

⚠️ **Docker Hub 태그는 "있다"가 아니라 *이름*으로 판정한다** — `4.1.0` 계열 태그가 **70개**인데
그중 다수가 `preview1`~`preview4` 접두를 달고 있다. 개수만 보고 고르면 프리뷰를 굽게 된다.

#### 판정 — ★★★★☆ 조건부 채택 (changelog 도입 **다음**)

- **`pyspark<3.6` 핀만 해제**한다. 러너 Spark가 4.1이면 클라이언트도 따라가야 한다.
- 🔴 **`dbt-spark<1.12` 핀은 유지**한다. 이 경로는 어댑터 계약이 아니라 **pyspark 내부 위임 동작**에
  얹혀 있어(위 §"미지원"과 "동작 안 함"은 다른 축이다) minor 업그레이드가 **에러 없이** 깨뜨릴 수 있다.
  상한을 올릴 때의 관문은 그대로 `scripts/spark_connect_smoke.py`다([../test.md](../test.md) §5-1).
- **동반 작업**: 러너 이미지 재빌드(Scala 2.13 · JDK 17+ 베이스) · Iceberg 런타임 좌표를
  `…-4.1_2.13`으로 교체 · **S3A 좌표 v1→v2 교체**(위 표).
- **순서**: Iceberg changelog 도입(★★★★★)을 **먼저** 닫는다. 그쪽은 신규 상주 인프라가 0이고
  이 상향과 **독립**이라, 둘을 붙이면 문제가 생겼을 때 변인이 둘이 된다.
  - ✅ **이 순서는 지켜졌다** — **changelog PoC는 현행 3.5.9에서 먼저 통과했다**(2026-08-23,
    위 §`create_changelog_view` 의미론 · [../test.md](../test.md) §5-3). ⇒ 이후 4.1로 올려 같은
    프로브가 깨지면 **변인은 엔진 하나**로 좁혀진다. 🔴 반대로 상향과 함께 도입했다면
    `create_changelog_view` 실패가 프로시저 문제인지 엔진 문제인지 가를 수 없었다.

#### 🔴 발견 1 — 현행 조합은 이미 공식 지원 밖이다

이 상향의 진짜 근거는 **최신화가 아니라 지원 범위 복귀**다.

- 저장소가 핀한 Spark Operator chart **1.8.0**(appVersion **1.0.0**)의 릴리스 노트가
  **"drop Spark 3.5"** 를 명시한다. ⇒ **현행 Spark 3.5.9는 이 오퍼레이터의 지원 목록에 없다.**
- 지금 도는 이유는 오퍼레이터가 `sparkVersion`을 관대하게 다루기 때문이지 **지원돼서가 아니다.**
- 📌 이 문서가 dbt-spark Connect 경로에 대해 이미 적은 **"미지원과 동작 안 함은 다른 축이다"**(위 §)가
  **오퍼레이터 축에서 그대로 재현**됐다. 같은 함정이 두 축에서 났다는 것은
  **"돌고 있다"를 지원 근거로 읽는 습관이 이 스택 전반에 있다**는 뜻이다.

#### 🔴 발견 2 — 같은 질문에 조사 2회가 정반대로 답했다

dbt-spark의 Spark 4.x 지원 여부를 두 번 조사했고 결과가 **정면으로 갈렸다.**

| 회차 | 실제로 본 저장소 | 답 |
| --- | --- | --- |
| 1차 | **`dbt-labs/dbt-adapters`** (현행 · 이 저장소가 쓰는 것) | "Spark v4.x 지원 추가"(PR #1537) |
| 2차 | `dbt-labs/dbt-spark` (**이전 완료된 구 저장소**, 안내문만 남음) | **0건** |

- ⇒ `CLAUDE.md`가 이미 적어 둔 **"같은 서비스가 두 환경에 이중 존재하면 살아 있는 레거시가
  정본 대신 답한다"** 의 정확한 재현이다(모니터링 축에서 방향을 달리해 2회 발생한 것과 같은 계열).
  **부정 답변("0건")이 나온 쪽이 죽은 저장소였다** — 모집단을 확인하지 않으면 이 0은 사실처럼 읽힌다.
- **1차를 채택한다.** 🔴 다만 **이행 시 `dbt build` 실행으로 재확인**하고 **문서 대조로 닫지 않는다** —
  CHANGELOG는 *"무엇을 넣었다"* 의 기록이지 *"우리 경로에서 같은 값이 난다"* 가 아니다.

#### 🔴 발견 3 — 같은 URL에 두 번 물었더니 답이 상충했다

Docker Hub의 `4.1.0-scala2.13-java17-python3-ubuntu` 태그 존재 여부를 `WebFetch`로 두 번 물었을 때
**1차는 "존재", 2차는 "False"** 였다. `curl` + `json.load`로 **API 응답 원문을 직접 파싱**해
실재를 확정했다.

📌 **요약을 관측으로 읽지 않는다.** 존재/부재처럼 이분법으로 떨어지는 사실일수록 **원문에서 직접
판정**한다 — 요약 계층은 같은 입력에도 답이 흔들리고, 흔들린 티가 나지 않는다.

#### changelog는 4.1이 주지 않는다 (엔진 축 정리)

Spark **4.2.0**에는 **Spark 자체 DSv2 CDC API**(`SPARK-55948`, `CHANGES` 절)가 들어갔지만
**4.1에는 없고**, 애초에 Iceberg 전용도 아니다. ⇒ 이 저장소의 변경분 조회는
**Iceberg Spark 프로시저 `create_changelog_view`** 로 간다
(`options => map('start-snapshot-id', …, 'end-snapshot-id', …)`, `net_changes`·`identifier_columns` 인자).
스트림 소스 결정의 전문은 [flink.md](flink.md) §스트림 소스를 Redpanda에서 Iceberg changelog로 바꾼 근거.

#### 🔴 닫지 못한 것 2건 — 상향에 착수하기 전에 읽어라

1. **22모델 값 정합은 여전히 미검증이다.** 원천 대용량 3종 부재로 `dbt build` 실행 검증을 못 한다
   ([../redesign.md](../redesign.md) Phase 2). 엔진을 3.5 → 4.1로 올리면 **값이 갈릴 가능성이
   새로 생기는데 확인 수단이 없다.** 🔴 `sqlfluff`·`dbt compile` 통과를 값 정합으로 읽지 않는다 —
   그 게이트가 보증하는 것은 스타일·구문까지다([../conventions/dbt.md](../conventions/dbt.md) §templater).
2. **S3A 체크섬 축이 새로 열린다.** Hadoop **3.4.0부터 S3A가 SDK v2**를 쓰므로, 이 저장소가
   **이미 두 번 겪은** SeaweedFS aws-chunked 손상 경로(위 §SeaweedFS 체크섬 결함)가
   **이번엔 S3A 축에서 재현될 수 있다.** 완화 env(`AWS_REQUEST_CHECKSUM_CALCULATION` ·
   `AWS_RESPONSE_CHECKSUM_VALIDATION`)는 SDK v2 **전역 설정**이라 S3A까지 덮을 것으로 **보이나
   이것은 추론이다.** 🔴 확인 방법은 **일부러 위반시키는 것**이다 — env를 뺀 채 한 번 써서 손상이
   재현되는지 보고, 되돌려 복구되는지 본다. 그래야 "안 났다"가 **관측 경로 생존**과 함께 유효해진다
   ([philosophy.md](../philosophy.md) 원칙 7). ⇒ 현 시점 상태는 `미확인`이다.

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

- **지금**: **Spark `rewrite_data_files`** 로 처리한다(2026-08-19 Trino에서 이관 — Trino 제거의 선행조건①).
  `remove_orphan_files`도 함께 Spark로 옮겨 **유지보수 엔진을 하나로** 모았다.
  유지보수 잡의 **1·3단계 op로 구현**했다
  ([maintenance.py](../../dagster/dockerfile.d/src/src/dagster_project/defs/maintenance.py)).
  접속은 **공식 통합 `dagster-pyspark`의 `LazyPySparkResource`** 를 쓴다(커스텀 리소스를 만들지 않는다 —
  [conventions/dagster.md](../conventions/dagster.md)의 "불필요한 서브클래싱 지양"). Spark Connect로 붙이는
  방법은 **`spark_config={"spark.remote": ...}`** 한 줄이다 — 내부 `builder.config(k, v)`가 이 키를 받아
  `pyspark.sql.connect` 세션을 만든다(2026-08-19 실측). 카탈로그 설정은 **서버 측**에 있어
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
- 🔴 **`remove_orphan_files`는 Hadoop FileSystem을 쓴다** — Iceberg의 `S3FileIO`(`io-impl`)는
  카탈로그가 아는 파일만 다루는데, 이 프로시저는 카탈로그가 **모르는** 파일을 찾는 게 목적이라
  warehouse 디렉터리를 직접 나열해야 한다. Spark Connect 서버에 `spark.hadoop.fs.s3*`(S3A) 설정이
  없으면 `UnsupportedFileSystemException: No FileSystem for scheme "s3"`로 죽는다(2026-08-19 실측).
  jar(`hadoop-aws`·`aws-java-sdk-bundle`)는 러너 이미지에 이미 있고 **설정만** 필요했다.
  - ⚠️ **그 배선이 실제로 통했는지는 `미검증`이다**(2026-08-22 재판정). 실행에서
    `No FileSystem for scheme "s3"`가 **0건**이었지만, 프로시저가 **테이블 해석 단계에서 먼저 죽어
    Hadoop FS 나열에 도달조차 못 했다.** 🔴 **에러가 안 났다는 것을 "배선이 통과했다"로 읽으면 안 된다** —
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
