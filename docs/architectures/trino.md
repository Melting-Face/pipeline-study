> **이행 상태**: **상시 기동 해제** — compose `trino`가 `profiles: ["legacy-sql"]`로
> 내려가 기본 `up`에서 빠졌다(자원 3 CPU / 6G 회수). 호스트 게시 포트도 `8080→8081`로 옮겼다
> (8080은 kind ingress가 점유).
>
> **"중단"과 "삭제"는 분리한다.** 제거의 선행조건은 둘이었다.
>
> | 선행조건 | 상태 |
> |---|---|
> | ① **유지보수 프로시저 Spark 이관** | 없으면 기능 공백이 생긴다. 🔴 **이관 완료와 실행 검증은 다른 축**이다 |
> | ② **22모델 방언 값 대조** | Trino가 정본이다. 기준은 *"도는 것"이 아니라 "같은 값"* 이다 |
>
> ②가 남아 **`dbt-trino` 어댑터와 `TrinoResource`는 유지**한다(리소스는 유지보수용에서
> **대조용**으로 역할만 바뀌었다). ②까지 해소되면 compose 서비스·어댑터·`common/trino.py`를
> 하나의 논리적 커밋으로 제거한다.
>
> **추가 변경**: **dbt 기본 타깃이 trino를 떠났다.** `profiles.yml`의 `target`이
> `"{{ env_var('DBT_TARGET', 'spark_connect') }}"` 라서, 아무것도 지정하지 않으면 **Spark Connect로 간다.**
> trino는 이제 *기본 경로*가 아니라 **명시적으로 불러내는 대조 경로**다.
>
> ```shell
> docker compose --profile legacy-sql up -d trino   # ① 값 대조가 필요할 때만 기동
> DBT_TARGET=dev dbt run                            # ② 타깃을 명시해 trino로 보낸다
> ```
>
> 🔴 **`trino`라는 이름의 타깃은 없다.** `profiles.yml`에 실재하는 타깃명은
> **`dev` · `prod` · `spark_session` · `spark_connect` · `spark_thrift`** 다.
> trino로 가는 것은 `dev`(개발)와 `prod`이며, **엔진 이름이 아니라 환경 이름으로 적혀 있다.**
> `DBT_TARGET=trino`는 조용히 도는 대신 프로파일 조회에서 실패한다 — 대조 작업 때 가장 먼저 걸리는 함정이다.
>
> ad-hoc 조회는 **Spark SQL**로 간다 — 호스트 노트북 경로는 [`notebooks/README.md`](../../notebooks/README.md).

# Trino (아키텍처 · 프로젝트 관점)

## 개요

Trino는 **MPP(대규모 병렬 처리) 분산 SQL 쿼리 엔진**이다. 데이터를 자체 저장하지 않고
(무상태), 여러 소스(Iceberg·Hive·RDB 등)에 **연합 쿼리(federated query)** 한다.
coordinator가 SQL을 분해해 worker들에 분산하고, 메모리 기반 파이프라인으로 배치 SQL을
빠르게 처리한다.

## 이 프로젝트에서의 위치 — 🔎 재설계로 제거(현행 compose까지 채택)

> **상태 변경**: 현행 compose 스택에서는 ✅ 채택이었으나, [재설계](../redesign.md)에서 **제거**한다.
> dbt는 **`dbt-spark`** 로 이관하고, ad-hoc 조회는 **Spark SQL**로 대체한다.

- **(현행) 역할**: dbt(`dbt-trino`)가 접속하는 쿼리 엔진. Iceberg 테이블을 읽고 써서 silver 모델을 만든다.
- **(현행) 채택 이유**:
  - **Iceberg JDBC 카탈로그 공유** — Trino와 Dagster(pyiceberg)가 **같은 Postgres `iceberg_catalog`** 를
    재사용한다(별도 메타스토어 불필요, [overview.md](overview.md)).
  - **dbt 친화** — `dbt-trino` 어댑터로 SQL 변환을 선언적으로 관리.
  - **경량 SQL 전용** — 배치 SQL 변환이 주 워크로드라 범용 엔진(Spark)보다 단순(YAGNI).
- **제거 이유·트레이드오프**: 재설계에서 컴퓨트를 **Spark(배치)+Flink(스트림)** 2엔진으로 통일하며 Trino를 뺀다.
  단일 PC 자원(6/16) 절약과 엔진 수 축소가 목적이나, **성숙한 인터랙티브 SQL·`dbt-trino`를 잃는 비용**을 감수한다
  ([redesign.md](../redesign.md) §5). 배치 SQL은 dbt-spark, 대규모 rewrite/compaction은 Spark로 이관([spark.md](spark.md)).

## 운영 메모 (현행 compose 한정)

> 재설계 이행 완료 시 아래는 레거시 참조가 된다. 유지보수 프로시저(`rewrite_data_files`·`remove_orphan_files`)는
> Trino `ALTER TABLE ... EXECUTE`에서 **Spark 프로시저**로 이관한다([spark.md](spark.md)).

- **JVM 기반** — 힙이 메모리 최다 소비. `trino/etc/jvm.config`의 `Xmx`를 호스트 한도 내로 유지
  ([resource-sizing.md](../resource-sizing.md)의 "3파일 메모리 제약").
- **버전**: 현재 `trinodb/trino:468`. Trino는 주 단위 릴리스라 **LTS(현재 477 계열)** 를 우선한다
  (비-LTS는 다음 릴리스 후 패치 중단 — [conventions/docker.md](../conventions/docker.md) §1-3).
- **Iceberg 유지보수**: pyiceberg 미지원 프로시저(`remove_orphan_files`)를 Trino
  `ALTER TABLE ... EXECUTE`로 실행한다([security.md](../security.md) §4-1).

## 참고

- Trino 문서: https://trino.io/docs/current/
- Iceberg connector: https://trino.io/docs/current/connector/iceberg.html
- 릴리스 유형(LTS): https://trino.io/docs/current/release.html
