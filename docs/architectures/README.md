# 아키텍처 문서 (architectures)

이 프로젝트의 전체 스택과, 각 처리·배포 기술을 **프로젝트 결정 관점**으로 정리한다.
채택 ✅ / 채택·이행중(PoC 게이트) 🚧 / 미채택(참고·향후 검토) 🔎로 표기한다.

> **재설계 진행중**: 호스트 Dagster + K8s(Spark Operator)로의 이행 로드맵은 [../redesign.md](../redesign.md).

## 목차

| 문서 | 상태 | 내용 |
| --- | --- | --- |
| [overview.md](overview.md) | 🚧 | 현행 스택 스냅샷·데이터 흐름(Dagster·dbt·Iceberg·SeaweedFS) — **재설계 이행 중**이라 Trino 경로는 제거 대상이고, 스냅샷은 관측 시점과 함께 읽는다 |
| [docker.md](docker.md) | ✅ | 컨테이너·compose 배포(채택) |
| [spark.md](spark.md) | 🚧 | 배치 엔진 — 대용량 인제스트 · dbt-spark 마트 · Iceberg 유지보수. 버전은 **최신이 아니라 Iceberg가 지원하는 짝**으로 고른다 |
| [flink.md](flink.md) | 🚧 | 스트림 엔진 — 실시간 조기경보. Iceberg bronze 스트리밍 읽기를 소스로 쓴다 |
| [k8s.md](k8s.md) | 🚧 | 컨테이너 오케스트레이션 — 컴퓨트·데이터 서비스 이전(이행중) |
| [terraform.md](terraform.md) | 🚧 | IaC — 로컬 K8s 플랫폼을 셸에서 이행. 분할 축은 부트스트랩이 아니라 **폭발반경**이다 |
| [oci.md](oci.md) | 🔎 | 클라우드 이행 — OCI Always Free A1(ARM) + Terraform + k3s(학습·확장 경로) |
| [trino.md](trino.md) | 🔎 | MPP SQL 엔진 — 현행 compose까지 채택, **재설계로 제거**(dbt→dbt-spark) |
| [monitoring.md](monitoring.md) | 🔎 | 모니터링·관측 — Grafana·Loki·Robusta 등을 **지금 쓰지 않는 이유** |

## 각 문서 형식

**개요 / 이 프로젝트에서의 위치(채택 이유·대안 비교) / 운영 메모 / 참고(공식 문서)**.

> 배포·운영 **규칙**은 [conventions/docker.md](../conventions/docker.md)·[conventions/k8s.md](../conventions/k8s.md),
> 자원 **수치**는 [resource-sizing.md](../resource-sizing.md)에서 단일 관리한다.
