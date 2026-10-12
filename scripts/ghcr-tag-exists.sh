#!/usr/bin/env bash
# GHCR 공개 이미지에 태그가 있는지 레지스트리 HTTP API로 판정한다(익명 pull 토큰 — 인증 불필요).
# 사용: scripts/ghcr-tag-exists.sh <owner/name> <tag>
#
# 종료 코드 — 「없음」과 「모름」을 가른다(일시 오류를 「없음」으로 읽으면 불변 태그를 덮어쓴다):
#   0  있음(HTTP 200)
#   1  없음(HTTP 404)
#   2  판정 불가(토큰 실패·그 밖의 HTTP 코드) — 호출 측은 실패로 끝낸다
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "사용법: $0 <owner/name> <tag>" >&2
    exit 2
fi
repo="$1"
tag="$2"

if ! token="$(curl -fsS "https://ghcr.io/token?scope=repository:${repo}:pull" | jq -er .token)"; then
    echo "GHCR 익명 토큰을 받지 못했다: ${repo}" >&2
    exit 2
fi
# 매니페스트 목록(멀티아키)과 단일 매니페스트를 모두 받아들인다 — 하나만 허용하면 있어도 404·406 이 난다
code="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${token}" \
    -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' \
    "https://ghcr.io/v2/${repo}/manifests/${tag}" || echo "000")"
case "${code}" in
    200) exit 0 ;;
    404) exit 1 ;;
    *)
        echo "GHCR 응답 ${code} — 태그 존재를 판정할 수 없다: ${repo}:${tag}" >&2
        exit 2
        ;;
esac
