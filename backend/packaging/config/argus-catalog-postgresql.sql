-- Argus Catalog Server - PostgreSQL DDL
-- Auto-generated from database schema

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Datasource Registry
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    logo_url VARCHAR(500),
    created_at TIMESTAMPTZ DEFAULT now(),
    datasource_id VARCHAR(36) NOT NULL UNIQUE,
    type VARCHAR(100) NOT NULL,
    -- 환경 구분 (DEV/STAGING/PROD). 생성 후 불변.
    origin VARCHAR(20) NOT NULL DEFAULT 'DEV'
);

-- ---------------------------------------------------------------------------
-- Datasource Configuration
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasource_configurations (
    id SERIAL PRIMARY KEY,
    datasource_id INT NOT NULL UNIQUE REFERENCES catalog_datasources(id) ON DELETE CASCADE,
    config_json TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Dataset
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasets (
    id SERIAL PRIMARY KEY,
    urn VARCHAR(500) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    datasource_id INT NOT NULL REFERENCES catalog_datasources(id),
    summary VARCHAR(200),
    description TEXT,
    origin VARCHAR(50) NOT NULL,
    qualified_name VARCHAR(500),
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    table_type VARCHAR(100),
    storage_format VARCHAR(100),
    datasource_properties TEXT,
    ddl TEXT,
    is_synced VARCHAR(5) DEFAULT 'false'
);

-- ---------------------------------------------------------------------------
-- Dataset Properties
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_dataset_properties (
    id SERIAL PRIMARY KEY,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    property_key VARCHAR(100) NOT NULL,
    property_value TEXT NOT NULL,
    UNIQUE (dataset_id, property_key)
);

-- ---------------------------------------------------------------------------
-- Dataset Schema
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_dataset_schemas (
    id SERIAL PRIMARY KEY,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    field_path VARCHAR(500) NOT NULL,
    field_type VARCHAR(100) NOT NULL,
    native_type VARCHAR(100),
    description TEXT,
    nullable VARCHAR(5),
    ordinal INT NOT NULL,
    is_primary_key VARCHAR(5) DEFAULT 'false',
    is_indexed VARCHAR(5) DEFAULT 'false',
    is_unique VARCHAR(5) DEFAULT 'false',
    is_partition_key VARCHAR(5) DEFAULT 'false',
    is_distribution_key VARCHAR(5) DEFAULT 'false'
);

-- ---------------------------------------------------------------------------
-- Schema Snapshots (change history)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_schema_snapshots (
    id SERIAL PRIMARY KEY,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    synced_at TIMESTAMPTZ DEFAULT now(),
    schema_json TEXT NOT NULL,
    field_count INT,
    change_summary VARCHAR(500),
    changes_json TEXT
);

-- ---------------------------------------------------------------------------
-- Tags
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_tags (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    color VARCHAR(7),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Dataset-Tag Mapping
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_dataset_tags (
    id SERIAL PRIMARY KEY,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    tag_id INT NOT NULL REFERENCES catalog_tags(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- Glossary Terms
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_glossary_terms (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL UNIQUE,
    description TEXT,
    parent_id INT REFERENCES catalog_glossary_terms(id),
    term_type VARCHAR(20) NOT NULL DEFAULT 'TERM',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Dataset-Glossary Mapping
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_dataset_glossary_terms (
    id SERIAL PRIMARY KEY,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    term_id INT NOT NULL REFERENCES catalog_glossary_terms(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- Ownership
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_owners (
    id SERIAL PRIMARY KEY,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    owner_name VARCHAR(200) NOT NULL,
    owner_type VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Datasource Metadata - Data Types
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasource_data_types (
    id SERIAL PRIMARY KEY,
    datasource_id INT NOT NULL REFERENCES catalog_datasources(id) ON DELETE CASCADE,
    type_name VARCHAR(100) NOT NULL,
    type_category VARCHAR(50) NOT NULL,
    description VARCHAR(500),
    ordinal INT NOT NULL,
    UNIQUE (datasource_id, type_name)
);

-- ---------------------------------------------------------------------------
-- Datasource Metadata - Table Types
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasource_table_types (
    id SERIAL PRIMARY KEY,
    datasource_id INT NOT NULL REFERENCES catalog_datasources(id) ON DELETE CASCADE,
    type_name VARCHAR(100) NOT NULL,
    display_name VARCHAR(200) NOT NULL,
    description VARCHAR(500),
    is_default VARCHAR(5),
    ordinal INT NOT NULL,
    UNIQUE (datasource_id, type_name)
);

-- ---------------------------------------------------------------------------
-- Datasource Metadata - Storage Formats
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasource_storage_formats (
    id SERIAL PRIMARY KEY,
    datasource_id INT NOT NULL REFERENCES catalog_datasources(id) ON DELETE CASCADE,
    format_name VARCHAR(100) NOT NULL,
    display_name VARCHAR(200) NOT NULL,
    description VARCHAR(500),
    is_default VARCHAR(5),
    ordinal INT NOT NULL,
    UNIQUE (datasource_id, format_name)
);

-- ---------------------------------------------------------------------------
-- Datasource Metadata - Features
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_datasource_features (
    id SERIAL PRIMARY KEY,
    datasource_id INT NOT NULL REFERENCES catalog_datasources(id) ON DELETE CASCADE,
    feature_key VARCHAR(100) NOT NULL,
    display_name VARCHAR(200) NOT NULL,
    description VARCHAR(500),
    value_type VARCHAR(50) NOT NULL,
    is_required VARCHAR(5),
    ordinal INT NOT NULL,
    UNIQUE (datasource_id, feature_key)
);

-- ---------------------------------------------------------------------------
-- Role Management
--   argus_users 가 role_id FK 로 참조하므로 반드시 먼저 생성해야 한다.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    description VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    role_id VARCHAR(50) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_argus_roles_role_id ON argus_roles USING btree (role_id);

-- ---------------------------------------------------------------------------
-- User Management
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    phone_number VARCHAR(30),
    password_hash VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL,
    -- 최초 로그인 시 비밀번호 강제 변경 플래그(LDAP 동기화 등 임시 비번 계정용)
    must_change_password BOOLEAN NOT NULL DEFAULT false,
    role_id INT NOT NULL REFERENCES argus_roles(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 기존 배포(컬럼이 없던 DB)도 멱등하게 수렴시키기 위한 컬럼 추가(이미 있으면 무시)
ALTER TABLE argus_users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;

-- ---------------------------------------------------------------------------
-- User Preferences (per-user UI preferences keyed by token sub)
--   로컬 인증: sub = str(argus_users.id)
--   Keycloak 인증: sub = Keycloak user UUID
-- 두 인증 모드에서 동일한 키로 동작하도록 별도 테이블로 분리
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_user_preferences (
    sub VARCHAR(100) PRIMARY KEY,
    avatar_preset_id VARCHAR(50),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- ML Model Registry - Registered Models
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_registered_models (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    urn VARCHAR(500) NOT NULL UNIQUE,
    datasource_id INT REFERENCES catalog_datasources(id) ON DELETE SET NULL,
    description TEXT,
    owner VARCHAR(200),
    storage_location VARCHAR(1000),
    max_version_number INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    created_by VARCHAR(200),
    updated_by VARCHAR(200),
    storage_type VARCHAR(20) NOT NULL DEFAULT 'local',
    bucket_name VARCHAR(255)
);

-- ---------------------------------------------------------------------------
-- ML Model Registry - Model Versions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_model_versions (
    id SERIAL PRIMARY KEY,
    model_id INT NOT NULL,
    version INT NOT NULL,
    source VARCHAR(1000),
    run_id VARCHAR(255),
    run_link VARCHAR(1000),
    description TEXT,
    status VARCHAR(30) NOT NULL,
    status_message TEXT,
    storage_location VARCHAR(1000),
    artifact_count INT,
    artifact_size INT,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    created_by VARCHAR(200),
    updated_by VARCHAR(200),
    stage VARCHAR(20) DEFAULT 'NONE',
    stage_changed_at TIMESTAMPTZ,
    stage_changed_by VARCHAR(200),
    UNIQUE (model_id, version)
);

-- ---------------------------------------------------------------------------
-- ML Model Registry - Model-Dataset Lineage
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_model_dataset_lineage (
    id SERIAL PRIMARY KEY,
    model_id INT NOT NULL REFERENCES catalog_registered_models(id) ON DELETE CASCADE,
    model_version INT,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    relation_type VARCHAR(30) NOT NULL DEFAULT 'TRAINING_DATA',
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_ds_lineage_model ON catalog_model_dataset_lineage (model_id);
CREATE INDEX IF NOT EXISTS idx_model_ds_lineage_dataset ON catalog_model_dataset_lineage (dataset_id);

-- ---------------------------------------------------------------------------
-- ML Model Registry - Model Metrics
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_model_metrics (
    id SERIAL PRIMARY KEY,
    model_id INT NOT NULL REFERENCES catalog_registered_models(id) ON DELETE CASCADE,
    version INT NOT NULL,
    metric_key VARCHAR(100) NOT NULL,
    metric_value DECIMAL(15,6) NOT NULL,
    recorded_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (model_id, version, metric_key)
);

CREATE INDEX IF NOT EXISTS idx_model_metrics_model ON catalog_model_metrics (model_id, version);

-- ---------------------------------------------------------------------------
-- ML Model Registry - Model Card
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_model_card (
    id SERIAL PRIMARY KEY,
    model_id INT NOT NULL UNIQUE REFERENCES catalog_registered_models(id) ON DELETE CASCADE,
    purpose TEXT,
    performance TEXT,
    limitations TEXT,
    training_data TEXT,
    framework VARCHAR(200),
    license VARCHAR(200),
    contact VARCHAR(200),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- ML Model Registry - Model Metadata
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_models (
    id SERIAL PRIMARY KEY,
    model_version_id INT NOT NULL,
    model_name VARCHAR(255) NOT NULL,
    version INT NOT NULL,
    predict_fn VARCHAR(100),
    python_version VARCHAR(20),
    serialization_format VARCHAR(50),
    sklearn_version VARCHAR(20),
    mlflow_version VARCHAR(20),
    mlflow_model_id VARCHAR(100),
    model_size_bytes BIGINT,
    utc_time_created VARCHAR(50),
    time_created TIMESTAMPTZ,
    requirements TEXT,
    conda TEXT,
    python_env TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    manifest TEXT,
    config TEXT,
    content_digest VARCHAR(100),
    source_type VARCHAR(50),
    UNIQUE (model_name, version)
);

-- ---------------------------------------------------------------------------
-- Model Download Log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_model_download_log (
    id SERIAL PRIMARY KEY,
    model_name VARCHAR(255) NOT NULL,
    version INT NOT NULL,
    download_type VARCHAR(20) NOT NULL,
    client_ip VARCHAR(45),
    user_agent VARCHAR(500),
    downloaded_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_catalog_model_download_log_downloaded_at ON catalog_model_download_log USING btree (downloaded_at);
CREATE INDEX IF NOT EXISTS ix_catalog_model_download_log_model_name ON catalog_model_download_log USING btree (model_name);

-- ---------------------------------------------------------------------------
-- OCI Model Hub - Models
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_oci_models (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(255),
    description TEXT,
    readme TEXT,
    task VARCHAR(50),
    framework VARCHAR(50),
    language VARCHAR(50),
    license VARCHAR(100),
    source_type VARCHAR(50),
    source_id VARCHAR(500),
    source_revision VARCHAR(100),
    bucket VARCHAR(255),
    storage_prefix VARCHAR(500),
    owner VARCHAR(200),
    version_count INT NOT NULL,
    total_size BIGINT,
    download_count INT NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- OCI Model Hub - Versions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_oci_model_versions (
    id SERIAL PRIMARY KEY,
    model_id INT NOT NULL REFERENCES catalog_oci_models(id) ON DELETE CASCADE,
    version INT NOT NULL,
    manifest TEXT,
    content_digest VARCHAR(100),
    file_count INT,
    total_size BIGINT,
    metadata jsonb,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (model_id, version)
);

-- ---------------------------------------------------------------------------
-- OCI Model Hub - Tags
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_oci_model_tags (
    id SERIAL PRIMARY KEY,
    model_id INT NOT NULL REFERENCES catalog_oci_models(id) ON DELETE CASCADE,
    tag_id INT NOT NULL REFERENCES catalog_tags(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (model_id, tag_id)
);

-- ---------------------------------------------------------------------------
-- OCI Model Hub - Lineage
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_oci_model_lineage (
    id SERIAL PRIMARY KEY,
    model_id INT NOT NULL REFERENCES catalog_oci_models(id) ON DELETE CASCADE,
    source_type VARCHAR(20) NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    source_name VARCHAR(255),
    relation_type VARCHAR(30) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- OCI Model Hub - Download Log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_oci_model_download_log (
    id SERIAL PRIMARY KEY,
    model_name VARCHAR(255) NOT NULL,
    version INT NOT NULL,
    download_type VARCHAR(20) NOT NULL,
    client_ip VARCHAR(45),
    user_agent VARCHAR(500),
    downloaded_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_catalog_oci_model_download_log_downloaded_at ON catalog_oci_model_download_log USING btree (downloaded_at);
CREATE INDEX IF NOT EXISTS ix_catalog_oci_model_download_log_model_name ON catalog_oci_model_download_log USING btree (model_name);

-- ---------------------------------------------------------------------------
-- Comments
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_comments (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    parent_id INT REFERENCES catalog_comments(id) ON DELETE CASCADE,
    root_id INT REFERENCES catalog_comments(id) ON DELETE CASCADE,
    depth INT NOT NULL,
    content TEXT NOT NULL,
    content_plain TEXT,
    author_name VARCHAR(100) NOT NULL,
    author_email VARCHAR(255),
    author_avatar VARCHAR(500),
    reply_count INT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    is_deleted BOOLEAN NOT NULL,
    category VARCHAR(20) NOT NULL DEFAULT 'general'
);

CREATE INDEX IF NOT EXISTS ix_catalog_comments_entity_id ON catalog_comments USING btree (entity_id);
CREATE INDEX IF NOT EXISTS ix_catalog_comments_entity_type ON catalog_comments USING btree (entity_type);

-- ---------------------------------------------------------------------------
-- Configuration
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_configuration (
    id SERIAL PRIMARY KEY,
    category VARCHAR(50) NOT NULL,
    config_key VARCHAR(100) NOT NULL UNIQUE,
    config_value VARCHAR(500) NOT NULL,
    description VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Collector - Hive Query History
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_collector_hive_query_history (
    id SERIAL PRIMARY KEY,
    query_id VARCHAR(256) NOT NULL UNIQUE,
    short_username VARCHAR(128),
    username VARCHAR(256),
    operation_name VARCHAR(64),
    start_time BIGINT,
    end_time BIGINT,
    duration_ms BIGINT,
    query TEXT,
    status VARCHAR(16) NOT NULL,
    error_msg TEXT,
    inputs_json TEXT,                                          -- hook 권위 입력 테이블(JSON 배열). 배치 write-lineage 우선 사용
    outputs_json TEXT,                                         -- hook 권위 출력 테이블(JSON 배열)
    received_at TIMESTAMPTZ DEFAULT now()
);

-- query_id 는 UNIQUE 제약이 인덱스를 겸하므로 별도 인덱스 불필요.
CREATE INDEX IF NOT EXISTS idx_hive_query_history_status ON argus_collector_hive_query_history USING btree (status);

-- ---------------------------------------------------------------------------
-- Collector - Impala Query History
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_collector_impala_query_history (
    id SERIAL PRIMARY KEY,
    query_id VARCHAR(256) NOT NULL UNIQUE,
    query_type VARCHAR(32),
    query_state VARCHAR(32),
    statement TEXT,
    database VARCHAR(256),
    username VARCHAR(256),
    coordinator_host VARCHAR(512),
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    duration_ms BIGINT,
    rows_produced BIGINT,
    datasource_id VARCHAR(100),
    received_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Collector - Trino Query History
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_collector_trino_query_history (
    id SERIAL PRIMARY KEY,
    query_id VARCHAR(256) NOT NULL UNIQUE,
    query_state VARCHAR(32),
    query_type VARCHAR(32),
    statement TEXT,
    plan TEXT,
    username VARCHAR(256),
    principal VARCHAR(256),
    source VARCHAR(256),
    catalog VARCHAR(256),
    schema VARCHAR(256),
    remote_client_address VARCHAR(256),
    create_time TIMESTAMPTZ,
    execution_start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    wall_time_ms BIGINT,
    cpu_time_ms BIGINT,
    physical_input_bytes BIGINT,
    physical_input_rows BIGINT,
    output_bytes BIGINT,
    output_rows BIGINT,
    peak_memory_bytes BIGINT,
    error_code VARCHAR(128),
    error_message TEXT,
    inputs_json TEXT,
    output_json TEXT,
    datasource_id VARCHAR(100),
    received_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trino_query_history_datasource_id ON argus_collector_trino_query_history USING btree (datasource_id);
CREATE INDEX IF NOT EXISTS idx_trino_query_history_query_id ON argus_collector_trino_query_history USING btree (query_id);
CREATE INDEX IF NOT EXISTS idx_trino_query_history_username ON argus_collector_trino_query_history USING btree (username);

-- ---------------------------------------------------------------------------
-- Collector - StarRocks Query History
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_collector_starrocks_query_history (
    id SERIAL PRIMARY KEY,
    query_id VARCHAR(256) NOT NULL UNIQUE,
    statement TEXT,
    digest VARCHAR(64),
    username VARCHAR(256),
    authorized_user VARCHAR(256),
    client_ip VARCHAR(64),
    database VARCHAR(256),
    catalog VARCHAR(256),
    state VARCHAR(16),
    error_code VARCHAR(512),
    query_time_ms BIGINT,
    scan_rows BIGINT,
    scan_bytes BIGINT,
    return_rows BIGINT,
    cpu_cost_ns BIGINT,
    mem_cost_bytes BIGINT,
    pending_time_ms BIGINT,
    is_query INT,
    fe_ip VARCHAR(128),
    event_timestamp BIGINT,
    datasource_id VARCHAR(100),
    received_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_starrocks_query_history_datasource_id ON argus_collector_starrocks_query_history USING btree (datasource_id);
CREATE INDEX IF NOT EXISTS idx_starrocks_query_history_query_id ON argus_collector_starrocks_query_history USING btree (query_id);
CREATE INDEX IF NOT EXISTS idx_starrocks_query_history_username ON argus_collector_starrocks_query_history USING btree (username);

-- ---------------------------------------------------------------------------
-- Lineage - Query Lineage (per-query source→target table mapping)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_query_lineage (
    id SERIAL PRIMARY KEY,
    query_hist_id INT,
    source_table VARCHAR(512) NOT NULL,
    target_table VARCHAR(512) NOT NULL,
    source_dataset_id INT,
    target_dataset_id INT,
    created_at TIMESTAMPTZ DEFAULT now(),
    -- 멱등성: 동일 (source→target) 엣지는 1행으로 유지
    CONSTRAINT uq_query_lineage_edge UNIQUE (source_table, target_table)
);

-- ---------------------------------------------------------------------------
-- Lineage - Column Lineage (per-query source→target column mapping)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_column_lineage (
    id SERIAL PRIMARY KEY,
    query_lineage_id INT NOT NULL,
    source_column VARCHAR(256) NOT NULL,
    target_column VARCHAR(256) NOT NULL,
    transform_type VARCHAR(64) NOT NULL DEFAULT 'DIRECT',
    -- 멱등성: 동일 query_lineage 내 (source_col→target_col) 매핑은 1행으로 유지
    CONSTRAINT uq_column_lineage_edge UNIQUE (query_lineage_id, source_column, target_column)
);

-- ---------------------------------------------------------------------------
-- Lineage - Data Pipeline (ETL/CDC/file-export pipeline registry)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_data_pipeline (
    id SERIAL PRIMARY KEY,
    pipeline_name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    pipeline_type VARCHAR(64) NOT NULL,
    schedule VARCHAR(100),
    owner VARCHAR(200),
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Lineage - Dataset Lineage (aggregated dataset-to-dataset relationships)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_dataset_lineage (
    id SERIAL PRIMARY KEY,
    source_dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    target_dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    relation_type VARCHAR(32) NOT NULL,
    lineage_source VARCHAR(32) NOT NULL,
    pipeline_id INT REFERENCES argus_data_pipeline(id) ON DELETE SET NULL,
    description TEXT,
    created_by VARCHAR(200),
    query_count INT NOT NULL,
    last_query_id VARCHAR(256),
    last_seen_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source_dataset_id, target_dataset_id, relation_type, lineage_source)
);

CREATE INDEX IF NOT EXISTS idx_dataset_lineage_pipeline ON argus_dataset_lineage USING btree (pipeline_id);
CREATE INDEX IF NOT EXISTS idx_dataset_lineage_source ON argus_dataset_lineage USING btree (source_dataset_id);
CREATE INDEX IF NOT EXISTS idx_dataset_lineage_target ON argus_dataset_lineage USING btree (target_dataset_id);

-- ---------------------------------------------------------------------------
-- Lineage - Dataset Column Mapping (cross-datasource column-level lineage)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_dataset_column_mapping (
    id SERIAL PRIMARY KEY,
    dataset_lineage_id INT NOT NULL REFERENCES argus_dataset_lineage(id) ON DELETE CASCADE,
    source_column VARCHAR(256) NOT NULL,
    target_column VARCHAR(256) NOT NULL,
    transform_type VARCHAR(64) NOT NULL,
    transform_expr VARCHAR(500),
    UNIQUE (dataset_lineage_id, source_column, target_column)
);

CREATE INDEX IF NOT EXISTS idx_dataset_column_mapping_lineage ON argus_dataset_column_mapping USING btree (dataset_lineage_id);

-- ---------------------------------------------------------------------------
-- Relationship - Column Relationship (사용 기반 컬럼 관계, 쿼리 JOIN 키 빈도)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_column_relationship (
    id SERIAL PRIMARY KEY,
    dataset_a_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    field_a VARCHAR(500) NOT NULL,
    dataset_b_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    field_b VARCHAR(500) NOT NULL,
    relation_type VARCHAR(32) NOT NULL DEFAULT 'JOIN_KEY',
    join_count INT NOT NULL DEFAULT 0,
    explicit_count INT NOT NULL DEFAULT 0,
    implicit_count INT NOT NULL DEFAULT 0,
    distinct_users INT NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMPTZ DEFAULT now(),
    last_seen_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (dataset_a_id, field_a, dataset_b_id, field_b, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_column_relationship_a ON catalog_column_relationship USING btree (dataset_a_id);
CREATE INDEX IF NOT EXISTS idx_column_relationship_b ON catalog_column_relationship USING btree (dataset_b_id);

COMMENT ON TABLE catalog_column_relationship IS '사용 기반 컬럼 관계 — 쿼리 워크로드의 JOIN 키에서 발견한 무방향 컬럼쌍(암묵 FK/조인키)';
COMMENT ON COLUMN catalog_column_relationship.dataset_a_id IS '무방향 한쪽 데이터셋(정규화 시 작은 쪽)';
COMMENT ON COLUMN catalog_column_relationship.field_a IS 'dataset_a 의 컬럼(field_path)';
COMMENT ON COLUMN catalog_column_relationship.dataset_b_id IS '무방향 다른 쪽 데이터셋';
COMMENT ON COLUMN catalog_column_relationship.field_b IS 'dataset_b 의 컬럼(field_path)';
COMMENT ON COLUMN catalog_column_relationship.relation_type IS '관계 유형: JOIN_KEY (확장: FILTER_EQ, CO_GROUP)';
COMMENT ON COLUMN catalog_column_relationship.join_count IS 'confidence — 이 관계가 등장한 쿼리 수(명시+암묵)';
COMMENT ON COLUMN catalog_column_relationship.explicit_count IS '명시적 JOIN ... ON 으로 관측된 횟수';
COMMENT ON COLUMN catalog_column_relationship.implicit_count IS '암묵 조인(WHERE 등치)으로 관측된 횟수';
COMMENT ON COLUMN catalog_column_relationship.distinct_users IS '관측된 사용자 다양성(선택)';
COMMENT ON COLUMN catalog_column_relationship.first_seen_at IS '최초 관측 시각';
COMMENT ON COLUMN catalog_column_relationship.last_seen_at IS '최근 관측 시각';

-- ---------------------------------------------------------------------------
-- Relationship Analyzer — 2단계 파이프라인(관측치 → 롤업) staging/watermark
-- relationship-analyzer(배치 잡)가 query_history 를 파싱해 관측치를 적재하고,
-- rollup 잡이 이를 집계해 catalog_column_relationship 을 재구축한다.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS relationship_observations (
    id            BIGSERIAL PRIMARY KEY,
    query_id      VARCHAR(256) NOT NULL,
    datasource_id VARCHAR(100) NOT NULL,
    a_table       VARCHAR(512) NOT NULL,
    a_col         VARCHAR(512) NOT NULL,
    b_table       VARCHAR(512) NOT NULL,
    b_col         VARCHAR(512) NOT NULL,
    kind          VARCHAR(16)  NOT NULL DEFAULT 'explicit',   -- explicit | implicit
    query_user    VARCHAR(256),
    observed_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (query_id, a_table, a_col, b_table, b_col)         -- 멱등(재처리 무해)
);
CREATE INDEX IF NOT EXISTS idx_relobs_datasource ON relationship_observations(datasource_id);
COMMENT ON TABLE relationship_observations IS '쿼리에서 추출한 JOIN 키 관측치(append-only staging). rollup 이 집계.';

CREATE TABLE IF NOT EXISTS relationship_ingest_offset (
    partition_key   VARCHAR(100) PRIMARY KEY,                 -- 플랫폼: trino/hive/impala/starrocks
    last_event_id   BIGINT NOT NULL DEFAULT 0,                -- 마지막 처리한 query_history.id
    updated_at      TIMESTAMPTZ DEFAULT now()
);
COMMENT ON TABLE relationship_ingest_offset IS 'analyzer 의 query_history 처리 워터마크(파티션=플랫폼).';

CREATE TABLE IF NOT EXISTS lineage_ingest_offset (
    partition_key   VARCHAR(100) PRIMARY KEY,                 -- 플랫폼: trino/hive/impala/starrocks
    last_event_id   BIGINT NOT NULL DEFAULT 0,                -- 마지막 처리한 query_history.id
    updated_at      TIMESTAMPTZ DEFAULT now()
);
COMMENT ON TABLE lineage_ingest_offset IS 'query-service write-lineage 배치의 query_history 처리 워터마크(파티션=플랫폼).';

-- ---------------------------------------------------------------------------
-- Alert - Lineage Alert (schema change impact events)
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Alert - Alert Rule (what to watch, when to trigger, who to notify)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_alert_rule (
    id SERIAL PRIMARY KEY,
    rule_name VARCHAR(255) NOT NULL,
    description TEXT,
    scope_type VARCHAR(32) NOT NULL,
    scope_id INT,
    trigger_type VARCHAR(64) NOT NULL,
    trigger_config TEXT DEFAULT '{}',
    severity_override VARCHAR(16),
    channels VARCHAR(200) NOT NULL DEFAULT 'IN_APP',
    notify_owners VARCHAR(5) NOT NULL DEFAULT 'true',
    webhook_url VARCHAR(500),
    subscribers VARCHAR(2000),
    is_active VARCHAR(5) NOT NULL DEFAULT 'true',
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alert_rule_scope ON argus_alert_rule (scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_alert_rule_active ON argus_alert_rule (is_active);

-- ---------------------------------------------------------------------------
-- Alert - Lineage Alert (schema change impact events)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_lineage_alert (
    id SERIAL PRIMARY KEY,
    alert_type VARCHAR(32) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    source_dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    affected_dataset_id INT REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    lineage_id INT REFERENCES argus_dataset_lineage(id) ON DELETE SET NULL,
    rule_id INT REFERENCES argus_alert_rule(id) ON DELETE SET NULL,
    change_summary VARCHAR(500) NOT NULL,
    change_detail TEXT,
    status VARCHAR(20) NOT NULL,
    resolved_by VARCHAR(200),
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lineage_alert_affected ON argus_lineage_alert USING btree (affected_dataset_id);
CREATE INDEX IF NOT EXISTS idx_lineage_alert_source ON argus_lineage_alert USING btree (source_dataset_id);
CREATE INDEX IF NOT EXISTS idx_lineage_alert_status ON argus_lineage_alert USING btree (status);
CREATE INDEX IF NOT EXISTS idx_lineage_alert_rule ON argus_lineage_alert USING btree (rule_id);

-- ---------------------------------------------------------------------------
-- Alert - Notification Log (delivery records)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS argus_alert_notification (
    id SERIAL PRIMARY KEY,
    alert_id INT NOT NULL REFERENCES argus_lineage_alert(id) ON DELETE CASCADE,
    channel VARCHAR(32) NOT NULL,
    recipient VARCHAR(200) NOT NULL,
    sent_at TIMESTAMPTZ DEFAULT now(),
    status VARCHAR(20) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alert_notification_alert ON argus_alert_notification USING btree (alert_id);

-- ---------------------------------------------------------------------------
-- Data Standard - Dictionary (표준 사전)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_dictionary (
    id SERIAL PRIMARY KEY,
    dict_name VARCHAR(200) NOT NULL UNIQUE,
    description TEXT,
    version VARCHAR(50),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    effective_date DATE,
    expiry_date DATE,
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Data Standard - Word (표준 단어)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_word (
    id SERIAL PRIMARY KEY,
    dictionary_id INT NOT NULL REFERENCES catalog_standard_dictionary(id) ON DELETE CASCADE,
    word_name VARCHAR(100) NOT NULL,
    word_english VARCHAR(100) NOT NULL,
    word_abbr VARCHAR(50) NOT NULL,
    description TEXT,
    word_type VARCHAR(20) NOT NULL DEFAULT 'GENERAL',
    is_forbidden VARCHAR(5) DEFAULT 'false',
    synonym_group_id INT,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (dictionary_id, word_name)
);

CREATE INDEX IF NOT EXISTS idx_std_word_dict ON catalog_standard_word (dictionary_id);
CREATE INDEX IF NOT EXISTS idx_std_word_type ON catalog_standard_word (word_type);

-- ---------------------------------------------------------------------------
-- Data Standard - Code Group (코드 그룹)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_code_group (
    id SERIAL PRIMARY KEY,
    dictionary_id INT NOT NULL REFERENCES catalog_standard_dictionary(id) ON DELETE CASCADE,
    group_name VARCHAR(200) NOT NULL,
    group_english VARCHAR(200),
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (dictionary_id, group_name)
);

-- ---------------------------------------------------------------------------
-- Data Standard - Code Value (코드 값)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_code_value (
    id SERIAL PRIMARY KEY,
    code_group_id INT NOT NULL REFERENCES catalog_code_group(id) ON DELETE CASCADE,
    code_value VARCHAR(100) NOT NULL,
    code_name VARCHAR(200) NOT NULL,
    code_english VARCHAR(200),
    description TEXT,
    sort_order INT NOT NULL DEFAULT 0,
    is_active VARCHAR(5) DEFAULT 'true',
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (code_group_id, code_value)
);

CREATE INDEX IF NOT EXISTS idx_code_value_group ON catalog_code_value (code_group_id);

-- ---------------------------------------------------------------------------
-- Data Standard - Domain (표준 도메인)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_domain (
    id SERIAL PRIMARY KEY,
    dictionary_id INT NOT NULL REFERENCES catalog_standard_dictionary(id) ON DELETE CASCADE,
    domain_name VARCHAR(100) NOT NULL,
    domain_group VARCHAR(100),
    data_type VARCHAR(50) NOT NULL,
    data_length INT,
    data_precision INT,
    data_scale INT,
    description TEXT,
    code_group_id INT REFERENCES catalog_code_group(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (dictionary_id, domain_name)
);

CREATE INDEX IF NOT EXISTS idx_std_domain_dict ON catalog_standard_domain (dictionary_id);

-- ---------------------------------------------------------------------------
-- Data Standard - Term (표준 용어)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_term (
    id SERIAL PRIMARY KEY,
    dictionary_id INT NOT NULL REFERENCES catalog_standard_dictionary(id) ON DELETE CASCADE,
    term_name VARCHAR(200) NOT NULL,
    term_english VARCHAR(200) NOT NULL,
    term_abbr VARCHAR(100) NOT NULL,
    physical_name VARCHAR(100) NOT NULL,
    domain_id INT REFERENCES catalog_standard_domain(id) ON DELETE SET NULL,
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (dictionary_id, term_name)
);

CREATE INDEX IF NOT EXISTS idx_std_term_dict ON catalog_standard_term (dictionary_id);
CREATE INDEX IF NOT EXISTS idx_std_term_physical ON catalog_standard_term (physical_name);

-- ---------------------------------------------------------------------------
-- Data Standard - Term Words (용어 구성 단어)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_term_words (
    id SERIAL PRIMARY KEY,
    term_id INT NOT NULL REFERENCES catalog_standard_term(id) ON DELETE CASCADE,
    word_id INT NOT NULL REFERENCES catalog_standard_word(id) ON DELETE CASCADE,
    ordinal INT NOT NULL,
    UNIQUE (term_id, word_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_std_term_words_term ON catalog_standard_term_words (term_id);

-- ---------------------------------------------------------------------------
-- Data Standard - Term-Column Mapping (표준 용어 ↔ 실제 컬럼 매핑)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_term_column_mapping (
    id SERIAL PRIMARY KEY,
    term_id INT NOT NULL REFERENCES catalog_standard_term(id) ON DELETE CASCADE,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    schema_id INT NOT NULL REFERENCES catalog_dataset_schemas(id) ON DELETE CASCADE,
    mapping_type VARCHAR(20) NOT NULL DEFAULT 'MATCHED',
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (term_id, schema_id)
);

CREATE INDEX IF NOT EXISTS idx_term_col_mapping_term ON catalog_term_column_mapping (term_id);
CREATE INDEX IF NOT EXISTS idx_term_col_mapping_dataset ON catalog_term_column_mapping (dataset_id);

-- ---------------------------------------------------------------------------
-- Data Standard - Change Log (변경 이력)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_standard_change_log (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(20) NOT NULL,
    entity_id INT NOT NULL,
    change_type VARCHAR(20) NOT NULL,
    field_name VARCHAR(100),
    old_value TEXT,
    new_value TEXT,
    changed_by VARCHAR(200),
    changed_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_std_change_log_entity ON catalog_standard_change_log (entity_type, entity_id);

-- ---------------------------------------------------------------------------
-- Data Quality - Profile (column-level statistics)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_data_profile (
    id SERIAL PRIMARY KEY,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    row_count BIGINT NOT NULL DEFAULT 0,
    profile_json TEXT NOT NULL DEFAULT '[]',
    profiled_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_data_profile_dataset ON catalog_data_profile (dataset_id);

-- ---------------------------------------------------------------------------
-- Data Quality - Rule (quality check definitions)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_quality_rule (
    id SERIAL PRIMARY KEY,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    rule_name VARCHAR(255) NOT NULL,
    check_type VARCHAR(50) NOT NULL,
    column_name VARCHAR(256),
    expected_value TEXT,
    threshold DECIMAL(5,2) DEFAULT 100.00,
    severity VARCHAR(16) NOT NULL DEFAULT 'WARNING',
    is_active VARCHAR(5) NOT NULL DEFAULT 'true',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quality_rule_dataset ON catalog_quality_rule (dataset_id);

-- ---------------------------------------------------------------------------
-- Data Quality - Result (check execution results)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_quality_result (
    id SERIAL PRIMARY KEY,
    rule_id INT NOT NULL REFERENCES catalog_quality_rule(id) ON DELETE CASCADE,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    passed VARCHAR(5) NOT NULL,
    actual_value TEXT,
    detail TEXT,
    checked_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quality_result_rule ON catalog_quality_result (rule_id);
CREATE INDEX IF NOT EXISTS idx_quality_result_dataset ON catalog_quality_result (dataset_id);

-- ---------------------------------------------------------------------------
-- Data Quality - Score (aggregated quality score history)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_quality_score (
    id SERIAL PRIMARY KEY,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    score DECIMAL(5,2) NOT NULL DEFAULT 0,
    total_rules INT NOT NULL DEFAULT 0,
    passed_rules INT NOT NULL DEFAULT 0,
    warning_rules INT NOT NULL DEFAULT 0,
    failed_rules INT NOT NULL DEFAULT 0,
    scored_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quality_score_dataset ON catalog_quality_score (dataset_id);

-- ---------------------------------------------------------------------------
-- Dataset Embeddings (semantic search)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_dataset_embeddings (
    id SERIAL PRIMARY KEY,
    dataset_id INT NOT NULL UNIQUE REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    embedding vector(384) NOT NULL,
    source_text TEXT NOT NULL,
    model_name VARCHAR(200) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    dimension INT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dataset_embeddings_ivfflat
    ON catalog_dataset_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ---------------------------------------------------------------------------
-- Entity Embeddings (semantic search — 용어집/AI Agent/API, 다형)
--   FK 대상이 엔티티별로 달라 CASCADE 불가 → 삭제는 서비스 delete 경로에서 수행.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_entity_embeddings (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(30) NOT NULL,
    entity_id INT NOT NULL,
    embedding vector(384) NOT NULL,
    source_text TEXT NOT NULL,
    model_name VARCHAR(200) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    dimension INT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_embeddings_ivfflat
    ON catalog_entity_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ---------------------------------------------------------------------------
-- AI Metadata Generation
-- ---------------------------------------------------------------------------

-- Add PII type column to dataset schemas
ALTER TABLE catalog_dataset_schemas ADD COLUMN IF NOT EXISTS pii_type VARCHAR(50);

-- Logical(display) name 컬럼 — physical name 과 분리된 표시용 라벨.
-- Dataset: 예) physical "REGION" → display "지역"
-- DatasetSchema: 예) physical "customer_no" → display "고객번호"
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);
ALTER TABLE catalog_dataset_schemas ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);

-- AI generation log for audit, preview/apply workflow, and cost tracking
CREATE TABLE IF NOT EXISTS catalog_ai_generation_log (
    id SERIAL PRIMARY KEY,
    entity_type VARCHAR(20) NOT NULL,
    entity_id INT NOT NULL,
    dataset_id INT NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    field_name VARCHAR(500),
    generation_type VARCHAR(30) NOT NULL,
    generated_text TEXT NOT NULL,
    applied BOOLEAN DEFAULT FALSE,
    provider VARCHAR(50) NOT NULL,
    model VARCHAR(100) NOT NULL,
    prompt_tokens INT,
    completion_tokens INT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_gen_log_dataset ON catalog_ai_generation_log (dataset_id);
CREATE INDEX IF NOT EXISTS idx_ai_gen_log_applied ON catalog_ai_generation_log (applied);

-- ---------------------------------------------------------------------------
-- 테이블 한글 설명 (psql ``\d+ <table>`` 또는 메타데이터 조회 시 노출)
-- ORM 의 docstring/주석과 동일한 의미를 DB 메타에도 남겨 두기 위함.
-- ---------------------------------------------------------------------------

COMMENT ON TABLE catalog_datasources IS '카탈로그가 연결되는 외부 데이터 플랫폼 레지스트리';
COMMENT ON TABLE catalog_datasource_configurations IS '플랫폼별 접속·인증·옵션 설정 (JDBC URL, 자격 증명 등)';
COMMENT ON TABLE catalog_datasets IS '데이터셋 본체 — 플랫폼·이름·소유자·라이프사이클 상태';
COMMENT ON COLUMN catalog_datasets.summary IS '한 줄 요약 (목록·검색·대시보드 카드 노출용, 최대 200자)';
COMMENT ON COLUMN catalog_datasets.description IS '데이터셋 상세 설명 (리치 텍스트 / 마크다운 본문)';
COMMENT ON TABLE catalog_dataset_properties IS '데이터셋 부가 속성(key/value)';
COMMENT ON TABLE catalog_dataset_schemas IS '데이터셋의 컬럼 스키마 (현재 시점)';
COMMENT ON TABLE catalog_schema_snapshots IS '스키마 변경 이력 — 시점별 전체 스냅샷';
COMMENT ON TABLE catalog_tags IS '태그 마스터(이름·색상·설명)';
COMMENT ON TABLE catalog_dataset_tags IS '데이터셋 ↔ 태그 매핑';
COMMENT ON TABLE catalog_glossary_terms IS '용어집 항목';
COMMENT ON TABLE catalog_dataset_glossary_terms IS '데이터셋 ↔ 용어집 매핑';
COMMENT ON TABLE catalog_owners IS '데이터셋 소유자(사람·팀)';
COMMENT ON TABLE catalog_datasource_data_types IS '플랫폼별 지원 데이터 타입 메타';
COMMENT ON TABLE catalog_datasource_table_types IS '플랫폼별 지원 테이블 타입(테이블/뷰/머터리얼라이즈드 등)';
COMMENT ON TABLE catalog_datasource_storage_formats IS '플랫폼별 지원 저장 포맷(Parquet/ORC 등)';
COMMENT ON TABLE catalog_datasource_features IS '플랫폼별 지원 기능 플래그';
COMMENT ON TABLE argus_users IS '로컬 인증 사용자 계정';
COMMENT ON TABLE argus_roles IS '권한 역할 정의';
COMMENT ON TABLE argus_user_preferences IS '사용자별 UI 환경 설정(아바타 등) — 토큰 sub 키로 로컬·Keycloak 인증 공용';
COMMENT ON TABLE catalog_registered_models IS 'MLflow 모델 레지스트리 — 등록 모델';
COMMENT ON TABLE catalog_model_versions IS 'MLflow 모델 버전 (PENDING_REGISTRATION → READY / FAILED_REGISTRATION)';
COMMENT ON TABLE catalog_model_dataset_lineage IS '모델 ↔ 학습/평가 데이터셋 리니지';
COMMENT ON TABLE catalog_model_metrics IS '모델 버전별 성능 지표(accuracy, f1 등)';
COMMENT ON TABLE catalog_model_card IS '모델 카드 — 목적·성능·한계·라이선스 등 거버넌스 문서';
COMMENT ON TABLE catalog_models IS 'MLflow 산출 메타데이터(predict_fn, sklearn_version 등)';
COMMENT ON TABLE catalog_model_download_log IS 'MLflow 모델 다운로드 이벤트 로그';
COMMENT ON TABLE catalog_oci_models IS 'OCI 모델 허브 — 모델 본체 (HuggingFace 스타일)';
COMMENT ON TABLE catalog_oci_model_versions IS 'OCI 모델 허브 — 버전별 아티팩트 + OCI manifest';
COMMENT ON TABLE catalog_oci_model_tags IS 'OCI 모델 ↔ 태그 매핑 (catalog_tags 재사용)';
COMMENT ON TABLE catalog_oci_model_lineage IS 'OCI 모델 — 학습 데이터·부모 모델 등 외부 자원 관계';
COMMENT ON TABLE catalog_oci_model_download_log IS 'OCI 모델 다운로드 이벤트 로그';
COMMENT ON TABLE catalog_comments IS '댓글 — 데이터셋·모델 등 엔티티에 공통으로 달리는 본문';
COMMENT ON TABLE catalog_configuration IS '시스템 전역 설정(key/value)';
COMMENT ON TABLE argus_collector_hive_query_history IS '외부 Hive 엔진 쿼리 히스토리 (collector 가 적재)';
COMMENT ON TABLE argus_collector_impala_query_history IS '외부 Impala 엔진 쿼리 히스토리 (collector 가 적재)';
COMMENT ON TABLE argus_collector_trino_query_history IS '외부 Trino 엔진 쿼리 히스토리 (collector 가 적재)';
COMMENT ON TABLE argus_collector_starrocks_query_history IS '외부 StarRocks 엔진 쿼리 히스토리 (collector 가 적재)';
COMMENT ON TABLE argus_query_lineage IS '쿼리 단위 source → target 테이블 매핑';
COMMENT ON TABLE argus_column_lineage IS '쿼리 단위 source → target 컬럼 매핑';
COMMENT ON TABLE argus_data_pipeline IS 'ETL/CDC/파일-export 등 외부 파이프라인 레지스트리';
COMMENT ON TABLE argus_dataset_lineage IS '집계된 데이터셋 ↔ 데이터셋 관계 (lineage)';
COMMENT ON TABLE argus_dataset_column_mapping IS '크로스 플랫폼 컬럼-레벨 lineage';
COMMENT ON TABLE argus_alert_rule IS '알림 규칙 — 감시 대상 + 트리거 조건 + 알림 채널 설정';
COMMENT ON TABLE argus_lineage_alert IS '스키마 변경 영향 알림';
COMMENT ON TABLE argus_alert_notification IS '알림 전달 기록 (IN_APP / WEBHOOK / EMAIL)';
COMMENT ON TABLE catalog_standard_dictionary IS '표준 사전 — 단어·도메인·코드·용어를 묶는 상위 그룹';
COMMENT ON TABLE catalog_standard_word IS '표준 단어 (영문/한글 명칭·약어)';
COMMENT ON TABLE catalog_code_group IS '코드 그룹 (예: 거래 상태)';
COMMENT ON TABLE catalog_code_value IS '코드 값 (예: PENDING, COMPLETED)';
COMMENT ON TABLE catalog_standard_domain IS '표준 도메인 — 논리 데이터 타입';
COMMENT ON TABLE catalog_standard_term IS '표준 용어 — 단어 조합으로 만든 컬럼명 후보';
COMMENT ON TABLE catalog_standard_term_words IS '표준 용어 ↔ 구성 단어 매핑';
COMMENT ON TABLE catalog_term_column_mapping IS '표준 용어 ↔ 실제 데이터셋 컬럼 매핑';
COMMENT ON TABLE catalog_standard_change_log IS '표준 단어/도메인/용어/코드의 변경 이력';
COMMENT ON TABLE catalog_data_profile IS '데이터 프로파일링 결과 (Method A/B 산출 통계)';
COMMENT ON TABLE catalog_quality_rule IS '데이터 품질 규칙';
COMMENT ON TABLE catalog_quality_result IS '품질 검사 실행 결과';
COMMENT ON TABLE catalog_quality_score IS '데이터셋별 품질 점수';
COMMENT ON TABLE catalog_dataset_embeddings IS '데이터셋 메타데이터 임베딩 (시맨틱 검색용)';
COMMENT ON TABLE catalog_entity_embeddings IS '용어집/AI Agent/API 임베딩 (시맨틱 검색용, 다형)';
COMMENT ON TABLE catalog_ai_generation_log IS 'AI 생성 호출 이력 (설명/태그/PII 자동 생성)';

-- ============================================================================
-- AI Agent 카탈로그 (catalog_ai_agents 및 서브리소스)
-- 설계: design/ai-agent-catalog.md (통합 메타데이터 모델 G1~G12)
-- ============================================================================

CREATE TABLE IF NOT EXISTS catalog_ai_agents (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    urn VARCHAR(500) NOT NULL UNIQUE,
    display_name VARCHAR(255),
    description TEXT,
    version VARCHAR(50) NOT NULL DEFAULT '0.1.0',
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    owner_email VARCHAR(200),
    department VARCHAR(200),
    category VARCHAR(100),
    base_model VARCHAR(255),
    base_model_ref INT REFERENCES catalog_registered_models(id) ON DELETE SET NULL,
    model_provider VARCHAR(100),
    framework VARCHAR(100),
    execution_policy VARCHAR(50),
    max_steps INT,
    memory_type VARCHAR(30),
    is_multi_agent BOOLEAN NOT NULL DEFAULT FALSE,
    endpoint VARCHAR(1000),
    protocol VARCHAR(30),
    invocation_method VARCHAR(30),
    auth_method VARCHAR(50),
    pii_handling VARCHAR(30),
    data_residency VARCHAR(100),
    budget_limit NUMERIC(12, 4),
    hitl_required BOOLEAN NOT NULL DEFAULT FALSE,
    audit_log_ref VARCHAR(500),
    latency_p50 INT,
    latency_p95 INT,
    error_rate NUMERIC(5, 4),
    avg_token_usage INT,
    cost_per_call NUMERIC(12, 6),
    reputation_score NUMERIC(5, 2),
    capabilities JSONB,
    input_schema JSONB,
    output_schema JSONB,
    supported_languages JSONB,
    use_cases JSONB,
    limitations JSONB,
    inference_params JSONB,
    guardrails JSONB,
    rag_config JSONB,
    network_allowlist JSONB,
    dlp_policies JSONB,
    hitl_config JSONB,
    sub_agents JSONB,
    tags JSONB,
    usage_count INT NOT NULL DEFAULT 0,
    last_invoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    created_by VARCHAR(200),
    updated_by VARCHAR(200)
);

CREATE TABLE IF NOT EXISTS catalog_ai_agent_versions (
    id SERIAL PRIMARY KEY,
    agent_id INT NOT NULL REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    version VARCHAR(50) NOT NULL,
    source VARCHAR(1000),
    system_prompt TEXT,
    changelog TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT now(),
    created_by VARCHAR(200),
    UNIQUE (agent_id, version)
);

CREATE TABLE IF NOT EXISTS catalog_ai_agent_tools (
    id SERIAL PRIMARY KEY,
    agent_id INT NOT NULL REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    tool_schema JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog_ai_agent_mcp_servers (
    id SERIAL PRIMARY KEY,
    agent_id INT NOT NULL REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    url VARCHAR(1000),
    auth_method VARCHAR(50),
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog_ai_agent_lineage (
    id SERIAL PRIMARY KEY,
    agent_id INT NOT NULL REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    target_type VARCHAR(20) NOT NULL DEFAULT 'agent',
    target_ref VARCHAR(500) NOT NULL,
    relation VARCHAR(20) NOT NULL DEFAULT 'depends_on',
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE catalog_ai_agents IS 'AI 에이전트 카탈로그 메인 엔티티 (식별/모델/실행/거버넌스/관측)';
COMMENT ON TABLE catalog_ai_agent_versions IS '에이전트 사양 버전 이력 (system_prompt 스냅샷)';
COMMENT ON TABLE catalog_ai_agent_tools IS '에이전트 호출 가능 도구 + 스키마';
COMMENT ON TABLE catalog_ai_agent_mcp_servers IS '에이전트 연결 MCP 서버';
COMMENT ON TABLE catalog_ai_agent_lineage IS '에이전트 ↔ 에이전트/모델/데이터셋 의존 관계';

-- ---- AI Agent 평가/미터링 (Phase 2) ----

CREATE TABLE IF NOT EXISTS catalog_ai_agent_evals (
    id SERIAL PRIMARY KEY,
    agent_id INT NOT NULL REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    version VARCHAR(50),
    eval_type VARCHAR(50) NOT NULL,
    metric_key VARCHAR(100) NOT NULL,
    metric_value NUMERIC(10, 4) NOT NULL,
    dataset_ref VARCHAR(500),
    passed BOOLEAN,
    notes TEXT,
    evaluated_at TIMESTAMPTZ DEFAULT now(),
    created_by VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS idx_ai_agent_evals_agent ON catalog_ai_agent_evals(agent_id);

CREATE TABLE IF NOT EXISTS catalog_ai_agent_invocation_log (
    id SERIAL PRIMARY KEY,
    agent_id INT NOT NULL REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    invoked_at TIMESTAMPTZ DEFAULT now(),
    consumer VARCHAR(200),
    status VARCHAR(20) NOT NULL DEFAULT 'success',
    error_type VARCHAR(100),
    latency_ms INT,
    input_tokens INT,
    output_tokens INT,
    cost NUMERIC(12, 6),
    session_id VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS idx_ai_agent_invlog_agent ON catalog_ai_agent_invocation_log(agent_id);
CREATE INDEX IF NOT EXISTS idx_ai_agent_invlog_time ON catalog_ai_agent_invocation_log(invoked_at);

COMMENT ON TABLE catalog_ai_agent_evals IS '에이전트 평가 결과 (정확도/성공률/환각/안전 등)';
COMMENT ON TABLE catalog_ai_agent_invocation_log IS '에이전트 호출 미터링 원장 (토큰/비용/지연/성공여부)';

-- ---- AI Agent 집행 훅 이벤트 (Phase 3 연동 인터페이스) ----

CREATE TABLE IF NOT EXISTS catalog_ai_agent_hook_events (
    id SERIAL PRIMARY KEY,
    agent_id INT NOT NULL REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    occurred_at TIMESTAMPTZ DEFAULT now(),
    stage VARCHAR(20) NOT NULL,
    decision VARCHAR(30) NOT NULL,
    action_type VARCHAR(50),
    target VARCHAR(500),
    policy_ref VARCHAR(100),
    reason TEXT,
    session_id VARCHAR(200),
    consumer VARCHAR(200),
    event_metadata JSONB
);
CREATE INDEX IF NOT EXISTS idx_ai_agent_hook_agent ON catalog_ai_agent_hook_events(agent_id);
CREATE INDEX IF NOT EXISTS idx_ai_agent_hook_time ON catalog_ai_agent_hook_events(occurred_at);

COMMENT ON TABLE catalog_ai_agent_hook_events IS '외부 Execution Plane이 보고한 집행 훅 이벤트 감사 로그 (egress 차단/PII 마스킹/HITL 등)';

-- ===========================================================================
-- 보충 스키마 — ORM 모델 대비 누락분 (API 카탈로그 / 변경관리 / 조직·시스템·분류 등)
-- 라이브 스키마(Base.metadata.create_all) 기준으로 생성. reconcile_schema() 보정분 포함.
-- ===========================================================================

CREATE TABLE IF NOT EXISTS argus_change_request (
    id SERIAL PRIMARY KEY,
    cr_code VARCHAR(32) NOT NULL UNIQUE,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    dataset_id INTEGER NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    change_type VARCHAR(32) NOT NULL,
    priority VARCHAR(16) NOT NULL,
    status VARCHAR(24) NOT NULL,
    schema_before TEXT,
    schema_after TEXT,
    impact_report TEXT,
    rollback_plan TEXT NOT NULL,
    business_justification TEXT NOT NULL,
    scheduled_at TIMESTAMPTZ,
    applied_at TIMESTAMPTZ,
    workflow_id VARCHAR(200),
    requested_by VARCHAR(200) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS argus_change_approval_step (
    id SERIAL PRIMARY KEY,
    cr_id INTEGER NOT NULL REFERENCES argus_change_request(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    approver VARCHAR(200) NOT NULL,
    role VARCHAR(64),
    decision VARCHAR(16),
    comment TEXT,
    decided_at TIMESTAMPTZ,
    delegated_to VARCHAR(200),
    due_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (cr_id, step_order)
);

CREATE TABLE IF NOT EXISTS argus_change_consumer (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    consumer_name VARCHAR(200) NOT NULL,
    consumer_type VARCHAR(32) NOT NULL,
    usage VARCHAR(64),
    criticality VARCHAR(16) NOT NULL,
    contact_emails VARCHAR(2000),
    webhook_url VARCHAR(500),
    slack_channel VARCHAR(200),
    auto_detected BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (dataset_id, consumer_name)
);

CREATE TABLE IF NOT EXISTS argus_change_referrer (
    id SERIAL PRIMARY KEY,
    cr_id INTEGER NOT NULL REFERENCES argus_change_request(id) ON DELETE CASCADE,
    name VARCHAR(200),
    email VARCHAR(300),
    channel VARCHAR(16) NOT NULL,
    slack_target VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_argus_change_referrer_cr_id ON argus_change_referrer USING btree (cr_id);

CREATE TABLE IF NOT EXISTS argus_change_notification_log (
    id SERIAL PRIMARY KEY,
    cr_id INTEGER NOT NULL REFERENCES argus_change_request(id) ON DELETE CASCADE,
    consumer_id INTEGER REFERENCES argus_change_consumer(id) ON DELETE CASCADE,
    referrer_id INTEGER REFERENCES argus_change_referrer(id) ON DELETE CASCADE,
    recipient VARCHAR(300),
    channel VARCHAR(32) NOT NULL,
    stage VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    sent_at TIMESTAMPTZ,
    acked_at TIMESTAMPTZ,
    ack_comment TEXT,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS argus_permissions (
    id SERIAL PRIMARY KEY,
    kind VARCHAR(10) NOT NULL,
    perm_key VARCHAR(100) NOT NULL,
    role_id VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (kind, perm_key, role_id)
);

CREATE TABLE IF NOT EXISTS catalog_ai_agent_status_history (
    id SERIAL PRIMARY KEY,
    agent_id INTEGER NOT NULL REFERENCES catalog_ai_agents(id) ON DELETE CASCADE,
    from_status VARCHAR(20),
    to_status VARCHAR(20) NOT NULL,
    note TEXT,
    changed_by VARCHAR(200),
    changed_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_catalog_ai_agent_status_history_agent_id ON catalog_ai_agent_status_history USING btree (agent_id);
CREATE INDEX IF NOT EXISTS ix_catalog_ai_agent_status_history_changed_at ON catalog_ai_agent_status_history USING btree (changed_at);

CREATE TABLE IF NOT EXISTS catalog_apis (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    urn VARCHAR(500) NOT NULL UNIQUE,
    display_name VARCHAR(255),
    description TEXT,
    version VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    owner_email VARCHAR(200),
    department VARCHAR(200),
    category VARCHAR(100),
    protocol VARCHAR(30),
    source VARCHAR(10) NOT NULL,
    spec_format VARCHAR(20),
    base_url VARCHAR(1000),
    base_url_overridden VARCHAR(5),
    contract_text TEXT,
    contract_url VARCHAR(1000),
    certification VARCHAR(20),
    tier VARCHAR(20),
    tags JSON,
    note TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    created_by VARCHAR(200),
    updated_by VARCHAR(200)
);

CREATE TABLE IF NOT EXISTS catalog_api_alerts (
    id SERIAL PRIMARY KEY,
    api_id INTEGER NOT NULL REFERENCES catalog_apis(id) ON DELETE CASCADE,
    from_spec_id INTEGER,
    to_spec_id INTEGER,
    from_version VARCHAR(50),
    to_version VARCHAR(50),
    severity VARCHAR(16) NOT NULL,
    breaking_count INTEGER NOT NULL,
    summary VARCHAR(500) NOT NULL,
    detail TEXT,
    status VARCHAR(20) NOT NULL,
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT now(),
    acknowledged_by VARCHAR(200),
    acknowledged_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_catalog_api_alerts_api_id ON catalog_api_alerts USING btree (api_id);
CREATE INDEX IF NOT EXISTS ix_catalog_api_alerts_created_at ON catalog_api_alerts USING btree (created_at);

CREATE TABLE IF NOT EXISTS catalog_api_credentials (
    id SERIAL PRIMARY KEY,
    api_id INTEGER NOT NULL REFERENCES catalog_apis(id) ON DELETE CASCADE,
    scheme_name VARCHAR(100),
    label VARCHAR(200) NOT NULL,
    type VARCHAR(30) NOT NULL,
    secret TEXT NOT NULL,
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_catalog_api_credentials_api_id ON catalog_api_credentials USING btree (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_endpoints (
    id SERIAL PRIMARY KEY,
    api_id INTEGER NOT NULL REFERENCES catalog_apis(id) ON DELETE CASCADE,
    method VARCHAR(40) NOT NULL,
    path VARCHAR(1000) NOT NULL,
    operation_id VARCHAR(255),
    summary TEXT,
    description TEXT,
    tags JSON,
    parameters JSON,
    request_body JSON,
    responses JSON,
    security JSON,
    extra JSON,
    sort_order INTEGER
);
CREATE INDEX IF NOT EXISTS ix_catalog_api_endpoints_api_id ON catalog_api_endpoints USING btree (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_favorites (
    id SERIAL PRIMARY KEY,
    user_key VARCHAR(200) NOT NULL,
    api_id INTEGER NOT NULL REFERENCES catalog_apis(id) ON DELETE CASCADE,
    method VARCHAR(10) NOT NULL,
    path VARCHAR(2000) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (user_key, api_id, method, path)
);
CREATE INDEX IF NOT EXISTS ix_catalog_api_favorites_api_id ON catalog_api_favorites USING btree (api_id);
CREATE INDEX IF NOT EXISTS ix_catalog_api_favorites_user_key ON catalog_api_favorites USING btree (user_key);

CREATE TABLE IF NOT EXISTS catalog_api_invocations (
    id SERIAL PRIMARY KEY,
    api_id INTEGER NOT NULL REFERENCES catalog_apis(id) ON DELETE CASCADE,
    method VARCHAR(10) NOT NULL,
    url VARCHAR(2000) NOT NULL,
    status_code INTEGER NOT NULL,
    ok VARCHAR(5) NOT NULL,
    latency_ms INTEGER NOT NULL,
    error TEXT,
    called_by VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT now(),
    endpoint_method VARCHAR(10),
    endpoint_path VARCHAR(2000),
    request_input JSON
);
CREATE INDEX IF NOT EXISTS ix_catalog_api_invocations_api_id ON catalog_api_invocations USING btree (api_id);
CREATE INDEX IF NOT EXISTS ix_catalog_api_invocations_created_at ON catalog_api_invocations USING btree (created_at);

CREATE TABLE IF NOT EXISTS catalog_api_lineage (
    id SERIAL PRIMARY KEY,
    api_id INTEGER NOT NULL REFERENCES catalog_apis(id) ON DELETE CASCADE,
    relation VARCHAR(20) NOT NULL,
    target_type VARCHAR(20) NOT NULL,
    target_ref VARCHAR(300) NOT NULL,
    target_label VARCHAR(300),
    note TEXT,
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (api_id, relation, target_type, target_ref)
);
CREATE INDEX IF NOT EXISTS ix_catalog_api_lineage_api_id ON catalog_api_lineage USING btree (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_security_schemes (
    id SERIAL PRIMARY KEY,
    api_id INTEGER NOT NULL REFERENCES catalog_apis(id) ON DELETE CASCADE,
    scheme_name VARCHAR(100) NOT NULL,
    type VARCHAR(30),
    config JSON
);
CREATE INDEX IF NOT EXISTS ix_catalog_api_security_schemes_api_id ON catalog_api_security_schemes USING btree (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_servers (
    id SERIAL PRIMARY KEY,
    api_id INTEGER NOT NULL REFERENCES catalog_apis(id) ON DELETE CASCADE,
    url VARCHAR(1000) NOT NULL,
    description VARCHAR(500),
    env VARCHAR(20)
);
CREATE INDEX IF NOT EXISTS ix_catalog_api_servers_api_id ON catalog_api_servers USING btree (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_specs (
    id SERIAL PRIMARY KEY,
    api_id INTEGER NOT NULL REFERENCES catalog_apis(id) ON DELETE CASCADE,
    version VARCHAR(50) NOT NULL,
    format VARCHAR(20),
    raw TEXT,
    parsed JSON,
    source_url VARCHAR(1000),
    is_current VARCHAR(5) NOT NULL DEFAULT 'true',
    created_at TIMESTAMPTZ DEFAULT now(),
    created_by VARCHAR(200),
    UNIQUE (api_id, version)
);
CREATE INDEX IF NOT EXISTS ix_catalog_api_specs_api_id ON catalog_api_specs USING btree (api_id);

CREATE TABLE IF NOT EXISTS catalog_api_status_history (
    id SERIAL PRIMARY KEY,
    api_id INTEGER NOT NULL REFERENCES catalog_apis(id) ON DELETE CASCADE,
    from_status VARCHAR(20),
    to_status VARCHAR(20) NOT NULL,
    note TEXT,
    changed_by VARCHAR(200),
    changed_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_catalog_api_status_history_changed_at ON catalog_api_status_history USING btree (changed_at);
CREATE INDEX IF NOT EXISTS ix_catalog_api_status_history_api_id ON catalog_api_status_history USING btree (api_id);

CREATE TABLE IF NOT EXISTS catalog_taxonomies (
    id SERIAL PRIMARY KEY,
    code VARCHAR(100) UNIQUE,
    name VARCHAR(200) NOT NULL UNIQUE,
    description TEXT,
    sort_order INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog_categories (
    id SERIAL PRIMARY KEY,
    taxonomy_id INTEGER NOT NULL REFERENCES catalog_taxonomies(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES catalog_categories(id) ON DELETE RESTRICT,
    code VARCHAR(100),
    name VARCHAR(200) NOT NULL,
    description TEXT,
    sort_order INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (taxonomy_id, parent_id, name)
);
CREATE INDEX IF NOT EXISTS ix_catalog_categories_taxonomy_id ON catalog_categories USING btree (taxonomy_id);
CREATE INDEX IF NOT EXISTS ix_catalog_categories_parent_id ON catalog_categories USING btree (parent_id);

CREATE TABLE IF NOT EXISTS catalog_dataset_categories (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES catalog_categories(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (dataset_id, category_id)
);
CREATE INDEX IF NOT EXISTS ix_catalog_dataset_categories_category_id ON catalog_dataset_categories USING btree (category_id);
CREATE INDEX IF NOT EXISTS ix_catalog_dataset_categories_dataset_id ON catalog_dataset_categories USING btree (dataset_id);

CREATE TABLE IF NOT EXISTS catalog_dataset_urn_alias (
    old_urn VARCHAR(500) PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES catalog_datasets(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_catalog_dataset_urn_alias_dataset_id ON catalog_dataset_urn_alias USING btree (dataset_id);

CREATE TABLE IF NOT EXISTS catalog_organizations (
    id SERIAL PRIMARY KEY,
    code VARCHAR(100) UNIQUE,
    name VARCHAR(200) NOT NULL,
    parent_id INTEGER REFERENCES catalog_organizations(id) ON DELETE RESTRICT,
    description TEXT,
    sort_order INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (parent_id, name)
);
CREATE INDEX IF NOT EXISTS ix_catalog_organizations_parent_id ON catalog_organizations USING btree (parent_id);

CREATE TABLE IF NOT EXISTS catalog_systems (
    id SERIAL PRIMARY KEY,
    code VARCHAR(100) UNIQUE,
    name VARCHAR(200) NOT NULL,
    org_id INTEGER REFERENCES catalog_organizations(id) ON DELETE RESTRICT,
    summary VARCHAR(200),
    description TEXT,
    owner VARCHAR(200),
    status VARCHAR(20) NOT NULL,
    sort_order INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (org_id, name)
);
CREATE INDEX IF NOT EXISTS ix_catalog_systems_org_id ON catalog_systems USING btree (org_id);

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
ALTER TABLE catalog_datasources ADD COLUMN IF NOT EXISTS system_id INTEGER REFERENCES catalog_systems(id) ON DELETE SET NULL;
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
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS last_ingested_at TIMESTAMPTZ;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS retention_days INTEGER;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS purge_days INTEGER;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS data_category VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS data_format VARCHAR(30);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS compression VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS encoding VARCHAR(30);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS row_count BIGINT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS byte_size BIGINT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS file_count INTEGER;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS sensitivity VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS contains_pii VARCHAR(5);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS pii_fields TEXT;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS compliance_tags VARCHAR(255);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS tier VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS certification VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS steward VARCHAR(255);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS view_count INTEGER;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS query_count INTEGER;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS quality_score INTEGER;
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS quality_status VARCHAR(20);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS show_quality_score VARCHAR(5);
ALTER TABLE catalog_datasets ADD COLUMN IF NOT EXISTS note TEXT;

-- ===========================================================================
-- 테이블·컬럼 주석 (한글) — ORM 모델 기준 자동 생성
-- ===========================================================================

-- argus_alert_notification
COMMENT ON COLUMN argus_alert_notification.id IS 'PK';
COMMENT ON COLUMN argus_alert_notification.alert_id IS '대상 알림 (argus_lineage_alert(id) 참조)';
COMMENT ON COLUMN argus_alert_notification.channel IS '전달 채널 (IN_APP/WEBHOOK/EMAIL)';
COMMENT ON COLUMN argus_alert_notification.recipient IS '수신자 (이메일/URL/사용자 식별자)';
COMMENT ON COLUMN argus_alert_notification.sent_at IS '전송 시각';
COMMENT ON COLUMN argus_alert_notification.status IS '전송 상태 (SENT/FAILED 등)';

-- argus_alert_rule
COMMENT ON COLUMN argus_alert_rule.id IS 'PK';
COMMENT ON COLUMN argus_alert_rule.rule_name IS '규칙 이름';
COMMENT ON COLUMN argus_alert_rule.description IS '규칙 설명';
COMMENT ON COLUMN argus_alert_rule.scope_type IS '감시 범위 (DATASET/TAG/LINEAGE/DATASOURCE/ALL)';
COMMENT ON COLUMN argus_alert_rule.scope_id IS '감시 대상 ID (ALL이면 NULL)';
COMMENT ON COLUMN argus_alert_rule.trigger_type IS '트리거 유형 (ANY/SCHEMA_CHANGE/COLUMN_WATCH/MAPPING_BROKEN/SYNC_STALE/QUALITY_FAILED)';
COMMENT ON COLUMN argus_alert_rule.trigger_config IS '트리거 조건 상세 (JSON)';
COMMENT ON COLUMN argus_alert_rule.severity_override IS '심각도 강제 지정 (NULL이면 자동 판정)';
COMMENT ON COLUMN argus_alert_rule.channels IS '알림 채널 (콤마 구분, 예: IN_APP/WEBHOOK/EMAIL)';
COMMENT ON COLUMN argus_alert_rule.notify_owners IS '데이터셋 Owner 알림 여부 (true/false)';
COMMENT ON COLUMN argus_alert_rule.webhook_url IS 'WEBHOOK 전송 URL';
COMMENT ON COLUMN argus_alert_rule.subscribers IS '구독자 목록 (콤마 구분)';
COMMENT ON COLUMN argus_alert_rule.is_active IS '활성화 여부 (true/false)';
COMMENT ON COLUMN argus_alert_rule.created_by IS '생성자';
COMMENT ON COLUMN argus_alert_rule.created_at IS '생성 시각';
COMMENT ON COLUMN argus_alert_rule.updated_at IS '수정 시각';

-- argus_change_approval_step
COMMENT ON TABLE argus_change_approval_step IS '변경 요청(CR)의 결재 단계별 처리 이력을 저장';
COMMENT ON COLUMN argus_change_approval_step.id IS 'PK';
COMMENT ON COLUMN argus_change_approval_step.cr_id IS '변경 요청 ID (argus_change_request(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN argus_change_approval_step.step_order IS '결재 단계 순서 (1부터 시작)';
COMMENT ON COLUMN argus_change_approval_step.approver IS '결재자 식별자(사용자/이메일)';
COMMENT ON COLUMN argus_change_approval_step.role IS '결재자 역할 (OWNER/DOMAIN_LEAD/DG_COMMITTEE 등)';
COMMENT ON COLUMN argus_change_approval_step.decision IS '결재 결과 (APPROVED/REJECTED/DELEGATED/PENDING)';
COMMENT ON COLUMN argus_change_approval_step.comment IS '결재 의견/사유';
COMMENT ON COLUMN argus_change_approval_step.decided_at IS '결재 처리 시각';
COMMENT ON COLUMN argus_change_approval_step.delegated_to IS '위임 결재 시 대결자 식별자';
COMMENT ON COLUMN argus_change_approval_step.due_at IS '결재 처리 기한';
COMMENT ON COLUMN argus_change_approval_step.created_at IS '생성 시각';

-- argus_change_consumer
COMMENT ON TABLE argus_change_consumer IS '데이터셋별 소비자(시스템/조직) 등록부 — 스키마 변경 시 통지 대상 기준';
COMMENT ON COLUMN argus_change_consumer.id IS 'PK';
COMMENT ON COLUMN argus_change_consumer.dataset_id IS '소비 대상 데이터셋 (catalog_datasets(id) 참조)';
COMMENT ON COLUMN argus_change_consumer.consumer_name IS '소비자 시스템 또는 조직 이름';
COMMENT ON COLUMN argus_change_consumer.consumer_type IS '소비자 유형 (SYSTEM/ORGANIZATION/TEAM)';
COMMENT ON COLUMN argus_change_consumer.usage IS '사용 용도 (ETL/DASHBOARD/ML_TRAINING/REGULATORY 등)';
COMMENT ON COLUMN argus_change_consumer.criticality IS '중요도 (MISSION_CRITICAL/IMPORTANT/NORMAL)';
COMMENT ON COLUMN argus_change_consumer.contact_emails IS '연락 이메일 목록 (콤마 구분)';
COMMENT ON COLUMN argus_change_consumer.webhook_url IS '통지용 Webhook URL';
COMMENT ON COLUMN argus_change_consumer.slack_channel IS '통지용 Slack 채널';
COMMENT ON COLUMN argus_change_consumer.auto_detected IS '쿼리 로그 기반 자동 탐지 등록 여부';
COMMENT ON COLUMN argus_change_consumer.created_at IS '생성 시각';
COMMENT ON COLUMN argus_change_consumer.updated_at IS '수정 시각';

-- argus_change_notification_log
COMMENT ON TABLE argus_change_notification_log IS '변경 요청에 대한 다운스트림 소비자·참조자별 통지 발송 및 ACK 기록';
COMMENT ON COLUMN argus_change_notification_log.id IS 'PK';
COMMENT ON COLUMN argus_change_notification_log.cr_id IS '변경 요청 ID (argus_change_request(id) 참조, CASCADE)';
COMMENT ON COLUMN argus_change_notification_log.consumer_id IS '통지 대상 소비자 ID (argus_change_consumer(id) 참조, nullable)';
COMMENT ON COLUMN argus_change_notification_log.referrer_id IS '통지 대상 참조자(CC) ID (argus_change_referrer(id) 참조, nullable)';
COMMENT ON COLUMN argus_change_notification_log.recipient IS '수신자 (이메일/채널 대상)';
COMMENT ON COLUMN argus_change_notification_log.channel IS '통지 채널 (EMAIL/SLACK/MATTERMOST/WEBHOOK/IN_APP)';
COMMENT ON COLUMN argus_change_notification_log.stage IS '통지 단계 (SUBMITTED/T_MINUS_30/T_MINUS_7/APPLIED 등)';
COMMENT ON COLUMN argus_change_notification_log.status IS '통지 상태 (PENDING/SENT/DELIVERED/ACKED/FAILED/REJECTED/DEFERRED)';
COMMENT ON COLUMN argus_change_notification_log.sent_at IS '발송 시각';
COMMENT ON COLUMN argus_change_notification_log.acked_at IS '수신 확인(ACK) 시각';
COMMENT ON COLUMN argus_change_notification_log.ack_comment IS '수신 확인 코멘트';
COMMENT ON COLUMN argus_change_notification_log.error IS '발송 실패 오류 메시지';
COMMENT ON COLUMN argus_change_notification_log.created_at IS '생성 시각';

-- argus_change_referrer
COMMENT ON TABLE argus_change_referrer IS '변경 요청(CR)의 참조자(CC) — 결재권 없이 통지만 받는 대상';
COMMENT ON COLUMN argus_change_referrer.id IS 'PK';
COMMENT ON COLUMN argus_change_referrer.cr_id IS '변경 요청 ID (argus_change_request(id) 참조, CASCADE)';
COMMENT ON COLUMN argus_change_referrer.name IS '참조자 표시명 (선택)';
COMMENT ON COLUMN argus_change_referrer.email IS '참조자 이메일 (EMAIL 채널 대상)';
COMMENT ON COLUMN argus_change_referrer.channel IS '통지 채널 (EMAIL/SLACK/MATTERMOST)';
COMMENT ON COLUMN argus_change_referrer.slack_target IS 'Slack/Mattermost 채널 또는 멘션 대상 (선택)';
COMMENT ON COLUMN argus_change_referrer.created_at IS '생성 시각';

-- argus_change_request
COMMENT ON TABLE argus_change_request IS '스키마 변경 요청(CR) 마스터 — 결재 흐름의 중심 엔티티';
COMMENT ON COLUMN argus_change_request.id IS 'PK';
COMMENT ON COLUMN argus_change_request.cr_code IS '변경 요청 코드 (예: CR-2026-0001, 고유)';
COMMENT ON COLUMN argus_change_request.title IS '변경 요청 제목';
COMMENT ON COLUMN argus_change_request.description IS '변경 요청 상세 설명';
COMMENT ON COLUMN argus_change_request.dataset_id IS '대상 데이터셋 (catalog_datasets(id) 참조)';
COMMENT ON COLUMN argus_change_request.change_type IS '변경 유형 (BREAKING/NON_BREAKING/ADDITIVE/COSMETIC)';
COMMENT ON COLUMN argus_change_request.priority IS '우선순위 (EMERGENCY/HIGH/NORMAL/LOW)';
COMMENT ON COLUMN argus_change_request.status IS '결재 상태 (기본 DRAFT)';
COMMENT ON COLUMN argus_change_request.schema_before IS '변경 전 스키마 (JSON)';
COMMENT ON COLUMN argus_change_request.schema_after IS '변경 후 스키마 (JSON, COSMETIC은 생략 가능)';
COMMENT ON COLUMN argus_change_request.impact_report IS '자동 생성된 영향 분석 결과 (JSON)';
COMMENT ON COLUMN argus_change_request.rollback_plan IS '롤백 계획 (필수)';
COMMENT ON COLUMN argus_change_request.business_justification IS '비즈니스 정당성 근거 (필수)';
COMMENT ON COLUMN argus_change_request.scheduled_at IS '적용 예정 시각';
COMMENT ON COLUMN argus_change_request.applied_at IS '실제 적용 시각';
COMMENT ON COLUMN argus_change_request.workflow_id IS 'Temporal 워크플로우 ID';
COMMENT ON COLUMN argus_change_request.requested_by IS '변경 요청자';
COMMENT ON COLUMN argus_change_request.created_at IS '생성 시각';
COMMENT ON COLUMN argus_change_request.updated_at IS '수정 시각';

-- argus_collector_hive_query_history
COMMENT ON COLUMN argus_collector_hive_query_history.id IS 'PK';
COMMENT ON COLUMN argus_collector_hive_query_history.query_id IS 'Hive 쿼리 식별자';
COMMENT ON COLUMN argus_collector_hive_query_history.short_username IS '실행 사용자 짧은 이름';
COMMENT ON COLUMN argus_collector_hive_query_history.username IS '실행 사용자 전체 이름';
COMMENT ON COLUMN argus_collector_hive_query_history.operation_name IS '작업 유형 (QUERY/DDL 등)';
COMMENT ON COLUMN argus_collector_hive_query_history.start_time IS '쿼리 시작 시각(epoch ms)';
COMMENT ON COLUMN argus_collector_hive_query_history.end_time IS '쿼리 종료 시각(epoch ms)';
COMMENT ON COLUMN argus_collector_hive_query_history.duration_ms IS '쿼리 수행 시간(ms)';
COMMENT ON COLUMN argus_collector_hive_query_history.query IS '실행된 SQL 원문';
COMMENT ON COLUMN argus_collector_hive_query_history.status IS '실행 상태 (SUCCESS/FAILED 등)';
COMMENT ON COLUMN argus_collector_hive_query_history.error_msg IS '실패 시 오류 메시지';
COMMENT ON COLUMN argus_collector_hive_query_history.received_at IS '수집 서버 수신 시각';

-- argus_collector_impala_query_history
COMMENT ON COLUMN argus_collector_impala_query_history.id IS 'PK';
COMMENT ON COLUMN argus_collector_impala_query_history.query_id IS 'Impala 쿼리 고유 식별자 (unique)';
COMMENT ON COLUMN argus_collector_impala_query_history.query_type IS '쿼리 유형 (DML/DDL/QUERY/UNKNOWN)';
COMMENT ON COLUMN argus_collector_impala_query_history.query_state IS '쿼리 실행 상태 (FINISHED/EXCEPTION 등)';
COMMENT ON COLUMN argus_collector_impala_query_history.statement IS '실행된 SQL 문';
COMMENT ON COLUMN argus_collector_impala_query_history.database IS '쿼리 대상 데이터베이스명';
COMMENT ON COLUMN argus_collector_impala_query_history.username IS '실효 사용자 (delegate 지정 시 그 값, 아니면 접속 사용자)';
COMMENT ON COLUMN argus_collector_impala_query_history.coordinator_host IS '쿼리를 조정한 coordinator 호스트';
COMMENT ON COLUMN argus_collector_impala_query_history.start_time IS '쿼리 시작 시각';
COMMENT ON COLUMN argus_collector_impala_query_history.end_time IS '쿼리 종료 시각';
COMMENT ON COLUMN argus_collector_impala_query_history.duration_ms IS '쿼리 실행 소요 시간(ms)';
COMMENT ON COLUMN argus_collector_impala_query_history.rows_produced IS '쿼리가 생성한 행 수';
COMMENT ON COLUMN argus_collector_impala_query_history.datasource_id IS '수집 출처 데이터소스 식별자';
COMMENT ON COLUMN argus_collector_impala_query_history.received_at IS '수집 적재 시각';

-- argus_collector_starrocks_query_history
COMMENT ON COLUMN argus_collector_starrocks_query_history.id IS 'PK';
COMMENT ON COLUMN argus_collector_starrocks_query_history.query_id IS 'StarRocks 쿼리 고유 ID (unique)';
COMMENT ON COLUMN argus_collector_starrocks_query_history.statement IS '실행된 SQL 원문';
COMMENT ON COLUMN argus_collector_starrocks_query_history.digest IS '쿼리 지문(fingerprint) 해시';
COMMENT ON COLUMN argus_collector_starrocks_query_history.username IS '실제 적용된 유효 사용자명';
COMMENT ON COLUMN argus_collector_starrocks_query_history.authorized_user IS '인증된 사용자명';
COMMENT ON COLUMN argus_collector_starrocks_query_history.client_ip IS '클라이언트 IP';
COMMENT ON COLUMN argus_collector_starrocks_query_history.database IS '실행 대상 데이터베이스';
COMMENT ON COLUMN argus_collector_starrocks_query_history.catalog IS '실행 대상 카탈로그';
COMMENT ON COLUMN argus_collector_starrocks_query_history.state IS '쿼리 종료 상태 (EOF/ERR/OK)';
COMMENT ON COLUMN argus_collector_starrocks_query_history.error_code IS '오류 코드/메시지';
COMMENT ON COLUMN argus_collector_starrocks_query_history.query_time_ms IS '쿼리 실행 시간(ms)';
COMMENT ON COLUMN argus_collector_starrocks_query_history.scan_rows IS '스캔한 행 수';
COMMENT ON COLUMN argus_collector_starrocks_query_history.scan_bytes IS '스캔한 바이트 수';
COMMENT ON COLUMN argus_collector_starrocks_query_history.return_rows IS '반환한 행 수';
COMMENT ON COLUMN argus_collector_starrocks_query_history.cpu_cost_ns IS 'CPU 비용(ns)';
COMMENT ON COLUMN argus_collector_starrocks_query_history.mem_cost_bytes IS '메모리 비용(bytes)';
COMMENT ON COLUMN argus_collector_starrocks_query_history.pending_time_ms IS '대기 시간(ms)';
COMMENT ON COLUMN argus_collector_starrocks_query_history.is_query IS '쿼리 여부 (1=query, 0=non-query)';
COMMENT ON COLUMN argus_collector_starrocks_query_history.fe_ip IS '처리한 FE 노드 IP';
COMMENT ON COLUMN argus_collector_starrocks_query_history.event_timestamp IS 'AuditEvent 발생 시각(epoch millis)';
COMMENT ON COLUMN argus_collector_starrocks_query_history.datasource_id IS '데이터소스 식별자';
COMMENT ON COLUMN argus_collector_starrocks_query_history.received_at IS '수집 시각';

-- argus_collector_trino_query_history
COMMENT ON COLUMN argus_collector_trino_query_history.id IS 'PK';
COMMENT ON COLUMN argus_collector_trino_query_history.query_id IS 'Trino 쿼리 고유 ID (unique)';
COMMENT ON COLUMN argus_collector_trino_query_history.query_state IS '쿼리 종료 상태 (FINISHED/FAILED)';
COMMENT ON COLUMN argus_collector_trino_query_history.query_type IS '쿼리 유형 (SELECT/INSERT 등)';
COMMENT ON COLUMN argus_collector_trino_query_history.statement IS '실행된 SQL 원문';
COMMENT ON COLUMN argus_collector_trino_query_history.plan IS '쿼리 실행 계획(plan)';
COMMENT ON COLUMN argus_collector_trino_query_history.username IS '실효 사용자명';
COMMENT ON COLUMN argus_collector_trino_query_history.principal IS '인증 주체 (Kerberos/OAuth principal)';
COMMENT ON COLUMN argus_collector_trino_query_history.source IS '클라이언트 도구 (trino-cli 등)';
COMMENT ON COLUMN argus_collector_trino_query_history.catalog IS '대상 카탈로그명';
COMMENT ON COLUMN argus_collector_trino_query_history.schema IS '대상 스키마명';
COMMENT ON COLUMN argus_collector_trino_query_history.remote_client_address IS '클라이언트 원격 주소(IP)';
COMMENT ON COLUMN argus_collector_trino_query_history.create_time IS '쿼리 생성 시각';
COMMENT ON COLUMN argus_collector_trino_query_history.execution_start_time IS '실행 시작 시각';
COMMENT ON COLUMN argus_collector_trino_query_history.end_time IS '실행 종료 시각';
COMMENT ON COLUMN argus_collector_trino_query_history.wall_time_ms IS '총 실행 경과 시간(ms)';
COMMENT ON COLUMN argus_collector_trino_query_history.cpu_time_ms IS 'CPU 사용 시간(ms)';
COMMENT ON COLUMN argus_collector_trino_query_history.physical_input_bytes IS '물리 입력 데이터 크기(bytes)';
COMMENT ON COLUMN argus_collector_trino_query_history.physical_input_rows IS '물리 입력 행 수';
COMMENT ON COLUMN argus_collector_trino_query_history.output_bytes IS '출력 데이터 크기(bytes)';
COMMENT ON COLUMN argus_collector_trino_query_history.output_rows IS '출력 행 수';
COMMENT ON COLUMN argus_collector_trino_query_history.peak_memory_bytes IS '최대 메모리 사용량(bytes)';
COMMENT ON COLUMN argus_collector_trino_query_history.error_code IS '오류 코드';
COMMENT ON COLUMN argus_collector_trino_query_history.error_message IS '오류 메시지';
COMMENT ON COLUMN argus_collector_trino_query_history.inputs_json IS '입력 테이블 목록 JSON ([{catalog,schema,table,columns}])';
COMMENT ON COLUMN argus_collector_trino_query_history.output_json IS '출력 테이블 정보 JSON ({catalog,schema,table,columns})';
COMMENT ON COLUMN argus_collector_trino_query_history.datasource_id IS '수집 대상 데이터소스 식별자';
COMMENT ON COLUMN argus_collector_trino_query_history.received_at IS '수집(수신) 시각';

-- argus_column_lineage
COMMENT ON COLUMN argus_column_lineage.id IS 'PK';
COMMENT ON COLUMN argus_column_lineage.query_lineage_id IS '소속 쿼리 리니지 ID (argus_query_lineage(id) 참조)';
COMMENT ON COLUMN argus_column_lineage.source_column IS '원본(source) 컬럼명';
COMMENT ON COLUMN argus_column_lineage.target_column IS '대상(target) 컬럼명';
COMMENT ON COLUMN argus_column_lineage.transform_type IS '변환 유형 (기본 DIRECT)';

-- argus_data_pipeline
COMMENT ON COLUMN argus_data_pipeline.id IS 'PK';
COMMENT ON COLUMN argus_data_pipeline.pipeline_name IS '파이프라인 고유 이름 (unique)';
COMMENT ON COLUMN argus_data_pipeline.description IS '파이프라인 설명';
COMMENT ON COLUMN argus_data_pipeline.pipeline_type IS '파이프라인 유형 (ETL/FILE_EXPORT/CDC/REPLICATION/API/MANUAL)';
COMMENT ON COLUMN argus_data_pipeline.schedule IS '실행 주기 (cron 표현식, 예: "0 2 * * *")';
COMMENT ON COLUMN argus_data_pipeline.owner IS '파이프라인 담당자';
COMMENT ON COLUMN argus_data_pipeline.status IS '파이프라인 상태 (ACTIVE/INACTIVE/DEPRECATED)';
COMMENT ON COLUMN argus_data_pipeline.created_at IS '생성 시각';
COMMENT ON COLUMN argus_data_pipeline.updated_at IS '수정 시각';

-- argus_dataset_column_mapping
COMMENT ON COLUMN argus_dataset_column_mapping.id IS 'PK';
COMMENT ON COLUMN argus_dataset_column_mapping.dataset_lineage_id IS '소속 리니지 (argus_dataset_lineage(id) 참조, CASCADE)';
COMMENT ON COLUMN argus_dataset_column_mapping.source_column IS '원본 컬럼명';
COMMENT ON COLUMN argus_dataset_column_mapping.target_column IS '대상 컬럼명';
COMMENT ON COLUMN argus_dataset_column_mapping.transform_type IS '변환 유형 (DIRECT/CAST/EXPRESSION/DERIVED)';
COMMENT ON COLUMN argus_dataset_column_mapping.transform_expr IS '변환 수식 (CAST/EXPRESSION 시, 예: CAST(emp_id AS BIGINT))';

-- argus_dataset_lineage
COMMENT ON COLUMN argus_dataset_lineage.id IS 'PK';
COMMENT ON COLUMN argus_dataset_lineage.source_dataset_id IS '원본(데이터 제공) 데이터셋, catalog_datasets(id) 참조';
COMMENT ON COLUMN argus_dataset_lineage.target_dataset_id IS '대상(데이터 수신) 데이터셋, catalog_datasets(id) 참조';
COMMENT ON COLUMN argus_dataset_lineage.relation_type IS '관계 유형 (ETL/FILE_EXPORT/CDC/REPLICATION/DERIVED/READ_WRITE)';
COMMENT ON COLUMN argus_dataset_lineage.lineage_source IS '리니지 출처 (QUERY_AGGREGATED/PIPELINE/MANUAL)';
COMMENT ON COLUMN argus_dataset_lineage.pipeline_id IS '파이프라인 참조 (PIPELINE 출처일 때), argus_data_pipeline(id) 참조';
COMMENT ON COLUMN argus_dataset_lineage.description IS '리니지 관계 설명';
COMMENT ON COLUMN argus_dataset_lineage.created_by IS '등록한 사용자';
COMMENT ON COLUMN argus_dataset_lineage.query_count IS '이 관계를 확인한 쿼리 수 (자동 수집 시)';
COMMENT ON COLUMN argus_dataset_lineage.last_query_id IS '마지막으로 확인된 쿼리 ID';
COMMENT ON COLUMN argus_dataset_lineage.last_seen_at IS '마지막 확인 시각';
COMMENT ON COLUMN argus_dataset_lineage.created_at IS '생성 시각';

-- argus_lineage_alert
COMMENT ON COLUMN argus_lineage_alert.id IS 'PK';
COMMENT ON COLUMN argus_lineage_alert.alert_type IS '알림 유형 (SCHEMA_CHANGE/LINEAGE_BROKEN/SYNC_FAILED/QUALITY_FAILED)';
COMMENT ON COLUMN argus_lineage_alert.severity IS '심각도 (INFO/WARNING/BREAKING)';
COMMENT ON COLUMN argus_lineage_alert.source_dataset_id IS '변경 발생 원본 데이터셋, catalog_datasets(id) 참조';
COMMENT ON COLUMN argus_lineage_alert.affected_dataset_id IS '영향받은 하위 데이터셋, catalog_datasets(id) 참조';
COMMENT ON COLUMN argus_lineage_alert.lineage_id IS '관련 리니지 관계, argus_dataset_lineage(id) 참조';
COMMENT ON COLUMN argus_lineage_alert.rule_id IS '이 알림을 생성한 규칙, argus_alert_rule(id) 참조';
COMMENT ON COLUMN argus_lineage_alert.change_summary IS '변경 요약 메시지';
COMMENT ON COLUMN argus_lineage_alert.change_detail IS '변경 상세 내용';
COMMENT ON COLUMN argus_lineage_alert.status IS '처리 상태 (OPEN/ACKNOWLEDGED/RESOLVED/DISMISSED)';
COMMENT ON COLUMN argus_lineage_alert.resolved_by IS '해결 처리자';
COMMENT ON COLUMN argus_lineage_alert.resolved_at IS '해결 시각';
COMMENT ON COLUMN argus_lineage_alert.created_at IS '생성 시각';

-- argus_permissions
COMMENT ON TABLE argus_permissions IS '역할별 메뉴/기능 허용 권한 매트릭스 (open-by-default 정책)';
COMMENT ON COLUMN argus_permissions.id IS 'PK';
COMMENT ON COLUMN argus_permissions.kind IS '권한 유형 (MENU/FEATURE)';
COMMENT ON COLUMN argus_permissions.perm_key IS '메뉴/기능 key (프런트 레지스트리와 일치)';
COMMENT ON COLUMN argus_permissions.role_id IS '허용 대상 역할 ID (argus-superuser/argus-user)';
COMMENT ON COLUMN argus_permissions.created_at IS '생성 시각';

-- argus_query_lineage
COMMENT ON COLUMN argus_query_lineage.id IS 'PK';
COMMENT ON COLUMN argus_query_lineage.query_hist_id IS '원본 쿼리 이력 ID (argus_query_history(id) 참조)';
COMMENT ON COLUMN argus_query_lineage.source_table IS '소스 테이블 정규화 이름 (schema.table)';
COMMENT ON COLUMN argus_query_lineage.target_table IS '타깃 테이블 정규화 이름 (schema.table)';
COMMENT ON COLUMN argus_query_lineage.source_dataset_id IS '소스 데이터셋 ID (catalog_datasets(id) 참조, 매칭 시)';
COMMENT ON COLUMN argus_query_lineage.target_dataset_id IS '타깃 데이터셋 ID (catalog_datasets(id) 참조, 매칭 시)';
COMMENT ON COLUMN argus_query_lineage.created_at IS '생성 시각';

-- argus_roles
COMMENT ON COLUMN argus_roles.id IS 'PK (auto-increment)';
COMMENT ON COLUMN argus_roles.name IS '화면 표시용 역할 이름 (예: Admin, Superuser, User)';
COMMENT ON COLUMN argus_roles.description IS '역할 설명 (선택)';
COMMENT ON COLUMN argus_roles.created_at IS '생성 시각';
COMMENT ON COLUMN argus_roles.updated_at IS '수정 시각';
COMMENT ON COLUMN argus_roles.role_id IS '역할 식별자, Keycloak realm role 이름과 1:1 매칭 (예: argus-admin), UNIQUE';

-- argus_user_preferences
COMMENT ON COLUMN argus_user_preferences.sub IS 'PK, 사용자 식별자 (로컬=argus_users.id 문자열, Keycloak=user UUID)';
COMMENT ON COLUMN argus_user_preferences.avatar_preset_id IS '선택한 아바타 preset 식별자';
COMMENT ON COLUMN argus_user_preferences.updated_at IS '수정 시각';

-- argus_users
COMMENT ON COLUMN argus_users.id IS 'PK';
COMMENT ON COLUMN argus_users.username IS '로그인 식별자 (UNIQUE)';
COMMENT ON COLUMN argus_users.email IS '이메일 주소 (UNIQUE)';
COMMENT ON COLUMN argus_users.first_name IS '이름';
COMMENT ON COLUMN argus_users.last_name IS '성';
COMMENT ON COLUMN argus_users.phone_number IS '연락처 전화번호 (선택)';
COMMENT ON COLUMN argus_users.password_hash IS '비밀번호 SHA-256 해시 (평문 저장 금지)';
COMMENT ON COLUMN argus_users.status IS '계정 상태 (active/inactive)';
COMMENT ON COLUMN argus_users.role_id IS '역할 참조, argus_roles(id) 참조';
COMMENT ON COLUMN argus_users.created_at IS '생성 시각';
COMMENT ON COLUMN argus_users.updated_at IS '수정 시각';

-- catalog_ai_agent_evals
COMMENT ON COLUMN catalog_ai_agent_evals.id IS 'PK';
COMMENT ON COLUMN catalog_ai_agent_evals.agent_id IS '평가 대상 에이전트 (catalog_ai_agents(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_ai_agent_evals.version IS '평가 대상 사양 버전 (SemVer)';
COMMENT ON COLUMN catalog_ai_agent_evals.eval_type IS '평가 유형 (accuracy/task_success/hallucination/safety/user_rating)';
COMMENT ON COLUMN catalog_ai_agent_evals.metric_key IS '지표 키 (측정 항목 식별자)';
COMMENT ON COLUMN catalog_ai_agent_evals.metric_value IS '지표 값 (0~1 정규화 권장)';
COMMENT ON COLUMN catalog_ai_agent_evals.dataset_ref IS '평가 데이터셋/케이스 참조';
COMMENT ON COLUMN catalog_ai_agent_evals.passed IS '합격 여부';
COMMENT ON COLUMN catalog_ai_agent_evals.notes IS '평가 비고';
COMMENT ON COLUMN catalog_ai_agent_evals.evaluated_at IS '평가 시각';
COMMENT ON COLUMN catalog_ai_agent_evals.created_by IS '평가 등록자';

-- catalog_ai_agent_hook_events
COMMENT ON COLUMN catalog_ai_agent_hook_events.id IS 'PK';
COMMENT ON COLUMN catalog_ai_agent_hook_events.agent_id IS '대상 AI 에이전트 (catalog_ai_agents(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_ai_agent_hook_events.occurred_at IS '훅 이벤트 발생 시각';
COMMENT ON COLUMN catalog_ai_agent_hook_events.stage IS '집행 단계 (access/pre_exec/post_exec)';
COMMENT ON COLUMN catalog_ai_agent_hook_events.decision IS '집행 결정 (allow/deny/mask/require_approval/approved/modified)';
COMMENT ON COLUMN catalog_ai_agent_hook_events.action_type IS '행위 유형 (tool_call/network_egress/data_write/budget_spend/browse 등)';
COMMENT ON COLUMN catalog_ai_agent_hook_events.target IS '집행 대상 (도메인/도구명/데이터셋 등)';
COMMENT ON COLUMN catalog_ai_agent_hook_events.policy_ref IS '발동된 정책 키 (network_allowlist/dlp_policies/hitl_config/guardrails 등)';
COMMENT ON COLUMN catalog_ai_agent_hook_events.reason IS '결정 사유 설명';
COMMENT ON COLUMN catalog_ai_agent_hook_events.session_id IS '세션 식별자';
COMMENT ON COLUMN catalog_ai_agent_hook_events.consumer IS '요청 소비자(호출 주체) 식별자';
COMMENT ON COLUMN catalog_ai_agent_hook_events.event_metadata IS '이벤트 부가 메타데이터 (JSONB)';

-- catalog_ai_agent_invocation_log
COMMENT ON COLUMN catalog_ai_agent_invocation_log.id IS 'PK';
COMMENT ON COLUMN catalog_ai_agent_invocation_log.agent_id IS '호출된 에이전트 (catalog_ai_agents(id) 참조, ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_ai_agent_invocation_log.invoked_at IS '호출 시각';
COMMENT ON COLUMN catalog_ai_agent_invocation_log.consumer IS '호출 주체 (사용자/팀/에이전트 식별자)';
COMMENT ON COLUMN catalog_ai_agent_invocation_log.status IS '호출 결과 상태 (success/error)';
COMMENT ON COLUMN catalog_ai_agent_invocation_log.error_type IS '실패 시 오류 유형';
COMMENT ON COLUMN catalog_ai_agent_invocation_log.latency_ms IS '응답 지연(ms)';
COMMENT ON COLUMN catalog_ai_agent_invocation_log.input_tokens IS '입력 토큰 수';
COMMENT ON COLUMN catalog_ai_agent_invocation_log.output_tokens IS '출력 토큰 수';
COMMENT ON COLUMN catalog_ai_agent_invocation_log.cost IS '호출 비용';
COMMENT ON COLUMN catalog_ai_agent_invocation_log.session_id IS '호출 세션 식별자';

-- catalog_ai_agent_lineage
COMMENT ON COLUMN catalog_ai_agent_lineage.id IS 'PK';
COMMENT ON COLUMN catalog_ai_agent_lineage.agent_id IS '대상 에이전트 (catalog_ai_agents(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_ai_agent_lineage.target_type IS '의존 대상 종류 (agent/model/dataset)';
COMMENT ON COLUMN catalog_ai_agent_lineage.target_ref IS '의존 대상 식별자 (name 또는 URN, 외부 대상 허용)';
COMMENT ON COLUMN catalog_ai_agent_lineage.relation IS '관계 종류 (depends_on/consumed_by/related)';
COMMENT ON COLUMN catalog_ai_agent_lineage.description IS '관계 설명';
COMMENT ON COLUMN catalog_ai_agent_lineage.created_at IS '생성 시각';

-- catalog_ai_agent_mcp_servers
COMMENT ON COLUMN catalog_ai_agent_mcp_servers.id IS 'PK';
COMMENT ON COLUMN catalog_ai_agent_mcp_servers.agent_id IS '소속 에이전트 (catalog_ai_agents(id) 참조, 삭제 시 CASCADE)';
COMMENT ON COLUMN catalog_ai_agent_mcp_servers.name IS 'MCP 서버 이름';
COMMENT ON COLUMN catalog_ai_agent_mcp_servers.url IS 'MCP 서버 접속 URL';
COMMENT ON COLUMN catalog_ai_agent_mcp_servers.auth_method IS '인증 방식 (API key/OAuth2.1/none)';
COMMENT ON COLUMN catalog_ai_agent_mcp_servers.description IS 'MCP 서버 설명';
COMMENT ON COLUMN catalog_ai_agent_mcp_servers.created_at IS '생성 시각';

-- catalog_ai_agent_status_history
COMMENT ON TABLE catalog_ai_agent_status_history IS 'AI 에이전트의 상태 변경 이력 (자동 기록)';
COMMENT ON COLUMN catalog_ai_agent_status_history.id IS 'PK';
COMMENT ON COLUMN catalog_ai_agent_status_history.agent_id IS '대상 에이전트 (catalog_ai_agents(id) 참조, 삭제 시 CASCADE)';
COMMENT ON COLUMN catalog_ai_agent_status_history.from_status IS '변경 전 상태 (draft/staging/active/blocked/deprecated/retired)';
COMMENT ON COLUMN catalog_ai_agent_status_history.to_status IS '변경 후 상태 (draft/staging/active/blocked/deprecated/retired)';
COMMENT ON COLUMN catalog_ai_agent_status_history.note IS '상태 변경 사유·메모';
COMMENT ON COLUMN catalog_ai_agent_status_history.changed_by IS '상태를 변경한 사용자';
COMMENT ON COLUMN catalog_ai_agent_status_history.changed_at IS '상태 변경 시각';

-- catalog_ai_agent_tools
COMMENT ON COLUMN catalog_ai_agent_tools.id IS 'PK';
COMMENT ON COLUMN catalog_ai_agent_tools.agent_id IS '소속 에이전트 (catalog_ai_agents(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_ai_agent_tools.name IS '도구 이름 (에이전트 내 함수/툴 식별자)';
COMMENT ON COLUMN catalog_ai_agent_tools.description IS '도구 설명 (LLM이 호출 판단에 사용하는 용도 설명)';
COMMENT ON COLUMN catalog_ai_agent_tools.tool_schema IS '함수 호출 파라미터 스키마 (JSON Schema)';
COMMENT ON COLUMN catalog_ai_agent_tools.created_at IS '생성 시각';

-- catalog_ai_agent_versions
COMMENT ON COLUMN catalog_ai_agent_versions.id IS 'PK';
COMMENT ON COLUMN catalog_ai_agent_versions.agent_id IS '소속 에이전트 (catalog_ai_agents(id) 참조, ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_ai_agent_versions.version IS '사양 버전 (SemVer 문자열, agent_id와 함께 UNIQUE)';
COMMENT ON COLUMN catalog_ai_agent_versions.source IS '사양 원본 위치 (Git repo·아티팩트 경로 등)';
COMMENT ON COLUMN catalog_ai_agent_versions.system_prompt IS '해당 버전의 system_prompt 스냅샷';
COMMENT ON COLUMN catalog_ai_agent_versions.changelog IS '버전 변경 사유·내역';
COMMENT ON COLUMN catalog_ai_agent_versions.status IS '버전 상태 (기본 active)';
COMMENT ON COLUMN catalog_ai_agent_versions.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_ai_agent_versions.created_by IS '생성자 (사용자 식별자/이메일)';

-- catalog_ai_agents
COMMENT ON COLUMN catalog_ai_agents.id IS 'PK';
COMMENT ON COLUMN catalog_ai_agents.name IS '전사 고유 머신 식별자 (예: cs.payment-refund-assistant)';
COMMENT ON COLUMN catalog_ai_agents.urn IS 'URN 형식 {name}.{ENV}.agent';
COMMENT ON COLUMN catalog_ai_agents.display_name IS 'UI 노출명';
COMMENT ON COLUMN catalog_ai_agents.description IS '에이전트 설명 (LLM 자동생성 + 휴먼 승인)';
COMMENT ON COLUMN catalog_ai_agents.version IS '사양 버전 (SemVer 문자열)';
COMMENT ON COLUMN catalog_ai_agents.status IS '라이프사이클 상태 (draft/staging/active/blocked/deprecated/retired)';
COMMENT ON COLUMN catalog_ai_agents.owner_email IS '소유자 이메일';
COMMENT ON COLUMN catalog_ai_agents.department IS '소속 부서';
COMMENT ON COLUMN catalog_ai_agents.category IS '분류 (데이터분석/고객지원/코드생성 등)';
COMMENT ON COLUMN catalog_ai_agents.base_model IS '베이스 모델명 (자유 문자열)';
COMMENT ON COLUMN catalog_ai_agents.base_model_ref IS '사내 등록 모델 참조 catalog_registered_models(id)';
COMMENT ON COLUMN catalog_ai_agents.model_provider IS '모델 제공자';
COMMENT ON COLUMN catalog_ai_agents.framework IS '구현 프레임워크';
COMMENT ON COLUMN catalog_ai_agents.execution_policy IS '실행 정책 (ReAct/Plan-Execute/Sequential/Reflection)';
COMMENT ON COLUMN catalog_ai_agents.max_steps IS '최대 실행 스텝 수';
COMMENT ON COLUMN catalog_ai_agents.memory_type IS '메모리 유형 (stateless/short-term/long-term)';
COMMENT ON COLUMN catalog_ai_agents.is_multi_agent IS '멀티 에이전트 여부';
COMMENT ON COLUMN catalog_ai_agents.endpoint IS '호출 엔드포인트 URL';
COMMENT ON COLUMN catalog_ai_agents.protocol IS '호출 프로토콜 (REST/MCP/A2A)';
COMMENT ON COLUMN catalog_ai_agents.invocation_method IS '호출 방식 (sync/async/streaming)';
COMMENT ON COLUMN catalog_ai_agents.auth_method IS '인증 방식 (API key/OAuth2.1(PKCE)/SSO)';
COMMENT ON COLUMN catalog_ai_agents.pii_handling IS 'PII 처리 정책';
COMMENT ON COLUMN catalog_ai_agents.data_residency IS '데이터 거주지(리전)';
COMMENT ON COLUMN catalog_ai_agents.budget_limit IS '예산 한도';
COMMENT ON COLUMN catalog_ai_agents.hitl_required IS '휴먼 개입(HITL) 필수 여부';
COMMENT ON COLUMN catalog_ai_agents.audit_log_ref IS '감사 로그 참조 위치';
COMMENT ON COLUMN catalog_ai_agents.latency_p50 IS '응답 지연 p50 (ms, 집계 캐시)';
COMMENT ON COLUMN catalog_ai_agents.latency_p95 IS '응답 지연 p95 (ms, 집계 캐시)';
COMMENT ON COLUMN catalog_ai_agents.error_rate IS '에러율 (0~1, 집계 캐시)';
COMMENT ON COLUMN catalog_ai_agents.avg_token_usage IS '평균 토큰 사용량 (집계 캐시)';
COMMENT ON COLUMN catalog_ai_agents.cost_per_call IS '호출당 평균 비용 (집계 캐시)';
COMMENT ON COLUMN catalog_ai_agents.reputation_score IS '종합 신뢰등급 점수 (파생값)';
COMMENT ON COLUMN catalog_ai_agents.capabilities IS '능력 목록 (JSON list[str])';
COMMENT ON COLUMN catalog_ai_agents.input_schema IS '입력 계약 (JSON Schema)';
COMMENT ON COLUMN catalog_ai_agents.output_schema IS '출력 계약 (JSON Schema)';
COMMENT ON COLUMN catalog_ai_agents.supported_languages IS '지원 언어 목록 (JSON list, 예: [ko,en])';
COMMENT ON COLUMN catalog_ai_agents.use_cases IS '활용 사례 목록 (JSON list[str])';
COMMENT ON COLUMN catalog_ai_agents.limitations IS '제약/한계 목록 (JSON list[str])';
COMMENT ON COLUMN catalog_ai_agents.inference_params IS '추론 파라미터 (JSON dict, temperature/top_p 등)';
COMMENT ON COLUMN catalog_ai_agents.guardrails IS '가드레일 정책 (JSON dict, 입출력 필터/금지 행위)';
COMMENT ON COLUMN catalog_ai_agents.rag_config IS 'RAG 설정 (JSON dict, vectorstore/retriever/top_k)';
COMMENT ON COLUMN catalog_ai_agents.network_allowlist IS 'egress 허용 도메인 목록 (JSON list[str])';
COMMENT ON COLUMN catalog_ai_agents.dlp_policies IS 'DLP 정책 목록 (JSON list[str])';
COMMENT ON COLUMN catalog_ai_agents.hitl_config IS 'HITL 정책 설정 (JSON dict, 중요 액션 사람 서명)';
COMMENT ON COLUMN catalog_ai_agents.sub_agents IS '하위 에이전트 구성 (JSON list[dict])';
COMMENT ON COLUMN catalog_ai_agents.tags IS '태그 목록 (JSON list[str])';
COMMENT ON COLUMN catalog_ai_agents.usage_count IS '누적 호출 횟수';
COMMENT ON COLUMN catalog_ai_agents.last_invoked_at IS '최근 호출 시각';
COMMENT ON COLUMN catalog_ai_agents.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_ai_agents.updated_at IS '수정 시각';
COMMENT ON COLUMN catalog_ai_agents.created_by IS '생성자';
COMMENT ON COLUMN catalog_ai_agents.updated_by IS '수정자';

-- catalog_ai_generation_log
COMMENT ON COLUMN catalog_ai_generation_log.id IS 'PK';
COMMENT ON COLUMN catalog_ai_generation_log.entity_type IS '대상 엔티티 유형 (dataset/column/tag/pii)';
COMMENT ON COLUMN catalog_ai_generation_log.entity_id IS '대상 엔티티 ID (dataset_id 또는 schema_field_id)';
COMMENT ON COLUMN catalog_ai_generation_log.dataset_id IS '소속 데이터셋 ID (catalog_datasets(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_ai_generation_log.field_name IS '컬럼 단위 생성 시 대상 컬럼명';
COMMENT ON COLUMN catalog_ai_generation_log.generation_type IS '생성 유형 (description/tag_suggestion/pii_detection)';
COMMENT ON COLUMN catalog_ai_generation_log.generated_text IS 'AI가 생성한 결과 텍스트';
COMMENT ON COLUMN catalog_ai_generation_log.applied IS '생성 결과 실제 적용 여부';
COMMENT ON COLUMN catalog_ai_generation_log.provider IS 'AI 제공자 (예: openai/anthropic)';
COMMENT ON COLUMN catalog_ai_generation_log.model IS '사용한 모델명';
COMMENT ON COLUMN catalog_ai_generation_log.prompt_tokens IS '프롬프트 입력 토큰 수';
COMMENT ON COLUMN catalog_ai_generation_log.completion_tokens IS '응답 생성 토큰 수';
COMMENT ON COLUMN catalog_ai_generation_log.created_at IS '생성 시각';

-- catalog_api_alerts
COMMENT ON TABLE catalog_api_alerts IS 'API 스펙 업로드 시 Breaking 변경 감지로 자동 생성되는 알림';
COMMENT ON COLUMN catalog_api_alerts.id IS 'PK';
COMMENT ON COLUMN catalog_api_alerts.api_id IS '대상 API (catalog_apis(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_api_alerts.from_spec_id IS '변경 전 current 스펙 ID';
COMMENT ON COLUMN catalog_api_alerts.to_spec_id IS '새로 업로드된 스펙 ID';
COMMENT ON COLUMN catalog_api_alerts.from_version IS '변경 전 스펙 버전';
COMMENT ON COLUMN catalog_api_alerts.to_version IS '변경 후 스펙 버전';
COMMENT ON COLUMN catalog_api_alerts.severity IS '심각도 (BREAKING)';
COMMENT ON COLUMN catalog_api_alerts.breaking_count IS '감지된 Breaking 변경 건수';
COMMENT ON COLUMN catalog_api_alerts.summary IS '변경 요약 (한 줄)';
COMMENT ON COLUMN catalog_api_alerts.detail IS '변경 상세 (JSON: removed/changed)';
COMMENT ON COLUMN catalog_api_alerts.status IS '처리 상태 (OPEN/ACKNOWLEDGED)';
COMMENT ON COLUMN catalog_api_alerts.created_by IS '알림 생성자';
COMMENT ON COLUMN catalog_api_alerts.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_api_alerts.acknowledged_by IS '확인 처리자';
COMMENT ON COLUMN catalog_api_alerts.acknowledged_at IS '확인 처리 시각';

-- catalog_api_credentials
COMMENT ON TABLE catalog_api_credentials IS 'API 호출용 자격증명 저장 (시크릿은 Fernet 암호화, Try-it 콘솔에서 서버가 복호화해 주입)';
COMMENT ON COLUMN catalog_api_credentials.id IS 'PK';
COMMENT ON COLUMN catalog_api_credentials.api_id IS '대상 API (catalog_apis(id) 참조, ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_api_credentials.scheme_name IS '연결된 보안 스킴 키 (있으면)';
COMMENT ON COLUMN catalog_api_credentials.label IS '자격증명 표시 이름';
COMMENT ON COLUMN catalog_api_credentials.type IS '인증 방식 (apiKey/bearer/basic/oauth2)';
COMMENT ON COLUMN catalog_api_credentials.secret IS '암호화된 시크릿 JSON (Fernet)';
COMMENT ON COLUMN catalog_api_credentials.created_by IS '생성자';
COMMENT ON COLUMN catalog_api_credentials.created_at IS '생성 시각';

-- catalog_api_endpoints
COMMENT ON TABLE catalog_api_endpoints IS 'API 엔드포인트(오퍼레이션) — 메서드/경로/요청·응답 스키마/보안 정의';
COMMENT ON COLUMN catalog_api_endpoints.id IS 'PK';
COMMENT ON COLUMN catalog_api_endpoints.api_id IS '소속 API (catalog_apis(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_api_endpoints.method IS '오퍼레이션 유형 (REST: GET/POST 등, GraphQL: query/mutation, gRPC: unary 등)';
COMMENT ON COLUMN catalog_api_endpoints.path IS '오퍼레이션 식별자 (REST: 경로, GraphQL: 이름, gRPC: Service.Method)';
COMMENT ON COLUMN catalog_api_endpoints.operation_id IS '오퍼레이션 고유 ID (operationId)';
COMMENT ON COLUMN catalog_api_endpoints.summary IS '오퍼레이션 요약';
COMMENT ON COLUMN catalog_api_endpoints.description IS '오퍼레이션 상세 설명';
COMMENT ON COLUMN catalog_api_endpoints.tags IS '분류 태그 목록 (list[str], JSON)';
COMMENT ON COLUMN catalog_api_endpoints.parameters IS '인자/파라미터 정의 목록 (list[dict], JSON)';
COMMENT ON COLUMN catalog_api_endpoints.request_body IS '요청/입력 스키마 (dict, JSON)';
COMMENT ON COLUMN catalog_api_endpoints.responses IS '응답/출력 정의 (dict, JSON)';
COMMENT ON COLUMN catalog_api_endpoints.security IS '보안 요구 사항 목록 (list, JSON)';
COMMENT ON COLUMN catalog_api_endpoints.extra IS '프로토콜별 추가 속성 (soap_action/grpc 메시지/graphql 반환타입 등, JSON)';
COMMENT ON COLUMN catalog_api_endpoints.sort_order IS '표시 정렬 순서';

-- catalog_api_favorites
COMMENT ON TABLE catalog_api_favorites IS '사용자별 API 엔드포인트 즐겨찾기 (method+path 로 식별해 스펙 재업로드에도 유지)';
COMMENT ON COLUMN catalog_api_favorites.id IS 'PK';
COMMENT ON COLUMN catalog_api_favorites.user_key IS '즐겨찾기 소유 사용자 식별자 (username 또는 email)';
COMMENT ON COLUMN catalog_api_favorites.api_id IS '대상 API (catalog_apis(id) 참조, ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_api_favorites.method IS '엔드포인트 HTTP 메서드 (GET/POST/PUT/DELETE 등)';
COMMENT ON COLUMN catalog_api_favorites.path IS '엔드포인트 경로';
COMMENT ON COLUMN catalog_api_favorites.created_at IS '생성 시각';

-- catalog_api_invocations
COMMENT ON TABLE catalog_api_invocations IS 'Try-it 콘솔(프록시) API 호출 로그 — 사용량 관측·미터링·입력 이력';
COMMENT ON COLUMN catalog_api_invocations.id IS 'PK';
COMMENT ON COLUMN catalog_api_invocations.api_id IS '호출된 API (catalog_apis(id) 참조, ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_api_invocations.method IS 'HTTP 메서드 (GET/POST/PUT/DELETE 등)';
COMMENT ON COLUMN catalog_api_invocations.url IS '실제 호출된 요청 URL';
COMMENT ON COLUMN catalog_api_invocations.status_code IS 'HTTP 응답 상태 코드 (0=네트워크/예외)';
COMMENT ON COLUMN catalog_api_invocations.ok IS '성공 여부 문자열 (2xx/3xx면 true)';
COMMENT ON COLUMN catalog_api_invocations.latency_ms IS '응답 지연(ms)';
COMMENT ON COLUMN catalog_api_invocations.error IS '오류 메시지 (실패 시)';
COMMENT ON COLUMN catalog_api_invocations.called_by IS '호출 사용자 (username 또는 email)';
COMMENT ON COLUMN catalog_api_invocations.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_api_invocations.endpoint_method IS '엔드포인트 템플릿 메서드';
COMMENT ON COLUMN catalog_api_invocations.endpoint_path IS '엔드포인트 템플릿 경로 (파라미터 치환 전)';
COMMENT ON COLUMN catalog_api_invocations.request_input IS '입력 파라미터 이력 (path/query/headers(마스킹)/body JSON)';

-- catalog_api_lineage
COMMENT ON TABLE catalog_api_lineage IS 'API의 제공/소비/의존 리니지 엣지(관계) 저장';
COMMENT ON COLUMN catalog_api_lineage.id IS 'PK';
COMMENT ON COLUMN catalog_api_lineage.api_id IS '대상 API (catalog_apis(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_api_lineage.relation IS '관계 유형 (provides/consumes/depends_on)';
COMMENT ON COLUMN catalog_api_lineage.target_type IS '대상 종류 (api/dataset/model/agent/system)';
COMMENT ON COLUMN catalog_api_lineage.target_ref IS '대상 식별자 (이름/URN/외부 참조)';
COMMENT ON COLUMN catalog_api_lineage.target_label IS '대상 표시명 (선택)';
COMMENT ON COLUMN catalog_api_lineage.note IS '부가 설명 메모';
COMMENT ON COLUMN catalog_api_lineage.created_by IS '생성자';
COMMENT ON COLUMN catalog_api_lineage.created_at IS '생성 시각';

-- catalog_api_security_schemes
COMMENT ON TABLE catalog_api_security_schemes IS 'API의 인증 스킴 정의(OpenAPI securitySchemes) 저장';
COMMENT ON COLUMN catalog_api_security_schemes.id IS 'PK';
COMMENT ON COLUMN catalog_api_security_schemes.api_id IS '소속 API (catalog_apis(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_api_security_schemes.scheme_name IS '스펙상 보안 스킴 키 이름';
COMMENT ON COLUMN catalog_api_security_schemes.type IS '인증 방식 (apiKey/oauth2/http/openIdConnect/mutualTLS)';
COMMENT ON COLUMN catalog_api_security_schemes.config IS '스킴 상세 설정 JSON (in/name/scheme/flows 등)';

-- catalog_api_servers
COMMENT ON TABLE catalog_api_servers IS 'API별 서버/환경(base URL) 목록 — OpenAPI servers 항목';
COMMENT ON COLUMN catalog_api_servers.id IS 'PK';
COMMENT ON COLUMN catalog_api_servers.api_id IS '소속 API (catalog_apis(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_api_servers.url IS '서버 base URL';
COMMENT ON COLUMN catalog_api_servers.description IS '서버 설명';
COMMENT ON COLUMN catalog_api_servers.env IS '배포 환경 (PROD/STAGING/DEV, 선택)';

-- catalog_api_specs
COMMENT ON TABLE catalog_api_specs IS 'API 스펙 버전 이력 (원본 raw 텍스트 + 파싱 요약 결과 저장)';
COMMENT ON COLUMN catalog_api_specs.id IS 'PK';
COMMENT ON COLUMN catalog_api_specs.api_id IS '소속 API (catalog_apis(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_api_specs.version IS '스펙 버전 (api_id별 UNIQUE)';
COMMENT ON COLUMN catalog_api_specs.format IS '스펙 포맷 (openapi2/openapi3 등)';
COMMENT ON COLUMN catalog_api_specs.raw IS '원본 스펙 텍스트 (JSON/YAML 정규화 JSON)';
COMMENT ON COLUMN catalog_api_specs.parsed IS '파싱 요약 (info/servers/securitySchemes 등)';
COMMENT ON COLUMN catalog_api_specs.source_url IS '스펙 원본 URL';
COMMENT ON COLUMN catalog_api_specs.is_current IS '현재 버전 여부 (문자열 ''true''/''false'')';
COMMENT ON COLUMN catalog_api_specs.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_api_specs.created_by IS '생성자';

-- catalog_api_status_history
COMMENT ON TABLE catalog_api_status_history IS 'API 상태 전이 이력 — 상태 변경 시 자동 기록';
COMMENT ON COLUMN catalog_api_status_history.id IS 'PK';
COMMENT ON COLUMN catalog_api_status_history.api_id IS '대상 API (catalog_apis(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_api_status_history.from_status IS '변경 전 상태 (draft/published/deprecated/retired)';
COMMENT ON COLUMN catalog_api_status_history.to_status IS '변경 후 상태 (draft/published/deprecated/retired)';
COMMENT ON COLUMN catalog_api_status_history.note IS '변경 사유·비고 메모';
COMMENT ON COLUMN catalog_api_status_history.changed_by IS '상태를 변경한 사용자';
COMMENT ON COLUMN catalog_api_status_history.changed_at IS '상태 변경 시각';

-- catalog_apis
COMMENT ON TABLE catalog_apis IS '등록된 API 메인 엔티티 (식별/소유/분류/상태/버전/스펙 출처)';
COMMENT ON COLUMN catalog_apis.id IS 'PK';
COMMENT ON COLUMN catalog_apis.name IS 'API 고유 이름 (unique)';
COMMENT ON COLUMN catalog_apis.urn IS 'API 고유 식별자 URN (unique)';
COMMENT ON COLUMN catalog_apis.display_name IS '화면 표시명';
COMMENT ON COLUMN catalog_apis.description IS 'API 설명';
COMMENT ON COLUMN catalog_apis.version IS 'API 버전 (기본 1.0.0)';
COMMENT ON COLUMN catalog_apis.status IS '상태 (draft/published/deprecated/retired)';
COMMENT ON COLUMN catalog_apis.owner_email IS '소유자 이메일';
COMMENT ON COLUMN catalog_apis.department IS '담당 부서';
COMMENT ON COLUMN catalog_apis.category IS '분류 카테고리';
COMMENT ON COLUMN catalog_apis.protocol IS '프로토콜 (REST/GraphQL/gRPC/AsyncAPI)';
COMMENT ON COLUMN catalog_apis.source IS '엔드포인트 출처 (spec=스펙 파싱/manual=수동 등록)';
COMMENT ON COLUMN catalog_apis.spec_format IS '스펙 형식 (openapi2/openapi3/asyncapi 등)';
COMMENT ON COLUMN catalog_apis.base_url IS '대표 서버 기본 base URL';
COMMENT ON COLUMN catalog_apis.base_url_overridden IS 'Base URL 수동 지정 여부 (true 면 스펙이 덮어쓰지 않음)';
COMMENT ON COLUMN catalog_apis.contract_text IS '계약/스키마 문서 원문 (SDL/WSDL/.proto/AsyncAPI 등)';
COMMENT ON COLUMN catalog_apis.contract_url IS '계약/스키마 문서 URL';
COMMENT ON COLUMN catalog_apis.certification IS '인증 상태';
COMMENT ON COLUMN catalog_apis.tier IS '등급 tier';
COMMENT ON COLUMN catalog_apis.tags IS '태그 목록 (list[str])';
COMMENT ON COLUMN catalog_apis.note IS '비고/메모';
COMMENT ON COLUMN catalog_apis.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_apis.updated_at IS '수정 시각';
COMMENT ON COLUMN catalog_apis.created_by IS '생성자';
COMMENT ON COLUMN catalog_apis.updated_by IS '수정자';

-- catalog_categories
COMMENT ON TABLE catalog_categories IS '분류체계(taxonomy) 내부의 분류 노드 — 자기참조 트리 구조';
COMMENT ON COLUMN catalog_categories.id IS 'PK';
COMMENT ON COLUMN catalog_categories.taxonomy_id IS '소속 분류체계 (catalog_taxonomies(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_categories.parent_id IS '상위 분류 노드 (catalog_categories(id) 참조, NULL=루트)';
COMMENT ON COLUMN catalog_categories.code IS '분류 코드/slug (선택)';
COMMENT ON COLUMN catalog_categories.name IS '분류 노드 이름';
COMMENT ON COLUMN catalog_categories.description IS '분류 노드 설명';
COMMENT ON COLUMN catalog_categories.sort_order IS '형제 노드 간 정렬 순서';
COMMENT ON COLUMN catalog_categories.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_categories.updated_at IS '수정 시각';

-- catalog_code_group
COMMENT ON COLUMN catalog_code_group.id IS 'PK';
COMMENT ON COLUMN catalog_code_group.dictionary_id IS '소속 표준 사전 (catalog_standard_dictionary(id) 참조)';
COMMENT ON COLUMN catalog_code_group.group_name IS '코드 그룹 한글명';
COMMENT ON COLUMN catalog_code_group.group_english IS '코드 그룹 영문명';
COMMENT ON COLUMN catalog_code_group.description IS '코드 그룹 설명';
COMMENT ON COLUMN catalog_code_group.status IS '상태 (ACTIVE/INACTIVE)';
COMMENT ON COLUMN catalog_code_group.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_code_group.updated_at IS '수정 시각';

-- catalog_code_value
COMMENT ON COLUMN catalog_code_value.id IS 'PK';
COMMENT ON COLUMN catalog_code_value.code_group_id IS '소속 코드 그룹 (catalog_code_group(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_code_value.code_value IS '코드 값 (예: M)';
COMMENT ON COLUMN catalog_code_value.code_name IS '코드명 한글 (예: 남성)';
COMMENT ON COLUMN catalog_code_value.code_english IS '코드명 영문 (예: Male)';
COMMENT ON COLUMN catalog_code_value.description IS '코드 설명';
COMMENT ON COLUMN catalog_code_value.sort_order IS '정렬 순서 (오름차순)';
COMMENT ON COLUMN catalog_code_value.is_active IS '활성 여부 (true/false)';
COMMENT ON COLUMN catalog_code_value.created_at IS '생성 시각';

-- catalog_comments
COMMENT ON COLUMN catalog_comments.id IS 'PK';
COMMENT ON COLUMN catalog_comments.entity_type IS '댓글 대상 엔티티 유형 (dataset/model/glossary 등)';
COMMENT ON COLUMN catalog_comments.entity_id IS '댓글 대상 엔티티 식별자 (FK 아님, 다형성 참조)';
COMMENT ON COLUMN catalog_comments.parent_id IS '부모 댓글 (catalog_comments(id) 참조, NULL이면 최상위)';
COMMENT ON COLUMN catalog_comments.root_id IS '스레드 최상위 댓글 (catalog_comments(id) 참조)';
COMMENT ON COLUMN catalog_comments.depth IS '댓글 중첩 깊이 (0=최상위)';
COMMENT ON COLUMN catalog_comments.content IS '댓글 본문 (서식 포함)';
COMMENT ON COLUMN catalog_comments.content_plain IS '댓글 본문 평문 (검색·미리보기용)';
COMMENT ON COLUMN catalog_comments.author_name IS '작성자 표시 이름';
COMMENT ON COLUMN catalog_comments.author_email IS '작성자 이메일';
COMMENT ON COLUMN catalog_comments.author_avatar IS '작성자 아바타 이미지 URL';
COMMENT ON COLUMN catalog_comments.reply_count IS '답글 수 (비정규화 캐시값)';
COMMENT ON COLUMN catalog_comments.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_comments.updated_at IS '수정 시각';
COMMENT ON COLUMN catalog_comments.is_deleted IS '소프트 삭제 여부';
COMMENT ON COLUMN catalog_comments.category IS '댓글 분류 (general/suggestion/feature/bug)';

-- catalog_configuration
COMMENT ON COLUMN catalog_configuration.id IS 'PK';
COMMENT ON COLUMN catalog_configuration.category IS '설정 분류 카테고리 (예: storage)';
COMMENT ON COLUMN catalog_configuration.config_key IS '설정 키 (고유)';
COMMENT ON COLUMN catalog_configuration.config_value IS '설정 값 (문자열)';
COMMENT ON COLUMN catalog_configuration.description IS '설정 항목 설명';
COMMENT ON COLUMN catalog_configuration.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_configuration.updated_at IS '수정 시각';

-- catalog_data_profile
COMMENT ON COLUMN catalog_data_profile.id IS 'PK';
COMMENT ON COLUMN catalog_data_profile.dataset_id IS '대상 데이터셋 (catalog_datasets(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_data_profile.row_count IS '프로파일링 시점의 전체 행 수';
COMMENT ON COLUMN catalog_data_profile.profile_json IS '컬럼별 통계 결과 JSON 배열 (null 비율·distinct·min/max 등)';
COMMENT ON COLUMN catalog_data_profile.profiled_at IS '프로파일링 수행 시각';

-- catalog_dataset_categories
COMMENT ON TABLE catalog_dataset_categories IS '데이터셋과 분류 노드의 N:M 매핑 테이블';
COMMENT ON COLUMN catalog_dataset_categories.id IS 'PK';
COMMENT ON COLUMN catalog_dataset_categories.dataset_id IS '데이터셋 ID (catalog_datasets(id) 참조, ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_dataset_categories.category_id IS '분류 노드 ID (catalog_categories(id) 참조, ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_dataset_categories.created_at IS '생성 시각';

-- catalog_dataset_embeddings
COMMENT ON COLUMN catalog_dataset_embeddings.id IS 'PK';
COMMENT ON COLUMN catalog_dataset_embeddings.dataset_id IS '대상 데이터셋 (catalog_datasets(id) 참조, UNIQUE, CASCADE 삭제)';
COMMENT ON COLUMN catalog_dataset_embeddings.embedding IS '임베딩 벡터 (pgvector, 384차원)';
COMMENT ON COLUMN catalog_dataset_embeddings.source_text IS '임베딩 생성에 사용된 원본 텍스트 (변경 감지용)';
COMMENT ON COLUMN catalog_dataset_embeddings.model_name IS '임베딩 생성 모델명';
COMMENT ON COLUMN catalog_dataset_embeddings.provider IS '임베딩 제공자 (예: openai/huggingface)';
COMMENT ON COLUMN catalog_dataset_embeddings.dimension IS '임베딩 벡터 차원 수 (기본 384)';
COMMENT ON COLUMN catalog_dataset_embeddings.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_dataset_embeddings.updated_at IS '수정 시각';

-- catalog_dataset_glossary_terms
COMMENT ON COLUMN catalog_dataset_glossary_terms.id IS 'PK';
COMMENT ON COLUMN catalog_dataset_glossary_terms.dataset_id IS '데이터셋 ID (catalog_datasets(id) 참조, ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_dataset_glossary_terms.term_id IS '용어집 용어 ID (catalog_glossary_terms(id) 참조, ON DELETE CASCADE)';

-- catalog_dataset_properties
COMMENT ON COLUMN catalog_dataset_properties.id IS 'PK';
COMMENT ON COLUMN catalog_dataset_properties.dataset_id IS '소속 데이터셋 (catalog_datasets(id) 참조, 삭제 시 CASCADE)';
COMMENT ON COLUMN catalog_dataset_properties.property_key IS '속성 키 (dataset_id와 함께 유일)';
COMMENT ON COLUMN catalog_dataset_properties.property_value IS '속성 값 (문자열)';

-- catalog_dataset_schemas
COMMENT ON COLUMN catalog_dataset_schemas.id IS 'PK';
COMMENT ON COLUMN catalog_dataset_schemas.dataset_id IS '소속 데이터셋 (catalog_datasets(id) 참조, 삭제 시 CASCADE)';
COMMENT ON COLUMN catalog_dataset_schemas.field_path IS '필드(컬럼) 경로/이름';
COMMENT ON COLUMN catalog_dataset_schemas.field_type IS '논리 데이터 타입';
COMMENT ON COLUMN catalog_dataset_schemas.native_type IS '데이터 소스 원본 타입';
COMMENT ON COLUMN catalog_dataset_schemas.description IS '필드 설명';
COMMENT ON COLUMN catalog_dataset_schemas.nullable IS 'NULL 허용 여부 ("true"/"false")';
COMMENT ON COLUMN catalog_dataset_schemas.ordinal IS '필드 정렬 순서(0-base)';
COMMENT ON COLUMN catalog_dataset_schemas.is_primary_key IS '기본키 여부 ("true"/"false")';
COMMENT ON COLUMN catalog_dataset_schemas.is_indexed IS '인덱스 여부 ("true"/"false")';
COMMENT ON COLUMN catalog_dataset_schemas.is_unique IS '유니크 제약 여부 ("true"/"false")';
COMMENT ON COLUMN catalog_dataset_schemas.is_partition_key IS '파티션 키 여부 ("true"/"false")';
COMMENT ON COLUMN catalog_dataset_schemas.is_distribution_key IS '분산 키 여부 ("true"/"false")';

-- catalog_dataset_tags
COMMENT ON COLUMN catalog_dataset_tags.id IS 'PK';
COMMENT ON COLUMN catalog_dataset_tags.dataset_id IS '데이터셋 ID (catalog_datasets(id) 참조, 삭제 시 CASCADE)';
COMMENT ON COLUMN catalog_dataset_tags.tag_id IS '태그 ID (catalog_tags(id) 참조, 삭제 시 CASCADE)';

-- catalog_dataset_urn_alias
COMMENT ON TABLE catalog_dataset_urn_alias IS '구 URN을 dataset에 매핑하는 별칭 테이블 (URN 포맷 전환기 외부 참조 해소용)';
COMMENT ON COLUMN catalog_dataset_urn_alias.old_urn IS 'PK, 과거 포맷의 dataset URN (환경 토큰 포함 등)';
COMMENT ON COLUMN catalog_dataset_urn_alias.dataset_id IS '매핑 대상 데이터셋, catalog_datasets(id) 참조 (ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_dataset_urn_alias.created_at IS '생성 시각';

-- catalog_datasets
COMMENT ON COLUMN catalog_datasets.id IS 'PK';
COMMENT ON COLUMN catalog_datasets.urn IS '데이터셋 전역 고유 식별자 URN (unique)';
COMMENT ON COLUMN catalog_datasets.name IS '데이터셋 물리명';
COMMENT ON COLUMN catalog_datasets.datasource_id IS '소속 데이터 소스 (catalog_datasources(id) 참조)';
COMMENT ON COLUMN catalog_datasets.summary IS '한 줄 요약 (목록/검색 카드 노출용)';
COMMENT ON COLUMN catalog_datasets.description IS '상세 설명';
COMMENT ON COLUMN catalog_datasets.origin IS '환경 구분 (DEV/STAGING/PROD)';
COMMENT ON COLUMN catalog_datasets.qualified_name IS '정규화된 전체 경로명 (예: catalog.schema.table)';
COMMENT ON COLUMN catalog_datasets.status IS '데이터셋 상태 (active/inactive 등)';
COMMENT ON COLUMN catalog_datasets.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_datasets.updated_at IS '수정 시각';
COMMENT ON COLUMN catalog_datasets.table_type IS '테이블 유형 (TABLE/VIEW/TOPIC/FILE 등)';
COMMENT ON COLUMN catalog_datasets.storage_format IS '저장 포맷 (PARQUET/ORC/CSV 등)';
COMMENT ON COLUMN catalog_datasets.datasource_properties IS '데이터 소스별 부가 메타데이터 (JSON)';
COMMENT ON COLUMN catalog_datasets.ddl IS 'CREATE TABLE DDL 원문 (sync 어댑터가 채움)';
COMMENT ON COLUMN catalog_datasets.is_synced IS '메타데이터 동기화 여부 ("true"/"false")';

-- catalog_datasource_configurations
COMMENT ON COLUMN catalog_datasource_configurations.id IS 'PK';
COMMENT ON COLUMN catalog_datasource_configurations.datasource_id IS '데이터소스 (catalog_datasources(id) 참조, UNIQUE·ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_datasource_configurations.config_json IS '접속·인증·옵션 설정 JSON (JDBC URL, 자격 증명 등)';
COMMENT ON COLUMN catalog_datasource_configurations.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_datasource_configurations.updated_at IS '수정 시각';

-- catalog_datasource_data_types
COMMENT ON COLUMN catalog_datasource_data_types.id IS 'PK';
COMMENT ON COLUMN catalog_datasource_data_types.datasource_id IS '소속 데이터 소스 (catalog_datasources(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_datasource_data_types.type_name IS '데이터 타입 이름 (예: VARCHAR, BIGINT, TIMESTAMP)';
COMMENT ON COLUMN catalog_datasource_data_types.type_category IS '타입 분류 (예: STRING/NUMERIC/DATETIME/BINARY)';
COMMENT ON COLUMN catalog_datasource_data_types.description IS '타입 설명';
COMMENT ON COLUMN catalog_datasource_data_types.ordinal IS '표시 정렬 순서';

-- catalog_datasource_features
COMMENT ON COLUMN catalog_datasource_features.id IS 'PK';
COMMENT ON COLUMN catalog_datasource_features.datasource_id IS '소속 데이터소스 (catalog_datasources(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_datasource_features.feature_key IS '파라미터 식별 키 (datasource_id와 함께 유니크)';
COMMENT ON COLUMN catalog_datasource_features.display_name IS '화면 표시용 이름';
COMMENT ON COLUMN catalog_datasource_features.description IS '파라미터 설명';
COMMENT ON COLUMN catalog_datasource_features.value_type IS '값 자료형 (string/int/bool 등)';
COMMENT ON COLUMN catalog_datasource_features.is_required IS '필수 입력 여부 (문자열 true/false)';
COMMENT ON COLUMN catalog_datasource_features.ordinal IS '표시 정렬 순서';

-- catalog_datasource_storage_formats
COMMENT ON COLUMN catalog_datasource_storage_formats.id IS 'PK';
COMMENT ON COLUMN catalog_datasource_storage_formats.datasource_id IS '소속 데이터소스 (catalog_datasources(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_datasource_storage_formats.format_name IS '포맷 식별 코드 (예: PARQUET/ORC/CSV)';
COMMENT ON COLUMN catalog_datasource_storage_formats.display_name IS '화면 표시용 포맷 이름';
COMMENT ON COLUMN catalog_datasource_storage_formats.description IS '포맷 설명';
COMMENT ON COLUMN catalog_datasource_storage_formats.is_default IS '기본 포맷 여부 (true/false 문자열)';
COMMENT ON COLUMN catalog_datasource_storage_formats.ordinal IS '표시 정렬 순서';

-- catalog_datasource_table_types
COMMENT ON COLUMN catalog_datasource_table_types.id IS 'PK';
COMMENT ON COLUMN catalog_datasource_table_types.datasource_id IS '소속 데이터소스 (catalog_datasources(id) 참조, ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_datasource_table_types.type_name IS '테이블 타입 코드 (TABLE/VIEW/MATERIALIZED_VIEW 등)';
COMMENT ON COLUMN catalog_datasource_table_types.display_name IS 'UI 표시용 타입 이름';
COMMENT ON COLUMN catalog_datasource_table_types.description IS '타입 설명';
COMMENT ON COLUMN catalog_datasource_table_types.is_default IS '기본 선택 타입 여부 (문자열 ''true''/''false'')';
COMMENT ON COLUMN catalog_datasource_table_types.ordinal IS 'UI 정렬 순서 (오름차순)';

-- catalog_datasources
COMMENT ON COLUMN catalog_datasources.id IS 'PK';
COMMENT ON COLUMN catalog_datasources.name IS '데이터 소스 표시 이름';
COMMENT ON COLUMN catalog_datasources.logo_url IS '데이터 소스 로고 이미지 URL';
COMMENT ON COLUMN catalog_datasources.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_datasources.datasource_id IS '데이터 소스 외부 식별자 (UUID, 고유)';
COMMENT ON COLUMN catalog_datasources.type IS '데이터 소스 플랫폼 유형 (예: hive/trino/postgres)';
COMMENT ON COLUMN catalog_datasources.origin IS '환경 구분 (DEV/STAGING/PROD), 생성 후 불변';

-- catalog_entity_embeddings
COMMENT ON COLUMN catalog_entity_embeddings.id IS 'PK';
COMMENT ON COLUMN catalog_entity_embeddings.entity_type IS '대상 엔티티 종류 (glossary_term/ai_agent/api)';
COMMENT ON COLUMN catalog_entity_embeddings.entity_id IS '엔티티 식별자 (entity_type별 원본 테이블 PK 참조)';
COMMENT ON COLUMN catalog_entity_embeddings.embedding IS 'pgvector 임베딩 벡터 (384차원)';
COMMENT ON COLUMN catalog_entity_embeddings.source_text IS '임베딩 생성에 사용된 원본 텍스트 (변경 감지용)';
COMMENT ON COLUMN catalog_entity_embeddings.model_name IS '임베딩 생성 모델명';
COMMENT ON COLUMN catalog_entity_embeddings.provider IS '임베딩 제공자 (provider 식별자)';
COMMENT ON COLUMN catalog_entity_embeddings.dimension IS '임베딩 벡터 차원 수 (기본 384)';
COMMENT ON COLUMN catalog_entity_embeddings.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_entity_embeddings.updated_at IS '수정 시각';

-- catalog_glossary_terms
COMMENT ON COLUMN catalog_glossary_terms.id IS 'PK';
COMMENT ON COLUMN catalog_glossary_terms.name IS '용어명 (고유)';
COMMENT ON COLUMN catalog_glossary_terms.description IS '용어 설명';
COMMENT ON COLUMN catalog_glossary_terms.parent_id IS '상위 용어 ID (catalog_glossary_terms(id) 자기참조, 계층 구조)';
COMMENT ON COLUMN catalog_glossary_terms.term_type IS '항목 유형 (CATEGORY/TERM)';
COMMENT ON COLUMN catalog_glossary_terms.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_glossary_terms.updated_at IS '수정 시각';

-- catalog_model_card
COMMENT ON COLUMN catalog_model_card.id IS 'PK';
COMMENT ON COLUMN catalog_model_card.model_id IS '대상 모델 (catalog_registered_models(id) 참조, 1:1)';
COMMENT ON COLUMN catalog_model_card.purpose IS '모델의 용도·목적 설명';
COMMENT ON COLUMN catalog_model_card.performance IS '성능 지표·평가 결과 설명';
COMMENT ON COLUMN catalog_model_card.limitations IS '모델의 한계·주의사항';
COMMENT ON COLUMN catalog_model_card.training_data IS '학습 데이터 출처·구성 설명';
COMMENT ON COLUMN catalog_model_card.framework IS '학습/추론 프레임워크명';
COMMENT ON COLUMN catalog_model_card.license IS '모델 라이선스';
COMMENT ON COLUMN catalog_model_card.contact IS '담당자·문의처';
COMMENT ON COLUMN catalog_model_card.updated_at IS '수정 시각';

-- catalog_model_dataset_lineage
COMMENT ON COLUMN catalog_model_dataset_lineage.id IS 'PK';
COMMENT ON COLUMN catalog_model_dataset_lineage.model_id IS '등록 모델 ID (catalog_registered_models(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_model_dataset_lineage.model_version IS '연관된 모델 버전 번호';
COMMENT ON COLUMN catalog_model_dataset_lineage.dataset_id IS '데이터셋 ID (catalog_datasets(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_model_dataset_lineage.relation_type IS '연관 유형 (TRAINING_DATA/EVALUATION_DATA/FEATURE_SOURCE)';
COMMENT ON COLUMN catalog_model_dataset_lineage.description IS '리니지 설명';
COMMENT ON COLUMN catalog_model_dataset_lineage.created_at IS '생성 시각';

-- catalog_model_download_log
COMMENT ON COLUMN catalog_model_download_log.id IS 'PK';
COMMENT ON COLUMN catalog_model_download_log.model_name IS '대상 모델 이름';
COMMENT ON COLUMN catalog_model_download_log.version IS '모델 버전 번호';
COMMENT ON COLUMN catalog_model_download_log.download_type IS '다운로드 유형 (load=MLflow, pull=SDK, download=단일 파일)';
COMMENT ON COLUMN catalog_model_download_log.client_ip IS '요청 클라이언트 IP (IPv4/IPv6)';
COMMENT ON COLUMN catalog_model_download_log.user_agent IS '요청 User-Agent 문자열';
COMMENT ON COLUMN catalog_model_download_log.downloaded_at IS '다운로드 발생 시각';

-- catalog_model_metrics
COMMENT ON COLUMN catalog_model_metrics.id IS 'PK';
COMMENT ON COLUMN catalog_model_metrics.model_id IS '등록 모델 ID (catalog_registered_models(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_model_metrics.version IS '모델 버전 번호';
COMMENT ON COLUMN catalog_model_metrics.metric_key IS '지표 이름 (예: accuracy, f1, precision)';
COMMENT ON COLUMN catalog_model_metrics.metric_value IS '지표 값 (DECIMAL(15,6))';
COMMENT ON COLUMN catalog_model_metrics.recorded_at IS '기록 시각';

-- catalog_model_versions
COMMENT ON COLUMN catalog_model_versions.id IS 'PK';
COMMENT ON COLUMN catalog_model_versions.model_id IS '등록 모델 (catalog_registered_models(id) 참조)';
COMMENT ON COLUMN catalog_model_versions.version IS '모델별 자동 증가 버전 번호 (1,2,3...)';
COMMENT ON COLUMN catalog_model_versions.source IS 'MLflow 아티팩트 소스 URI (예: models:/m-abc123)';
COMMENT ON COLUMN catalog_model_versions.run_id IS '이 버전을 생성한 MLflow run ID';
COMMENT ON COLUMN catalog_model_versions.run_link IS 'MLflow run UI 링크 (선택)';
COMMENT ON COLUMN catalog_model_versions.description IS '버전 설명';
COMMENT ON COLUMN catalog_model_versions.status IS '등록 상태 (PENDING_REGISTRATION/READY/FAILED_REGISTRATION)';
COMMENT ON COLUMN catalog_model_versions.status_message IS '상태 사유 메시지 (예: 등록 중 모델 삭제)';
COMMENT ON COLUMN catalog_model_versions.storage_location IS '버전별 아티팩트 저장 경로 (file:///.../{name}/versions/{ver}/)';
COMMENT ON COLUMN catalog_model_versions.artifact_count IS 'finalize 시점 집계한 아티팩트 파일 수';
COMMENT ON COLUMN catalog_model_versions.artifact_size IS 'finalize 시점 집계한 아티팩트 총 크기(byte)';
COMMENT ON COLUMN catalog_model_versions.finished_at IS 'finalize 호출 시각 (NULL=미완료)';
COMMENT ON COLUMN catalog_model_versions.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_model_versions.updated_at IS '수정 시각';
COMMENT ON COLUMN catalog_model_versions.created_by IS '생성자';
COMMENT ON COLUMN catalog_model_versions.updated_by IS '수정자';
COMMENT ON COLUMN catalog_model_versions.stage IS '배포 스테이지 (NONE/STAGING/PRODUCTION/ARCHIVED)';
COMMENT ON COLUMN catalog_model_versions.stage_changed_at IS '스테이지 변경 시각';
COMMENT ON COLUMN catalog_model_versions.stage_changed_by IS '스테이지 변경자';

-- catalog_models
COMMENT ON COLUMN catalog_models.id IS 'PK';
COMMENT ON COLUMN catalog_models.model_version_id IS 'catalog_model_versions(id) 참조 (ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_models.model_name IS '모델 이름 (version과 UNIQUE)';
COMMENT ON COLUMN catalog_models.version IS '모델 버전 번호 (model_name과 UNIQUE)';
COMMENT ON COLUMN catalog_models.predict_fn IS 'MLmodel의 예측 함수명 (예: predict)';
COMMENT ON COLUMN catalog_models.python_version IS '모델 학습 환경 Python 버전';
COMMENT ON COLUMN catalog_models.serialization_format IS '모델 직렬화 포맷 (예: cloudpickle)';
COMMENT ON COLUMN catalog_models.sklearn_version IS 'scikit-learn 버전';
COMMENT ON COLUMN catalog_models.mlflow_version IS 'MLflow 버전';
COMMENT ON COLUMN catalog_models.mlflow_model_id IS 'MLflow 모델 식별자';
COMMENT ON COLUMN catalog_models.model_size_bytes IS '모델 아티팩트 크기(byte)';
COMMENT ON COLUMN catalog_models.utc_time_created IS 'MLmodel 생성 시각 (UTC 원본 문자열)';
COMMENT ON COLUMN catalog_models.time_created IS '생성 시각 (서버 로컬 타임존 변환값)';
COMMENT ON COLUMN catalog_models.requirements IS 'requirements.txt 원본 내용';
COMMENT ON COLUMN catalog_models.conda IS 'conda.yaml 원본 내용';
COMMENT ON COLUMN catalog_models.python_env IS 'python_env.yaml 원본 내용';
COMMENT ON COLUMN catalog_models.created_at IS '레코드 생성 시각';
COMMENT ON COLUMN catalog_models.manifest IS 'OCI 매니페스트 원본 내용';
COMMENT ON COLUMN catalog_models.config IS 'OCI config 원본 내용';
COMMENT ON COLUMN catalog_models.content_digest IS 'OCI 콘텐츠 다이제스트 해시';
COMMENT ON COLUMN catalog_models.source_type IS '모델 출처 (mlflow/huggingface/local/oras)';

-- catalog_oci_model_download_log
COMMENT ON COLUMN catalog_oci_model_download_log.id IS 'PK';
COMMENT ON COLUMN catalog_oci_model_download_log.model_name IS '다운로드 대상 모델 이름';
COMMENT ON COLUMN catalog_oci_model_download_log.version IS '다운로드 대상 모델 버전';
COMMENT ON COLUMN catalog_oci_model_download_log.download_type IS '다운로드 유형 (download/pull)';
COMMENT ON COLUMN catalog_oci_model_download_log.client_ip IS '요청 클라이언트 IP (IPv4/IPv6)';
COMMENT ON COLUMN catalog_oci_model_download_log.user_agent IS '요청 클라이언트 User-Agent';
COMMENT ON COLUMN catalog_oci_model_download_log.downloaded_at IS '다운로드 발생 시각';

-- catalog_oci_model_lineage
COMMENT ON COLUMN catalog_oci_model_lineage.id IS 'PK';
COMMENT ON COLUMN catalog_oci_model_lineage.model_id IS '대상 OCI 모델 (catalog_oci_models(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_oci_model_lineage.source_type IS '연관 자원 유형 (dataset/model/huggingface 등)';
COMMENT ON COLUMN catalog_oci_model_lineage.source_id IS '연관 자원 식별자';
COMMENT ON COLUMN catalog_oci_model_lineage.source_name IS '연관 자원 표시명';
COMMENT ON COLUMN catalog_oci_model_lineage.relation_type IS '관계 유형 (trained_on/derived_from 등)';
COMMENT ON COLUMN catalog_oci_model_lineage.description IS '관계 설명';
COMMENT ON COLUMN catalog_oci_model_lineage.created_at IS '생성 시각';

-- catalog_oci_model_tags
COMMENT ON COLUMN catalog_oci_model_tags.id IS 'PK';
COMMENT ON COLUMN catalog_oci_model_tags.model_id IS 'OCI 모델 ID (catalog_oci_models(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_oci_model_tags.tag_id IS '태그 ID (catalog_tags(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_oci_model_tags.created_at IS '생성 시각';

-- catalog_oci_model_versions
COMMENT ON COLUMN catalog_oci_model_versions.id IS 'PK';
COMMENT ON COLUMN catalog_oci_model_versions.model_id IS '소속 모델 (catalog_oci_models(id) 참조, CASCADE 삭제)';
COMMENT ON COLUMN catalog_oci_model_versions.version IS '모델 버전 번호 (model_id별 UNIQUE)';
COMMENT ON COLUMN catalog_oci_model_versions.manifest IS 'OCI manifest.json 원문';
COMMENT ON COLUMN catalog_oci_model_versions.content_digest IS '아티팩트 콘텐츠 다이제스트 (sha256 해시)';
COMMENT ON COLUMN catalog_oci_model_versions.file_count IS '버전에 포함된 파일 개수';
COMMENT ON COLUMN catalog_oci_model_versions.total_size IS '아티팩트 전체 크기(byte)';
COMMENT ON COLUMN catalog_oci_model_versions.metadata IS '추가 메타데이터 (JSONB)';
COMMENT ON COLUMN catalog_oci_model_versions.status IS '버전 상태 (ready 등)';
COMMENT ON COLUMN catalog_oci_model_versions.created_at IS '생성 시각';

-- catalog_oci_models
COMMENT ON COLUMN catalog_oci_models.id IS 'PK';
COMMENT ON COLUMN catalog_oci_models.name IS '모델 고유 이름 (UNIQUE, 식별자)';
COMMENT ON COLUMN catalog_oci_models.display_name IS '화면 표시용 모델 이름';
COMMENT ON COLUMN catalog_oci_models.description IS '모델 요약 설명';
COMMENT ON COLUMN catalog_oci_models.readme IS '모델 상세 문서 (Markdown README)';
COMMENT ON COLUMN catalog_oci_models.task IS '수행 작업 유형 (예: classification/nlp 등 task 코드)';
COMMENT ON COLUMN catalog_oci_models.framework IS '사용 프레임워크 (예: pytorch/tensorflow)';
COMMENT ON COLUMN catalog_oci_models.language IS '지원 언어 코드 (예: ko/en)';
COMMENT ON COLUMN catalog_oci_models.license IS '라이선스 식별자 (예: Apache-2.0)';
COMMENT ON COLUMN catalog_oci_models.source_type IS '모델 출처 유형 (예: huggingface/upload 등)';
COMMENT ON COLUMN catalog_oci_models.source_id IS '출처 식별자 (원본 모델 ID/경로)';
COMMENT ON COLUMN catalog_oci_models.source_revision IS '출처 리비전 (커밋/브랜치/태그)';
COMMENT ON COLUMN catalog_oci_models.bucket IS '아티팩트 저장 OCI 버킷명';
COMMENT ON COLUMN catalog_oci_models.storage_prefix IS '버킷 내 저장 경로 prefix';
COMMENT ON COLUMN catalog_oci_models.owner IS '모델 소유자(담당자)';
COMMENT ON COLUMN catalog_oci_models.version_count IS '등록된 버전 수 (비정규화 집계)';
COMMENT ON COLUMN catalog_oci_models.total_size IS '전체 아티팩트 크기(byte, 비정규화 집계)';
COMMENT ON COLUMN catalog_oci_models.download_count IS '누적 다운로드 횟수 (비정규화 집계)';
COMMENT ON COLUMN catalog_oci_models.status IS '라이프사이클 상태 (draft/review/approved/production/deprecated/archived)';
COMMENT ON COLUMN catalog_oci_models.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_oci_models.updated_at IS '수정 시각';

-- catalog_organizations
COMMENT ON TABLE catalog_organizations IS '조직 계층(부서/팀) 트리를 저장하는 마스터 테이블';
COMMENT ON COLUMN catalog_organizations.id IS 'PK';
COMMENT ON COLUMN catalog_organizations.code IS 'URL/외부참조용 slug (고유, 자동 생성)';
COMMENT ON COLUMN catalog_organizations.name IS '조직 이름 (동일 부모 내 중복 불가)';
COMMENT ON COLUMN catalog_organizations.parent_id IS '상위 조직 ID (catalog_organizations(id) 참조, NULL=루트)';
COMMENT ON COLUMN catalog_organizations.description IS '조직 상세 설명';
COMMENT ON COLUMN catalog_organizations.sort_order IS '같은 부모 내 형제 정렬 순서';
COMMENT ON COLUMN catalog_organizations.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_organizations.updated_at IS '수정 시각';

-- catalog_owners
COMMENT ON COLUMN catalog_owners.id IS 'PK';
COMMENT ON COLUMN catalog_owners.dataset_id IS '대상 데이터셋 (catalog_datasets(id) 참조)';
COMMENT ON COLUMN catalog_owners.owner_name IS '소유자 이름(사람 또는 팀)';
COMMENT ON COLUMN catalog_owners.owner_type IS '소유자 유형 (TECHNICAL_OWNER/BUSINESS_OWNER 등)';
COMMENT ON COLUMN catalog_owners.created_at IS '생성 시각';

-- catalog_quality_result
COMMENT ON COLUMN catalog_quality_result.id IS 'PK';
COMMENT ON COLUMN catalog_quality_result.rule_id IS '품질 규칙 ID (catalog_quality_rule(id) 참조)';
COMMENT ON COLUMN catalog_quality_result.dataset_id IS '대상 데이터셋 ID (catalog_datasets(id) 참조)';
COMMENT ON COLUMN catalog_quality_result.passed IS '규칙 통과 여부 (true/false)';
COMMENT ON COLUMN catalog_quality_result.actual_value IS '실제 측정값 또는 위반 건수';
COMMENT ON COLUMN catalog_quality_result.detail IS '실행 상세 메시지/사유';
COMMENT ON COLUMN catalog_quality_result.checked_at IS '검사 실행 시각';

-- catalog_quality_rule
COMMENT ON COLUMN catalog_quality_rule.id IS 'PK';
COMMENT ON COLUMN catalog_quality_rule.dataset_id IS '검사 대상 데이터셋 (catalog_datasets(id) 참조)';
COMMENT ON COLUMN catalog_quality_rule.rule_name IS '룰 이름';
COMMENT ON COLUMN catalog_quality_rule.check_type IS '검사 유형 (NOT_NULL/UNIQUE/MIN_VALUE/MAX_VALUE/ACCEPTED_VALUES/REGEX/ROW_COUNT/FRESHNESS/CUSTOM_SQL)';
COMMENT ON COLUMN catalog_quality_rule.column_name IS '검사 대상 컬럼명 (테이블 단위 검사면 NULL)';
COMMENT ON COLUMN catalog_quality_rule.expected_value IS '기대값·검사 설정 (JSON)';
COMMENT ON COLUMN catalog_quality_rule.threshold IS '통과 임계값 (%)';
COMMENT ON COLUMN catalog_quality_rule.severity IS '심각도 (WARNING/ERROR 등, 기본 WARNING)';
COMMENT ON COLUMN catalog_quality_rule.is_active IS '활성화 여부 (true/false)';
COMMENT ON COLUMN catalog_quality_rule.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_quality_rule.updated_at IS '수정 시각';

-- catalog_quality_score
COMMENT ON COLUMN catalog_quality_score.id IS 'PK';
COMMENT ON COLUMN catalog_quality_score.dataset_id IS '대상 데이터셋 (catalog_datasets(id) 참조, 삭제 시 CASCADE)';
COMMENT ON COLUMN catalog_quality_score.score IS '품질 점수 (통과/전체*100, %)';
COMMENT ON COLUMN catalog_quality_score.total_rules IS '평가한 전체 규칙 수';
COMMENT ON COLUMN catalog_quality_score.passed_rules IS '통과한 규칙 수';
COMMENT ON COLUMN catalog_quality_score.warning_rules IS '경고(WARNING) 발생 규칙 수';
COMMENT ON COLUMN catalog_quality_score.failed_rules IS '실패한 규칙 수';
COMMENT ON COLUMN catalog_quality_score.scored_at IS '점수 산정 시각';

-- catalog_registered_models
COMMENT ON COLUMN catalog_registered_models.id IS 'PK';
COMMENT ON COLUMN catalog_registered_models.name IS '3단 모델 이름 (catalog.schema.model, 전역 고유)';
COMMENT ON COLUMN catalog_registered_models.urn IS '모델 URN ({name}.{ENV}.model, 전역 고유)';
COMMENT ON COLUMN catalog_registered_models.datasource_id IS '연결된 데이터소스 (catalog_datasources(id) 참조, 삭제 시 NULL)';
COMMENT ON COLUMN catalog_registered_models.description IS '모델 설명';
COMMENT ON COLUMN catalog_registered_models.owner IS '모델 소유자';
COMMENT ON COLUMN catalog_registered_models.storage_location IS '아티팩트 기본 저장 경로 (file:// 또는 s3://)';
COMMENT ON COLUMN catalog_registered_models.max_version_number IS '생성된 최대 버전 번호 (버전마다 자동 증가)';
COMMENT ON COLUMN catalog_registered_models.status IS '모델 상태 (active/deleted, soft delete)';
COMMENT ON COLUMN catalog_registered_models.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_registered_models.updated_at IS '수정 시각';
COMMENT ON COLUMN catalog_registered_models.created_by IS '생성자';
COMMENT ON COLUMN catalog_registered_models.updated_by IS '수정자';
COMMENT ON COLUMN catalog_registered_models.storage_type IS '저장소 유형 (local/s3)';
COMMENT ON COLUMN catalog_registered_models.bucket_name IS 'S3 버킷 이름 (storage_type=s3 일 때)';

-- catalog_schema_snapshots
COMMENT ON COLUMN catalog_schema_snapshots.id IS 'PK';
COMMENT ON COLUMN catalog_schema_snapshots.dataset_id IS '대상 데이터셋 (catalog_datasets(id) 참조, ON DELETE CASCADE)';
COMMENT ON COLUMN catalog_schema_snapshots.synced_at IS '스냅샷 생성(동기화) 시각';
COMMENT ON COLUMN catalog_schema_snapshots.schema_json IS '전체 스키마 본문 (JSON 배열)';
COMMENT ON COLUMN catalog_schema_snapshots.field_count IS '스냅샷의 컬럼/필드 개수';
COMMENT ON COLUMN catalog_schema_snapshots.change_summary IS '변경 요약 (예: "Added 2, Modified 1, Dropped 1")';
COMMENT ON COLUMN catalog_schema_snapshots.changes_json IS '개별 변경 내역 (JSON 배열, ADD/MODIFY/DROP)';

-- catalog_standard_change_log
COMMENT ON COLUMN catalog_standard_change_log.id IS 'PK';
COMMENT ON COLUMN catalog_standard_change_log.entity_type IS '변경 대상 엔티티 유형 (WORD/DOMAIN/TERM/CODE_GROUP/CODE_VALUE)';
COMMENT ON COLUMN catalog_standard_change_log.entity_id IS '변경 대상 엔티티 ID';
COMMENT ON COLUMN catalog_standard_change_log.change_type IS '변경 유형 (CREATE/UPDATE/DELETE)';
COMMENT ON COLUMN catalog_standard_change_log.field_name IS '변경된 필드명';
COMMENT ON COLUMN catalog_standard_change_log.old_value IS '변경 전 값';
COMMENT ON COLUMN catalog_standard_change_log.new_value IS '변경 후 값';
COMMENT ON COLUMN catalog_standard_change_log.changed_by IS '변경 수행자';
COMMENT ON COLUMN catalog_standard_change_log.changed_at IS '변경 시각';

-- catalog_standard_dictionary
COMMENT ON COLUMN catalog_standard_dictionary.id IS 'PK';
COMMENT ON COLUMN catalog_standard_dictionary.dict_name IS '표준 사전 이름 (고유)';
COMMENT ON COLUMN catalog_standard_dictionary.description IS '표준 사전 설명';
COMMENT ON COLUMN catalog_standard_dictionary.version IS '표준 사전 버전';
COMMENT ON COLUMN catalog_standard_dictionary.status IS '상태 (ACTIVE/INACTIVE)';
COMMENT ON COLUMN catalog_standard_dictionary.effective_date IS '적용 시작일';
COMMENT ON COLUMN catalog_standard_dictionary.expiry_date IS '적용 만료일';
COMMENT ON COLUMN catalog_standard_dictionary.created_by IS '생성자';
COMMENT ON COLUMN catalog_standard_dictionary.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_standard_dictionary.updated_at IS '수정 시각';

-- catalog_standard_domain
COMMENT ON COLUMN catalog_standard_domain.id IS 'PK';
COMMENT ON COLUMN catalog_standard_domain.dictionary_id IS '소속 표준 사전 (catalog_standard_dictionary(id) 참조)';
COMMENT ON COLUMN catalog_standard_domain.domain_name IS '도메인명 (예: 번호, 금액)';
COMMENT ON COLUMN catalog_standard_domain.domain_group IS '도메인 그룹 (예: 문자형, 숫자형)';
COMMENT ON COLUMN catalog_standard_domain.data_type IS '데이터 타입 (예: VARCHAR)';
COMMENT ON COLUMN catalog_standard_domain.data_length IS '데이터 길이';
COMMENT ON COLUMN catalog_standard_domain.data_precision IS '숫자 정밀도 (전체 자릿수)';
COMMENT ON COLUMN catalog_standard_domain.data_scale IS '숫자 스케일 (소수 자릿수)';
COMMENT ON COLUMN catalog_standard_domain.description IS '도메인 설명';
COMMENT ON COLUMN catalog_standard_domain.code_group_id IS '연결 코드 그룹 (catalog_code_group(id) 참조)';
COMMENT ON COLUMN catalog_standard_domain.status IS '상태 (ACTIVE/INACTIVE)';
COMMENT ON COLUMN catalog_standard_domain.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_standard_domain.updated_at IS '수정 시각';

-- catalog_standard_term
COMMENT ON COLUMN catalog_standard_term.id IS 'PK';
COMMENT ON COLUMN catalog_standard_term.dictionary_id IS '소속 표준 사전 (catalog_standard_dictionary(id) 참조)';
COMMENT ON COLUMN catalog_standard_term.term_name IS '용어 한글명 (예: 고객번호)';
COMMENT ON COLUMN catalog_standard_term.term_english IS '용어 영문명 (예: Customer Number)';
COMMENT ON COLUMN catalog_standard_term.term_abbr IS '용어 영문약어 (예: CUST_NO)';
COMMENT ON COLUMN catalog_standard_term.physical_name IS '물리 컬럼명 (예: cust_no)';
COMMENT ON COLUMN catalog_standard_term.domain_id IS '연결된 표준 도메인 (catalog_standard_domain(id) 참조)';
COMMENT ON COLUMN catalog_standard_term.description IS '용어 설명';
COMMENT ON COLUMN catalog_standard_term.status IS '용어 상태 (ACTIVE/INACTIVE)';
COMMENT ON COLUMN catalog_standard_term.created_by IS '생성자';
COMMENT ON COLUMN catalog_standard_term.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_standard_term.updated_at IS '수정 시각';

-- catalog_standard_term_words
COMMENT ON COLUMN catalog_standard_term_words.id IS 'PK';
COMMENT ON COLUMN catalog_standard_term_words.term_id IS '표준 용어 ID (catalog_standard_term(id) 참조, 삭제 시 CASCADE)';
COMMENT ON COLUMN catalog_standard_term_words.word_id IS '구성 단어 ID (catalog_standard_word(id) 참조, 삭제 시 CASCADE)';
COMMENT ON COLUMN catalog_standard_term_words.ordinal IS '용어 내 단어 배치 순서 (term_id, word_id와 UNIQUE)';

-- catalog_standard_word
COMMENT ON COLUMN catalog_standard_word.id IS 'PK';
COMMENT ON COLUMN catalog_standard_word.dictionary_id IS '소속 표준 사전 (catalog_standard_dictionary(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_standard_word.word_name IS '단어 한글명 (예: 고객)';
COMMENT ON COLUMN catalog_standard_word.word_english IS '단어 영문명 (예: Customer)';
COMMENT ON COLUMN catalog_standard_word.word_abbr IS '단어 영문 약어 (예: CUST)';
COMMENT ON COLUMN catalog_standard_word.description IS '단어 설명';
COMMENT ON COLUMN catalog_standard_word.word_type IS '단어 유형 (GENERAL/SUFFIX/PREFIX)';
COMMENT ON COLUMN catalog_standard_word.is_forbidden IS '금칙어 여부 (true/false)';
COMMENT ON COLUMN catalog_standard_word.synonym_group_id IS '이음동의어 그룹 식별자';
COMMENT ON COLUMN catalog_standard_word.status IS '단어 상태 (ACTIVE 등)';
COMMENT ON COLUMN catalog_standard_word.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_standard_word.updated_at IS '수정 시각';

-- catalog_systems
COMMENT ON TABLE catalog_systems IS '조직이 운영하는 데이터 소스 묶음(시스템/앱/서비스 배포) 정의';
COMMENT ON COLUMN catalog_systems.id IS 'PK';
COMMENT ON COLUMN catalog_systems.code IS '시스템 고유 코드 (전역 unique)';
COMMENT ON COLUMN catalog_systems.name IS '시스템 이름 (org_id 내 unique)';
COMMENT ON COLUMN catalog_systems.org_id IS '소속 조직 catalog_organizations(id) 참조 (NULL=미분류)';
COMMENT ON COLUMN catalog_systems.summary IS '한 줄 요약';
COMMENT ON COLUMN catalog_systems.description IS '상세 설명';
COMMENT ON COLUMN catalog_systems.owner IS '담당자';
COMMENT ON COLUMN catalog_systems.status IS '상태 (ACTIVE/INACTIVE/DEPRECATED)';
COMMENT ON COLUMN catalog_systems.sort_order IS '표시 정렬 순서';
COMMENT ON COLUMN catalog_systems.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_systems.updated_at IS '수정 시각';

-- catalog_tags
COMMENT ON COLUMN catalog_tags.id IS 'PK';
COMMENT ON COLUMN catalog_tags.name IS '태그 이름 (고유)';
COMMENT ON COLUMN catalog_tags.description IS '태그 설명';
COMMENT ON COLUMN catalog_tags.color IS '표시 색상 (HEX, 예: #3b82f6)';
COMMENT ON COLUMN catalog_tags.created_at IS '생성 시각';

-- catalog_taxonomies
COMMENT ON TABLE catalog_taxonomies IS '분류체계(scheme) — 이름 붙은 분류 트리의 루트 정의';
COMMENT ON COLUMN catalog_taxonomies.id IS 'PK';
COMMENT ON COLUMN catalog_taxonomies.code IS '분류체계 slug 코드 (자동 생성, 고유)';
COMMENT ON COLUMN catalog_taxonomies.name IS '분류체계 이름 (예: 업무 도메인, 데이터 등급, 고유)';
COMMENT ON COLUMN catalog_taxonomies.description IS '분류체계 상세 설명';
COMMENT ON COLUMN catalog_taxonomies.sort_order IS '형제 분류체계 정렬 순서';
COMMENT ON COLUMN catalog_taxonomies.created_at IS '생성 시각';
COMMENT ON COLUMN catalog_taxonomies.updated_at IS '수정 시각';

-- catalog_term_column_mapping
COMMENT ON COLUMN catalog_term_column_mapping.id IS 'PK';
COMMENT ON COLUMN catalog_term_column_mapping.term_id IS '표준 용어 ID (catalog_standard_term(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_term_column_mapping.dataset_id IS '데이터셋 ID (catalog_datasets(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_term_column_mapping.schema_id IS '데이터셋 컬럼/스키마 ID (catalog_dataset_schemas(id) 참조, CASCADE)';
COMMENT ON COLUMN catalog_term_column_mapping.mapping_type IS '매핑 유형 (MATCHED/SIMILAR/VIOLATION)';
COMMENT ON COLUMN catalog_term_column_mapping.created_at IS '생성 시각';


-- ===========================================================================
-- Federation (카탈로그 페더레이션) — 연합 대상 peer 인스턴스 레지스트리
-- ===========================================================================

CREATE TABLE IF NOT EXISTS federation_instances (
    id SERIAL PRIMARY KEY,
    instance_key VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    base_url VARCHAR(500) NOT NULL,
    auth_token TEXT,
    mode VARCHAR(20) NOT NULL DEFAULT 'LIVE',
    sync_interval_sec INT NOT NULL DEFAULT 900,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    -- 소비자(허브) 표시 선택 — 이 peer 에서 화면에 표시할 capability 키 JSON 배열(NULL=전부)
    display_fields TEXT,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 기존 배포(컬럼이 없던 DB)도 멱등하게 수렴
ALTER TABLE federation_instances ADD COLUMN IF NOT EXISTS display_fields TEXT;

COMMENT ON TABLE federation_instances IS '연합(federation) 대상 peer Argus Catalog 인스턴스 레지스트리';
COMMENT ON COLUMN federation_instances.instance_key IS '허브 내 peer 식별 키 (URN namespace prefix)';
COMMENT ON COLUMN federation_instances.base_url IS 'peer base URL (예: https://catalog.team-a.internal)';
COMMENT ON COLUMN federation_instances.auth_token IS 'peer 호출용 서비스 토큰(선택). Phase 1 에서 암호화 전환 예정';
COMMENT ON COLUMN federation_instances.mode IS '연합 모드 (HARVEST/LIVE/HYBRID)';
COMMENT ON COLUMN federation_instances.sync_interval_sec IS 'HARVEST 동기화 주기(초) — 후속 단계 사용';
COMMENT ON COLUMN federation_instances.status IS '상태 (ACTIVE=연합검색 포함 / PAUSED=제외)';


-- ===========================================================================
-- Federation HARVEST 미러 — peer 메타데이터 로컬 복제 + 허브 모델 재임베딩
-- ===========================================================================

CREATE TABLE IF NOT EXISTS federation_datasets (
    id SERIAL PRIMARY KEY,
    instance_id INT NOT NULL REFERENCES federation_instances(id) ON DELETE CASCADE,
    remote_urn VARCHAR(500) NOT NULL,
    federated_urn VARCHAR(600) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    display_name VARCHAR(255),
    datasource_name VARCHAR(200),
    datasource_type VARCHAR(100),
    summary VARCHAR(200),
    description TEXT,
    qualified_name VARCHAR(500),
    origin VARCHAR(50),
    field_count INT NOT NULL DEFAULT 0,
    has_sample BOOLEAN NOT NULL DEFAULT false,
    source_text TEXT,
    remote_created_at TIMESTAMPTZ,
    remote_updated_at TIMESTAMPTZ,
    harvested_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (instance_id, remote_urn)
);
-- 기존 배포(컬럼이 없던 DB)도 멱등하게 수렴
ALTER TABLE federation_datasets ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);
ALTER TABLE federation_datasets ADD COLUMN IF NOT EXISTS field_count INT NOT NULL DEFAULT 0;
ALTER TABLE federation_datasets ADD COLUMN IF NOT EXISTS has_sample BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE federation_datasets ADD COLUMN IF NOT EXISTS remote_created_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_federation_datasets_instance
    ON federation_datasets (instance_id);

CREATE TABLE IF NOT EXISTS federation_dataset_embeddings (
    id SERIAL PRIMARY KEY,
    federation_dataset_id INT NOT NULL UNIQUE
        REFERENCES federation_datasets(id) ON DELETE CASCADE,
    embedding vector(384) NOT NULL,
    source_text TEXT NOT NULL,
    model_name VARCHAR(200) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    dimension INT NOT NULL DEFAULT 384,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_federation_dataset_embeddings_ivfflat
    ON federation_dataset_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE TABLE IF NOT EXISTS federation_sync_runs (
    id SERIAL PRIMARY KEY,
    instance_id INT NOT NULL REFERENCES federation_instances(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING',
    datasets_total INT NOT NULL DEFAULT 0,
    datasets_seen INT NOT NULL DEFAULT 0,
    datasets_upserted INT NOT NULL DEFAULT 0,
    datasets_embedded INT NOT NULL DEFAULT 0,
    datasets_pruned INT NOT NULL DEFAULT 0,
    phase VARCHAR(20) NOT NULL DEFAULT 'FETCH',
    phase_done INT NOT NULL DEFAULT 0,
    phase_total INT NOT NULL DEFAULT 0,
    error TEXT,
    started_at TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ
);
-- 기존 배포(컬럼이 없던 DB)도 멱등하게 수렴
ALTER TABLE federation_sync_runs ADD COLUMN IF NOT EXISTS datasets_total INT NOT NULL DEFAULT 0;
ALTER TABLE federation_sync_runs ADD COLUMN IF NOT EXISTS phase VARCHAR(20) NOT NULL DEFAULT 'FETCH';
ALTER TABLE federation_sync_runs ADD COLUMN IF NOT EXISTS phase_done INT NOT NULL DEFAULT 0;
ALTER TABLE federation_sync_runs ADD COLUMN IF NOT EXISTS phase_total INT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_federation_sync_runs_instance
    ON federation_sync_runs (instance_id);

COMMENT ON TABLE federation_datasets IS 'HARVEST 로 가져온 peer 데이터셋 로컬 미러 (read-only)';
COMMENT ON TABLE federation_dataset_embeddings IS 'HARVEST 미러 데이터셋의 허브 모델 재임베딩 (pgvector)';
COMMENT ON TABLE federation_sync_runs IS '페더레이션 HARVEST 실행 이력 (관측성)';


-- ===========================================================================
-- Federation 리니지 미러 — peer 리니지 엣지(URN→URN) cross-instance stitching
-- ===========================================================================

CREATE TABLE IF NOT EXISTS federation_lineage (
    id SERIAL PRIMARY KEY,
    instance_id INT NOT NULL REFERENCES federation_instances(id) ON DELETE CASCADE,
    source_urn VARCHAR(500) NOT NULL,
    target_urn VARCHAR(500) NOT NULL,
    relation_type VARCHAR(32) NOT NULL DEFAULT 'READ_WRITE',
    lineage_source VARCHAR(32) NOT NULL DEFAULT 'QUERY_AGGREGATED',
    description TEXT,
    harvested_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (instance_id, source_urn, target_urn, relation_type)
);
CREATE INDEX IF NOT EXISTS ix_federation_lineage_instance
    ON federation_lineage (instance_id);
CREATE INDEX IF NOT EXISTS ix_federation_lineage_source ON federation_lineage (source_urn);
CREATE INDEX IF NOT EXISTS ix_federation_lineage_target ON federation_lineage (target_urn);

COMMENT ON TABLE federation_lineage IS 'HARVEST 로 가져온 peer 리니지 엣지(URN→URN, cross-instance stitching)';
