#!/usr/bin/env bash
# gitops/apps/<app>/config.yaml(앱별 설정 파일)을 정적으로 검사한다 — 클러스터에는 붙지 않는다.
#
# 사용법: scripts/gitops-apps-check.sh [apps-dir]   (기본 gitops/apps — 음성 대조 테스트가 임시 디렉터리를 넘긴다)
#
# 설정 파일은 ApplicationSet git files generator 가 읽을 앱 목록의 단일 출처다(docs/argocd-gitops.md).
# terraform/platform 의 var.apps validation 이 하던 검사를 여기로 옮기고 몇 가지를 더한다.
#   (a) 최상위 키는 name·namespace·tier·source 넷 — 누락·알 수 없는 키 모두 실패(오타가 조용히 무시되는 것을 막는다)
#   (b) name 은 디렉터리 이름과 같고 DNS-1123 label(63자 이하)이며 전체에서 유일하다
#       — name 이 Application 이름이라 중복되면 하나가 다른 하나를 덮어쓴다
#   (c) namespace 는 DNS-1123 label
#   (d) tier 는 문자열 "0"·"1"·"2" 중 하나(RollingSync 단계 라벨 — 숫자로 쓰면 라벨 값 비교가 어긋난다)
#   (e) source.type 별 필수 키
#       umbrella: path — gitops/charts/ 로 시작하고 그 아래 Chart.yaml 이 있다
#       upstream: repoURL·chart·targetRevision — targetRevision 은 정확 버전(범위·와일드카드 금지),
#                 같은 디렉터리에 values.yaml 이 있다
#   (f) gitops/charts/* 의 모든 차트가 정확히 하나의 umbrella 설정에서 참조된다(고아 차트 = 배포 안 되는 차트)
#   (g) [이행기] terraform/platform/variables.tf 의 var.apps 기본값과 (name·path·namespace) 집합이 같다
#       — A2 에서 AppSet 이 이 파일들을 직접 읽게 되면 var.apps 와 함께 이 검사를 지운다
# 마지막 줄은 `검사한 앱: N개` 이며, 0개면 실패한다.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "${root}"

apps_dir="${1:-gitops/apps}"

uv run --quiet --with pyyaml python - "${apps_dir}" <<'PY'
import glob
import os
import re
import sys

import yaml

apps_dir = sys.argv[1]
errors = []
dns1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
exact_version = re.compile(r"^v?\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
top_keys = {"name", "namespace", "tier", "source"}
source_keys = {"umbrella": {"type", "path"}, "upstream": {"type", "repoURL", "chart", "targetRevision"}}

paths = sorted(glob.glob(os.path.join(apps_dir, "*", "config.yaml")))
names = {}
umbrella_paths = {}
triples = set()
for path in paths:
    app_dir = os.path.dirname(path)
    label = os.path.basename(app_dir)
    cfg = yaml.safe_load(open(path)) or {}
    if not isinstance(cfg, dict):
        errors.append(f"[{label}] (a) 최상위가 맵이 아니다")
        continue
    # (a) 키 집합
    for key in sorted(top_keys - set(cfg)):
        errors.append(f"[{label}] (a) 필수 키 {key} 없음")
    for key in sorted(set(cfg) - top_keys):
        errors.append(f"[{label}] (a) 알 수 없는 키 {key}")
    name = cfg.get("name")
    # (b) name
    if name != label:
        errors.append(f"[{label}] (b) name {name!r} 이 디렉터리 이름과 다르다")
    if not isinstance(name, str) or not dns1123.match(name) or len(name) > 63:
        errors.append(f"[{label}] (b) name {name!r} 이 DNS-1123 label 이 아니다")
    if name in names:
        errors.append(f"[{label}] (b) name {name!r} 중복({names[name]})")
    names[name] = path
    # (c) namespace
    ns = cfg.get("namespace")
    if not isinstance(ns, str) or not dns1123.match(ns) or len(ns) > 63:
        errors.append(f"[{label}] (c) namespace {ns!r} 이 DNS-1123 label 이 아니다")
    # (d) tier
    tier = cfg.get("tier")
    if tier not in ("0", "1", "2"):
        errors.append(f"[{label}] (d) tier {tier!r} 는 문자열 \"0\"·\"1\"·\"2\" 중 하나여야 한다")
    # (e) source
    src = cfg.get("source")
    if not isinstance(src, dict):
        errors.append(f"[{label}] (e) source 가 맵이 아니다")
        continue
    stype = src.get("type")
    if stype not in source_keys:
        errors.append(f"[{label}] (e) source.type {stype!r} 는 umbrella·upstream 중 하나여야 한다")
        continue
    for key in sorted(source_keys[stype] - set(src)):
        errors.append(f"[{label}] (e) source.{key} 없음({stype})")
    for key in sorted(set(src) - source_keys[stype]):
        errors.append(f"[{label}] (e) source.{key} 는 {stype} 에서 쓰지 않는 키")
    if stype == "umbrella":
        chart_path = str(src.get("path", "")).rstrip("/")
        if not chart_path.startswith("gitops/charts/"):
            errors.append(f"[{label}] (e) source.path {chart_path!r} 가 gitops/charts/ 로 시작하지 않는다")
        elif not os.path.isfile(os.path.join(chart_path, "Chart.yaml")):
            errors.append(f"[{label}] (e) {chart_path}/Chart.yaml 이 없다")
        if chart_path in umbrella_paths:
            errors.append(f"[{label}] (f) {chart_path} 를 {umbrella_paths[chart_path]} 도 참조한다")
        umbrella_paths[chart_path] = label
        triples.add((name, chart_path, ns))
    else:
        if not exact_version.match(str(src.get("targetRevision", ""))):
            errors.append(f"[{label}] (e) source.targetRevision {src.get('targetRevision')!r} 는 정확 버전이어야 한다")
        if not os.path.isfile(os.path.join(app_dir, "values.yaml")):
            errors.append(f"[{label}] (e) upstream 앱은 {app_dir}/values.yaml 이 있어야 한다")

# (f) 고아 차트 — 기본 디렉터리를 검사할 때만(음성 대조용 임시 디렉터리는 저장소 차트와 무관하다)
if os.path.normpath(apps_dir) == os.path.normpath("gitops/apps"):
    for chart in sorted(d.rstrip("/") for d in glob.glob("gitops/charts/*/")):
        if chart not in umbrella_paths:
            errors.append(f"(f) {chart} 를 참조하는 설정 파일이 없다(배포되지 않는 차트)")
    # (g) [이행기] var.apps 와 대조
    tf = open("terraform/platform/variables.tf").read()
    entry = re.compile(r'\{\s*name\s*=\s*"([^"]+)",\s*path\s*=\s*"([^"]+)",\s*namespace\s*=\s*"([^"]+)"\s*\}')
    tf_triples = set(entry.findall(tf))
    if not tf_triples:
        errors.append("(g) variables.tf 에서 var.apps 원소를 하나도 읽지 못했다(파서가 죽었다 — 0건을 일치로 읽지 않는다)")
    for t in sorted(tf_triples - triples):
        errors.append(f"(g) var.apps 에만 있다: {t}")
    for t in sorted(triples - tf_triples):
        errors.append(f"(g) 설정 파일에만 있다: {t}")

for e in errors:
    print(f"FAIL {e}")
print(f"검사한 앱: {len(paths)}개")
sys.exit(1 if errors or not paths else 0)
PY
