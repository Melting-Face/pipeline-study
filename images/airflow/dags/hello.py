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
    """이미지 빌드·배포와 두 executor 경로(Celery·Kubernetes)를 확인하는 DAG."""

    # executor 미지정 → 배포 기본값(목록 첫 번째 = CeleryExecutor)
    @task
    def say_hello() -> None:
        # 현재 시각을 KST로 남긴다(태스크 로그에 기록된다)
        logger.info(
            "hello from Airflow (celery path), now (KST) = %s",
            pendulum.now(KST).isoformat(),
        )

    # 태스크 단위로 KubernetesExecutor를 지정한다(태스크마다 파드 1개)
    @task(executor="KubernetesExecutor")
    def say_hello_k8s() -> None:
        logger.info(
            "hello from Airflow (k8s path), now (KST) = %s",
            pendulum.now(KST).isoformat(),
        )

    say_hello() >> say_hello_k8s()


hello()
