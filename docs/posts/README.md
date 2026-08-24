# 외부 공개 산출물 (posts)

**저장소 밖 사람에게 보이는 글**을 두는 곳이다. 블로그 원고(티스토리 등)·공유용 정리 자료·발표 자료.

- 규칙 정본: [`../conventions/publishing.md`](../conventions/publishing.md)
- 작성 워커: [`tech-writer`](../../.claude/agents/tech-writer.md) — **이 디렉터리에만** 쓸 수 있다
- 근거 수집: [`researcher`](../../.claude/agents/researcher.md)

## [`../analyses/`](../analyses/)와 무엇이 다른가

| | `docs/analyses/` | `docs/posts/` |
| --- | --- | --- |
| 독자 | **이 저장소를 아는 사람** | **모르는 사람** |
| 목적 | 데이터셋의 질문에 답한다(결론) | 그 결론·과정을 **공개**한다 |
| 소유 워커 | `analyst` | `tech-writer` |
| 수치 | gold 모델 경유 | gold 모델 경유 **+ 소규모 셀(<5) 마스킹** |
| 게이트 | 재현성 | **`security` 컨펌 → 사람이 발행** |

## 파일 규약

- `docs/posts/<NN>-<slug>.md` — `NN`=2자리 일련번호, `slug`=영문 kebab-case
- 첨부: `docs/posts/assets/<NN>-<slug>/`
- 프론트매터(`title`·`status`·`target`·`source_of_truth`·`engine`·`security_review`)는
  [`publishing.md` §4](../conventions/publishing.md)

## 🔴 발행은 사람이 한다

```
draft → security 컨펌 → 사용자 확인 → 🧍 사람이 업로드 → status: published
```

워커는 **파일과 발행 체크리스트까지**만 만든다. 외부 발신은 비가역이라 자동화하지 않는 것이 설계다.
`status: published`도 사람이 붙인다.
