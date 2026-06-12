# SPDX-License-Identifier: Apache-2.0
"""페더레이션용 SQLAlchemy ORM 모델.

Phase 0 PoC 는 ``FederatedInstance``(peer 레지스트리)만 사용한다. HARVEST 모드용
미러 테이블/동기화 이력(``FederationSyncRun`` 등)은 후속 단계에서 추가한다.
"""

import logging

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.core.database import Base

logger = logging.getLogger(__name__)

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    # pgvector 미설치 — 모듈 임포트는 가능하게 두고, 미러 임베딩 테이블/검색은 비활성.
    logger.warning("pgvector 미설치 — 페더레이션 미러 임베딩이 비활성화됩니다.")
    Vector = None


class FederatedInstance(Base):
    """연합 대상 peer Argus Catalog 인스턴스.

    Trino 의 catalog(connector) 등록에 해당한다. ``instance_key`` 는 이 허브 안에서
    peer 를 구분하는 네임스페이스 prefix(예: ``team-payments``)로, 연합 검색 결과의
    출처 표시와 (후속 단계의) cross-instance URN 네임스페이싱에 사용한다.
    """

    __tablename__ = "federation_instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 허브 내 peer 식별 네임스페이스 (URN prefix 로 사용: ``{instance_key}::{urn}``)
    instance_key = Column(String(64), nullable=False, unique=True)
    name = Column(String(200), nullable=False)            # 표시용 이름
    base_url = Column(String(500), nullable=False)        # 예: https://catalog.team-a.internal
    # peer 호출 시 Bearer 로 전송할 서비스 토큰 (선택). Phase 0 은 평문 보관 —
    # Phase 1 에서 ARGUS_SECRET_KEY 기반 암호화로 전환한다. 응답에는 노출하지 않는다.
    auth_token = Column(Text)
    # 연합 모드: HARVEST / LIVE / HYBRID. Phase 0 PoC 는 LIVE 만 실제 동작.
    mode = Column(String(20), nullable=False, default="LIVE")
    # HARVEST 주기(초) — 후속 단계 사용
    sync_interval_sec = Column(Integer, nullable=False, default=900)
    # 상태: ACTIVE(연합 검색 포함) / PAUSED(제외)
    status = Column(String(20), nullable=False, default="ACTIVE")
    # 소비자(허브) 표시 선택 — 이 peer 에서 화면에 표시할 capability 키 JSON 배열.
    # NULL=전부 표시(노출자 노출 범위 그대로). 노출자 노출 ∩ 이 선택만 상세에 노출.
    display_fields = Column(Text)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# HARVEST 미러 — peer 메타데이터를 로컬에 복제(read-only)하고 허브 모델로 재임베딩.
# ---------------------------------------------------------------------------

class FederatedDataset(Base):
    """HARVEST 로 가져온 peer 데이터셋의 로컬 미러(read-only).

    peer 마다 임베딩 모델이 달라 cross-instance 유사도 비교가 깨지는 문제를 피하기
    위해, 이 미러를 **허브의 임베딩 모델로 재임베딩**(``FederatedDatasetEmbedding``)해
    로컬 pgvector 검색에 편입한다. ``source_text`` 로 변경을 감지해 불변 시 재임베딩을
    건너뛴다.
    """

    __tablename__ = "federation_datasets"
    __table_args__ = (
        UniqueConstraint("instance_id", "remote_urn", name="uq_fed_dataset_instance_urn"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    instance_id = Column(
        Integer, ForeignKey("federation_instances.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    remote_urn = Column(String(500), nullable=False)        # peer 내부 URN
    # 네임스페이스 URN: ``{instance_key}::{remote_urn}`` — 허브 안에서 전역 유일
    federated_urn = Column(String(600), nullable=False, unique=True)
    name = Column(String(255), nullable=False)              # 물리명(예: sakila.actor)
    display_name = Column(String(255))                      # 논리명(예: 배우) — 없으면 NULL
    datasource_name = Column(String(200))
    datasource_type = Column(String(100))
    summary = Column(String(200))
    description = Column(Text)
    qualified_name = Column(String(500))
    origin = Column(String(50))
    # peer 측 스키마 컬럼 개수(가져오기 시점 len(fields)) — 0=미수집/빈 스키마
    field_count = Column(Integer, nullable=False, default=0)
    # 미러 샘플 데이터 보유 여부(HARVEST 시 federation/samples 에 저장). 서빙·UI 판단용.
    has_sample = Column(Boolean, nullable=False, default=False)
    # 재임베딩 변경 감지용 원본 텍스트
    source_text = Column(Text)
    # peer 측 등록 시각(원본 데이터셋 created_at)
    remote_created_at = Column(DateTime(timezone=True))
    # peer 측 최종 수정 시각 — 증분 동기화 watermark(후속 단계)
    remote_updated_at = Column(DateTime(timezone=True))
    harvested_at = Column(DateTime(timezone=True), server_default=func.now())


class FederatedDatasetEmbedding(Base):
    """HARVEST 미러 데이터셋의 허브 모델 재임베딩(pgvector)."""

    __tablename__ = "federation_dataset_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    federation_dataset_id = Column(
        Integer, ForeignKey("federation_datasets.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    embedding = Column(Vector(384) if Vector else Text, nullable=False)
    source_text = Column(Text, nullable=False)
    model_name = Column(String(200), nullable=False)
    provider = Column(String(50), nullable=False)
    dimension = Column(Integer, nullable=False, default=384)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FederationLineage(Base):
    """HARVEST 로 가져온 peer 의 리니지 엣지(URN→URN).

    엔드포인트는 데이터소스 전역 유일 URN(``{datasource_id}.{path}.dataset``)이라,
    어느 peer 가 보고했든 같은 데이터셋을 가리키면 같은 문자열이다. 이 성질로
    로컬/미러 데이터셋과 URN 매칭해 cross-instance 데이터 흐름을 stitch 한다.
    """

    __tablename__ = "federation_lineage"
    __table_args__ = (
        UniqueConstraint(
            "instance_id", "source_urn", "target_urn", "relation_type",
            name="uq_fed_lineage_edge",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    instance_id = Column(                                       # 이 엣지를 보고한 peer
        Integer, ForeignKey("federation_instances.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_urn = Column(String(500), nullable=False)            # 원본 데이터셋 URN(peer 내부)
    target_urn = Column(String(500), nullable=False)            # 대상 데이터셋 URN(peer 내부)
    relation_type = Column(String(32), nullable=False, default="READ_WRITE")
    lineage_source = Column(String(32), nullable=False, default="QUERY_AGGREGATED")
    description = Column(Text)
    harvested_at = Column(DateTime(timezone=True), server_default=func.now())


class FederationSyncRun(Base):
    """HARVEST 실행 이력(관측성)."""

    __tablename__ = "federation_sync_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instance_id = Column(
        Integer, ForeignKey("federation_instances.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status = Column(String(20), nullable=False, default="RUNNING")  # RUNNING/SUCCESS/FAILED
    # peer 가 보고한 전체 데이터셋 수(진행률 표시용). 0=미정.
    datasets_total = Column(Integer, nullable=False, default=0)
    datasets_seen = Column(Integer, nullable=False, default=0)
    datasets_upserted = Column(Integer, nullable=False, default=0)
    datasets_embedded = Column(Integer, nullable=False, default=0)
    datasets_pruned = Column(Integer, nullable=False, default=0)
    # 단계별 진행률(가중 % 계산용) — 현재 단계와 그 단계의 진척 분자/분모.
    phase = Column(String(20), nullable=False, default="FETCH")  # FETCH/EMBED/FINALIZE
    phase_done = Column(Integer, nullable=False, default=0)
    phase_total = Column(Integer, nullable=False, default=0)
    error = Column(Text)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True))
