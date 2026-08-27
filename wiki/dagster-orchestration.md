# Dagster 오케스트레이션 — 에러 없이 사라진 에셋들

> 학습 노트. 이 프로젝트에서 Dagster로 적재 파이프라인을 짜며 배운 것을 정리한다.
> 결론부터 쓰면 — **Dagster에서 가장 무서운 실패는 예외가 아니라 침묵이다.**

## 왜 Dagster였나

이 프로젝트가 다루는 것은 "매일 도는 잡"이 아니라 **테이블**이다.
원천 CSV를 Iceberg bronze로 넣고, dbt가 silver/gold로 올리고,
노트북과 리포트가 그걸 읽는다. 관심사가 **작업의 순서**가 아니라
**데이터의 상태**라면, 태스크 중심 스케줄러보다 **에셋 중심** 모델이 맞는다.

Dagster는 파이프라인의 단위를 *"무엇을 실행하는가"* 가 아니라
*"어떤 테이블이 존재하는가"* 로 잡는다. dbt의 모델 개념과 그대로 포개지므로,
`@dbt_assets`로 dbt 프로젝트를 통째로 lineage에 끌어올 수 있다.

## 에셋은 함수 + 데코레이터로 정의한다

```python
@dg.asset(group_name=GROUP_NAME, io_manager_key=IO_MANAGER_KEY,
          kinds={"python", "iceberg", "bronze"})
def patient(context: dg.AssetExecutionContext, s3: S3Resource) -> pa.Table:
    """EICU patient 원본을 bronze Iceberg 테이블로 적재한다."""
    table = read_csv_gz_table(s3, f"{SOURCE_BASE}/patient.csv.gz")
    context.add_output_metadata({
        "row_count": dg.MetadataValue.int(table.num_rows),
    })
    return table
```

클래스로 감싸거나 커스터마이징을 위해 서브클래싱하지 않는다.
커스터마이징이 필요하면 **선언적 설정**(데코레이터 인자·메타데이터·dbt config)을 먼저 쓴다.

테이블이 수십 개면 팩토리로 찍어내고 싶어지는데, 그러지 않기로 했다.

```python
# 지양 — 탐색성이 죽는다
bronze_assets = [build_csv_to_iceberg_asset(t) for t in TABLES]
```

이유는 **탐색성**이다. 에셋 이름으로 grep해서 정의로 점프할 수 있어야 한다.
팩토리로 만들면 UI에 뜬 이름이 코드 어디에도 문자열로 존재하지 않는다.
공통 로직은 일반 함수로 빼서 재사용하되(DRY), **정의 자체는 각각 명시**한다.

## 조용히 깨진 것 ① — 리소스가 에셋을 삼킨다

가장 오래 못 찾은 버그다.

Dagster는 정의 루트를 재귀 탐색해 **모듈 스코프의 정의 객체**를 자동 수집한다.
그런데 한 모듈에 `@dg.definitions`가 있으면, **그 함수의 반환값이
모듈의 정의 전체를 대체한다.** 같은 파일에 있던 `@asset`은 수집되지 않는다.

```python
# assets.py — 이렇게 두면 아래 에셋이 UI에 영영 뜨지 않는다
@dg.definitions
def resources() -> dg.Definitions:
    return dg.Definitions(resources={"s3": ...})

@dg.asset
def poc_spark_ingest(...): ...      # ← 조용히 사라진다
```

**에러도 경고도 나지 않는다.** 임포트는 성공하고, 파이프라인은 정상 기동하고,
그냥 에셋이 하나 없을 뿐이다. 실제로 이 형태로 작성된 에셋 하나가
UI에 뜬 적이 한 번도 없었다는 걸 한참 뒤에 발견했다.

규칙으로 굳혔다 — **`@dg.definitions`는 `@asset`이 있는 모듈에 두지 않는다.**
리소스 등록은 자산이 없는 별도 모듈(`resources.py`)에 둔다.

## 조용히 깨진 것 ② — future annotations

이건 반대로 시끄럽게 깨지지만, 원인이 엉뚱한 곳에 있어서 헤맸다.

```
DagsterInvalidDefinitionError: Cannot annotate context parameter …
```

Dagster는 `@asset`/op의 `context` 파라미터를 **클래스 identity**로 검사한다.
`from __future__ import annotations`를 켜면 모든 어노테이션이 **문자열**이 되어
이 검사가 실패한다. 파일 맨 위 한 줄이 파일 맨 아래 정의를 깨뜨리는 형태다.

- 자산·op 정의가 있는 모듈에서는 future annotations를 쓰지 않는다.
- 자산이 아닌 공통 헬퍼 모듈은 써도 무방하다.

## 조용히 깨진 것 ③ — 셀렉터가 cwd에 묶인다

`@dbt_assets`로 dbt 모델을 데이터셋별로 나눠 소유할 때, 셀렉터를 이렇게 쓰면 안 된다.

```python
@dbt_assets(select="path:models/eicu")     # ← cwd 글롭이다
```

`path:`는 **현재 작업 디렉터리 기준 글롭**이라, 정의를 로드하는 프로세스의
cwd가 다르면 모델이 **하나도 수집되지 않는다**. 역시 에러가 아니라 0건이다.

```python
@dbt_assets(select="fqn:eicu", project=dbt_project)   # ← 이렇게
```

## 그래서 무엇을 배웠나

세 가지 실패가 전부 **같은 형태**다 — 실패가 예외로 나타나지 않고,
**개수가 조용히 줄어든다.** 정상 기동, 정상 종료, 그냥 에셋이 없다.

그래서 정의를 추가하면 **에셋 수를 센다**.

```bash
dg check defs
```

이게 이 프로젝트에서 *"성공 신호를 의심한다"* 는 원칙이 나온 자리 중 하나다.
**"통과했다"가 *검사했다*인지 *실행됐다*뿐인지 구분해야 한다.**
부정 결과(없음·통과·정상)는 **관측 경로가 살아 있었음을 함께 확인**해야 유효하다.

세는 김에 하나 더 — **그 수치가 무엇을 세는지** 라벨을 정확히 붙인다.
"에셋 9개"가 *정의된 에셋 수*인지 *UI에 뜬 수*인지 *머티리얼라이즈된 수*인지는
전부 다른 값이고, 값이 맞는데 라벨이 틀리면 **검산을 통과한 채로 남는다.**

---

[← 홈으로](Home.md)
