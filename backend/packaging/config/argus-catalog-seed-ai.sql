-- ============================================================================
-- Argus Catalog — AI 카탈로그 데모 시드 데이터 (PostgreSQL)
--
-- Sakila(DVD 대여) 데모 세계관과 연결된 AI 자산을 등록한다.
--
-- 구성:
--   1. MLflow 모델 3종 (catalog_registered_models + 버전 + 메트릭)
--      영화 추천 / 회원 이탈 예측 / 대여 수요 예측
--   2. OCI 모델 허브 3종 (catalog_oci_models + 버전 + 리니지)
--      LLM / 임베딩 / 사내 추천모델 ONNX 아티팩트
--   3. AI Agent 3종 (catalog_ai_agents + 도구 + MCP 서버 + 버전)
--      카탈로그 메타데이터 도우미 / 대여 고객지원 / 품질 감시
--
-- 실행:
--   psql -U <user> -d <db> -f argus-catalog-seed-ai.sql
--
-- 특징:
--   - 재실행 안전(idempotent): ON CONFLICT DO NOTHING / WHERE NOT EXISTS
--   - argus-catalog-seed-demo.sql / -sakila.sql 과 독립 실행 가능
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. MLflow 모델 (catalog_registered_models)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_registered_models
    (name, urn, description, owner, storage_type, storage_location,
     max_version_number, status, created_by)
VALUES
    ('argus.ml.film_recommender',
     'argus.ml.film_recommender.PROD.model',
     '영화 추천 모델 — 회원의 대여 이력(rental)과 장르 선호를 학습해 다음 대여 후보를 추천한다. 협업 필터링(implicit ALS) 기반.',
     'mjkim', 'local', '/var/lib/argus-catalog-server/models/film_recommender',
     2, 'active', 'admin'),
    ('argus.ml.churn_predictor',
     'argus.ml.churn_predictor.PROD.model',
     '회원 이탈 예측 모델 — 최근 90일 대여 빈도·연체 이력·결제 패턴으로 휴면 전환 확률을 예측한다. scikit-learn GradientBoosting.',
     'jypark', 'local', '/var/lib/argus-catalog-server/models/churn_predictor',
     1, 'active', 'admin'),
    ('argus.ml.rental_demand_forecast',
     'argus.ml.rental_demand_forecast.PROD.model',
     '대여 수요 예측 모델 — 점포·장르별 일간 대여량을 2주 선행 예측해 재고 배치에 활용한다. 시계열(Prophet) 기반.',
     'tkoh', 'local', '/var/lib/argus-catalog-server/models/rental_demand_forecast',
     1, 'active', 'admin')
ON CONFLICT (name) DO NOTHING;

-- 1.1 모델 버전
INSERT INTO catalog_model_versions
    (model_id, version, source, run_id, description, status, stage, artifact_count, artifact_size, created_by)
SELECT m.id, v.version, v.source, v.run_id, v.description, 'READY', v.stage, v.artifact_count, v.artifact_size, 'admin'
FROM (VALUES
    ('argus.ml.film_recommender', 1, 'mlflow-artifacts:/1/a1b2c3/artifacts/model', 'a1b2c3d4e5f60001',
     '최초 학습 — 2024년 대여 이력 전체', 'Archived', 6, 18874368),
    ('argus.ml.film_recommender', 2, 'mlflow-artifacts:/1/f6e5d4/artifacts/model', 'f6e5d4c3b2a10002',
     '장르 피처 추가 + 하이퍼파라미터 튜닝 (factors 128)', 'Production', 6, 19922944),
    ('argus.ml.churn_predictor', 1, 'mlflow-artifacts:/2/09f8e7/artifacts/model', '09f8e7d6c5b40003',
     '연체 횟수·평균 대여 간격 피처 기반 베이스라인', 'Production', 5, 2097152),
    ('argus.ml.rental_demand_forecast', 1, 'mlflow-artifacts:/3/778899/artifacts/model', '7788990011220004',
     '점포×장르 시계열 96개 학습', 'Staging', 4, 5242880)
) AS v(model_name, version, source, run_id, description, stage, artifact_count, artifact_size)
JOIN catalog_registered_models m ON m.name = v.model_name
ON CONFLICT (model_id, version) DO NOTHING;

-- 1.2 버전별 메트릭 (Metrics 탭 버전 비교용)
INSERT INTO catalog_model_metrics (model_id, version, metric_key, metric_value)
SELECT m.id, mt.version, mt.metric_key, mt.metric_value
FROM (VALUES
    ('argus.ml.film_recommender', 1, 'precision_at_10', 0.231),
    ('argus.ml.film_recommender', 1, 'recall_at_10',    0.187),
    ('argus.ml.film_recommender', 2, 'precision_at_10', 0.284),
    ('argus.ml.film_recommender', 2, 'recall_at_10',    0.226),
    ('argus.ml.churn_predictor', 1, 'roc_auc',   0.873),
    ('argus.ml.churn_predictor', 1, 'f1',        0.642),
    ('argus.ml.churn_predictor', 1, 'precision', 0.701),
    ('argus.ml.rental_demand_forecast', 1, 'mape_2w',  0.184),
    ('argus.ml.rental_demand_forecast', 1, 'rmse_2w', 12.700)
) AS mt(model_name, version, metric_key, metric_value)
JOIN catalog_registered_models m ON m.name = mt.model_name
ON CONFLICT (model_id, version, metric_key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. OCI 모델 허브 (catalog_oci_models)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_oci_models
    (name, display_name, description, readme, task, framework, language, license,
     source_type, source_id, bucket, storage_prefix, owner,
     version_count, total_size, download_count, status)
VALUES
    ('llm-qwen2.5-7b-instruct', 'Qwen2.5 7B Instruct',
     '사내 표준 범용 LLM — 카탈로그 메타데이터 생성(설명/태그/PII)과 AI Agent 의 기반 모델로 사용.',
     E'# Qwen2.5 7B Instruct\n\n사내 GPU 서빙 표준 LLM 입니다.\n\n- 용도: 메타데이터 자동 생성, 에이전트 기반 모델\n- 서빙: vLLM (OpenAI 호환 엔드포인트)\n- 한국어/영어 지원',
     'text-generation', 'transformers', 'multilingual', 'apache-2.0',
     'huggingface', 'Qwen/Qwen2.5-7B-Instruct', 'argus-models', 'llm-qwen2.5-7b-instruct',
     'admin', 1, 15728640000, 42, 'production'),
    ('embedding-bge-m3', 'BGE-M3 Embedding',
     '시맨틱 검색용 다국어 임베딩 모델 — 카탈로그 통합 검색(데이터셋/용어집/Agent/API)의 임베딩 백엔드 후보.',
     E'# BGE-M3\n\n다국어 임베딩 모델 (1024차원).\n\n- 용도: 카탈로그 시맨틱 검색 임베딩\n- 짧은 질의·긴 문서 모두 안정적',
     'feature-extraction', 'sentence-transformers', 'multilingual', 'mit',
     'huggingface', 'BAAI/bge-m3', 'argus-models', 'embedding-bge-m3',
     'admin', 1, 2415919104, 18, 'approved'),
    ('ml-film-recommender-onnx', 'Film Recommender (ONNX)',
     'MLflow 의 argus.ml.film_recommender v2 를 ONNX 로 변환한 서빙용 아티팩트 — 추천 API 의 경량 추론에 사용.',
     E'# Film Recommender ONNX\n\nMLflow film_recommender v2 의 ONNX 변환본.\n\n- 입력: customer_id 임베딩 + 후보 film 피처\n- 출력: 선호 점수 (float32)',
     'recommendation', 'onnx', 'ko', 'proprietary',
     'mlflow', 'argus.ml.film_recommender', 'argus-models', 'ml-film-recommender-onnx',
     'mjkim', 1, 20971520, 7, 'review')
ON CONFLICT (name) DO NOTHING;

-- 2.1 OCI 모델 버전
INSERT INTO catalog_oci_model_versions
    (model_id, version, content_digest, file_count, total_size, status)
SELECT m.id, v.version, v.digest, v.file_count, v.total_size, 'ready'
FROM (VALUES
    ('llm-qwen2.5-7b-instruct',   1, 'sha256:3f1a9c2e7b8d45f6a0c1e2d3b4a5968712cd34ef56ab78cd90ef12ab34cd56ef', 14, 15728640000),
    ('embedding-bge-m3',          1, 'sha256:9b8c7d6e5f4a3210fedcba9876543210ab12cd34ef56ab78cd90ef12ab34cd56',  9, 2415919104),
    ('ml-film-recommender-onnx',  1, 'sha256:0a1b2c3d4e5f6789abcdef0123456789ab12cd34ef56ab78cd90ef12ab34cd56',  3, 20971520)
) AS v(model_name, version, digest, file_count, total_size)
JOIN catalog_oci_models m ON m.name = v.model_name
ON CONFLICT (model_id, version) DO NOTHING;

-- 2.2 OCI 모델 리니지 — 학습 데이터/기반 모델 관계
INSERT INTO catalog_oci_model_lineage (model_id, source_type, source_id, source_name, relation_type, description)
SELECT m.id, l.source_type, l.source_id, l.source_name, l.relation_type, l.description
FROM (VALUES
    ('ml-film-recommender-onnx', 'mlflow', 'argus.ml.film_recommender', 'argus.ml.film_recommender v2',
     'CONVERTED_FROM', 'MLflow Production 버전을 ONNX 로 변환'),
    ('ml-film-recommender-onnx', 'dataset', 'sakila-mysql.sakila.rental.dataset', 'sakila.rental',
     'TRAINED_ON', '대여 트랜잭션 이력으로 학습'),
    ('ml-film-recommender-onnx', 'dataset', 'sakila-mysql.sakila.film.dataset', 'sakila.film',
     'TRAINED_ON', '영화 메타데이터(장르·등급) 피처로 사용')
) AS l(model_name, source_type, source_id, source_name, relation_type, description)
JOIN catalog_oci_models m ON m.name = l.model_name
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_oci_model_lineage x
    WHERE x.model_id = m.id AND x.source_id = l.source_id AND x.relation_type = l.relation_type
);

-- ---------------------------------------------------------------------------
-- 3. AI Agent (catalog_ai_agents)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_ai_agents
    (name, urn, display_name, description, version, status, owner_email, department,
     category, base_model, model_provider, framework, execution_policy,
     endpoint, protocol, streaming, invocation_method, auth_method,
     is_multi_agent, hitl_required, usage_count,
     capabilities, supported_languages, use_cases, tags, created_by)
VALUES
    ('catalog.metadata-assistant', 'catalog.metadata-assistant.agent', '카탈로그 메타데이터 도우미',
     '데이터셋 설명·요약·태그를 생성하고 자연어로 카탈로그를 검색해 주는 도우미 에이전트. Argus Catalog 의 AI 자동 생성 기능을 대화형으로 제공한다.',
     '1.2.0', 'active', 'mjkim@data-dynamics.io', '공정데이터팀',
     'productivity', 'llm-qwen2.5-7b-instruct', 'internal', 'langgraph', 'sandboxed',
     'https://agents.data-dynamics.io/catalog-assistant', 'REST', true, 'sync', 'OAuth2.1',
     false, false, 1248,
     '["metadata-generation", "semantic-search", "pii-detection"]'::json,
     '["ko", "en"]'::json,
     '["데이터셋 설명 초안 작성", "비슷한 데이터셋 찾기", "PII 컬럼 후보 안내"]'::json,
     '["catalog", "demo"]'::json, 'admin'),
    ('cs.rental-support', 'cs.rental-support.agent', '대여 고객지원 에이전트',
     'DVD 대여 고객 문의(대여 이력·연체·환불)를 처리하는 고객지원 에이전트. 환불 처리 같은 중요 액션은 사람 승인(HITL)을 거친다.',
     '0.9.0', 'active', 'khshin@data-dynamics.io', '물류운영팀',
     'customer-support', 'llm-qwen2.5-7b-instruct', 'internal', 'langgraph', 'sandboxed',
     'https://agents.data-dynamics.io/rental-support', 'REST', true, 'streaming', 'OAuth2.1',
     false, true, 305,
     '["rental-lookup", "refund", "overdue-notice"]'::json,
     '["ko", "en"]'::json,
     '["대여 이력 조회 응대", "연체 안내", "환불 접수(승인 필요)"]'::json,
     '["sakila", "demo"]'::json, 'admin'),
    ('ops.quality-watchdog', 'ops.quality-watchdog.agent', '품질 감시 에이전트',
     '품질 배치(quality/*.py) 결과를 모니터링해 점수 하락·규칙 실패를 감지하면 담당자에게 알리는 운영 에이전트. 야간 배치 후 자동 실행된다.',
     '1.0.1', 'active', 'iuhong@data-dynamics.io', '물류데이터팀',
     'operations', 'llm-qwen2.5-7b-instruct', 'internal', 'crewai', 'restricted',
     'https://agents.data-dynamics.io/quality-watchdog', 'REST', false, 'batch', 'ApiKey',
     false, false, 86,
     '["quality-monitoring", "alerting", "report-summary"]'::json,
     '["ko"]'::json,
     '["품질 점수 하락 감지", "실패 규칙 요약 통지", "주간 품질 리포트 작성"]'::json,
     '["quality", "demo"]'::json, 'admin')
ON CONFLICT (name) DO NOTHING;

-- 3.1 에이전트 도구 (스킬) — A2A 카드와 임베딩 소스 텍스트에 노출
INSERT INTO catalog_ai_agent_tools (agent_id, name, description, tool_schema, risk, requires_approval)
SELECT a.id, t.name, t.description, t.tool_schema::json, t.risk, t.requires_approval
FROM (VALUES
    ('catalog.metadata-assistant', 'search_datasets',
     '자연어 질의로 카탈로그 데이터셋을 시맨틱 검색한다',
     '{"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}',
     'low', false),
    ('catalog.metadata-assistant', 'generate_description',
     '데이터셋 스키마를 근거로 설명 초안을 생성한다',
     '{"type": "object", "properties": {"dataset_urn": {"type": "string"}}, "required": ["dataset_urn"]}',
     'low', false),
    ('catalog.metadata-assistant', 'detect_pii',
     '컬럼명·샘플을 분석해 PII 후보 컬럼을 표시한다',
     '{"type": "object", "properties": {"dataset_urn": {"type": "string"}}, "required": ["dataset_urn"]}',
     'medium', false),
    ('cs.rental-support', 'lookup_rental',
     '회원의 대여·반납 이력을 조회한다 (sakila.rental)',
     '{"type": "object", "properties": {"customer_id": {"type": "integer"}}, "required": ["customer_id"]}',
     'low', false),
    ('cs.rental-support', 'send_overdue_notice',
     '연체 회원에게 반납 안내를 발송한다',
     '{"type": "object", "properties": {"rental_id": {"type": "integer"}}, "required": ["rental_id"]}',
     'medium', false),
    ('cs.rental-support', 'process_refund',
     '결제 건을 환불 처리한다 — 금전 액션이므로 사람 승인 필수',
     '{"type": "object", "properties": {"payment_id": {"type": "integer"}, "reason": {"type": "string"}}, "required": ["payment_id", "reason"]}',
     'high', true),
    ('ops.quality-watchdog', 'fetch_quality_scores',
     '데이터 소스의 최신 품질 점수·결과를 API 로 조회한다',
     '{"type": "object", "properties": {"datasource_id": {"type": "string"}}, "required": ["datasource_id"]}',
     'low', false),
    ('ops.quality-watchdog', 'notify_channel',
     '품질 이상을 담당자 채널(메일/웹훅)로 통지한다',
     '{"type": "object", "properties": {"severity": {"type": "string"}, "message": {"type": "string"}}, "required": ["message"]}',
     'medium', false)
) AS t(agent_name, name, description, tool_schema, risk, requires_approval)
JOIN catalog_ai_agents a ON a.name = t.agent_name
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_ai_agent_tools x WHERE x.agent_id = a.id AND x.name = t.name
);

-- 3.2 MCP 서버 연결
INSERT INTO catalog_ai_agent_mcp_servers (agent_id, name, url, auth_method, description)
SELECT a.id, m.name, m.url, m.auth_method, m.description
FROM (VALUES
    ('catalog.metadata-assistant', 'argus-catalog-mcp',
     'https://agents.data-dynamics.io/mcp/catalog', 'OAuth2.1',
     'Argus Catalog 조회/생성 API 를 노출하는 MCP 서버'),
    ('cs.rental-support', 'sakila-oltp-mcp',
     'https://agents.data-dynamics.io/mcp/sakila', 'ApiKey',
     'Sakila 운영 DB 의 읽기 전용 조회 도구 모음'),
    ('ops.quality-watchdog', 'argus-quality-mcp',
     'https://agents.data-dynamics.io/mcp/quality', 'ApiKey',
     '품질 점수/결과 조회 및 통지 도구 모음')
) AS m(agent_name, name, url, auth_method, description)
JOIN catalog_ai_agents a ON a.name = m.agent_name
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_ai_agent_mcp_servers x WHERE x.agent_id = a.id AND x.name = m.name
);

-- 3.3 에이전트 버전 이력
INSERT INTO catalog_ai_agent_versions (agent_id, version, source, changelog, status, created_by)
SELECT a.id, v.version, v.source, v.changelog, v.status, 'admin'
FROM (VALUES
    ('catalog.metadata-assistant', '1.0.0', 'git@github.com:DataDynamics/catalog-assistant.git#v1.0.0',
     '최초 릴리스 — 설명 생성/검색', 'archived'),
    ('catalog.metadata-assistant', '1.2.0', 'git@github.com:DataDynamics/catalog-assistant.git#v1.2.0',
     'PII 감지 도구 추가, 한국어 프롬프트 개선', 'active'),
    ('cs.rental-support', '0.9.0', 'git@github.com:DataDynamics/rental-support.git#v0.9.0',
     '베타 — 환불 HITL 승인 플로 적용', 'active'),
    ('ops.quality-watchdog', '1.0.1', 'git@github.com:DataDynamics/quality-watchdog.git#v1.0.1',
     '주간 리포트 요약 포맷 수정', 'active')
) AS v(agent_name, version, source, changelog, status)
JOIN catalog_ai_agents a ON a.name = v.agent_name
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_ai_agent_versions x WHERE x.agent_id = a.id AND x.version = v.version
);

-- 소유권 보정 — 생성자 admin (행 단위 소유권 기본값)
UPDATE catalog_oci_models SET created_by = 'admin' WHERE created_by IS NULL;
UPDATE catalog_registered_models SET created_by = 'admin' WHERE created_by IS NULL;

COMMIT;
