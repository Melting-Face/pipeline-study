# CNPG — 카탈로그·메타 Postgres 규칙

> [k8s.md](../k8s.md) §12에서 분리(상위 문서가 doc_lint 500줄 상한에 닿았다).
> 상위 규칙은 [k8s.md](../k8s.md), 자원 수치는 [../../resource-sizing.md](../../resource-sizing.md)에 있다.

- **오퍼레이터**: [CloudNativePG](https://cloudnative-pg.io/)(CNCF). **`terraform/lakehouse-platform/`**
  (`helm_release.cnpg`)가 `ns=cnpg-system`에 설치한다 — 그 **네임스페이스는 `k8s-operators.sh`가**
  미리 만들고(`create_namespace = false`), `Cluster` CR은 `k8s/catalog-postgres.yaml`(적용은
  `k8s-poc-storage.sh`). 즉 CNPG 하나에 세 주체가 걸쳐 있어 **순서가 곧 전제**다([`../../setup.md`](../../setup.md) §3).
- **차트 버전 ≠ appVersion**(§9 Spark 오퍼레이터와 같은 함정): chart **0.29.0** = CNPG **1.30.0**.
  `helm search repo cnpg/cloudnative-pg --versions`로 대조하고
  `terraform/lakehouse-platform/variables.tf`의 `cnpg.chart_version`에 핀한다(오퍼레이터 설정은 셸에서 이관됐다).
- **서비스 이름에 접미사가 붙는다** — `<cluster>-rw`(쓰기)·`-ro`(읽기 전용)·`-r`(전체)만 생기고
  `<cluster>` 이름의 서비스는 **만들어지지 않는다**. jdbc URI는 `catalog-postgres-rw:5432`다.
- **자동생성 시크릿(`<cluster>-app`)을 쓰지 않는다** — Dagster·dbt가 이 DB에 직접 붙으므로
  (`ICEBERG_CATALOG_*`) 오퍼레이터가 만든 비밀번호는 사람이 옮겨야 하고, 그 동기화가
  어긋나면 [operations.md](../../operations.md)의 "부분 성공" 드리프트가 재현된다(나열은 되고
  `load_table`에서 거부). → `bootstrap.initdb.secret`으로 **선언 시크릿**
  `catalog-pg-app`(type `kubernetes.io/basic-auth`, 키 `username`/`password` 고정)을 지정하고,
  값의 단일 출처는 `scripts/k8s-poc-storage.sh`(env override)로 둔다.
  PG 크리덴셜은 `lakehouse-creds`(S3 전용)와 **분리**한다 — 같은 비밀번호를 두 시크릿에 두지 않는다.
- **`bootstrap.initdb.secret`은 "초기화 1회"다 — 스크립트 재실행으로 비밀번호가 회전되지 않는다.**
  `PG_PASSWORD=새값 ./scripts/k8s-poc-storage.sh`를 돌리면 **k8s Secret만 바뀌고 DB 롤은 옛 값 그대로**다.
  그러면 Secret을 읽는 워크로드는 인증에 실패하고 `.env`를 읽는 호스트 경로는 성공해
  **위 "부분 성공" 드리프트가 축만 바꿔 재현된다**(`security`·`devops-qa` 감사 공통 지적).
  CNPG가 시크릿 변경을 롤에 반영하는 것은 **`spec.managed.roles`로 선언한 롤뿐**이고
  `bootstrap.initdb`로 만든 계정은 대상이 아니다(CNPG `declarative_role_management` 문서).
  → **해결**: `spec.managed.roles`에 `iceberg`를 선언해 CNPG가 시크릿 변경을 롤에 재적용하게 했다.
  `bootstrap.initdb`로 만든 롤을 **선언 관리로 인수**하는 형태이며 `name`은 initdb의 `owner`와 같아야 한다.
  `dagster` 롤도 같은 방식으로 추가했다(인수가 아니라 처음부터 선언) —
  실측 `reconciled: ["iceberg","dagster"]`. 단 **이미 떠 있는 워크로드의 env는 여전히 수동**이라
  Spark Connect·Flink·Dagster 파드는 **재기동까지 한 벌**이다.
- **`bootstrap.initdb.owner`와 시크릿 `username`은 반드시 같아야 한다**(CNPG 문서 명시).
  CR의 `owner`는 리터럴이라 `PG_USER` env override와 자동으로 맞춰지지 않으므로,
  `k8s-poc-storage.sh`가 **적용 전에 CR의 `owner`와 `PG_USER`를 대조해 불일치 시 중단**한다.
- **probe(§3)·RBAC(§5)·securityContext(§6)는 CR에 쓰지 않는다 — 오퍼레이터가 채운다.**
  `kubectl get pod catalog-postgres-1 -o yaml` 실측: `runAsNonRoot:true`·uid/gid `26`·
  `readOnlyRootFilesystem:true`·`capabilities.drop:[ALL]`·`seccompProfile:RuntimeDefault`,
  `/healthz`·`/readyz`·`/startupz` 3종 probe, 전용 SA·Role(자기 시크릿에 `resourceNames` 한정)이 모두 자동 생성된다.
  **CR에 없다고 위반으로 읽지 않는다**(정적 감사의 거짓 갭). 반대로 중복 선언해 오퍼레이터 값과 충돌시키지도 않는다.
- **operand 이미지 태그를 명시**한다(§4 `latest` 금지). 형식은 `MM.mm-TYPE-OS`
  ([postgres-containers](https://github.com/cloudnative-pg/postgres-containers)). `system` 타입은 deprecated이므로
  **`standard`** 를 쓴다(백업은 in-tree가 아닌 플러그인 경로라 barman 바이너리가 이미지에 필요 없다).
- **백업·PITR은 Barman Cloud 플러그인(CNPG-I)** 으로 한다 — in-tree barman-cloud는 **CNPG 1.31.0에서 제거 예정**.
  전제는 CNPG ≥ 1.26 + **cert-manager**다. cert-manager는 Flink Operator 웹훅과 **공용**이라
  `k8s-env.sh`의 `ensure_cert_manager` 헬퍼가 있으면 재사용·없으면 설치한다(멱등).
  **`rollout status` 완료 ≠ 웹훅 서빙 준비** — cainjector가 CA 번들을 주입하기 전에는
  cert-manager 리소스 생성이 `x509: certificate signed by unknown authority`로 거부된다
  (실제로 **직후 플러그인 apply가 실패**했다). `ensure_cert_manager`는 **설치 여부와 무관하게**
  self-signed `Issuer`의 `--dry-run=server`가 통과할 때까지 폴링한다("이미 설치됨"도 준비를 뜻하지 않는다).
  백업 대상은 클러스터 내부 **SeaweedFS(S3)** 로 두어 외부 비용을 만들지 않는다.
  🔴 **지금 이 배선은 꺼져 있다 — 백업은 돌지 않는다.** `Cluster` CR의 `spec.plugins`가
  **주석 처리**되어 플러그인을 참조하지 않으므로 base backup·WAL 아카이빙 모두 **미수행**이고
  `bootstrap.recovery`(PITR)도 쓸 수 없다([operations.md](../../operations.md) §4-4).
  끈 이유는 SeaweedFS로의 `PutObject`가 `InternalError`로 실패해 WAL 아카이빙이
  `exit status 4` 재시도 루프에 빠졌기 때문이고(`ContinuousArchiving=False`),
  aws-chunked 체크섬 가설로 사이드카 env를 넣어도 증상이 같아 **원인은 미규명**이다
  (경위는 CR 주석 `k8s/catalog-postgres.yaml`).
  실패한 채 배선을 남겨두면 **아카이빙 못 한 WAL이 PVC(5Gi)를 채워 DB가 선다** → 참조를 뺐다.
  ⚠️ 이 문서는 한동안 인과를 **정반대로** 적고 있었다("플러그인이 없으면 WAL이 쌓인다").
  같은 결과를 반대 조건에 귀속시키면 되살릴 때 **틀린 쪽을 만진다**.
  - **설치는 능력, 적용은 배선** — 플러그인 설치(`k8s-operators.sh`)와 CRD 선행 검사는 **유지**한다.
    재활성을 **CR 주석 해제 1단계**로 남기기 위해서다(설치는 상주 비용이 아니라 능력이다).
  - **적용은 배선과 한 벌** — `k8s-poc-storage.sh`는 CR에 `plugins` 배선이 있을 때만
    `ObjectStore`·`ScheduledBackup`을 적용한다(판정은 클러스터가 아니라 **선언 파일**을 읽는다).
    배선 없이 `ScheduledBackup`만 돌면 **아무도 안 읽는 실패**가 매일 쌓인다.
  - ⚠️ **가드는 신규 적용만 가른다 — 잔존 오브젝트는 회수하지 않는다.** 로그는 "건너뜀"인데 옛
    `ScheduledBackup`이 계속 도는 **로그↔실체 괴리**가 남는다. 더 나쁜 축은 **라이브 `Cluster`에
    `spec.plugins`가 남은 경우**다 — `ObjectStore` 없이 아카이빙만 시도돼 **끈 이유(WAL이 PVC를
    채운다)가 로그상 "미수행"인 채로 재현**된다(카탈로그 정지 = 전 테이블 메타 접근 불가).
    선언 파일 주석이 **라이브 CR의 필드까지 지운다는 보장은 없다**(client-side apply의 prune 동작 —
    이 저장소에는 필드 소유권이 `kubectl-patch`로 넘어간 실측이 `terraform/lakehouse-platform/manifests.tf`에
    있다). **prune 여부는 미확인**이니 배선을 끈 뒤 처음 재실행하는 클러스터는 **두 축을 다** 본다.

    ```shell
    kubectl get scheduledbackup,objectstore -n default
    kubectl get cluster catalog-postgres -o jsonpath='{.spec.plugins}'
    ```

    삭제는 파괴적이므로 **출력을 확인한 뒤 사용자 판단**이다.
  - **재검토 트리거는 시점이 아니라 조건** — *SeaweedFS가 aws-chunked·flexible checksum을
    해제한다는 실측이 나올 때* 되살린다. 🔴 **버전 상승은 트리거가 아니다 — `거부` ≠ `지원`.**
    새 버전에서 증상이 바뀌어도(조용한 손상 → 명시적 거부) 체크섬을 처리하게 된 것은 아니다.
    그전까지는 리스크 **수용**이고(대응 4종은 [risk.md](../../risk.md) §3),
    카탈로그 소실 시 복구 수단은 **재적재**다.
  - 아래 두 단서(DR 아님·평문 전송)는 **되살린 뒤에도 그대로 유효**하다.
  **이 백업은 DR이 아니다** — 백업본이 원본과 **같은 노드·같은 호스트 디스크**에 놓이므로
  노드/PVC 유실 시 함께 사라진다. 목적은 **논리 오류·실수 복구**로 한정한다. 또 SeaweedFS S3는 `http://`
  평문이라 WAL·base backup이 평문 전송·저장된다(카탈로그 DB는 테이블 식별자·메타 포인터만 담아
  PHI 경로는 아니다 — [security.md](../../security.md) 4-4).
- **PVC 사후 확장이 안 된다** — kind 기본 SC(`rancher.io/local-path`)는 `ALLOWVOLUMEEXPANSION=false`다
  (실측). 용량은 처음에 넉넉히 잡고, 늘리려면 클러스터 재생성이다.
- **메타 Postgres(Dagster)도 이 클러스터가 갖는다**. 별도 Cluster를 세우지 않고
  같은 `catalog-postgres`에 **`Database` CR로 `dagster` DB만** 더한다(CRD `databases.postgresql.cnpg.io`).
  롤은 `managed.roles`의 **`dagster`** 로 카탈로그(`iceberg`)와 분리하고 시크릿도 따로 둔다.
  구 근거 "Dagster가 호스트라 순환 의존"은 §8 개정으로 소멸했다 — 이제 kind 기동 순서만 지키면 된다.
