# 타임존 정책 (timezone)

> **목적**: 저장·표시·스케줄 각 레이어의 타임존을 명시해 집계 오류·스케줄 오발화를 예방한다.
> **언제 읽나**: `datetime` 다루는 코드, `ScheduleDefinition` 추가, `compose.yml` 서비스 추가 시.
> **원칙**: **저장은 UTC, 표시·스케줄은 KST**. (명시적 — [philosophy.md](../philosophy.md))

`data-pipeline` 레포에서 이식. 타임존은 코드·스케줄·컨테이너·표시 레이어에 걸쳐 있으므로
한 문서에서 관리한다.

## 레이어별 타임존

| 레이어 | 타임존 | 이유 |
| --- | --- | --- |
| **데이터 저장** (Iceberg 테이블, Postgres 컬럼) | **UTC** | 타임존 변환 없는 안전한 비교·집계. 소스가 여럿이어도 단일 기준 |
| **컨테이너 시스템 시간** (로그 타임스탬프) | **KST** (`Asia/Seoul`) | 로그·운영 이벤트를 업무 시간 기준으로 읽기 |
| **Dagster 스케줄 실행** | **KST** | cron을 로컬 업무 시간으로 해석 (`execution_timezone`) |
| **표시 레이어** (UI·대시보드·리포트) | **KST** | 최종 사용자 관점 |

> 핵심: **데이터는 UTC로 저장하고, 사람이 보는 지점(로그·스케줄·표시)에서만 KST로 변환**한다.
> 저장 시점에 KST를 섞으면 서머타임 없는 한국이라도 소스 혼합·재처리에서 오차가 누적된다.

## 규칙

### 1. `datetime`은 timezone-aware(UTC)로 생성

```python
from datetime import datetime, timezone

# Good — tz-aware UTC
now = datetime.now(tz=timezone.utc)

# Bad — tz-naive (로컬 타임존 암시, 비교·저장 시 모호)
now = datetime.now()
```

- ruff `DTZ`(flake8-datetimez) 룰로 tz-naive `datetime`을 차단한다([python.md](python.md)).
- KST 표시가 필요하면 저장은 UTC로 두고 **표시 시점에만** 변환한다:
  `dt.astimezone(ZoneInfo("Asia/Seoul"))`.

### 2. 스케줄은 `execution_timezone="Asia/Seoul"`를 **명시**

> **예외** — 파티션 키가 그대로 원천 API의 날짜 파라미터로 나가는 일자 파티션은 **UTC**로 둔다
> (Dagster `polygon_market`·`fred_calendar`, Airflow `CronDataIntervalTimetable(..., timezone="UTC")`).
> KST 하루와 원천 달력일이 9시간 어긋나고 그 어긋남은 기록되지 않는다.

`execution_timezone`을 지정하지 않으면 스케줄이 **daemon 컨테이너의 시스템 타임존**으로
cron을 해석한다(UTC 이미지면 9시간 어긋남). 반드시 명시한다.

```python
# Good — 매시 정각을 KST 기준으로 해석
dbt_all_schedule = dg.ScheduleDefinition(
    name="dbt_all_schedule",
    job=dbt_all_job,
    cron_schedule="0 * * * *",
    execution_timezone="Asia/Seoul",
)
```

> [`defs/automation.py`](../../dagster/dockerfile.d/src/src/dagster_project/defs/automation.py)의
> `dbt_all_schedule`에 `execution_timezone="Asia/Seoul"`를 적용했다. 신규 스케줄도 동일하게 명시한다.

### 3. 컨테이너에 `TZ=Asia/Seoul` 주입

로그 타임스탬프를 KST로 남기려면 서비스에 `TZ`를 준다. 공용 앵커(`x-dagster-common`)에
한 번 추가하면 webserver·daemon에 전파된다.

```yaml
x-dagster-common: &dagster-common
  environment:
    - TZ=Asia/Seoul
    # ...기존 환경변수
```

> `TZ`는 **로그·프로세스 로컬 시간**에만 영향을 준다. Iceberg/Postgres에 **저장되는 값은
> 여전히 UTC**여야 하므로(규칙 1), `TZ` 설정과 저장 타임존을 혼동하지 않는다.

## 적용 체크리스트 (현재 레포)

- [x] `compose.yml` `x-dagster-common` 앵커에 `TZ=Asia/Seoul` 추가 (webserver·daemon 전파)
- [x] `dbt_all_schedule`에 `execution_timezone="Asia/Seoul"` 추가
- [ ] 신규 `@asset`/헬퍼에서 `datetime`은 `tz=timezone.utc`로 생성 (상시 규칙)
- [ ] 신규 `ScheduleDefinition`은 `execution_timezone` 필수 (상시 규칙)

> **Trino/Postgres에는 `TZ`를 넣지 않았다.** Trino JVM 기본 타임존을 바꾸면 `current_timestamp`
> 등 쿼리 시각 함수가 KST가 되어 "저장은 UTC" 원칙과 충돌할 수 있다. 로그 가독성보다
> 저장 일관성을 우선해 Dagster 서비스에만 적용했다.

## 참고

- Dagster — Schedules(`execution_timezone`): https://docs.dagster.io/guides/automate/schedules
- Python — `datetime` timezone-aware objects: https://docs.python.org/3/library/datetime.html
- Python — `zoneinfo`: https://docs.python.org/3/library/zoneinfo.html
- ruff — flake8-datetimez(`DTZ`): https://docs.astral.sh/ruff/rules/#flake8-datetimez-dtz
