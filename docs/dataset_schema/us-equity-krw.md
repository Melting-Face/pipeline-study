# 미국 주식 원화 종가 (us_equity_krw)

Iceberg 네임스페이스 `us_equity_krw`. 원천 테이블은 [`../dataset_schema.md`](../dataset_schema.md)의
Frankfurter·Polygon 절이 갖고, 이 문서는 **둘을 조인해 원화 종가를 만드는 규칙**을 갖는다.

**질문**: 미국 종목 하나가 원화로 얼마인가 — 하루 한 번, 전일 종가 기준. 보유 수량·평가액은 다루지 않는다.

## 구조

`stock_forecast`와 같은 이유로 **원천 디렉터리와 1:1이 아니다** — 두 원천을 가로지르므로 소유자를
`defs/us_equity_krw/`라는 별도 서브프로젝트에 두고 거기에는 `@asset`이 없다(dbt 소유권만).

| 모델 | 그레인 | 역할 |
|------|--------|------|
| `fx_usdkrw_daily` | 원천 고시일 | EUR 기준 두 환율을 나눈 **교차 USD/KRW** |
| `us_equity_krw__close_daily` | 종목·거래일 | **gold** — 달러 종가 × as-of 환율 = 원화 종가 |

달러 종가는 bronze를 다시 캐스팅하지 않고 `stock_forecast`의 silver `equity_prices_daily`를 `ref`로 쓴다.

## 환율을 어떻게 고르는가

- **교차환율** — 원천은 EUR 기준이라 달러/원이 직접 없다. 같은 고시의 `KRW per EUR ÷ USD per EUR`로 만든다.
  두 값이 같은 고시 시각이라 이 나눗셈에서는 시각 차이가 생기지 않는다.
- **날짜는 `source_date`** — 주말·휴일 요청에 원천이 직전 영업일 값을 주므로 요청일(`rate_date`)로 두면
  고시가 없던 날이 있던 날처럼 보인다. 에코는 `distinct`로 접고, 같은 고시일에 **값이 둘이면 접지 않는다**.
- **as-of** — 거래일 당일 또는 이전의 가장 최근 고시를 쓴다. ECB 휴장·미국 개장일(부활절 월요일 등)에
  직전 값이 들어가고 `fx_date`·`fx_lag_days`가 그 사실을 드러낸다.

## 🔴 오차의 정체 — 시각 차이

ECB 고시는 유럽 오후, 미국 종가는 뉴욕 16:00이다. **같은 날 고시는 종가보다 먼저 나오므로 미래 참조는
아니지만**, 그 사이 몇 시간의 환율 변동이 원화 종가의 오차로 남는다. 평일 대부분은 작고, 환율이 하루 안에
급변한 날은 1%를 넘을 수 있다. 허용 기준(1%)과 그 근거는 사용자 결정이다 — 국내 매매기준율·환율 종가는
원천 확보가 어렵고, 이 오차를 받아들였다.

⚠️ 비교 대상이 **국내 매매기준율(포털·증권사 표시)** 과의 차이는 재지 않았다(**미측정**) — 대조한 것은
독립 미국 공식 환율(FRED `DEXKOUS`)이다. `DEXKOUS`는 공개가 약 1주 늦어 일 1회 갱신 원천으로 쓸 수 없다.

## 게이트

| 테스트 | 막는 것 |
|---|---|
| `unique(fx_date)` | 같은 고시일에 값이 둘(원천 개정) — **gold의 `row_number()`는 하나를 임의로 골라 못 본다**. `dbt build`가 이 실패로 gold를 건너뛴다 |
| `accepted_range(usd_krw, 500~3000)` | 교차를 거꾸로 나눈 단위 사고 |
| `not_null(usd_krw)` | 거래일보다 환율이 늦게 시작(백필 범위 밖) — 행을 지우지 않고 운다 |
| `accepted_range(fx_lag_days, 0~5)` | 환율 적재 정지 — 휴일 조합 최대가 4라 5를 넘으면 휴일이 아니다. **경고가 아니라 차단** |

## 운영

- 🔴 **이관 중**: 정기 실행은 Airflow DAG(`fx_rates_daily`·`equity_ohlcv_daily`·`us_equity_krw_dbt`)로 옮긴다
  ([`airflow.md`](../conventions/airflow.md)). 컷오버 전까지 아래 Dagster 잡·스케줄은 기본 `STOPPED`인 채 남는다.
- 잡 `us_equity_krw_job`은 이 그룹 + 상류 `equity_prices_daily`를 고른다 — 그 silver는 스케줄이 없어,
  빼면 낡은 달러 종가 위에서 매일 빌드되고 아무 테스트도 울지 않는다.
- 스케줄은 19:00 KST·기본 `STOPPED`. 환율(10:00 KST)·시세(18:00 KST) 적재보다 뒤다.
- 환율 파티션 시작일은 `polygon_market`과 같다. 켜기 전 **두 bronze를 그 시작일부터 백필**한다.

## 검증

카탈로그에 데이터가 생긴 뒤:

1. `dbt build --select fqn:us_equity_krw` — 위 게이트 전부 PASS.
2. **값 대조** — Frankfurter에 `base=USD&symbols=KRW`로 같은 날짜를 물어 `fx_usdkrw_daily.usd_krw`와
   일치하는지 본다(원천이 직접 교차한 값이라 반올림 차만 남아야 한다).
3. `fx_lag_days` 분포 — 대부분 0, 휴장일만 1~4.

## 거버넌스

원화 종가는 Polygon 종가의 파생이라 **재배포 금지를 상속**하고, 환율 열은 ECB **출처 표기 의무**를 진다.
판정 정본은 [`../security/datasets.md`](../security/datasets.md).
