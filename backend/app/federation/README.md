# Catalog Federation (카탈로그 페더레이션)

다수의 팀이 각각 운영하는 Argus Catalog 인스턴스를 **하나로 연합**해, 한 화면에서
통합 검색·탐색하는 기능. Trino 가 여러 카탈로그를 연합하듯 **메타데이터 카탈로그**를
연합한다.

## 모델 (하이브리드)

| 모드 | 동작 | 비고 |
|------|------|------|
| `LIVE` | 검색을 요청 시점에 각 peer 로 fan-out(scatter-gather) | 항상 최신, 복제 없음 |
| `HARVEST` | peer 메타데이터를 주기적으로 pull → 로컬 미러 + **재임베딩** → 통합 검색 | 빠름·일관된 시맨틱·에어갭 친화 |
| `HYBRID` | HARVEST(검색) + LIVE(상세 drill-down) | 권장 최종형 |

> peer 마다 임베딩 provider/모델이 다르면 벡터 공간이 달라 cross-instance 유사도
> 비교가 깨진다. HARVEST 가 허브 모델로 **재임베딩**해 일관성을 확보하는 이유다.

## 현재 구현

### Phase 0 — `LIVE` scatter-gather
- peer 레지스트리 CRUD: `FederatedInstance` (`models.py`)
- 동시 fan-out 통합 검색: 로컬 `hybrid_search` + LIVE peer 들을 `asyncio.gather`
  로 병합, 도달 실패 peer 는 `instances_failed` 로 degrade (`service.py`)
- peer 호출 클라이언트: httpx, 짧은 타임아웃 (`client.py`)
- 이 인스턴스를 peer 로 노출하는 export API (`export_router.py`)

### Phase 1 — `HARVEST` 미러 + 재임베딩 + 서비스 토큰
- **미러 테이블**: `FederatedDataset`(peer 데이터셋 read-only 복제) +
  `FederatedDatasetEmbedding`(허브 모델 재임베딩) + `FederationSyncRun`(실행 이력)
- **HARVEST job** (`harvester.py`): peer export 목록을 페이지로 가져오기 → upsert →
  `source_text` 변경분만 **허브 임베딩 모델로 재임베딩** → 사라진 항목 prune
- **주기 스케줄러** (`scheduler.py`): ACTIVE·미러모드 peer 를 각자 `sync_interval_sec`
  주기로 가져온다. lifespan 에서 기동(`federation.harvest_enabled`).
- **미러 검색** (`service.search_mirror`): 가져온 데이터를 **로컬 pgvector + 키워드**로 검색
  → `federated_search` 가 HARVEST/HYBRID peer 는 미러로, LIVE peer 는 fan-out 으로 라우팅.
  → peer 가 죽어도 검색 동작, cross-instance 시맨틱 일관성 확보.
- **서비스 토큰 인증** (`auth.py`): `ARGUS_FEDERATION_TOKEN`(또는 config) 설정 시
  export API 가 `Authorization: Bearer` 를 상수시간 비교로 검증.

### Phase 2 — LIVE drill-down + visibility 거버넌스
- **drill-down 프록시** (`service.federated_dataset_detail/sample`): federated URN
  (`{instance_key}::{remote_urn}`)을 해석해 원 인스턴스에서 상세 메타데이터(스키마/
  태그/소유자/속성)와 샘플 데이터(parquet→JSON)를 실시간 조회. peer 오류는 404/502 로 변환.
- **visibility 거버넌스** (`visibility.py`): 노출자 측 정책으로 export 의 목록·검색·
  드릴다운 전 경로에서 PII 제외(`export_exclude_pii`)·민감도 제외
  (`export_exclude_sensitivity`)·데이터소스 allow-list(`export_datasource_allowlist`)를
  적용. 노출 불가 데이터셋은 드릴다운에서 404 로 숨겨 존재 여부도 가린다.
- **출처 표시**: 검색·드릴다운 응답에 `source_instance_key/name/base_url` 포함
  (UI 출처 배지용 데이터는 백엔드에서 제공).

### Phase 3 — 운영 강화 (증분 동기화 + circuit breaker)
- **증분 동기화** (`harvester.py`): watermark(`max remote_updated_at`) 이후 변경분만
  가져오기(`export/datasets?updated_after=`). 변경분만 보므로 prune 은 건너뛰고, 삭제 반영은
  전체 재동기화(`harvest?full=true` 또는 watermark 없는 첫 동기화)에서 수행한다.
- **circuit breaker** (`breaker.py`): LIVE peer 호출(검색 fan-out·드릴다운)을 peer 단위로
  감싸 연속 실패가 `breaker_threshold` 에 도달하면 `breaker_cooldown_seconds` 동안 회로를
  열어 즉시 실패(503)시킨다. 죽은 peer 가 연합 전체를 느리게 만드는 것을 막는다.

### Phase 4 — cross-instance 리니지 stitching
- **리니지 미러** (`FederationLineage`): peer 리니지 엣지(URN→URN)를 가져와 instance
  단위 전량 교체로 보관(`export/lineage`). harvest 시 best-effort 로 함께 가져온다.
- **URN 매칭 stitch** (`service.build_lineage_graph`): 엔드포인트 URN 은 데이터소스 전역
  유일이라, 로컬 리니지와 여러 peer 가 보고한 미러 리니지를 같은 URN 키로 합쳐
  BFS(depth) 그래프를 만든다. 각 노드는 로컬/미러(어느 peer)/미해석으로 해석되고,
  각 엣지는 보고 출처(`reported_by`)를 갖는다 → 팀 경계를 넘는 데이터 흐름을 한눈에.

### Phase 5 — 샘플 미러링 + 로컬 승격(import)
- **미러 메타 확장**: 미러가 `display_name`(논리명)·`field_count`(컬럼 수)·
  `remote_created_at`(원본 등록 시각)도 함께 가져온다.
- **샘플 미러링** (`samples.py`): HARVEST 시 `sample` capability 가 켜진 peer 의 샘플을
  받아 로컬 `data_dir/federation/samples/{instance_id}/{federation_dataset_id}.json`
  에 저장하고(`federation_datasets.has_sample`), 샘플 드릴다운은 **미러 우선**(오프라인)
  → 없으면 LIVE 폴백. 로컬 데이터셋 샘플 경로와 **분리된 네임스페이스**. 증분은 미보유분만,
  전체 동기화는 전량 갱신. prune·인스턴스 삭제 시 샘플 파일도 정리.
- **로컬 승격(import)** (`service.import_federated_dataset`): 미러 데이터셋을 peer
  드릴다운으로 전체 메타(스키마·태그·소유자·용어집·DDL·확장·샘플)를 받아 전용
  **"Federation Imports"** 데이터소스에 **1급 로컬 데이터셋**으로 복사(1회 스냅샷,
  이후 peer 와 분리). URN 은 `{ds}.{instance_key}.{원본경로}.dataset` 로 유일.
  - **태그 정책**: peer 태그는 **매칭만**(이미 있는 로컬 태그에만 연결, 새로 만들지 않음)
    → 로컬 태그 목록 오염 방지. 원본 태그/용어는 `imported_tags`·`imported_glossary`
    속성으로 보존. 모든 import 데이터셋에 **"페더레이션"** 마커 태그를 부착(용어집도 매칭만).
  - **provenance**: `imported_from`(federated URN)·`imported_from_instance` 속성으로 출처
    기록, 같은 URN 재import 는 409 로 차단.

### API

> 권한 표기: 로그인=인증된 사용자, admin=관리자, 토큰\*=export 서비스 토큰

| Method | Path | 설명 | 권한 |
|--------|------|------|------|
| `GET` | `/api/v1/federation/stats` | 관측 요약(미러 카운트·최근 동기화·breaker) | 로그인 |
| `GET` | `/api/v1/federation/instances` | peer 목록 | 로그인 |
| `POST` | `/api/v1/federation/instances` | peer 등록 | admin |
| `PUT` | `/api/v1/federation/instances/{id}` | peer 수정 | admin |
| `DELETE` | `/api/v1/federation/instances/{id}` | peer 삭제 | admin |
| `GET` | `/api/v1/federation/instances/{id}/health` | 도달성 점검 | 로그인 |
| `POST` | `/api/v1/federation/instances/{id}/harvest?full=` | 단일 peer 즉시 가져오기(기본 증분) | admin |
| `POST` | `/api/v1/federation/harvest?full=` | 전체 미러 peer 즉시 가져오기(기본 증분) | admin |
| `GET` | `/api/v1/federation/instances/{id}/sync-runs` | HARVEST 실행 이력 | 로그인 |
| `GET` | `/api/v1/federation/search?q=` | 통합 연합 검색 | 로그인 |
| `GET` | `/api/v1/federation/datasets/detail?urn=` | 드릴다운 — 상세 메타데이터 프록시 | 로그인 |
| `GET` | `/api/v1/federation/datasets/sample?urn=` | 드릴다운 — 샘플 데이터 프록시 | 로그인 |
| `GET` | `/api/v1/federation/datasets/lineage?urn=&depth=` | cross-instance 리니지 그래프(stitch) | 로그인 |
| `POST` | `/api/v1/federation/datasets/import` | 미러 데이터셋을 로컬 카탈로그로 승격(import) | admin |
| `GET` | `/api/v1/federation/export/search?q=` | (peer 노출용) 이 인스턴스 검색 | 토큰* |
| `GET` | `/api/v1/federation/export/datasets` | (peer 노출용) 데이터셋 목록(HARVEST) | 토큰* |
| `GET` | `/api/v1/federation/export/dataset?urn=` | (peer 노출용) 상세 메타데이터 | 토큰* |
| `GET` | `/api/v1/federation/export/dataset/sample?urn=` | (peer 노출용) 샘플 데이터 | 토큰* |
| `GET` | `/api/v1/federation/export/lineage` | (peer 노출용) 리니지 엣지 | 토큰* |

\* `federation_export_token` 미설정 시 인증 비강제(개발, `/external` 과 동일). 설정 시 강제.

### 모드별 검색/드릴다운 경로

| peer.mode | 검색 | 상세·샘플 드릴다운 | 비고 |
|-----------|------|--------------------|------|
| `LIVE` | 요청 시 fan-out | LIVE 프록시 | 항상 최신, 복제 없음 |
| `HARVEST` | 로컬 미러(pgvector) | LIVE 프록시 | 빠름·일관·내결함성 |
| `HYBRID` | 로컬 미러(pgvector) | LIVE 프록시 | 권장 (검색=미러, 상세=LIVE) |

### 동작 예시

```bash
# 1) Payments 팀 카탈로그를 HARVEST peer 로 등록
curl -XPOST localhost:4600/api/v1/federation/instances -H 'Authorization: Bearer <admin>' \
  -d '{"instance_key":"team-payments","name":"Payments","base_url":"https://catalog.pay.internal","mode":"HARVEST"}'

# 2) 즉시 가져오기(또는 스케줄러가 sync_interval_sec 주기로 자동 실행)
curl -XPOST localhost:4600/api/v1/federation/instances/1/harvest -H 'Authorization: Bearer <admin>'

# 3) 로컬 + 미러 + LIVE peer 통합 검색
curl 'localhost:4600/api/v1/federation/search?q=transaction' -H 'Authorization: Bearer <token>'

# 4) 검색 결과의 federated URN 으로 상세/샘플 드릴다운(원 인스턴스 실시간 프록시)
curl 'localhost:4600/api/v1/federation/datasets/detail?urn=team-payments::mysql.pay.txn.dataset' \
  -H 'Authorization: Bearer <token>'
```

### 설정

| 키 (config `federation.*` / env) | 기본값 | 설명 |
|----------------------------------|--------|------|
| `export_token` / `ARGUS_FEDERATION_TOKEN` | `""` | export API 서비스 토큰(빈 값=비강제) |
| `harvest_enabled` | `true` | HARVEST 스케줄러 기동 여부 |
| `harvest_tick_seconds` | `300` | 스케줄러 점검 주기(초) |
| `harvest_samples` | `true` | HARVEST 시 미러 데이터셋 샘플 데이터도 가져와 저장 |
| `sample_limit` | `100` | 미러 샘플 저장 시 받아올 최대 행 수 |
| `export_exclude_pii` | `false` | PII 포함 데이터셋 노출 제외 |
| `export_exclude_sensitivity` | `""` | 노출 제외 민감도 등급(쉼표, 예: `RESTRICTED,CONFIDENTIAL`) |
| `export_datasource_allowlist` | `""` | 노출 허용 데이터소스 이름(쉼표, 빈 값=전체) |
| `breaker_threshold` | `3` | LIVE peer 연속 실패 임계치(회로 open) |
| `breaker_cooldown_seconds` | `60` | 회로 open 유지 시간(초) |

### 관측성 (observability)

`GET /federation/stats` 가 peer 별 미러 데이터셋/리니지 카운트, 최근 동기화 이력
(`federation_sync_runs`), circuit breaker 상태(`breaker.snapshot()`)를 집계한다.
프론트엔드 "관측성" 탭에서 요약 카드 + peer 상태 테이블로 노출한다.

## 로드맵 (후속 단계)

- **리니지 그래프 시각화** — 현재 프론트엔드는 엣지 리스트. react-flow 등으로 노드 그래프.
- **리니지 고도화** — 컬럼 수준 cross-instance 매핑, 증분 리니지 동기화.
