# SPDX-License-Identifier: Apache-2.0
"""데이터 카탈로그용 SQLAlchemy ORM 모델.

DataHub 를 본떠 모델링한 핵심 엔티티 유형:
- Dataset: 테이블, 뷰, 토픽, 파일
- Tag: 분류용 라벨
- GlossaryTerm: 비즈니스 용어집 용어
- Owner: 데이터셋 소유권 추적
"""

from sqlalchemy import (
    BigInteger,
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


class Organization(Base):
    """운영 조직/부서 — parent_id 로 트리를 이룬다 (NULL = 루트).

    조직 → 시스템 → 데이터 소스 → 데이터셋 계층의 최상위. 사이드바 데이터 소스 메뉴를
    조직 관점으로 구조화하기 위한 엔티티.
    """

    __tablename__ = "catalog_organizations"
    __table_args__ = (
        UniqueConstraint("parent_id", "name", name="uq_org_sibling_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(100), unique=True)            # URL/외부참조용 slug (자동 생성)
    name = Column(String(200), nullable=False)
    parent_id = Column(
        Integer, ForeignKey("catalog_organizations.id", ondelete="RESTRICT"), index=True
    )                                                   # NULL = 루트
    description = Column(Text)
    sort_order = Column(Integer, nullable=False, default=0)   # 형제 정렬
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class System(Base):
    """시스템 — 조직이 특정 이름으로 운영하는 데이터 데이터 소스 묶음(앱/서비스 배포).

    예: '고객데이터데이터 소스' 시스템 안에 trino-prod / mysql-oltp / impala-mart 데이터 소스.
    """

    __tablename__ = "catalog_systems"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_system_per_org"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(100), unique=True)
    name = Column(String(200), nullable=False)
    org_id = Column(
        Integer, ForeignKey("catalog_organizations.id", ondelete="RESTRICT"), index=True
    )                                                   # NULL = 미분류(조직 미배정)
    summary = Column(String(200))                        # 한 줄 요약
    description = Column(Text)                            # 상세 설명
    owner = Column(String(200))                          # 담당자
    status = Column(String(20), nullable=False, default="ACTIVE")  # ACTIVE / INACTIVE / DEPRECATED
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Datasource(Base):
    """데이터 데이터 소스 레지스트리 (예: Hive, MySQL, Kafka, S3)."""

    __tablename__ = "catalog_datasources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    datasource_id = Column(String(100), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    type = Column(String(100), nullable=False)
    logo_url = Column(String(500))
    # 환경 구분 (DEV/STAGING/PROD). 데이터 소스 생성 시 한 번 정해지면 이후 변경 불가 —
    # metadata-sync 어댑터가 이 값을 dataset.origin 으로 그대로 적용한다.
    origin = Column(String(20), nullable=False, default="DEV")
    # 소속 시스템 (NULL = 미분류). 시스템 삭제 시 SET NULL 로 미분류로 떨어진다.
    system_id = Column(
        Integer, ForeignKey("catalog_systems.id", ondelete="SET NULL"), index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DatasourceConfiguration(Base):
    """데이터 소스 인스턴스의 접속 및 설정 정보."""

    __tablename__ = "catalog_datasource_configurations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    datasource_id = Column(Integer, ForeignKey("catalog_datasources.id", ondelete="CASCADE"),
                         nullable=False, unique=True)
    config_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Dataset(Base):
    """데이터셋 엔티티 — 데이터 데이터 소스의 테이블, 뷰, 토픽 또는 파일."""

    __tablename__ = "catalog_datasets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    urn = Column(String(500), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    # 논리명 — 사람이 읽기 위한 표시용 이름 (예: physical "REGION" → display "지역").
    # NULL 이면 UI 는 name 으로 fallback. sync 어댑터는 절대 덮어쓰지 않는다.
    display_name = Column(String(255))
    datasource_id = Column(Integer, ForeignKey("catalog_datasources.id"), nullable=False)
    # 한 줄 요약 — 목록/검색/대시보드의 카드·테이블에서 노출되는 짧은 설명.
    # 본문(description) 보다 짧고 명확해야 하며 NULL 허용 (점진적으로 채움).
    summary = Column(String(200))
    description = Column(Text)
    origin = Column(String(50), nullable=False, default="PROD")
    qualified_name = Column(String(500))
    table_type = Column(String(100))
    storage_format = Column(String(100))
    datasource_properties = Column(Text)  # JSON: 데이터 소스별 고유 메타데이터
    # CREATE TABLE DDL — sync 어댑터가 채움 (예: trino 의 SHOW CREATE TABLE 결과).
    # 값이 있으면 UI 의 dataset 상세에 "DDL" tab 이 노출된다.
    ddl = Column(Text)
    created_by = Column(String(200))  # 행 단위 소유권 — 생성자
    is_synced = Column(String(5), default="false")
    status = Column(String(20), nullable=False, default="active")

    # --- 확장 메타데이터 (design/dataset-extended-metadata-design.md) ---
    # A. 생명주기·운영
    ingestion_frequency = Column(String(50))   # REALTIME/HOURLY/DAILY/WEEKLY/MONTHLY/MANUAL 또는 cron
    ingestion_time = Column(String(5))         # 수집 시각 "HH:mm"(일/주/월) 또는 분 "mm"(시간별)
    ingestion_day = Column(String(10))         # WEEKLY: 요일(MON..SUN) · MONTHLY: 일자(1..31)
    ingestion_timezone = Column(String(40))    # 기본 Asia/Seoul
    ingestion_cron = Column(String(120))       # 고급: cron 표현식 (있으면 우선)
    ingestion_mode = Column(String(20))        # BATCH/STREAMING/CDC/MANUAL
    update_type = Column(String(20))           # FULL/INCREMENTAL/APPEND/UPSERT
    freshness_sla = Column(String(200))        # 최신성 SLA 설명
    last_ingested_at = Column(DateTime(timezone=True))
    retention_days = Column(Integer)           # 보존 기한(일), NULL=영구
    purge_days = Column(Integer)               # 삭제 주기(일), NULL=없음
    # B. 물리·형식
    data_category = Column(String(20))         # STRUCTURED/SEMI_STRUCTURED/UNSTRUCTURED (정형/반정형/비정형)
    data_format = Column(String(30))           # CSV/JSON/XML/PARQUET/IMAGE/VIDEO/DOCUMENT 등
    compression = Column(String(20))           # NONE/GZIP/SNAPPY/ZSTD ...
    encoding = Column(String(30))              # UTF-8 ...
    row_count = Column(BigInteger)
    byte_size = Column(BigInteger)
    file_count = Column(Integer)
    # C. 거버넌스·보안
    sensitivity = Column(String(20))           # PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED
    contains_pii = Column(String(5))           # "true"/"false"
    pii_fields = Column(Text)                  # 개인정보 항목(쉼표)
    compliance_tags = Column(String(255))      # 규제 태그(쉼표)
    # D. 비즈니스
    tier = Column(String(20))                  # GOLD/SILVER/BRONZE
    certification = Column(String(20))         # CERTIFIED/IN_REVIEW/DEPRECATED/NONE
    steward = Column(String(255))              # 데이터 책임자
    # E. 사용·인기 (자동 집계 대상 — 표시 전용)
    view_count = Column(Integer, default=0)
    query_count = Column(Integer, default=0)
    last_accessed_at = Column(DateTime(timezone=True))
    # F. 품질 (점수는 품질 모듈에서 자동 연동, 표시 여부만 사용자 제어)
    quality_score = Column(Integer)            # 0~100 (자동 연동)
    quality_status = Column(String(20))        # GOOD/WARN/BAD/UNKNOWN
    show_quality_score = Column(String(5), default="false")  # 품질 점수 표시 여부
    # 자유 메모(비고)
    note = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DatasetProperty(Base):
    """데이터셋의 데이터 소스별 고유 키-값 속성."""

    __tablename__ = "catalog_dataset_properties"
    __table_args__ = (UniqueConstraint("dataset_id", "property_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"),
                        nullable=False)
    property_key = Column(String(100), nullable=False)
    property_value = Column(Text, nullable=False)


class DatasetSchema(Base):
    """데이터셋의 스키마 필드."""

    __tablename__ = "catalog_dataset_schemas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"),
                        nullable=False)
    field_path = Column(String(500), nullable=False)
    # 컬럼 논리명 — 한글 등 표시용. NULL 이면 UI 는 field_path 로 fallback.
    # 향후 표준 사전(catalog_standard_*) 매칭으로 자동 채워질 수 있다.
    display_name = Column(String(255))
    field_type = Column(String(100), nullable=False)
    native_type = Column(String(100))
    description = Column(Text)
    nullable = Column(String(5), default="true")
    is_primary_key = Column(String(5), default="false")
    is_unique = Column(String(5), default="false")
    is_indexed = Column(String(5), default="false")
    is_partition_key = Column(String(5), default="false")
    is_distribution_key = Column(String(5), default="false")
    ordinal = Column(Integer, nullable=False, default=0)
    pii_type = Column(String(50))  # 개인정보 분류: EMAIL, PHONE, SSN, NAME, ADDRESS 등


class DatasetUrnAlias(Base):
    """구 URN → dataset 매핑 (URN 포맷 전환 alias).

    URN 에서 환경(DEV/STAGING/PROD)을 제거하면서, 과거 URN(``...DEV.dataset``)으로
    들어오는 외부 참조/리니지/링크를 계속 해소하기 위한 별칭 테이블. 전환기 이후에도
    저비용으로 영구 유지한다.
    """

    __tablename__ = "catalog_dataset_urn_alias"

    old_urn = Column(String(500), primary_key=True)
    dataset_id = Column(
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SchemaSnapshot(Base):
    """스키마 변경 이력 스냅샷.

    동기화가 변경을 감지할 때마다 전체 스키마 스냅샷을 기록한다.
    실제 변경(ADD/MODIFY/DROP)이 감지된 경우에만 저장한다.
    """

    __tablename__ = "catalog_schema_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"),
                        nullable=False)
    synced_at = Column(DateTime(timezone=True), server_default=func.now())
    schema_json = Column(Text, nullable=False)       # 전체 스키마를 JSON 배열로 저장
    field_count = Column(Integer, default=0)
    change_summary = Column(String(500))              # 예: "Added 2, Modified 1, Dropped 1"
    changes_json = Column(Text)                       # 개별 변경 내역을 JSON 배열로 저장


class Tag(Base):
    """데이터셋 분류용 태그."""

    __tablename__ = "catalog_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    color = Column(String(7), default="#3b82f6")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DatasetTag(Base):
    """데이터셋과 태그 간 다대다(N:M) 관계."""

    __tablename__ = "catalog_dataset_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"),
                        nullable=False)
    tag_id = Column(Integer, ForeignKey("catalog_tags.id", ondelete="CASCADE"), nullable=False)


class GlossaryTerm(Base):
    """비즈니스 용어집 용어."""

    __tablename__ = "catalog_glossary_terms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    description = Column(Text)
    parent_id = Column(Integer, ForeignKey("catalog_glossary_terms.id"))
    term_type = Column(String(20), nullable=False, default="TERM")  # CATEGORY 또는 TERM
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DatasetGlossaryTerm(Base):
    """데이터셋과 용어집 용어 간 다대다(N:M) 관계."""

    __tablename__ = "catalog_dataset_glossary_terms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"),
                        nullable=False)
    term_id = Column(Integer, ForeignKey("catalog_glossary_terms.id", ondelete="CASCADE"),
                     nullable=False)


class Owner(Base):
    """데이터셋 소유권."""

    __tablename__ = "catalog_owners"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"),
                        nullable=False)
    owner_name = Column(String(200), nullable=False)
    owner_type = Column(String(50), nullable=False, default="TECHNICAL_OWNER")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# 데이터 소스 메타데이터 모델
# ---------------------------------------------------------------------------

class DataPipeline(Base):
    """데이터 파이프라인 레지스트리.

    이기종 시스템 간 데이터 흐름(ETL, CDC, 파일 내보내기 등)을 등록하고 관리한다.
    DatasetLineage에서 pipeline_id로 참조하여 어떤 파이프라인이
    cross-datasource 리니지를 만드는지 추적한다.

    예시: PostgreSQL → Parquet 변환 → Impala 적재 파이프라인
    """

    __tablename__ = "argus_data_pipeline"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_name = Column(String(255), nullable=False, unique=True)   # 파이프라인 고유 이름
    description = Column(Text)                                         # 파이프라인 설명
    pipeline_type = Column(String(64), nullable=False, default="ETL")  # 유형: ETL, FILE_EXPORT, CDC, REPLICATION, API, MANUAL
    schedule = Column(String(100))                                     # 실행 주기 (cron 표현식, 예: "0 2 * * *")
    owner = Column(String(200))                                        # 파이프라인 담당자
    status = Column(String(20), nullable=False, default="ACTIVE")      # 상태: ACTIVE, INACTIVE, DEPRECATED
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DatasetLineage(Base):
    """데이터셋 간 리니지 관계 (Cross-Datasource 지원).

    이기종 시스템 간의 데이터 흐름 관계를 저장한다.
    동일 데이터 소스 내 쿼리 기반 자동 수집과 수동/파이프라인 등록을 모두 지원.

    리니지 출처(lineage_source)에 따른 구분:
    - QUERY_AGGREGATED: 쿼리 로그 분석으로 자동 수집 (같은 데이터 소스 내)
    - PIPELINE: 파이프라인 등록을 통해 명시적 연결 (이기종 간)
    - MANUAL: 사용자가 UI에서 직접 등록 (이기종 간)

    예시:
      PostgreSQL.hr_db.employees → Impala.analytics.emp_fact
      (lineage_source=PIPELINE, relation_type=ETL)
    """

    __tablename__ = "argus_dataset_lineage"
    __table_args__ = (
        # lineage_source 까지 unique key 에 포함 — 같은 (source, target, relation_type) 이라도
        # 출처가 다르면 (FK / MANUAL / PIPELINE / QUERY_AGGREGATED) 별개 row 로 양립한다.
        UniqueConstraint(
            "source_dataset_id", "target_dataset_id",
            "relation_type", "lineage_source",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_dataset_id = Column(                                           # 원본 데이터셋 (데이터를 제공하는 쪽)
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False
    )
    target_dataset_id = Column(                                           # 대상 데이터셋 (데이터를 받는 쪽)
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False
    )
    relation_type = Column(String(32), nullable=False, default="READ_WRITE")  # 관계 유형: ETL, FILE_EXPORT, CDC, REPLICATION, DERIVED, READ_WRITE
    lineage_source = Column(String(32), nullable=False, default="QUERY_AGGREGATED")  # 리니지 출처: QUERY_AGGREGATED, PIPELINE, MANUAL
    pipeline_id = Column(                                                 # 파이프라인 참조 (PIPELINE 출처일 때)
        Integer, ForeignKey("argus_data_pipeline.id", ondelete="SET NULL")
    )
    description = Column(Text)                                            # 리니지 관계 설명
    created_by = Column(String(200))                                      # 등록한 사용자
    query_count = Column(Integer, nullable=False, default=0)              # 이 관계를 확인한 쿼리 수 (자동 수집 시)
    last_query_id = Column(String(256))                                   # 마지막으로 확인된 쿼리 ID
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())  # 마지막 확인 시각
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ColumnRelationship(Base):
    """사용 기반 컬럼 관계 — 쿼리 워크로드의 JOIN 키에서 발견한 무방향 컬럼쌍.

    데이터 흐름(lineage)이 아니라 "어떤 컬럼이 어떤 컬럼과 자주 함께 조인되는가"를
    빈도(join_count)로 집계한다 — 암묵 FK/조인키 발견용. 명시적 ``JOIN ... ON`` 의
    조인키만 신호로 사용(v1).

    무방향 정규화: 항상 ``(dataset_a_id, field_a) <= (dataset_b_id, field_b)`` 가 되도록
    정렬해 저장하므로 (a,b)/(b,a) 중복이 생기지 않는다. dataset id 가 전역이라
    cross-datasource 조인도 같은 테이블로 표현 가능.
    """

    __tablename__ = "catalog_column_relationship"
    __table_args__ = (
        UniqueConstraint(
            "dataset_a_id", "field_a", "dataset_b_id", "field_b", "relation_type",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_a_id = Column(                                                # 무방향 한쪽 (정규화 시 작은 쪽)
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_a = Column(String(500), nullable=False)                        # dataset_a 의 컬럼(field_path)
    dataset_b_id = Column(                                               # 무방향 다른 쪽
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_b = Column(String(500), nullable=False)                        # dataset_b 의 컬럼(field_path)
    relation_type = Column(String(32), nullable=False, default="JOIN_KEY")  # JOIN_KEY (확장: FILTER_EQ, CO_GROUP)
    join_count = Column(Integer, nullable=False, default=0)              # confidence — 이 관계가 등장한 쿼리 수(명시+암묵)
    explicit_count = Column(Integer, nullable=False, default=0)          # 명시적 JOIN ... ON 으로 관측된 횟수
    implicit_count = Column(Integer, nullable=False, default=0)          # 암묵 조인(WHERE 등치)으로 관측된 횟수
    distinct_users = Column(Integer, nullable=False, default=0)          # (선택) 관측된 사용자 다양성
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())  # 최초 관측
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())   # 최근 관측
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DatasetColumnMapping(Base):
    """Cross-Datasource 컬럼 수준 리니지 매핑.

    이기종 시스템 간 데이터셋 리니지에서 개별 컬럼 간의 매핑 관계를 저장한다.
    스키마 변경 시 영향 분석(Impact Analysis)의 핵심 데이터.

    변환 유형(transform_type):
    - DIRECT: 동일한 값을 그대로 전달
    - CAST: 타입 변환 (예: INT → BIGINT)
    - EXPRESSION: 수식/변환 적용 (transform_expr에 수식 기록)
    - DERIVED: 여러 컬럼에서 파생된 값 (집계, 계산 등)

    예시:
      source=emp_id → target=employee_key (transform_type=CAST, expr="CAST(emp_id AS BIGINT)")
    """

    __tablename__ = "argus_dataset_column_mapping"
    __table_args__ = (
        UniqueConstraint("dataset_lineage_id", "source_column", "target_column"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_lineage_id = Column(                                         # 소속 리니지 관계
        Integer, ForeignKey("argus_dataset_lineage.id", ondelete="CASCADE"), nullable=False
    )
    source_column = Column(String(256), nullable=False)                  # 원본 컬럼명
    target_column = Column(String(256), nullable=False)                  # 대상 컬럼명
    transform_type = Column(String(64), nullable=False, default="DIRECT")  # 변환 유형: DIRECT, CAST, EXPRESSION, DERIVED
    transform_expr = Column(String(500))                                 # 변환 수식 (CAST/EXPRESSION일 때, 예: "CAST(emp_id AS BIGINT)")


class DatasourceDataType(Base):
    """데이터 소스별 지원 데이터 타입."""

    __tablename__ = "catalog_datasource_data_types"
    __table_args__ = (UniqueConstraint("datasource_id", "type_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    datasource_id = Column(Integer, ForeignKey("catalog_datasources.id", ondelete="CASCADE"),
                         nullable=False)
    type_name = Column(String(100), nullable=False)
    type_category = Column(String(50), nullable=False)
    description = Column(String(500))
    ordinal = Column(Integer, nullable=False, default=0)


class DatasourceTableType(Base):
    """데이터 소스별 지원 테이블/엔티티 타입."""

    __tablename__ = "catalog_datasource_table_types"
    __table_args__ = (UniqueConstraint("datasource_id", "type_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    datasource_id = Column(Integer, ForeignKey("catalog_datasources.id", ondelete="CASCADE"),
                         nullable=False)
    type_name = Column(String(100), nullable=False)
    display_name = Column(String(200), nullable=False)
    description = Column(String(500))
    is_default = Column(String(5), default="false")
    ordinal = Column(Integer, nullable=False, default=0)


class DatasourceStorageFormat(Base):
    """데이터 소스별 지원 저장/직렬화 포맷."""

    __tablename__ = "catalog_datasource_storage_formats"
    __table_args__ = (UniqueConstraint("datasource_id", "format_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    datasource_id = Column(Integer, ForeignKey("catalog_datasources.id", ondelete="CASCADE"),
                         nullable=False)
    format_name = Column(String(100), nullable=False)
    display_name = Column(String(200), nullable=False)
    description = Column(String(500))
    is_default = Column(String(5), default="false")
    ordinal = Column(Integer, nullable=False, default=0)


class DatasourceFeature(Base):
    """데이터 소스별 고유 기능(파티션 키, 분산 등)."""

    __tablename__ = "catalog_datasource_features"
    __table_args__ = (UniqueConstraint("datasource_id", "feature_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    datasource_id = Column(Integer, ForeignKey("catalog_datasources.id", ondelete="CASCADE"),
                         nullable=False)
    feature_key = Column(String(100), nullable=False)
    display_name = Column(String(200), nullable=False)
    description = Column(String(500))
    value_type = Column(String(50), nullable=False)
    is_required = Column(String(5), default="false")
    ordinal = Column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------
# Dataset Taxonomy (분류체계) — 다중 분류체계, 데이터셋 N:M 매핑
# ---------------------------------------------------------------------------

class Taxonomy(Base):
    """분류체계(scheme) — 이름 붙은 분류 트리의 루트 개념.

    예: '업무 도메인', '데이터 등급'. 각 taxonomy 는 독립된 Category 트리를 가진다.
    """

    __tablename__ = "catalog_taxonomies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(100), unique=True)            # slug(자동 생성)
    name = Column(String(200), nullable=False, unique=True)
    description = Column(Text)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Category(Base):
    """분류 노드 — taxonomy 내부의 자기참조 트리(parent_id, NULL=루트)."""

    __tablename__ = "catalog_categories"
    __table_args__ = (
        UniqueConstraint("taxonomy_id", "parent_id", "name", name="uq_category_sibling_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    taxonomy_id = Column(
        Integer, ForeignKey("catalog_taxonomies.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    parent_id = Column(
        Integer, ForeignKey("catalog_categories.id", ondelete="RESTRICT"), index=True
    )                                                   # NULL = 루트
    code = Column(String(100))
    name = Column(String(200), nullable=False)
    description = Column(Text)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DatasetCategory(Base):
    """데이터셋 ↔ 분류 노드 N:M 매핑."""

    __tablename__ = "catalog_dataset_categories"
    __table_args__ = (
        UniqueConstraint("dataset_id", "category_id", name="uq_dataset_category"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(
        Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id = Column(
        Integer, ForeignKey("catalog_categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
