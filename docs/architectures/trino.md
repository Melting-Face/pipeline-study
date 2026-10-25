> **이행 상태(K8s)**: 🚧 **K8s로 올라온다 — 재설계의 「Trino 제거」가 번복됐다**
> ([../redesign.md](../redesign.md) §3 「Trino 제거 결정 번복」). ArgoCD ApplicationSet의 **upstream 앱**
> (`gitops/apps/trino/` — 공식 차트 `trinodb/charts` + 저장소 values)이고, ns `default`·coordinator 단독·
> `trino.localtest.me`(Ingress)다. 카탈로그는 Spark·Flink와 **같은 CNPG Iceberg JDBC 카탈로그**다.
> **dbt 기본 타깃(`dev`)과 Iceberg 유지보수(`optimize`·`remove_orphan_files`)가 이 Trino로 왔다**
> (env `TRINO_HOST`·`TRINO_PORT`, 기본 `trino.localtest.me:8080`). 🔴 **Spark 시절 dbt 결과와의 값 대조는
> 하지 않았다(미대조)** — 사용자 결정으로 재적재·대조를 범위 밖에 뒀고, 대조는 Airflow로 옮기는 파이프라인부터 한다.
> 알려진 갈림 지점은 [conventions/dbt.md](../conventions/dbt.md) §Spark 시절과 값이 갈릴 수 있는 지점.
> **교차 엔진 실증**: Trino로 CTAS한 테이블을 Spark Connect가 같은 값으로 읽었다(행 수·합계·문자열 해시) —
> 카탈로그 공유와 쓰기 경로가 함께 확인됐다. 체크섬 축의 남은 미확인은 [checksum.md](../conventions/k8s/checksum.md) §7 ⓚ.
>
> **이행 상태(compose)**: **철거했다(B2)** — compose `trino`(`legacy-sql`)와 `trino/etc/`를 지웠다.
> 다른 카탈로그 DB(compose postgres)를 보던 두 번째 「Trino」가 사라져 이름이 다시 **하나의 엔진**(k8s)을 가리킨다.
> 철거 전의 선행조건(유지보수 이관·22모델 값 대조)은 dbt·유지보수가 k8s Trino로 옮겨오며 대상 자체가 바뀌었고,
> 값 대조는 위 「미대조」로 남는다. `dbt-trino` 어댑터와 `TrinoResource`는 현행 경로라 유지한다.
>
> 🔴 **`trino`라는 이름의 타깃은 없다.** `profiles.yml`에 실재하는 타깃명은 **`dev` · `prod`** 다.
> trino로 가는 것은 `dev`(개발)와 `prod`이며, **엔진 이름이 아니라 환경 이름으로 적혀 있다.**
> `DBT_TARGET=trino`는 조용히 도는 대신 프로파일 조회에서 실패한다 — 대조 작업 때 가장 먼저 걸리는 함정이다.
>
> ad-hoc 조회도 **Trino**(k8s)로 간다 — 호스트 노트북 경로는 [`notebooks/README.md`](../../notebooks/README.md).

# Trino (아키텍처 · 프로젝트 관점)

## 이 프로젝트에서의 역할

silver/gold 변환 — dbt가 k8s Trino에서 SQL을 실행하고, Trino는 Iceberg 유지보수도 맡는다.

## 어떻게 도나

- 배포: ArgoCD 앱 `gitops/apps/trino/`(upstream 차트, `config.yaml`·`values.yaml`).
- dbt: `dagster/dockerfile.d/src/dbt_pipelines/profiles.yml`의 `dev`·`prod` 타깃이 `TRINO_HOST`로 접속한다.
  타깃 이름은 엔진이 아니라 환경이다.
  엔진 방언은 `dagster/dockerfile.d/src/dbt_pipelines/macros/cross_engine.sql`이 흡수한다.
- 유지보수: Dagster `dagster/dockerfile.d/src/src/dagster_project/defs/maintenance.py`가
  `ALTER TABLE … EXECUTE`를 실행한다.
- 카탈로그: 다른 엔진과 같은 Iceberg JDBC 카탈로그 `iceberg`(CNPG)를 본다.

## 개요

Trino는 **MPP(대규모 병렬 처리) 분산 SQL 쿼리 엔진**이다. 데이터를 자체 저장하지 않고
(무상태), 여러 소스(Iceberg·Hive·RDB 등)에 **연합 쿼리(federated query)** 한다.
coordinator가 SQL을 분해해 worker들에 분산하고, 메모리 기반 파이프라인으로 배치 SQL을
빠르게 처리한다.

## 왜 이걸 골랐나 — 🚧 K8s 이행중(재설계의 제거 결정 번복)

> **상태 변경 이력**: compose 스택에서 ✅ 채택 → [재설계](../redesign.md)에서 **제거** 결정(dbt-spark 이관) →
> Spark Connect 철거로 dbt 엔진이 필요해져 **번복**, K8s upstream 앱으로 올라왔다. 아래 「제거 이유」는 번복된 판단이며
> 이력으로 남긴다.

- **(현행) 역할**: dbt(`dbt-trino`)가 접속하는 쿼리 엔진. Iceberg 테이블을 읽고 써서 silver 모델을 만든다.
- **(현행) 채택 이유**:
  - **Iceberg JDBC 카탈로그 공유** — Trino와 Dagster(pyiceberg)가 **같은 Postgres `iceberg_catalog`** 를
    재사용한다(별도 메타스토어 불필요, [overview.md](overview.md)).
  - **dbt 친화** — `dbt-trino` 어댑터로 SQL 변환을 선언적으로 관리.
  - **경량 SQL 전용** — 배치 SQL 변환이 주 워크로드라 범용 엔진(Spark)보다 단순(YAGNI).
- **(번복됨) 제거 이유·트레이드오프**: 재설계에서 컴퓨트를 **Spark(배치)+Flink(스트림)** 2엔진으로 통일하며
  Trino를 뺀다.
  단일 PC 자원(6/16) 절약과 엔진 수 축소가 목적이나, **성숙한 인터랙티브 SQL·`dbt-trino`를 잃는 비용**을 감수한다
  ([redesign.md](../redesign.md) §5). 배치 SQL은 dbt-spark, 대규모 rewrite/compaction은 Spark로 이관([spark.md](spark.md)).

## 운영 메모 (k8s)

> 유지보수 프로시저는 Spark로 갔다가 **Trino `ALTER TABLE ... EXECUTE`로 되돌아왔다**
> (k8s Trino 실행 — `defs/maintenance.py`).

- **JVM 기반** — 힙이 메모리 최다 소비. 힙·컨테이너 한도는 `gitops/apps/trino/values.yaml`이 정본이다 —
  배분 요약은 [resource-sizing.md](../resource-sizing.md) §(B).
- **버전**: `gitops/apps/trino/values.yaml`의 `image.tag`로 고정한다(`latest` 금지 —
  [conventions/docker.md](../conventions/docker.md) §1-3).
- **Iceberg 유지보수**: pyiceberg 미지원 프로시저(`remove_orphan_files`)를 Trino
  `ALTER TABLE ... EXECUTE`로 실행한다([security.md](../security.md) §4-1).

## 교훈

- [엔진마다 값이 갈리는 SQL — 같은 자리에서 두 번 틀렸다](../../wiki/cross-engine-sql.md)

## 참고

- Trino 문서: https://trino.io/docs/current/
- Iceberg connector: https://trino.io/docs/current/connector/iceberg.html
- 릴리스 유형(LTS): https://trino.io/docs/current/release.html
