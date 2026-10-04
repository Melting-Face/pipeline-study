# 오케스트레이터 중립 코드 이미지 — 신 경로(`pipeline/`)용.
# 구 Dagster 이미지(구 트리의 `dockerfile.d/Dockerfile`)는 **그대로 돌고 있는 정본**이고,
# 이 파일은 공존 기간 동안 **병렬로 추가**된 것이다(이동이 아니다).
# ⚠️ 구 경로 문자열(`dagster/` + `dockerfile.d`)을 주석에 적지 않는다 — 이관 게이트가 그
#    문자열의 **출현 횟수**를 세므로, 설명용 언급도 "지운 것 없음" 판정을 흐린다.
#
# 🔴 **빌드 컨텍스트는 `pipeline/`이고 `pipeline/dockerfile.d/`가 아니다.**
#   구 레이아웃은 코드가 컨텍스트 *안*(구 트리의 `dockerfile.d/src/`)에 있었지만, 신 레이아웃의
#   파이썬 루트 `pipeline/src/`는 이 디렉터리의 **형제**다. 이 배치는 임의가 아니라
#   `scripts/worker_boundaries.py` §devops-engineer 가 요구한다 — `src/` 한 겹만 deny 하면
#   Dockerfile(데브옵스 소유)과 파이썬 소스(데이터 엔지니어 소유)가 자연히 갈린다.
#   컨텍스트 **밖은 COPY할 수 없으므로** 한 단 위가 컨텍스트 루트가 된다:
#     podman build -f pipeline/dockerfile.d/Dockerfile -t pipeline:dev pipeline/
#   ⇒ 그래서 `.dockerignore`도 **`pipeline/.dockerignore`** 에 둔다(엔진은 `.dockerignore`를
#     **컨텍스트 루트**에서 읽는다). `pipeline/dockerfile.d/.dockerignore`에 두면 조용히
#     **죽은 규칙**이 된다 — 구 `.dockerignore` 머리 주석이 기록한 그 실패(3.64GB)와 같은 축이다.
#
# 🔴 베이스 3.12 — **구 파일의 사유는 이 트리에 전이되지 않는다.**
#   구 파일이 3.12에 묶인 이유는 `pyspark[connect]<3.6` → `numpy<2` → numpy 1.26.x에 cp313
#   휠이 없음이었다. `pipeline/src/pyproject.toml`에는 **pyspark 선언이 없으므로** 그 제약은
#   여기 없다. 그래도 3.12로 두는 이유는 ①CI `lint` 잡과 루트 venv가 3.12 ②오케스트레이터
#   (Airflow) 버전이 미확정이라 지원 Python 매트릭스를 1차 출처로 확인하기 전에 베이스를
#   올리면 근거 없는 상향이 된다는 것뿐이다.
#   ⚠️ `pipeline/src/.venv`는 현재 **3.13.9**다(uv가 상한 없이 최신을 골랐다 — pyvenv.cfg 실측).
#      즉 지금은 이미지 3.12 ↔ 로컬 3.13 **드리프트 상태**이고, 해소는 TODO(P0-f)다.
FROM python:3.12-slim

# HOME을 잡는 이유는 구 파일과 같다: `USER 1000`으로 내려가면 기본 HOME(/root)에 쓸 수 없는데
# dbt는 `~/.dbt`(사용자 설정·익명 사용 파일)에 쓰려 한다.
# 🔴 `DAGSTER_HOME`은 **옮기지 않았다** — 특정 오케스트레이터의 인스턴스 홈 규약이다.
#    TODO(P0-f): Airflow 채택 확정 시 `AIRFLOW_HOME`을 같은 경로로 선언한다.
ENV HOME=/opt/pipeline

WORKDIR /opt/pipeline

COPY src/ /opt/pipeline/

# 🔴 `-e .`이지 `-e ".[dev]"`가 아니다. `[dev]`는 `[project.optional-dependencies]`가 아니라
#   PEP 735 `[dependency-groups]`라 extras가 아니고, pip은 이걸 에러가 아니라
#   `WARNING: … does not provide the extra 'dev'`로 **흘려보낸다**(구 트리 빌드 로그에서 실재
#   확인). 런타임 이미지에 dev 도구(pytest·ruff·mypy)를 넣을 이유도 없다.
#   근거: docs/conventions/docker.md §2
RUN pip install --no-cache-dir --compile --prefer-binary -e .

# dbt 패키지 설치 + manifest 사전 생성.
# 🔴 **이 단계는 유지 대상이다** — 사유가 오케스트레이터와 무관하다. 런타임에 dbt를 부르지
#   않으려고 빌드 시점에 `target/manifest.json`을 굳히는 것이고(parse는 DB 미접속), 그 필요는
#   Dagster든 Airflow든 같다.
# ⚠️ 지금은 **비활성**이다 — `dbt_pipelines/`가 아직 `pipeline/` 안에 없다(이관은 단계 11의
#   `git mv`). 없는 경로를 참조하면 빌드가 통째로 깨지므로 자리만 남긴다.
#   `cd`를 단일 `RUN` 안에 두는 형태를 유지한다(`WORKDIR`로 바꾸면 ENTRYPOINT가 도는 CWD가
#   함께 바뀐다 — `.hadolint.yaml`이 DL3003을 무시하는 사유가 이것이다).
# TODO(단계 11, dbt_pipelines/ 이관 직후 아래 한 줄을 되살린다):
# RUN cd dbt_pipelines && dbt deps && dbt parse --profiles-dir .

# `USER` 전환 **전에** 소유권을 넘긴다(docs/conventions/docker.md §2).
# 런타임에 쓰기가 일어나는 경로: dbt_pipelines/{target,logs}/ · $HOME/.dbt
RUN chown -R 1000:1000 /opt/pipeline

USER 1000

# 🔴 `ENTRYPOINT`를 두지 않았다 — 구 파일의 `dg`는 Dagster CLI다.
#   TODO(P0-f): 오케스트레이터 확정 후 진입점을 선언한다. 그때까지는 베이스 이미지의
#   기본 CMD(`python3`)가 남으며, 이 이미지는 **빌드·린트 대상**으로서만 의미가 있다.
