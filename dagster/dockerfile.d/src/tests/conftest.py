"""단위 테스트 공통 설정.

`dagster_project.common.constants`는 **모듈 로드 시점**에 카탈로그 접속 문자열을
조립하며 `POSTGRES_USER`·`AWS_ACCESS_KEY_ID` 등을 env에서 읽는다. 값이 없으면
`KeyError`로 **수집 단계에서** 죽으므로, 테스트 모듈이 import되기 전에 더미
값을 넣는다(conftest는 테스트 수집보다 먼저 로드된다).

🔴 이 값들은 **접속에 쓰이지 않는다.** 여기 있는 테스트는 순수 함수(JSON/RDB →
Arrow)만 검증하고 S3·Iceberg·Postgres 어디에도 붙지 않는다. env가 필요한 이유는
접속이 아니라 **모듈 로드**다 — 둘을 혼동하면 "단위 테스트가 실인프라를 탄다"고
오해하게 되는데, `docs/test.md`의 격리 원칙은 그대로 지켜지고 있다.

`setdefault`를 쓰는 이유: 실제 환경변수가 이미 있으면 그것을 존중한다(로컬에서
`.env`를 켠 채 돌려도 값이 덮이지 않는다).
"""

import os

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
