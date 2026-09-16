# aws-chunked 체크섬 — SeaweedFS 호환 규칙

> [k8s.md](../k8s.md) §11에서 분리(상위 문서가 doc_lint 500줄 상한에 닿았다).
> 상위 규칙은 [k8s.md](../k8s.md), 배경 서술은
> [../../architectures/spark.md](../../architectures/spark.md) §SeaweedFS에 있다.

## 1. 증상과 원인

- **SeaweedFS는 AWS SDK의 flexible checksum(aws-chunked)을 풀지 못한다.**
  SDK가 `PutObject`에 체크섬을 기본 적용하며 본문을 청크로 감싸는데, SeaweedFS가 그것을
  해제하지 않아 **프레이밍 바이트가 객체 내용에 그대로 저장**된다.
  실측: Iceberg `metadata.json`이 `11\r\n{...}\r\n0\r\nx-amz-checksum-...`로 저장되고
  다음 읽기에서 pyiceberg가 JSON 파싱에 실패했다.
- **오류가 쓰기가 아니라 이후 읽기에서 나므로 추적이 어렵다.** 쓰기는 성공 응답을 받고 끝나며,
  깨진 사실은 그 객체를 처음 읽는 **다른 프로세스·다른 세션**에서 드러난다.
- 끄는 수단은 셋이고 값은 모두 `when_required`다 — 환경변수
  `AWS_REQUEST_CHECKSUM_CALCULATION`(+`AWS_RESPONSE_CHECKSUM_VALIDATION`),
  설정 파일 키 `request_checksum_calculation`, JVM 시스템 프로퍼티
  `aws.requestChecksumCalculation`(Java/Kotlin 전용). **SDK 기본값은 `WHEN_SUPPORTED`** 다.

## 2. 🔴 영향 경로는 SDK **버전**이 가른다 — 언어가 아니다

| 경로 | SDK | 체크섬 env |
| --- | --- | --- |
| S3A (`spark.hadoop.fs.s3*`) | AWS SDK **v1** | 무관 |
| S3FileIO (Iceberg `io-impl` = `iceberg-aws-bundle`) | AWS SDK **v2** | **필요** |
| pyiceberg·pyarrow·boto3 | 파이썬 SDK(boto3/botocore) — **별개 계보** | **필요**(경계 판본 미측정) |

- S3A가 무관한 근거는 **번들 실측**이다 — `flink-s3-fs-hadoop` jar에 `com/amazonaws/*`는 있고
  `software/amazon/awssdk/*`는 **0개**다(`k8s/flink/flinkdeployment-session.yaml` 주석).
  v1은 위 환경변수를 **모르는 변수라 무시**하므로 안전 근거는 "env로 덮었다"가 아니라
  **SDK 버전 하나뿐**이다.
- **버전 경계는 SDK v2 `2.30.0`** 이다. 그 판본부터 S3 클라이언트가 지원하는 연산에
  **기본으로 체크섬을 계산**하도록 바뀌었고(기본 알고리즘 CRC32), 그 아래는 `WHEN_REQUIRED`다.
  출처는 CHANGELOG와 개발자 가이드의 버전별 표이고 **둘의 값이 일치**한다(A등급 2건 교차 확인).
  URL은 [../../references.md](../../references.md)에 단일 관리한다 — 여기 복제하지 않는다.
- **파이썬 경로가 영향받는 근거는 이 저장소의 실측**이다(§1 — `metadata.json`이 프레이밍째 저장돼
  pyiceberg가 파싱에 실패했다). **자바의 `2.30.0`을 파이썬에 옮겨 적지 않는다** — 계보가 다르다.

| `iceberg-aws-bundle` | 번들 `awssdk-bom` | 경계(`2.30.0`) 대비 | 기본 동작 |
| --- | --- | --- | --- |
| `1.6.1` | `2.26.20` | **이전** | `WHEN_REQUIRED` — 증상 없음 |
| `1.11.0` | `2.44.4` | **이후** | `WHEN_SUPPORTED` — **손상** |

번들 SDK 값의 출처는 `apache-iceberg-<버전>` **릴리스 태그**의 `gradle/libs.versions.toml`(A등급).
현재 러너 2종(`spark-runner`·`flink-runner`)의 `ARG ICEBERG_VERSION`은 **`1.11.0`** 이므로
**Spark·Flink의 S3FileIO 경로도 영향을 받는다.**

## 3. 🔴 「Java 경로는 무관」은 무조건이 아니라 **버전 조건부**다

종전 §11은 *"Java SDK 경로(Spark·Flink의 iceberg-aws-bundle)는 영향받지 않는다 — 파이썬
경로만 해당"* 이라고 **무조건**으로 적었다. **그 서술은 쓰인 시점엔 참이었다** — 당시 러너의
`ARG ICEBERG_VERSION`이 `1.6.1`(번들 SDK `2.26.20`, 경계 이전)이었다.

**낡게 만든 것은 시간이 아니라 버전을 올린 커밋이다.** 러너 Dockerfile 2종을 `1.6.1 → 1.11.0`
으로 인상한 커밋이 **이 규약 문서를 변경 파일에 넣지 않았다.** 같은 커밋이 매니페스트에는
체크섬 env와 실측 주석을 넣었으므로 **매니페스트가 맞고 문서가 틀린** 상태로 갈라졌다.

⇒ **옛 서술을 기억하고 무조건 면제로 되돌리지 마라.** 되돌릴 조건은 하나뿐이다 —
러너가 싣는 `iceberg-aws-bundle`의 번들 SDK가 **`2.30.0` 미만**으로 내려갈 때.
그래서 **`ICEBERG_VERSION`을 움직이는 변경은 위 대응표를 함께 갱신**한다. 이것이
이 서술을 낡게 만드는 유일한 입력이다.

## 4. 적용 대상 전수

| 위치 | 주입 형태 |
| --- | --- |
| `compose.yml` | 공용 앵커 `x-dagster-common`의 env |
| `.env.example` | 호스트 실행·외부 도구용 기본값 |
| `dagster_project/common/constants.py` | `os.environ.setdefault` — 환경 누락 시 조용한 손상 방지 |
| `k8s/dagster/dagster-deploy.yaml` | ConfigMap env |
| `k8s/spark/spark-connect-server.yaml` | `spark.executorEnv.*`(executor) + 파드 env(driver 자신) |
| `k8s/spark/sparkapplication-poc.yaml` | `driverEnv` + `executorEnv` **양쪽** |
| `k8s/flink/flinkdeployment-session.yaml` | `podTemplate` env — **JM·TM 양쪽** |
| `k8s/catalog-pg-backup.yaml` | barman 사이드카 env |

**driver에만 주면 샌다** — client mode의 driver는 파드 자신이고 executor는 별도 파드다.
로컬 모드에서는 둘이 한 몸이라 driver env 하나가 양쪽을 덮었고, client mode가 그 우연을 깬다
([spark-connect.md](spark-connect.md)).

### 🔴 미적용 1건 — `k8s/spark/spark-thrift-server.yaml`

`spark-runner:0.5.0`(= bundle `1.11.0`)을 쓰고 `io-impl=S3FileIO`를 켜는데 체크섬 env 2종이
**없다**. 다만 이 매니페스트는 파일 머리에서 **⏸ 선언만 하고 배포하지 않는다**고 못 박혀 있어
**현재는 잠복**이다 — 도는 것이 없으니 손상도 없다.

⇒ **빠뜨린 것이 아니라 미배포다.** 그러나 **되살리는 사람이 반드시 함께 붙인다** —
`kubectl apply` 전에 env 2종을 추가한다. 붙이지 않고 띄우면 증상이 **기동 실패가 아니라
쓴 객체의 조용한 손상**으로 나오므로 배포 성공을 정상으로 읽게 된다.

## 5. 🔴 체크섬으로 **오진**하기 쉽다 — 반대 방향 선례

SeaweedFS의 `InternalError`를 체크섬 탓으로 돌렸다가 틀린 기록이 저장소에 남아 있다.
`k8s/catalog-postgres.yaml`의 Barman WAL 아카이빙 실패에 체크섬 가설로 사이드카 env를
넣었으나 **증상이 그대로**였고, 진짜 원인은 **볼륨 슬롯 상한**이었다
(`k8s/seaweedfs.yaml` — `-volume.max`를 올려 해소).

| 관측 | 갈래 |
| --- | --- |
| 쓰기는 성공하고 **이후 읽기**가 파싱에 실패 | 체크섬 |
| `No writable volumes and no free volumes left` | 볼륨 슬롯 |
| 같은 클라이언트로 **다른 버킷은 성공** | 체크섬 아님(버킷별 자원) |
| 체크섬 env가 **켜진 상태에서 재현** | 체크섬 아님 |

⇒ 체크섬 env를 넣었는데 증상이 안 바뀌면 **가설을 버린다.** 넣은 채로 다른 원인을 찾으면
해소된 뒤에 *"체크섬 때문이었다"* 는 잘못된 인과가 남는다. 이 문서는 갈래를 좁히는 용도이지
**모든 `InternalError`의 설명이 아니다.**

## 6. 🔴 증상은 쓰기 경로마다 다르고, 하나는 원인을 틀린 곳으로 가리킨다

`overwrite(overwrite_filter=...)`는 `delete`+`append`로 풀려 parquet를 다시 쓰는데
(copy-on-write), 이 경로는 **쓰기 시점에**
`AWS Error INVALID_ACCESS_KEY_ID during UploadPart`로 죽는다.

**자격증명 문제가 아니다** — 같은 키로 체크섬 모드만 바꾸면 통과한다(변인 하나만 달리한 대조).
이 문구를 보면 `.env`·시크릿·롤을 뒤지기 전에 **체크섬 모드를 먼저 본다.**

이 저장소가 반복해 적은 *"조용히 잘못된 값"* 과는 다른 축이다 — 관측 경로는 살아 있고
에러를 내고 멈추는데 **그 에러가 거짓 증언**을 한다.

## 7. 미확인

- ⓐ **「1.6.1에서 실제로 무해했다」를 재현하지 않았다.** 근거는 매니페스트에 남은 실측 주석과
  위 버전 대조 둘뿐이며, `1.6.1` 이미지를 다시 구워 손상 부재를 본 기록은 없다.
- ⓑ **`1.6.1`과 `1.11.0` 사이 중간 판본은 재지 않았다.** 경계가 `2.30.0`이라는 것은 SDK 쪽
  사실이고, 어느 Iceberg 판본에서 번들이 그 선을 넘었는지는 대응표의 **두 점만** 안다.
- ⓒ **Java 경로의 쓰기 유형별 차이**(append·compaction·rewrite)는 어느 기록에도 없다.
- ⓓ §6의 대조는 **일회용 컨테이너 실측**이라 in-cluster 재현은 하지 않았다.
- ⓔ **다른 쓰기 경로**(청크 append·IO 매니저)에서 같은 문구가 나오는지 세지 않았다.
- ⓕ **두 체크섬 변수를 함께 뒤집어** 어느 쪽이 원인인지 갈라내지 않았다.
- ⓖ Flink 체크포인트 파일(S3A 경로)이 실제로 온전한지는 실행으로 확인하지 않았다
  (`k8s/flink/flinkdeployment-session.yaml` 주석에 남은 미확인).
- ⓘ **파이썬 경로(boto3/botocore)의 기본값 전환 판본을 조사하지 않았다.** 영향받는다는 것은 이
  저장소의 실측이고 어느 판본부터인지는 모른다 — **자바의 `2.30.0`을 옮겨 적지 마라**(다른 계보다).
