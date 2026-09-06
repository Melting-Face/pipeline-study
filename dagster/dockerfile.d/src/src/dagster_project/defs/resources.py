"""공유 리소스 정의 — `defs/` 자동발견 대상.

`@dg.definitions`로 리소스만 담은 Definitions를 반환하면 `load_defs`가 수집·merge한다.
S3 접속·dbt·데이터셋별 IO 매니저·대용량 테이블 바인딩을 한 곳에서 선언한다.
접속 파라미터는 `common.constants`에서 참조하고, Iceberg 카탈로그 설정
(`IcebergCatalogConfig`)은 각 리소스에 직접 명시한다.

주의: dagster-iceberg의 IcebergCatalogConfig는 아직 dg.EnvVar를 지원하지 않으므로
(properties는 평문 문자열), 비밀값은 정의 로드 시점(컨테이너)의 os.environ에서 읽는다.
Trino의 iceberg JDBC 카탈로그와 동일한 pyiceberg properties를 쓴다.
"""

from dagster_aws.s3 import S3Resource
from dagster_iceberg.config import IcebergCatalogConfig
from dagster_iceberg.io_manager.arrow import PyArrowIcebergIOManager
from dagster_iceberg.resource import IcebergTableResource
from dagster_pyspark import LazyPySparkResource

import dagster as dg
from dagster_project.common.constants import (
    AWS_REGION,
    CATALOG_NAME,
    ICEBERG_CATALOG_URI,
    S3_ACCESS_KEY_ID,
    S3_ENDPOINT,
    S3_SECRET_ACCESS_KEY,
    SPARK_REMOTE,
    TRINO_HOST,
    TRINO_PORT,
    TRINO_USER,
    WAREHOUSE,
)
from dagster_project.common.dbt import build_dbt_resource
from dagster_project.common.trino import TrinoResource
from dagster_project.defs.eicu.constants import NAMESPACE as EICU_NS
from dagster_project.defs.mimic_iv.constants import NAMESPACE as MIMICIV_NS
from dagster_project.defs.usgs_water.constants import NAMESPACE as USGS_WATER_NS


@dg.definitions
def resources() -> dg.Definitions:
    """공유 리소스를 Definitions로 반환한다(load_defs가 자동 수집)."""
    # 카탈로그 접속 문자열은 common.constants에서 env로 조립한다(단일 출처).
    return dg.Definitions(
        resources={
            # 공유: S3 접속(SeaweedFS). 파라미터는 common.constants에서 추적.
            "s3": S3Resource(
                endpoint_url=S3_ENDPOINT,
                aws_access_key_id=S3_ACCESS_KEY_ID,
                aws_secret_access_key=S3_SECRET_ACCESS_KEY,
                region_name=AWS_REGION,
            ),
            "dbt": build_dbt_resource(),
            # Spark Connect 접속(Iceberg 유지보수 프로시저용 — defs/maintenance.py).
            # 카탈로그 설정은 **서버 측**에 있어 여기엔 주소만 온다(비밀정보 비노출).
            # `Lazy~`를 쓰는 이유: 세션을 `spark_session` **접근 시점**에 만든다.
            # 비-Lazy(PySparkResource)는 리소스 초기화에서 즉시 연결해, 유지보수 잡과
            # 무관한 run까지 Spark Connect(=port-forward) 가용성에 묶인다.
            "spark": LazyPySparkResource(spark_config={"spark.remote": SPARK_REMOTE}),
            # Trino 접속. 유지보수는 Spark로 이관했고, 이 접속은 dbt-spark 이행 중
            # **방언 값 대조**용으로 남긴다(docs/architectures/trino.md).
            "trino": TrinoResource(
                host=TRINO_HOST, port=TRINO_PORT, user=TRINO_USER, catalog=CATALOG_NAME
            ),
            # 데이터셋 전용 IO 매니저 (일반 적재: pa.Table → namespace.<asset> write)
            "io_manager_eicu": PyArrowIcebergIOManager(
                name=CATALOG_NAME,
                namespace=EICU_NS,
                config=IcebergCatalogConfig(
                    properties={
                        "type": "sql",
                        "uri": ICEBERG_CATALOG_URI,
                        "warehouse": WAREHOUSE,
                        "s3.endpoint": S3_ENDPOINT,
                        "s3.access-key-id": S3_ACCESS_KEY_ID,
                        "s3.secret-access-key": S3_SECRET_ACCESS_KEY,
                        "s3.region": AWS_REGION,
                        "s3.path-style-access": "true",
                    }
                ),
            ),
            "io_manager_mimiciv": PyArrowIcebergIOManager(
                name=CATALOG_NAME,
                namespace=MIMICIV_NS,
                config=IcebergCatalogConfig(
                    properties={
                        "type": "sql",
                        "uri": ICEBERG_CATALOG_URI,
                        "warehouse": WAREHOUSE,
                        "s3.endpoint": S3_ENDPOINT,
                        "s3.access-key-id": S3_ACCESS_KEY_ID,
                        "s3.secret-access-key": S3_SECRET_ACCESS_KEY,
                        "s3.region": AWS_REGION,
                        "s3.path-style-access": "true",
                    }
                ),
            ),
            # 대용량 경로 청크 append용 테이블 바인딩(IO 매니저 미사용).
            "mimiciv_chartevents_table": IcebergTableResource(
                name=CATALOG_NAME,
                namespace=MIMICIV_NS,
                table="chartevents",
                config=IcebergCatalogConfig(
                    properties={
                        "type": "sql",
                        "uri": ICEBERG_CATALOG_URI,
                        "warehouse": WAREHOUSE,
                        "s3.endpoint": S3_ENDPOINT,
                        "s3.access-key-id": S3_ACCESS_KEY_ID,
                        "s3.secret-access-key": S3_SECRET_ACCESS_KEY,
                        "s3.region": AWS_REGION,
                        "s3.path-style-access": "true",
                    }
                ),
            ),
            "mimiciv_labevents_table": IcebergTableResource(
                name=CATALOG_NAME,
                namespace=MIMICIV_NS,
                table="labevents",
                config=IcebergCatalogConfig(
                    properties={
                        "type": "sql",
                        "uri": ICEBERG_CATALOG_URI,
                        "warehouse": WAREHOUSE,
                        "s3.endpoint": S3_ENDPOINT,
                        "s3.access-key-id": S3_ACCESS_KEY_ID,
                        "s3.secret-access-key": S3_SECRET_ACCESS_KEY,
                        "s3.region": AWS_REGION,
                        "s3.path-style-access": "true",
                    }
                ),
            ),
            "eicu_nurse_charting_table": IcebergTableResource(
                name=CATALOG_NAME,
                namespace=EICU_NS,
                table="nurse_charting",
                config=IcebergCatalogConfig(
                    properties={
                        "type": "sql",
                        "uri": ICEBERG_CATALOG_URI,
                        "warehouse": WAREHOUSE,
                        "s3.endpoint": S3_ENDPOINT,
                        "s3.access-key-id": S3_ACCESS_KEY_ID,
                        "s3.secret-access-key": S3_SECRET_ACCESS_KEY,
                        "s3.region": AWS_REGION,
                        "s3.path-style-access": "true",
                    }
                ),
            ),
            # USGS 수문 bronze 두 벌. 🔴 순간값은 **append 전용**이라 IO 매니저를
            # 쓰지 않고 테이블 바인딩으로 직접 적재한다 — Iceberg Flink 스트리밍
            # 소스는 IncrementalAppendScan 기반이라 overwrite 스냅샷이 한 번이라도
            # 생기면 소스 자격을 잃고, 그 실패는 에러가 아니라 조용한 누락이다.
            "usgs_water_iv_table": IcebergTableResource(
                name=CATALOG_NAME,
                namespace=USGS_WATER_NS,
                table="water_iv_raw",
                config=IcebergCatalogConfig(
                    properties={
                        "type": "sql",
                        "uri": ICEBERG_CATALOG_URI,
                        "warehouse": WAREHOUSE,
                        "s3.endpoint": S3_ENDPOINT,
                        "s3.access-key-id": S3_ACCESS_KEY_ID,
                        "s3.secret-access-key": S3_SECRET_ACCESS_KEY,
                        "s3.region": AWS_REGION,
                        "s3.path-style-access": "true",
                    }
                ),
            ),
            # 관측소 메타(조인 차원)는 스트리밍 소스가 아니라 replace로 갱신한다.
            "usgs_water_sites_table": IcebergTableResource(
                name=CATALOG_NAME,
                namespace=USGS_WATER_NS,
                table="water_sites",
                config=IcebergCatalogConfig(
                    properties={
                        "type": "sql",
                        "uri": ICEBERG_CATALOG_URI,
                        "warehouse": WAREHOUSE,
                        "s3.endpoint": S3_ENDPOINT,
                        "s3.access-key-id": S3_ACCESS_KEY_ID,
                        "s3.secret-access-key": S3_SECRET_ACCESS_KEY,
                        "s3.region": AWS_REGION,
                        "s3.path-style-access": "true",
                    }
                ),
            ),
        },
    )
