"""잡·스케줄 정의 — `defs/` 자동발견 대상.

모듈 스코프의 잡/스케줄 객체는 `load_defs`가 자동 수집한다(@dg.definitions 불필요).

- dbt 인제스트 그룹 전체를 매시각 빌드하는 잡·스케줄
- USGS 수문 적재 잡·스케줄 **2쌍** — 순간값(15분)과 관측소 메타(일 1회)는
  주기가 자릿수로 달라 한 잡으로 묶지 않는다. 그룹 전체를 선택하면 차원
  테이블이 15분마다 재적재된다.
- Frankfurter 환율 잡·스케줄 1쌍 — **파티션 잡이라 스케줄을 직접 쓰지 않고
  파티션 정의에서 파생**시킨다(아래).
- Polygon 시장 데이터 잡·스케줄 **2쌍**(시세·뉴스) — 같은 원천·같은 주기라도
  요청 예산이 달라 분리한다(아래 주석). 둘 다 파티션 잡이다.
"""

import dagster as dg
from dagster_project.defs.frankfurter_fx.constants import SCHEDULE_HOUR_UTC
from dagster_project.defs.polygon_market.constants import (
    SCHEDULE_HOUR_UTC as POLYGON_SCHEDULE_HOUR_UTC,
)

dbt_all_job = dg.define_asset_job(
    "dbt_all_job",
    selection=dg.AssetSelection.groups("dbt_ingest"),
)

dbt_all_schedule = dg.ScheduleDefinition(
    name="dbt_all_schedule",
    job=dbt_all_job,
    cron_schedule="0 * * * *",
    # cron을 KST로 해석(미지정 시 daemon 시스템 TZ 의존). docs/conventions/timezone.md
    execution_timezone="Asia/Seoul",
    # 기본 STOPPED를 명시한다 — 미지정일 때의 기본값과 값은 같지만, dbt 타깃이
    # spark_connect로 바뀌면서 실패 모드가 달라졌기 때문이다.
    # 전: 접속 자체가 실패 / 후: 접속은 성공하고 TABLE_OR_VIEW_NOT_FOUND
    # (카탈로그에 mimiciv 네임스페이스가 아직 없다). 매시각 실패가 쌓이면
    # 빨간불이 배경 소음이 되어 진짜 회귀를 가린다.
    #
    # 켜기 전 전제조건:
    #   1. Spark Connect 접속 경로 확보(kubectl port-forward svc/spark-connect 15002)
    #   2. bronze 적재로 Iceberg 카탈로그에 mimiciv 네임스페이스·소스 테이블 존재
    #   3. `dbt build --target spark_connect` 수동 1회 성공
    # 정의 자체는 배선 가치가 있으므로 삭제하지 않는다.
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

# ── USGS 수문(NWIS) 적재 ─────────────────────────────────────────────────
# 🔴 자산 두 개를 **그룹이 아니라 개별 선택**으로 가른다. 그룹 전체를 한 잡에
# 묶으면 관측소 메타(차원)가 15분마다 재적재된다 — 주기가 자릿수로 다르다.

usgs_water_iv_job = dg.define_asset_job(
    "usgs_water_iv_job",
    selection=dg.AssetSelection.assets("water_iv_raw"),
)

usgs_water_iv_schedule = dg.ScheduleDefinition(
    name="usgs_water_iv_schedule",
    job=usgs_water_iv_job,
    # 원천 갱신이 "Updated every minute"이고 관측 간격은 15분이 최빈이다
    # (2026-09-05 실측: 15분 2,169건 / 5분 420건). 5분 관측소를 놓치지 않으려면
    # 주기를 줄여야 하지만, LOOKBACK 창이 겹쳐 받으므로 누락은 생기지 않는다.
    cron_schedule="*/15 * * * *",
    execution_timezone="Asia/Seoul",
    # 외부 API를 주기적으로 호출하므로 기본 정지 상태로 둔다 — 켜는 시점을
    # 사람이 정한다. rate limit은 "5-10 req/s"가 공식 명시돼 여유가 크지만,
    # 시간당 한도는 문서가 다루지 않아 미확인이다.
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

usgs_water_sites_job = dg.define_asset_job(
    "usgs_water_sites_job",
    selection=dg.AssetSelection.assets("water_sites"),
)

usgs_water_sites_schedule = dg.ScheduleDefinition(
    name="usgs_water_sites_schedule",
    job=usgs_water_sites_job,
    # 관측소 메타는 거의 바뀌지 않는 차원이다. 일 1회로 충분하다.
    cron_schedule="0 4 * * *",
    execution_timezone="Asia/Seoul",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

# ── Frankfurter 환율(Frankfurter) 적재 ───────────────────────────────────────
# 🔴 위 셋과 **선언 방식이 다르다.** 파티션 자산의 스케줄은 cron도 타임존도
# 직접 주지 못한다 — `build_schedule_from_partitioned_job`에
# `cron_schedule`/`execution_timezone`을 넘기면 시간 파티션 잡에서는
# `CheckError`로 죽는다(실측). 둘 다 `partitions_def`에서 파생되고, 우리가
# 미는 것은 **발화 시각**뿐이다.
#
# 그래서 이 파일의 다른 스케줄과 달리 `execution_timezone="Asia/Seoul"`이
# 없는데, 규약을 어긴 것이 아니라 **만족 지점이 옮겨간 것**이다:
# 타임존은 `frankfurter_fx/constants.py`의 `PARTITION_TIMEZONE`이 명시한다.
# (파생 결과 실측: cron `"0 1 * * *"` · tz `"UTC"`)
#
# `partitions_def=`는 `define_asset_job`에 넘기지 않는다 — 선택된 자산에서
# 추론되므로 중복이고, Dagster가 폐기 예고한 인자다.
frankfurter_fx_rates_job = dg.define_asset_job(
    "frankfurter_fx_rates_job",
    selection=dg.AssetSelection.assets("fx_rates_daily"),
)

frankfurter_fx_rates_schedule = dg.build_schedule_from_partitioned_job(
    frankfurter_fx_rates_job,
    name="frankfurter_fx_rates_schedule",
    hour_of_day=SCHEDULE_HOUR_UTC,
    # 외부 API를 주기 호출하므로 기본 정지. 켜는 시점은 사람이 정한다.
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

# ── Polygon(Massive) 시장 데이터 적재 ────────────────────────────────────────
# 선언 방식은 위 Frankfurter와 같다(파티션 잡 → 스케줄 파생, 타임존은
# `polygon_market/constants.py`의 `PARTITION_TIMEZONE`이 명시).
#
# 🔴 **잡을 둘로 나눈다.** 시세와 뉴스는 같은 원천·같은 주기·같은 파티션인데도
# 한 잡으로 묶지 않는다 — 뉴스는 페이징이 걸려 요청 수가 하루치마다 다르고,
# 한쪽이 상한 초과로 실패할 때 다른 쪽까지 되돌리면 **멀쩡한 시세를 다시 받아야
# 한다**(요청 예산이 곧 비용이다). 같은 이유로 `usgs_water`도 두 쌍이다.
#
# ⚠️ **무료 플랜은 분당 호출 수가 제한된다.** 백필은 파티션당 1요청(뉴스는 N요청)
# 이므로 **동시 실행 수가 곧 순간 요청 수**다 — 대량 백필 전에
# `max_concurrent_runs`를 함께 본다(docs/resource-sizing.md).
polygon_equity_ohlcv_job = dg.define_asset_job(
    "polygon_equity_ohlcv_job",
    selection=dg.AssetSelection.assets("equity_ohlcv_daily"),
)

polygon_equity_ohlcv_schedule = dg.build_schedule_from_partitioned_job(
    polygon_equity_ohlcv_job,
    name="polygon_equity_ohlcv_schedule",
    hour_of_day=POLYGON_SCHEDULE_HOUR_UTC,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

polygon_news_job = dg.define_asset_job(
    "polygon_news_job",
    selection=dg.AssetSelection.assets("news_articles"),
)

polygon_news_schedule = dg.build_schedule_from_partitioned_job(
    polygon_news_job,
    name="polygon_news_schedule",
    hour_of_day=POLYGON_SCHEDULE_HOUR_UTC,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
