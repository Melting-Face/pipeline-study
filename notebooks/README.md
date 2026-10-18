# notebooks — 호스트 탐색용 노트북

Dagster 파이프라인 **밖**에서 레이크하우스를 ad-hoc 조회·탐색(EDA)하는 자리다.
파이프라인 정의(에셋·dbt 모델)는 여기 두지 않는다 — 정의는 `dagster_project/defs/`가 단일 출처다.

> **작성 규칙의 정본은 [`docs/conventions/analysis.md`](../docs/conventions/analysis.md)** 다
> (파일명 `NN-<slug>.ipynb` · 위→아래 1회 실행 재현 · 결론 수치는 gold/dbt 모델 경유 ·
> 반복되는 조회는 gold 모델로 승격). 이 문서는 **실행 환경**(포트·venv·접속·정리)을 다룬다.

## 실행

```shell
# Trino는 ArgoCD가 상시 운영한다 — 올릴 것이 없다.
# 접속 대상은 .env의 TRINO_HOST·TRINO_PORT(기본 trino.localtest.me:8080, Ingress·HTTP·인증 없음)

cd dagster/dockerfile.d/src
uv run --group notebook jupyter lab --port 8889 --notebook-dir ../../../notebooks

# 모델링 노트북(02·03)을 열 때는 ml 그룹을 함께 켠다
uv run --group notebook --group ml jupyter lab --port 8889 --notebook-dir ../../../notebooks
```

| 항목 | 값 | 이유 |
|---|---|---|
| 포트 | **8889** | 기본 8888은 스택 A SeaweedFS filer UI가 게시 |
| venv | **Dagster와 공유** (`dagster/dockerfile.d/src/.venv`) | `trino`·`pyiceberg`·`pandas`·`pyarrow`가 이미 있고, `dagster_project.common.*`를 그대로 import해 **에셋과 같은 코드로** 검증할 수 있다 |
| 의존성 | `[dependency-groups] notebook` | 런타임(`[project].dependencies`)과 분리 — 이미지·daemon에는 들어가지 않는다 |
| 모델링 의존성 | `[dependency-groups] ml` | `notebook`과도 **분리**한다 — 접속만 하는 00에 scikit-learn이 필요 없고, ML 의존성 문제가 **접속 검증 관문**(`docs/test.md` §6의 nbconvert)까지 죽이지 않게 한다 |

> ⚠️ **그룹 분리는 선언 축에서만 보증된다.** venv가 하나라 `--group ml` 없이 띄워도 이미
> 설치된 scikit-learn은 import된다. 분리의 근거는 `pyproject.toml` 선언과 이미지 빌드 경로이지
> 로컬 디스크 상태가 아니다 — "`--group ml` 없이도 import된다"를 분리 실패로 읽지 않는다.

## SQL 엔진은 Trino다

종전 재설계에서 Trino는 제거 대상이었고 ad-hoc 조회는 Spark Connect로 갔다. **Spark Connect를 철거하면서
이 결정이 번복됐다** — Trino가 K8s 앱(`gitops/apps/trino/`, coordinator 단독)으로 돌아와 dbt·ad-hoc 조회
엔진을 맡는다([`docs/redesign.md`](../docs/redesign.md) §3 「Trino 제거 결정 번복」).
카탈로그는 Spark·Flink와 **같은 CNPG Iceberg JDBC 카탈로그**(`iceberg`)다.

카탈로그 설정(JDBC URI·warehouse·S3·자격증명)은 **Trino 서버 측**(`gitops/apps/trino/values.yaml`)에 있다.
→ 클라이언트는 **호스트·포트만** 알면 되고 **비밀정보를 노트북에 두지 않는다**. 접속은
`trino.dbapi.connect(host=TRINO_HOST, port=TRINO_PORT, user="notebook", catalog="iceberg", http_scheme="http")`이고,
env 이름은 Dagster `common/constants.py`·dbt `profiles.yml`과 같다(한 벌). `user`는 인증이 아니라 쿼리 귀속 라벨이다.
pyiceberg로 직접 붙는 경로는 `.env`가 추가로 필요하다(스타터 노트북 §6 참고).

| 주의 | 내용 |
|---|---|
| 무인증 쿼리 API | Trino는 UI와 쿼리 제출이 **같은 포트**라 Ingress가 무인증 쿼리 API를 함께 연다(`values.yaml` 주석) — 「UI만 열었다」로 읽지 않는다 |
| 방언 | Spark SQL과 다르다 — `approx_percentile`·배열 리터럴 `array[…]`·메타데이터 테이블 `"<table>$snapshots"`, `SHOW TABLES` 결과 컬럼은 `Table` 하나 |
| 결과 적재 | `cursor.fetchall()`은 결과 **전량**을 클라이언트 메모리에 올린다 — 집계하거나 `LIMIT`을 건 SQL에만 쓴다(`toPandas()`와 같은 규율) |

### 🔴 Trino 이관 후 노트북은 미실행이다

이관 시점에 k8s Trino의 `SHOW SCHEMAS FROM iceberg`는 `information_schema`·`system`만 돌려줬다
(사용자 네임스페이스 0개 — **카탈로그가 비어 있다**). 그래서 02·03(과 00의 §3 이후)은 **Trino로 한 번도
실행되지 않았다.** SQL은 Trino 방언으로 고쳐 구문·함수 해석까지만 확인했다.
**재적재 후 위→아래 1회 실행으로 재현을 확인해야 한다.** 노트북에 남은 과거 수치는 **Spark Connect** 산출이다.

### pyiceberg 직접 접속 — 엔드포인트와 S3 키는 한 쌍이다

`.env`로 대상을 K8s 카탈로그로 돌릴 때 **`ICEBERG_S3_ACCESS_KEY`/`ICEBERG_S3_SECRET_KEY`도 함께**
채워야 한다. 엔드포인트(`ICEBERG_S3_ENDPOINT`)만 K8s로 바꾸고 키는 공용 `AWS_ACCESS_KEY_ID`
(compose Dagster용)를 두면 **두 SeaweedFS의 키가 달라** 이렇게 죽는다(실측):

| 단계 | 결과 |
|---|---|
| `list_namespaces()` · `list_tables()` | ✅ 성공 (카탈로그 DB만 보므로) |
| `load_table()` | ❌ `ACCESS_DENIED during HeadObject` (metadata.json을 S3에서 읽는 순간) |

**부분 성공이라 원인을 오해하기 쉽다** — 나열이 되니 자격증명은 맞다고 넘겨짚게 된다.
`ICEBERG_S3_*`를 비우면 공용 `AWS_*`로 폴백하므로 **compose 단독 구성은 기존대로** 동작한다.
값은 `kubectl get secret lakehouse-creds -o jsonpath='{.data.s3-access-key}' | base64 -d` 로 얻는다.

> Trino 경로는 이 문제와 무관하다 — 자격증명이 서버 측에 있어 클라이언트에 비밀이 필요 없다.
> **그래서 기본 경로는 Trino다.**

## ⚠️ 셀 출력은 커밋되지 않는다

원천은 비식별 연구 데이터셋이지만 **DUA 대상**이다([`docs/security.md`](../docs/security.md)).
`.ipynb`의 셀 출력에는 조회 결과가 **그대로 박제**되고, `gitleaks`는 크리덴셜 패턴을 잡지
헬스 데이터를 잡지 못한다. 그래서 두 겹으로 막는다.

- `nbstripout` pre-commit 훅 — 커밋 시 셀 출력·실행횟수 제거 (`.pre-commit-config.yaml`)
- `.gitignore` — `**/.ipynb_checkpoints/` (Jupyter 자동 스냅샷은 출력을 그대로 담는다)

훅을 `--no-verify`로 우회해 커밋하지 않는다.

## 파일

| 파일 | 내용 | 그룹 |
|---|---|---|
| `00-lakehouse-connect.ipynb` | 접속 스타터 — 환경 로드·경로 점검 → Trino → 카탈로그 탐색 → pandas → Iceberg 메타데이터 → (선택) pyiceberg 직접 접속 | `notebook` |
| `02-water-task-candidates.ipynb` | **과제 후보 실측** — 규모·시간 범위 → 후보 3종의 표본·라벨 출처·누수 위험 → 피처 가용성 → 선정 근거표 | `notebook` |
| `03-water-discharge-baseline.ipynb` | **베이스라인** — attrition → 수위→유량 회귀 → 관측소 단위 분할 → dummy·Ridge·HistGB → 음성 대조 → 유효 표본 검증 → 산출물 | `+ ml` |

> 02가 03의 입력이다 — 과제는 02의 **실측 결과로** 고른다(미리 정해 두고 확인하지 않는다).

### ⚠️ 왜 MIMIC-IV가 아니라 USGS인가

이 저장소의 분석 질문 정본은 MIMIC-IV의 SOFA → Sepsis-3다([`dataset_schema.md`](../docs/dataset_schema.md)).
그러나 **`mimiciv`·`eicu`는 네임스페이스만 등록돼 있고 Iceberg 테이블이 0건**이며,
`s3://warehouse/raw/mimiciv/icu/`도 비어 있다 — **정의는 있으나 실체가 없다.**
그래서 지금 실제로 조회되는 `usgs_water`에서 시작한다. 데이터가 갖춰지면 정본 질문으로 돌아간다.

> 🔴 이건 "적재가 실패했다"가 아니라 **"아직 적재하지 않았다"** 일 가능성이 높다 — 둘은 다른 축이고,
> 이 문서는 어느 쪽인지 판정하지 않는다. 확인 방법만 남긴다:
> ```shell
> kubectl exec catalog-postgres-1 -c postgres -- \
>   psql -U postgres -d iceberg -c "select table_namespace, count(*) from iceberg_tables group by 1"
> ```
> Trino는 없는 네임스페이스·테이블을 `SCHEMA_NOT_FOUND`·`TABLE_NOT_FOUND`로 **직접** 알린다
> (Spark Connect 시절에는 테이블 부재가 `JDBC catalog is initialized without view support`라는 엉뚱한
> 에러로 나왔다). ⚠️ 대신 **`EXPLAIN`도 테이블 해석에서 먼저 멈춘다** — 테이블이 없을 때의
> `SCHEMA_NOT_FOUND`는 「구문 통과」까지만 뜻하고 함수 이름 오류(`FUNCTION_NOT_FOUND`)는 가려진다.

## 🔴 학습 산출물은 저장소 밖에 둔다

모델 파일은 **훈련 데이터의 함수**다 — ① 적합 산출물에 훈련 분포 통계가 박히고
(`SimpleImputer.statistics_`·`StandardScaler.mean_`·회귀계수) ② 트리 계열은 **관측치 자체가
분할 임계값**이 된다. "비식별 데이터로 만든 모델"이 자동으로 비식별인 것은 아니므로,
데이터 반출과 같은 등급으로 다룬다.

| 항목 | 값 |
|---|---|
| 착지 | `$DATA_EXTRACT_DIR`(기본 `~/extracts`) 하위 `ml/<slug>/` — **저장소 밖** |
| 내용 | `model.joblib`, `metrics.json`(지표·유병률·N·시드·라이브러리 버전·커밋 SHA) |
| 1층 방어 | `.gitignore`의 `notebooks/**/*.joblib` |
| 2층 방어 | `no-health-data-files` 훅 정규식(`joblib` 포함) |
| 3층 방어 | 03의 저장 셀 안 경로 검사 |

⚠️ **3층이 왜 필요한가** — 쓰기 경로를 막는 hook은 없고, 있더라도 하네스 도구 호출만 본다.
Jupyter 커널은 하네스 **밖**이라, 노트북이 저장소 안에 쓰는 것을 막는 기계는 없다.
그래서 저장 셀이 직접 경로를 확인하고 에러를 내고 멈춘다.

실험 추적은 이 `metrics.json` 하나다(MLflow 등 상주 서비스를 두지 않는다).
그래서 **다시 만들 수 있을 만큼** 적는다 — 시드·분할 방식·버전·커밋·산출 엔진.

## 정리

Trino는 ArgoCD가 상시 운영하므로 **노트북이 내릴 컴퓨트가 없다** — 각 노트북 마지막 셀의 `conn.close()`로
커넥션만 닫는다. ⚠️ 그렇다고 비용이 0은 아니다 — 상주 coordinator가 CPU 예산을 나눠 쓴다.
배분은 [`docs/resource-sizing.md`](../docs/resource-sizing.md) §(B) 「컴포넌트 배분」이다.
