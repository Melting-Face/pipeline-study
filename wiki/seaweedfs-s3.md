# 클러스터 밖에 S3를 두기 — 수명을 가르고 이름으로 잇는다

> 스터디 장. SeaweedFS를 클러스터 밖 컨테이너로 두고 파드에서 ExternalName으로 닿게 하면서 배운 것.
> 결론부터 쓰면 — **스토리지는 클러스터와 수명이 달라야 하고,
> 호환 스토어의 결함은 쓰기가 아니라 나중의 읽기에서 드러난다.**

## 개념

오브젝트 스토리지는 파일시스템이 아니라 「키 → 바이트열 + 메타데이터」의 평평한 저장소다.
디렉터리도 부분 수정도 없고 쓰기는 객체 단위 교체이며 나열은 프리픽스 질의다.
S3 API가 사실상의 공통 계약이라 스토어를 바꿔도 클라이언트가 그대로인 이식성이 생긴다.
그러나 이식성은 계약의 범위까지다. 호환 스토어마다 구현한 부분집합이 다르다.

- 공식 문서: [SeaweedFS](https://github.com/seaweedfs/seaweedfs) ·
  [Kubernetes — Service ExternalName](https://kubernetes.io/docs/concepts/services-networking/service/#externalname)

## 이 저장소에서의 쓰임

- S3는 클러스터 밖 컨테이너다. 소유는 Terraform 스택 A의 `docker_container.seaweedfs`이고
  버킷은 `terraform_data.seaweedfs_buckets`가 만든다.
  스택 분할의 이유는 [terraform-stacks.md](terraform-stacks.md)에 있다.
- 데이터는 호스트 바인드 마운트 `seaweedfs/data`에 있어 컨테이너를 교체하거나 스택을 destroy해도 남는다.
  Terraform은 이 디렉터리를 만들지도 지우지도 않고 버킷도 생성만 한다.
- 파드는 `http://seaweedfs:8333`으로 닿는다. 그 이름을 [`gitops/charts/storage-external/`](https://github.com/Melting-Face/pipeline-study/blob/main/gitops/charts/storage-external/templates/service.yaml)의
  `Service seaweedfs`가 받아 kind 네트워크 별칭 `seaweedfs-ext`로 넘긴다.
- 별칭을 Service와 같은 이름으로 두지 않는 이유는 클러스터 DNS 검색 경로에서 CNAME이 자기 자신을 가리킬 수 있어서다.
- ExternalName은 엔드포인트를 갖지 않는다. endpointslice가 생기면 다른 무언가가 같은 이름으로 답하는 것이다.
- 레이크하우스의 바닥이다. Iceberg 데이터와 메타데이터, Flink 체크포인트가 모두 여기를 본다.
  카탈로그 Postgres 백업도 배선되면 이 자리를 본다. 지금 그 배선은 꺼져 있다.
  카탈로그 쪽은 [iceberg-catalog.md](iceberg-catalog.md)다.
- 정확성은 AWS SDK 체크섬 설정으로 지킨다. 클라이언트에 `AWS_REQUEST_CHECKSUM_CALCULATION=when_required`를 유지한다.
- 현행 구현의 선택 근거와 교체 후보 평가는 [docs/architectures/storage.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/storage.md),
  체크섬 규칙은 [docs/conventions/k8s/checksum.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/k8s/checksum.md)가 정본이다.

## 실습

**띄우기** — 스택 A를 apply하면 컨테이너와 버킷이 함께 생긴다.
순서와 명령은 [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 2단계 「③ 클러스터 · S3」를 따른다.
S3를 먼저 올리는 것은 운영자 몫이다. 컴포즈 쪽 컨테이너가 의존으로 표현할 수 없기 때문이다.

**확인 1 — 컨테이너 헬스**

출처: [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 2단계 「③ 클러스터 · S3」

```shell
podman inspect -f '{{.State.Health.Status}}' seaweedfs
```

```text
# 관측: podman inspect -f '{{.State.Health.Status}}' seaweedfs
healthy
```

- 성공 기준: `healthy`가 보인다.

**확인 2 — 파드로 이어주는 Service**

출처: [docs/setup/local-k8s.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup/local-k8s.md) 5단계 「⑤ 앱 수렴」

```shell
kubectl get svc seaweedfs -n default
```

```text
# 관측: kubectl get svc seaweedfs -n default
NAME        TYPE           CLUSTER-IP   EXTERNAL-IP     PORT(S)    AGE
seaweedfs   ExternalName   <none>       seaweedfs-ext   8333/TCP   <경과>
```

- 성공 기준: `TYPE`이 `ExternalName`이고 `EXTERNAL-IP` 칸이 별칭 `seaweedfs-ext`다.

**확인 3 — 호스트에서 S3 포트가 응답하는가**

주소 `http://localhost:8333`은 [docs/setup.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/setup.md) §3-1에 있다.
아래 curl 형태는 저장소 문서의 명령이 아니라 관측 시 쓴 형태다. 인증 없이 부르는 음성 대조다.

```shell
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8333/
```

```text
# 관측: curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8333/   (무인증 요청)
403
```

- 성공 기준: `403`이다. 무인증 요청이 거부된다는 것은 서버가 살아 있고 인증을 검사한다는 뜻이다.
  연결 실패나 `200`이면 기대와 다른 것이다.
- 이 값은 도달 여부만 말한다. 객체가 온전한지는 말하지 않는다.

**내리기** — 컨테이너를 내려도 `seaweedfs/data`는 남는다. 스택 A destroy는 클러스터까지 지우는 비가역 작업이라
[terraform-stacks.md](terraform-stacks.md)의 절차를 먼저 읽는다.

## 고장 대응

| 증상 | 먼저 볼 것 | 처방 |
| --- | --- | --- |
| 쓰기는 성공하는데 나중에 읽는 쪽이 파싱에 실패한다 | 객체 앞뒤에 청크 프레이밍 바이트가 붙었는가 | 클라이언트의 체크섬 env를 `when_required`로 둔다. 적용 대상 전수는 checksum.md §4 |
| `INVALID_ACCESS_KEY_ID during UploadPart`가 난다 | 같은 키로 체크섬 모드만 바꾸면 통과하는가 | 자격증명을 뒤지기 전에 체크섬 모드를 먼저 본다. checksum.md §6 |
| `No writable volumes and no free volumes left` | 볼륨 슬롯 상한 | 체크섬이 아니라 볼륨 문제다. checksum.md §5 |
| 같은 클라이언트로 다른 버킷은 성공한다 | 버킷별 자원 | 체크섬이 아니다. checksum.md §5 |
| 체크섬 env를 넣었는데 증상이 그대로다 | env가 켜진 상태에서 재현되는가 | 가설을 버린다. 넣은 채 다른 원인을 찾지 않는다 |

다섯 행 모두 checksum.md §5와 §6에 적힌 증상이다.

## 함정

### 호환 스토어는 쓰기 성공으로 손상을 감춘다

출처: [docs/architectures/storage.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/architectures/storage.md) 「교체 검토의 동기」.
SeaweedFS는 AWS SDK의 flexible checksum을 풀지 못해 `aws-chunked` 프레이밍 바이트가 객체 내용에 그대로 저장된다.
쓰기는 성공 응답을 받고 끝나서, 깨진 사실은 그 객체를 처음 읽는 다른 프로세스에서 드러난다.
우회 설정을 한 곳이라도 빠뜨리면 그 경로가 조용히 샌다. driver에만 주고 executor에 주지 않는 식이다.

### 「Java 경로는 무관」은 무조건이 아니다

출처: [docs/conventions/k8s/checksum.md](https://github.com/Melting-Face/pipeline-study/blob/main/docs/conventions/k8s/checksum.md) §3.
종전 서술은 쓰인 시점에는 참이었다. 러너의 번들 버전을 올린 커밋이 이 규약 문서를 변경 파일에 넣지 않아 서술이 낡았다.
영향 경로는 언어가 아니라 SDK 버전이 가른다.

### 문서의 침묵을 지원 여부로 읽지 않는다

출처: storage.md 「문서로는 후보를 가릴 수 없다」.
후보 스토어의 공식 문서에서 체크섬 트레일러 관련 어휘가 한 번도 매칭되지 않았다.
이것은 부재의 증거가 아니라 관측 경로가 그 축을 다루지 않는다는 뜻이다.
현행 SeaweedFS도 어디에도 적혀 있지 않았고 실측으로 드러났다.
판정 수단은 실측 프로브 [`scripts/storage_conformance_probe.py`](https://github.com/Melting-Face/pipeline-study/blob/main/scripts/storage_conformance_probe.py)이고,
현행 SeaweedFS가 체크섬 축에서 반드시 실패해야 프로브가 살아 있다는 대조 규율이 같은 문서에 있다.
