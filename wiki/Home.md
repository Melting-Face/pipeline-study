# pipeline-study 학습 노트

성격이 다른 여러 도메인의 데이터셋을 하나의 레이크하우스 패턴으로 적재·변환하고,
재현 가능한 분석 질문까지 연결하는 **학습·포트폴리오 프로젝트**다.

이 위키는 그 과정에서 **배운 것**을 정리한 곳이다.
잘 된 설계보다 **조용히 깨졌던 것**과 그걸 어떻게 알아챘는지를 주로 적는다.

> ⚠️ **이 위키는 저장소에서 자동 생성된다.**
> 원본은 [`wiki/`](https://github.com/Melting-Face/pipeline-study/tree/main/wiki)에 있고,
> `main`에 push되면 GitHub Actions가 이 위키로 단방향 미러한다.
> **웹에서 편집하면 다음 미러가 덮어쓴다** — 고칠 것이 있으면 저장소에 PR을 보내라.

## 학습 노트

| 노트 | 무엇을 배웠나 |
| --- | --- |
| [Dagster 오케스트레이션](dagster-orchestration.md) | 에셋 정의 방식의 선택과, 에러 없이 사라진 에셋들 |
| [엔진마다 값이 갈리는 SQL](cross-engine-sql.md) | 같은 SQL이 Trino와 Spark에서 다른 답을 낸다 |

## 이 프로젝트가 궁금하다면

문서의 정본은 **저장소**에 있다. 위키는 그것을 요약하지 않고 **별개의 글**을 둔다.

| | |
| --- | --- |
| 프로젝트 소개 | [README](https://github.com/Melting-Face/pipeline-study#readme) |
| 환경 세팅 절차 | [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) |
| 아키텍처·데이터 흐름 | [docs/architectures/](https://github.com/Melting-Face/pipeline-study/tree/main/docs/architectures) |
| 코딩 규칙 | [docs/conventions/](https://github.com/Melting-Face/pipeline-study/tree/main/docs/conventions) |

## 스택

```text
원천 파일 → Dagster 적재 → Iceberg bronze → dbt silver/gold → 노트북 → 리포트
```

저장소는 **Kubernetes(kind on Podman)** 위에서 돌고, 컴퓨트는 **Spark**·**Flink**가
같은 Iceberg JDBC 카탈로그를 공유한다. 오케스트레이터는 **Dagster**이고
in-cluster로 배포한다.
