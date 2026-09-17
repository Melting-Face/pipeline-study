# notebooks — 호스트 탐색용 노트북

Dagster 파이프라인 **밖**에서 레이크하우스를 ad-hoc 조회·탐색(EDA)하는 자리다.
파이프라인 정의(에셋·dbt 모델)는 여기 두지 않는다 — 정의는 `dagster_project/defs/`가 단일 출처다.

> **작성 규칙의 정본은 [`docs/conventions/analysis.md`](../docs/conventions/analysis.md)** 다
> (파일명 `NN-<slug>.ipynb` · 위→아래 1회 실행 재현 · 결론 수치는 gold/dbt 모델 경유 ·
> 반복되는 조회는 gold 모델로 승격). 이 문서는 **실행 환경**(포트·venv·접속·정리)을 다룬다.

## 실행

```shell
kubectl scale deploy/spark-connect --replicas=1      # 평시 0이다 — 먼저 올린다

# 접속 경로 ① TLS Ingress (기본) — .env의 SPARK_REMOTE·GRPC_DEFAULT_SSL_ROOTS_FILE_PATH를 쓴다
# 접속 경로 ② port-forward (폴백) — CA 미배포 환경·컨트롤러 장애용
kubectl port-forward svc/spark-connect 15002:15002   # 폴백을 쓸 때만, 별도 터미널

cd dagster/dockerfile.d/src
uv run --group notebook jupyter lab --port 8889 --notebook-dir ../../../notebooks

# 모델링 노트북(02·03)을 열 때는 ml 그룹을 함께 켠다
uv run --group notebook --group ml jupyter lab --port 8889 --notebook-dir ../../../notebooks
```

| 항목 | 값 | 이유 |
|---|---|---|
| 포트 | **8889** | 기본 8888은 compose SeaweedFS filer UI가 게시 |
| venv | **Dagster와 공유** (`dagster/dockerfile.d/src/.venv`) | `pyspark[connect]`·`pyiceberg`·`pandas`·`pyarrow`가 이미 있고, `dagster_project.common.*`를 그대로 import해 **에셋과 같은 코드로** 검증할 수 있다 |
| 의존성 | `[dependency-groups] notebook` | 런타임(`[project].dependencies`)과 분리 — 이미지·daemon에는 들어가지 않는다 |
| 모델링 의존성 | `[dependency-groups] ml` | `notebook`과도 **분리**한다 — 접속·관측만 하는 00·01에 scikit-learn이 필요 없고, ML 의존성 문제가 **접속 검증 관문**(`docs/test.md` §6의 nbconvert)까지 죽이지 않게 한다 |

> ⚠️ **그룹 분리는 선언 축에서만 보증된다.** venv가 하나라 `--group ml` 없이 띄워도 이미
> 설치된 scikit-learn은 import된다. 분리의 근거는 `pyproject.toml` 선언과 이미지 빌드 경로이지
> 로컬 디스크 상태가 아니다 — "`--group ml` 없이도 import된다"를 분리 실패로 읽지 않는다.

## SQL 엔진은 Spark Connect다 (Trino 아님)

재설계에서 Trino는 **제거 대상**이고 ad-hoc 조회는 **Spark SQL**로 간다
([`docs/architectures/trino.md`](../docs/architectures/trino.md) · [`docs/redesign.md`](../docs/redesign.md) §5).
compose의 `trino`는 `--profile legacy-sql` 로만 뜬다(방언 값 대조용).

카탈로그 설정(JDBC URI·warehouse·S3·자격증명)은 **Spark Connect 서버 측**
(`k8s/spark/spark-connect-server.yaml`)에 있다. → 클라이언트는 **`sc://` 주소만** 알면 되고
**비밀정보를 노트북에 두지 않는다**. pyiceberg로 직접 붙는 경로는 `.env`가 추가로 필요하다
(스타터 노트북 §6 참고).

같은 이유로 **executor 설정도 클라이언트가 못 바꾼다.** 런타임 `spark.conf.set(...)`은
`CANNOT_MODIFY_CONFIG`로 거부되고, `builder.config(...)`는 **조용히 무시**된다(경고로 강등) —
두 경로가 다르게 실패하므로 "설정했다"를 "적용됐다"로 읽지 않는다.

### pyiceberg 직접 접속 — 엔드포인트와 S3 키는 한 쌍이다

`.env`로 대상을 K8s 카탈로그로 돌릴 때 **`ICEBERG_S3_ACCESS_KEY`/`ICEBERG_S3_SECRET_KEY`도 함께**
채워야 한다. 엔드포인트(`ICEBERG_S3_ENDPOINT`)만 K8s로 바꾸고 키는 공용 `AWS_ACCESS_KEY_ID`
(compose SeaweedFS용)를 두면 **두 SeaweedFS의 키가 달라** 이렇게 죽는다(실측):

| 단계 | 결과 |
|---|---|
| `list_namespaces()` · `list_tables()` | ✅ 성공 (카탈로그 DB만 보므로) |
| `load_table()` | ❌ `ACCESS_DENIED during HeadObject` (metadata.json을 S3에서 읽는 순간) |

**부분 성공이라 원인을 오해하기 쉽다** — 나열이 되니 자격증명은 맞다고 넘겨짚게 된다.
`ICEBERG_S3_*`를 비우면 공용 `AWS_*`로 폴백하므로 **compose 단독 구성은 기존대로** 동작한다.
값은 `kubectl get secret lakehouse-creds -o jsonpath='{.data.s3-access-key}' | base64 -d` 로 얻는다.

> Spark Connect 경로는 이 문제와 무관하다 — 자격증명이 서버 측에 있어 클라이언트에 비밀이 필요 없다.
> **그래서 기본 경로는 Spark Connect다.**

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
| `00-lakehouse-connect.ipynb` | 접속 스타터 — 환경 로드·경로 점검 → Spark Connect → 카탈로그 탐색 → pandas → Iceberg 메타데이터 → (선택) pyiceberg 직접 접속 | `notebook` |
| `01-spark-on-k8s.ipynb` | K8s executor 관측 — 클라이언트가 못 바꾸는 것 → **계산이 어디서 도는가**(REST API 태스크 대조) → 파티션의 두 얼굴 → 스냅샷 계보 | `notebook` |
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
> ⚠️ **존재하지 않는 테이블을 Spark로 조회하면 `JDBC catalog is initialized without view support`라는
> 엉뚱한 에러**가 난다. 카탈로그 설정 문제처럼 보이지만 원인은 **테이블 부재**다 —
> 에러 메시지를 원인으로 읽지 말고 위 쿼리나 `show tables`로 실재부터 확인한다.

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

⚠️ **3층이 왜 필요한가** — `scripts/worker_path_guard.py`는 **워커의 쓰기만** 본다.
Jupyter 커널은 그 가드 **밖**이라, 노트북이 저장소 안에 쓰는 것을 막는 기계는 없다.
그래서 저장 셀이 직접 경로를 확인하고 에러를 내고 멈춘다.

실험 추적은 이 `metrics.json` 하나다(MLflow 등 상주 서비스를 두지 않는다).
그래서 **다시 만들 수 있을 만큼** 적는다 — 시드·분할 방식·버전·커밋·산출 엔진.

## 컴퓨트 정리

Spark Connect는 클러스터의 **유일한 상주 컴퓨트**다. 오래 안 쓸 거면 내린다
([`docs/conventions/k8s.md`](../docs/conventions/k8s.md)).

```shell
kubectl scale deploy/spark-connect --replicas=0
kubectl get pods -l spark-role=executor   # 🔴 executor도 함께 사라져야 한다
```

`--master k8s://`라 **executor 파드가 driver와 함께 상주**한다. 내린 뒤에도 executor가 남았다면
회수가 안 된 것이고, 에러도 알림도 없이 1 CPU를 계속 점유한다.
