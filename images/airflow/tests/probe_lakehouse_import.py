"""이미지 안에서 DAG 파일이 공용 패키지 `lakehouse`를 import할 수 있는지 본다.

Airflow 3의 DagBag은 DAG 폴더를 sys.path에 넣지 않는다(실측). Dockerfile의
`ENV PYTHONPATH=/opt/airflow/dags`가 받치며, 그 줄이 빠지면 이 프로브가 실패한다.
임시 폴더에 DAG 하나를 만들어 파싱하고 결과를 JSON 한 줄로 출력한다.
"""

import json
import sys
import tempfile
from pathlib import Path

from airflow.models.dagbag import DagBag

PROBE_DAG = """\
from lakehouse.iceberg import CATALOG_NAME
from airflow.sdk import dag


@dag(schedule=None)
def probe_lakehouse():
    pass


probe_lakehouse()
"""

with tempfile.TemporaryDirectory() as folder:
    Path(folder, "probe_dag.py").write_text(PROBE_DAG)
    bag = DagBag(dag_folder=folder, include_examples=False)
    result = {"errors": len(bag.import_errors), "ok": "probe_lakehouse" in bag.dag_ids}
    sys.stdout.write(json.dumps(result) + "\n")
