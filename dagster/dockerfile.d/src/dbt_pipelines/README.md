# dbt_pipelines

이 저장소의 단일 dbt 프로젝트다. 엔진은 k8s Trino(`profiles.yml`의 `dev` 타깃)이고,
데이터셋별 모델은 `models/<dataset>/`에 둔다.

- 규칙·타깃·주의사항: [dbt 컨벤션](../../../../docs/conventions/dbt.md)
- 실행은 Dagster `@dbt_assets`가 맡는다: [Dagster 컨벤션](../../../../docs/conventions/dagster.md)
