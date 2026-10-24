"""Dagster 패키지 import용 더미 env(Dagster `tests/conftest.py`와 같은 이유).

`test_parity.py`가 Dagster 모듈을 import하면 모듈 로드 시점에 env를 읽는다.
🔴 접속에 쓰이지 않는 값이고, `setdefault`라 실제 환경변수가 있으면 존중한다.
"""

import os

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
