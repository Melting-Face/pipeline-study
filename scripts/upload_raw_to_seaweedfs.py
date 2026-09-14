#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3"]
# ///
"""로컬 원천 csv.gz를 SeaweedFS(S3 호환) warehouse 버킷의 raw/로 업로드한다.

🔴 **이것은 폴백이다. 정본 경로가 아니다.**
    원천 획득의 정본은 **Dagster 수집 자산**(`defs/<dataset>/raw_assets.py`)이고,
    그쪽이 PhysioNet에서 직접 받아 같은 `raw/` 경로에 놓는다. 이 스크립트는
    그 경로가 막혔을 때를 위한 수동 우회다 — 클러스터에서 physionet.org로
    나가지 못하거나, 상류가 응답하지 않거나, 이미 로컬에 받아둔 파일이 있을 때.

왜 아직 남아 있는가:
    SeaweedFS Admin UI(:23646)는 프론트엔드 JS에 500MB 업로드 상한이 하드코딩돼 있어
    대용량 원천(chartevents·labevents ≈ 3.3GB)을 UI로 올릴 수 없다.
    boto3 upload_file은 TransferConfig 기반 자동 멀티파트로 상한 없이 안정 적재한다.

🔴 **파일 목록(매니페스트)을 더 이상 소유하지 않는다.**
    예전에는 대상 14개가 이 파일에 하드코딩돼 있었고, 그래서 15번째 파일이
    추가되면 **에러가 아니라 조용한 누락**으로 그 파일만 안 올라갔다. 이제는
    로컬 디렉터리에 있는 `.csv.gz`를 **구조 그대로 미러**한다 — 목록을 갖지
    않으면 목록이 낡을 수도 없다(드리프트의 구조적 해소).
    열거의 정본은 이제 **자산 그래프**이고 `dg check`가 센다.

    ⚠️ 그 대가로 *"목록에 없는 파일은 올리지 않는다"*는 안전장치를 잃었다.
    그래서 **로컬 구조가 곧 S3 구조**이며(평평한 폴더 자동 매칭은 사라졌다),
    올리기 전에 `-n`으로 목록을 확인하는 것이 기본 절차다.

전제:
    - 리포 루트 .env에 AWS_ACCESS_KEY_ID/SECRET/DEFAULT_REGION 존재
    - SeaweedFS S3 엔드포인트 접근 가능(compose 또는 port-forward)

실행(의존성은 위 PEP 723 블록에 선언 — uv가 자동 provisioning):
    uv run scripts/upload_raw_to_seaweedfs.py -n ./data/raw      # 미리보기(먼저 할 것)
    uv run scripts/upload_raw_to_seaweedfs.py ./data/raw         # 전부 업로드
    uv run scripts/upload_raw_to_seaweedfs.py ./data/raw -u mimiciv   # 하위 트리만

로컬 파일 배치 — **raw/ 기준 상대경로를 그대로 재현**한다:
    <LOCAL_DIR>/mimiciv/icu/chartevents.csv.gz  →  s3://warehouse/raw/mimiciv/icu/chartevents.csv.gz
    <LOCAL_DIR>/eicu/patient.csv.gz             →  s3://warehouse/raw/eicu/patient.csv.gz

스타일: 스크립트 컨벤션(docs/conventions/python.md)에 따라 절차형으로 쓴다.
    선언(상수)은 상단에 두고 진입은 하단(호이스팅), 클래스·보조 함수로 쪼개지 않고
    main에서 위→아래로 실행한다(캡슐화·함수화 최소화). 근거는 가독성/LoB.
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 업로드 대상 확장자. 원천은 전부 gzip CSV다 — 이 필터가 매니페스트를 대신하는
# 유일한 안전장치이므로 넓히지 않는다(넓히면 로컬의 아무 파일이나 올라간다).
SOURCE_SUFFIX = ".csv.gz"


def main() -> int:
    """.env·인자를 읽어 로컬 csv.gz를 SeaweedFS raw/로 미러한다(절차형)."""
    # 1) 인자
    parser = argparse.ArgumentParser(
        description="로컬 원천 csv.gz를 SeaweedFS warehouse/raw/로 업로드",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "local_dir", nargs="?", default="./data/raw", help="원천 파일 로컬 디렉토리"
    )
    parser.add_argument(
        "-e",
        "--endpoint",
        default=os.environ.get("S3_ENDPOINT_URL", "http://localhost:8333"),
        help="S3 엔드포인트(호스트 기준)",
    )
    parser.add_argument(
        "-b", "--bucket", default=os.environ.get("S3_BUCKET", "warehouse")
    )
    parser.add_argument(
        "-u",
        "--under",
        default="",
        help="이 하위 경로만 대상으로 한다(예: mimiciv, mimiciv/icu). 비우면 전체",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="전송 없이 매칭만 출력"
    )
    args = parser.parse_args()

    # 2) boto3 (--help 이후 로드 → 도움말은 미설치여도 동작. uv run이 PEP 723으로 설치)
    try:
        import boto3
        from boto3.s3.transfer import TransferConfig
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError
    except ModuleNotFoundError:
        sys.exit("❌ boto3가 필요합니다: uv run scripts/upload_raw_to_seaweedfs.py ...")

    # 3) .env 로드 (KEY=VALUE; 인라인 주석·따옴표 제거; 기존 env 우선)
    env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, _, env_val = line.partition("=")
            env_key = env_key.strip()
            env_val = env_val.split(" #", 1)[0].strip().strip("'\"")
            if env_key:
                os.environ.setdefault(env_key, env_val)

    for cred in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if not os.environ.get(cred):
            sys.exit(f"❌ .env에 {cred}가 없습니다")

    local_dir = Path(args.local_dir)
    if not local_dir.is_dir():
        sys.exit(f"❌ 로컬 디렉토리가 없습니다: {local_dir}")

    # 로컬 트리를 훑어 대상을 만든다(매니페스트 없음 — 로컬 구조가 곧 S3 구조).
    scan_root = local_dir / args.under if args.under else local_dir
    if not scan_root.is_dir():
        sys.exit(f"❌ --under 경로가 없습니다: {scan_root}")
    manifest = sorted(
        str(p.relative_to(local_dir))
        for p in scan_root.rglob("*")
        if p.is_file() and p.name.endswith(SOURCE_SUFFIX)
    )
    if not manifest:
        sys.exit(f"❌ {scan_root} 아래에 {SOURCE_SUFFIX} 파일이 없습니다")

    print(f"▶ 엔드포인트 : {args.endpoint}")
    print(f"▶ 대상 버킷  : s3://{args.bucket}/raw   (under={args.under or '(전체)'})")
    print(f"▶ 로컬 소스  : {local_dir}")
    if args.dry_run:
        print("▶ 모드       : DRY-RUN (실제 전송 안 함)")
    print()

    # 4) S3 클라이언트 (SeaweedFS는 가상호스트 미지원 → path-style 강제, iceberg와 동일)
    s3 = boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )
    try:
        s3.list_buckets()
    except (ClientError, BotoCoreError) as exc:
        sys.exit(f"❌ S3 엔드포인트 접속 실패: {args.endpoint}\n   ({exc})")

    # 버킷 없으면 생성 (멱등)
    try:
        s3.head_bucket(Bucket=args.bucket)
    except ClientError:
        print(f"• 버킷 생성: s3://{args.bucket}")
        if not args.dry_run:
            s3.create_bucket(Bucket=args.bucket)

    # 5) 매니페스트 순회 업로드 (8MB↑는 64MB 청크 멀티파트로 병렬 전송)
    transfer = TransferConfig(
        multipart_threshold=8 * 1024 * 1024,
        multipart_chunksize=64 * 1024 * 1024,
        max_concurrency=4,
    )
    found = uploaded = 0
    for rel in manifest:
        # 매니페스트가 로컬 스캔 결과이므로 파일은 반드시 존재한다.
        src = local_dir / rel
        found += 1
        size_mb = src.stat().st_size / 1024 / 1024
        size_str = f"{size_mb / 1024:.1f}GB" if size_mb >= 1024 else f"{size_mb:.1f}MB"
        key = f"raw/{rel}"
        target = f"s3://{args.bucket}/{key}"
        if args.dry_run:
            print(f"  → [{size_str}]  {src}  →  {target}")
            continue
        print(f"  ↑ [{size_str}]  {target}")
        s3.upload_file(str(src), args.bucket, key, Config=transfer)
        uploaded += 1

    # 6) 요약
    print()
    # 🔴 「대상」과 「업로드」가 세는 단위가 다르다 — dry-run이면 대상은 있고
    #    업로드는 0이다. 두 수가 갈리는 것이 정상이니 라벨을 붙여 센다.
    print(
        f"요약: 로컬발견 {found} / 업로드 {uploaded}"
        + (" (DRY-RUN)" if args.dry_run else "")
    )
    if not args.dry_run:
        print(
            f"확인: aws --endpoint-url {args.endpoint} "
            f"s3 ls s3://{args.bucket}/raw/ --recursive --human-readable"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
