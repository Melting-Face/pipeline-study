"""PoC 자산 상수 — Spark Operator CRD 좌표·매니페스트 경로.

값은 환경변수로 override 가능(호스트 실행 유연성). 매니페스트 기본 경로는
레포 루트 기준으로 계산한다(이 모듈 위치에서 7단계 상위).
"""

import os
from pathlib import Path

# Apache Spark K8s Operator CRD 좌표 (Kubeflow `sparkoperator.k8s.io/v1beta2` 아님).
# 버전은 실측 기준 `v1` — CRD가 v1beta1(served)·v1(served+storage)을 함께 내고
# storedVersions=["v1"]이라 정본이 v1이다. 규칙: docs/conventions/k8s.md §9.
CRD_GROUP = "spark.apache.org"
CRD_VERSION = "v1"
CRD_PLURAL = "sparkapplications"

# 호스트 → kind 접속 컨텍스트·네임스페이스
KUBE_CONTEXT = os.environ.get("POC_KUBE_CONTEXT", "kind-lakehouse")
NAMESPACE = os.environ.get("POC_NAMESPACE", "default")

# 커밋된 SparkApplication 매니페스트(단일 출처). 환경변수로 override 가능.
#
# 🔴 이 계산은 **호스트 실행 전용**이다. 이미지에서는 반드시 깨진다 —
#   호스트: .../dagster/dockerfile.d/src/src/dagster_project/defs/poc/constants.py
#           → parents[7] = 레포 루트 ✅
#   이미지: /opt/dagster/dagster_home/src/dagster_project/defs/poc/constants.py
#           → `COPY src/ $DAGSTER_HOME/`이 바깥 src/ 한 겹을 벗겨내 parents[7] = "/" ❌
#   그래서 in-cluster에서는 `POC_SPARKAPP_MANIFEST` override가 **필수**다
#   (k8s/dagster/dagster-deploy.yaml의 ConfigMap이 ConfigMap 마운트 경로를 준다).
#   ⚠️ 같은 축의 common/dbt.py `parents[3]`은 우연히 양쪽에서 맞는다 —
#      "경로 계산은 다 깨진다"도 "다 괜찮다"도 아니다. 단계 수마다 따로 확인해야 한다.
_DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[7] / "k8s" / "spark" / "sparkapplication-poc.yaml"
)
SPARKAPP_MANIFEST = os.environ.get("POC_SPARKAPP_MANIFEST", str(_DEFAULT_MANIFEST))
