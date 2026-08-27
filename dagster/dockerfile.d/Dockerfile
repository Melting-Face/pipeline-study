# Dagster 코드 이미지 — compose와 K8s(`k8s/dagster/`)가 **같은 이미지**를 쓴다.
#
# 🔴 베이스는 **3.12**여야 한다(2026-08-27 실측으로 3.13에서 되돌림).
#   `pyspark[connect]<3.6`이 `numpy<2`를 요구하는데 numpy 1.26.4에는 **cp313 휠이 없다**
#   (numpy 1.26.x는 Python 3.13 출시 이전 릴리스다). 그래서 3.13에서는 소스 빌드로 떨어지고
#   `-slim`에 컴파일러가 없어 `ERROR: Unknown compiler(s)`로 **빌드가 통째로 실패**했다.
#   호스트 venv도 이미 3.12.12이므로, 3.12는 임시 회피가 아니라 **런타임 정합을 되돌린 것**이다.
#   올리려면 pyspark의 numpy 상한이 먼저 풀려야 한다 — 베이스만 올리면 다시 깨진다.
FROM python:3.12-slim

# HOME을 함께 잡는 이유: `USER 1000`으로 내려가면 기본 HOME(/root)에 쓸 수 없는데
# dbt는 `~/.dbt`(사용자 설정·익명 사용 파일)에 쓰려 한다.
ENV DAGSTER_HOME=/opt/dagster/dagster_home/ \
    HOME=/opt/dagster/dagster_home/

WORKDIR $DAGSTER_HOME

COPY src/ $DAGSTER_HOME/

# 🔴 `-e .`이지 `-e ".[dev]"`가 아니다. `[dev]`는 `[project.optional-dependencies]`가 아니라
#   PEP 735 `[dependency-groups]`라 extras가 아니고, pip은 이걸 에러가 아니라
#   `WARNING: dagster-project 0.1.0 does not provide the extra 'dev'`로 **흘려보낸다**
#   (2026-08-27 빌드 로그에서 실재 확인). 즉 지금까지 dev 그룹은 **한 번도 설치된 적이 없다**.
#   떼도 손실이 없다 — `dagster-webserver`는 이미 `[project].dependencies`에 핀돼 있고,
#   나머지(pytest·ruff·sqlfluff·gitlint·yamllint·mypy)는 런타임 이미지에 있으면 안 되는 것들이다.
RUN pip install --no-cache-dir --compile --prefer-binary -e .

# dbt 패키지 설치 + manifest 사전 생성.
# webserver/daemon은 비-dev라 prepare_if_dev()가 no-op → @dbt_assets 로드에
# target/manifest.json이 필요하므로 빌드 단계에서 미리 생성한다(parse는 DB 미접속).
RUN cd dbt_pipelines && dbt deps && dbt parse --profiles-dir .

# `USER` 전환 **전에** 소유권을 넘긴다(conventions/docker.md §2).
# 런타임에 쓰기가 일어나는 경로: storage/ · .telemetry/ · dbt_pipelines/{target,logs}/ · $HOME/.dbt
RUN chown -R 1000:1000 /opt/dagster/dagster_home

USER 1000

ENTRYPOINT ["dg"]
