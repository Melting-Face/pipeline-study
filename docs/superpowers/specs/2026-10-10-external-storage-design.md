# 외부 스토리지(SeaweedFS on Docker) + CNPG 카탈로그 차트화 — 설계

> 구현 계획은 이 설계를 입력으로 별도 작성한다. 관련 설계: [`argocd-gitops.md`](../../argocd-gitops.md).


## Context

- **현재**: S3 정본은 K8s SeaweedFS StatefulSet(`k8s/seaweedfs.yaml`, `k8s-poc-storage.sh`가 apply, PVC 20Gi).
  compose `seaweedfs`(`compose.yml:195`)는 `legacy-storage`로 정지된 레거시. 카탈로그 Postgres는 CNPG
  (`k8s/catalog-postgres.yaml`)지만 ArgoCD 밖(스크립트 apply), `dagster` 롤이 소비자 없이 남아 있다.
- **목적(사용자 결정)**: **실무 구성 모사** — 오브젝트 스토리지는 클러스터 밖 외부 서비스, 클러스터는
  엔드포인트+자격증명으로만 닿는다. 부수효과로 kind 재생성(D3/D8)에도 레이크 데이터가 산다.
- **범위 결정**: Postgres는 **카탈로그 CR의 ArgoCD 차트화**만. **Dagster는 Airflow로 대체 예정**
  (argocd-gitops D6·PR3)이라 메타 DB 이관은 하지 않고 `dagster` 롤·Secret을 제거한다. Airflow 메타 DB는 PR3 소관.
- 이 결정은 `docs/redesign.md:119`의 "SeaweedFS K8s 이전(한 플랫폼 표준화)"을 **번복**한다.

## 결정 요약

| # | 결정 | 근거 |
|---|---|---|
| E1 | SeaweedFS 정본 = compose(profile `storage`, 이미지 `4.36`) | 실무 모사, 수명주기 독립 |
| E2 | 파드 연결 = **kind 네트워크 후결합 + `Service seaweedfs` (`ExternalName`)** | registry 선례(`terraform/cluster/kind/main.tf:106-119`) 재사용, `k8s/` 매니페스트의 `http://seaweedfs:8333` 참조 무변경 |
| E3 | S3 인증 켬(`s3.json`, gitignore) — `.env` `ICEBERG_S3_*`·K8s Secret `lakehouse-creds`와 **한 벌** | 실무 모사, 키 불일치 `ACCESS_DENIED` 함정 |
| E4 | 데이터 = K8s→compose **S3 sync 전량**(키 보존) → CNPG 카탈로그 경로 유효 유지 | 카탈로그 재생성 불필요 |
| E5 | `catalog-postgres` → `gitops/charts/catalog-postgres`(ArgoCD), `dagster` 롤 제거 | PR2 계획 흡수, YAGNI |
| E6 | K8s SeaweedFS PVC는 이번 PR에서 **보존**(롤백), 삭제는 별도 승인 게이트 | 비가역 분리 |

## 아키텍처

```
podman machine
├─ compose: seaweedfs (독립 기동, 127.0.0.1:8333 ← 호스트 도구 직결, port-forward 18333 폐지)
│     │  (TF terraform_data.seaweedfs_network: network connect --alias seaweedfs-ext — 없으면 경고·skip)
└─ network kind
     └─ kind node → pod
          Service seaweedfs (ExternalName → 별칭 seaweedfs-ext; 같은 이름이면 CNAME 자기참조 위험)
          http://seaweedfs:8333  (Spark Connect/Thrift/SparkApplication/Flink/CNPG backup 무변경)
          catalog-postgres-rw:5432/iceberg (CNPG, 이제 ArgoCD 관리)
```

## 구성요소(변경 파일)

**PR-A `feat(storage)`**
- `compose.yml` `seaweedfs`: profile `legacy-storage`→`storage`(의존자 때문에 `legacy-sql`·`monitoring` 유지),
  `-s3.config` 마운트, healthcheck, `deploy.resources` 확인. 확인: `podman compose --profile <p> config --services`.
- `gitops/charts/storage-external/`: `Service seaweedfs` `type: ExternalName` 1개.
  `terraform/platform/variables.tf` `var.apps` 등록.
- `terraform/cluster/kind/main.tf`: `terraform_data.seaweedfs_network`(registry_network와 같은 모양, 멱등).
- `scripts/storage-up.sh`(신규): compose 기동·`s3.json` 생성·버킷 생성·kind 네트워크 멱등 재연결.
- `scripts/`: 버킷 초기화(`warehouse`·`pg-backup`·`dagster-logs`),
  K8s→compose sync(PEP 723, 절차형 `main()`, `when_required`).
- `k8s/seaweedfs.yaml` 제거, `k8s-poc-storage.sh`에서 SeaweedFS apply·버킷 생성 제거.
- `.env.example`: `ICEBERG_S3_ENDPOINT=http://localhost:8333`, 미사용 `ENDPOINT_URL` 정리.

**PR-B `feat(k8s)`**
- `gitops/charts/catalog-postgres/`: `k8s/catalog-postgres.yaml` Cluster CR 이관
  (`managed.roles`에서 `dagster` 제거). `var.apps` 등록.
- `scripts/k8s-poc-storage.sh` → `scripts/k8s-secrets.sh`로 축소(Secret만, `dagster-meta-pg-app` 제거).
- Barman 백업 재활성화는 **범위 밖 → Issue 1줄**(대상이 compose SeaweedFS로 바뀜).

## 전환 순서(집행 단계, 🔴 = reviewer 체크리스트 + 사용자 승인)

0. **Spike 게이트**: 임시 파드에서 `seaweedfs` 해석·`:8333` 응답 확인 + **음성 대조**(미연결 이름은 실패).
   실패 시 정지 → B안(호스트 게이트웨이) 재분류.
1. compose `seaweedfs` 기동(인증·healthcheck) + 버킷 생성. 이중 존재 구간 시작.
2. 🔴 쓰기 동결 → **S3 sync**(port-forward 18333 → 8333).
   판정: 버킷별 객체 수·총 바이트 일치, `raw/` `.sha256` 사이드카 대조.
3. 🔴 전환: K8s StatefulSet `replicas=0`(PVC 보존) → 같은 이름 ExternalName Service 적용(ArgoCD)
   → Spark Connect·Flink 재기동.
   확인: `kubectl get endpoints seaweedfs` 0건(레거시가 대신 답하지 않음).
4. 실증: Spark 기존 테이블 `count(*)` 전환 전 값 대조(엔진 병기)
   + 새 테이블 쓰기→읽기(`write_verified: True`) + `load_table`까지.
5. 🔴 PR-B: catalog-postgres 차트를 ArgoCD로 채택(기존 CR과 동일 스펙 — diff 0 확인 후), `dagster` 롤 제거.
6. 🔴 (롤백 기간 뒤 별도) K8s SeaweedFS PVC 삭제.

## 리스크

| 위험 | 관측 경로 | 대응 |
|---|---|---|
| 레거시가 정본 대신 답함(이중 존재) | `kubectl get endpoints seaweedfs` | `replicas=0` 먼저, Service 교체는 그 뒤 |
| 키 불일치 → 나열 OK·`load_table` `ACCESS_DENIED` | 실증을 `load_table`까지 | `s3.json`·`lakehouse-creds`·`.env` 한 벌 갱신 |
| compose 재기동으로 kind 네트워크 연결 해제 | `storage-up.sh` 재실행 | 멱등 재연결, 문서 명시 |
| aws-chunked 손상 | 사이드카 해시 대조 | sync에도 `when_required` |
| ArgoCD 채택 시 CNPG Cluster 재생성 | `argocd app diff` | diff 0 확인 후 sync, `Prune=false` |

## 문서(한 벌, 일자·실측 수치 없음)

`docs/redesign.md`(번복 근거) · `docs/architectures/storage.md`·`overview.md` · `docs/conventions/k8s.md` §1·§10·§11 ·
`docs/conventions/docker.md`(profile `storage`) · `docs/operations.md`(엔드포인트 표) · `docs/conventions/k8s/cnpg.md` ·
`docs/argocd-gitops.md`(PR2 범위) · `CLAUDE.md` 인프라 요약 1줄 교체(순감) · `AGENTS.md` 요약 동기화.

## Verification

- `podman compose --profile storage|legacy-sql|monitoring config --services`
- 0단계 spike(양성+음성 대조)
- `uv run scripts/storage_conformance_probe.py`(새 엔드포인트), `uv run scripts/spark_connect_smoke.py`
- 4단계 실증(count 대조·쓰기→읽기·`load_table`)
- `terraform plan`(cluster/kind: seaweedfs_network만 추가), `argocd app diff catalog-postgres` = 0
- pre-commit(sqlfluff·ruff·doc lint) 통과
