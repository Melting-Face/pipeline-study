# Flink를 K8s 위에 올리며 — 막힌 층이 다르면 같은 처방이 안 듣는다

> 학습 노트. 오퍼레이터로 Flink 세션 클러스터를 띄우고 Iceberg 테이블을 읽고 쓰며 배운 것.
> 결론부터 쓰면 — **막힌 자리를 「권한」·「버전」 같은 한 단어로 묶는 순간 처방이 빗나간다.**

## 이 글이 다루는 시스템

데이터셋 여러 종 — 일부는 인증 절차가 걸린 연구용이다 — 을 같은 방식으로 적재·변환하는
개인 학습용 데이터 파이프라인이다. 흐름은 한 줄이고, Flink가 붙는 자리는 가운데다.

```text
원천 파일 ─▶ 적재 ─▶ 테이블(bronze) ─▶ 변환(silver/gold) ─▶ 분석
                          └─ 이 테이블을 Spark와 Flink가 함께 읽고 쓴다
```

낱말 셋만 먼저 정한다. 나머지는 처음 나오는 자리에서 정의한다.

- **Iceberg** — 오브젝트 스토리지에 흩어진 파일 더미를 *테이블*처럼 보이게 하는
  **테이블 형식**이다. 스냅샷·스키마·파일 목록을 메타데이터로 들고 있어 여러 엔진이
  같은 테이블을 공유한다. 테이블 목록은 관계형 DB에, 데이터 파일은 S3 호환 스토리지에.
- **오퍼레이터** — 쿠버네티스에 *새 종류의 오브젝트*와 그것을 돌보는 컨트롤러를 더하는
  확장이다. `FlinkDeployment` 한 장을 적으면 JobManager·TaskManager 파드가 생긴다.
- **선언 등급** — 이 노트가 자기 신뢰도를 매기는 말이다. **저장소에 선언이 커밋돼
  있다**까지가 참이고, **클러스터에서 그 값을 받아 봤다**는 별개 축이다. 둘을 가르지
  않으면 "배포돼 있다"가 매니페스트를 세는지 파드를 세는지 모르게 된다.

**이 잡이 하는 일.** 배치는 Spark, 스트림은 Flink로 역할을 갈랐다. Flink가 맡은 것은
**같은 Iceberg 테이블을 읽어 다시 Iceberg에 쓰는 것**이다 — 배치 경로는 한 번 읽고
끝이고, 스트림 경로는 소스에 새 스냅샷이 생기면 잡이 살아 있는 채로 따라 붙는다.

**제출 방식이 이 노트의 전제를 정한다.** 잡을 jar로 빌드하지 않는다. SQL 파일을
**ConfigMap**(설정 텍스트를 담는 쿠버네티스 오브젝트)으로 만들어 JobManager 파드에
마운트하고 그 안에서 `sql-client.sh`로 실행한다. 아래에 `.yaml`인데 내용이 SQL인
블록이 계속 나오는 이유다 — ConfigMap 껍데기 안의 SQL이다.

파드가 쓰는 것은 공식 이미지가 아니라 **직접 구운 러너 이미지**이고 — Iceberg
런타임과 몇몇 의존을 미리 넣어 둔 것이다 — 이 노트 실패의 절반이 거기서 나온다.
회수를 자동화한 자리에는 **Dagster**(파이썬으로 파이프라인을 정의하는 오케스트레이터)가
있다. 아래 코드의 `자산`·`context`·`flink_session`은 전부 Dagster 어휘다.

그래서 이 노트의 실패는 대부분 **잡 로직이 아니라 그 주변**에서 나왔다 — 오퍼레이터의
권한, 이미지에 든 클래스, 아티팩트를 해석하는 층, 회수 순서.
**인용은 전부 저장소 파일에서 왔고 실행 출력은 지어내지 않았다.** 관측하지 못한
것은 그렇다고 적었다.

## 세션 클러스터는 잡이 없어도 산다

배치 엔진 쪽에서는 잡을 제출하면 driver가 뜨고, 잡이 끝나면 driver와 그 UI가 함께
사라진다. 「끝났다」와 「자원이 회수됐다」가 같은 사건이다.

세션 클러스터는 그렇지 않다. 그리고 그 차이를 만드는 것은 **선언에 무엇이 없는가**다.

```yaml
# k8s/flink/flinkdeployment-session.yaml — `spec.job` 블록이 없다
kind: FlinkDeployment
spec:
    image: <러너 이미지>
    flinkVersion: v2_1
    serviceAccount: flink
    # ← 여기에 `job:`이 없으면 세션 클러스터다.
    #   JobManager가 상주하고, 잡은 나중에 붙인다.
    jobManager:
        resource: {...}
    taskManager:
        resource: {...}
```

같은 파일의 주석이 그 대비를 못 박아 둔다 — *"Spark의 driver UI가 잡 종료와 함께
사라지는 것과 대비된다."*

```text
배치 엔진      잡 제출 ─▶ driver 생성 ─▶ 잡 종료 ─▶ driver 소멸
세션 클러스터  JM 상주 ─▶ 잡 제출 ─▶ TM 생성 ─▶ 잡 종료 ─▶ TM 소멸 · JM 상주
```

이 차이가 회수 규율을 통째로 바꾼다. 배치 쪽은 잡이 끝나면 저절로 회수되지만,
세션 쪽은 **내가 내리지 않으면 계속 먹는다.** 화면에는 아무 이상이 없다 —
잡 목록은 비어 있고 파드는 `Running`이다.

그래서 회수를 사람의 규율에 맡기지 않고 오케스트레이터 자산이 지게 했다.
`dagster_project/defs/flink/assets.py`의 뼈대가 이렇다.

```python
try:
    jm_pod = flink_session.ensure_session(context)
    result = flink_session.run_sql(jm_pod)
    # teardown 전에 진단 근거를 회수한다 — 순서가 규칙이다.
    jm_logs = flink_session.jm_logs(jm_pod)
    ...
finally:
    # 회수 규율을 코드가 진다 — 실패해도 내린다.
    flink_session.teardown(context)
```

**규율을 문서에 적어 두는 것과 그것을 트리거하는 주체가 있는 것은 다른 축이다.**
이 저장소에는 규약이 문서에 있는데도 상주 컴퓨트가 반나절 넘게 샌 기록이 있고,
발견 경로는 성능 이상이 아니라 "안 쓰는 것 정리"였다.

## 에러를 내고 멈춘 것 ① — 감시 범위를 좁혔더니 잡 생성이 거부됐다

오퍼레이터에 감시 네임스페이스를 지정했다. 지정하지 않으면 잡용 서비스 계정과 Role이
오퍼레이터 네임스페이스에만 생겨 정작 잡이 뜨는 곳에서 파드가 안 뜬다.

그런데 범위를 지정하는 순간 오퍼레이터 자신의 권한도 네임스페이스로 좁아진다.
`k8s/flink/flink-operator-webhook-rbac.yaml`의 주석이 그 경로를 기록해 뒀다.

```text
mutating webhook의 `FlinkMutator.mutateSessionJob`은 대상 FlinkDeployment를
클러스터 스코프로 list 한다:
    /apis/flink.apache.org/v1beta1/flinkdeployments?resourceVersion=0
→ 403으로 webhook이 실패하고 `FlinkSessionJob` 생성 자체가 거부된다.
```

📌 **`FlinkSessionJob`은 `FlinkDeployment`와 짝인 별개 오브젝트다.** 앞엣것이 *잡 한 건*,
뒤엣것이 *그 잡이 돌 클러스터*다. 세션 클러스터를 띄워 두고 잡만 따로 붙이는 표준
경로이고, 나는 결국 이 경로를 **버리고** JM 파드 안에서 `sql-client.sh`를 쓴다.
왜 버렸는지는 두 절 뒤에 나온다 — 그러니 이 절은 *가 보고 막힌 길*의 첫 관문이다.

처방은 넓은 ClusterRole을 새로 주는 것이 아니라 **해당 리소스 한정 · 읽기 세 동사**만
클러스터 스코프로 되돌리는 것이다. 규칙 본문은 이 세 줄이 전부다.

```yaml
# k8s/flink/flink-operator-webhook-rbac.yaml — 최소권한으로 뚫은 구멍
kind: ClusterRole                       # 웹훅이 클러스터 스코프로 보므로 Role로는 안 된다
rules:
    - apiGroups: ["flink.apache.org"]
      resources: ["flinkdeployments"]   # 이 종류만
      verbs: ["get", "list", "watch"]   # 읽기만. 쓰기·삭제는 차트 Role 그대로
```

파일에는 이 뒤에 오퍼레이터 서비스 계정을 묶는 `ClusterRoleBinding`이 한 장 더 있다.
**권한은 언제나 두 장**이다 — 무엇을 허용하는지와 누구에게 주는지.

## 그럴듯해서 속은 것 ① — DDL은 되고 쿼리만 안 되니 권한을 의심하지 않았다

같은 좁히기가 **두 번째 구멍**을 만든다. 이쪽은 오퍼레이터가 아니라 **잡의 권한**이고,
증상이 훨씬 고약하다.

| 무엇을 했나 | 결과 | 어디까지 갔나 |
| --- | --- | --- |
| 카탈로그 생성 DDL | 통과 | JM 안에서 끝난다 |
| 데이터베이스·테이블 목록 조회 | 통과 | JM 안에서 끝난다 |
| 실제 쿼리 실행 | **거부** | REST 서비스를 조회해야 한다 |

**두 구멍의 실패 메시지를 나란히 놓으면** 왜 하나만 헷갈리는지가 보인다.

```text
① 웹훅 구멍 — 잡을 만드는 순간 막힌다. 아무것도 시작되지 않는다
   403 Forbidden  (admission webhook 실패)

② 잡 권한 구멍 — 앞의 것들이 전부 통과한 뒤에 막힌다
   services "<세션 이름>-rest" is forbidden
```

첫째는 정직하다. 아무것도 안 되니 바로 권한을 본다.
둘째는 **정상 경로가 여럿 통과한 뒤**에 온다.
`k8s/flink/flink-workload-rbac.yaml`의 주석이 그 이유를 짚는다.

```text
JobManager 파드 안에서 `sql-client.sh`로 잡을 제출하면 Flink의 Kubernetes
클라이언트가 REST 서비스(`<name>-rest`)를 조회해 접속 주소를 찾는다 →
권한이 없으면 DDL은 되는데 쿼리 실행만 실패한다.
메타데이터 조회(SHOW DATABASES/TABLES)는 JM 내부에서 끝나 통과하므로
증상이 헷갈린다.
```

이쪽 보완도 최소권한이다. 클러스터 스코프가 아니라 **잡 네임스페이스 한정 Role**이다.

```yaml
# k8s/flink/flink-workload-rbac.yaml
kind: Role                              # ClusterRole이 아니다
rules:
    - apiGroups: [""]
      resources: ["services"]           # 이 종류만
      verbs: ["get", "list"]
```

나는 앞선 성공들을 "권한은 됐다"는 근거로 읽고 SQL을 먼저 의심했다.
카탈로그 설정, 테이블 이름, 타입 정합을 차례로 봤다. **권한은 마지막에 봤다.**

⇒ **부분 성공은 성공의 부분집합이 아니라 별개의 증상이다.** 무엇이 통과했는지가
아니라 **통과한 것들이 같은 자원을 쓰는지**를 봐야 했다. 여기서는 아니었다 —
앞의 둘은 JM 안에서 끝나고 뒤의 하나만 API 서버로 나간다.

## 에러를 내고 멈춘 것 ② — 허용목록을 통과했는데 그다음 층에서 막혔다

**여기서 말하는 jar가 무엇인지 먼저 밝힌다.** 앞에서 "잡을 jar로 빌드하지 않는다"고
한 것과 모순이 아니다 — `FlinkSessionJob`은 **잡을 jar로 받는 것 말고 입력이 없다.**
표준 경로를 타려면 무엇이든 하나를 줘야 했고, 나는 러너 이미지에 이미 있는 jar를
`local://`로 가리켰다. 외부에서 받지 않는 편이 공급망상 안전하다는 판단이었다.

먼저 막힌 것은 웹훅의 아티팩트 허용목록이다. 그건 오퍼레이터 값으로 열 수 있었다.

```yaml
# k8s/flink/operator-values.yaml — 기본값은 [https]뿐이라 local이 거부된다
kubernetes.operator.user.artifacts.allowed-schemes: local
```

같은 파일이 **https를 뺀 이유**도 적어 둔다 — 남겨두면 배포 선언에 URL 한 줄로
런타임 외부 jar fetch 경로가 열린다. 그런데 허용목록을 통과시켜도 죽었다.

```text
UnsupportedFileSystemSchemeException
```

층이 둘이었다. 허용목록은 **"이 스킴을 받아도 되는가"**, 그다음 단계는
**"이 스킴을 실제로 열 수 있는가"** 를 본다. 세션 잡은 오퍼레이터가 jar를 받아
JobManager에 **올리는** 구조라 읽어올 수 있는 스킴이 필요한데, `local://`은
*이미 그 파드 안에 있다*는 뜻이라 올릴 대상이 되지 못한다.

```text
세션 모드       클러스터가 먼저 뜬다 → 잡을 나중에 올린다 → jar를 「받아서」 올려야 한다
application 모드 잡이 곧 클러스터다   → 이미지가 이미 jar를 갖고 있다 → local:// 가 맞는다
```

⇒ 허용목록을 고치는 처방이 통하지 않았던 이유는 **처방이 틀려서가 아니라
막힌 층이 거기가 아니어서**다. 같은 값이 두 층에서 각각 판정된다.
그리고 이 막다른 길이 내가 `sql-client.sh` 경로로 간 이유다 — 잡이 전부 SQL인데
그걸 jar로 감싸는 것은 수단이 목적을 정하는 꼴이었다.

## 에러를 내고 멈춘 것 ③ — 이미지에 없는 것은 엔진마다 다르다

카탈로그를 만들려는데 클래스를 못 찾았다. `k8s/flink/Dockerfile.flink-runner`의
주석이 그 지점을 정확히 적어 뒀다.

```text
Flink 이미지에는 Hadoop 클래스가 **없다**(Spark 이미지와 다른 지점).
Iceberg의 FlinkCatalogFactory.clusterHadoopConf()가
org.apache.hadoop.conf.Configuration을 로드하므로
CREATE CATALOG 단계에서 ClassNotFoundException으로 죽는다.
```

여기서 낱말 셋이 필요하다. **uber jar**는 의존을 전부 한 파일에 욱여넣은 것,
**shading**은 그렇게 넣으면서 클래스 이름을 바꿔 남의 것과 안 부딪히게 하는 것,
그 이름 바꾸기가 **relocate**다. 같은 클래스가 두 벌 올라가면 JVM은 먼저 잡히는
쪽을 쓰므로, **누가 어떻게 감쌌는지가 런타임 동작을 정한다.**

그래서 오래된 통짜 uber jar 대신 **배치 러너와 같은 계열의 shaded 클라이언트**를 골랐다.

```dockerfile
# k8s/flink/Dockerfile.flink-runner — 레거시 uber jar 대신 같은 계열 shaded 클라이언트
ARG HADOOP_VERSION=3.3.4
RUN cd /opt/flink/lib; \
    curl -fLO ".../hadoop-client-api-${HADOOP_VERSION}.jar"; \
    curl -fLO ".../hadoop-client-runtime-${HADOOP_VERSION}.jar"
```

계열을 맞춘 것은 **예방이지 관측이 아니다** — 두 엔진이 서로 다른 Hadoop 계열로
같은 카탈로그에 붙었을 때 실제로 값이 갈리는지는 이 프로젝트가 확인하지 않았다.
근거로 든 것은 바로 아래 절의 **클래스 충돌**뿐이고, 그건 값이 아니라 기동 실패다.

⇒ **"엔진 A에서 됐으니 엔진 B에서도 된다"의 실제 근거는 대개 이미지다.**
엔진의 성질이 아니라 그 이미지에 무엇이 들었는가였다.

## 에러 없이 깨진 것 ① — 같은 jar를 어디에 두느냐가 클래스로더를 가른다

체크포인트를 오브젝트 스토리지에 쓰려면 해당 파일시스템 jar가 필요하다. 베이스
이미지에 이미 있지만 기본 경로에서 자동으로 올라오지 않아 옮겨야 한다.
**라이브러리 디렉터리가 아니라 플러그인 디렉터리에 둔다.** 이유는 그 jar의 내용물이고,
Dockerfile 주석에 **세어 본 값**이 남아 있다.

```text
이 jar는 클래스를 relocate하지 않고 `org/apache/hadoop/fs/s3a/*` + AWS SDK **v1**
(`com/amazonaws/*` 3887개, SDK v2 0개)을 그대로 품는다.
lib/에 두면 같은 클래스로더에서 hadoop-client-api/runtime 3.3.4·
iceberg-aws-bundle(SDK v2)와 충돌한다.
plugins/ 하위는 Flink가 **격리 클래스로더**로 올려 그 충돌이 생기지 않는다.
```

그래서 복사 한 줄이 아니라 **경로가 규칙**이다.

```dockerfile
# k8s/flink/Dockerfile.flink-runner — lib/가 아니라 plugins/<name>/ 이어야 한다
RUN set -eux; \
    mkdir -p /opt/flink/plugins/s3-fs-hadoop; \
    cp /opt/flink/opt/flink-s3-fs-hadoop-*.jar /opt/flink/plugins/s3-fs-hadoop/; \
    test -n "$(ls -A /opt/flink/plugins/s3-fs-hadoop)"; \
    chown -R flink:flink /opt/flink/plugins/s3-fs-hadoop
```

`test -n` 줄이 이 노트의 결을 그대로 보여준다. 버전 핀이 **글롭**이라 안 맞으면
조용히 0개를 복사할 수 있어, **"복사됐다"를 값으로 확인**하고 빌드를 이어간다.

여기서 정직하게 적어 둘 것이 있다. **나는 이 클래스 충돌을 겪지 않았다.** jar 안의
클래스를 세어 보고 피했을 뿐이다. 그러니 이건 관측이 아니라 **회피**이고,
라이브러리 디렉터리에 두면 정확히 어떻게 깨지는지는 이 노트가 답하지 못한다.

## 에러 없이 깨진 것 ② — 배치 파일을 복사하면 딸려오는 한 줄

배치 잡 SQL에는 동기 실행 설정이 있다. 완료를 봐야 하기 때문이다.

```sql
-- k8s/flink/iceberg-batch-job.yaml — 배치는 완료를 봐야 한다
SET 'execution.runtime-mode' = 'batch';
SET 'sql-client.execution.result-mode' = 'tableau';
SET 'table.dml-sync' = 'true';          -- 제출만 하고 빠지지 않게 한다
```

스트리밍 잡을 만들 때 이 파일을 복사했다면 그 줄이 그대로 따라온다.
그런데 스트리밍 INSERT는 **끝나지 않는다.** 스트림 쪽 SQL에는 그래서 그 줄이 없고,
**없다는 사실 자체가 주석으로 적혀 있다.**

```sql
-- k8s/flink/iceberg-stream-job.yaml
-- `table.dml-sync`는 켜지 않는다. 스트리밍 INSERT는 끝나지 않으므로
-- 동기 실행하면 sql-client가 영영 반환하지 않고 kubectl exec 세션이 물린다.
-- ⇒ 배치 파일을 복사해 만들 때 가장 실수하기 쉬운 한 줄이다. 없는 것이 정상이다.
SET 'execution.runtime-mode' = 'streaming';
SET 'table.local-time-zone' = 'UTC';
```

⇒ **「없어야 정상인 것」은 리뷰에서 가장 안 보인다.** 있는 줄은 읽히지만 빠진 줄은
읽히지 않는다. 그래서 그 자리에 **왜 없는지를 주석으로 남기는 것**이 유일한 방어였다.

## 순서가 규칙이 된 자리 둘

둘 다 "하긴 했는데 순서가 틀렸다" 형태다. 증상은 정반대다.

**ⓐ 취소보다 삭제가 먼저면 삭제가 멈춘다.** `k8s/flink/usgs-water-stream-job.yaml`에
순서와 그 근거가 함께 있다.

```text
회수 — 이 잡들은 끝나지 않는다. 순서가 규칙이다:
  1) flink cancel <job-id>
  2) kubectl delete -f flinkdeployment-session.yaml

순서를 뒤집으면 오퍼레이터가
  CLEANUPFAILED | The session cluster has non terminated jobs
  ... that should be cancelled first
로 15초마다 재시도하며 CR이 DELETING에 고착한다.
```

⚠️ 같은 주석이 한 줄 더 붙인다 — **이것은 오퍼레이터 고장이 아니다.**
정확한 진단을 내며 기다리는 정상 동작이다. 고장으로 읽으면 엉뚱한 데를 판다.

**ⓑ 회수보다 로그 회수가 먼저여야 한다.** 이쪽은 에러가 없다. 회수는 성공하고,
나중에 원인을 보려 할 때 볼 것이 없을 뿐이다. 그래서 자산 코드가 순서를 강제한다 —
위 §세션 클러스터의 `jm_logs` 호출이 `teardown`보다 앞에 있는 것이 그 이유다.

체크포인트에도 같은 축이 있다. 보존 설정 기본값이 **취소 시 삭제**라, 복구를
검증하려면 취소 전에 경로를 확보해야 한다. 지운 뒤에는 되돌릴 수 없다.

## 그럴듯해서 속은 것 ② — 버전을 내가 고른다고 생각했다

오퍼레이터의 CRD가 더 높은 엔진 버전을 값으로 받아줬다. 그래서 그 버전을 쓸 수
있다고 읽었다. Dockerfile 머리말이 그 오독을 정면으로 적어 뒀다.

```dockerfile
# k8s/flink/Dockerfile.flink-runner
#   - `iceberg-flink-runtime-<flinkMinor>` 아티팩트는 2.1까지만 존재한다(2.2는 404).
#     그래서 Flink는 2.1.3으로 내린다. 오퍼레이터 CRD enum의 `v2_2`를 쓸 수 있어도
#     Iceberg가 못 따라오면 의미가 없다 — 엔진 최신판이 아니라 짝이 맞는 조합을 고른다.
FROM flink:2.1.3-java17
ARG ICEBERG_VERSION=1.11.0
ARG FLINK_MINOR=2.1
```

⇒ **버전을 정하는 것은 엔진이 아니라 커넥터였다.** CRD가 값을 받아준다는 것은
그 조합의 존재를 보증하지 않는다.

같은 자리에서 하나 더 걸렸다. 핀해 둔 차트 URL이 **404**가 됐다. 배포처가
현행 릴리스만 보관하기 때문에, 어제 되던 주소가 오늘 사라진다.
핀은 재현성을 위한 것인데 **그 핀이 가리키는 대상이 사라질 수 있다**는 축을
나는 세지 않았다. 그래서 설치 시점에 목록을 대조하고 다시 핀하는 절차를 넣었다.

```shell
# 설치 전에 현행 목록과 대조한다 — 핀이 가리키는 대상이 아직 있는지부터 본다
curl -s https://downloads.apache.org/flink/ | grep flink-kubernetes-operator
```

## 그럴듯해서 속은 것 ③ — 점검이 초록인데 범위 밖에 남아 있었다

SQL 클라이언트는 **실행문을 그대로 화면에 되비춘다.** 저장소의 여러 파일이 같은
문장을 반복해 적어 둘 만큼 이게 설계를 좌우했다.

```text
sql-client는 실행문을 그대로 echo해 터미널·로그에 평문이 남는다.
```

그래서 둘을 한다. S3 키는 DDL에서 빼 **표준 환경변수**로 넣고, 그래도 DDL에 들어갈
수밖에 없는 접속 정보는 **파일을 갈라** 크리덴셜 0인 잡 파일의 출력만 관측한다.
렌더와 정리는 파드 안에서 한 벌로 끝낸다.

```shell
# k8s/flink/iceberg-stream-job.yaml 주석의 절차 — 호스트로 크리덴셜을 가져오지 않는다
kubectl exec -n <잡-ns> <JM 파드> -- sh -c '
  umask 077
  envsubst < <초기화 SQL> > /tmp/init.sql
  chmod 600 /tmp/init.sql
  /opt/flink/bin/sql-client.sh --history /tmp/sql-history \
    -i /tmp/init.sql -f <잡 SQL>
  rc=$?; rm -f /tmp/init.sql /tmp/sql-history; exit $rc
'
```

`--history`를 **버릴 경로로 지정**하는 것과 `rm`을 `&&`가 아니라 `;`로 잇는 것이
둘 다 의도다. 뒤엣것은 실패했을 때도 정리가 돌아야 하기 때문이다.

그 규칙을 지켰고, 유출 점검도 돌렸고, 점검이 지정한 경로는 셋 다 멀쩡했다.
그런데 **그 셋에 포함되지 않은 자리**에 평문이 남아 있었다.

📌 **무엇이 어디에 남았는지는 일부러 적지 않는다.** 구체 위치와 건수는 공개 범위
밖이라 가린 것이고, **빠뜨린 것이 아니다.** 이 절이 전하려는 것은 그 자리가
어디였나가 아니라 **점검이 초록인 채로 틀리는 구조**다.

⇒ **관측 경로가 살아 있다는 것과 관측 범위가 충분하다는 것은 다른 축이다.**
경로 생존은 "이 검사가 실제로 돌았다"를 말할 뿐 "볼 곳을 다 봤다"를 말하지 않는다.
숨김 파일과 홈 디렉터리를 범위에 넣는 것이 처방이고, 교훈은 **범위를 값으로 적는
것**이다 — 어디를 봤는지 적지 않은 점검은 다음 사람이 넓힐 수도 없다.

## 확인 방법

⚠️ **명령과 판정 기준까지다.** 이 프로젝트의 Flink는 **선언 등급**(§이 글이 다루는
시스템)이라 여기 적은 것 중 클러스터에서 값을 받아 본 것은 일부뿐이다.
그래서 **기대 출력을 박제하지 않았다** — 무엇이 나와야 통과인지만 단다.

```shell
# 1. 층이 살아 있나 — CRD와 컨트롤러는 따로 온다
kubectl get crd | grep flink
kubectl get deploy -n <오퍼레이터-ns>
#    통과: CRD가 나오고 컨트롤러의 READY가 채워져 있다. CRD만 있으면 아무 일도 없다

# 2. 잡이 없을 때 무엇이 남나
kubectl get pods -l component=jobmanager     # 통과: 남아 있다
kubectl get pods -l component=taskmanager    # 통과: 비어 있다
#    ⇒ 잡 제출 뒤 같은 명령으로 **뜨는 것까지** 봐야 그 0건이 증거가 된다.
#      음성 대조 없이는 셀렉터 오타와 구분되지 않는다

# 3. 권한 두 구멍 — 서로 다른 축이다. 하나가 통과해도 다른 하나를 넘겨짚지 않는다
kubectl auth can-i list <배포 CRD 복수형> --all-namespaces \
    --as=system:serviceaccount:<오퍼레이터-ns>:<오퍼레이터 SA>
#    통과: yes  ← no면 잡 생성이 403으로 거부된다
kubectl auth can-i get services -n <잡-ns> \
    --as=system:serviceaccount:<잡-ns>:<잡 SA>
#    통과: yes  ← no면 DDL만 되고 쿼리 실행에서 forbidden이 난다

# 4·5. 이미지에 클래스가 들었나 · 격리 클래스로더로 올라갔나
kubectl exec <JM 파드> -- ls /opt/flink/lib      # 통과: hadoop-client-*·iceberg-* 가 보인다
kubectl exec <JM 파드> -- ls /opt/flink/plugins  # 통과: s3-fs-hadoop 이 있고 비어 있지 않다
#    ⚠️ lib/에 같은 jar가 함께 있으면 5가 통과해도 4가 충돌원이다 — 양쪽을 다 본다
```

## 출처

아래는 저장소가 1차 출처로 등재해 둔 것을 옮긴 것이고, **이 노트를 쓰며 다시
열어보지는 않았다** — 확인 시점은 등재 시점이지 작성 시점이 아니다. 표에 없는 것은
「없다」가 아니라 이 노트가 고르지 않았거나 확인하지 못한 것이다.

| 문서 | 링크 |
| --- | --- |
| Flink 문서 | https://flink.apache.org/documentation/flink-stable/ |
| Flink 다운로드·릴리스 | https://flink.apache.org/downloads/ |
| Iceberg Flink 커넥터 | https://iceberg.apache.org/docs/latest/flink/ |
| Maven Central | https://repo1.maven.org/maven2/ |

본문 인용은 전부 **저장소 파일**에서 왔다 — 매니페스트 넷(`flinkdeployment-session` ·
`flink-operator-webhook-rbac` · `flink-workload-rbac` · `operator-values`),
SQL ConfigMap 셋(`iceberg-batch-job` · `iceberg-stream-job` · `usgs-water-stream-job`),
`Dockerfile.flink-runner`, Dagster 자산 코드.

**확인하지 못한 것도 적는다.** 플러그인 디렉터리의 격리 클래스로더 규약은 저장소
안의 실측 메모에 기대고 있고 1차 문서 링크를 고르지 않았다.
라이브러리 디렉터리에 두었을 때의 실제 충돌 증상도 **돌려본 적이 없다.**
오퍼레이터 문서는 **릴리스 버전 문서**를 봐야 한다 — 나이틀리에는 설치된
오퍼레이터가 모르는 필드가 섞인다.

## 그래서 무엇을 배웠나

**막힌 층을 먼저 특정한다.** 이 노트의 실패 중 셋은 「권한」·「경로」·「버전」이라는
한 단어로 묶이는데, 실제로는 각각 두 층에서 따로 판정됐다. 웹훅 권한과 잡 권한,
허용목록과 아티팩트 해석, CRD가 받는 값과 아티팩트의 존재. **같은 단어로 묶으면
한쪽을 고치고 나머지를 고쳤다고 믿게 된다.**

**부분 성공은 가장 비싼 증상이다.** 전부 실패하면 원인을 찾는다. 일부가 통과하면
통과한 쪽을 근거로 삼아 엉뚱한 데를 판다. 두 구멍의 메시지를 나란히 놓고 나서야
차이가 보였다 — 하나는 시작도 못 하게 막고, 하나는 **세 번째 걸음에서** 막는다.

**수명의 기본값이 엔진마다 다르다.** 잡과 함께 사라지는 것이 기본인 쪽에서 오면
상주하는 쪽의 회수를 잊는다. 그리고 회수는 성능으로 드러나지 않는다 —
아무 신호도 없이 자원만 먹는다.

**「했다」의 순서까지가 규칙이다.** 취소와 삭제, 로그 회수와 자원 회수는 둘 다
하긴 했는데 순서가 틀리면 한쪽은 고착하고 다른 쪽은 **아무 증상 없이** 근거만
사라진다. 뒤엣것이 더 위험하다.

**없어야 정상인 것은 주석으로만 남길 수 있다.** 복사해 온 한 줄이 잡을 영영
멈추게 하는데, 빠진 줄은 리뷰에 걸리지 않는다. 이 저장소가 그 자리마다
「없는 것이 정상이다」를 적어 두는 이유다.

**점검의 초록은 범위에 대해 아무 말도 하지 않는다.** 경로가 살아 있음을 확인하는
것과 볼 곳을 다 봤음을 확인하는 것은 다른 일이고, 전자만으로 후자를 읽는 순간
점검은 통과하면서 틀린다.

---

[← 홈으로](Home.md) · [읽는 법](how-to-read.md) · [Spark를 K8s 위로](spark-on-k8s.md) · [오퍼레이터를 둘 것인가](k8s-operators.md) · [스트림 소스를 고르며](streaming-source-choice.md)
