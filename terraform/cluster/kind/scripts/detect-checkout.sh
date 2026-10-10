#!/usr/bin/env bash
# 체크아웃 종류 판정 — 이 디렉터리가 linked worktree 인지 Terraform `data "external"`로 넘긴다.
#
# 왜 필요한가:
#   seaweedfs 데이터 기본 경로(main.tf 의 local.seaweedfs_data_dir)는 체크아웃 기준 상대 경로라,
#   linked worktree 에서 apply 하면 빈 seaweedfs/data 를 바인드한 새 레이크가 조용히 뜬다.
#   이 결과를 precondition 이 받아 "루트 체크아웃에서만 apply" 규칙을 plan 단계에서 집행한다.
#
# 판정: linked worktree 는 --git-dir(.git/worktrees/<name>)과 --git-common-dir(.git)이 다르다.
#   둘 다 절대 경로로 받아 비교한다 — 기본 출력은 cwd 기준 상대 경로라 문자열 비교가 어긋날 수 있다.
set -euo pipefail

# Terraform 이 어디서 실행되든 이 스크립트가 놓인 체크아웃을 판정한다.
cd "$(dirname "$0")"

git_dir="$(git rev-parse --path-format=absolute --git-dir)"
common_dir="$(git rev-parse --path-format=absolute --git-common-dir)"

linked="false"
if [ "$git_dir" != "$common_dir" ]; then
    linked="true"
fi

printf '{"linked":"%s"}\n' "$linked"
