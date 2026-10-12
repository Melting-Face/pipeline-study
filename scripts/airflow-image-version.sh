#!/usr/bin/env bash
# Airflow 이미지 발행 버전을 values 파일의 `# bump-image-tag` 줄에서 뽑는다.
# 사용: scripts/airflow-image-version.sh <values-file>   → stdout 에 vX.Y.Z
#
# 이 한 줄이 **배포 상태와 발행 버전의 단일 출처**다 — ArgoCD 는 이 태그를 당겨 가고,
# .github/workflows/airflow-image.yml 은 같은 태그를 GHCR 에 발행한다(없을 때만).
#
# 종료 코드:
#   0  성공 — stdout 에 vX.Y.Z
#   1  사용법 오류·파일 없음
#   3  마커 줄이 정확히 1개가 아니거나 값이 `vX.Y.Z` 가 아님
#      (조용히 다른 값을 내놓으면 엉뚱한 태그가 발행된다 — 판정 불가는 실패로 끝낸다)
set -euo pipefail

marker='# bump-image-tag'

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "사용법: $0 <values-file>  (파일이 있어야 한다)" >&2
    exit 1
fi

lines="$(grep -F -- "${marker}" "$1" || true)"
count="$(printf '%s' "${lines}" | grep -c . || true)"
if [ "${count}" -ne 1 ]; then
    echo "마커 '${marker}' 줄이 정확히 1개여야 한다(현재 ${count}개): $1" >&2
    exit 3
fi

if ! [[ "${lines}" =~ tag:[[:space:]]*\"(v[0-9]+\.[0-9]+\.[0-9]+)\"[[:space:]]*${marker}$ ]]; then
    echo "마커 줄이 'tag: \"vX.Y.Z\"  ${marker}' 모양이 아니다: ${lines}" >&2
    exit 3
fi
printf '%s\n' "${BASH_REMATCH[1]}"
