"""Dagster → Flink 세션 클러스터 수명 관리 + SQL 배치 잡 실행 리소스.

왜 `PipesK8sClient`가 아닌가:
    `dagster-k8s`의 Pipes 클라이언트는 **K8s Job/Pod를 띄우는** 방식이다.
    그런데 `docs/conventions/k8s.md` §C5(JDBC 카탈로그 DDL 조건부 허용) 조건 2는
    *"`kubectl exec` 스트림으로만 실행한다. stdout이 **컨테이너 로그가 되는 Job/파드
    형태로 만들지 않는다**"* 로 못 박는다 — 로그로 나가면 회수가 불가능하기 때문이다.
    ⇒ Pipes는 이 경로에 쓸 수 없다. (`docs/redesign.md`의 "PipesK8sClient" 선언은
    이 조건과 충돌하며 교정 대상이다.)

왜 `FlinkSessionJob` CRD가 아닌가:
    오퍼레이터가 jar를 받아 JM에 업로드하는 구조라 `jarURI: local://`이
    `UnsupportedFileSystemSchemeException`으로 죽는다(§9-2 실측). 이 저장소의 배치 잡은
    jar가 아니라 **SQL ConfigMap + `sql-client.sh`** 이므로 CRD 제출 경로가 없다.

⇒ 남는 형태는 `kubectl exec` 스트림뿐이고, 이 리소스가 그것을 감싼다.
   `defs/poc/resources.py`(SparkApplication 제출·폴링)와 같은 계열이며,
   인증 폴백·상태 2축 판정·로그 회수 패턴을 공유한다.

🔴 **회수 규율을 코드가 진다.** 세션 클러스터는 잡이 없어도 JobManager가 상주 자원을
   먹는다. 문서 규약("검증용으로 띄운 상주 컴퓨트는 그 자리에서 내린다")이 실제로
   13시간 샜고, 발견 경로는 성능 이상이 아니라 "안 쓰는 것 정리"였다. 회수를 트리거하는
   **주체**가 없으면 규약은 조용히 샌다 ⇒ 자산이 `finally`에서 `teardown()`을 부른다.

주의: Dagster가 context를 클래스 identity로 검사하므로 이 모듈에서는
`from __future__ import annotations`(어노테이션 문자열화)를 사용하지 않는다.
"""

import json
import time
from typing import NamedTuple

import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.stream import stream
from kubernetes.stream.ws_client import ERROR_CHANNEL, WSClient

import dagster as dg
from dagster_project.defs.flink.constants import (
    CRD_GROUP,
    CRD_PLURAL,
    CRD_VERSION,
    JM_CONTAINER,
    JM_SELECTOR,
    KUBE_CONTEXT,
    NAMESPACE,
    REQUIRED_CONFIGMAPS,
    SECRET_ENV,
    SESSION_MANIFEST,
    SQL_INIT_PATH,
    SQL_JOB_PATH,
)

# JM이 준비된 상태. CRD 스키마 enum 실측값(2026-08-30):
# DEPLOYING / DEPLOYED_NOT_READY / READY / MISSING / ERROR
JM_READY = "READY"
JM_ERROR_STATES = frozenset({"ERROR", "MISSING"})

# 가드 자체가 내는 종료 코드. sql-client의 종료 코드와 겹치지 않도록 90번대를 쓴다.
RC_REDACTION_FAILED = 90
RC_SECRET_UNSET = 91
RC_REDACTION_MECHANISM_DEAD = 92

# 파드 안에서 도는 실행 스크립트.
#
# 🔴 **마스킹은 `sed`가 아니라 `grep -F`다.** `sed "s|$PW|***|g"`는 비밀번호에 정규식
#   메타문자(`|`·`&`·`\`)가 섞이면 치환에 실패하는데, 그 실패가 **조용하다** —
#   마스킹된 줄
#   대신 원문이 그대로 흘러나온다. `-F`는 리터럴 매칭이라 이 실패 모드가 없다.
#   대가로 **치환이 아니라 줄 삭제**가 되지만, 지워지는 것은 크리덴셜을 품은 DDL 줄이고
#   잡 출력(`02-*.sql`, 크리덴셜 0)은 그대로 남는다.
#
# 🔴 **비밀값은 argv에 올리지 않는다.** 패턴을 파일(`$SECRET`, mktemp=600)에 두고
#   `grep -f`로 넘긴다 — `grep`의 argv에는 파일명만 남아 `/proc/<pid>/cmdline`으로
#   새지 않는다. `printf`·`echo`는 bash 빌트인이라 별도 프로세스를 만들지 않는다.
#
# 🔴 **센티널 프로브가 매 실행 앞에 붙는다.** 마스킹이 "설정됐다"와 "작동한다"는 다른
#   축이라(philosophy.md 원칙 7), 실제 비밀번호를 품은 합성 줄을 같은 파이프에 흘려
#   **지워지는지 확인**한다. 안 지워지면 잡을 돌리기 전에 죽는다(fail-closed).
#   새로 건 게이트는 일부러 위반시켜 막히는지 본다 — 그것을 매 실행 자동화한 것이다.
#
# 정리는 `trap ... EXIT`이 진다. `&&`로 이으면 실패 경로에서 렌더 산출물이 남는다
# (§C5 조건 3 — 렌더 산출물은 chmod 600 + 실행 직후 삭제, `--history`도 버릴 경로 지정).
_RUN_SQL_SCRIPT = r"""
set -u
umask 077

RAW=$(mktemp /tmp/.flink-raw.XXXXXX)
CLEAN=$(mktemp /tmp/.flink-clean.XXXXXX)
INIT=$(mktemp /tmp/.flink-init.XXXXXX)
SECRET=$(mktemp /tmp/.flink-secret.XXXXXX)
HIST=$(mktemp /tmp/.flink-hist.XXXXXX)
trap 'rm -f "$RAW" "$CLEAN" "$INIT" "$SECRET" "$HIST"' EXIT

# 🔴 **파일 크기가 아니라 값을 검사한다.** `printf '%%s\n' ""`는 개행 1바이트를 쓰므로
#   `test -s`(비어있지 않음)가 **참이 되어 통과**한다. 그러면 패턴 파일에 빈 줄만 남고,
#   빈 패턴은 **모든 줄에 매칭**돼 `grep -v`가 출력을 통째로 버린다 — 유출은 없지만
#   "성공했는데 출력이 없는" 조용한 실패가 된다
#   (2026-08-30 프로브에서 실제로 exit 0이 났다).
#   `${VAR:-}`는 미설정과 빈 값을 함께 잡는다(`set -u`의 unbound 에러도 회피).
if [ -z "${%(secret_env)s:-}" ]; then
    echo "[guard] %(secret_env)s 미설정/빈값 — 마스킹 대상을 몰라 실행하지 않는다"
    exit %(rc_unset)d
fi
printf '%%s\n' "${%(secret_env)s}" > "$SECRET"

# 센티널 프로브 — 마스킹 파이프가 실제로 지우는지 매 실행 확인한다.
if printf 'SENTINEL_%%s_SENTINEL\n' "$(cat "$SECRET")" \
        | grep -F -v -f "$SECRET" | grep -q 'SENTINEL'; then
    echo "[guard] 마스킹 메커니즘이 작동하지 않는다 — 잡을 실행하지 않는다"
    exit %(rc_mech)d
fi

envsubst < %(init_src)s > "$INIT"

/opt/flink/bin/sql-client.sh --history "$HIST" -i "$INIT" -f %(job_src)s > "$RAW" 2>&1
rc=$?

grep -F -v -f "$SECRET" "$RAW" > "$CLEAN" || true

# 사후 확인 — 위 프로브가 통과했어도 실제 출력에 남았다면 통째로 버린다.
if grep -F -q -f "$SECRET" "$CLEAN"; then
    echo "[guard] 마스킹 후에도 비밀값이 남아 있다 — 출력을 버린다"
    exit %(rc_leak)d
fi

cat "$CLEAN"
exit $rc
"""


class FlinkSqlResult(NamedTuple):
    """`sql-client` 실행 결과. 자산이 그대로 메타데이터에 쓴다."""

    exit_code: int
    logs: str
    redaction_ok: bool


class FlinkSessionResource(dg.ConfigurableResource):
    """Flink 세션 클러스터를 띄우고, SQL 배치 잡을 돌리고, 내린다."""

    kube_context: str = KUBE_CONTEXT
    namespace: str = NAMESPACE
    manifest_path: str = SESSION_MANIFEST
    poll_interval_s: int = 5
    # JM 기동 대기. 이미지 pull(`imagePullPolicy: Always`)이 포함될 수 있어
    # 넉넉히 잡는다.
    startup_timeout_s: int = 600
    # SQL 잡 실행 대기. 배치 잡은 `table.dml-sync = true`라 완료까지 블록된다.
    job_timeout_s: int = 1800
    teardown_timeout_s: int = 300

    def load_kube_auth(self) -> str:
        """인증 경로를 로드하고 **실제로 쓴 경로 이름**을 돌려준다.

        in-cluster(ServiceAccount)를 먼저 시도하고, 없으면 호스트 kubeconfig로 떨어진다.
        🔴 반환값을 로그에 쓴다. `kube_context`를 그대로 찍으면 in-cluster에서 쓰이지도
        않은 값을 로그가 사실처럼 말한다(원칙 7 — 값은 맞는데 라벨이 틀린 경우).
        """
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config(context=self.kube_context)
            return f"kubeconfig:{self.kube_context}"
        else:
            return "in-cluster"

    def _custom_api(self) -> client.CustomObjectsApi:
        self.load_kube_auth()
        return client.CustomObjectsApi()

    def _core_api(self) -> client.CoreV1Api:
        self.load_kube_auth()
        return client.CoreV1Api()

    def load_manifest(self) -> dict:
        """커밋된 FlinkDeployment 매니페스트를 읽는다(단일 출처)."""
        with open(self.manifest_path, encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    def ensure_configmaps(self) -> None:
        """세션 클러스터가 마운트하는 ConfigMap 2종의 존재를 **확인만** 한다.

        만들지 않는다 — 정본은 레포의 `k8s/flink/*.yaml`이고, 여기서 만들면
        클러스터 상태가 레포와 갈린다. 없으면 즉시 죽는 편이 낫다:
        JM 파드가 `CreateContainerConfigError`로 뜨지 않는데, 그 증상은
        "타임아웃"으로 보여 원인 추적이 오래 걸린다.
        """
        # 이름별로 조회하지 않고 **한 번 나열해 차집합**을 낸다 — API 왕복이 이름 수만큼
        # 늘지 않고, 404를 예외로 받아 루프에서 처리하는 구조도 사라진다.
        present = {
            item.metadata.name
            for item in self._core_api()
            .list_namespaced_config_map(self.namespace)
            .items
        }
        missing = [name for name in REQUIRED_CONFIGMAPS if name not in present]
        if missing:
            raise dg.Failure(
                description=(
                    f"필수 ConfigMap 부재: {', '.join(missing)} — "
                    "FlinkDeployment의 podTemplate이 volume으로 참조하므로 "
                    "JM 파드가 뜨지 않는다. `kubectl apply -f "
                    "k8s/flink/iceberg-batch-job.yaml "
                    "-f k8s/flink/iceberg-stream-job.yaml` 를 먼저 적용한다."
                )
            )

    def _find_jm_pod(self, name: str) -> str | None:
        """JobManager 파드 이름을 라벨로 찾는다(없으면 None)."""
        pods = self._core_api().list_namespaced_pod(
            self.namespace, label_selector=JM_SELECTOR.format(name=name)
        )
        running = [p for p in pods.items if p.status.phase == "Running"]
        return running[0].metadata.name if running else None

    def ensure_session(self, context: dg.AssetExecutionContext) -> str:
        """세션 클러스터를 확보하고 JM 파드 이름을 반환한다.

        🔴 **멱등이다 — 이미 있으면 재사용한다.** `defs/poc/resources.py`의
        `_delete_if_exists()`(매번 지우고 다시 만든다)와 **의도적으로 갈린다**:
        Spark 쪽은 잡 1회 실행이 곧 CR 1개라 재생성이 자연스럽지만, 여기서는 CR이
        **세션 클러스터**라 지웠다 만들면 JM 기동(이미지 pull 포함)이 매 실행 반복된다.
        """
        manifest = self.load_manifest()
        name = manifest["metadata"]["name"]
        co = self._custom_api()

        try:
            co.create_namespaced_custom_object(
                CRD_GROUP, CRD_VERSION, self.namespace, CRD_PLURAL, manifest
            )
            context.log.info("FlinkDeployment 생성: %s", name)
        except ApiException as exc:
            if exc.status != 409:
                raise
            context.log.info("FlinkDeployment 재사용(이미 존재): %s", name)

        waited = 0
        state = ""
        while waited < self.startup_timeout_s:
            obj = co.get_namespaced_custom_object(
                CRD_GROUP, CRD_VERSION, self.namespace, CRD_PLURAL, name
            )
            status = obj.get("status") or {}
            state = status.get("jobManagerDeploymentStatus", "")
            if state in JM_ERROR_STATES:
                raise dg.Failure(
                    description=(
                        f"JobManager 기동 실패: state={state} "
                        f"error={status.get('error')!r}"
                    )
                )
            # 🔴 2축 판정 — 오퍼레이터 상태와 파드 phase를 **함께** 본다.
            #   Spark 쪽에서 오퍼레이터 watch가 죽어 상태가 영구 고착한 전례가 있다
            #   (실측 4시간 32분). 상태 필드 하나로 닫지 않는다.
            pod = self._find_jm_pod(name)
            if state == JM_READY and pod:
                context.log.info("JM 준비 완료: state=%s pod=%s", state, pod)
                return pod
            time.sleep(self.poll_interval_s)
            waited += self.poll_interval_s

        raise dg.Failure(
            description=(
                f"JM 준비 대기 타임아웃({self.startup_timeout_s}s) — "
                f"마지막 state={state!r}. ConfigMap 부재면 "
                "CreateContainerConfigError로 "
                "파드가 아예 뜨지 않는다(ensure_configmaps 참고)."
            )
        )

    def run_sql(self, jm_pod: str) -> FlinkSqlResult:
        """JM 파드 안에서 `sql-client.sh`로 배치 잡을 실행한다.

        크리덴셜 렌더(`envsubst`)·마스킹·삭제가 **전부 파드 안에서** 끝난다
        (§C5 조건 1·3). Dagster로 오는 것은 마스킹을 통과한 stdout뿐이다.
        """
        script = _RUN_SQL_SCRIPT % {
            "secret_env": SECRET_ENV,
            "init_src": SQL_INIT_PATH,
            "job_src": SQL_JOB_PATH,
            "rc_unset": RC_SECRET_UNSET,
            "rc_mech": RC_REDACTION_MECHANISM_DEAD,
            "rc_leak": RC_REDACTION_FAILED,
        }
        resp = stream(
            self._core_api().connect_get_namespaced_pod_exec,
            jm_pod,
            self.namespace,
            container=JM_CONTAINER,
            command=["bash", "-c", script],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        chunks: list[str] = []
        waited = 0.0
        while resp.is_open() and waited < self.job_timeout_s:
            resp.update(timeout=1)
            if resp.peek_stdout():
                chunks.append(resp.read_stdout())
            if resp.peek_stderr():
                chunks.append(resp.read_stderr())
            waited += 1
        exit_code = self._read_exit_code(resp)
        resp.close()

        logs = "".join(chunks)
        redaction_ok = exit_code not in (
            RC_REDACTION_FAILED,
            RC_REDACTION_MECHANISM_DEAD,
            RC_SECRET_UNSET,
        )
        return FlinkSqlResult(exit_code=exit_code, logs=logs, redaction_ok=redaction_ok)

    def _read_exit_code(self, resp: WSClient) -> int:
        """exec의 종료 코드를 ERROR_CHANNEL에서 읽는다.

        🔴 stdout 파싱이 아니라 **별도 채널**이다 — 출력은 마스킹을 거치므로
        거기서 성공 여부를 읽으면 마스킹 로직이 판정에 끼어든다(축이 섞인다).
        성공 시 `{"status":"Success"}`, 실패 시 `details.causes`에 `ExitCode`가 온다.
        """
        raw = resp.read_channel(ERROR_CHANNEL)
        if not raw:
            # 채널이 비었다 = 종료 상태를 못 받았다. 성공으로 읽지 않는다(fail-closed).
            return -1
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return -1
        if payload.get("status") == "Success":
            return 0
        for cause in (payload.get("details") or {}).get("causes") or []:
            if cause.get("reason") == "ExitCode":
                try:
                    return int(cause.get("message", "-1"))
                except (TypeError, ValueError):
                    return -1
        return -1

    def jm_logs(self, jm_pod: str, tail_lines: int = 200) -> str:
        """JM 파드 로그를 읽는다(진단용, teardown 전에 회수한다).

        `_preload_content=True`(기본)면 클라이언트가 본문을 bytes의 **repr 문자열**로
        돌려줘 개행이 리터럴로 남는다(UI에서 한 줄로 뭉침). 원본 바이트를 직접 받는다.
        """
        try:
            resp = self._core_api().read_namespaced_pod_log(
                jm_pod,
                self.namespace,
                container=JM_CONTAINER,
                tail_lines=tail_lines,
                _preload_content=False,
            )
            return resp.data.decode("utf-8", errors="replace")
        except ApiException:
            return ""

    def teardown(self, context: dg.AssetExecutionContext) -> None:
        """세션 클러스터를 내린다(회수 규율 — 자산의 `finally`가 부른다).

        🔴 **실패해도 던지지 않는다.** 여기서 예외가 나면 원래의 실패 원인을 덮어
        진단이 어려워진다. 회수 실패는 로그로 알리고 원인 예외를 그대로 올려보낸다.
        """
        try:
            manifest = self.load_manifest()
            name = manifest["metadata"]["name"]
            co = self._custom_api()
            co.delete_namespaced_custom_object(
                CRD_GROUP, CRD_VERSION, self.namespace, CRD_PLURAL, name
            )
            waited = 0
            while waited < self.teardown_timeout_s:
                try:
                    co.get_namespaced_custom_object(
                        CRD_GROUP, CRD_VERSION, self.namespace, CRD_PLURAL, name
                    )
                except ApiException as exc:
                    if exc.status == 404:
                        context.log.info("FlinkDeployment 회수 완료: %s", name)
                        return
                time.sleep(self.poll_interval_s)
                waited += self.poll_interval_s
            context.log.warning(
                "FlinkDeployment 회수 대기 타임아웃(%ss): %s — "
                "`kubectl get flinkdeployment`로 잔존을 확인한다.",
                self.teardown_timeout_s,
                name,
            )
        except ApiException as exc:
            if exc.status == 404:
                return
            context.log.warning("FlinkDeployment 회수 실패: %s", exc)
        except OSError as exc:
            context.log.warning("매니페스트를 읽지 못해 회수를 건너뛴다: %s", exc)


@dg.definitions
def flink_resources() -> dg.Definitions:
    """Flink 전용 리소스를 등록한다(load_defs가 수집).

    🔴 `@dg.definitions`는 **자산 모듈과 같은 파일에 두면 안 된다** — 그 모듈의 정의가
    이 함수 반환값으로 대체돼 모듈 스코프 `@asset`이 조용히 누락된다(2026-08-18 실측).
    """
    return dg.Definitions(
        resources={"flink_session": FlinkSessionResource()},
    )
