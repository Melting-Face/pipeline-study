"""PoC: Dagster → kind SparkApplication 제출·폴링 리소스.

`dagster-k8s`의 `K8sRunLauncher`는 **run 자체**를 파드로 띄우는 옵션이라 축이 다르다.
현행 run launcher는 `DefaultRunLauncher`(run = daemon 파드 내 서브프로세스)이고,
여기서는 kubernetes 클라이언트로 Spark Operator의 `SparkApplication`(CRD)을
직접 제출하고 상태를 폴링한다. 이 리소스는 in-cluster·호스트 양쪽에서 동작한다
(인증 경로 분기는 `load_kube_auth()` 참조).

상태 판정은 **Apache 오퍼레이터 스펙**을 따른다(Kubeflow와 필드가 다르다):
`status.currentState.currentStateSummary` + `status.stateTransitionHistory`.
성공·실패 모두 최종적으로 `ResourceReleased`로 수렴하므로, 성공 여부는
전이 이력에 `Succeeded`가 있었는지로 판정한다(최종 상태만 보면 구분 불가).

🔴 단, **상태 필드만 믿지 않는다**. 오퍼레이터의 `SparkApplication` watch가 장시간 후
죽어, driver 파드가 `Succeeded`인데도 상태가 `DriverReady`에 영구 고착하는 현상이 있다
(2026-08-19 실측, 최장 4시간 32분). 그때 오퍼레이터 파드는 재시작 0·GC 정상이라
살아 있는 것처럼 보인다. 그래서 **driver 파드 phase를 보조 신호로 함께** 본다
(상세·기각 가설은 conventions/k8s.md §9).
"""

import time
from typing import NamedTuple

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

import dagster as dg
from dagster_project.defs.poc.constants import (
    CRD_GROUP,
    CRD_PLURAL,
    CRD_VERSION,
    KUBE_CONTEXT,
    NAMESPACE,
)

# CRD v1 openAPIV3Schema의 currentStateSummary enum 실측값 중 종료 상태.
# `ResourceReleased`는 성공·실패 공통의 최종 수렴 상태다.
TERMINAL_STATES = frozenset(
    {
        "Succeeded",
        "Failed",
        "SchedulingFailure",
        "DriverStartTimedOut",
        "DriverReadyTimedOut",
        "ExecutorsStartTimedOut",
        "DriverEvicted",
        "TerminatedWithoutReleaseResources",
        "ResourceReleased",
    }
)
SUCCESS_STATE = "Succeeded"

# driver 파드의 종료 phase. 오퍼레이터 상태와 달리 kubelet이 직접 쓰므로
# watch가 죽어도 정확하다 — 상태 고착 시의 탈출 신호로 쓴다.
POD_TERMINAL_PHASES = frozenset({"Succeeded", "Failed"})
POD_SUCCESS_PHASE = "Succeeded"

# driver 파드 이름은 `<app>-<attemptId>-driver`라 attempt마다 바뀐다
# (Kubeflow의 `<app>-driver`와 다르다). 이름을 조립하지 말고 오퍼레이터가 붙이는
# 라벨로 찾는다 — 2026-08-18 실측: `poc-ingest-0-driver`.
DRIVER_SELECTOR = "spark.operator/spark-app-name={name},spark-role=driver"


class SparkRunResult(NamedTuple):
    """SparkApplication 종료 결과(자산이 로그 파싱·메타데이터에 그대로 쓴다)."""

    state: str
    succeeded: bool
    logs: str
    driver_pod: str


class SparkOperatorResource(dg.ConfigurableResource):
    """kind 클러스터의 SparkApplication을 제출하고 종료까지 폴링한다(호스트 실행)."""

    kube_context: str
    namespace: str = "default"
    poll_interval_s: int = 5
    timeout_s: int = 900
    # driver 파드가 종료된 뒤 오퍼레이터 전이를 기다려 주는 유예.
    # 정상 경로의 전이는 파드 종료 후 약 2분이라(2026-08-19 실측) 넉넉히 잡는다.
    # 이 시간을 넘기면 watch가 죽은 것으로 보고 파드 phase로 판정한다.
    pod_terminal_grace_s: int = 180

    def load_kube_auth(self) -> str:
        """인증 경로를 로드하고 **실제로 쓴 경로 이름**을 돌려준다.

        in-cluster(ServiceAccount)를 먼저 시도하고, 없으면 호스트 kubeconfig로 떨어진다.
        한 코드가 두 실행 위치를 지탱한다 — 파드에는 `~/.kube/config`가 없고,
        호스트에는 `/var/run/secrets/...`가 없다.

        🔴 반환값을 로그·메타데이터에 쓴다. `kube_context`를 그대로 찍으면
        in-cluster에서 쓰이지도 않은 값을 로그가 사실처럼 말한다
        (원칙 7 — 값은 맞는데 라벨이 틀린 경우).
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

    def _delete_if_exists(self, co: client.CustomObjectsApi, name: str) -> None:
        # 동일 이름 잔여 오브젝트 제거 후 404까지 대기(멱등 재제출)
        try:
            co.delete_namespaced_custom_object(
                CRD_GROUP, CRD_VERSION, self.namespace, CRD_PLURAL, name
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
        for _ in range(60):
            try:
                co.get_namespaced_custom_object(
                    CRD_GROUP, CRD_VERSION, self.namespace, CRD_PLURAL, name
                )
            except ApiException as exc:
                if exc.status == 404:
                    break
            time.sleep(2)
        # 로그 회수를 위해 driver 파드를 retain하므로(매니페스트 resourceRetainPolicy),
        # 직전 실행분이 cascade 삭제될 때까지 기다린다 — 남아 있으면 옛 로그를 읽는다.
        self._wait_driver_gone(name)

    def _wait_driver_gone(self, name: str) -> None:
        """해당 앱의 driver 파드가 사라질 때까지 대기한다(이미 없으면 즉시 반환)."""
        for _ in range(60):
            if self._find_driver_pod(name) is None:
                return
            time.sleep(2)

    def _find_driver_pod(self, name: str) -> str | None:
        """라벨로 driver 파드 이름을 찾는다(없으면 None)."""
        pods = self._core_api().list_namespaced_pod(
            self.namespace, label_selector=DRIVER_SELECTOR.format(name=name)
        )
        return pods.items[0].metadata.name if pods.items else None

    def _driver_phase(self, name: str) -> str | None:
        """Driver 파드의 phase를 읽는다(없으면 None).

        오퍼레이터 상태와 독립된 신호다 — watch가 죽어도 kubelet이 쓰는 값이라 정확하다.
        """
        pods = self._core_api().list_namespaced_pod(
            self.namespace, label_selector=DRIVER_SELECTOR.format(name=name)
        )
        return pods.items[0].status.phase if pods.items else None

    def submit_and_wait(self, manifest: dict) -> SparkRunResult:
        """SparkApplication을 제출하고 종료까지 폴링한 결과를 반환한다."""
        co = self._custom_api()
        name = manifest["metadata"]["name"]
        self._delete_if_exists(co, name)
        co.create_namespaced_custom_object(
            CRD_GROUP, CRD_VERSION, self.namespace, CRD_PLURAL, manifest
        )

        state = ""
        succeeded = False
        waited = 0
        # driver 파드가 종료 상태로 처음 관측된 시각(유예 기산점). None이면 아직 미종료.
        pod_terminal_at: int | None = None
        while waited < self.timeout_s:
            obj = co.get_namespaced_custom_object(
                CRD_GROUP, CRD_VERSION, self.namespace, CRD_PLURAL, name
            )
            state, succeeded = self._read_state(obj)
            if state in TERMINAL_STATES:
                break

            # watch가 죽으면 상태가 영영 전이하지 않는다(실측 4시간 32분 고착).
            # 파드가 끝났는데 유예까지 전이가 없으면 파드 phase로 판정하고 빠져나온다.
            phase = self._driver_phase(name)
            if phase in POD_TERMINAL_PHASES:
                if pod_terminal_at is None:
                    pod_terminal_at = waited
                elif waited - pod_terminal_at >= self.pod_terminal_grace_s:
                    state = f"{state or 'Unknown'}(watch-stalled,pod={phase})"
                    succeeded = phase == POD_SUCCESS_PHASE
                    break
            else:
                # 재시도로 파드가 다시 뜬 경우 기산점을 되돌린다.
                pod_terminal_at = None

            time.sleep(self.poll_interval_s)
            waited += self.poll_interval_s

        driver_pod = self._find_driver_pod(name)
        logs = self._driver_logs(driver_pod) if driver_pod else ""
        return SparkRunResult(state, succeeded, logs, driver_pod or "not-found")

    def _read_state(self, obj: dict) -> tuple[str, bool]:
        """현재 상태와 성공 여부를 읽는다(성공은 전이 이력에서 판정)."""
        status = obj.get("status") or {}
        state = (status.get("currentState") or {}).get("currentStateSummary", "")
        history = status.get("stateTransitionHistory") or {}
        succeeded = state == SUCCESS_STATE or any(
            entry.get("currentStateSummary") == SUCCESS_STATE
            for entry in history.values()
        )
        return state, succeeded

    def _driver_logs(self, driver_pod: str) -> str:
        """Driver 파드 로그를 읽는다(파드 부재·권한 오류면 빈 문자열).

        `_preload_content=True`(기본)면 클라이언트가 본문을 bytes의 **repr 문자열**로
        돌려줘 개행이 리터럴로 남는다(2026-08-18 실측 — Dagster UI에서 한 줄로 뭉침).
        원본 바이트를 직접 받아 디코드한다.
        """
        try:
            resp = self._core_api().read_namespaced_pod_log(
                driver_pod, self.namespace, _preload_content=False
            )
            return resp.data.decode("utf-8", errors="replace")
        except ApiException:
            return ""


@dg.definitions
def poc_resources() -> dg.Definitions:
    """PoC 전용 리소스를 등록한다(load_defs가 수집).

    `@dg.definitions`는 **자산 모듈과 같은 파일에 두면 안 된다** — 그 모듈의 정의가
    이 함수 반환값으로 대체돼 모듈 스코프 `@asset`이 수집되지 않는다(2026-08-18 실측).
    """
    return dg.Definitions(
        resources={
            "spark_operator": SparkOperatorResource(
                kube_context=KUBE_CONTEXT, namespace=NAMESPACE
            ),
        }
    )
