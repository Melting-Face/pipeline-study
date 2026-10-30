# pipeline-study 기술별 스터디 장

성격이 다른 여러 도메인의 데이터셋을 하나의 레이크하우스 패턴으로 적재·변환하고,
재현 가능한 분석 질문까지 연결하는 **학습·포트폴리오 프로젝트**다.

이 위키는 그 과정에서 **배운 것**을 정리한 곳이다.
잘 된 설계보다 **에러 없이 깨진 것**과 **그럴듯해서 속은 것**,
그리고 그걸 어떻게 알아챘는지를 주로 적는다.

> ⚠️ **이 위키는 저장소에서 자동 생성된다.**
> 원본은 [`wiki/`](https://github.com/Melting-Face/pipeline-study/tree/main/wiki)에 있고,
> `main`에 push되면 GitHub Actions가 이 위키로 단방향 미러한다.
> **웹에서 편집하면 다음 미러가 덮어쓴다** — 고칠 것이 있으면 저장소에 PR을 보내라.

## 먼저 읽을 것

| | |
| --- | --- |
| [이 위키를 읽는 법](how-to-read.md) | 이 프로젝트가 무엇인지, 반복해서 쓰는 낱말 여섯 개 |

## 기술별 스터디 장

**장 하나 = 한 기술**이다. 장은 개념 → 이 저장소에서의 쓰임 → 실습 → 고장 대응 → 함정,
5절 템플릿을 같은 이름·순서로 따른다. 하위 쪽은 템플릿 없이 상위 장의 「함정」에서 이어진다.

- **파이프라인**
    - [Dagster 오케스트레이션](dagster-orchestration.md) — 에셋 정의 방식의 선택과, 에러 없이 사라진 에셋들
        - [Dagster 파티션](dagster-partitions.md) — 날짜를 키로 삼으면 네 군데서 따로 어긋난다
    - [엔진마다 값이 갈리는 SQL](cross-engine-sql.md)
        — 같은 SQL이 Trino와 Spark에서 다른 답을 낸다 — 그리고 같은 자리에서 두 번 틀렸다
    - [Airflow를 클러스터 안에 두며](airflow-on-k8s.md)
        — Airflow는 ArgoCD 앱 하나이고, 로직은 이미지 안의 순수 패키지, DAG는 배선만 한다
- **컴퓨트**
    - [Spark를 K8s 위로](spark-on-k8s.md) — 파드가 뜬 것과 계산이 거기서 도는 것은 다르다
    - [Flink를 K8s 위에](flink-on-k8s.md) — 막힌 층이 다르면 같은 처방이 안 듣는다
        - [Flink 함정](flink-on-k8s-pitfalls.md)
            — 고장 대응과 회고 — 권한·경로·버전이 각각 두 층에서 판정됐다
        - [스트림 소스를 고르며](streaming-source-choice.md) — 질문을 되물었더니 설계 절반이 사라졌다
- **플랫폼**
    - [빈 클러스터를 세우며](k8s-bootstrap.md) — 순서를 정하는 것은 의존이 아니라 소유와 해석 시점이다
    - [오퍼레이터를 둘 것인가](k8s-operators.md) — 맡기면 선언은 짧아지고 관측은 한 칸 멀어진다
    - [ArgoCD로 클러스터를 수렴시키며](argocd-bootstrap.md)
        — 한 리소스는 한 주인만 갖는다 — Terraform은 ArgoCD까지, 그 위는 Git이 소유한다
    - [Terraform 스택을 둘로 가르며](terraform-stacks.md)
        — 스택은 의존이 아니라 폭발반경으로 가르고, state는 apply한 트리에 산다
    - [Iceberg JDBC 카탈로그를 Postgres에 둘 때](iceberg-catalog.md) — 카탈로그의 수명이 곧 테이블의 수명이다
    - [클러스터 밖에 S3를 두기](seaweedfs-s3.md)
        — 스토리지는 클러스터와 수명이 달라야 하고, 호환 스토어의 결함은 나중의 읽기에서 드러난다
    - [클러스터 안 Prometheus 하나](prometheus-monitoring.md)
        — 관측은 타깃, 수집기, 규칙 셋이 이어져야 성립하고, 초록 화면은 그 증거가 아니다
- **방법론**
    - [이 위키를 읽는 법](how-to-read.md) — 이 프로젝트가 무엇인지, 반복해서 쓰는 낱말 여섯 개
        - [kubectl로 판정하기](kubectl-judgement.md) — 없음·정상·통과는 셋 다 「안 봤다」와 모양이 같다
    - [Claude Code CLI](claude-code-cli.md) — 세션은 누적만 한다 — 비용은 요청 수 × 컨텍스트 크기
    - [분석을 세 층에 나눠 두기](analysis-layers.md)
        — 노트북에서 본 숫자는 결론이 아니다, 결론에 쓰는 숫자는 gold 모델을 지나와야 다시 낼 수 있다

위키에 계층 사이드바가 없어 파일은 평평하게 두고, 분류는 이 인덱스에서만 한다.

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
같은 Iceberg JDBC 카탈로그를 공유한다. SQL·dbt 엔진은 클러스터 안의 **Trino**다.
오케스트레이터는 **Dagster**이고 클러스터 밖 호스트에서 돈다(in-cluster 배포는 철거했다).
