-- Argus Catalog Server - MariaDB DDL
-- Auto-generated from database schema

-- ---------------------------------------------------------------------------
-- Datasource Registry
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasources (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    name VARCHAR(100) NOT NULL COMMENT '데이터 소스 표시 이름',
    logo_url VARCHAR(500) COMMENT '데이터 소스 로고 이미지 URL',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    datasource_id VARCHAR(36) NOT NULL UNIQUE COMMENT '데이터 소스 외부 식별자 (UUID, 고유)',
    type VARCHAR(100) NOT NULL COMMENT '데이터 소스 플랫폼 유형 (예: hive/trino/postgres)',
    origin VARCHAR(20) NOT NULL DEFAULT 'DEV' COMMENT '환경 구분 (DEV/STAGING/PROD), 생성 후 불변'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Datasource Configuration
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasource_configurations (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    datasource_id INT NOT NULL UNIQUE COMMENT '데이터소스 (catalog_datasources(id) 참조, UNIQUE·ON DELETE CASCADE)' REFERENCES catalog_datasources(id) ON DELETE CASCADE,
    config_json TEXT NOT NULL COMMENT '접속·인증·옵션 설정 JSON (JDBC URL, 자격 증명 등)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Dataset
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasets (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    urn VARCHAR(500) NOT NULL UNIQUE COMMENT '데이터셋 전역 고유 식별자 URN (unique)',
    name VARCHAR(255) NOT NULL COMMENT '데이터셋 물리명',
    datasource_id INT NOT NULL COMMENT '소속 데이터 소스 (catalog_datasources(id) 참조)' REFERENCES catalog_datasources(id),
    summary VARCHAR(200) COMMENT '한 줄 요약 (목록·검색·대시보드 카드 노출용, 최대 200자)',
    description TEXT COMMENT '데이터셋 상세 설명 (리치 텍스트 / 마크다운 본문)',
    origin VARCHAR(50) NOT NULL COMMENT '환경 구분 (DEV/STAGING/PROD)',
    qualified_name VARCHAR(500) COMMENT '정규화된 전체 경로명 (예: catalog.schema.table)',
    status VARCHAR(20) NOT NULL COMMENT '데이터셋 상태 (active/inactive 등)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    table_type VARCHAR(100) COMMENT '테이블 유형 (TABLE/VIEW/TOPIC/FILE 등)',
    storage_format VARCHAR(100) COMMENT '저장 포맷 (PARQUET/ORC/CSV 등)',
    datasource_properties TEXT COMMENT '데이터 소스별 부가 메타데이터 (JSON)',
    ddl TEXT COMMENT 'CREATE TABLE DDL 원문 (sync 어댑터가 채움)',
    is_synced VARCHAR(5) DEFAULT 'false' COMMENT '메타데이터 동기화 여부 ("true"/"false")'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Dataset Properties
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_dataset_properties (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '소속 데이터셋 (catalog_datasets(id) 참조, 삭제 시 CASCADE)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    property_key VARCHAR(100) NOT NULL COMMENT '속성 키 (dataset_id와 함께 유일)',
    property_value TEXT NOT NULL COMMENT '속성 값 (문자열)',
    UNIQUE (dataset_id, property_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Dataset Schema
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_dataset_schemas (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '소속 데이터셋 (catalog_datasets(id) 참조, 삭제 시 CASCADE)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    field_path VARCHAR(500) NOT NULL COMMENT '필드(컬럼) 경로/이름',
    field_type VARCHAR(100) NOT NULL COMMENT '논리 데이터 타입',
    native_type VARCHAR(100) COMMENT '데이터 소스 원본 타입',
    description TEXT COMMENT '필드 설명',
    nullable VARCHAR(5) COMMENT 'NULL 허용 여부 ("true"/"false")',
    ordinal INT NOT NULL COMMENT '필드 정렬 순서(0-base)',
    is_primary_key VARCHAR(5) DEFAULT 'false' COMMENT '기본키 여부 ("true"/"false")',
    is_indexed VARCHAR(5) DEFAULT 'false' COMMENT '인덱스 여부 ("true"/"false")',
    is_unique VARCHAR(5) DEFAULT 'false' COMMENT '유니크 제약 여부 ("true"/"false")',
    is_partition_key VARCHAR(5) DEFAULT 'false' COMMENT '파티션 키 여부 ("true"/"false")',
    is_distribution_key VARCHAR(5) DEFAULT 'false' COMMENT '분산 키 여부 ("true"/"false")'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Schema Snapshots (change history)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_schema_snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '대상 데이터셋 (catalog_datasets(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '스냅샷 생성(동기화) 시각',
    schema_json TEXT NOT NULL COMMENT '전체 스키마 본문 (JSON 배열)',
    field_count INT COMMENT '스냅샷의 컬럼/필드 개수',
    change_summary VARCHAR(500) COMMENT '변경 요약 (예: "Added 2, Modified 1, Dropped 1")',
    changes_json TEXT COMMENT '개별 변경 내역 (JSON 배열, ADD/MODIFY/DROP)'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Tags
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_tags (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    name VARCHAR(100) NOT NULL UNIQUE COMMENT '태그 이름 (고유)',
    description TEXT COMMENT '태그 설명',
    color VARCHAR(7) COMMENT '표시 색상 (HEX, 예: #3b82f6)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Dataset-Tag Mapping
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_dataset_tags (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '데이터셋 ID (catalog_datasets(id) 참조, 삭제 시 CASCADE)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    tag_id INT NOT NULL COMMENT '태그 ID (catalog_tags(id) 참조, 삭제 시 CASCADE)' REFERENCES catalog_tags(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Glossary Terms
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_glossary_terms (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    name VARCHAR(200) NOT NULL UNIQUE COMMENT '용어명 (고유)',
    description TEXT COMMENT '용어 설명',
    parent_id INT COMMENT '상위 용어 ID (catalog_glossary_terms(id) 자기참조, 계층 구조)' REFERENCES catalog_glossary_terms(id),
    term_type VARCHAR(20) NOT NULL DEFAULT 'TERM' COMMENT '항목 유형 (CATEGORY/TERM)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Dataset-Glossary Mapping
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_dataset_glossary_terms (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '데이터셋 ID (catalog_datasets(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    term_id INT NOT NULL COMMENT '용어집 용어 ID (catalog_glossary_terms(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_glossary_terms(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Ownership
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_owners (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '대상 데이터셋 (catalog_datasets(id) 참조)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    owner_name VARCHAR(200) NOT NULL COMMENT '소유자 이름(사람 또는 팀)',
    owner_type VARCHAR(50) NOT NULL COMMENT '소유자 유형 (TECHNICAL_OWNER/BUSINESS_OWNER 등)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Datasource Metadata - Data Types
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasource_data_types (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    datasource_id INT NOT NULL COMMENT '소속 데이터 소스 (catalog_datasources(id) 참조, CASCADE)' REFERENCES catalog_datasources(id) ON DELETE CASCADE,
    type_name VARCHAR(100) NOT NULL COMMENT '데이터 타입 이름 (예: VARCHAR, BIGINT, TIMESTAMP)',
    type_category VARCHAR(50) NOT NULL COMMENT '타입 분류 (예: STRING/NUMERIC/DATETIME/BINARY)',
    description VARCHAR(500) COMMENT '타입 설명',
    ordinal INT NOT NULL COMMENT '표시 정렬 순서',
    UNIQUE (datasource_id, type_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Datasource Metadata - Table Types
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasource_table_types (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    datasource_id INT NOT NULL COMMENT '소속 데이터소스 (catalog_datasources(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_datasources(id) ON DELETE CASCADE,
    type_name VARCHAR(100) NOT NULL COMMENT '테이블 타입 코드 (TABLE/VIEW/MATERIALIZED_VIEW 등)',
    display_name VARCHAR(200) NOT NULL COMMENT 'UI 표시용 타입 이름',
    description VARCHAR(500) COMMENT '타입 설명',
    is_default VARCHAR(5) COMMENT '기본 선택 타입 여부 (문자열 ''true''/''false'')',
    ordinal INT NOT NULL COMMENT 'UI 정렬 순서 (오름차순)',
    UNIQUE (datasource_id, type_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Datasource Metadata - Storage Formats
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasource_storage_formats (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    datasource_id INT NOT NULL COMMENT '소속 데이터소스 (catalog_datasources(id) 참조, CASCADE 삭제)' REFERENCES catalog_datasources(id) ON DELETE CASCADE,
    format_name VARCHAR(100) NOT NULL COMMENT '포맷 식별 코드 (예: PARQUET/ORC/CSV)',
    display_name VARCHAR(200) NOT NULL COMMENT '화면 표시용 포맷 이름',
    description VARCHAR(500) COMMENT '포맷 설명',
    is_default VARCHAR(5) COMMENT '기본 포맷 여부 (true/false 문자열)',
    ordinal INT NOT NULL COMMENT '표시 정렬 순서',
    UNIQUE (datasource_id, format_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Datasource Metadata - Features
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasource_features (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    datasource_id INT NOT NULL COMMENT '소속 데이터소스 (catalog_datasources(id) 참조, CASCADE 삭제)' REFERENCES catalog_datasources(id) ON DELETE CASCADE,
    feature_key VARCHAR(100) NOT NULL COMMENT '파라미터 식별 키 (datasource_id와 함께 유니크)',
    display_name VARCHAR(200) NOT NULL COMMENT '화면 표시용 이름',
    description VARCHAR(500) COMMENT '파라미터 설명',
    value_type VARCHAR(50) NOT NULL COMMENT '값 자료형 (string/int/bool 등)',
    is_required VARCHAR(5) COMMENT '필수 입력 여부 (문자열 true/false)',
    ordinal INT NOT NULL COMMENT '표시 정렬 순서',
    UNIQUE (datasource_id, feature_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Role Management
--   argus_users 가 role_id FK 로 참조하므로 반드시 먼저 생성해야 한다.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_roles (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK (auto-increment)',
    name VARCHAR(50) NOT NULL COMMENT '화면 표시용 역할 이름 (예: Admin, Superuser, User)',
    description VARCHAR(255) COMMENT '역할 설명 (선택)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    role_id VARCHAR(50) NOT NULL COMMENT '역할 식별자, Keycloak realm role 이름과 1:1 매칭 (예: argus-admin), UNIQUE'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- User Management
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_users (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    username VARCHAR(100) NOT NULL UNIQUE COMMENT '로그인 식별자 (UNIQUE)',
    email VARCHAR(255) NOT NULL UNIQUE COMMENT '이메일 주소 (UNIQUE)',
    first_name VARCHAR(100) NOT NULL COMMENT '이름',
    last_name VARCHAR(100) NOT NULL COMMENT '성',
    phone_number VARCHAR(30) COMMENT '연락처 전화번호 (선택)',
    password_hash VARCHAR(255) NOT NULL COMMENT '비밀번호 SHA-256 해시 (평문 저장 금지)',
    status VARCHAR(20) NOT NULL COMMENT '계정 상태 (active/inactive)',
    role_id INT NOT NULL COMMENT '역할 참조, argus_roles(id) 참조' REFERENCES argus_roles(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- User Preferences (per-user UI preferences keyed by token sub)
--   로컬 인증: sub = str(argus_users.id)
--   Keycloak 인증: sub = Keycloak user UUID
-- 두 인증 모드에서 동일한 키로 동작하도록 별도 테이블로 분리
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_user_preferences (
    sub VARCHAR(100) PRIMARY KEY COMMENT 'PK, 사용자 식별자 (로컬=argus_users.id 문자열, Keycloak=user UUID)',
    avatar_preset_id VARCHAR(50) COMMENT '선택한 아바타 preset 식별자',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;



-- ---------------------------------------------------------------------------
-- ML Model Registry - Registered Models
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_registered_models (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    name VARCHAR(255) NOT NULL UNIQUE COMMENT '3단 모델 이름 (catalog.schema.model, 전역 고유)',
    urn VARCHAR(500) NOT NULL UNIQUE COMMENT '모델 URN ({name}.{ENV}.model, 전역 고유)',
    datasource_id INT COMMENT '연결된 데이터소스 (catalog_datasources(id) 참조, 삭제 시 NULL)' REFERENCES catalog_datasources(id) ON DELETE SET NULL,
    description TEXT COMMENT '모델 설명',
    owner VARCHAR(200) COMMENT '모델 소유자',
    storage_location VARCHAR(1000) COMMENT '아티팩트 기본 저장 경로 (file:// 또는 s3://)',
    max_version_number INT NOT NULL COMMENT '생성된 최대 버전 번호 (버전마다 자동 증가)',
    status VARCHAR(20) NOT NULL COMMENT '모델 상태 (active/deleted, soft delete)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    created_by VARCHAR(200) COMMENT '생성자',
    updated_by VARCHAR(200) COMMENT '수정자',
    storage_type VARCHAR(20) NOT NULL DEFAULT 'local' COMMENT '저장소 유형 (local/s3)',
    bucket_name VARCHAR(255) COMMENT 'S3 버킷 이름 (storage_type=s3 일 때)'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- ML Model Registry - Model Versions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_model_versions (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    model_id INT NOT NULL COMMENT '등록 모델 (catalog_registered_models(id) 참조)',
    version INT NOT NULL COMMENT '모델별 자동 증가 버전 번호 (1,2,3...)',
    source VARCHAR(1000) COMMENT 'MLflow 아티팩트 소스 URI (예: models:/m-abc123)',
    run_id VARCHAR(255) COMMENT '이 버전을 생성한 MLflow run ID',
    run_link VARCHAR(1000) COMMENT 'MLflow run UI 링크 (선택)',
    description TEXT COMMENT '버전 설명',
    status VARCHAR(30) NOT NULL COMMENT '등록 상태 (PENDING_REGISTRATION/READY/FAILED_REGISTRATION)',
    status_message TEXT COMMENT '상태 사유 메시지 (예: 등록 중 모델 삭제)',
    storage_location VARCHAR(1000) COMMENT '버전별 아티팩트 저장 경로 (file:///.../{name}/versions/{ver}/)',
    artifact_count INT COMMENT 'finalize 시점 집계한 아티팩트 파일 수',
    artifact_size INT COMMENT 'finalize 시점 집계한 아티팩트 총 크기(byte)',
    finished_at TIMESTAMP COMMENT 'finalize 호출 시각 (NULL=미완료)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    stage VARCHAR(20) DEFAULT 'NONE' COMMENT '배포 스테이지 (NONE/STAGING/PRODUCTION/ARCHIVED)',
    stage_changed_at TIMESTAMP NULL COMMENT '스테이지 변경 시각',
    stage_changed_by VARCHAR(200) COMMENT '스테이지 변경자',
    created_by VARCHAR(200) COMMENT '생성자',
    updated_by VARCHAR(200) COMMENT '수정자',
    UNIQUE (model_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- ML Model Registry - Model Metadata
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_models (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    model_version_id INT NOT NULL COMMENT 'catalog_model_versions(id) 참조 (ON DELETE CASCADE)',
    model_name VARCHAR(255) NOT NULL COMMENT '모델 이름 (version과 UNIQUE)',
    version INT NOT NULL COMMENT '모델 버전 번호 (model_name과 UNIQUE)',
    predict_fn VARCHAR(100) COMMENT 'MLmodel의 예측 함수명 (예: predict)',
    python_version VARCHAR(20) COMMENT '모델 학습 환경 Python 버전',
    serialization_format VARCHAR(50) COMMENT '모델 직렬화 포맷 (예: cloudpickle)',
    sklearn_version VARCHAR(20) COMMENT 'scikit-learn 버전',
    mlflow_version VARCHAR(20) COMMENT 'MLflow 버전',
    mlflow_model_id VARCHAR(100) COMMENT 'MLflow 모델 식별자',
    model_size_bytes BIGINT COMMENT '모델 아티팩트 크기(byte)',
    utc_time_created VARCHAR(50) COMMENT 'MLmodel 생성 시각 (UTC 원본 문자열)',
    time_created TIMESTAMP COMMENT '생성 시각 (서버 로컬 타임존 변환값)',
    requirements TEXT COMMENT 'requirements.txt 원본 내용',
    conda TEXT COMMENT 'conda.yaml 원본 내용',
    python_env TEXT COMMENT 'python_env.yaml 원본 내용',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '레코드 생성 시각',
    manifest TEXT COMMENT 'OCI 매니페스트 원본 내용',
    config TEXT COMMENT 'OCI config 원본 내용',
    content_digest VARCHAR(100) COMMENT 'OCI 콘텐츠 다이제스트 해시',
    source_type VARCHAR(50) COMMENT '모델 출처 (mlflow/huggingface/local/oras)',
    UNIQUE (model_name, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Model Download Log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_model_download_log (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    model_name VARCHAR(255) NOT NULL COMMENT '대상 모델 이름',
    version INT NOT NULL COMMENT '모델 버전 번호',
    download_type VARCHAR(20) NOT NULL COMMENT '다운로드 유형 (load=MLflow, pull=SDK, download=단일 파일)',
    client_ip VARCHAR(45) COMMENT '요청 클라이언트 IP (IPv4/IPv6)',
    user_agent VARCHAR(500) COMMENT '요청 User-Agent 문자열',
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '다운로드 발생 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE INDEX IF NOT EXISTS ix_catalog_model_download_log_downloaded_at ON catalog_model_download_log (downloaded_at);
CREATE INDEX IF NOT EXISTS ix_catalog_model_download_log_model_name ON catalog_model_download_log (model_name);

-- ---------------------------------------------------------------------------
-- OCI Model Hub - Models
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_oci_models (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    name VARCHAR(255) NOT NULL UNIQUE COMMENT '모델 고유 이름 (UNIQUE, 식별자)',
    display_name VARCHAR(255) COMMENT '화면 표시용 모델 이름',
    description TEXT COMMENT '모델 요약 설명',
    readme TEXT COMMENT '모델 상세 문서 (Markdown README)',
    task VARCHAR(50) COMMENT '수행 작업 유형 (예: classification/nlp 등 task 코드)',
    framework VARCHAR(50) COMMENT '사용 프레임워크 (예: pytorch/tensorflow)',
    language VARCHAR(50) COMMENT '지원 언어 코드 (예: ko/en)',
    license VARCHAR(100) COMMENT '라이선스 식별자 (예: Apache-2.0)',
    source_type VARCHAR(50) COMMENT '모델 출처 유형 (예: huggingface/upload 등)',
    source_id VARCHAR(500) COMMENT '출처 식별자 (원본 모델 ID/경로)',
    source_revision VARCHAR(100) COMMENT '출처 리비전 (커밋/브랜치/태그)',
    bucket VARCHAR(255) COMMENT '아티팩트 저장 OCI 버킷명',
    storage_prefix VARCHAR(500) COMMENT '버킷 내 저장 경로 prefix',
    owner VARCHAR(200) COMMENT '모델 소유자(담당자)',
    version_count INT NOT NULL COMMENT '등록된 버전 수 (비정규화 집계)',
    total_size BIGINT COMMENT '전체 아티팩트 크기(byte, 비정규화 집계)',
    download_count INT NOT NULL COMMENT '누적 다운로드 횟수 (비정규화 집계)',
    status VARCHAR(20) NOT NULL COMMENT '라이프사이클 상태 (draft/review/approved/production/deprecated/archived)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- OCI Model Hub - Versions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_oci_model_versions (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    model_id INT NOT NULL COMMENT '소속 모델 (catalog_oci_models(id) 참조, CASCADE 삭제)' REFERENCES catalog_oci_models(id) ON DELETE CASCADE,
    version INT NOT NULL COMMENT '모델 버전 번호 (model_id별 UNIQUE)',
    manifest TEXT COMMENT 'OCI manifest.json 원문',
    content_digest VARCHAR(100) COMMENT '아티팩트 콘텐츠 다이제스트 (sha256 해시)',
    file_count INT COMMENT '버전에 포함된 파일 개수',
    total_size BIGINT COMMENT '아티팩트 전체 크기(byte)',
    metadata JSON COMMENT '추가 메타데이터 (JSONB)',
    status VARCHAR(20) NOT NULL COMMENT '버전 상태 (ready 등)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE (model_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- OCI Model Hub - Tags
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_oci_model_tags (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    model_id INT NOT NULL COMMENT 'OCI 모델 ID (catalog_oci_models(id) 참조, CASCADE 삭제)' REFERENCES catalog_oci_models(id) ON DELETE CASCADE,
    tag_id INT NOT NULL COMMENT '태그 ID (catalog_tags(id) 참조, CASCADE 삭제)' REFERENCES catalog_tags(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE (model_id, tag_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- OCI Model Hub - Lineage
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_oci_model_lineage (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    model_id INT NOT NULL COMMENT '대상 OCI 모델 (catalog_oci_models(id) 참조, CASCADE)' REFERENCES catalog_oci_models(id) ON DELETE CASCADE,
    source_type VARCHAR(20) NOT NULL COMMENT '연관 자원 유형 (dataset/model/huggingface 등)',
    source_id VARCHAR(255) NOT NULL COMMENT '연관 자원 식별자',
    source_name VARCHAR(255) COMMENT '연관 자원 표시명',
    relation_type VARCHAR(30) NOT NULL COMMENT '관계 유형 (trained_on/derived_from 등)',
    description TEXT COMMENT '관계 설명',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- OCI Model Hub - Download Log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_oci_model_download_log (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    model_name VARCHAR(255) NOT NULL COMMENT '다운로드 대상 모델 이름',
    version INT NOT NULL COMMENT '다운로드 대상 모델 버전',
    download_type VARCHAR(20) NOT NULL COMMENT '다운로드 유형 (download/pull)',
    client_ip VARCHAR(45) COMMENT '요청 클라이언트 IP (IPv4/IPv6)',
    user_agent VARCHAR(500) COMMENT '요청 클라이언트 User-Agent',
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '다운로드 발생 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE INDEX IF NOT EXISTS ix_catalog_oci_model_download_log_downloaded_at ON catalog_oci_model_download_log (downloaded_at);
CREATE INDEX IF NOT EXISTS ix_catalog_oci_model_download_log_model_name ON catalog_oci_model_download_log (model_name);

-- ---------------------------------------------------------------------------
-- Comments
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_comments (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    entity_type VARCHAR(50) NOT NULL COMMENT '댓글 대상 엔티티 유형 (dataset/model/glossary 등)',
    entity_id VARCHAR(255) NOT NULL COMMENT '댓글 대상 엔티티 식별자 (FK 아님, 다형성 참조)',
    parent_id INT COMMENT '부모 댓글 (catalog_comments(id) 참조, NULL이면 최상위)' REFERENCES catalog_comments(id) ON DELETE CASCADE,
    root_id INT COMMENT '스레드 최상위 댓글 (catalog_comments(id) 참조)' REFERENCES catalog_comments(id) ON DELETE CASCADE,
    depth INT NOT NULL COMMENT '댓글 중첩 깊이 (0=최상위)',
    content TEXT NOT NULL COMMENT '댓글 본문 (서식 포함)',
    content_plain TEXT COMMENT '댓글 본문 평문 (검색·미리보기용)',
    author_name VARCHAR(100) NOT NULL COMMENT '작성자 표시 이름',
    author_email VARCHAR(255) COMMENT '작성자 이메일',
    author_avatar VARCHAR(500) COMMENT '작성자 아바타 이미지 URL',
    reply_count INT NOT NULL COMMENT '답글 수 (비정규화 캐시값)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    is_deleted TINYINT(1) NOT NULL DEFAULT 0 COMMENT '소프트 삭제 여부',
    category VARCHAR(20) NOT NULL DEFAULT 'general' COMMENT '댓글 분류 (general/suggestion/feature/bug)'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE INDEX IF NOT EXISTS ix_catalog_comments_entity_id ON catalog_comments (entity_id);
CREATE INDEX IF NOT EXISTS ix_catalog_comments_entity_type ON catalog_comments (entity_type);

-- ---------------------------------------------------------------------------
-- Configuration
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_configuration (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    category VARCHAR(50) NOT NULL COMMENT '설정 분류 카테고리 (예: storage)',
    config_key VARCHAR(100) NOT NULL UNIQUE COMMENT '설정 키 (고유)',
    config_value VARCHAR(500) NOT NULL COMMENT '설정 값 (문자열)',
    description VARCHAR(255) COMMENT '설정 항목 설명',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Collector - Hive Query History
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_collector_hive_query_history (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    query_id VARCHAR(256) NOT NULL UNIQUE COMMENT 'Hive 쿼리 식별자 (unique)',
    short_username VARCHAR(128) COMMENT '실행 사용자 짧은 이름',
    username VARCHAR(256) COMMENT '실행 사용자 전체 이름',
    operation_name VARCHAR(64) COMMENT '작업 유형 (QUERY/DDL 등)',
    start_time BIGINT COMMENT '쿼리 시작 시각(epoch ms)',
    end_time BIGINT COMMENT '쿼리 종료 시각(epoch ms)',
    duration_ms BIGINT COMMENT '쿼리 수행 시간(ms)',
    query TEXT COMMENT '실행된 SQL 원문',
    status VARCHAR(16) NOT NULL COMMENT '실행 상태 (SUCCESS/FAILED 등)',
    error_msg TEXT COMMENT '실패 시 오류 메시지',
    inputs_json TEXT COMMENT 'hook 권위 입력 테이블(JSON 배열). 배치 write-lineage 우선 사용',
    outputs_json TEXT COMMENT 'hook 권위 출력 테이블(JSON 배열)',
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수집 서버 수신 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- query_id 는 UNIQUE 제약이 인덱스를 겸하므로 별도 인덱스 불필요.
CREATE INDEX IF NOT EXISTS idx_hive_query_history_status ON argus_collector_hive_query_history (status);

-- ---------------------------------------------------------------------------
-- Collector - Impala Query History
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_collector_impala_query_history (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    query_id VARCHAR(256) NOT NULL UNIQUE COMMENT 'Impala 쿼리 고유 식별자 (unique)',
    query_type VARCHAR(32) COMMENT '쿼리 유형 (DML/DDL/QUERY/UNKNOWN)',
    query_state VARCHAR(32) COMMENT '쿼리 실행 상태 (FINISHED/EXCEPTION 등)',
    statement TEXT COMMENT '실행된 SQL 문',
    database VARCHAR(256) COMMENT '쿼리 대상 데이터베이스명',
    username VARCHAR(256) COMMENT '실효 사용자 (delegate 지정 시 그 값, 아니면 접속 사용자)',
    coordinator_host VARCHAR(512) COMMENT '쿼리를 조정한 coordinator 호스트',
    start_time TIMESTAMP COMMENT '쿼리 시작 시각',
    end_time TIMESTAMP COMMENT '쿼리 종료 시각',
    duration_ms BIGINT COMMENT '쿼리 실행 소요 시간(ms)',
    rows_produced BIGINT COMMENT '쿼리가 생성한 행 수',
    datasource_id VARCHAR(100) COMMENT '수집 출처 데이터소스 식별자',
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수집 적재 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Collector - Trino Query History
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_collector_trino_query_history (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    query_id VARCHAR(256) NOT NULL UNIQUE COMMENT 'Trino 쿼리 고유 ID (unique)',
    query_state VARCHAR(32) COMMENT '쿼리 종료 상태 (FINISHED/FAILED)',
    query_type VARCHAR(32) COMMENT '쿼리 유형 (SELECT/INSERT 등)',
    statement TEXT COMMENT '실행된 SQL 원문',
    plan TEXT COMMENT '쿼리 실행 계획(plan)',
    username VARCHAR(256) COMMENT '실효 사용자명',
    principal VARCHAR(256) COMMENT '인증 주체 (Kerberos/OAuth principal)',
    source VARCHAR(256) COMMENT '클라이언트 도구 (trino-cli 등)',
    catalog VARCHAR(256) COMMENT '대상 카탈로그명',
    schema VARCHAR(256) COMMENT '대상 스키마명',
    remote_client_address VARCHAR(256) COMMENT '클라이언트 원격 주소(IP)',
    create_time TIMESTAMP COMMENT '쿼리 생성 시각',
    execution_start_time TIMESTAMP COMMENT '실행 시작 시각',
    end_time TIMESTAMP COMMENT '실행 종료 시각',
    wall_time_ms BIGINT COMMENT '총 실행 경과 시간(ms)',
    cpu_time_ms BIGINT COMMENT 'CPU 사용 시간(ms)',
    physical_input_bytes BIGINT COMMENT '물리 입력 데이터 크기(bytes)',
    physical_input_rows BIGINT COMMENT '물리 입력 행 수',
    output_bytes BIGINT COMMENT '출력 데이터 크기(bytes)',
    output_rows BIGINT COMMENT '출력 행 수',
    peak_memory_bytes BIGINT COMMENT '최대 메모리 사용량(bytes)',
    error_code VARCHAR(128) COMMENT '오류 코드',
    error_message TEXT COMMENT '오류 메시지',
    inputs_json TEXT COMMENT '입력 테이블 목록 JSON ([{catalog,schema,table,columns}])',
    output_json TEXT COMMENT '출력 테이블 정보 JSON ({catalog,schema,table,columns})',
    datasource_id VARCHAR(100) COMMENT '수집 대상 데이터소스 식별자',
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수집(수신) 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE INDEX IF NOT EXISTS idx_trino_query_history_datasource_id ON argus_collector_trino_query_history (datasource_id);
CREATE INDEX IF NOT EXISTS idx_trino_query_history_query_id ON argus_collector_trino_query_history (query_id);
CREATE INDEX IF NOT EXISTS idx_trino_query_history_username ON argus_collector_trino_query_history (username);

-- ---------------------------------------------------------------------------
-- Collector - StarRocks Query History
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_collector_starrocks_query_history (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    query_id VARCHAR(256) NOT NULL UNIQUE COMMENT 'StarRocks 쿼리 고유 ID (unique)',
    statement TEXT COMMENT '실행된 SQL 원문',
    digest VARCHAR(64) COMMENT '쿼리 지문(fingerprint) 해시',
    username VARCHAR(256) COMMENT '실제 적용된 유효 사용자명',
    authorized_user VARCHAR(256) COMMENT '인증된 사용자명',
    client_ip VARCHAR(64) COMMENT '클라이언트 IP',
    database VARCHAR(256) COMMENT '실행 대상 데이터베이스',
    catalog VARCHAR(256) COMMENT '실행 대상 카탈로그',
    state VARCHAR(16) COMMENT '쿼리 종료 상태 (EOF/ERR/OK)',
    error_code VARCHAR(512) COMMENT '오류 코드/메시지',
    query_time_ms BIGINT COMMENT '쿼리 실행 시간(ms)',
    scan_rows BIGINT COMMENT '스캔한 행 수',
    scan_bytes BIGINT COMMENT '스캔한 바이트 수',
    return_rows BIGINT COMMENT '반환한 행 수',
    cpu_cost_ns BIGINT COMMENT 'CPU 비용(ns)',
    mem_cost_bytes BIGINT COMMENT '메모리 비용(bytes)',
    pending_time_ms BIGINT COMMENT '대기 시간(ms)',
    is_query INT COMMENT '쿼리 여부 (1=query, 0=non-query)',
    fe_ip VARCHAR(128) COMMENT '처리한 FE 노드 IP',
    event_timestamp BIGINT COMMENT 'AuditEvent 발생 시각(epoch millis)',
    datasource_id VARCHAR(100) COMMENT '데이터소스 식별자',
    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수집 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE INDEX IF NOT EXISTS idx_starrocks_query_history_datasource_id ON argus_collector_starrocks_query_history (datasource_id);
CREATE INDEX IF NOT EXISTS idx_starrocks_query_history_query_id ON argus_collector_starrocks_query_history (query_id);
CREATE INDEX IF NOT EXISTS idx_starrocks_query_history_username ON argus_collector_starrocks_query_history (username);

-- ---------------------------------------------------------------------------
-- Lineage - Query Lineage (per-query source→target table mapping)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_query_lineage (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    query_hist_id INT COMMENT '원본 쿼리 이력 ID (argus_query_history(id) 참조)',
    source_table VARCHAR(512) NOT NULL COMMENT '소스 테이블 정규화 이름 (schema.table)',
    target_table VARCHAR(512) NOT NULL COMMENT '타깃 테이블 정규화 이름 (schema.table)',
    source_dataset_id INT COMMENT '소스 데이터셋 ID (catalog_datasets(id) 참조, 매칭 시)',
    target_dataset_id INT COMMENT '타깃 데이터셋 ID (catalog_datasets(id) 참조, 매칭 시)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    -- 멱등성: 동일 (source→target) 엣지 dedup. utf8mb4 키 길이 한계(3072B)로 prefix 인덱스 사용.
    UNIQUE KEY uq_query_lineage_edge (source_table(200), target_table(200))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Lineage - Column Lineage (per-query source→target column mapping)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_column_lineage (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    query_lineage_id INT NOT NULL COMMENT '소속 쿼리 리니지 ID (argus_query_lineage(id) 참조)',
    source_column VARCHAR(256) NOT NULL COMMENT '원본(source) 컬럼명',
    target_column VARCHAR(256) NOT NULL COMMENT '대상(target) 컬럼명',
    transform_type VARCHAR(64) NOT NULL DEFAULT 'DIRECT' COMMENT '변환 유형 (기본 DIRECT)',
    -- 멱등성: 동일 query_lineage 내 (source_col→target_col) 매핑 dedup
    UNIQUE KEY uq_column_lineage_edge (query_lineage_id, source_column, target_column)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Lineage - Data Pipeline (ETL/CDC/file-export pipeline registry)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_data_pipeline (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    pipeline_name VARCHAR(255) NOT NULL UNIQUE COMMENT '파이프라인 고유 이름 (unique)',
    description TEXT COMMENT '파이프라인 설명',
    pipeline_type VARCHAR(64) NOT NULL COMMENT '파이프라인 유형 (ETL/FILE_EXPORT/CDC/REPLICATION/API/MANUAL)',
    schedule VARCHAR(100) COMMENT '실행 주기 (cron 표현식, 예: "0 2 * * *")',
    owner VARCHAR(200) COMMENT '파이프라인 담당자',
    status VARCHAR(20) NOT NULL COMMENT '파이프라인 상태 (ACTIVE/INACTIVE/DEPRECATED)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------------
-- Lineage - Dataset Lineage (aggregated dataset-to-dataset relationships)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_dataset_lineage (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    source_dataset_id INT NOT NULL COMMENT '원본(데이터 제공) 데이터셋, catalog_datasets(id) 참조' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    target_dataset_id INT NOT NULL COMMENT '대상(데이터 수신) 데이터셋, catalog_datasets(id) 참조' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    relation_type VARCHAR(32) NOT NULL COMMENT '관계 유형 (ETL/FILE_EXPORT/CDC/REPLICATION/DERIVED/READ_WRITE)',
    lineage_source VARCHAR(32) NOT NULL COMMENT '리니지 출처 (QUERY_AGGREGATED/PIPELINE/MANUAL)',
    pipeline_id INT COMMENT '파이프라인 참조 (PIPELINE 출처일 때), argus_data_pipeline(id) 참조' REFERENCES argus_data_pipeline(id) ON DELETE SET NULL,
    description TEXT COMMENT '리니지 관계 설명',
    created_by VARCHAR(200) COMMENT '등록한 사용자',
    query_count INT NOT NULL COMMENT '이 관계를 확인한 쿼리 수 (자동 수집 시)',
    last_query_id VARCHAR(256) COMMENT '마지막으로 확인된 쿼리 ID',
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '마지막 확인 시각',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE (source_dataset_id, target_dataset_id, relation_type, lineage_source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE INDEX IF NOT EXISTS idx_dataset_lineage_pipeline ON argus_dataset_lineage (pipeline_id);
CREATE INDEX IF NOT EXISTS idx_dataset_lineage_source ON argus_dataset_lineage (source_dataset_id);
CREATE INDEX IF NOT EXISTS idx_dataset_lineage_target ON argus_dataset_lineage (target_dataset_id);

-- ---------------------------------------------------------------------------
-- Lineage - Dataset Column Mapping (cross-datasource column-level lineage)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_dataset_column_mapping (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_lineage_id INT NOT NULL COMMENT '소속 리니지 (argus_dataset_lineage(id) 참조, CASCADE)' REFERENCES argus_dataset_lineage(id) ON DELETE CASCADE,
    source_column VARCHAR(256) NOT NULL COMMENT '원본 컬럼명',
    target_column VARCHAR(256) NOT NULL COMMENT '대상 컬럼명',
    transform_type VARCHAR(64) NOT NULL COMMENT '변환 유형 (DIRECT/CAST/EXPRESSION/DERIVED)',
    transform_expr VARCHAR(500) COMMENT '변환 수식 (CAST/EXPRESSION 시, 예: CAST(emp_id AS BIGINT))',
    UNIQUE (dataset_lineage_id, source_column, target_column)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE INDEX IF NOT EXISTS idx_dataset_column_mapping_lineage ON argus_dataset_column_mapping (dataset_lineage_id);

-- ---------------------------------------------------------------------------
-- Relationship - Column Relationship (사용 기반 컬럼 관계, 쿼리 JOIN 키 빈도)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_column_relationship (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_a_id INT NOT NULL COMMENT '무방향 한쪽 데이터셋(정규화 시 작은 쪽), catalog_datasets(id) 참조' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    field_a VARCHAR(500) NOT NULL COMMENT 'dataset_a 의 컬럼(field_path)',
    dataset_b_id INT NOT NULL COMMENT '무방향 다른 쪽 데이터셋, catalog_datasets(id) 참조' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    field_b VARCHAR(500) NOT NULL COMMENT 'dataset_b 의 컬럼(field_path)',
    relation_type VARCHAR(32) NOT NULL DEFAULT 'JOIN_KEY' COMMENT '관계 유형: JOIN_KEY (확장: FILTER_EQ, CO_GROUP)',
    join_count INT NOT NULL DEFAULT 0 COMMENT 'confidence — 이 관계가 등장한 쿼리 수(명시+암묵)',
    explicit_count INT NOT NULL DEFAULT 0 COMMENT '명시적 JOIN ... ON 으로 관측된 횟수',
    implicit_count INT NOT NULL DEFAULT 0 COMMENT '암묵 조인(WHERE 등치)으로 관측된 횟수',
    distinct_users INT NOT NULL DEFAULT 0 COMMENT '관측된 사용자 다양성(선택)',
    first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '최초 관측 시각',
    last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '최근 관측 시각',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE (dataset_a_id, field_a, dataset_b_id, field_b, relation_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='사용 기반 컬럼 관계 — 쿼리 JOIN 키에서 발견한 무방향 컬럼쌍';

CREATE INDEX IF NOT EXISTS idx_column_relationship_a ON catalog_column_relationship (dataset_a_id);
CREATE INDEX IF NOT EXISTS idx_column_relationship_b ON catalog_column_relationship (dataset_b_id);

-- ---------------------------------------------------------------------------
-- Relationship Analyzer — 2단계 파이프라인(관측치 → 롤업) staging/watermark
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS relationship_observations (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    query_id      VARCHAR(256) NOT NULL,
    datasource_id VARCHAR(100) NOT NULL,
    a_table       VARCHAR(512) NOT NULL,
    a_col         VARCHAR(512) NOT NULL,
    b_table       VARCHAR(512) NOT NULL,
    b_col         VARCHAR(512) NOT NULL,
    kind          VARCHAR(16)  NOT NULL DEFAULT 'explicit',
    query_user    VARCHAR(256),
    observed_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_relobs (query_id, a_table(150), a_col(150), b_table(150), b_col(150)),
    INDEX idx_relobs_datasource (datasource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='쿼리 JOIN 키 관측치(staging). rollup 이 집계';

CREATE TABLE IF NOT EXISTS relationship_ingest_offset (
    partition_key   VARCHAR(100) PRIMARY KEY,
    last_event_id   BIGINT NOT NULL DEFAULT 0,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='analyzer 워터마크(파티션=플랫폼)';

CREATE TABLE IF NOT EXISTS lineage_ingest_offset (
    partition_key   VARCHAR(100) PRIMARY KEY,
    last_event_id   BIGINT NOT NULL DEFAULT 0,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='query-service write-lineage 배치 워터마크(파티션=플랫폼)';

-- ---------------------------------------------------------------------------
-- Alert - Alert Rule (what to watch, when to trigger, who to notify)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_alert_rule (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    rule_name VARCHAR(255) NOT NULL COMMENT '규칙 이름',
    description TEXT COMMENT '규칙 설명',
    scope_type VARCHAR(32) NOT NULL COMMENT '감시 범위 (DATASET/TAG/LINEAGE/DATASOURCE/ALL)',
    scope_id INT COMMENT '감시 대상 ID (ALL이면 NULL)',
    trigger_type VARCHAR(64) NOT NULL COMMENT '트리거 유형 (ANY/SCHEMA_CHANGE/COLUMN_WATCH/MAPPING_BROKEN/SYNC_STALE/QUALITY_FAILED)',
    trigger_config TEXT DEFAULT '{}' COMMENT '트리거 조건 상세 (JSON)',
    severity_override VARCHAR(16) COMMENT '심각도 강제 지정 (NULL이면 자동 판정)',
    channels VARCHAR(200) NOT NULL DEFAULT 'IN_APP' COMMENT '알림 채널 (콤마 구분, 예: IN_APP/WEBHOOK/EMAIL)',
    notify_owners VARCHAR(5) NOT NULL DEFAULT 'true' COMMENT '데이터셋 Owner 알림 여부 (true/false)',
    webhook_url VARCHAR(500) COMMENT 'WEBHOOK 전송 URL',
    subscribers VARCHAR(2000) COMMENT '구독자 목록 (콤마 구분)',
    is_active VARCHAR(5) NOT NULL DEFAULT 'true' COMMENT '활성화 여부 (true/false)',
    created_by VARCHAR(200) COMMENT '생성자',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 시각',
    INDEX idx_alert_rule_scope (scope_type, scope_id),
    INDEX idx_alert_rule_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Alert - Lineage Alert (schema change impact events)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_lineage_alert (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    alert_type VARCHAR(32) NOT NULL COMMENT '알림 유형 (SCHEMA_CHANGE/LINEAGE_BROKEN/SYNC_FAILED/QUALITY_FAILED)',
    severity VARCHAR(16) NOT NULL COMMENT '심각도 (INFO/WARNING/BREAKING)',
    source_dataset_id INT NOT NULL COMMENT '변경 발생 원본 데이터셋, catalog_datasets(id) 참조' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    affected_dataset_id INT COMMENT '영향받은 하위 데이터셋, catalog_datasets(id) 참조' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    lineage_id INT COMMENT '관련 리니지 관계, argus_dataset_lineage(id) 참조' REFERENCES argus_dataset_lineage(id) ON DELETE SET NULL,
    rule_id INT COMMENT '이 알림을 생성한 규칙, argus_alert_rule(id) 참조' REFERENCES argus_alert_rule(id) ON DELETE SET NULL,
    change_summary VARCHAR(500) NOT NULL COMMENT '변경 요약 메시지',
    change_detail TEXT COMMENT '변경 상세 내용',
    status VARCHAR(20) NOT NULL COMMENT '처리 상태 (OPEN/ACKNOWLEDGED/RESOLVED/DISMISSED)',
    resolved_by VARCHAR(200) COMMENT '해결 처리자',
    resolved_at TIMESTAMP NULL COMMENT '해결 시각',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    INDEX idx_lineage_alert_affected (affected_dataset_id),
    INDEX idx_lineage_alert_source (source_dataset_id),
    INDEX idx_lineage_alert_status (status),
    INDEX idx_lineage_alert_rule (rule_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Alert - Notification Log (delivery records)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_alert_notification (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    alert_id INT NOT NULL COMMENT '대상 알림 (argus_lineage_alert(id) 참조)' REFERENCES argus_lineage_alert(id) ON DELETE CASCADE,
    channel VARCHAR(32) NOT NULL COMMENT '전달 채널 (IN_APP/WEBHOOK/EMAIL)',
    recipient VARCHAR(200) NOT NULL COMMENT '수신자 (이메일/URL/사용자 식별자)',
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '전송 시각',
    status VARCHAR(20) NOT NULL COMMENT '전송 상태 (SENT/FAILED 등)'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE INDEX IF NOT EXISTS idx_alert_notification_alert ON argus_alert_notification (alert_id);

-- ---------------------------------------------------------------------------
-- Data Standard - Dictionary (표준 사전)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_dictionary (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dict_name VARCHAR(200) NOT NULL UNIQUE COMMENT '표준 사전 이름 (고유)',
    description TEXT COMMENT '표준 사전 설명',
    version VARCHAR(50) COMMENT '표준 사전 버전',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT '상태 (ACTIVE/INACTIVE)',
    effective_date DATE COMMENT '적용 시작일',
    expiry_date DATE COMMENT '적용 만료일',
    created_by VARCHAR(200) COMMENT '생성자',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Standard - Word (표준 단어)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_word (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dictionary_id INT NOT NULL COMMENT '소속 표준 사전 (catalog_standard_dictionary(id) 참조, CASCADE)',
    word_name VARCHAR(100) NOT NULL COMMENT '단어 한글명 (예: 고객)',
    word_english VARCHAR(100) NOT NULL COMMENT '단어 영문명 (예: Customer)',
    word_abbr VARCHAR(50) NOT NULL COMMENT '단어 영문 약어 (예: CUST)',
    description TEXT COMMENT '단어 설명',
    word_type VARCHAR(20) NOT NULL DEFAULT 'GENERAL' COMMENT '단어 유형 (GENERAL/SUFFIX/PREFIX)',
    is_forbidden VARCHAR(5) DEFAULT 'false' COMMENT '금칙어 여부 (true/false)',
    synonym_group_id INT COMMENT '이음동의어 그룹 식별자',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT '단어 상태 (ACTIVE 등)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 시각',
    UNIQUE KEY uq_std_word (dictionary_id, word_name),
    INDEX idx_std_word_dict (dictionary_id),
    INDEX idx_std_word_type (word_type),
    FOREIGN KEY (dictionary_id) REFERENCES catalog_standard_dictionary(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Standard - Code Group (코드 그룹)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_code_group (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dictionary_id INT NOT NULL COMMENT '소속 표준 사전 (catalog_standard_dictionary(id) 참조)',
    group_name VARCHAR(200) NOT NULL COMMENT '코드 그룹 한글명',
    group_english VARCHAR(200) COMMENT '코드 그룹 영문명',
    description TEXT COMMENT '코드 그룹 설명',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT '상태 (ACTIVE/INACTIVE)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 시각',
    UNIQUE KEY uq_code_group (dictionary_id, group_name),
    FOREIGN KEY (dictionary_id) REFERENCES catalog_standard_dictionary(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Standard - Code Value (코드 값)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_code_value (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    code_group_id INT NOT NULL COMMENT '소속 코드 그룹 (catalog_code_group(id) 참조, CASCADE 삭제)',
    code_value VARCHAR(100) NOT NULL COMMENT '코드 값 (예: M)',
    code_name VARCHAR(200) NOT NULL COMMENT '코드명 한글 (예: 남성)',
    code_english VARCHAR(200) COMMENT '코드명 영문 (예: Male)',
    description TEXT COMMENT '코드 설명',
    sort_order INT NOT NULL DEFAULT 0 COMMENT '정렬 순서 (오름차순)',
    is_active VARCHAR(5) DEFAULT 'true' COMMENT '활성 여부 (true/false)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE KEY uq_code_value (code_group_id, code_value),
    INDEX idx_code_value_group (code_group_id),
    FOREIGN KEY (code_group_id) REFERENCES catalog_code_group(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Standard - Domain (표준 도메인)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_domain (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dictionary_id INT NOT NULL COMMENT '소속 표준 사전 (catalog_standard_dictionary(id) 참조)',
    domain_name VARCHAR(100) NOT NULL COMMENT '도메인명 (예: 번호, 금액)',
    domain_group VARCHAR(100) COMMENT '도메인 그룹 (예: 문자형, 숫자형)',
    data_type VARCHAR(50) NOT NULL COMMENT '데이터 타입 (예: VARCHAR)',
    data_length INT COMMENT '데이터 길이',
    data_precision INT COMMENT '숫자 정밀도 (전체 자릿수)',
    data_scale INT COMMENT '숫자 스케일 (소수 자릿수)',
    description TEXT COMMENT '도메인 설명',
    code_group_id INT COMMENT '연결 코드 그룹 (catalog_code_group(id) 참조)',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT '상태 (ACTIVE/INACTIVE)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 시각',
    UNIQUE KEY uq_std_domain (dictionary_id, domain_name),
    INDEX idx_std_domain_dict (dictionary_id),
    FOREIGN KEY (dictionary_id) REFERENCES catalog_standard_dictionary(id) ON DELETE CASCADE,
    FOREIGN KEY (code_group_id) REFERENCES catalog_code_group(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Standard - Term (표준 용어)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_term (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dictionary_id INT NOT NULL COMMENT '소속 표준 사전 (catalog_standard_dictionary(id) 참조)',
    term_name VARCHAR(200) NOT NULL COMMENT '용어 한글명 (예: 고객번호)',
    term_english VARCHAR(200) NOT NULL COMMENT '용어 영문명 (예: Customer Number)',
    term_abbr VARCHAR(100) NOT NULL COMMENT '용어 영문약어 (예: CUST_NO)',
    physical_name VARCHAR(100) NOT NULL COMMENT '물리 컬럼명 (예: cust_no)',
    domain_id INT COMMENT '연결된 표준 도메인 (catalog_standard_domain(id) 참조)',
    description TEXT COMMENT '용어 설명',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' COMMENT '용어 상태 (ACTIVE/INACTIVE)',
    created_by VARCHAR(200) COMMENT '생성자',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 시각',
    UNIQUE KEY uq_std_term (dictionary_id, term_name),
    INDEX idx_std_term_dict (dictionary_id),
    INDEX idx_std_term_physical (physical_name),
    FOREIGN KEY (dictionary_id) REFERENCES catalog_standard_dictionary(id) ON DELETE CASCADE,
    FOREIGN KEY (domain_id) REFERENCES catalog_standard_domain(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Standard - Term Words (용어 구성 단어)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_term_words (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    term_id INT NOT NULL COMMENT '표준 용어 ID (catalog_standard_term(id) 참조, 삭제 시 CASCADE)',
    word_id INT NOT NULL COMMENT '구성 단어 ID (catalog_standard_word(id) 참조, 삭제 시 CASCADE)',
    ordinal INT NOT NULL COMMENT '용어 내 단어 배치 순서 (term_id, word_id와 UNIQUE)',
    UNIQUE KEY uq_term_words (term_id, word_id, ordinal),
    INDEX idx_std_term_words_term (term_id),
    FOREIGN KEY (term_id) REFERENCES catalog_standard_term(id) ON DELETE CASCADE,
    FOREIGN KEY (word_id) REFERENCES catalog_standard_word(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Standard - Term-Column Mapping (표준 용어 ↔ 실제 컬럼 매핑)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_term_column_mapping (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    term_id INT NOT NULL COMMENT '표준 용어 ID (catalog_standard_term(id) 참조, CASCADE)',
    dataset_id INT NOT NULL COMMENT '데이터셋 ID (catalog_datasets(id) 참조, CASCADE)',
    schema_id INT NOT NULL COMMENT '데이터셋 컬럼/스키마 ID (catalog_dataset_schemas(id) 참조, CASCADE)',
    mapping_type VARCHAR(20) NOT NULL DEFAULT 'MATCHED' COMMENT '매핑 유형 (MATCHED/SIMILAR/VIOLATION)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE KEY uq_term_col (term_id, schema_id),
    INDEX idx_term_col_mapping_term (term_id),
    INDEX idx_term_col_mapping_dataset (dataset_id),
    FOREIGN KEY (term_id) REFERENCES catalog_standard_term(id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id) REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    FOREIGN KEY (schema_id) REFERENCES catalog_dataset_schemas(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Standard - Change Log (변경 이력)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_change_log (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    entity_type VARCHAR(20) NOT NULL COMMENT '변경 대상 엔티티 유형 (WORD/DOMAIN/TERM/CODE_GROUP/CODE_VALUE)',
    entity_id INT NOT NULL COMMENT '변경 대상 엔티티 ID',
    change_type VARCHAR(20) NOT NULL COMMENT '변경 유형 (CREATE/UPDATE/DELETE)',
    field_name VARCHAR(100) COMMENT '변경된 필드명',
    old_value TEXT COMMENT '변경 전 값',
    new_value TEXT COMMENT '변경 후 값',
    changed_by VARCHAR(200) COMMENT '변경 수행자',
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '변경 시각',
    INDEX idx_std_change_log_entity (entity_type, entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Quality - Profile
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_data_profile (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '대상 데이터셋 (catalog_datasets(id) 참조, CASCADE 삭제)',
    row_count BIGINT NOT NULL DEFAULT 0 COMMENT '프로파일링 시점의 전체 행 수',
    profile_json TEXT NOT NULL COMMENT '컬럼별 통계 결과 JSON 배열 (null 비율·distinct·min/max 등)',
    profiled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '프로파일링 수행 시각',
    INDEX idx_data_profile_dataset (dataset_id),
    FOREIGN KEY (dataset_id) REFERENCES catalog_datasets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Quality - Rule
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_quality_rule (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '검사 대상 데이터셋 (catalog_datasets(id) 참조)',
    rule_name VARCHAR(255) NOT NULL COMMENT '룰 이름',
    check_type VARCHAR(50) NOT NULL COMMENT '검사 유형 (NOT_NULL/UNIQUE/MIN_VALUE/MAX_VALUE/ACCEPTED_VALUES/REGEX/ROW_COUNT/FRESHNESS/CUSTOM_SQL)',
    column_name VARCHAR(256) COMMENT '검사 대상 컬럼명 (테이블 단위 검사면 NULL)',
    expected_value TEXT COMMENT '기대값·검사 설정 (JSON)',
    threshold DECIMAL(5,2) DEFAULT 100.00 COMMENT '통과 임계값 (%)',
    severity VARCHAR(16) NOT NULL DEFAULT 'WARNING' COMMENT '심각도 (WARNING/ERROR 등, 기본 WARNING)',
    is_active VARCHAR(5) NOT NULL DEFAULT 'true' COMMENT '활성화 여부 (true/false)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '수정 시각',
    INDEX idx_quality_rule_dataset (dataset_id),
    FOREIGN KEY (dataset_id) REFERENCES catalog_datasets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Quality - Result
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_quality_result (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    rule_id INT NOT NULL COMMENT '품질 규칙 ID (catalog_quality_rule(id) 참조)',
    dataset_id INT NOT NULL COMMENT '대상 데이터셋 ID (catalog_datasets(id) 참조)',
    passed VARCHAR(5) NOT NULL COMMENT '규칙 통과 여부 (true/false)',
    actual_value TEXT COMMENT '실제 측정값 또는 위반 건수',
    detail TEXT COMMENT '실행 상세 메시지/사유',
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '검사 실행 시각',
    INDEX idx_quality_result_rule (rule_id),
    INDEX idx_quality_result_dataset (dataset_id),
    FOREIGN KEY (rule_id) REFERENCES catalog_quality_rule(id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id) REFERENCES catalog_datasets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Data Quality - Score
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_quality_score (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '대상 데이터셋 (catalog_datasets(id) 참조, 삭제 시 CASCADE)',
    score DECIMAL(5,2) NOT NULL DEFAULT 0 COMMENT '품질 점수 (통과/전체*100, %)',
    total_rules INT NOT NULL DEFAULT 0 COMMENT '평가한 전체 규칙 수',
    passed_rules INT NOT NULL DEFAULT 0 COMMENT '통과한 규칙 수',
    warning_rules INT NOT NULL DEFAULT 0 COMMENT '경고(WARNING) 발생 규칙 수',
    failed_rules INT NOT NULL DEFAULT 0 COMMENT '실패한 규칙 수',
    scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '점수 산정 시각',
    INDEX idx_quality_score_dataset (dataset_id),
    FOREIGN KEY (dataset_id) REFERENCES catalog_datasets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- AI Metadata Generation
-- ---------------------------------------------------------------------------

-- Add PII type column to dataset schemas (idempotent via procedure)
SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'catalog_dataset_schemas' AND COLUMN_NAME = 'pii_type');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE catalog_dataset_schemas ADD COLUMN pii_type VARCHAR(50)', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Logical(display) name 컬럼 — physical name 과 분리된 표시용 라벨.
SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'catalog_datasets' AND COLUMN_NAME = 'display_name');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE catalog_datasets ADD COLUMN display_name VARCHAR(255)', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'catalog_dataset_schemas' AND COLUMN_NAME = 'display_name');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE catalog_dataset_schemas ADD COLUMN display_name VARCHAR(255)', 'SELECT 1');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- AI generation log for audit, preview/apply workflow, and cost tracking
CREATE TABLE IF NOT EXISTS catalog_ai_generation_log (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    entity_type VARCHAR(20) NOT NULL COMMENT '대상 엔티티 유형 (dataset/column/tag/pii)',
    entity_id INT NOT NULL COMMENT '대상 엔티티 ID (dataset_id 또는 schema_field_id)',
    dataset_id INT NOT NULL COMMENT '소속 데이터셋 ID (catalog_datasets(id) 참조, CASCADE 삭제)',
    field_name VARCHAR(500) COMMENT '컬럼 단위 생성 시 대상 컬럼명',
    generation_type VARCHAR(30) NOT NULL COMMENT '생성 유형 (description/tag_suggestion/pii_detection)',
    generated_text TEXT NOT NULL COMMENT 'AI가 생성한 결과 텍스트',
    applied TINYINT(1) DEFAULT 0 COMMENT '생성 결과 실제 적용 여부',
    provider VARCHAR(50) NOT NULL COMMENT 'AI 제공자 (예: openai/anthropic)',
    model VARCHAR(100) NOT NULL COMMENT '사용한 모델명',
    prompt_tokens INT COMMENT '프롬프트 입력 토큰 수',
    completion_tokens INT COMMENT '응답 생성 토큰 수',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    CONSTRAINT fk_ai_gen_log_dataset FOREIGN KEY (dataset_id)
        REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    INDEX idx_ai_gen_log_dataset (dataset_id),
    INDEX idx_ai_gen_log_applied (applied)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- 테이블 한글 설명 (``SHOW CREATE TABLE`` / information_schema 조회 시 노출)
-- ORM 의 docstring/주석과 동일한 의미를 DB 메타에도 남겨 두기 위함.
-- ---------------------------------------------------------------------------

ALTER TABLE catalog_datasources COMMENT = '카탈로그가 연결되는 외부 데이터 플랫폼 레지스트리';
ALTER TABLE catalog_datasource_configurations COMMENT = '플랫폼별 접속·인증·옵션 설정 (JDBC URL, 자격 증명 등)';
ALTER TABLE catalog_datasets COMMENT = '데이터셋 본체 — 플랫폼·이름·소유자·라이프사이클 상태';
ALTER TABLE catalog_dataset_properties COMMENT = '데이터셋 부가 속성(key/value)';
ALTER TABLE catalog_dataset_schemas COMMENT = '데이터셋의 컬럼 스키마 (현재 시점)';
ALTER TABLE catalog_schema_snapshots COMMENT = '스키마 변경 이력 — 시점별 전체 스냅샷';
ALTER TABLE catalog_tags COMMENT = '태그 마스터(이름·색상·설명)';
ALTER TABLE catalog_dataset_tags COMMENT = '데이터셋 ↔ 태그 매핑';
ALTER TABLE catalog_glossary_terms COMMENT = '용어집 항목';
ALTER TABLE catalog_dataset_glossary_terms COMMENT = '데이터셋 ↔ 용어집 매핑';
ALTER TABLE catalog_owners COMMENT = '데이터셋 소유자(사람·팀)';
ALTER TABLE catalog_datasource_data_types COMMENT = '플랫폼별 지원 데이터 타입 메타';
ALTER TABLE catalog_datasource_table_types COMMENT = '플랫폼별 지원 테이블 타입(테이블/뷰/머터리얼라이즈드 등)';
ALTER TABLE catalog_datasource_storage_formats COMMENT = '플랫폼별 지원 저장 포맷(Parquet/ORC 등)';
ALTER TABLE catalog_datasource_features COMMENT = '플랫폼별 지원 기능 플래그';
ALTER TABLE argus_users COMMENT = '로컬 인증 사용자 계정';
ALTER TABLE argus_roles COMMENT = '권한 역할 정의';
ALTER TABLE argus_user_preferences COMMENT = '사용자별 UI 환경 설정(아바타 등) — 토큰 sub 키로 로컬·Keycloak 인증 공용';
ALTER TABLE catalog_registered_models COMMENT = 'MLflow 모델 레지스트리 — 등록 모델';
ALTER TABLE catalog_model_versions COMMENT = 'MLflow 모델 버전 (PENDING_REGISTRATION → READY / FAILED_REGISTRATION)';
-- ---- 보충(A): 기존 ALTER COMMENT 가 참조하는 model/embedding 테이블 (선재 누락 수정) ----
-- 임베딩 컬럼은 pgvector 미지원으로 TEXT fallback.

CREATE TABLE IF NOT EXISTS catalog_dataset_embeddings (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL UNIQUE COMMENT '대상 데이터셋 (catalog_datasets(id) 참조, UNIQUE, CASCADE 삭제)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    embedding TEXT NOT NULL COMMENT '임베딩 벡터 (pgvector, 384차원)',
    source_text TEXT NOT NULL COMMENT '임베딩 생성에 사용된 원본 텍스트 (변경 감지용)',
    model_name VARCHAR(200) NOT NULL COMMENT '임베딩 생성 모델명',
    provider VARCHAR(50) NOT NULL COMMENT '임베딩 제공자 (예: openai/huggingface)',
    dimension INT NOT NULL COMMENT '임베딩 벡터 차원 수 (기본 384)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_entity_embeddings (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    entity_type VARCHAR(30) NOT NULL COMMENT '대상 엔티티 종류 (glossary_term/ai_agent/api)',
    entity_id INT NOT NULL COMMENT '엔티티 식별자 (entity_type별 원본 테이블 PK 참조)',
    embedding TEXT NOT NULL COMMENT 'pgvector 임베딩 벡터 (384차원)',
    source_text TEXT NOT NULL COMMENT '임베딩 생성에 사용된 원본 텍스트 (변경 감지용)',
    model_name VARCHAR(200) NOT NULL COMMENT '임베딩 생성 모델명',
    provider VARCHAR(50) NOT NULL COMMENT '임베딩 제공자 (provider 식별자)',
    dimension INT NOT NULL COMMENT '임베딩 벡터 차원 수 (기본 384)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    UNIQUE (entity_type, entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_model_card (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    model_id INT NOT NULL UNIQUE COMMENT '대상 모델 (catalog_registered_models(id) 참조, 1:1)' REFERENCES catalog_registered_models(id) ON DELETE CASCADE,
    purpose TEXT COMMENT '모델의 용도·목적 설명',
    performance TEXT COMMENT '성능 지표·평가 결과 설명',
    limitations TEXT COMMENT '모델의 한계·주의사항',
    training_data TEXT COMMENT '학습 데이터 출처·구성 설명',
    framework VARCHAR(200) COMMENT '학습/추론 프레임워크명',
    license VARCHAR(200) COMMENT '모델 라이선스',
    contact VARCHAR(200) COMMENT '담당자·문의처',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_model_dataset_lineage (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    model_id INT NOT NULL COMMENT '등록 모델 ID (catalog_registered_models(id) 참조, CASCADE)' REFERENCES catalog_registered_models(id) ON DELETE CASCADE,
    model_version INT COMMENT '연관된 모델 버전 번호',
    dataset_id INT NOT NULL COMMENT '데이터셋 ID (catalog_datasets(id) 참조, CASCADE)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    relation_type VARCHAR(30) NOT NULL COMMENT '연관 유형 (TRAINING_DATA/EVALUATION_DATA/FEATURE_SOURCE)',
    description TEXT COMMENT '리니지 설명',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_model_metrics (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    model_id INT NOT NULL COMMENT '등록 모델 ID (catalog_registered_models(id) 참조, CASCADE)' REFERENCES catalog_registered_models(id) ON DELETE CASCADE,
    version INT NOT NULL COMMENT '모델 버전 번호',
    metric_key VARCHAR(100) NOT NULL COMMENT '지표 이름 (예: accuracy, f1, precision)',
    metric_value DECIMAL(15,6) NOT NULL COMMENT '지표 값 (DECIMAL(15,6))',
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '기록 시각',
    UNIQUE (model_id, version, metric_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE catalog_model_dataset_lineage COMMENT = '모델 ↔ 학습/평가 데이터셋 리니지';
ALTER TABLE catalog_model_metrics COMMENT = '모델 버전별 성능 지표(accuracy, f1 등)';
ALTER TABLE catalog_model_card COMMENT = '모델 카드 — 목적·성능·한계·라이선스 등 거버넌스 문서';
ALTER TABLE catalog_models COMMENT = 'MLflow 산출 메타데이터(predict_fn, sklearn_version 등)';
ALTER TABLE catalog_model_download_log COMMENT = 'MLflow 모델 다운로드 이벤트 로그';
ALTER TABLE catalog_oci_models COMMENT = 'OCI 모델 허브 — 모델 본체 (HuggingFace 스타일)';
ALTER TABLE catalog_oci_model_versions COMMENT = 'OCI 모델 허브 — 버전별 아티팩트 + OCI manifest';
ALTER TABLE catalog_oci_model_tags COMMENT = 'OCI 모델 ↔ 태그 매핑 (catalog_tags 재사용)';
ALTER TABLE catalog_oci_model_lineage COMMENT = 'OCI 모델 — 학습 데이터·부모 모델 등 외부 자원 관계';
ALTER TABLE catalog_oci_model_download_log COMMENT = 'OCI 모델 다운로드 이벤트 로그';
ALTER TABLE catalog_comments COMMENT = '댓글 — 데이터셋·모델 등 엔티티에 공통으로 달리는 본문';
ALTER TABLE catalog_configuration COMMENT = '시스템 전역 설정(key/value)';
ALTER TABLE argus_collector_hive_query_history COMMENT = '외부 Hive 엔진 쿼리 히스토리 (collector 가 적재)';
ALTER TABLE argus_collector_impala_query_history COMMENT = '외부 Impala 엔진 쿼리 히스토리 (collector 가 적재)';
ALTER TABLE argus_collector_trino_query_history COMMENT = '외부 Trino 엔진 쿼리 히스토리 (collector 가 적재)';
ALTER TABLE argus_collector_starrocks_query_history COMMENT = '외부 StarRocks 엔진 쿼리 히스토리 (collector 가 적재)';
ALTER TABLE argus_query_lineage COMMENT = '쿼리 단위 source → target 테이블 매핑';
ALTER TABLE argus_column_lineage COMMENT = '쿼리 단위 source → target 컬럼 매핑';
ALTER TABLE argus_data_pipeline COMMENT = 'ETL/CDC/파일-export 등 외부 파이프라인 레지스트리';
ALTER TABLE argus_dataset_lineage COMMENT = '집계된 데이터셋 ↔ 데이터셋 관계 (lineage)';
ALTER TABLE argus_dataset_column_mapping COMMENT = '크로스 플랫폼 컬럼-레벨 lineage';
ALTER TABLE argus_alert_rule COMMENT = '알림 규칙 — 감시 대상 + 트리거 조건 + 알림 채널 설정';
ALTER TABLE argus_lineage_alert COMMENT = '스키마 변경 영향 알림';
ALTER TABLE argus_alert_notification COMMENT = '알림 전달 기록 (IN_APP / WEBHOOK / EMAIL)';
ALTER TABLE catalog_standard_dictionary COMMENT = '표준 사전 — 단어·도메인·코드·용어를 묶는 상위 그룹';
ALTER TABLE catalog_standard_word COMMENT = '표준 단어 (영문/한글 명칭·약어)';
ALTER TABLE catalog_code_group COMMENT = '코드 그룹 (예: 거래 상태)';
ALTER TABLE catalog_code_value COMMENT = '코드 값 (예: PENDING, COMPLETED)';
ALTER TABLE catalog_standard_domain COMMENT = '표준 도메인 — 논리 데이터 타입';
ALTER TABLE catalog_standard_term COMMENT = '표준 용어 — 단어 조합으로 만든 컬럼명 후보';
ALTER TABLE catalog_standard_term_words COMMENT = '표준 용어 ↔ 구성 단어 매핑';
ALTER TABLE catalog_term_column_mapping COMMENT = '표준 용어 ↔ 실제 데이터셋 컬럼 매핑';
ALTER TABLE catalog_standard_change_log COMMENT = '표준 단어/도메인/용어/코드의 변경 이력';
ALTER TABLE catalog_data_profile COMMENT = '데이터 프로파일링 결과 (Method A/B 산출 통계)';
ALTER TABLE catalog_quality_rule COMMENT = '데이터 품질 규칙';
ALTER TABLE catalog_quality_result COMMENT = '품질 검사 실행 결과';
ALTER TABLE catalog_quality_score COMMENT = '데이터셋별 품질 점수';
ALTER TABLE catalog_dataset_embeddings COMMENT = '데이터셋 메타데이터 임베딩 (시맨틱 검색용)';
ALTER TABLE catalog_ai_generation_log COMMENT = 'AI 생성 호출 이력 (설명/태그/PII 자동 생성)';


-- ============================================================================
-- AI Agent 카탈로그 (catalog_ai_agents 및 서브리소스)
-- 설계: design/ai-agent-catalog.md (통합 메타데이터 모델 G1~G12)
-- ============================================================================

CREATE TABLE IF NOT EXISTS catalog_ai_agents (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    name VARCHAR(255) NOT NULL UNIQUE COMMENT '전사 고유 머신 식별자 (예: cs.payment-refund-assistant)',
    urn VARCHAR(500) NOT NULL UNIQUE COMMENT 'URN 형식 {name}.{ENV}.agent',
    display_name VARCHAR(255) COMMENT 'UI 노출명',
    description TEXT COMMENT '에이전트 설명 (LLM 자동생성 + 휴먼 승인)',
    version VARCHAR(50) NOT NULL DEFAULT '0.1.0' COMMENT '사양 버전 (SemVer 문자열)',
    status VARCHAR(20) NOT NULL DEFAULT 'draft' COMMENT '라이프사이클 상태 (draft/staging/active/blocked/deprecated/retired)',
    owner_email VARCHAR(200) COMMENT '소유자 이메일',
    department VARCHAR(200) COMMENT '소속 부서',
    category VARCHAR(100) COMMENT '분류 (데이터분석/고객지원/코드생성 등)',
    base_model VARCHAR(255) COMMENT '베이스 모델명 (자유 문자열)',
    base_model_ref INT COMMENT '사내 등록 모델 참조 catalog_registered_models(id)' REFERENCES catalog_registered_models(id) ON DELETE SET NULL,
    model_provider VARCHAR(100) COMMENT '모델 제공자',
    framework VARCHAR(100) COMMENT '구현 프레임워크',
    execution_policy VARCHAR(50) COMMENT '실행 정책 (ReAct/Plan-Execute/Sequential/Reflection)',
    max_steps INT COMMENT '최대 실행 스텝 수',
    memory_type VARCHAR(30) COMMENT '메모리 유형 (stateless/short-term/long-term)',
    is_multi_agent BOOLEAN NOT NULL DEFAULT FALSE COMMENT '멀티 에이전트 여부',
    endpoint VARCHAR(1000) COMMENT '호출 엔드포인트 URL',
    protocol VARCHAR(30) COMMENT '호출 프로토콜 (REST/MCP/A2A)',
    invocation_method VARCHAR(30) COMMENT '호출 방식 (sync/async/streaming)',
    auth_method VARCHAR(50) COMMENT '인증 방식 (API key/OAuth2.1(PKCE)/SSO)',
    pii_handling VARCHAR(30) COMMENT 'PII 처리 정책',
    data_residency VARCHAR(100) COMMENT '데이터 거주지(리전)',
    budget_limit DECIMAL(12, 4) COMMENT '예산 한도',
    hitl_required BOOLEAN NOT NULL DEFAULT FALSE COMMENT '휴먼 개입(HITL) 필수 여부',
    audit_log_ref VARCHAR(500) COMMENT '감사 로그 참조 위치',
    latency_p50 INT COMMENT '응답 지연 p50 (ms, 집계 캐시)',
    latency_p95 INT COMMENT '응답 지연 p95 (ms, 집계 캐시)',
    error_rate DECIMAL(5, 4) COMMENT '에러율 (0~1, 집계 캐시)',
    avg_token_usage INT COMMENT '평균 토큰 사용량 (집계 캐시)',
    cost_per_call DECIMAL(12, 6) COMMENT '호출당 평균 비용 (집계 캐시)',
    reputation_score DECIMAL(5, 2) COMMENT '종합 신뢰등급 점수 (파생값)',
    capabilities JSON COMMENT '능력 목록 (JSON list[str])',
    input_schema JSON COMMENT '입력 계약 (JSON Schema)',
    output_schema JSON COMMENT '출력 계약 (JSON Schema)',
    supported_languages JSON COMMENT '지원 언어 목록 (JSON list, 예: [ko,en])',
    use_cases JSON COMMENT '활용 사례 목록 (JSON list[str])',
    limitations JSON COMMENT '제약/한계 목록 (JSON list[str])',
    inference_params JSON COMMENT '추론 파라미터 (JSON dict, temperature/top_p 등)',
    guardrails JSON COMMENT '가드레일 정책 (JSON dict, 입출력 필터/금지 행위)',
    rag_config JSON COMMENT 'RAG 설정 (JSON dict, vectorstore/retriever/top_k)',
    network_allowlist JSON COMMENT 'egress 허용 도메인 목록 (JSON list[str])',
    dlp_policies JSON COMMENT 'DLP 정책 목록 (JSON list[str])',
    hitl_config JSON COMMENT 'HITL 정책 설정 (JSON dict, 중요 액션 사람 서명)',
    sub_agents JSON COMMENT '하위 에이전트 구성 (JSON list[dict])',
    tags JSON COMMENT '태그 목록 (JSON list[str])',
    usage_count INT NOT NULL DEFAULT 0 COMMENT '누적 호출 횟수',
    last_invoked_at TIMESTAMP NULL COMMENT '최근 호출 시각',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    created_by VARCHAR(200) COMMENT '생성자',
    updated_by VARCHAR(200) COMMENT '수정자'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_ai_agent_versions (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    agent_id INT NOT NULL COMMENT '소속 에이전트 (catalog_ai_agents(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    version VARCHAR(50) NOT NULL COMMENT '사양 버전 (SemVer 문자열, agent_id와 함께 UNIQUE)',
    source VARCHAR(1000) COMMENT '사양 원본 위치 (Git repo·아티팩트 경로 등)',
    system_prompt TEXT COMMENT '해당 버전의 system_prompt 스냅샷',
    changelog TEXT COMMENT '버전 변경 사유·내역',
    status VARCHAR(30) NOT NULL DEFAULT 'active' COMMENT '버전 상태 (기본 active)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    created_by VARCHAR(200) COMMENT '생성자 (사용자 식별자/이메일)',
    UNIQUE (agent_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_ai_agent_tools (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    agent_id INT NOT NULL COMMENT '소속 에이전트 (catalog_ai_agents(id) 참조, CASCADE 삭제)' REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL COMMENT '도구 이름 (에이전트 내 함수/툴 식별자)',
    description TEXT COMMENT '도구 설명 (LLM이 호출 판단에 사용하는 용도 설명)',
    tool_schema JSON COMMENT '함수 호출 파라미터 스키마 (JSON Schema)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_ai_agent_mcp_servers (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    agent_id INT NOT NULL COMMENT '소속 에이전트 (catalog_ai_agents(id) 참조, 삭제 시 CASCADE)' REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL COMMENT 'MCP 서버 이름',
    url VARCHAR(1000) COMMENT 'MCP 서버 접속 URL',
    auth_method VARCHAR(50) COMMENT '인증 방식 (API key/OAuth2.1/none)',
    description TEXT COMMENT 'MCP 서버 설명',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_ai_agent_lineage (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    agent_id INT NOT NULL COMMENT '대상 에이전트 (catalog_ai_agents(id) 참조, CASCADE)' REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    target_type VARCHAR(20) NOT NULL DEFAULT 'agent' COMMENT '의존 대상 종류 (agent/model/dataset)',
    target_ref VARCHAR(500) NOT NULL COMMENT '의존 대상 식별자 (name 또는 URN, 외부 대상 허용)',
    relation VARCHAR(20) NOT NULL DEFAULT 'depends_on' COMMENT '관계 종류 (depends_on/consumed_by/related)',
    description TEXT COMMENT '관계 설명',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---- AI Agent 평가/미터링 (Phase 2) ----

CREATE TABLE IF NOT EXISTS catalog_ai_agent_evals (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    agent_id INT NOT NULL COMMENT '평가 대상 에이전트 (catalog_ai_agents(id) 참조, CASCADE)' REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    version VARCHAR(50) COMMENT '평가 대상 사양 버전 (SemVer)',
    eval_type VARCHAR(50) NOT NULL COMMENT '평가 유형 (accuracy/task_success/hallucination/safety/user_rating)',
    metric_key VARCHAR(100) NOT NULL COMMENT '지표 키 (측정 항목 식별자)',
    metric_value DECIMAL(10, 4) NOT NULL COMMENT '지표 값 (0~1 정규화 권장)',
    dataset_ref VARCHAR(500) COMMENT '평가 데이터셋/케이스 참조',
    passed BOOLEAN COMMENT '합격 여부',
    notes TEXT COMMENT '평가 비고',
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '평가 시각',
    created_by VARCHAR(200) COMMENT '평가 등록자',
    INDEX idx_ai_agent_evals_agent (agent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_ai_agent_invocation_log (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    agent_id INT NOT NULL COMMENT '호출된 에이전트 (catalog_ai_agents(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    invoked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '호출 시각',
    consumer VARCHAR(200) COMMENT '호출 주체 (사용자/팀/에이전트 식별자)',
    status VARCHAR(20) NOT NULL DEFAULT 'success' COMMENT '호출 결과 상태 (success/error)',
    error_type VARCHAR(100) COMMENT '실패 시 오류 유형',
    latency_ms INT COMMENT '응답 지연(ms)',
    input_tokens INT COMMENT '입력 토큰 수',
    output_tokens INT COMMENT '출력 토큰 수',
    cost DECIMAL(12, 6) COMMENT '호출 비용',
    session_id VARCHAR(200) COMMENT '호출 세션 식별자',
    INDEX idx_ai_agent_invlog_agent (agent_id),
    INDEX idx_ai_agent_invlog_time (invoked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---- AI Agent 집행 훅 이벤트 (Phase 3 연동 인터페이스) ----

CREATE TABLE IF NOT EXISTS catalog_ai_agent_hook_events (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    agent_id INT NOT NULL COMMENT '대상 AI 에이전트 (catalog_ai_agents(id) 참조, CASCADE)' REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '훅 이벤트 발생 시각',
    stage VARCHAR(20) NOT NULL COMMENT '집행 단계 (access/pre_exec/post_exec)',
    decision VARCHAR(30) NOT NULL COMMENT '집행 결정 (allow/deny/mask/require_approval/approved/modified)',
    action_type VARCHAR(50) COMMENT '행위 유형 (tool_call/network_egress/data_write/budget_spend/browse 등)',
    target VARCHAR(500) COMMENT '집행 대상 (도메인/도구명/데이터셋 등)',
    policy_ref VARCHAR(100) COMMENT '발동된 정책 키 (network_allowlist/dlp_policies/hitl_config/guardrails 등)',
    reason TEXT COMMENT '결정 사유 설명',
    session_id VARCHAR(200) COMMENT '세션 식별자',
    consumer VARCHAR(200) COMMENT '요청 소비자(호출 주체) 식별자',
    event_metadata JSON COMMENT '이벤트 부가 메타데이터 (JSONB)',
    INDEX idx_ai_agent_hook_agent (agent_id),
    INDEX idx_ai_agent_hook_time (occurred_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ===========================================================================
-- 보충(B) — ORM 모델 대비 누락분 (API 카탈로그 / 변경관리 / 조직·시스템·분류)
-- 라이브 스키마(Base.metadata.create_all) 기준.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS argus_change_request (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    cr_code VARCHAR(32) NOT NULL UNIQUE COMMENT '변경 요청 코드 (예: CR-2026-0001, 고유)',
    title VARCHAR(500) NOT NULL COMMENT '변경 요청 제목',
    description TEXT COMMENT '변경 요청 상세 설명',
    dataset_id INT NOT NULL COMMENT '대상 데이터셋 (catalog_datasets(id) 참조)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    change_type VARCHAR(32) NOT NULL COMMENT '변경 유형 (BREAKING/NON_BREAKING/ADDITIVE/COSMETIC)',
    priority VARCHAR(16) NOT NULL COMMENT '우선순위 (EMERGENCY/HIGH/NORMAL/LOW)',
    status VARCHAR(24) NOT NULL COMMENT '결재 상태 (기본 DRAFT)',
    schema_before TEXT COMMENT '변경 전 스키마 (JSON)',
    schema_after TEXT COMMENT '변경 후 스키마 (JSON, COSMETIC은 생략 가능)',
    impact_report TEXT COMMENT '자동 생성된 영향 분석 결과 (JSON)',
    rollback_plan TEXT NOT NULL COMMENT '롤백 계획 (필수)',
    business_justification TEXT NOT NULL COMMENT '비즈니스 정당성 근거 (필수)',
    scheduled_at TIMESTAMP COMMENT '적용 예정 시각',
    applied_at TIMESTAMP COMMENT '실제 적용 시각',
    workflow_id VARCHAR(200) COMMENT 'Temporal 워크플로우 ID',
    requested_by VARCHAR(200) NOT NULL COMMENT '변경 요청자',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS argus_change_approval_step (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    cr_id INT NOT NULL COMMENT '변경 요청 ID (argus_change_request(id) 참조, CASCADE 삭제)' REFERENCES argus_change_request(id) ON DELETE CASCADE,
    step_order INT NOT NULL COMMENT '결재 단계 순서 (1부터 시작)',
    approver VARCHAR(200) NOT NULL COMMENT '결재자 식별자(사용자/이메일)',
    role VARCHAR(64) COMMENT '결재자 역할 (OWNER/DOMAIN_LEAD/DG_COMMITTEE 등)',
    decision VARCHAR(16) COMMENT '결재 결과 (APPROVED/REJECTED/DELEGATED/PENDING)',
    comment TEXT COMMENT '결재 의견/사유',
    decided_at TIMESTAMP COMMENT '결재 처리 시각',
    delegated_to VARCHAR(200) COMMENT '위임 결재 시 대결자 식별자',
    due_at TIMESTAMP COMMENT '결재 처리 기한',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE (cr_id, step_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS argus_change_consumer (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '소비 대상 데이터셋 (catalog_datasets(id) 참조)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    consumer_name VARCHAR(200) NOT NULL COMMENT '소비자 시스템 또는 조직 이름',
    consumer_type VARCHAR(32) NOT NULL COMMENT '소비자 유형 (SYSTEM/ORGANIZATION/TEAM)',
    `usage` VARCHAR(64) COMMENT '사용 용도 (ETL/DASHBOARD/ML_TRAINING/REGULATORY 등)',
    criticality VARCHAR(16) NOT NULL COMMENT '중요도 (MISSION_CRITICAL/IMPORTANT/NORMAL)',
    contact_emails VARCHAR(2000) COMMENT '연락 이메일 목록 (콤마 구분)',
    webhook_url VARCHAR(500) COMMENT '통지용 Webhook URL',
    slack_channel VARCHAR(200) COMMENT '통지용 Slack 채널',
    auto_detected BOOLEAN NOT NULL COMMENT '쿼리 로그 기반 자동 탐지 등록 여부',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    UNIQUE (dataset_id, consumer_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS argus_change_referrer (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    cr_id INT NOT NULL COMMENT '변경 요청 ID (argus_change_request(id) 참조, CASCADE)' REFERENCES argus_change_request(id) ON DELETE CASCADE,
    name VARCHAR(200) COMMENT '참조자 표시명 (선택)',
    email VARCHAR(300) COMMENT '참조자 이메일 (EMAIL 채널 대상)',
    channel VARCHAR(16) NOT NULL COMMENT '통지 채널 (EMAIL/SLACK/MATTERMOST)',
    slack_target VARCHAR(200) COMMENT 'Slack/Mattermost 채널 또는 멘션 대상 (선택)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_argus_change_referrer_cr_id ON argus_change_referrer (cr_id);

CREATE TABLE IF NOT EXISTS argus_change_notification_log (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    cr_id INT NOT NULL COMMENT '변경 요청 ID (argus_change_request(id) 참조, CASCADE)' REFERENCES argus_change_request(id) ON DELETE CASCADE,
    consumer_id INT COMMENT '통지 대상 소비자 ID (argus_change_consumer(id) 참조, nullable)' REFERENCES argus_change_consumer(id) ON DELETE CASCADE,
    referrer_id INT COMMENT '통지 대상 참조자(CC) ID (argus_change_referrer(id) 참조, nullable)' REFERENCES argus_change_referrer(id) ON DELETE CASCADE,
    recipient VARCHAR(300) COMMENT '수신자 (이메일/채널 대상)',
    channel VARCHAR(32) NOT NULL COMMENT '통지 채널 (EMAIL/SLACK/MATTERMOST/WEBHOOK/IN_APP)',
    stage VARCHAR(16) NOT NULL COMMENT '통지 단계 (SUBMITTED/T_MINUS_30/T_MINUS_7/APPLIED 등)',
    status VARCHAR(16) NOT NULL COMMENT '통지 상태 (PENDING/SENT/DELIVERED/ACKED/FAILED/REJECTED/DEFERRED)',
    sent_at TIMESTAMP COMMENT '발송 시각',
    acked_at TIMESTAMP COMMENT '수신 확인(ACK) 시각',
    ack_comment TEXT COMMENT '수신 확인 코멘트',
    error TEXT COMMENT '발송 실패 오류 메시지',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS argus_permissions (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    kind VARCHAR(10) NOT NULL COMMENT '권한 유형 (MENU/FEATURE)',
    perm_key VARCHAR(100) NOT NULL COMMENT '메뉴/기능 key (프런트 레지스트리와 일치)',
    role_id VARCHAR(50) NOT NULL COMMENT '허용 대상 역할 ID (argus-superuser/argus-user)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE (kind, perm_key, role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_ai_agent_status_history (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    agent_id INT NOT NULL COMMENT '대상 에이전트 (catalog_ai_agents(id) 참조, 삭제 시 CASCADE)' REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    from_status VARCHAR(20) COMMENT '변경 전 상태 (draft/staging/active/blocked/deprecated/retired)',
    to_status VARCHAR(20) NOT NULL COMMENT '변경 후 상태 (draft/staging/active/blocked/deprecated/retired)',
    note TEXT COMMENT '상태 변경 사유·메모',
    changed_by VARCHAR(200) COMMENT '상태를 변경한 사용자',
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '상태 변경 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_ai_agent_status_history_agent_id ON catalog_ai_agent_status_history (agent_id);
CREATE INDEX IF NOT EXISTS ix_catalog_ai_agent_status_history_changed_at ON catalog_ai_agent_status_history (changed_at);

CREATE TABLE IF NOT EXISTS catalog_apis (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    name VARCHAR(255) NOT NULL UNIQUE COMMENT 'API 고유 이름 (unique)',
    urn VARCHAR(500) NOT NULL UNIQUE COMMENT 'API 고유 식별자 URN (unique)',
    display_name VARCHAR(255) COMMENT '화면 표시명',
    description TEXT COMMENT 'API 설명',
    version VARCHAR(50) NOT NULL COMMENT 'API 버전 (기본 1.0.0)',
    status VARCHAR(20) NOT NULL COMMENT '상태 (draft/published/deprecated/retired)',
    owner_email VARCHAR(200) COMMENT '소유자 이메일',
    department VARCHAR(200) COMMENT '담당 부서',
    category VARCHAR(100) COMMENT '분류 카테고리',
    protocol VARCHAR(30) COMMENT '프로토콜 (REST/GraphQL/gRPC/AsyncAPI)',
    source VARCHAR(10) NOT NULL COMMENT '엔드포인트 출처 (spec=스펙 파싱/manual=수동 등록)',
    spec_format VARCHAR(20) COMMENT '스펙 형식 (openapi2/openapi3/asyncapi 등)',
    base_url VARCHAR(1000) COMMENT '대표 서버 기본 base URL',
    base_url_overridden VARCHAR(5) COMMENT 'Base URL 수동 지정 여부 (true 면 스펙이 덮어쓰지 않음)',
    contract_text TEXT COMMENT '계약/스키마 문서 원문 (SDL/WSDL/.proto/AsyncAPI 등)',
    contract_url VARCHAR(1000) COMMENT '계약/스키마 문서 URL',
    certification VARCHAR(20) COMMENT '인증 상태',
    tier VARCHAR(20) COMMENT '등급 tier',
    tags JSON COMMENT '태그 목록 (list[str])',
    note TEXT COMMENT '비고/메모',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    created_by VARCHAR(200) COMMENT '생성자',
    updated_by VARCHAR(200) COMMENT '수정자'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_api_alerts (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    api_id INT NOT NULL COMMENT '대상 API (catalog_apis(id) 참조, CASCADE)' REFERENCES catalog_apis(id) ON DELETE CASCADE,
    from_spec_id INT COMMENT '변경 전 current 스펙 ID',
    to_spec_id INT COMMENT '새로 업로드된 스펙 ID',
    from_version VARCHAR(50) COMMENT '변경 전 스펙 버전',
    to_version VARCHAR(50) COMMENT '변경 후 스펙 버전',
    severity VARCHAR(16) NOT NULL COMMENT '심각도 (BREAKING)',
    breaking_count INT NOT NULL COMMENT '감지된 Breaking 변경 건수',
    summary VARCHAR(500) NOT NULL COMMENT '변경 요약 (한 줄)',
    detail TEXT COMMENT '변경 상세 (JSON: removed/changed)',
    status VARCHAR(20) NOT NULL COMMENT '처리 상태 (OPEN/ACKNOWLEDGED)',
    created_by VARCHAR(200) COMMENT '알림 생성자',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    acknowledged_by VARCHAR(200) COMMENT '확인 처리자',
    acknowledged_at TIMESTAMP COMMENT '확인 처리 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_api_alerts_api_id ON catalog_api_alerts (api_id);
CREATE INDEX IF NOT EXISTS ix_catalog_api_alerts_created_at ON catalog_api_alerts (created_at);

CREATE TABLE IF NOT EXISTS catalog_api_credentials (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    api_id INT NOT NULL COMMENT '대상 API (catalog_apis(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_apis(id) ON DELETE CASCADE,
    scheme_name VARCHAR(100) COMMENT '연결된 보안 스킴 키 (있으면)',
    label VARCHAR(200) NOT NULL COMMENT '자격증명 표시 이름',
    type VARCHAR(30) NOT NULL COMMENT '인증 방식 (apiKey/bearer/basic/oauth2)',
    secret TEXT NOT NULL COMMENT '암호화된 시크릿 JSON (Fernet)',
    created_by VARCHAR(200) COMMENT '생성자',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_api_credentials_api_id ON catalog_api_credentials (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_endpoints (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    api_id INT NOT NULL COMMENT '소속 API (catalog_apis(id) 참조, CASCADE 삭제)' REFERENCES catalog_apis(id) ON DELETE CASCADE,
    method VARCHAR(40) NOT NULL COMMENT '오퍼레이션 유형 (REST: GET/POST 등, GraphQL: query/mutation, gRPC: unary 등)',
    path VARCHAR(1000) NOT NULL COMMENT '오퍼레이션 식별자 (REST: 경로, GraphQL: 이름, gRPC: Service.Method)',
    operation_id VARCHAR(255) COMMENT '오퍼레이션 고유 ID (operationId)',
    summary TEXT COMMENT '오퍼레이션 요약',
    description TEXT COMMENT '오퍼레이션 상세 설명',
    tags JSON COMMENT '분류 태그 목록 (list[str], JSON)',
    parameters JSON COMMENT '인자/파라미터 정의 목록 (list[dict], JSON)',
    request_body JSON COMMENT '요청/입력 스키마 (dict, JSON)',
    responses JSON COMMENT '응답/출력 정의 (dict, JSON)',
    security JSON COMMENT '보안 요구 사항 목록 (list, JSON)',
    extra JSON COMMENT '프로토콜별 추가 속성 (soap_action/grpc 메시지/graphql 반환타입 등, JSON)',
    sort_order INT COMMENT '표시 정렬 순서'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_api_endpoints_api_id ON catalog_api_endpoints (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_favorites (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    user_key VARCHAR(200) NOT NULL COMMENT '즐겨찾기 소유 사용자 식별자 (username 또는 email)',
    api_id INT NOT NULL COMMENT '대상 API (catalog_apis(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_apis(id) ON DELETE CASCADE,
    method VARCHAR(10) NOT NULL COMMENT '엔드포인트 HTTP 메서드 (GET/POST/PUT/DELETE 등)',
    path VARCHAR(2000) NOT NULL COMMENT '엔드포인트 경로',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE (user_key, api_id, method, path)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_api_favorites_api_id ON catalog_api_favorites (api_id);
CREATE INDEX IF NOT EXISTS ix_catalog_api_favorites_user_key ON catalog_api_favorites (user_key);

CREATE TABLE IF NOT EXISTS catalog_api_invocations (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    api_id INT NOT NULL COMMENT '호출된 API (catalog_apis(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_apis(id) ON DELETE CASCADE,
    method VARCHAR(10) NOT NULL COMMENT 'HTTP 메서드 (GET/POST/PUT/DELETE 등)',
    url VARCHAR(2000) NOT NULL COMMENT '실제 호출된 요청 URL',
    status_code INT NOT NULL COMMENT 'HTTP 응답 상태 코드 (0=네트워크/예외)',
    ok VARCHAR(5) NOT NULL COMMENT '성공 여부 문자열 (2xx/3xx면 true)',
    latency_ms INT NOT NULL COMMENT '응답 지연(ms)',
    error TEXT COMMENT '오류 메시지 (실패 시)',
    called_by VARCHAR(200) COMMENT '호출 사용자 (username 또는 email)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    endpoint_method VARCHAR(10) COMMENT '엔드포인트 템플릿 메서드',
    endpoint_path VARCHAR(2000) COMMENT '엔드포인트 템플릿 경로 (파라미터 치환 전)',
    request_input JSON COMMENT '입력 파라미터 이력 (path/query/headers(마스킹)/body JSON)'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_api_invocations_api_id ON catalog_api_invocations (api_id);
CREATE INDEX IF NOT EXISTS ix_catalog_api_invocations_created_at ON catalog_api_invocations (created_at);

CREATE TABLE IF NOT EXISTS catalog_api_lineage (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    api_id INT NOT NULL COMMENT '대상 API (catalog_apis(id) 참조, CASCADE)' REFERENCES catalog_apis(id) ON DELETE CASCADE,
    relation VARCHAR(20) NOT NULL COMMENT '관계 유형 (provides/consumes/depends_on)',
    target_type VARCHAR(20) NOT NULL COMMENT '대상 종류 (api/dataset/model/agent/system)',
    target_ref VARCHAR(300) NOT NULL COMMENT '대상 식별자 (이름/URN/외부 참조)',
    target_label VARCHAR(300) COMMENT '대상 표시명 (선택)',
    note TEXT COMMENT '부가 설명 메모',
    created_by VARCHAR(200) COMMENT '생성자',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE (api_id, relation, target_type, target_ref)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_api_lineage_api_id ON catalog_api_lineage (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_security_schemes (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    api_id INT NOT NULL COMMENT '소속 API (catalog_apis(id) 참조, CASCADE)' REFERENCES catalog_apis(id) ON DELETE CASCADE,
    scheme_name VARCHAR(100) NOT NULL COMMENT '스펙상 보안 스킴 키 이름',
    type VARCHAR(30) COMMENT '인증 방식 (apiKey/oauth2/http/openIdConnect/mutualTLS)',
    config JSON COMMENT '스킴 상세 설정 JSON (in/name/scheme/flows 등)'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_api_security_schemes_api_id ON catalog_api_security_schemes (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_servers (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    api_id INT NOT NULL COMMENT '소속 API (catalog_apis(id) 참조, CASCADE)' REFERENCES catalog_apis(id) ON DELETE CASCADE,
    url VARCHAR(1000) NOT NULL COMMENT '서버 base URL',
    description VARCHAR(500) COMMENT '서버 설명',
    env VARCHAR(20) COMMENT '배포 환경 (PROD/STAGING/DEV, 선택)'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_api_servers_api_id ON catalog_api_servers (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_specs (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    api_id INT NOT NULL COMMENT '소속 API (catalog_apis(id) 참조, CASCADE 삭제)' REFERENCES catalog_apis(id) ON DELETE CASCADE,
    version VARCHAR(50) NOT NULL COMMENT '스펙 버전 (api_id별 UNIQUE)',
    format VARCHAR(20) COMMENT '스펙 포맷 (openapi2/openapi3 등)',
    raw TEXT COMMENT '원본 스펙 텍스트 (JSON/YAML 정규화 JSON)',
    parsed JSON COMMENT '파싱 요약 (info/servers/securitySchemes 등)',
    source_url VARCHAR(1000) COMMENT '스펙 원본 URL',
    is_current VARCHAR(5) NOT NULL DEFAULT 'true' COMMENT '현재 버전 여부 (문자열 ''true''/''false'')',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    created_by VARCHAR(200) COMMENT '생성자',
    UNIQUE (api_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_api_specs_api_id ON catalog_api_specs (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_status_history (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    api_id INT NOT NULL COMMENT '대상 API (catalog_apis(id) 참조, CASCADE 삭제)' REFERENCES catalog_apis(id) ON DELETE CASCADE,
    from_status VARCHAR(20) COMMENT '변경 전 상태 (draft/published/deprecated/retired)',
    to_status VARCHAR(20) NOT NULL COMMENT '변경 후 상태 (draft/published/deprecated/retired)',
    note TEXT COMMENT '변경 사유·비고 메모',
    changed_by VARCHAR(200) COMMENT '상태를 변경한 사용자',
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '상태 변경 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_api_status_history_changed_at ON catalog_api_status_history (changed_at);
CREATE INDEX IF NOT EXISTS ix_catalog_api_status_history_api_id ON catalog_api_status_history (api_id);

CREATE TABLE IF NOT EXISTS catalog_taxonomies (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    code VARCHAR(100) UNIQUE COMMENT '분류체계 slug 코드 (자동 생성, 고유)',
    name VARCHAR(200) NOT NULL UNIQUE COMMENT '분류체계 이름 (예: 업무 도메인, 데이터 등급, 고유)',
    description TEXT COMMENT '분류체계 상세 설명',
    sort_order INT NOT NULL COMMENT '형제 분류체계 정렬 순서',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS catalog_categories (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    taxonomy_id INT NOT NULL COMMENT '소속 분류체계 (catalog_taxonomies(id) 참조, CASCADE)' REFERENCES catalog_taxonomies(id) ON DELETE CASCADE,
    parent_id INT COMMENT '상위 분류 노드 (catalog_categories(id) 참조, NULL=루트)' REFERENCES catalog_categories(id) ON DELETE RESTRICT,
    code VARCHAR(100) COMMENT '분류 코드/slug (선택)',
    name VARCHAR(200) NOT NULL COMMENT '분류 노드 이름',
    description TEXT COMMENT '분류 노드 설명',
    sort_order INT NOT NULL COMMENT '형제 노드 간 정렬 순서',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    UNIQUE (taxonomy_id, parent_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_categories_taxonomy_id ON catalog_categories (taxonomy_id);
CREATE INDEX IF NOT EXISTS ix_catalog_categories_parent_id ON catalog_categories (parent_id);

CREATE TABLE IF NOT EXISTS catalog_dataset_categories (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    dataset_id INT NOT NULL COMMENT '데이터셋 ID (catalog_datasets(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    category_id INT NOT NULL COMMENT '분류 노드 ID (catalog_categories(id) 참조, ON DELETE CASCADE)' REFERENCES catalog_categories(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    UNIQUE (dataset_id, category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_dataset_categories_category_id ON catalog_dataset_categories (category_id);
CREATE INDEX IF NOT EXISTS ix_catalog_dataset_categories_dataset_id ON catalog_dataset_categories (dataset_id);

CREATE TABLE IF NOT EXISTS catalog_dataset_urn_alias (
    old_urn VARCHAR(500) PRIMARY KEY COMMENT 'PK, 과거 포맷의 dataset URN (환경 토큰 포함 등)',
    dataset_id INT NOT NULL COMMENT '매핑 대상 데이터셋, catalog_datasets(id) 참조 (ON DELETE CASCADE)' REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_dataset_urn_alias_dataset_id ON catalog_dataset_urn_alias (dataset_id);

CREATE TABLE IF NOT EXISTS catalog_organizations (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    code VARCHAR(100) UNIQUE COMMENT 'URL/외부참조용 slug (고유, 자동 생성)',
    name VARCHAR(200) NOT NULL COMMENT '조직 이름 (동일 부모 내 중복 불가)',
    parent_id INT COMMENT '상위 조직 ID (catalog_organizations(id) 참조, NULL=루트)' REFERENCES catalog_organizations(id) ON DELETE RESTRICT,
    description TEXT COMMENT '조직 상세 설명',
    sort_order INT NOT NULL COMMENT '같은 부모 내 형제 정렬 순서',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    UNIQUE (parent_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_organizations_parent_id ON catalog_organizations (parent_id);

CREATE TABLE IF NOT EXISTS catalog_systems (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT 'PK',
    code VARCHAR(100) UNIQUE COMMENT '시스템 고유 코드 (전역 unique)',
    name VARCHAR(200) NOT NULL COMMENT '시스템 이름 (org_id 내 unique)',
    org_id INT COMMENT '소속 조직 catalog_organizations(id) 참조 (NULL=미분류)' REFERENCES catalog_organizations(id) ON DELETE RESTRICT,
    summary VARCHAR(200) COMMENT '한 줄 요약',
    description TEXT COMMENT '상세 설명',
    owner VARCHAR(200) COMMENT '담당자',
    status VARCHAR(20) NOT NULL COMMENT '상태 (ACTIVE/INACTIVE/DEPRECATED)',
    sort_order INT NOT NULL COMMENT '표시 정렬 순서',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '수정 시각',
    UNIQUE (org_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE INDEX IF NOT EXISTS ix_catalog_systems_org_id ON catalog_systems (org_id);

-- ---------------------------------------------------------------------------
-- 기존 테이블 누락 컬럼 보충 (reconcile_schema() 가 런타임 보정하던 항목)
-- ---------------------------------------------------------------------------
ALTER TABLE argus_users ADD COLUMN IF NOT EXISTS organization VARCHAR(100);
ALTER TABLE argus_users ADD COLUMN IF NOT EXISTS department VARCHAR(100);
ALTER TABLE catalog_ai_agent_tools ADD COLUMN IF NOT EXISTS risk VARCHAR(20);
ALTER TABLE catalog_ai_agent_tools ADD COLUMN IF NOT EXISTS requires_approval BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE catalog_ai_agents ADD COLUMN IF NOT EXISTS streaming BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE catalog_dataset_schemas ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);
ALTER TABLE catalog_dataset_schemas ADD COLUMN IF NOT EXISTS pii_type VARCHAR(50);
ALTER TABLE catalog_datasources ADD COLUMN IF NOT EXISTS system_id INT REFERENCES catalog_systems(id) ON DELETE SET NULL;
ALTER TABLE catalog_oci_models ADD COLUMN IF NOT EXISTS created_by VARCHAR(200);
ALTER TABLE catalog_quality_result ADD COLUMN IF NOT EXISTS failed_samples TEXT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS created_by VARCHAR(200);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS ingestion_frequency VARCHAR(50);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS ingestion_time VARCHAR(5);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS ingestion_day VARCHAR(10);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS ingestion_timezone VARCHAR(40);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS ingestion_cron VARCHAR(120);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS ingestion_mode VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS update_type VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS freshness_sla VARCHAR(200);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS last_ingested_at TIMESTAMP;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS retention_days INT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS purge_days INT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS data_category VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS data_format VARCHAR(30);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS compression VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS encoding VARCHAR(30);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS row_count BIGINT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS byte_size BIGINT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS file_count INT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS sensitivity VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS contains_pii VARCHAR(5);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS pii_fields TEXT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS compliance_tags VARCHAR(255);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS tier VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS certification VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS steward VARCHAR(255);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS view_count INT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS query_count INT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMP;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS quality_score INT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS quality_status VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS show_quality_score VARCHAR(5);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS note TEXT;

-- 추가 테이블 주석
ALTER TABLE argus_change_approval_step COMMENT = '변경 요청(CR)의 결재 단계별 처리 이력을 저장';
ALTER TABLE argus_change_consumer COMMENT = '데이터셋별 소비자(시스템/조직) 등록부 — 스키마 변경 시 통지 대상 기준';
ALTER TABLE argus_change_notification_log COMMENT = '변경 요청에 대한 다운스트림 소비자·참조자별 통지 발송 및 ACK 기록';
ALTER TABLE argus_change_referrer COMMENT = '변경 요청(CR)의 참조자(CC) — 결재권 없이 통지만 받는 대상';
ALTER TABLE argus_change_request COMMENT = '스키마 변경 요청(CR) 마스터 — 결재 흐름의 중심 엔티티';
ALTER TABLE argus_permissions COMMENT = '역할별 메뉴/기능 허용 권한 매트릭스 (open-by-default 정책)';
ALTER TABLE catalog_ai_agent_evals COMMENT = 'AI 에이전트 평가 결과 (정확도/성공률/환각/안전 등 지표별 측정값)';
ALTER TABLE catalog_ai_agent_hook_events COMMENT = '외부 Execution Plane이 보고한 정책 집행 훅 이벤트 감사 로그(egress 차단/PII 마스킹/HITL 등)';
ALTER TABLE catalog_ai_agent_invocation_log COMMENT = '에이전트 호출 미터링 원장 (호출별 토큰·비용·지연·성공여부 텔레메트리)';
ALTER TABLE catalog_ai_agent_lineage COMMENT = 'AI 에이전트의 에이전트/모델/데이터셋 의존 관계(리니지)를 저장';
ALTER TABLE catalog_ai_agent_mcp_servers COMMENT = 'AI 에이전트가 연결한 MCP(Model Context Protocol) 서버 목록';
ALTER TABLE catalog_ai_agent_status_history COMMENT = 'AI 에이전트의 상태 변경 이력 (자동 기록)';
ALTER TABLE catalog_ai_agent_tools COMMENT = 'AI 에이전트가 자율 판단으로 호출 가능한 도구와 호출 스키마 목록';
ALTER TABLE catalog_ai_agent_versions COMMENT = 'AI 에이전트 사양 버전 이력 (system_prompt 스냅샷·changelog 보관, 롤백/감사용)';
ALTER TABLE catalog_ai_agents COMMENT = '등록된 AI 에이전트 메인 엔티티 (식별/모델/실행/인터페이스/거버넌스/관측 메타데이터)';
ALTER TABLE catalog_api_alerts COMMENT = 'API 스펙 업로드 시 Breaking 변경 감지로 자동 생성되는 알림';
ALTER TABLE catalog_api_credentials COMMENT = 'API 호출용 자격증명 저장 (시크릿은 Fernet 암호화, Try-it 콘솔에서 서버가 복호화해 주입)';
ALTER TABLE catalog_api_endpoints COMMENT = 'API 엔드포인트(오퍼레이션) — 메서드/경로/요청·응답 스키마/보안 정의';
ALTER TABLE catalog_api_favorites COMMENT = '사용자별 API 엔드포인트 즐겨찾기 (method+path 로 식별해 스펙 재업로드에도 유지)';
ALTER TABLE catalog_api_invocations COMMENT = 'Try-it 콘솔(프록시) API 호출 로그 — 사용량 관측·미터링·입력 이력';
ALTER TABLE catalog_api_lineage COMMENT = 'API의 제공/소비/의존 리니지 엣지(관계) 저장';
ALTER TABLE catalog_api_security_schemes COMMENT = 'API의 인증 스킴 정의(OpenAPI securitySchemes) 저장';
ALTER TABLE catalog_api_servers COMMENT = 'API별 서버/환경(base URL) 목록 — OpenAPI servers 항목';
ALTER TABLE catalog_api_specs COMMENT = 'API 스펙 버전 이력 (원본 raw 텍스트 + 파싱 요약 결과 저장)';
ALTER TABLE catalog_api_status_history COMMENT = 'API 상태 전이 이력 — 상태 변경 시 자동 기록';
ALTER TABLE catalog_apis COMMENT = '등록된 API 메인 엔티티 (식별/소유/분류/상태/버전/스펙 출처)';
ALTER TABLE catalog_categories COMMENT = '분류체계(taxonomy) 내부의 분류 노드 — 자기참조 트리 구조';
ALTER TABLE catalog_dataset_categories COMMENT = '데이터셋과 분류 노드의 N:M 매핑 테이블';
ALTER TABLE catalog_dataset_urn_alias COMMENT = '구 URN을 dataset에 매핑하는 별칭 테이블 (URN 포맷 전환기 외부 참조 해소용)';
ALTER TABLE catalog_entity_embeddings COMMENT = '비-데이터셋 카탈로그 엔티티(용어집/AI Agent/API)의 시맨틱 검색용 벡터 임베딩 저장';
ALTER TABLE catalog_organizations COMMENT = '조직 계층(부서/팀) 트리를 저장하는 마스터 테이블';
ALTER TABLE catalog_systems COMMENT = '조직이 운영하는 데이터 소스 묶음(시스템/앱/서비스 배포) 정의';
ALTER TABLE catalog_taxonomies COMMENT = '분류체계(scheme) — 이름 붙은 분류 트리의 루트 정의';
