"""PoC: Spark → Iceberg(JDBC 카탈로그, SeaweedFS S3) write/read.

성공 게이트 검증용 최소 잡. 카탈로그·S3 접속 정보는 환경변수로 주입받는다
(크리덴셜 비노출 — SparkApplication이 Secret에서 env로 전달).
Phase 2에서 이 자리를 실제 대용량 CSV.gz 적재로 대체한다.
"""

import hashlib
import os
import random
from typing import Any

from pyspark.sql import SparkSession

# 카탈로그 이름은 **모든 엔진이 같아야 한다**. Iceberg JDBC 카탈로그는 catalog_name으로
# 네임스페이스·테이블 레지스트리를 분할하므로, 이름이 다르면 같은 DB를 봐도
# 서로의 테이블이 보이지 않는다
# (2026-08-18 실측: Spark=jdbccat vs Dagster=iceberg로 갈려 있었다).
# 정본은 `iceberg` — Trino iceberg.properties·Dagster common/constants.py와 일치시킨다.
CATALOG = os.environ.get("ICEBERG_CATALOG_NAME", "iceberg")
# 엔진 버전 이행 검증은 기존 테이블이 아니라 **전용 네임스페이스**에 쓴다
# (상위 버전이 쓴 메타데이터를 하위 버전이 읽을 수 있는지는 확인되지 않았다).
# 기본값을 두지 않는다 — env가 빠졌을 때 기존 테이블을 덮지 않고 KeyError로 멈춘다.
NAMESPACE = os.environ["POC_NAMESPACE"]
TABLE = f"{CATALOG}.{NAMESPACE}.sample"
# 설정 시에만 S3A 쓰기·재독 무결성 프로브를 돈다
# (예: s3a://warehouse/<ns>/_s3a_probe/probe.bin).
S3A_PROBE_URI = os.environ.get("POC_S3A_PROBE_URI")
VERSION_INFO = "software.amazon.awssdk.core.util.VersionInfo"
# SeaweedFS가 aws-chunked 프레이밍을 풀지 못하면 객체 앞에 `<hex>\r\n`,
# 뒤에 `x-amz-checksum-…`이 남는다(docs/conventions/k8s/checksum.md §1).
CHUNK_TRAILER = b"x-amz-checksum"


def build_spark() -> SparkSession:
    """Iceberg JDBC 카탈로그 + S3FileIO(SeaweedFS, path-style) 세션을 만든다."""
    prefix = f"spark.sql.catalog.{CATALOG}"
    return (
        SparkSession.builder.appName("poc-ingest")
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config(prefix, "org.apache.iceberg.spark.SparkCatalog")
        .config(f"{prefix}.catalog-impl", "org.apache.iceberg.jdbc.JdbcCatalog")
        .config(f"{prefix}.uri", os.environ["ICEBERG_JDBC_URI"])
        .config(f"{prefix}.jdbc.user", os.environ["PG_USER"])
        .config(f"{prefix}.jdbc.password", os.environ["PG_PASSWORD"])
        .config(f"{prefix}.warehouse", os.environ["ICEBERG_WAREHOUSE"])
        .config(f"{prefix}.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
        .config(f"{prefix}.s3.endpoint", os.environ["S3_ENDPOINT"])
        .config(f"{prefix}.s3.path-style-access", "true")
        .config(f"{prefix}.s3.access-key-id", os.environ["S3_ACCESS_KEY"])
        .config(f"{prefix}.s3.secret-access-key", os.environ["S3_SECRET_KEY"])
        .getOrCreate()
    )


def class_origin(jvm: Any, class_name: str) -> str:
    """클래스가 실제로 로드된 jar 경로(CodeSource)."""
    domain = jvm.java.lang.Class.forName(class_name).getProtectionDomain()
    source = domain.getCodeSource()
    return source.getLocation().toString() if source else "?"


def s3a_filesystem(spark: SparkSession, uri: str) -> tuple[Any, Any]:
    """잡 env로 S3A를 구성해 (Path, FileSystem)을 연다.

    이 잡은 `spark.hadoop.fs.s3a.*`를 sparkConf로 받지 않으므로 여기서 채운다.
    """
    jvm = spark.sparkContext._jvm
    conf = spark.sparkContext._jsc.hadoopConfiguration()
    conf.set("fs.s3.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    conf.set("fs.s3a.endpoint", os.environ["S3_ENDPOINT"])
    conf.set("fs.s3a.endpoint.region", os.environ.get("AWS_REGION", "us-east-1"))
    conf.set("fs.s3a.path.style.access", "true")
    conf.set("fs.s3a.connection.ssl.enabled", "false")
    conf.set("fs.s3a.access.key", os.environ["S3_ACCESS_KEY"])
    conf.set("fs.s3a.secret.key", os.environ["S3_SECRET_KEY"])
    path = jvm.org.apache.hadoop.fs.Path(uri)
    return path, path.getFileSystem(conf)


def read_raw(spark: SparkSession, uri: str) -> bytes:
    """저장된 바이트를 그대로 읽는다(S3A). 프레이밍째 저장됐다면 그대로 돌아온다."""
    jvm = spark.sparkContext._jvm
    path, fs = s3a_filesystem(spark, uri.replace("s3://", "s3a://", 1))
    stream = fs.open(path)
    try:
        return bytes(jvm.org.apache.commons.io.IOUtils.toByteArray(stream))
    finally:
        stream.close()


def probe_s3a_roundtrip(spark: SparkSession, uri: str) -> None:
    """S3A로 고정 바이트를 쓰고 다시 읽어 sha256을 대조한다.

    rename이 없는 단일 객체 쓰기라 FileOutputCommitter 제약(k8s.md)과 무관하다.
    """
    # 무결성 대조용 결정적 바이트(보안 난수 불필요)
    payload = random.Random(41).randbytes(1024 * 1024)  # noqa: S311
    expected = hashlib.sha256(payload).hexdigest()
    path, fs = s3a_filesystem(spark, uri)
    client = fs.getS3AInternals().getAmazonS3Client("poc").getClass().getName()
    origin = class_origin(spark.sparkContext._jvm, client)
    print(f"[poc] s3a-client from={origin}", flush=True)
    out = fs.create(path, True)
    out.write(bytearray(payload))
    out.close()
    actual = hashlib.sha256(read_raw(spark, uri)).hexdigest()
    verdict = "OK" if actual == expected else "CORRUPT"
    print(f"[poc] s3a-probe {verdict} expected={expected} actual={actual}", flush=True)


def check_s3fileio_objects(spark: SparkSession) -> None:
    """S3FileIO가 쓴 객체(현 메타데이터·데이터 파일)에 프레이밍이 없는지 본다."""
    # TABLE은 상수라 인젝션 아님
    meta_sql = f"SELECT file FROM {TABLE}.metadata_log_entries ORDER BY timestamp DESC"  # noqa: S608
    files_sql = f"SELECT file_path FROM {TABLE}.files"  # noqa: S608
    meta = spark.sql(meta_sql).collect()[0]["file"]
    files = [r["file_path"] for r in spark.sql(files_sql).collect()]
    for uri in [meta, *files]:
        raw = read_raw(spark, uri)
        if uri.endswith(".json"):
            ok = raw[:1] == b"{"
        else:
            ok = raw[:4] == b"PAR1" and raw[-4:] == b"PAR1"
        ok = ok and CHUNK_TRAILER not in raw
        digest = hashlib.sha256(raw).hexdigest()
        print(f"[poc] s3fileio ok={ok} n={len(raw)} sha256={digest} {uri}", flush=True)


def main() -> None:
    """샘플 테이블을 Iceberg에 write하고 read-back으로 검증한다."""
    spark = build_spark()

    # S3A·S3FileIO가 어느 SDK v2로 도는지 매 실행 남긴다
    # (판본 경계 2.30.0 — docs/conventions/k8s/checksum.md §2-1).
    jvm = spark.sparkContext._jvm
    sdk_version = jvm.software.amazon.awssdk.core.util.VersionInfo.SDK_VERSION
    origin = class_origin(jvm, VERSION_INFO)
    print(f"[poc] sdk-v2={sdk_version} from={origin}", flush=True)

    # 네임스페이스 + 샘플 테이블 write(createOrReplace로 멱등)
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {CATALOG}.{NAMESPACE}")
    rows = [(1, "alice"), (2, "bob"), (3, "carol")]
    df = spark.createDataFrame(rows, "id int, name string")
    df.writeTo(TABLE).createOrReplace()

    # read-back 검증 (TABLE은 상수라 인젝션 아님)
    count = spark.sql(f"SELECT count(*) AS c FROM {TABLE}").collect()[0]["c"]  # noqa: S608
    print(f"[poc] wrote table={TABLE} rows={count}", flush=True)
    spark.sql(f"SELECT * FROM {TABLE} ORDER BY id").show(truncate=False)  # noqa: S608

    if S3A_PROBE_URI:
        probe_s3a_roundtrip(spark, S3A_PROBE_URI)
        check_s3fileio_objects(spark)

    spark.stop()


if __name__ == "__main__":
    main()
