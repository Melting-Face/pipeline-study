# 공식 Airflow 이미지에 우리 DAG만 얹는다 (버전은 정확히 고정, latest 금지)
FROM apache/airflow:3.2.2

# 추가 의존성 — apache-airflow 를 같은 명령에 고정해 넣어야 해석기가 Airflow 를 바꾸지 않는다
COPY --chown=airflow:root requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir "apache-airflow==3.2.2" -r /tmp/requirements.txt

# dbt 는 별도 venv — dbt-core 와 Airflow 의 의존성 충돌을 피한다(설계 F4, Cosmos dbt_executable_path)
USER root
RUN python -m venv /opt/dbt-venv \
    && /opt/dbt-venv/bin/pip install --no-cache-dir dbt-core==1.10.23 dbt-trino==1.10.6 \
    && chown -R airflow:root /opt/dbt-venv
USER airflow

# dbt 프로젝트 — 이름 붙은 빌드 컨텍스트 `dbt`(dagster/dockerfile.d/src/dbt_pipelines)에서 필요한 것만.
# 🔴 디렉터리를 한 COPY 에 여럿 주면 내용이 평탄화된다 — 하나씩 복사한다.
#    target/·dbt_packages/(다른 worktree 의 산출물)는 일부러 고르지 않는다.
ARG DBT_DIR=/opt/airflow/dbt/dbt_pipelines
# hadolint DL3022 은 `dbt` 를 이전 FROM 스테이지로 오인한다 — 이름 붙은 빌드 컨텍스트라 오탐이다(줄마다 끈다)
# hadolint ignore=DL3022
COPY --from=dbt --chown=airflow:root dbt_project.yml packages.yml package-lock.yml profiles.yml ${DBT_DIR}/
# hadolint ignore=DL3022
COPY --from=dbt --chown=airflow:root models ${DBT_DIR}/models
# hadolint ignore=DL3022
COPY --from=dbt --chown=airflow:root macros ${DBT_DIR}/macros
# hadolint ignore=DL3022
COPY --from=dbt --chown=airflow:root tests ${DBT_DIR}/tests
# hadolint ignore=DL3022
COPY --from=dbt --chown=airflow:root seeds ${DBT_DIR}/seeds
# hadolint ignore=DL3022
COPY --from=dbt --chown=airflow:root snapshots ${DBT_DIR}/snapshots
# hadolint ignore=DL3022
COPY --from=dbt --chown=airflow:root analyses ${DBT_DIR}/analyses
# manifest 를 빌드 시점에 굽는다 — parse 는 엔진에 접속하지 않는다(TRINO_HOST 는 렌더용 더미)
RUN cd ${DBT_DIR} \
    && /opt/dbt-venv/bin/dbt deps \
    && TRINO_HOST=build-time-placeholder /opt/dbt-venv/bin/dbt parse --profiles-dir .

# DAG 파일 소유권은 airflow 사용자 (그룹 root — 공식 이미지 규약)
COPY --chown=airflow:root dags/ /opt/airflow/dags/
# 🔴 Airflow 3 의 DagBag 은 DAG 폴더를 sys.path 에 넣지 않는다(실측 — DAG 의 `import lakehouse` 가
#    ModuleNotFoundError). 공용 패키지 `lakehouse` 를 DAG·워커·k8s 태스크 파드 어디서든 import 하도록 명시한다.
ENV PYTHONPATH=/opt/airflow/dags
