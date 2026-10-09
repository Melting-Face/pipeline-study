"""KST 시각을 출력하는 학습용 hello DAG."""

import logging

import pendulum
from airflow.sdk import dag, task

logger = logging.getLogger(__name__)

# 타임존 정책: 스케줄·표시는 KST. tz-aware 객체만 쓰고 naive datetime은 쓰지 않는다.
KST = pendulum.timezone("Asia/Seoul")


@dag(
    dag_id="hello",
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Seoul"),
    schedule=None,  # 수동 트리거 전용
    catchup=False,
    tags=["study"],
)
def hello() -> None:
    """이미지 빌드·배포 경로를 확인하는 단일 태스크 DAG."""

    @task
    def say_hello() -> None:
        # 현재 시각을 KST로 남긴다(태스크 로그에 기록된다)
        logger.info("hello from Airflow, now (KST) = %s", pendulum.now(KST).isoformat())

    say_hello()


hello()
