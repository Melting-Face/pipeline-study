# 환경변수·운영 정책 (operations)

> **목적**: 환경변수 주입 방식과 데이터 보존 등 운영 정책을 한곳에서 관리한다.
> **언제 읽나**: 새 환경변수 추가, 서비스 추가, 보존기간·만료 정책 결정 시.
> **연관**: [conventions/docker.md](conventions/docker.md), [conventions/general.md](conventions/general.md)(비밀정보), [resource-sizing.md](resource-sizing.md).

`data-pipeline` 레포에서 이식·적응.

## 1. 환경변수 주입

- 민감한 값(DB 비밀번호, S3 키, 토큰)은 반드시 `.env`에 정의하고 **코드·설정에 하드코딩하지 않는다.**
- Python 코드에서는 `dg.EnvVar("KEY")`(리소스 config) 또는 `os.environ["KEY"]`(즉시 필요)를 쓴다.
- `os.environ.get("KEY", "default")`는 **선택적** 환경변수에만 쓴다.
- `.env`는 절대 커밋하지 않는다(`.gitignore`에 포함). Trino 카탈로그 등 설정 파일은 `${ENV:KEY}`로 치환.

```python
# Good — 참조로 주입
S3Resource(aws_access_key_id=dg.EnvVar("AWS_ACCESS_KEY_ID"))

# Bad — 하드코딩
S3Resource(aws_access_key_id="AKIAIOSFODNN7EXAMPLE")
```

### 1-1. 환경변수 추가 시 전파 확인 (의존성 관리)

새 환경변수는 **코드에서 참조하는 것으로 끝내지 않고, 그 값을 실제로 사용하는 컨테이너까지
주입되는지** 확인한다. `.env`에만 있고 서비스에 전달되지 않으면 컨테이너 안에서
`KeyError`·인증 실패가 난다. 아래 체인을 위→아래로 모두 채운다.

```
.env.example  (형식·예시 문서화, 값은 비움 — 팀 공유용, 커밋)
    │
.env          (실제 값, 커밋 금지)
    │
compose.yml   (${KEY} 보간 → 컨테이너 environment)
    │
dg.EnvVar("KEY") / os.environ["KEY"]  (코드에서 참조)
```

**절차**:

1. **`.env.example`에 키와 형식 예시를 추가**한다(값은 비움 — 커밋 대상).
2. `compose.yml`에서 그 값을 **사용하는 서비스**에 `- KEY=${KEY}`가 있는지 확인하고 없으면 추가한다.
   - 공용 앵커 **`x-dagster-common`**(`&dagster-common`)을 상속하는 서비스(webserver·daemon)는
     **앵커에 한 번만** 추가하면 둘 다 전파된다.
   - 앵커를 상속하지 않는 서비스(`trino`·`seaweedfs` 등)는 해당 서비스의 `environment:`에 직접 추가한다.
3. **에셋 실행 컨테이너**에 전파되는지 확인한다. 이 레포는 `DefaultRunLauncher`라 run이
   **daemon in-process 서브프로세스**로 돌아 daemon 서비스 env로 커버된다. 향후
   `DockerRunLauncher` 등 별도 컨테이너로 바꾸면 그 컨테이너 env에도 추가해야 한다.
4. 코드에서 `dg.EnvVar("KEY")`(필수) 또는 `os.environ.get("KEY", ...)`(선택)로 참조한다.

> 예) `AWS_*`·`ENDPOINT_URL`은 `x-dagster-common` 앵커에 있어 webserver·daemon에 전파되고,
> `trino` 서비스는 앵커를 안 쓰므로 `environment:`에 `AWS_*`를 **별도로** 나열한다(현재 구현).

### 1-2. 호스트 실행과 컨테이너 실행의 값이 다른 키

재설계 토폴로지에서 Dagster는 **호스트**(`uv run dg dev`)에서 돌고 메타 Postgres는 **compose**에 있다
([conventions/k8s.md](conventions/k8s.md) §8). 같은 키라도 **누가 읽느냐에 따라 값이 달라진다**.

| 키 | 컨테이너(compose) | 호스트(`dg dev`) |
| --- | --- | --- |
| `POSTGRES_HOST` | `postgres`(서비스명) — `compose.yml`이 **리터럴로 고정** | `localhost` — `.env` 값 사용 |

- `dagster.yaml`의 `hostname`은 **하드코딩하지 않고** `env: POSTGRES_HOST`로 참조한다.
  하드코딩하면 호스트 실행 시 이름 해석이 안 돼 `too many retries for DB connection`으로 죽는다(2026-08-18 실측).
- compose `postgres`는 호스트가 붙을 수 있도록 **`127.0.0.1:${POSTGRES_PORT}:5432`** 로 퍼블리시한다
  (루프백 바인딩 — 외부 노출 금지, [security.md](security.md)).
- 호스트 실행 시 **`DAGSTER_HOME`을 `dagster.yaml`이 있는 디렉터리**(`dagster/dockerfile.d/src`)로 지정한다.
  지정하지 않으면 임시 sqlite 인스턴스가 쓰여 **UI에 런이 안 남는다**.
- **Iceberg 적재 대상 전환 키**: `ICEBERG_CATALOG_HOST`·`_PORT`·`_DB`·`_USER`·`_PASSWORD`
  (`common/constants.py`가 읽는다). 미지정 시 compose 기본값(`postgres:5432/iceberg_catalog`, 메타 DB 계정)을
  쓰므로 기존 동작이 보존된다. K8s 카탈로그를 대상으로 하려면 이 값들을 K8s 쪽(전용 계정)으로 넘긴다.
  - ⚠️ **이 키들은 `compose.yml`에 일부러 넣지 않았다** — 체인 2단계(compose 전파)의 **의도된 예외**다.
    컨테이너 실행은 코드 기본값이 곧 정답(`postgres:5432/iceberg_catalog`)이고, 값을 바꿔야 하는 쪽은
    **호스트 실행 + K8s 카탈로그** 조합뿐이라 `.env`만으로 충분하다. 누락으로 오인해 앵커에 추가하지 않는다.
  - **JDBC 계열 키(`ICEBERG_JDBC_URI`·`ICEBERG_PG_USER`·`ICEBERG_PG_PASSWORD`)와 혼동 주의**:
    같은 카탈로그를 가리키지만 전자는 **pyiceberg(파이썬)**, 후자는 **dbt-spark(JVM/JDBC)** 경로다.
- **Iceberg S3 접속 키**: `ICEBERG_S3_ENDPOINT`·`ICEBERG_S3_ACCESS_KEY`·`ICEBERG_S3_SECRET_KEY`
  (`common/constants.py`의 `S3_ENDPOINT`·`S3_ACCESS_KEY_ID`·`S3_SECRET_ACCESS_KEY`가 읽는다).
  미지정 시 공용 `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`로 **폴백**해 compose 단독 구성이 보존된다.
  위 카탈로그 키와 같은 이유로 **`compose.yml`에 넣지 않는다**(의도된 예외 — 값을 바꿔야 하는 쪽은
  호스트 실행 + K8s 조합뿐이다).
  - **엔드포인트와 자격증명은 한 쌍으로 바꾼다.** 엔드포인트만 K8s(`localhost:18333`)로 돌리고
    키를 공용 `AWS_*`로 두면 **부분 성공**이 난다 — 카탈로그 나열(`list_tables`)은 Postgres만 보므로
    성공하고, `load_table`이 `metadata.json`을 S3에서 읽는 순간 `ACCESS_DENIED during HeadObject`로 죽는다
    (2026-08-19 실측). 값 자체가 다르다(k8s Secret `lakehouse-creds`). **접속 대상을 바꾸는 값은 한 벌로 묶어 바꾼다.**
- **`AWS_REQUEST_CHECKSUM_CALCULATION`/`AWS_RESPONSE_CHECKSUM_VALIDATION`**: SeaweedFS 호환 필수 키.
  값이 없으면 최신 SDK 기본값이 객체를 손상시킨다([conventions/k8s.md](conventions/k8s.md) §11).
  코드 기본값이 있지만 컨테이너·외부 도구를 위해 `.env`·compose 앵커에도 명시한다.
- **dbt-spark 타깃 키**(`ICEBERG_*`·`SPARK_REMOTE`)도 같은 성격이다. 호스트에서 dbt를 돌리면
  in-cluster 서비스(카탈로그 Postgres·SeaweedFS·Spark Connect)에 **port-forward가 필요**하므로
  `.env` 기본값은 `localhost:<로컬포트>`를 가리킨다. 클러스터 안에서 도는 워크로드는
  매니페스트가 서비스명(`catalog-postgres-rw`·`seaweedfs`)을 직접 주입한다.
  - **카탈로그 PG의 서비스명에는 접미사가 붙는다** — CloudNativePG가 `<cluster>-rw`(쓰기)·`-ro`(읽기 전용)·
    `-r`(전체)를 만들고 `<cluster>` 이름의 서비스는 **만들지 않는다**. 오퍼레이터 이전 시 `catalog-postgres`를
    그대로 두면 DNS가 안 풀려 죽는다. 계정 시크릿도 `lakehouse-creds`(S3 전용)에서 분리해
    **`catalog-pg-app`**(basic-auth, 키 `username`/`password`)이 in-cluster 단일 출처다.

## 2. 운영 정책 (보존·만료)

무엇을 얼마나 보관하고 언제 지울지를 정한다.

| 항목 | 어떻게 도는가 |
| --- | --- |
| Iceberg 유지보수 | `iceberg_maintenance_job`이 주 1회 **컴팩션 → 스냅샷 만료 → orphan 정리** 순서로 처리한다 |
| SeaweedFS 용량 | 수명주기 정책 없음 |
| Docker 컨테이너 로그 | `max-size` × `max-file`로 컨테이너당 상한을 건다 |
| Claude Code 세션 로그 | `~/.claude/settings.json`의 `cleanupPeriodDays` |

🔴 **순서 강제가 이 잡의 설계다** — 컴팩션이 새 파일을 쓰고 만료가 옛 스냅샷을 끊은 **뒤에야**
orphan 정리가 안전하다. 순서를 바꾸면 살아 있는 파일을 지운다.
🔴 **다만 순서 강제와 실패 전파가 같은 배선에 묶여 있다** — 첫 op이 실패하면 뒤가 전부 중단된다
([`test.md`](test.md) §5-2).

📌 **각 항목의 현재 설정 상태와 남은 결정은 저장소 밖에 있다** —
`$OBSIDIAN_VAULT/status/observations.md` §운영 정책 미설정 항목.

### 2-1. 로컬 세션 로그 정리 (`cleanupPeriodDays`)

AI 세션 로그는 `~/.claude/projects/<프로젝트-경로-슬러그>/`에 `<session-id>.jsonl`로 쌓이고,
서브에이전트 로그는 그 아래 `<session-id>/subagents/`에 붙는다. 2026-08-20 실측 시 이 저장소 몫만
**74MB·103파일**이었다.

**통째로 지우지 않는다 — 같은 디렉터리에 영구 메모리가 산다.**

```
~/.claude/projects/<프로젝트>/
├── <session-id>.jsonl     ← 세션 로그 (정리 대상)
└── memory/                ← ⚠️ 자동 메모리(`MEMORY.md` 포함) — 삭제 금지
```

`rm -rf <프로젝트>`는 축적된 메모리를 함께 날린다. 수동 삭제가 필요하면 반드시
**유형을 한정**한다(`-name '*.jsonl'`). 그래야 `.md`인 메모리가 구조적으로 안 걸린다.

**정리 정책**

| 항목 | 값·동작 |
| --- | --- |
| 설정 키 | `~/.claude/settings.json`의 `cleanupPeriodDays`(기본 **30**, 이 환경은 **14**) |
| 실행 시점 | 세션 기동마다가 아니라 **주기적**. 마지막 실행은 `~/.claude/.last-cleanup`(ISO8601, UTC) |
| 실행 조건 | **대화형 기동에서만 돈다.** 헤드리스 `claude -p`는 3회 기동해도 마커가 갱신되지 않았다 |
| 보존 단위 | **파일이 아니라 세션(부모)**. `subagents/` 하위 로그는 **부모의 수명을 따른다** |

**판정 명령** — 값이 아니라 **단위**를 맞춰야 한다:

```bash
# ✅ 세션 단위(정책과 같은 단위). N일 초과 세션이 0이면 정상
find ~/.claude/projects -maxdepth 2 -name '*.jsonl' -mtime +14 | wc -l

# ❌ 재귀 탐색은 *파일*을 센다 — subagents/ 하위가 부모 수명으로 살아남아 0이 안 된다
find ~/.claude/projects -name '*.jsonl' -mtime +14 | wc -l
```

**설정을 넣은 것과 정리가 도는 것은 다른 축이다.** 값을 바꿨으면 **대화형 세션을 한 번 띄운 뒤**
위 명령으로 확인한다. 실측 결과 기준선 9개 중 8개가 삭제되고 109MB → 104MB로 줄었으며,
대조군인 `memory/` 8개는 무손상이었다. 남은 1개는 미삭제가 아니라 **위 재귀 명령이 세션이 아니라
파일을 세고 있었기 때문**이다([philosophy.md](philosophy.md) §계측 단위).

> 즉시 회수가 필요하면 `.last-cleanup`을 과거로 되돌린 뒤 대화형 세션을 띄우면 다음 주기를
> 기다리지 않는다(마커는 정리 후 정상 값으로 자동 복원된다). 다만 이 파일은 **내부 상태**이므로
> 원본 값을 먼저 기록해 두고 손댄다.

> Iceberg 유지보수는 `iceberg_maintenance_job`(주간 스케줄, **컴팩션→만료→orphan** 순서)으로
> 자동화했다. 컴팩션·orphan 정리는 **Spark Iceberg 프로시저**로 실행한다(2026-08-19에 Trino에서 이관 —
> [architectures/trino.md](architectures/trino.md)). 실행에는 **Spark Connect 접속**이 필요하다:
> 호스트에서 돌릴 때는 `kubectl port-forward svc/spark-connect 15002:15002`, 주소는 `SPARK_REMOTE`.
>
> `remove_orphan_files`는 warehouse를 **Hadoop FileSystem으로 나열**하므로 Spark Connect 서버에
> `spark.hadoop.fs.s3*` 설정이 있어야 한다(Iceberg S3FileIO로 대체 불가 — 카탈로그가 *모르는* 파일을
> 찾는 게 목적이다). 없으면 `No FileSystem for scheme "s3"`로 죽는다([conventions/k8s.md](conventions/k8s.md)).
>
> 남은 결정은 **보존기간(기본 7일)·컴팩션 임계값
> (기본 100MB)·대상 테이블 범위** 확정이며, 확정 시 이 표·[security.md §4-1](security.md)·[resource-sizing.md](resource-sizing.md)를 함께 갱신한다.

## 3. 토큰 비용 계측

> **관측 시각**: 2026-08-21 18:43~19:20 KST(스냅샷 — 세션이 계속 쌓이므로 재실행 시 값이 달라진다).

### 왜

토큰 비용 체감은 있었으나 계측 수단이 전무했다. 계측 없는 절감은 착각만 남긴다([philosophy.md](philosophy.md) 원칙 7).

### 계측 수단

`scripts/token_cost_report.py` — 실행: `uv run scripts/token_cost_report.py`

- 원천: `~/.claude/projects/<슬러그>/`의 JSONL 트랜스크립트에 담긴 `message.usage`.
  §2-1과 같은 로그 트리를 읽는다.
- **4개 토큰 축을 따로 센다** — `input_tokens`(미캐시 입력) / `output_tokens`(출력) /
  `cache_creation_input_tokens`(캐시 쓰기) / `cache_read_input_tokens`(캐시 읽기). 넷은 단가가
  전부 달라 합산하면 비용을 읽을 수 없다.
- 옵션: `--top N`(상위 N개), `--json`(기계 판독용), `--no-dedupe`(중복 제거 끔, 검증용).
- 🔴 `--project`는 `=`로 붙여 쓴다(`--project=-Users-jin-foo`). 슬러그가 `-`로 시작해 띄우면
  argparse가 옵션으로 오인한다.

### 무엇을 읽는가

📌 **실측값은 저장소 밖에 있다** — `$OBSIDIAN_VAULT/status/observations.md` §토큰 비용 계측.
관측 시각·모집단이 그쪽에 병기돼 있다. 세션이 계속 쌓이므로 **재실행하면 값이 달라진다.**

읽을 때 지킬 것 셋:

- 🔴 **절편을 반드시 함께 읽는다.** 회귀 기울기만 보면 그 값이 **총량인지 한계인지** 갈리지 않는다.
  기울기는 **1바이트 늘 때의 증분**이고 총량은 절편을 더해야 나온다.
- 🔴 **백분율은 관측 시점의 바이트와 함께 적는다.** 같은 회귀라도 분모가 바뀌면 비중이 바뀐다 —
  값이 달라진 것과 값이 틀린 것은 다르다.
- **관측 이력을 덮어쓰지 않고 나란히 둔다.** 지우면 "무엇이 달라졌는지"가 사라진다.

⚠️ **기여도 추정에는 교란 요인이 있다** — 같은 기간에 워커·스킬 목록도 함께 커졌으므로
회귀 기울기는 **과대 추정**일 수 있다. 단독 기여는 범위로 읽는다.

### 계측 단위

"요청"은 **고유 `requestId` 수**다(메시지 줄 수가 아니다). 같은 응답이 트랜스크립트에 여러 줄로
반복되므로 줄을 세면 부풀려진다.

### 단가 유지보수

단가는 스크립트 상단 `PRICING`의 **하드코딩 스냅샷**이다. 모델 출시·인하 때 사람이 갱신해야 한다.
Sonnet 5 인트로 단가는 2026-08-31 만료다. 단가표에 없는 모델은 0원이 아니라 `미측정`으로 표기된다.

## 참고

- Dagster — Environment variables & secrets: https://docs.dagster.io/guides/deploy/using-environment-variables-and-secrets
- Docker Compose — 환경변수 보간: https://docs.docker.com/reference/compose-file/interpolation/
- Iceberg — Maintenance(expire snapshots): https://iceberg.apache.org/docs/latest/maintenance/
