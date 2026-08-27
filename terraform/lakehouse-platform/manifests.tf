# 매니페스트 — **YAML 이 정본이고 Terraform 은 적용자다.**
#
# `k8s/**` 의 주석에는 "왜 이 값인가"가 들어 있다(aws-chunked 함정 · S3FileIO 와 S3A 의
# 역할 분담 · probe 가 보증하지 않는 것 · 관측 경로 선언 …). HCL 타입 리소스로 재작성하면
# 그 지식이 사라지므로 파일을 그대로 두고 여기서 읽는다.

locals {
  # 🔴 명시 목록이다. `fileset` 로 긁지 않는다 — `k8s/**` 에는 **다른 스택 소유**가 섞여 있다.
  #    제외 대상과 이유:
  #      kind-cluster.yaml           A(cluster) — 클러스터 생성 시점 산출물
  #      seaweedfs.yaml              B(data) — destroy 하면 PVC 10G 가 간다
  #      catalog-postgres.yaml       B(data) — 위와 같음
  #      catalog-pg-backup.yaml      B(data) — Cluster CR 과 한 벌
  #      spark/spark-connect-server.yaml   온디맨드 컴퓨트(회수 다이얼 0번) — 상주가 아니다
  #      spark/spark-thrift-server.yaml    선언만 있고 미배포
  #      spark/sparkapplication-poc.yaml   잡 — Dagster 가 런타임에 제출한다
  #      flink/flinkdeployment-session.yaml 세션 클러스터 — 회수 다이얼 1번
  #      flink/iceberg-*-job.yaml    잡 정의(ConfigMap) — 세션 클러스터와 한 벌
  manifest_files = [
    "local-ca.yaml",
    "spark/spark-workload-cleanup-rbac.yaml",
    "flink/flink-operator-webhook-rbac.yaml",
    "flink/flink-workload-rbac.yaml",
    "dagster/dagster-rbac.yaml",
    "dagster/dagster-meta-db.yaml",
    "dagster/dagster-deploy.yaml",
  ]

  # 🔴 멀티도큐먼트 분해. 7파일 중 단일 문서는 하나뿐이고 나머지는 2~5개를 담는다(총 18개).
  #    `yamldecode` 는 **단일 문서만** 받으므로 `---` 로 쪼갠다.
  #
  # ⚠️ 필터가 `trimspace(d) != ""` 로는 부족하다(2026-08-28 실측). 이 저장소의 파일은
  #    **주석 블록으로 시작한 뒤 `---` 가 오는** 구조라, 분할하면 **주석만 있는 첫 조각**이
  #    생기고 거기서 `yamldecode` 가 `missing start of document` 로 죽는다.
  #    ⇒ `can()` 으로 파싱 가능 여부를, `kind` 유무로 **오브젝트인지**를 함께 본다.
  #    시끄럽게 죽는 편이라 조용한 오적재는 아니지만, 필터를 약하게 두면 이 스택이
  #    파일 편집에 취약해진다.
  manifest_docs = merge([
    for f in local.manifest_files : {
      for d in [
        for raw in split("\n---\n", file("${path.module}/../../k8s/${f}")) : yamldecode(raw)
        if can(yamldecode(raw)) && try(yamldecode(raw).kind, null) != null
      ] :
      # 키는 GVK+이름이다. 파일명을 키에 넣지 않는다 — 파일을 쪼개거나 합쳐도
      # 리소스 주소가 안 바뀌어야 state 가 따라온다.
      "${d.kind}/${try(d.metadata.namespace, "-")}/${d.metadata.name}" => d
    }
  ]...)
}

resource "kubernetes_manifest" "platform" {
  for_each = local.manifest_docs

  manifest = each.value
}
