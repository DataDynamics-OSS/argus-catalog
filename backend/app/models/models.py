# SPDX-License-Identifier: Apache-2.0
"""MLflow 모델 레지스트리의 SQLAlchemy ORM 모델 정의.

Unity Catalog OSS 의 ``RegisteredModel`` / ``ModelVersion`` 패턴을 따른다.
MLflow 가 UC 호환 API (``/api/2.1/unity-catalog/``) 로 모델을 등록하면
내부적으로 이 테이블들에 매핑된다.

주요 테이블:
  - ``catalog_registered_models`` : 모델 메타데이터 + ``max_version_number`` 카운터
  - ``catalog_model_versions``    : 버전별 아티팩트와 상태 라이프사이클
  - ``catalog_models``            : MLflow 산출 메타데이터(predict_fn, sklearn_version 등)
  - ``model_download_logs``       : 다운로드 이벤트 로그
  - ``model_metrics`` / ``model_cards`` / ``model_dataset_lineages``

상태 라이프사이클:
  ``PENDING_REGISTRATION`` → ``READY`` (성공) 또는 ``FAILED_REGISTRATION`` (실패)

이름 규칙:
  MLflow 가 요구하는 3-part 이름: ``{catalog}.{schema}.{model_name}``
  예) ``argus.ml.iris_classifier``
  이 전체 이름이 ``name`` 컬럼에 그대로 저장된다.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.core.database import Base


class RegisteredModel(Base):
    """등록된 ML 모델.

    각 모델은 고유한 3-part 이름(catalog.schema.model_name)을 가지며, 이는
    MLflow 통합의 기본 조회 키 역할을 한다. ``max_version_number`` 필드는
    지금까지 생성된 최대 버전 번호를 추적하며 새 버전 등록 시 원자적으로
    증가한다.

    소프트 삭제: status='deleted' 로 설정하면 참조 무결성을 유지한 채
    목록에서만 모델을 숨긴다.
    """

    __tablename__ = "catalog_registered_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 3-part 이름: catalog.schema.model (예: "argus.ml.iris_classifier")
    name = Column(String(255), nullable=False, unique=True)
    # URN: {name}.{ENV}.model (예: "argus.ml.iris_classifier.PROD.model")
    urn = Column(String(500), nullable=False, unique=True)
    # 데이터 데이터소스와의 선택적 연결 (nullable — ML 모델은 데이터소스에 속하지 않을 수 있음)
    datasource_id = Column(Integer, ForeignKey("catalog_datasources.id", ondelete="SET NULL"),
                         nullable=True)
    description = Column(Text)
    owner = Column(String(200))
    # "local" (file://) 또는 "s3" (s3://)
    storage_type = Column(String(20), nullable=False, default="local")
    # 아티팩트 저장소 기본 경로 (file:///var/lib/... 또는 s3://bucket/prefix)
    storage_location = Column(String(1000))
    # S3 버킷 이름 (storage_type="s3" 일 때)
    bucket_name = Column(String(255))
    # 생성된 최대 버전 번호 추적 (버전마다 자동 증가)
    max_version_number = Column(Integer, nullable=False, default=0)
    # "active" 또는 "deleted" (소프트 삭제)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(200))
    updated_by = Column(String(200))


class ModelVersion(Base):
    """상태 라이프사이클과 아티팩트 추적을 갖는 모델 버전.

    각 버전은 (model_id, version) 으로 식별되며 다음 흐름을 거친다:
      PENDING_REGISTRATION → READY 또는 FAILED_REGISTRATION

    운영 가시성을 위해 추가된 감사 필드:
      - status_message: 사람이 읽을 수 있는 실패 사유
      - artifact_count: 확정 시점의 파일 수 (0 = 의심스러움)
      - artifact_size: 확정 시점의 총 바이트 (0 = 의심스러움)
      - finished_at: 확정이 호출된 시각 (NULL = 확정된 적 없음)

    이 필드들 덕분에 운영자는 DB 만 보고도 버전이 정상적으로 완료됐는지
    판별할 수 있다:
      READY + artifact_count>0 + finished_at≠NULL → 성공
      FAILED + status_message="..." → 알려진 실패
      PENDING + finished_at=NULL + 오래된 created_at → 멈춤/방치
    """

    __tablename__ = "catalog_model_versions"
    __table_args__ = (UniqueConstraint("model_id", "version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("catalog_registered_models.id", ondelete="CASCADE"),
                      nullable=False)
    # 모델별 자동 증가 (1, 2, 3, ...)
    version = Column(Integer, nullable=False)
    # MLflow 아티팩트 소스 URI (예: "models:/m-abc123")
    source = Column(String(1000))
    # 이 모델 버전을 생성한 MLflow run ID
    run_id = Column(String(255))
    # MLflow run UI 로의 선택적 링크
    run_link = Column(String(1000))
    description = Column(Text)
    # 상태 라이프사이클: PENDING_REGISTRATION → READY | FAILED_REGISTRATION
    status = Column(String(30), nullable=False, default="PENDING_REGISTRATION")
    # 사람이 읽을 수 있는 실패 사유 (예: "Model deleted during registration")
    status_message = Column(Text)
    # 버전별 아티팩트 경로 (file:///.../{name}/versions/{ver}/)
    storage_location = Column(String(1000))
    # 확정 시점에 집계된 아티팩트 파일 수
    artifact_count = Column(Integer, default=0)
    # 확정 시점에 집계된 아티팩트 총 크기(바이트)
    artifact_size = Column(Integer, default=0)
    # 확정이 호출된 타임스탬프 (NULL = 확정된 적 없음)
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(200))
    updated_by = Column(String(200))
    # 스테이지 라이프사이클: NONE → STAGING → PRODUCTION → ARCHIVED
    stage = Column(String(20), default="NONE")
    stage_changed_at = Column(DateTime(timezone=True))
    stage_changed_by = Column(String(200))


class ModelDatasetLineage(Base):
    """모델과 학습/평가에 사용된 데이터셋 사이의 연결.

    각 모델 버전을 학습하는 데 어떤 데이터셋이 사용됐는지 추적할 수 있게 하여,
    데이터 변경 시 모델 재학습 알림을 트리거할 수 있다.
    """

    __tablename__ = "catalog_model_dataset_lineage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("catalog_registered_models.id", ondelete="CASCADE"), nullable=False)
    model_version = Column(Integer)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    relation_type = Column(String(30), nullable=False, default="TRAINING_DATA")  # TRAINING_DATA, EVALUATION_DATA, FEATURE_SOURCE
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ModelMetric(Base):
    """모델의 버전별 성능 메트릭.

    Metrics 탭에서 버전 간 비교가 가능하도록 모델 버전별로 키-값 메트릭
    (accuracy, f1, latency 등) 을 저장한다.
    """

    __tablename__ = "catalog_model_metrics"
    __table_args__ = (UniqueConstraint("model_id", "version", "metric_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("catalog_registered_models.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    metric_key = Column(String(100), nullable=False)
    metric_value = Column(Numeric(15, 6), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())


class ModelCard(Base):
    """거버넌스 필드를 갖춘 구조화된 모델 카드.

    Model Card 표준(Google/Microsoft)을 따르며 목적, 성능, 한계,
    학습 데이터, 프레임워크, 라이선스 필드를 갖는다.
    """

    __tablename__ = "catalog_model_card"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("catalog_registered_models.id", ondelete="CASCADE"), nullable=False, unique=True)
    purpose = Column(Text)
    performance = Column(Text)
    limitations = Column(Text)
    training_data = Column(Text)
    framework = Column(String(200))
    license = Column(String(200))
    contact = Column(String(200))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CatalogModel(Base):
    """확정 시점에 MLflow 아티팩트 파일에서 추출한 파싱된 모델 메타데이터.

    모델 버전이 READY 로 전이되면 서버가 아티팩트 디렉토리에서 MLmodel(YAML),
    requirements.txt, conda.yaml, python_env.yaml 을 읽어 그 내용과 주요
    메타데이터 필드를 이 테이블에 저장한다.

    MLmodel YAML 에서 오는 컬럼:
      predict_fn, python_version, serialization_format, sklearn_version,
      mlflow_version, mlflow_model_id, model_size_bytes, utc_time_created, time_created
    텍스트 파일에서 오는 컬럼:
      requirements, conda, python_env
    """

    __tablename__ = "catalog_models"
    __table_args__ = (UniqueConstraint("model_name", "version"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_version_id = Column(
        Integer,
        ForeignKey("catalog_model_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_name = Column(String(255), nullable=False)
    version = Column(Integer, nullable=False)

    # --- MLmodel YAML 에서 파싱된 필드 ---
    predict_fn = Column(String(100))
    python_version = Column(String(20))
    serialization_format = Column(String(50))
    sklearn_version = Column(String(20))
    mlflow_version = Column(String(20))
    mlflow_model_id = Column(String(100))
    model_size_bytes = Column(BigInteger)
    utc_time_created = Column(String(50))
    # utc_time_created 를 서버 로컬 timezone 으로 변환한 값
    time_created = Column(DateTime(timezone=True))

    # --- 원본 파일 내용 ---
    requirements = Column(Text)
    conda = Column(Text)
    python_env = Column(Text)

    # --- OCI 매니페스트 ---
    manifest = Column(Text)
    config = Column(Text)
    content_digest = Column(String(100))

    # --- 소스 정보 ---
    source_type = Column(String(50))  # mlflow, huggingface, local, oras

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ModelDownloadLog(Base):
    """모델 사용량 추적용 다운로드 로그.

    모델 버전이 load / pull / download 될 때마다 기록한다.
    일/주/월 단위 사용량 통계에 사용된다.
    """

    __tablename__ = "catalog_model_download_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(255), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    # 'load' (MLflow), 'pull' (SDK), 'download' (단일 파일)
    download_type = Column(String(20), nullable=False)
    client_ip = Column(String(45))
    user_agent = Column(String(500))
    downloaded_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
