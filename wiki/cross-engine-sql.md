# 엔진마다 값이 갈리는 SQL — 같은 자리에서 두 번 틀렸다

> 스터디 장. 쿼리 엔진을 Trino에서 Spark로 옮기며 배운 것.
> 결론부터 쓰면 — **"양쪽에서 돈다"와 "양쪽에서 같은 값이 나온다"는 다른 기준이다.**

## 개념

dbt는 SQL 변환을 **모델** 단위로 관리한다.
엔진마다 다른 방언은 어댑터와 매크로가 흡수한다.
Trino는 분산 SQL 쿼리 엔진이다. 이 저장소에서 dbt는 k8s Trino에서 SQL을 실행한다.

- 공식 문서: [dbt-trino](https://github.com/starburstdata/dbt-trino) · [Trino](https://trino.io/docs/current/) ·
  [Trino Iceberg connector](https://trino.io/docs/current/connector/iceberg.html)

## 이 저장소에서의 쓰임

- 배포: ArgoCD 앱 [`gitops/apps/trino/`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/apps/trino)이다.
  upstream 차트를 `config.yaml`·`values.yaml`로 건다.
- dbt 접속: [`profiles.yml`](https://github.com/Melting-Face/pipeline-study/blob/main/dagster/dockerfile.d/src/dbt_pipelines/profiles.yml)의
  `dev`·`prod` 타깃이 `TRINO_HOST`로 붙는다. 타깃 이름은 엔진이 아니라 환경이다.
- 방언 흡수: [`macros/cross_engine.sql`](https://github.com/Melting-Face/pipeline-study/blob/main/dagster/dockerfile.d/src/dbt_pipelines/macros/cross_engine.sql).
  아래 「함정」의 `elapsed` 매크로가 이 파일에 있다.
- 유지보수: [`defs/maintenance.py`](https://github.com/Melting-Face/pipeline-study/blob/main/dagster/dockerfile.d/src/src/dagster_project/defs/maintenance.py)가
  `ALTER TABLE … EXECUTE`를 실행한다.
- 카탈로그: 다른 엔진과 같은 Iceberg JDBC 카탈로그 `iceberg`를 본다.

경로 출처는 [docs/architectures/trino.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/trino.md) 「어떻게 도나」다.
아래 「함정」은 엔진을 Spark로 옮기던 때의 회고다. 이행은 번복됐고 dbt 엔진은 Trino다
([docs/redesign.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/redesign.md) §3 「Trino 제거 결정 번복」).

## 실습

**띄우기** — 따로 띄우지 않는다. Trino는 상시 앱이라 로컬 클러스터 수렴으로 함께 뜬다
([docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-1 접근 경로 표 「✅ 상시」).

**확인** — 앱 수렴을 먼저, dbt 파싱을 다음에 본다.

출처: [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3 「올리기 (단축 경로)」

```shell
kubectl get applications -n argocd        # 전부 Synced · Healthy 면 수렴
```

```text
# 관측: kubectl get applications -n argocd
NAME               SYNC STATUS   HEALTH STATUS
airflow            Synced        Healthy
catalog-postgres   Synced        Healthy
cnpg-operator      Synced        Healthy
flink-operator     Synced        Healthy
prometheus         Synced        Healthy
spark-operator     Synced        Healthy
storage-external   Synced        Healthy
trino              Synced        Healthy
```

- 성공 기준: 전부 `Synced` · `Healthy`면 수렴이다(같은 줄 주석). 위는 8개 앱이 모두 그 상태인 관측이다.
- 수렴 직전에는 `airflow`·`prometheus`가 `Progressing`으로 남는다. 이 둘이 마지막에 수렴했다.
- Trino UI는 `http://trino.localtest.me:8080`이다(§3-1). `…/ui/`가 200이면 열린 것이다.

```text
# 관측: curl -s -o /dev/null -w '%{http_code}\n' http://trino.localtest.me:8080/ui/
200
```

출처: [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §1 「저장소 준비」

```shell
(cd dagster/dockerfile.d/src \
  && uv run dbt deps  --project-dir dbt_pipelines \
  && uv run dbt parse --project-dir dbt_pipelines --profiles-dir dbt_pipelines)
```

```text
# 관측: uv run dbt deps --project-dir dbt_pipelines
...
<시각>  Installed from version 0.19.0
<시각>  Updated version available: 0.21.0
<시각>
<시각>  Updates available for packages: ['dbt-labs/dbt_utils', 'godatadriven/dbt_date']
Update your versions in packages.yml, then run dbt deps
```

```text
# 관측: uv run dbt parse --project-dir dbt_pipelines --profiles-dir dbt_pipelines
...
<시각>  Registered adapter: trino=1.10.6
<시각>  Unable to do partial parsing because saved manifest not found. Starting full parse.
<시각>  [WARNING]: Configuration paths exist in your dbt_project.yml file which do not apply to any resources.
There are 1 unused configuration paths:
- models.dbt_pipelines.eicu
<시각>  Performance info: dbt_pipelines/target/perf_info.json
```

- 성공 기준: `dbt parse`가 에러 없이 끝나고 마지막 줄이 `Performance info: dbt_pipelines/target/perf_info.json`이다.
- `Configuration paths … unused: models.dbt_pipelines.eicu` 경고는 실패 신호가 아니다.
  위 관측에서 이 경고와 함께 parse가 끝까지 갔다.
- `dbt deps`의 `Updates available for packages` 안내도 설치가 끝난 뒤의 버전 안내다.
- `dbt parse`는 엔진에 접속하지 않는다. setup.md 「절별 전제」에서 §1의 전제는 「없음」이다.
- ⚠️ 파싱 통과는 「같은 값」의 증거가 아니다. 아래 「함정」의 마지막 절이 그 이유다.

**내리기** — 상시 앱이라 내리지 않는다.

## 고장 대응

| 증상 | 먼저 볼 것 | 처방 |
| --- | --- | --- |
| `elapsed: 이 어댑터용 구현이 없다` 컴파일 에러 | 새 엔진용 `<adapter>__elapsed` 구현이 있는가 | 그 어댑터용 구현을 추가한다 |
| 임계값 비교의 코호트 행이 엔진마다 다르다 | `dbt.datediff`·`dbt.dateadd`를 썼는가 | 어댑터별 구현 둘을 나란히 읽고, 갈리면 프로젝트 dispatch 매크로로 흡수한다 |
| `INTERVAL` 줄에서 컴파일이 깨진다 | `INTERVAL` 값의 따옴표가 엔진마다 같은가 | 크로스 어댑터 내장 매크로를 쓰되, 둘째 행처럼 구현을 확인한다 |

세 행 모두 아래 「함정」에서 옮겼다.

## 함정

### 문제 설정

이 프로젝트는 dbt 모델 20여 개를 Trino에서 Spark로 옮기는 중이다.
dbt는 어댑터가 방언을 흡수해 주니, 소박하게 생각하면 타깃만 바꾸면 될 것 같다.

실제로 처음 부딪히는 건 **문법**이다. 예를 들어 시간 빼기가 이렇게 갈린다.

```sql
-- Trino:  따옴표가 있다
ih.endtime - INTERVAL '1' HOUR

-- Spark:  따옴표가 없다
ih.endtime - INTERVAL 1 HOUR
```

이건 착한 실패다. **컴파일이 깨지므로 즉시 안다.**
그리고 dbt는 이런 걸 위해 **크로스 어댑터 내장 매크로**를 준다.
`{{ dbt.datediff(...) }}`처럼 쓰면 어댑터가 알아서 자기 방언으로 전개해 준다.

문제는 그다음이다.

### 내장 매크로가 보장하는 것과 보장하지 않는 것

`dbt.datediff`의 어댑터별 구현을 **설치본 소스를 열어** 읽어 봤다.

| 구현 | `'hour'`의 의미 |
| --- | --- |
| Trino 네이티브 `date_diff('hour', a, b)` | **경계 교차 횟수** |
| `trino__datediff(..., 'hour')` | `day*24 + hour(b) - hour(a)` — 네이티브와 같다 |
| `spark__datediff(..., 'hour')` | `ceil((unix(b) - unix(a)) / 3600)` — **경과시간 올림** |

셋이 같지 않다. 구체적인 값으로 보면 분명해진다.

```text
11:00 → 12:59

경계 교차:  11시대 → 12시대  =  1
경과시간 :  1시간 59분 → ceil  =  2
```

**같은 SQL, 같은 데이터, 다른 답.** 그리고 아무도 에러를 내지 않는다.

이게 왜 치명적이냐면, 이 값이 **임계값 비교**에 쓰이기 때문이다.
`>= 14`, `<= 5` 같은 조건 옆에서 1과 2가 갈리면 **행이 코호트에 들어가거나 빠진다.**
파이프라인은 초록불이고 테이블도 채워지는데 **피처가 달라져 있다.**

⇒ 배운 것: **dbt 내장 크로스 데이터베이스 매크로는 *문법의 이식성*을 보장하지
*값의 동일성*을 보장하지 않는다.** 문서 어디에도 후자를 약속한 적이 없다.
"내장이니 안전하다"는 내가 멋대로 읽은 것이었다.

### 같은 자리에서 두 번째로 틀렸다

`datediff`를 걸러낸 뒤, 이 프로젝트는 오랫동안 **`dbt.dateadd`는 안전하다고
분류하고 권장**했다. 틀렸다. 나중에 소스를 열어보니 이랬다.

| 구현 | `dateadd('hour', n, ts)` 전개 |
| --- | --- |
| `trino__dateadd` | `date_add('hour', n, ts)` — 네이티브 타임스탬프 연산 |
| `spark__dateadd` | `to_timestamp(to_unix_timestamp(ts) + n*3600)` — **epoch 초 왕복** |

Spark 쪽이 epoch **초**를 경유하는 바람에 두 갈래로 갈린다.

- **초 이하가 잘린다** — `to_unix_timestamp`는 초 단위라 밀리·마이크로초가 버려진다.
  Trino는 보존한다.
- **타임존을 탄다** — `to_unix_timestamp`/`to_timestamp`가 세션 타임존 설정을 참조한다.
  명시하지 않으면 JVM 기본값에 좌우되므로, **서버가 어디서 뜨느냐에 따라 값이 바뀔 수 있다.**

#### 왜 이건 놓쳤나

여기가 이 노트에서 제일 배운 게 많은 지점이다.

`datediff`는 **"차이를 어떻게 세는가"라는 질문이 이름에 드러나** 있다.
경계를 세나, 길이를 재나? 애매하니 의심받았고, 그래서 소스를 열어 봤다.

`dateadd`는 **"더하기"라 의미가 자명해 보였다.** 한 시간 더하는 데 무슨 해석의 여지가
있겠는가. 그래서 안 열어 봤다.

그런데 실제로 갈린 지점은 **덧셈이 아니었다.** 덧셈을 하려고 거쳐 간
**중간 표현(epoch 초)** 이었다. 연산 자체는 자명했고, 자명한 연산에 도달하기까지의
경로가 갈렸다.

⇒ 의심의 트리거를 바꿨다.

```text
전:  이 연산은 의미가 애매한가?        → 이름만 보고 판단 가능 → 틀렸다
후:  이 구현은 중간 표현을 경유하는가?  → 소스를 열어야만 보인다
```

**후자는 이름을 아무리 봐도 알 수 없다.** 그래서 새 내장 매크로를 도입할 때는
`<adapter>__<macro>` 구현 **두 개를 나란히 놓고 읽는** 절차를 규칙으로 만들었다.

### 해법 — 정본을 정하고 재현한다

의미론이 갈리는 것은 내장 매크로를 버리고 **프로젝트 dispatch 매크로**로 흡수한다.
Trino 네이티브를 **정본**으로 두고 Spark에 같은 수식을 재현하는 방향이다.

```sql
{% macro elapsed(datepart, from_ts, to_ts) -%}
    {{ return(adapter.dispatch('elapsed', 'dbt_pipelines')(datepart, from_ts, to_ts)) }}
{%- endmacro %}

{% macro trino__elapsed(datepart, from_ts, to_ts) -%}
    date_diff('{{ datepart }}', {{ from_ts }}, {{ to_ts }})
{%- endmacro %}

{% macro spark__elapsed(datepart, from_ts, to_ts) -%}
    {#- Trino의 경계교차 수식을 그대로 옮긴다 -#}
    (datediff(to_date({{ to_ts }}), to_date({{ from_ts }})) * 24
        + hour({{ to_ts }}) - hour({{ from_ts }}))
{%- endmacro %}
```

설계에서 한 가지만 강조하고 싶다 — **`default__` 구현**이다.

```sql
{% macro default__elapsed(datepart, from_ts, to_ts) -%}
    {{ exceptions.raise_compiler_error(
        "elapsed: 이 어댑터용 구현이 없다 — <adapter>__elapsed 추가 필요"
    ) }}
{%- endmacro %}
```

여기에 그럴듯한 기본 구현을 넣고 싶은 유혹이 있다. 넣으면 안 된다.
**새 엔진을 붙였을 때 에러 없이 틀린 값을 내는 대신 즉시 실패**해야 한다.
이 노트 전체가 "에러 없이 틀리는 것"에 관한 이야기이고, 그 대책은 **에러를 내고 멈추게
만드는 것**이다.

### 그래서 무엇을 배웠나

**`dbt compile` 통과를 이행 완료로 읽지 않는다.**
compile이 말해주는 것은 **구문이 파서에 맞았다**는 것뿐이다.
두 엔진이 같은 값을 내는지는 **실행해서 대조해야만** 알 수 있다.

린트도 마찬가지다. 이 프로젝트는 SQL 린트 게이트를 갖고 있지만, 그 게이트가 보는 것은
**스텁으로 치환된 SQL**이지 컴파일된 SQL이 아니다. 즉 **매크로가 엔진별로 같은 값을
내는지는 린트의 관측 범위 밖**이다. 초록불이 무엇을 보증하는지 알고 읽어야 한다.

이건 첫 노트에서 만난 것과 **같은 형태**다.
[Dagster 노트](dagster-orchestration.md)에서는 에셋이 에러 없이 사라졌고,
여기서는 값이 에러 없이 달라졌다. 둘 다 **성공 신호가 뜬 채로 틀린다.**

그래서 규칙 하나를 붙여 두었다 — **수치에는 산출 엔진을 병기한다.**
같은 SQL이 엔진에 따라 값이 갈린 적이 실제로 있으므로, 리포트의 숫자는
"무엇을 셌는가"뿐 아니라 **"어디서 셌는가"** 까지 적어야 재현된다.

---

[← 홈으로](Home.md) · [읽는 법](how-to-read.md) · [Spark를 K8s 위로](spark-on-k8s.md)
