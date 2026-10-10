"""S3(csv.gz) → Iceberg 적재 공통 상수 (데이터셋 무관)."""

import os

# SeaweedFS 호환 shim — 반드시 S3 클라이언트 생성 **전에** 적용되어야 한다.
#   최신 AWS SDK는 PutObject에 flexible checksum(CRC64NVME)을 기본 적용하며
#   본문을 `aws-chunked`로 감싼다. 이 프로젝트의 SeaweedFS는 이를 풀지 못해
#   **프레이밍 바이트를 객체 내용에 그대로 저장**한다(2026-08-18 실측:
#   Iceberg metadata.json이 `11\r\n{...}\r\n0\r\nx-amz-checksum-...`로 저장되어
#   pyiceberg가 JSON 파싱에 실패). 오류가 쓰기 시점이 아니라 **다음 읽기에서** 나므로
#   원인을 찾기 어렵다 → 기본값을 코드로 못 박는다.
#   env로 이미 지정했다면 존중한다(운영에서 상위 설정이 이기도록).
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")

# Iceberg JDBC 카탈로그 — Trino iceberg.properties의 catalog-name과 반드시 일치
CATALOG_NAME = "iceberg"
WAREHOUSE = os.environ.get("ICEBERG_WAREHOUSE", "s3://warehouse")

# 카탈로그 접속은 **환경마다 다르다** — compose(기본)와 K8s를 env로 전환한다.
#   compose : postgres:5432/iceberg_catalog, Dagster 메타 DB와 같은 계정
#   K8s     : catalog-postgres-rw:5432/iceberg, 전용 계정(Secret catalog-pg-app)
#             카탈로그 PG는 CloudNativePG가 관리 → 서비스명에 `-rw`(쓰기) 접미사
# 호스트에서 K8s를 대상으로 돌릴 땐 port-forward 주소를 넣는다(operations.md §1-2).
ICEBERG_CATALOG_HOST = os.environ.get("ICEBERG_CATALOG_HOST", "postgres")
ICEBERG_CATALOG_PORT = os.environ.get("ICEBERG_CATALOG_PORT", "5432")
ICEBERG_CATALOG_DB = os.environ.get("ICEBERG_CATALOG_DB", "iceberg_catalog")
# 계정은 별도 지정이 없으면 메타 DB 계정을 따른다(compose 기존 동작 보존).
ICEBERG_CATALOG_USER = (
    os.environ.get("ICEBERG_CATALOG_USER") or os.environ["POSTGRES_USER"]
)
ICEBERG_CATALOG_PASSWORD = (
    os.environ.get("ICEBERG_CATALOG_PASSWORD") or os.environ["POSTGRES_PASSWORD"]
)
ICEBERG_CATALOG_URI = (
    f"postgresql+psycopg2://{ICEBERG_CATALOG_USER}:{ICEBERG_CATALOG_PASSWORD}"
    f"@{ICEBERG_CATALOG_HOST}:{ICEBERG_CATALOG_PORT}/{ICEBERG_CATALOG_DB}"
)

# SeaweedFS(S3 호환) 엔드포인트 (scheme 포함)
S3_ENDPOINT = os.environ.get("ICEBERG_S3_ENDPOINT", "http://seaweedfs:8333")

# S3 접속 자격증명/리전 (env 참조 — S3Resource·pyiceberg 카탈로그 공용).
# 값은 코드에 하드코딩하지 않고 env에서 읽는다(12-Factor Config).
#
# 🔴 **엔드포인트와 자격증명은 한 쌍으로 움직인다.**
# 엔드포인트만 `ICEBERG_S3_ENDPOINT`로 바꾸고 키는 공용 `AWS_*`를 쓰면, 키가 SeaweedFS의
# `s3.json`(storage-up.sh가 `ICEBERG_S3_*`로 생성)과 어긋나 **카탈로그 나열은 되는데
# `load_table`에서 `ACCESS_DENIED`** 로 죽는다(2026-08-19 실측).
# 부분 성공이라 원인을 오해하기 쉬워 전용 키를 둔다(엔드포인트와 같은 접두어).
# 미설정이면 공용 `AWS_*`로 폴백해 compose 단독 구성의 기존 동작을 보존한다.
S3_ACCESS_KEY_ID = (
    os.environ.get("ICEBERG_S3_ACCESS_KEY") or os.environ["AWS_ACCESS_KEY_ID"]
)
S3_SECRET_ACCESS_KEY = (
    os.environ.get("ICEBERG_S3_SECRET_KEY") or os.environ["AWS_SECRET_ACCESS_KEY"]
)
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# Spark Connect 접속 (Iceberg 유지보수 프로시저 실행용)
# 카탈로그 설정·자격증명은 **서버 측**(k8s/spark/spark-connect-server.yaml)에 있어
# 여기엔 주소만 둔다(비밀 아님). 호스트 실행이 현행이라 port-forward 주소가 기본값이다.
#   kubectl port-forward svc/spark-connect 15002:15002
SPARK_REMOTE = os.environ.get("SPARK_REMOTE", "sc://localhost:15002")

# Trino 쿼리 엔진 접속 — 유지보수는 Spark로 이관했고(위 SPARK_REMOTE),
# 이 접속은 dbt-spark 이행 중 **방언 값 대조**용으로만 남긴다.
# compose `trino`는 `--profile legacy-sql`로만 뜨며 호스트 게시 포트는 8081이다.
# 컨테이너 내부망 값이라 비밀 아님 → 기본값 제공, 필요 시 env로 재정의.
TRINO_HOST = os.environ.get("TRINO_HOST", "trino")
TRINO_PORT = int(os.environ.get("TRINO_PORT", "8080"))
TRINO_USER = os.environ.get("TRINO_USER", "dagster")

# 적재 기본값
DEFAULT_CHUNK_ROWS = 1_000_000
DEFAULT_NAMESPACE = "bronze"
DEFAULT_GROUP_NAME = "bronze"
