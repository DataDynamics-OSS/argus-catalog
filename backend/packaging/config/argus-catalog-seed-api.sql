-- ============================================================================
-- Argus Catalog — API 카탈로그 데모 시드 데이터 (PostgreSQL)
--
-- Sakila(DVD 대여) 데모 세계관의 서비스 API 4종을 등록한다.
--
-- 구성:
--   1. API 4종 (catalog_apis) — REST 3 + Webhook 1, 상태/인증/티어 다양화
--   2. 엔드포인트 16건 — 메서드·경로·요약·파라미터/응답 스키마
--   3. 서버(환경별 base URL) 8건
--   4. 리니지 — 데이터셋/모델/에이전트와의 provides/consumes/depends_on 관계
--
-- 실행:
--   psql -U <user> -d <db> -f argus-catalog-seed-api.sql
--
-- 특징:
--   - 재실행 안전(idempotent): ON CONFLICT DO NOTHING / WHERE NOT EXISTS
--   - 다른 시드(-demo/-sakila/-ai)와 독립 실행 가능
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. API 등록 (source=manual — 상세에서 엔드포인트 직접 관리)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_apis
    (name, urn, display_name, description, version, status, owner_email, department,
     category, protocol, source, base_url, base_url_overridden,
     certification, tier, tags, note, created_by)
VALUES
    ('rental-service', 'rental-service.api', '대여 서비스 API',
     'DVD 대여~반납 라이프사이클을 처리하는 핵심 서비스 API. 대여 생성/반납/연체 조회를 제공하며 sakila.rental 을 원장으로 사용한다.',
     '2.1.0', 'published', 'khshin@data-dynamics.io', '물류운영팀',
     'core', 'REST', 'manual', 'https://api.data-dynamics.io/rental/v2', 'true',
     'CERTIFIED', 'GOLD', '["sakila", "demo", "core"]'::json,
     '결제 연동은 payment-service 를 통해서만 수행할 것.', 'admin'),
    ('payment-service', 'payment-service.api', '결제 서비스 API',
     '대여 요금 결제·환불을 처리하는 API. 환불은 멱등 키(idempotency-key) 헤더가 필수다.',
     '1.4.0', 'published', 'tkoh@data-dynamics.io', 'SCM팀',
     'core', 'REST', 'manual', 'https://api.data-dynamics.io/payment/v1', 'true',
     'CERTIFIED', 'GOLD', '["sakila", "demo", "core"]'::json,
     NULL, 'admin'),
    ('recommendation-api', 'recommendation-api.api', '영화 추천 API',
     'film_recommender 모델(ONNX) 서빙 API — 회원별 추천 영화 목록을 반환한다. 콜드스타트 회원은 인기 차트로 폴백.',
     '0.3.0', 'draft', 'mjkim@data-dynamics.io', '공정데이터팀',
     'ml-serving', 'REST', 'manual', 'https://api.data-dynamics.io/reco/v0', 'true',
     'IN_REVIEW', 'SILVER', '["sakila", "demo", "ml"]'::json,
     '베타 — p95 응답 80ms 목표. GA 전 부하 테스트 필요.', 'admin'),
    ('rental-events', 'rental-events.api', '대여 이벤트 Webhook',
     '대여/반납/연체 발생 시 구독자에게 푸시되는 Webhook. 품질 감시 에이전트와 알림 시스템이 구독한다.',
     '1.0.0', 'published', 'iuhong@data-dynamics.io', '물류데이터팀',
     'integration', 'Webhook', 'manual', 'https://hooks.data-dynamics.io/rental', 'true',
     'NONE', 'BRONZE', '["sakila", "demo", "events"]'::json,
     '재전송 정책: 5xx 응답 시 지수 백오프로 최대 5회.', 'admin')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. 엔드포인트 (api_id + method + path 기준 멱등)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_api_endpoints
    (api_id, method, path, operation_id, summary, description, tags, parameters, request_body, responses, sort_order)
SELECT a.id, e.method, e.path, e.operation_id, e.summary, e.description,
       e.tags::json, e.parameters::json, e.request_body::json, e.responses::json, e.sort_order
FROM (VALUES
    -- rental-service (5)
    ('rental-service', 'POST', '/rentals', 'createRental',
     '대여 생성', '재고(inventory)와 회원(customer)을 받아 대여를 생성한다. 재고가 대여 중이면 409.',
     '["rental"]',
     'null',
     '{"type": "object", "properties": {"inventory_id": {"type": "integer"}, "customer_id": {"type": "integer"}, "staff_id": {"type": "integer"}}, "required": ["inventory_id", "customer_id", "staff_id"]}',
     '{"201": "대여 생성됨 (rental_id 반환)", "409": "이미 대여 중인 재고"}', 0),
    ('rental-service', 'POST', '/rentals/{rental_id}/return', 'returnRental',
     '반납 처리', '대여 건을 반납 처리하고 연체료가 있으면 미수금으로 계상한다.',
     '["rental"]',
     '[{"name": "rental_id", "in": "path", "type": "integer", "required": true}]',
     'null',
     '{"200": "반납 완료 (연체료 포함 정산 내역)", "404": "대여 건 없음"}', 1),
    ('rental-service', 'GET', '/rentals/{rental_id}', 'getRental',
     '대여 상세 조회', NULL,
     '["rental"]',
     '[{"name": "rental_id", "in": "path", "type": "integer", "required": true}]',
     'null', '{"200": "대여 상세", "404": "없음"}', 2),
    ('rental-service', 'GET', '/customers/{customer_id}/rentals', 'listCustomerRentals',
     '회원 대여 이력', '회원의 대여 이력을 최신순 페이지네이션으로 반환한다.',
     '["rental", "customer"]',
     '[{"name": "customer_id", "in": "path", "type": "integer", "required": true}, {"name": "page", "in": "query", "type": "integer"}]',
     'null', '{"200": "대여 이력 목록"}', 3),
    ('rental-service', 'GET', '/rentals/overdue', 'listOverdueRentals',
     '연체 목록', '미반납 + 기본 대여 기간 초과 건을 점포별로 조회한다.',
     '["rental", "ops"]',
     '[{"name": "store_id", "in": "query", "type": "integer"}]',
     'null', '{"200": "연체 목록"}', 4),
    -- payment-service (4)
    ('payment-service', 'POST', '/payments', 'createPayment',
     '결제 생성', '대여 건에 대한 결제를 생성한다.',
     '["payment"]',
     'null',
     '{"type": "object", "properties": {"rental_id": {"type": "integer"}, "amount": {"type": "number"}, "customer_id": {"type": "integer"}}, "required": ["rental_id", "amount", "customer_id"]}',
     '{"201": "결제 완료 (payment_id)", "402": "결제 실패"}', 0),
    ('payment-service', 'POST', '/payments/{payment_id}/refund', 'refundPayment',
     '환불 처리', '결제 건을 환불한다. Idempotency-Key 헤더 필수 — 같은 키 재요청은 동일 결과를 반환.',
     '["payment", "refund"]',
     '[{"name": "payment_id", "in": "path", "type": "integer", "required": true}, {"name": "Idempotency-Key", "in": "header", "type": "string", "required": true}]',
     '{"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}',
     '{"200": "환불 완료", "409": "이미 환불됨"}', 1),
    ('payment-service', 'GET', '/payments/{payment_id}', 'getPayment',
     '결제 상세 조회', NULL,
     '["payment"]',
     '[{"name": "payment_id", "in": "path", "type": "integer", "required": true}]',
     'null', '{"200": "결제 상세", "404": "없음"}', 2),
    ('payment-service', 'GET', '/customers/{customer_id}/payments', 'listCustomerPayments',
     '회원 결제 이력', NULL,
     '["payment", "customer"]',
     '[{"name": "customer_id", "in": "path", "type": "integer", "required": true}]',
     'null', '{"200": "결제 이력 목록"}', 3),
    -- recommendation-api (3)
    ('recommendation-api', 'GET', '/recommendations/{customer_id}', 'getRecommendations',
     '회원별 추천 목록', 'film_recommender ONNX 모델 추론 결과 상위 N편을 반환한다. 콜드스타트는 인기 차트 폴백.',
     '["recommendation"]',
     '[{"name": "customer_id", "in": "path", "type": "integer", "required": true}, {"name": "limit", "in": "query", "type": "integer", "default": 10}]',
     'null', '{"200": "추천 영화 목록 (film_id + score)"}', 0),
    ('recommendation-api', 'GET', '/charts/popular', 'getPopularChart',
     '인기 차트', '최근 30일 대여량 기준 인기 영화 차트 (콜드스타트 폴백 소스).',
     '["recommendation"]',
     '[{"name": "genre", "in": "query", "type": "string"}]',
     'null', '{"200": "인기 영화 목록"}', 1),
    ('recommendation-api', 'POST', '/feedback', 'postFeedback',
     '추천 피드백 수집', '노출/클릭/대여 전환 피드백을 수집해 재학습에 사용한다.',
     '["recommendation", "ml"]',
     'null',
     '{"type": "object", "properties": {"customer_id": {"type": "integer"}, "film_id": {"type": "integer"}, "event": {"type": "string", "enum": ["impression", "click", "rental"]}}, "required": ["customer_id", "film_id", "event"]}',
     '{"202": "수집됨"}', 2),
    -- rental-events Webhook (3) — method=이벤트 유형, path=채널/이벤트명
    ('rental-events', 'EVENT', 'rental.created', 'onRentalCreated',
     '대여 생성 이벤트', '대여 생성 시 구독자에게 푸시된다.',
     '["events"]', 'null',
     '{"type": "object", "properties": {"rental_id": {"type": "integer"}, "customer_id": {"type": "integer"}, "inventory_id": {"type": "integer"}, "rented_at": {"type": "string", "format": "date-time"}}}',
     '{"2xx": "수신 확인 — 그 외 응답은 지수 백오프 재전송"}', 0),
    ('rental-events', 'EVENT', 'rental.returned', 'onRentalReturned',
     '반납 이벤트', '반납 처리 시 연체료 정보와 함께 푸시된다.',
     '["events"]', 'null',
     '{"type": "object", "properties": {"rental_id": {"type": "integer"}, "returned_at": {"type": "string", "format": "date-time"}, "late_fee": {"type": "number"}}}',
     '{"2xx": "수신 확인"}', 1),
    ('rental-events', 'EVENT', 'rental.overdue', 'onRentalOverdue',
     '연체 발생 이벤트', '기본 대여 기간 초과 시 일 1회 푸시된다. 품질 감시·알림 시스템이 구독.',
     '["events", "ops"]', 'null',
     '{"type": "object", "properties": {"rental_id": {"type": "integer"}, "customer_id": {"type": "integer"}, "days_overdue": {"type": "integer"}}}',
     '{"2xx": "수신 확인"}', 2)
) AS e(api_name, method, path, operation_id, summary, description, tags, parameters, request_body, responses, sort_order)
JOIN catalog_apis a ON a.name = e.api_name
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_api_endpoints x
    WHERE x.api_id = a.id AND x.method = e.method AND x.path = e.path
);

-- ---------------------------------------------------------------------------
-- 3. 서버 (환경별 base URL)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_api_servers (api_id, url, description)
SELECT a.id, s.url, s.description
FROM (VALUES
    ('rental-service',     'https://api.data-dynamics.io/rental/v2',       '운영 (PROD)'),
    ('rental-service',     'https://api-dev.data-dynamics.io/rental/v2',   '개발 (DEV)'),
    ('payment-service',    'https://api.data-dynamics.io/payment/v1',      '운영 (PROD)'),
    ('payment-service',    'https://api-dev.data-dynamics.io/payment/v1',  '개발 (DEV)'),
    ('recommendation-api', 'https://api-dev.data-dynamics.io/reco/v0',     '개발 (DEV) — 베타'),
    ('rental-events',      'https://hooks.data-dynamics.io/rental',        '운영 (PROD)'),
    ('rental-events',      'https://hooks-dev.data-dynamics.io/rental',    '개발 (DEV)')
) AS s(api_name, url, description)
JOIN catalog_apis a ON a.name = s.api_name
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_api_servers x WHERE x.api_id = a.id AND x.url = s.url
);

-- ---------------------------------------------------------------------------
-- 4. 리니지 — 데이터셋/모델/에이전트와의 관계 (Backstage 스타일)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_api_lineage (api_id, relation, target_type, target_ref, target_label, note, created_by)
SELECT a.id, l.relation, l.target_type, l.target_ref, l.target_label, l.note, 'admin'
FROM (VALUES
    ('rental-service',     'provides',   'dataset', 'sakila-mysql.sakila.rental.dataset',  'sakila.rental',
     '대여 트랜잭션의 기록 시스템(SoR)'),
    ('rental-service',     'depends_on', 'api',     'payment-service',                      '결제 서비스 API',
     '반납 시 연체료 정산 호출'),
    ('payment-service',    'provides',   'dataset', 'sakila-mysql.sakila.payment.dataset', 'sakila.payment',
     '결제 원장 적재'),
    ('recommendation-api', 'consumes',   'dataset', 'sakila-mysql.sakila.rental.dataset',  'sakila.rental',
     '추천 모델 학습/피드백 데이터'),
    ('recommendation-api', 'depends_on', 'model',   'ml-film-recommender-onnx',             'Film Recommender (ONNX)',
     '서빙 모델 아티팩트'),
    ('rental-events',      'consumes',   'api',     'rental-service',                       '대여 서비스 API',
     '대여 라이프사이클 이벤트 원천'),
    ('rental-events',      'provides',   'agent',   'ops.quality-watchdog',                 '품질 감시 에이전트',
     '연체 이벤트 구독자')
) AS l(api_name, relation, target_type, target_ref, target_label, note)
JOIN catalog_apis a ON a.name = l.api_name
ON CONFLICT (api_id, relation, target_type, target_ref) DO NOTHING;

COMMIT;
