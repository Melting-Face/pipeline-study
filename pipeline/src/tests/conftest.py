"""단위 테스트 공통 설정 — **의도적으로 비어 있다**.

원본(`dagster/dockerfile.d/src/tests/conftest.py`)에는 더미 env `setdefault`가
4개 있었다(`POSTGRES_USER`·`POSTGRES_PASSWORD`·`AWS_ACCESS_KEY_ID`·
`AWS_SECRET_ACCESS_KEY`). 이유는 접속이 아니라 **모듈 로드**였다 —
`common/constants.py`가 모듈 스코프에서 그 값들을 읽어, 없으면 테스트 *수집*
단계에서 `KeyError`로 죽었다.

🔴 **그 필요가 지연 평가 전환으로 소멸했다.** 이 트리의 `constants.py`는 env를
함수 안에서 읽고, 여기 있는 테스트는 그 함수를 호출하는 경로를 타지 않는다:
  · `test_physionet_fetch.py`        — S3·HTTP 대역만 쓴다(클라이언트를 만들지 않는다)
  · `test_frankfurter_fx_partition_load.py` — `catalog.load_catalog`를 monkeypatch해
    로컬 `SqlCatalog`로 갈아끼운다(카탈로그 properties를 만들지 않는다)

⇒ 남길 `setdefault`는 **0개**다. 실측으로 확인했다(이 파일을 비운 상태에서
`pytest`가 수집·통과). 빈 파일을 남기는 이유는 **왜 비었는지**를 여기 적어
누군가 "빠뜨렸다"고 판단해 되살리는 것을 막기 위한 것이다 — 더미 자격증명이
다시 들어오면 *진짜 접속이 필요해진 테스트*가 조용히 통과할 수 있다.

🔴 **여기 있는 테스트는 실인프라(SeaweedFS·Trino·Spark·Postgres)에 붙지 않는다.**
"""
