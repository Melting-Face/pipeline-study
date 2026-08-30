"""Flink 배치 잡 자산 상수 — CRD 좌표·매니페스트 경로·파드 내 SQL 경로.

값은 환경변수로 override 가능하다(호스트 실행 유연성). `defs/poc/constants.py`와
같은 구조이며, 아래 경로 계산의 함정도 같다.
"""

import os
from pathlib import Path

# Flink Kubernetes Operator CRD 좌표.
# 🔴 버전은 추측하지 않고 클러스터에서 읽었다(docs/conventions/k8s.md §9) —
#   2026-08-30 실측: `flinkdeployments.flink.apache.org`는 **v1beta1 단일**이고
#   served·storage 둘 다 true다. Spark 쪽(`v1beta1`+`v1` 병존)과 달리 선택지가 없다.
CRD_GROUP = "flink.apache.org"
CRD_VERSION = "v1beta1"
CRD_PLURAL = "flinkdeployments"

# 호스트 → kind 접속 컨텍스트·네임스페이스
KUBE_CONTEXT = os.environ.get("FLINK_KUBE_CONTEXT", "kind-lakehouse")
NAMESPACE = os.environ.get("FLINK_NAMESPACE", "default")

# 커밋된 FlinkDeployment 매니페스트(단일 출처). 환경변수로 override 가능.
#
# 🔴 이 계산은 **호스트 실행 전용**이다.
#   `defs/poc/constants.py`와 완전히 같은 함정이다.
#   호스트: .../dagster/dockerfile.d/src/src/dagster_project/defs/flink/constants.py
#           → parents[7] = 레포 루트 ✅
#   이미지: `COPY src/ $DAGSTER_HOME/`이 바깥 src/ 한 겹을 벗겨내 parents[7] = "/" ❌
#   ⇒ in-cluster에서는 `FLINK_SESSION_MANIFEST` override가 **필수**이며,
#      매니페스트 실체는 ConfigMap으로 마운트해야 한다
#      (`spark-app-manifests`와 같은 방식).
#      배선이 없으면 자산은 파일 부재로 죽는다 — 조용히 통과하지 않는다.
_DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[7]
    / "k8s"
    / "flink"
    / "flinkdeployment-session.yaml"
)
SESSION_MANIFEST = os.environ.get("FLINK_SESSION_MANIFEST", str(_DEFAULT_MANIFEST))

# 세션 클러스터가 참조하는 SQL ConfigMap 2종.
# 🔴 **둘 다 먼저 존재해야 JM 파드가 뜬다** — FlinkDeployment의 podTemplate이 둘을
#   volume으로 참조하므로, 하나라도 없으면
#   `CreateContainerConfigError`로 기동하지 않는다.
#   스트림 잡을 쓰지 않아도 마운트 대상이라 **함께** 확인한다.
REQUIRED_CONFIGMAPS = ("iceberg-batch-job", "iceberg-stream-job")

# 파드 안의 SQL 파일 경로(ConfigMap 마운트 지점).
# 배치=/opt/flink/sql, 스트림=/opt/flink/sql-stream 으로 **경로가 갈린다** —
# 한 경로에 ConfigMap 2개를 겹쳐 마운트하면 뒤엣것이 앞엣것을 통째로 가리기 때문이다.
SQL_INIT_PATH = "/opt/flink/sql/01-catalog.sql"
SQL_JOB_PATH = "/opt/flink/sql/02-batch-job.sql"

# JobManager 파드를 찾는 라벨. 오퍼레이터가 붙이는 표준 라벨이다.
JM_SELECTOR = "app={name},component=jobmanager"

# 오퍼레이터가 만드는 JM 컨테이너 이름(파드에 컨테이너가 여럿일 때 exec 대상 지정용).
JM_CONTAINER = "flink-main-container"

# `01-catalog.sql`이 요구하는 env 전수. 파드 안에서 `envsubst`가 치환한다.
#
# 🔴 이 목록은 **직접 세어 만든 것이 아니라 실패를 겪고 만든 것**이다. 처음에
#   `grep -o '\${[A-Z_]*}'`로 뽑았더니 문자 클래스에 숫자가 없어 **`${S3_ENDPOINT}`가
#   `S3`에서 끊겨 누락**됐다(2026-08-30). 목록은 깨끗해 보였고 검산을 통과했을 것이다.
#   `envsubst`는 미설정 변수를 **빈 문자열로 조용히 치환**하므로, 빠뜨렸다면 DDL이
#   `'s3.endpoint' = ''`가 되어 진단이 어려운 실패로 나타났다.
#   ⇒ SQL 파일을 고치면 이 목록을 **숫자를 포함한 패턴**으로 다시 뽑는다.
REQUIRED_SQL_ENV = (
    "ICEBERG_JDBC_URI",
    "ICEBERG_WAREHOUSE",
    "PG_PASSWORD",
    "PG_USER",
    "S3_ENDPOINT",
)

# 크리덴셜을 담는 env 이름. 마스킹 대상이며 파드 안에서만 다뤄진다.
# ruff S105는 이 값을 비밀번호로 오인한다 — **비밀번호가 아니라 env 이름**이고,
# 실제 값은 파드의 Secret→env로만 존재해 이 저장소 어디에도 없다.
SECRET_ENV = "PG_PASSWORD"  # noqa: S105
