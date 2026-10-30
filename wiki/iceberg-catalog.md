# Iceberg JDBC 카탈로그를 Postgres에 둘 때 — 테이블 목록은 DB에, 데이터는 S3에 있다

> 스터디 장. 세 엔진이 하나의 Iceberg 카탈로그를 공유하도록 JDBC 카탈로그를 CloudNativePG 위에 세우며 배운 것.
> 결론부터 쓰면 — **카탈로그가 비면 S3의 파일은 남아도 테이블은 사라진다.
> 그래서 카탈로그의 수명이 곧 테이블의 수명이다.**

## 개념

Iceberg 카탈로그는 「테이블 이름 → 현재 메타데이터 파일 위치」를 가리키는 포인터 저장소다.
JDBC 카탈로그는 그 포인터를 관계형 DB의 테이블에 둔다.
쓰는 쪽은 포인터를 바꿀 때 낙관적 동시성으로 경합을 푼다.
데이터 파일과 메타데이터 파일은 오브젝트 스토리지에 있고 카탈로그 DB에는 포인터만 있다.

- 공식 문서: [Iceberg — JDBC Catalog](https://iceberg.apache.org/docs/latest/jdbc/) ·
  [CloudNativePG](https://cloudnative-pg.io/)
- 오퍼레이터 일반론은 [k8s-operators.md](k8s-operators.md)에 있다.

## 이 저장소에서의 쓰임

- 카탈로그 DB는 CNPG `Cluster` CR [`cluster.yaml`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/charts/catalog-postgres/templates/cluster.yaml)이 선언한다.
  오퍼레이터는 [`gitops/charts/cnpg-operator/`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/charts/cnpg-operator)가 설치한다.
- 구 구성은 `Deployment`에 `emptyDir`를 붙인 형태였고, 파드가 재기동하면 카탈로그가 통째로 사라졌다.
  S3의 파일은 남지만 테이블이 없으므로 복구는 재적재다. 이 이유로 오퍼레이터가 관리하는 `Cluster`로 바꿨다.
- 서비스 이름에는 접미사가 붙는다. 쓰기는 `catalog-postgres-rw`, 읽기 전용은 `-ro`, 전체는 `-r`이다.
  접미사 없는 이름은 만들어지지 않는다.
- 크리덴셜은 오퍼레이터의 자동 생성 시크릿이 아니라 선언 시크릿 `catalog-pg-app`을 쓴다.
  Dagster와 dbt가 같은 DB에 직접 붙으므로 비밀번호를 사람이 옮기다 어긋나는 일을 막으려는 선택이다.
- 같은 `Cluster` 안에 Airflow 메타 DB용 롤과 DB가 따로 있다. 카탈로그 롤과 시크릿을 공유하지 않는다.
- 세 엔진 Spark·Flink·Trino와 Dagster·dbt가 이 카탈로그를 공유한다. 카탈로그 이름은 전 엔진이 `iceberg`로 같아야 한다.
- 이 DB의 메트릭은 클러스터 안 Prometheus가 `cnpg` job으로 긁는다. [prometheus-monitoring.md](prometheus-monitoring.md)를 본다.
- S3 쪽 사정은 [seaweedfs-s3.md](seaweedfs-s3.md)에 있다.
- 규칙 정본은 [docs/conventions/k8s/cnpg.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/k8s/cnpg.md)와
  [docs/conventions/k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/k8s.md) §11이다.
  비밀번호 회전 같은 집행 절차는 여기 옮기지 않고 cnpg.md만 따른다.

## 실습

**띄우기** — 따로 띄우지 않는다. 카탈로그는 앱 수렴으로 함께 뜨고, Secret 단계가 먼저 있어야 진행한다
([docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3).
클러스터를 올리는 순서는 [k8s-bootstrap.md](k8s-bootstrap.md)다.

**확인 1 — 클러스터 상태**

출처: [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 5단계 「⑤ 앱 수렴」

```shell
kubectl get clusters.postgresql.cnpg.io -n default
```

```text
# 관측: kubectl get clusters.postgresql.cnpg.io -n default
NAME               AGE     INSTANCES   READY   STATUS                     PRIMARY
catalog-postgres   <경과>   1           1       Cluster in healthy state   catalog-postgres-1
```

- 성공 기준: `STATUS`가 `Cluster in healthy state`이고 `READY`가 `INSTANCES`와 같다.

**확인 2 — 호스트에서 닿는 길**

출처: [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-1 「접근 경로」

```shell
kubectl port-forward svc/catalog-postgres-rw 15432:5432
```

```text
# 관측: kubectl port-forward svc/catalog-postgres-rw 15432:5432
Forwarding from 127.0.0.1:15432 -> 5432
Forwarding from [::1]:15432 -> 5432
Handling connection for 15432
```

- 성공 기준: `Forwarding from` 두 줄이 보이고, 클라이언트가 붙을 때 `Handling connection`이 찍힌다.
- 터널이 살았는지와 터널 없는 포트의 음성 대조는 [dagster-orchestration.md](dagster-orchestration.md)의 실습에 있다.
- 호스트는 JDBC·S3만 `port-forward`를 쓰고 HTTP는 Ingress로 나간다. S3는 클러스터 밖이라 터널이 없다.

**내리기** — 앱 제거는 설정 삭제 PR과 Application 손 삭제를 더한 비가역 절차다.
`Prune=false,Delete=false`가 걸려 있어 앱을 지워도 카탈로그가 따라 지워지지 않는다.
경로는 [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 「운영 — 바꾸기·되돌리기」 표를 따른다.

## 고장 대응

| 증상 | 먼저 볼 것 | 처방 |
| --- | --- | --- |
| 테이블 나열은 되는데 `load_table`에서 `ACCESS_DENIED` | 엔진이 쓰는 S3 키가 카탈로그 접속 키와 한 벌인가 | 접속 대상을 바꾸는 값은 한 벌로 바꾼다. 전파 체인은 [docs/operations.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/operations.md) §1-2 |
| 엔진마다 서로의 테이블이 보이지 않는다 | 엔진별 카탈로그 이름 | 전 엔진을 `iceberg`로 통일한다. 설정 위치는 k8s.md §11 |
| Secret을 바꿨는데 롤 비밀번호가 옛 값이다 | 그 롤이 `managed.roles`에 선언돼 있는가 | 절차는 cnpg.md를 따른다. 워크로드 재기동까지 한 벌이다 |
| 서비스 `catalog-postgres`로 접속이 안 된다 | 서비스 이름 접미사 | `catalog-postgres-rw`로 붙는다 |
| PVC를 늘릴 수 없다 | 스토리지 클래스의 확장 허용 여부 | 용량은 처음에 넉넉히 잡고, 늘리려면 클러스터를 다시 만든다 |

다섯 행 모두 cnpg.md와 k8s.md §11에 적힌 증상이다.

## 함정

### 카탈로그 이름이 다르면 같은 DB를 봐도 빈 카탈로그다

출처: [docs/conventions/k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/k8s.md) §11.
JDBC 카탈로그는 `catalog_name` 컬럼으로 레지스트리를 나눈다.
Spark와 Flink가 `jdbccat`, Dagster와 Trino가 `iceberg`로 갈려 있었고, 같은 DB와 같은 버킷을 보는데도
Dagster가 적재한 테이블이 Spark에서 보이지 않을 상태였다.
에러가 아니라 빈 목록으로 나타나는 점이 위험하다.

### 나열 성공을 접속 성공으로 읽지 않는다

출처: [docs/conventions/analysis.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/analysis.md) §3.
`list_namespaces()`와 `list_tables()`는 카탈로그 Postgres만 읽어 성공한다.
`metadata.json`을 S3에서 읽는 `load_table()`에서 비로소 `ACCESS_DENIED`가 난다.
키를 분리한 뒤에도 `ICEBERG_S3_*`를 비워 두면 같은 증상이 재현된다.

### 초기화 1회와 회전은 다른 일이다

출처: [docs/conventions/k8s/cnpg.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/k8s/cnpg.md).
`bootstrap.initdb.secret`은 초기화 때 한 번만 읽는다.
스크립트를 다시 돌려 Secret만 바뀌면 DB 롤은 옛 값 그대로여서, 성공한 것처럼 보이는데 안 바뀐 상태가 된다.

### 백업 배선은 꺼져 있다

출처: cnpg.md의 백업·PITR 항목.
`spec.plugins`가 주석 처리돼 있어 백업은 돌지 않고 카탈로그 소실 시 복구 수단은 재적재다.
끈 이유는 S3로의 `PutObject`가 실패해 WAL 아카이빙이 재시도 루프에 빠졌기 때문이다.
이 백업은 원본과 같은 호스트 디스크에 놓여 DR이 아니라는 단서도 같은 항목에 있다.
