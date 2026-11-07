# SPDX-License-Identifier: Apache-2.0
"""페더레이션 API 용 Pydantic 스키마."""

from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_base_url(v: str | None) -> str | None:
    """peer base URL 은 http(s) 절대 URL 만 허용(file://·상대 경로 등 차단)."""
    if v is None:
        return v
    v = v.strip()
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("base_url 은 http:// 또는 https:// 로 시작하는 절대 URL 이어야 합니다")
    if parsed.username or parsed.password:
        raise ValueError("base_url 에 사용자 정보를 넣지 마세요(서비스 토큰을 사용하세요)")
    return v.rstrip("/")


# peer 운영 모드/상태의 허용 값 — 검색·스케줄러·드릴다운이 이 값들로만 분기한다.
FEDERATION_MODES = ("LIVE", "HARVEST", "HYBRID")
FEDERATION_STATUSES = ("ACTIVE", "PAUSED")


def _choice_validator(allowed: tuple[str, ...], label: str):
    """대소문자 무시로 허용 값만 받아 대문자로 정규화(알 수 없는 값은 422)."""
    def _check(v: str | None) -> str | None:
        if v is None:
            return v
        norm = v.strip().upper()
        if norm not in allowed:
            raise ValueError(f"{label} 은(는) {' / '.join(allowed)} 중 하나여야 합니다: {v!r}")
        return norm
    return _check


_validate_mode = _choice_validator(FEDERATION_MODES, "mode")
_validate_status = _choice_validator(FEDERATION_STATUSES, "status")


# ---------------------------------------------------------------------------
# Peer 레지스트리 CRUD
# ---------------------------------------------------------------------------

class FederatedInstanceCreate(BaseModel):
    """peer 등록 요청. status 는 받지 않으며 모델 기본값(ACTIVE)으로 생성된다."""

    instance_key: str = Field(..., min_length=1, max_length=64,
                              pattern=r"^[A-Za-z0-9_-]+$",
                              description="허브 내 peer 식별 키(URN namespace prefix). "
                                          "영문 대소문자·숫자·_·- 만 허용")
    name: str = Field(..., min_length=1, max_length=200)
    base_url: str = Field(..., max_length=500, description="peer 의 base URL")
    auth_token: str | None = Field(None, description="peer 호출용 서비스 토큰(선택)")
    mode: str = Field("LIVE", description="HARVEST / LIVE / HYBRID")
    sync_interval_sec: int = Field(900, ge=30)
    description: str | None = None
    display_fields: list[str] | None = Field(
        None, description="화면에 표시할 capability 키(소비자 선택). None=전부 표시",
    )

    _check_base_url = field_validator("base_url")(_validate_base_url)
    _check_mode = field_validator("mode")(_validate_mode)


class FederatedInstanceUpdate(BaseModel):
    """peer 부분 수정 요청 — 명시한 필드만 반영한다(exclude_unset).

    ``auth_token`` 을 null/빈 값으로 보내면 토큰을 제거하고, ``display_fields`` 를 null 로
    보내면 '전부 표시' 로 초기화한다.
    """

    name: str | None = Field(None, max_length=200)
    base_url: str | None = Field(None, max_length=500)
    auth_token: str | None = None
    mode: str | None = None
    sync_interval_sec: int | None = Field(None, ge=30)
    status: str | None = Field(None, description="ACTIVE / PAUSED")
    description: str | None = None
    display_fields: list[str] | None = None

    _check_base_url = field_validator("base_url")(_validate_base_url)
    _check_mode = field_validator("mode")(_validate_mode)
    _check_status = field_validator("status")(_validate_status)


class FederatedInstanceResponse(BaseModel):
    """peer 레지스트리 응답 — auth_token 은 노출하지 않고 보유 여부만 표시."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    instance_key: str
    name: str
    base_url: str
    has_auth_token: bool = False
    mode: str
    sync_interval_sec: int
    status: str
    description: str | None = None
    display_fields: list[str] | None = None
    created_at: datetime
    updated_at: datetime


class InstanceHealth(BaseModel):
    """peer 연결 상태 점검 결과."""
    instance_key: str
    reachable: bool
    status_code: int | None = None
    version: str | None = None
    latency_ms: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# 연합 검색
# ---------------------------------------------------------------------------

class FederatedDatasetHit(BaseModel):
    """단일 데이터셋 검색 결과 (peer export 응답의 item)."""
    urn: str
    name: str
    datasource_name: str | None = None
    datasource_type: str | None = None
    description: str | None = None
    origin: str | None = None
    score: float = Field(..., description="연관도 점수 (0.0 - 1.0)")
    match_type: str = Field(..., description="'semantic' | 'keyword' | 'hybrid'")


class FederatedExportSearchResponse(BaseModel):
    """이 인스턴스가 peer 로서 노출하는 검색 응답(export API)."""
    items: list[FederatedDatasetHit]
    total: int
    query: str
    provider: str | None = None
    model: str | None = None


class FederatedSearchHit(FederatedDatasetHit):
    """허브의 통합 검색 결과 item — 출처 인스턴스 정보를 부가한다."""
    # None 이면 이 허브(로컬) 결과. 값이 있으면 해당 peer 의 결과.
    source_instance_key: str | None = None
    source_instance_name: str | None = None
    source_base_url: str | None = None


class FederatedSearchResponse(BaseModel):
    """다수 인스턴스를 fan-out 한 통합 검색 응답."""
    query: str
    items: list[FederatedSearchHit]
    total: int
    instances_queried: int = Field(..., description="조회를 시도한 인스턴스 수(로컬 포함)")
    instances_failed: list[str] = Field(
        default_factory=list, description="도달 실패/타임아웃한 peer 의 instance_key 목록",
    )


# ---------------------------------------------------------------------------
# HARVEST — export 데이터셋 목록(peer 노출) + 동기화 결과
# ---------------------------------------------------------------------------

class FederatedExportField(BaseModel):
    """export 데이터셋의 스키마 컬럼(허브 재임베딩 텍스트 구성용)."""
    field_path: str
    field_type: str | None = None
    description: str | None = None


class FederatedExportDataset(BaseModel):
    """HARVEST 대상 — peer 가 노출하는 단일 데이터셋 메타데이터."""
    urn: str
    name: str
    display_name: str | None = None
    datasource_name: str | None = None
    datasource_type: str | None = None
    summary: str | None = None
    description: str | None = None
    qualified_name: str | None = None
    origin: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    fields: list[FederatedExportField] = Field(default_factory=list)


class FederatedExportDatasetsResponse(BaseModel):
    """peer 의 데이터셋 목록(페이지네이션) — 허브가 페이지를 돌며 가져온다."""
    items: list[FederatedExportDataset]
    total: int
    limit: int
    offset: int


class FederationSyncResult(BaseModel):
    """단일 peer HARVEST 실행 결과."""
    instance_key: str
    status: str = Field(..., description="SUCCESS / FAILED / SKIPPED(이미 진행 중)")
    datasets_seen: int = 0
    datasets_upserted: int = 0
    datasets_embedded: int = 0
    datasets_pruned: int = 0
    error: str | None = None


class FederationSyncRunResponse(BaseModel):
    """HARVEST 실행 이력 항목."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    instance_id: int
    status: str
    datasets_total: int = 0
    datasets_seen: int
    datasets_upserted: int
    datasets_embedded: int
    datasets_pruned: int
    # 단계별 진행률(가중 % 계산용)
    phase: str = "FETCH"
    phase_done: int = 0
    phase_total: int = 0
    # 전체 동기화(prune 포함) 여부 / 다음 증분 기준 시각(성공 실행만)
    is_full: bool = False
    watermark: datetime | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


# ---------------------------------------------------------------------------
# Capability — 노출자가 줄 수 있는 정보 항목 레지스트리 + 노출/선택 정책
# ---------------------------------------------------------------------------

class CapabilityItem(BaseModel):
    """정보 항목(capability) 한 개 — 키/라벨/그룹."""
    key: str
    label: str
    group: str
    group_label: str


class CapabilityGroup(BaseModel):
    """capability 그룹(표시 순서·라벨)."""
    group: str
    label: str


class ExportPolicyResponse(BaseModel):
    """노출자 정책 — 전체 레지스트리 + 현재 노출 중인 키."""
    items: list[CapabilityItem]
    groups: list[CapabilityGroup]
    exposed: list[str] = Field(..., description="외부에 노출하는 capability 키")


class ExportPolicyUpdate(BaseModel):
    """노출자 정책 변경 — 노출할 capability 키 목록."""
    exposed: list[str] = Field(default_factory=list)


class CapabilitiesResponse(BaseModel):
    """peer advertise — 이 인스턴스가 노출하는 항목(소비자가 그중 선택)."""
    items: list[CapabilityItem]
    groups: list[CapabilityGroup]
    exposed: list[str]


class ProbeCapabilitiesRequest(BaseModel):
    """등록 전 peer capabilities 사전 조회 요청."""
    base_url: str = Field(..., max_length=500)
    auth_token: str | None = None

    # 등록/수정과 동일한 base_url 검증(http(s) 절대 URL·사용자 정보 금지) — 잘못된 URL 은 422
    _check_base_url = field_validator("base_url")(_validate_base_url)


# ---------------------------------------------------------------------------
# 인스턴스별 미러 데이터셋 탐색(browse) — 분류체계 트리
#   HARVEST 미러(federation_datasets)를 인스턴스 → 데이터소스 → 데이터셋 으로
#   계층화해 반환한다. 검색어 없이 둘러보는 용도(통합 검색과 별개).
# ---------------------------------------------------------------------------

class FederatedBrowseDataset(BaseModel):
    """탐색 트리의 잎(leaf) — 미러된 단일 데이터셋."""
    federated_urn: str                         # 드릴다운 키 ({instance_key}::{remote_urn})
    remote_urn: str
    name: str
    display_name: str | None = None            # 논리명(예: 배우) — 없으면 물리명 표시
    summary: str | None = None
    description: str | None = None
    qualified_name: str | None = None
    origin: str | None = None
    field_count: int = 0
    remote_created_at: datetime | None = None
    remote_updated_at: datetime | None = None
    harvested_at: datetime | None = None


class FederatedBrowseDatasource(BaseModel):
    """탐색 트리의 중간 노드 — 데이터소스 단위 그룹."""
    datasource_name: str                       # null 은 '(미지정)' 으로 정규화
    datasource_type: str | None = None
    dataset_count: int
    datasets: list[FederatedBrowseDataset] = Field(default_factory=list)


class FederatedBrowseResponse(BaseModel):
    """단일 인스턴스의 미러 데이터셋을 데이터소스별로 묶은 탐색 응답."""
    instance_id: int
    instance_key: str
    instance_name: str
    total_datasets: int
    truncated: bool = Field(
        False, description="상한(cap)을 초과해 일부만 반환했는지 여부",
    )
    datasources: list[FederatedBrowseDatasource] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 로컬 승격(import) — 미러 데이터셋을 로컬 카탈로그 1급 데이터셋으로 복사
# ---------------------------------------------------------------------------

class FederatedImportRequest(BaseModel):
    """페더레이션 데이터셋 로컬 승격 요청."""
    federated_urn: str = Field(..., description="가져올 federated URN ({instance_key}::{remote_urn})")


class FederatedImportResponse(BaseModel):
    """승격 결과 — 생성된 로컬 데이터셋."""
    id: int
    urn: str
    name: str


# ---------------------------------------------------------------------------
# Cross-instance 리니지 stitching
# ---------------------------------------------------------------------------

class FederatedExportLineageEdge(BaseModel):
    """peer 가 노출하는 리니지 엣지(URN→URN)."""
    source_urn: str
    target_urn: str
    relation_type: str = "READ_WRITE"
    lineage_source: str = "QUERY_AGGREGATED"
    description: str | None = None


class FederatedExportLineageResponse(BaseModel):
    """peer 리니지 엣지 목록(export)."""
    edges: list[FederatedExportLineageEdge]
    total: int


class FederatedLineageNode(BaseModel):
    """stitched 리니지 그래프의 노드(데이터셋)."""
    urn: str                                  # 데이터소스 전역 유일 URN(그래프 키)
    name: str | None = None
    datasource_name: str | None = None
    datasource_type: str | None = None
    # None = 이 허브(로컬). 값이 있으면 해당 peer 의 데이터셋.
    source_instance_key: str | None = None
    source_instance_name: str | None = None
    # 미러/로컬 어디에서도 못 찾은 미해석 URN 이면 True (placeholder 노드)
    unresolved: bool = False


class FederatedLineageEdge(BaseModel):
    """stitched 리니지 그래프의 엣지."""
    source_urn: str
    target_urn: str
    relation_type: str = "READ_WRITE"
    # 이 엣지의 출처: None=로컬 리니지, 그 외=보고한 peer 의 instance_key
    reported_by: str | None = None


class FederatedLineageGraph(BaseModel):
    """특정 데이터셋을 중심으로 stitch 된 cross-instance 리니지 그래프."""
    root_urn: str
    depth: int
    nodes: list[FederatedLineageNode]
    edges: list[FederatedLineageEdge]


# ---------------------------------------------------------------------------
# 관측성 (observability)
# ---------------------------------------------------------------------------

class FederationStatsInstance(BaseModel):
    """peer 단위 관측 지표."""
    id: int
    instance_key: str
    name: str
    mode: str
    status: str
    mirror_datasets: int = 0
    mirror_lineage: int = 0
    last_sync_status: str | None = None        # SUCCESS/FAILED/RUNNING/None
    last_sync_started_at: datetime | None = None
    last_sync_finished_at: datetime | None = None
    last_sync_seen: int | None = None
    last_sync_embedded: int | None = None
    last_error: str | None = None
    breaker_open: bool = False
    breaker_failures: int = 0


class FederationStats(BaseModel):
    """페더레이션 전체 관측 요약."""
    total_instances: int
    active_instances: int
    total_mirror_datasets: int
    total_mirror_lineage: int
    instances: list[FederationStatsInstance]
