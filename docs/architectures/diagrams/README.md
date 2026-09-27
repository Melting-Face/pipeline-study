# 아키텍처 다이어그램 (archify 스펙)

이 디렉터리는 **JSON 스펙만** 둔다. 렌더된 HTML은 장당 800KB대 생성물이라 커밋하지 않는다 —
필요할 때 아래 명령으로 다시 만든다. 스펙이 단일 출처이고 HTML은 파생물이다.

| 스펙 | 타입 | 무엇을 그리는가 |
|---|---|---|
| [`lakehouse-pipeline.dataflow.json`](lakehouse-pipeline.dataflow.json) | `dataflow` | 원천 → S3 착지 → 적재 경로 → Iceberg bronze → dbt silver·Flink |
| [`local-k8s.architecture.json`](local-k8s.architecture.json) | `architecture` | kind on Podman 런타임 배치 — 노출 경로와 Iceberg JDBC 카탈로그 공유 |
| [`agent-orchestration.workflow.json`](agent-orchestration.workflow.json) | `workflow` (schema v2) | supervisor → 워커 → G1/G2 게이트 → 저널 |

세 장은 **서로 다른 축**이다(데이터 흐름 / 런타임 배치 / 통제 배선). 한 장에 합치지 않는다.

## 재생성

archify 스킬이 설치돼 있어야 한다. 설치 경로는 `.claude/skills/archify`이고 **스킬 디렉터리 자체는
git 추적 대상이 아니므로** 새로 클론한 트리에는 없다.

🔴 **archify는 현재 [`skills-lock.json`](../../../skills-lock.json)·[`docs/skills.md`](../../skills.md)에
미등재다.** 출처가 개인 저장소이고 실행 파일(`bin/archify.mjs`)을 포함하므로 등재 루브릭과
`security` 검토를 거쳐야 한다 — **등재 전에는 이 절차를 상시 경로로 쓰지 않는다.**
아래 명령은 등재가 끝난 뒤의 재생성 절차를 기록해 둔 것이다.

```bash
ARCHIFY=.claude/skills/archify/bin/archify.mjs
OUT=/tmp/archify-out          # 저장소 밖으로 둔다 (아래 주의 참고)

node "$ARCHIFY" deliver dataflow     docs/architectures/diagrams/lakehouse-pipeline.dataflow.json  "$OUT"/lakehouse-pipeline.html  --quality showcase --json
node "$ARCHIFY" deliver architecture docs/architectures/diagrams/local-k8s.architecture.json       "$OUT"/local-k8s.html           --quality showcase --json
node "$ARCHIFY" deliver workflow     docs/architectures/diagrams/agent-orchestration.workflow.json "$OUT"/agent-orchestration.html --quality showcase --json
```

`deliver`가 exit 0이면 스펙/산출물의 SHA-256과 바이트 수를 영수증으로 돌려준다.
브라우저 실측(4개 뷰포트 담김 + 라이트/다크 캡처)은 **배포가 성공한 뒤에만** 돌린다 —
실패한 배포는 이전 산출물을 보존하므로, 그 경로에 `visual-check`를 돌리면 낡은 파일을 재게 된다.

```bash
node "$ARCHIFY" visual-check "$OUT"/lakehouse-pipeline.html --json
```

⚠️ **출력 경로를 저장소 안에 두지 않는다.** `deliver`는 스펙 옆에 비공개 스냅샷을,
`visual-check`는 산출물 옆에 PNG 4장·contact sheet·영수증을 남긴다.

⚠️ **셸 리다이렉션(`>`·`>>`·`2>&1`)을 붙이지 않는다.** [`scripts/worktree_guard.py`](../../../scripts/worktree_guard.py)가
세그먼트 안의 쓰기 신호와 저장소 경로 토큰이 함께 나타나면 `deny`한다 — 조회 명령도 막힌다.
파이프(`| head`)는 세그먼트 구분자라 안전하다.

## 수치의 유효 조건 (시제)

스펙에는 **저절로 낡는 수치**가 박혀 있다 — `dbt silver 22 모델` · `bronze 17 테이블` ·
`PreToolUse 가드 9종` · `판정 워커 5종` · `gold 0건`.

이것은 [`CLAUDE.md`](../../../CLAUDE.md) 문서 3축의 **②시제에서 걸리는 값**이고, 그럼에도 **수용한다** —
근거는 수치가 이 그림의 설득력 자체이기 때문이다(일반화하면 「어떤 레이어가 있다」는 하나 마나 한
문장이 된다). 수용에는 재검토 트리거를 함께 둔다([`docs/risk.md`](../../risk.md) §대응) —
트리거는 시점이 아니라 **조건**이다:

| 조건 | 갱신 대상 |
|---|---|
| `dagster/dockerfile.d/src/dbt_pipelines/models/**` 의 모델이 늘거나 줄 때 | D1 `dbt silver` 수치 |
| Dagster 적재 자산이 늘거나 줄 때 | D1 `bronze` 수치 |
| `.claude/agents/*.md` 가 늘거나 줄 때 | D3 `판정 워커` 수치 |
| `scripts/*_guard.py` 또는 `.claude/settings.json` 의 `hooks` 배선이 바뀔 때 | D3 `PreToolUse 가드` 수치 |
| `gold` 태그가 처음 생길 때 | D1 「선언된 공백」 카드 |
| 오퍼레이터·차트 버전이 바뀔 때 | D2 「이 그림이 그리지 않는 것」 카드 |
| `k8s/catalog-postgres.yaml` 의 `spec.plugins` 주석이 풀릴 때 | D2 「이 그림이 그리지 않는 것」 카드 |
| `k8s/spark/spark-thrift-server.yaml` 이 배포될 때 | D2 「이 그림이 그리지 않는 것」 카드 |

⚠️ 아래 두 행은 **미해소 항목의 해소 트리거**다. 같은 실태가 [`docs/security.md`](../../security.md)·
[`docs/conventions/k8s/cnpg.md`](../../conventions/k8s/cnpg.md)·[`docs/operations.md`](../../operations.md)·
[`docs/setup.md`](../../setup.md)에도 적혀 있어 이 스펙이 **네 번째 사본**이다 —
해소되면 **한 벌로** 고친다. 한쪽만 고치면 「아직 안 됐다」가 남는다.

🔴 **세기 전에 그 명령이 무엇을 세는지 적는다.** 이 저장소에서 반복된 실패가 계측 *단위* 어긋남이다
(값은 맞고 라벨이 틀리면 검산을 통과한다):

```bash
ls .claude/agents/*.md | wc -l                   # 워커 총수 — 판정자 수가 아니다
grep -l 'disallowedTools' .claude/agents/*.md    # 도구 제한이 걸린 워커 — 전부가 판정자는 아니다
ls scripts/*_guard.py | wc -l                    # 가드 파일 총수 — PreToolUse 배선 수가 아니다
```

`판정 워커 5종`은 `*-verifier`·`*-qa`·`security`를 센 값이고, `PreToolUse 가드 9종`은
**배선된 스크립트 종수**다(가드 파일은 10종 — `plan_mirror_guard`는 `PostToolUse`/`Stop` 전용이라 빠진다).

## 로케일

archify가 지원하는 Viewer 로케일은 `en`·`zh-CN`뿐이라 `meta.locale`을 **생략했다**.
본문·노드·범례·카드는 한국어지만 **Viewer 고정 UI와 `<html lang>`은 영어로 폴백**한다.
렌더러는 작성된 문자열을 번역하지 않는다.

## 작성 규칙 (다음에 고칠 사람에게)

- `meta.quality_profile: "showcase"` 누락·오타 시 검증이 **4-check 기본 모드로 조용히 떨어진다**.
  9-check가 아니면 showcase 합격이 아니다.
- 기하 제어(`via`·`channelX`·`labelAt`)는 **처음부터 쓰지 않는다**. 전부 `auto`로 시작해
  진단이 지목한 것만 한 번에 하나 적용한다.
- **한글은 라벨 마스크에서 2유닛으로 센다**(`mask ≈ 6.5px × units + 13px`). 긴 한국어 서브라벨은
  렌더러가 폰트를 줄여 넣는데, 그 작은 폰트가 가독성 하한(6px)에 걸려 **뷰어가 화면에 맞게 축소하지
  못하고 세로로 넘친다**. 서브라벨은 짧게 두고 식별자·프로토콜은 ASCII로 남긴다.
- `route: "straight"`는 dataflow에서 **대각선이라 직교 화살표 계약을 위반**한다. 통로 충돌은
  라우팅이 아니라 **배치**로 푼다.
