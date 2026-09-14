"""PhysioNet credentialed 원천 접근 리소스.

MIMIC-IV·eICU-CRD 원천 파일을 **인증된 세션으로** 받아오기 위한 리소스다.
연결(자격증명·세션·URL 조립)을 자산이 아니라 리소스에 두는 저장소 규약을 따른다.

🔴 **인증 방식이 미확인이다.** 현재 구현은 HTTP Basic 한 경로이고, 실측에서
거부되면 세션 로그인(`POST /login/` + CSRF 쿠키)으로 바뀐다. 그 변경이
**이 파일 안에서 끝나도록** 리소스의 계약을 "인증된 `requests.Session`을
돌려준다"로 잡았다 — 자산·헬퍼는 세션만 보므로 분기가 전파되지 않는다.
(그래서 `auth=` 튜플을 자산에 넘기지 않는다. 요청 생성은 리소스가 소유한다.)

🔴 **개인 크리덴셜이다.** 서비스 계정이 아니라 DUA에 서명한 *개인*의 계정이며
`docs/security.md`가 크리덴셜 관리를 협약상 실제 의무로 명시한다. 값은
`dg.EnvVar`로 **run 시점에** 해석한다 — 모듈 로드 시점에 `os.environ`으로
읽으면 키가 없는 webserver에서 **정의 로드가 통째로 실패**한다.
"""

import requests
from dagster_aws.s3 import S3Resource

import dagster as dg
from dagster import AssetExecutionContext
from dagster_project.common.fetch import (
    download_to_s3,
    mask_url,
    parse_sha256sums,
    raise_masked,
    validate_relative_path,
)

# 무결성 정본 파일명. 버전 루트에 놓인다.
# ⚠️ 존재·형식 모두 **미확인**(외부 사실) — `scripts/physionet_access_probe.py`가
#    판정한다. 없으면 빈 매핑이 되고 멱등 판정은 `downloaded_manifest_missing`
#    으로 떨어져 **매 실행 재수신**한다(비싸지만 조용히 틀리지는 않는다).
SHA256SUMS_FILENAME = "SHA256SUMS.txt"


class PhysioNetResource(dg.ConfigurableResource):
    """PhysioNet 파일 저장소 접근 리소스.

    Attributes:
        username: PhysioNet 계정 ID.
        password: PhysioNet 계정 비밀번호.
        base_url: 파일 저장소 루트. 기본값은 공식 경로.
        timeout_s: HTTP 타임아웃(초).
    """

    username: str
    password: str
    base_url: str = "https://physionet.org/files"
    timeout_s: int = 60

    def session(self) -> requests.Session:
        """인증이 걸린 requests 세션을 만든다.

        🔴 **`Accept-Encoding: identity`를 강제한다.** 서버가 `.csv.gz`에
        `Content-Encoding: gzip`을 붙이면 urllib3가 전송 계층에서 한 번 풀어
        **`.csv.gz`라는 이름의 평문 CSV**가 S3에 올라간다. SHA256SUMS와의
        바이트 동일성도 그 순간 깨진다.
        """
        session = requests.Session()
        session.auth = (self.username, self.password)
        session.headers.update({"Accept-Encoding": "identity"})
        return session

    def file_url(self, project: str, version: str, rel_path: str) -> str:
        """원천 파일의 절대 URL을 만든다.

        Args:
            project: PhysioNet 프로젝트 슬러그(예: "mimiciv").
            version: 고정 버전(예: "3.1"). **스키마 계약이다** — 올리면
                `docs/dataset_schema.md`도 한 벌로 고친다.
            rel_path: 프로젝트 루트 기준 상대경로.

        Returns:
            파일 URL.
        """
        safe = validate_relative_path(rel_path)
        return f"{self.base_url}/{project}/{version}/{safe}"

    def fetch_sha256sums(self, project: str, version: str) -> dict[str, str]:
        """버전 루트의 SHA256SUMS.txt를 받아 파싱한다.

        받지 못하면 **빈 매핑을 돌려준다**(예외로 올리지 않는다). 무결성 정본이
        없다는 것은 자산 실패 사유가 아니라 *판정 축이 없다*는 뜻이고, 그
        상태는 `decide_download`가 `downloaded_manifest_missing`으로 세어
        재수신한다 — 조용한 스킵보다 비싼 재수신이 안전한 방향이다.

        Args:
            project: 프로젝트 슬러그.
            version: 고정 버전.

        Returns:
            상대경로 → sha256 hex 매핑. 정본이 없으면 빈 매핑.
        """
        url = f"{self.base_url}/{project}/{version}/{SHA256SUMS_FILENAME}"
        session = self.session()
        try:
            response = session.get(url, timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise_masked(url, exc)
        finally:
            session.close()

        if not response.ok:
            return {}
        # ⚠️ 200이어도 HTML이면 정본이 아니다(로그인 페이지). 해시 형식을
        #    만족하는 줄이 하나도 없으면 파싱 결과가 자연히 빈 매핑이 된다.
        return parse_sha256sums(response.text)

    def digest_for(self, sums: dict[str, str], project_rel_path: str) -> str | None:
        """manifest에서 대상 파일의 기대 해시를 찾는다.

        찾지 못하면 None이다. 🔴 **None을 「일치」로 읽지 않는다** —
        `decide_download`가 이 축을 `downloaded_manifest_missing`으로 따로
        센다.

        Args:
            sums: `fetch_sha256sums` 결과.
            project_rel_path: 프로젝트 루트 기준 상대경로.

        Returns:
            기대 해시 또는 None.
        """
        return sums.get(project_rel_path)

    def masked(self, url: str) -> str:
        """로그·메시지에 쓸 마스킹된 URL."""
        return mask_url(url)


def fetch_physionet_file(
    context: AssetExecutionContext,
    *,
    s3: S3Resource,
    physionet: PhysioNetResource,
    project: str,
    version: str,
    rel_path: str,
    target_base: str,
    force: bool = False,
) -> dg.MaterializeResult:
    """PhysioNet 파일 하나를 S3 `raw/`로 가져온다(수집 자산의 공통 본문).

    🔴 **`rel_path` 하나가 원천 URL과 S3 키를 동시에 만든다.** 이것이 의도다 —
    둘을 따로 적으면 "받는 파일과 읽는 파일이 다른데 양쪽 다 성공하는" 상태가
    가능해지고, 그 어긋남은 적재가 끝난 뒤에야 값으로 드러난다.
    (그래서 `validate_relative_path`가 traversal을 막는 것이 필수다.)

    manifest는 자산마다 한 번씩 받는다. 14개 자산이면 14회지만 대상이 수십 KB
    텍스트라 무시할 만하고, run 간 캐시를 두면 그 캐시가 곧 또 하나의 무효화
    대상이 된다(단순함 우선).

    Args:
        context: 에셋 실행 컨텍스트.
        s3: dagster-aws S3Resource.
        physionet: PhysioNet 접근 리소스.
        project: 프로젝트 슬러그.
        version: 고정 버전.
        rel_path: 프로젝트 루트 기준 상대경로(= S3 키의 데이터셋 하위 경로).
        target_base: 도착지 s3:// 루트(`SOURCE_BASE`).
        force: 강제 재수신 여부.

    Returns:
        판정·수집 결과를 담은 MaterializeResult.
    """
    sums = physionet.fetch_sha256sums(project, version)
    expected = physionet.digest_for(sums, rel_path)
    if expected is None:
        # 조용히 넘어가지 않는다 — 이 실행이 **무결성 대조 없이** 받는다는
        # 사실을 로그에 남긴다(원칙 7: 약한 판정임을 드러낸다).
        context.log.warning(
            "%s — manifest에 기대 해시가 없다(항목 %d개). 무결성 대조 없이 수신한다.",
            rel_path,
            len(sums),
        )
    session = physionet.session()
    try:
        return download_to_s3(
            context,
            s3=s3,
            session=session,
            source_url=physionet.file_url(project, version, rel_path),
            target_uri=f"{target_base}/{rel_path}",
            expected_sha256=expected,
            force=force,
            timeout_s=physionet.timeout_s,
        )
    finally:
        session.close()
