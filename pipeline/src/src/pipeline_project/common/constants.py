"""S3(csv.gz) → Iceberg 적재 공통 상수 (데이터셋 무관 · 오케스트레이터 중립).

🔴 **env 참조는 전부 지연 평가다**(모듈 로드 시점에 읽지 않는다).
원본(`dagster_project/common/constants.py`)은 모듈 스코프에서 `os.environ[...]`을
직접 읽어, 값이 없으면 **import 한 번에 정의 로드 전체가 실패**했다. Dagster에서는
그것이 `dg check defs` 한 번의 실패로 끝났지만, Airflow에서는 dag-processor가
DAG 파일을 **주기적으로 재파싱**하므로 같은 `KeyError`가 **DAG 임포트 에러로 상시
반복**된다(웹 UI 배너·스케줄러 로그가 계속 더러워지고, 원인이 되는 값이 실제로
필요한 태스크는 그중 일부뿐이다).

⇒ 접속 파라미터는 **함수**로 감싼다. 호출 시점(= 태스크 실행 시점)에 읽으므로
크리덴셜이 없는 파서 프로세스에서도 DAG이 정상적으로 올라온다.

**`@lru_cache`를 쓰지 않는 이유**: 여기서 하는 일은 dict 조회와 문자열 조립뿐이라
캐시의 이득이 없고, 캐시를 걸면 테스트가 env를 바꿔도 **첫 호출 값이 박제**되는
staleness 축이 새로 생긴다. 캐시가 실익이 있는 지점은 무거운 객체를 만드는
`catalog.load_catalog()`이고 거기에만 건다.
"""

import os

# SeaweedFS 호환 shim — 반드시 S3 클라이언트 생성 **전에** 적용되어야 한다.
#   최신 AWS SDK는 PutObject에 flexible checksum(CRC64NVME)을 기본 적용하며
#   본문을 `aws-chunked`로 감싼다. 이 프로젝트의 SeaweedFS는 이를 풀지 못해
#   **프레이밍 바이트를 객체 내용에 그대로 저장**한다(2026-08-18 실측:
#   Iceberg metadata.json이 `11\r\n{...}\r\n0\r\nx-amz-checksum-...`로 저장되어
#   pyiceberg가 JSON 파싱에 실패). 오류가 쓰기 시점이 아니라 **다음 읽기에서** 나므로
#   원인을 찾기 어렵다 → 기본값을 코드로 못 박는다.
#   env로 이미 지정했다면 존중한다(운영에서 상위 설정이 이기도록).
#
# 🔴 **이 두 줄만 모듈 스코프에 남긴다**(위 지연 평가 원칙의 의도된 예외).
#    ⓐ `setdefault`는 값을 *쓰는* 쪽이라 미설정으로 실패할 수 없다(KeyError 축이 없다).
#    ⓑ 클라이언트가 만들어진 **뒤에** 적용하면 늦는다 — 함수로 감싸면 "언제 불리는가"가
#       호출부 규율에 달리고, 그 규율은 빠뜨려도 에러가 아니라 **조용한 객체 손상**이다.
#    S3 클라이언트·Iceberg 카탈로그를 만드는 경로는 모두 이 모듈의 함수를 거치므로
#    (`helper.build_s3_client`·`catalog.load_catalog`) import는 항상 선행한다.
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")

# Iceberg JDBC 카탈로그 이름 — 전 엔진(Spark·Flink·dbt)에서 동일해야 한다.
# JDBC 카탈로그는 `catalog_name`으로 레지스트리를 분할하므로, 이름이 다르면
# 같은 DB를 봐도 서로의 테이블이 보이지 않는다(에러 없이 깨지는 축).
CATALOG_NAME = "iceberg"

# 적재 기본값 (env와 무관한 순수 상수 → 지연 평가 대상이 아니다).
DEFAULT_CHUNK_ROWS = 1_000_000
DEFAULT_NAMESPACE = "bronze"


def _require_env(name: str, fallback: str) -> str:
    """전용 env를 읽고, 없으면 공용 env로 폴백한다(둘 다 없으면 실패).

    🔴 **빈 값을 「미설정」으로 함께 취급한다.** `os.environ.get`이 빈 문자열을
    돌려주는 경우(`KEY=`)를 통과시키면 자격증명이 빈 채로 접속을 시도해
    실패 지점이 여기서 한참 뒤로 밀린다(fail-closed).

    Args:
        name: 전용 환경변수 이름.
        fallback: 미설정 시 볼 공용 환경변수 이름.

    Returns:
        읽은 값.

    Raises:
        KeyError: 둘 다 미설정(또는 빈 값)일 때.
    """
    value = os.environ.get(name) or os.environ.get(fallback)
    if not value:
        message = f"환경변수 {name} 또는 {fallback} 중 하나가 필요하다(둘 다 미설정)"
        raise KeyError(message)
    return value


def warehouse() -> str:
    """Iceberg warehouse 루트(s3:// URI)."""
    return os.environ.get("ICEBERG_WAREHOUSE", "s3://warehouse")


def iceberg_catalog_uri() -> str:
    """Iceberg JDBC 카탈로그 접속 URI(sqlalchemy 형식).

    카탈로그 접속은 **환경마다 다르다** — compose(기본)와 K8s를 env로 전환한다.
      compose : postgres:5432/iceberg_catalog, 메타 DB와 같은 계정
      K8s     : catalog-postgres-rw:5432/iceberg, 전용 계정(Secret catalog-pg-app)
                카탈로그 PG는 CloudNativePG가 관리 → 서비스명에 `-rw`(쓰기) 접미사
    호스트에서 K8s를 대상으로 돌릴 땐 port-forward 주소를 넣는다(operations.md §1-2).

    🔴 **반환값에 비밀번호가 들어 있다 — 로그·메타데이터에 싣지 않는다.**

    Returns:
        `postgresql+psycopg2://...` 형식의 접속 URI.
    """
    host = os.environ.get("ICEBERG_CATALOG_HOST", "postgres")
    port = os.environ.get("ICEBERG_CATALOG_PORT", "5432")
    database = os.environ.get("ICEBERG_CATALOG_DB", "iceberg_catalog")
    # 계정은 별도 지정이 없으면 메타 DB 계정을 따른다(compose 기존 동작 보존).
    user = _require_env("ICEBERG_CATALOG_USER", "POSTGRES_USER")
    password = _require_env("ICEBERG_CATALOG_PASSWORD", "POSTGRES_PASSWORD")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"


def s3_endpoint() -> str:
    """SeaweedFS(S3 호환) 엔드포인트 (scheme 포함)."""
    return os.environ.get("ICEBERG_S3_ENDPOINT", "http://seaweedfs:8333")


def s3_access_key_id() -> str:
    """S3 access key.

    🔴 **엔드포인트와 자격증명은 한 쌍으로 움직인다.**
    엔드포인트만 `ICEBERG_S3_ENDPOINT`로 바꾸고 키는 공용 `AWS_*`를 쓰면,
    compose SeaweedFS와 K8s SeaweedFS의 키가 달라 **카탈로그 나열은 되는데
    `load_table`에서 `ACCESS_DENIED`** 로 죽는다(2026-08-19 실측).
    부분 성공이라 원인을 오해하기 쉬워 전용 키를 둔다(엔드포인트와 같은 접두어).
    미설정이면 공용 `AWS_*`로 폴백해 compose 단독 구성의 기존 동작을 보존한다.
    """
    return _require_env("ICEBERG_S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID")


def s3_secret_access_key() -> str:
    """S3 secret key (근거는 `s3_access_key_id` 독스트링)."""
    return _require_env("ICEBERG_S3_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")


def aws_region() -> str:
    """S3 리전(SeaweedFS는 무의미하나 SDK가 요구한다)."""
    return os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def spark_remote() -> str:
    """Spark Connect 접속 주소 (Iceberg 유지보수 프로시저 실행용).

    카탈로그 설정·자격증명은 **서버 측**(k8s/spark/spark-connect-server.yaml)에 있어
    여기엔 주소만 둔다(비밀 아님). 호스트 실행이 현행이라 port-forward가 기본값이다.
      kubectl port-forward svc/spark-connect 15002:15002

    ⚠️ **P0 시점에 이 값의 호출부는 아직 없다.** 유지보수·dbt 경로(단계 11)가
    쓰며, Spark는 제거 대상이 아니라 현행 엔진이므로 함께 이식한다.
    """
    return os.environ.get("SPARK_REMOTE", "sc://localhost:15002")
