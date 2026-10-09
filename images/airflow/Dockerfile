# 공식 Airflow 이미지에 우리 DAG만 얹는다 (버전은 정확히 고정, latest 금지)
FROM apache/airflow:3.2.2

# DAG 파일 소유권은 airflow 사용자 (그룹 root — 공식 이미지 규약)
COPY --chown=airflow:root dags/ /opt/airflow/dags/
