# Docker · Compose (아키텍처 · 프로젝트 관점)

## 이 프로젝트에서의 역할

호스트 보조 서비스 — `compose.yml`의 `profiles`로 전부 opt-in 기동한다.

## 어떻게 도나

- `compose.yml` 하나에 profile `host-dagster`(dagster×2·postgres)와 `legacy-meta`가 있다.
- 뼈대(core)는 비어 있어 `--profile <name>`으로 켤 때만 뜬다. 런타임은 `podman compose`다.

## 개요

Docker는 **OS 수준 가상화(컨테이너)** 로 앱과 의존성을 이미지로 패키징해 어디서나 재현 가능하게
실행한다. Compose는 다중 컨테이너를 **단일 선언 파일(`compose.yml`)** 로 정의·오케스트레이션한다
(단일 호스트 범위).

## 왜 이걸 골랐나 — ✅ 채택

- **역할**: 호스트 쪽 보조 서비스(`host-dagster`의 dagster×2·postgres)를 `compose.yml` 하나로
  기동(전부 opt-in). podman-compose와도 호환.
- **채택 이유**: 단일 호스트 학습·개발엔 compose가 가장 단순(YAGNI). 다중 노드·오토스케일이 불필요하다.
- **구성 규칙**(상세 [conventions/docker.md](../conventions/docker.md)): 로깅·env YAML 앵커(DRY),
  이미지 태그 고정(`latest` 금지), healthcheck+`depends_on`, 전 서비스 `deploy.resources`,
  **옵션 기능은 `profiles`**(host-dagster·legacy-meta).
- **K8s 대비**: compose=단일 호스트·단순 / K8s=다중 노드·자가치유·오토스케일·롤링업데이트.
  이행 기준은 [k8s.md](k8s.md).

## 운영 메모

- `deploy.resources`로 CPU·메모리 상한을 명시해 OOM·경합을 방지한다. 수치는 [resource-sizing.md](../resource-sizing.md).
- 비밀정보는 `.env`+`${ENV:...}` 참조, 설정 파일은 `:ro` 마운트([security.md](../security.md)).

## 참고

- Docker Compose 파일 레퍼런스: https://docs.docker.com/reference/compose-file/
- `deploy.resources`: https://docs.docker.com/reference/compose-file/deploy/#resources
- profiles: https://docs.docker.com/compose/how-tos/profiles/
