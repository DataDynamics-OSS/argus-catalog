-- ============================================================================
-- Argus Catalog — Sakila 데모 시드 데이터 (PostgreSQL)
--
-- MySQL 표준 샘플 DB 'Sakila'(DVD 대여점)를 카탈로그 데이터셋으로 등록한다.
--
-- 구성:
--   1. 데이터 소스: sakila-mysql (MySQL)
--   2. 데이터셋 16개 — 전체 테이블 + 스키마 89컬럼 (PII 분류 포함)
--   3. 태그(sakila/demo/미디어) + 핵심 테이블 소유자
--   4. 용어집 — '영화 대여' 카테고리 + 용어 10건 + 데이터셋 연결
--   5. 데이터 표준 — 영화대여 표준 사전 (단어·도메인·관람등급코드·표준용어)
--
-- 실행:
--   psql -U <user> -d <db> -f argus-catalog-seed-sakila.sql
--
-- 특징:
--   - 재실행 안전(idempotent): ON CONFLICT DO NOTHING / WHERE NOT EXISTS
--   - argus-catalog-seed-demo.sql 과 독립적으로 실행 가능
--   - 시드 후 권장: 설정 > 임베딩 > 백필 실행 (시맨틱/통합 검색 노출)
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. 데이터 소스
-- ---------------------------------------------------------------------------

INSERT INTO catalog_datasources (datasource_id, name, type, origin)
VALUES ('sakila-mysql', 'Sakila (MySQL)', 'mysql', 'DEV')
ON CONFLICT (datasource_id) DO NOTHING;

-- 접속 설정 — deploy/docker-compose.infra.yml 의 mariadb-sakila 컨테이너 기준
-- (localhost:3306, sakila/sakila, db=sakila — 메타데이터 동기화·품질 수집이 사용)
INSERT INTO catalog_datasource_configurations (datasource_id, config_json)
SELECT ds.id, '{"host": "localhost", "port": 3306, "database": "sakila", "username": "sakila", "password": "sakila"}'
FROM catalog_datasources ds
WHERE ds.datasource_id = 'sakila-mysql'
  AND NOT EXISTS (
      SELECT 1 FROM catalog_datasource_configurations c WHERE c.datasource_id = ds.id
  );

-- ---------------------------------------------------------------------------
-- 2. 데이터셋 (Sakila 16개 테이블)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_datasets
    (urn, name, display_name, datasource_id, summary, description, qualified_name,
     table_type, origin, status, is_synced, data_category, row_count,
     sensitivity, contains_pii, pii_fields, tier, steward, created_by)
SELECT t.urn, t.name, t.display_name, ds.id, t.summary, t.description, t.name,
       'TABLE', 'DEV', 'active', 'false', 'STRUCTURED', t.row_count,
       t.sensitivity, t.contains_pii, t.pii_fields, t.tier, 'admin', 'admin'
FROM (VALUES
    ('sakila-mysql.sakila.film.dataset', 'sakila.film', '영화',
     'DVD 대여점에서 보유한 영화 카탈로그',
     '영화의 제목·설명·개봉연도·대여 조건(기간/요금)·관람등급·교체비용 등 상품 마스터. 재고(inventory)와 대여(rental)의 기준 데이터.',
     1000, 'INTERNAL', 'false', NULL, 'GOLD'),
    ('sakila-mysql.sakila.actor.dataset', 'sakila.actor', '배우',
     '영화 출연 배우 마스터',
     '배우의 이름 정보. film_actor 매핑을 통해 영화와 다대다로 연결된다.',
     200, 'INTERNAL', 'false', NULL, 'SILVER'),
    ('sakila-mysql.sakila.customer.dataset', 'sakila.customer', '고객',
     'DVD 대여점 회원(고객) 마스터',
     '고객 이름·이메일·주소·활성 여부. 대여(rental)·결제(payment)의 주체. 개인정보 포함 — 접근 통제 대상.',
     599, 'CONFIDENTIAL', 'true', 'first_name,last_name,email,address_id', 'GOLD'),
    ('sakila-mysql.sakila.rental.dataset', 'sakila.rental', '대여',
     'DVD 대여 트랜잭션 (대여~반납)',
     '재고(inventory) 단위의 대여·반납 이력. 대여일시·반납일시·담당 직원을 기록하며 결제(payment)의 근거가 된다.',
     16044, 'INTERNAL', 'false', NULL, 'GOLD'),
    ('sakila-mysql.sakila.payment.dataset', 'sakila.payment', '결제',
     '대여 요금 결제 트랜잭션',
     '고객의 대여 건별 결제 금액·결제일시. 매출 분석의 기준 팩트 테이블.',
     16049, 'INTERNAL', 'false', NULL, 'GOLD'),
    ('sakila-mysql.sakila.inventory.dataset', 'sakila.inventory', '재고',
     '점포별 보유 영화 재고(실물 DVD)',
     '영화(film) × 점포(store) 단위의 실물 DVD 재고. 대여 가능 여부 판단의 기준.',
     4581, 'INTERNAL', 'false', NULL, 'SILVER'),
    ('sakila-mysql.sakila.store.dataset', 'sakila.store', '점포',
     'DVD 대여 점포 마스터',
     '점포의 관리 직원·주소 정보. 재고와 고객이 점포에 귀속된다.',
     2, 'INTERNAL', 'false', NULL, 'SILVER'),
    ('sakila-mysql.sakila.staff.dataset', 'sakila.staff', '직원',
     '점포 직원 마스터',
     '직원의 이름·이메일·로그인 계정 정보. 대여/결제 처리의 담당자. 개인정보 포함.',
     2, 'CONFIDENTIAL', 'true', 'first_name,last_name,email,username,picture', 'SILVER'),
    ('sakila-mysql.sakila.address.dataset', 'sakila.address', '주소',
     '고객·직원·점포 공용 주소',
     '시(city)·우편번호·전화번호를 포함한 주소 마스터. 고객/직원/점포가 참조. 개인정보 포함.',
     603, 'CONFIDENTIAL', 'true', 'address,phone,postal_code', 'BRONZE'),
    ('sakila-mysql.sakila.city.dataset', 'sakila.city', '도시',
     '도시 마스터 (주소 상위)',
     '국가(country)에 속한 도시 정보.',
     600, 'PUBLIC', 'false', NULL, 'BRONZE'),
    ('sakila-mysql.sakila.country.dataset', 'sakila.country', '국가',
     '국가 마스터',
     '주소 체계의 최상위 분류.',
     109, 'PUBLIC', 'false', NULL, 'BRONZE'),
    ('sakila-mysql.sakila.category.dataset', 'sakila.category', '카테고리',
     '영화 장르 분류',
     'Action/Comedy/Drama 등 16개 장르. film_category 매핑으로 영화와 연결.',
     16, 'PUBLIC', 'false', NULL, 'BRONZE'),
    ('sakila-mysql.sakila.language.dataset', 'sakila.language', '언어',
     '영화 언어 마스터',
     '영화의 음성/원어 언어 분류.',
     6, 'PUBLIC', 'false', NULL, 'BRONZE'),
    ('sakila-mysql.sakila.film_actor.dataset', 'sakila.film_actor', '영화-배우 매핑',
     '영화 × 배우 다대다 매핑',
     '영화별 출연 배우 매핑 테이블.',
     5462, 'INTERNAL', 'false', NULL, 'BRONZE'),
    ('sakila-mysql.sakila.film_category.dataset', 'sakila.film_category', '영화-카테고리 매핑',
     '영화 × 장르 다대다 매핑',
     '영화별 장르 매핑 테이블.',
     1000, 'INTERNAL', 'false', NULL, 'BRONZE'),
    ('sakila-mysql.sakila.film_text.dataset', 'sakila.film_text', '영화 검색 텍스트',
     '영화 제목·설명 전문 검색용 테이블',
     'MyISAM FULLTEXT 인덱스 기반 영화 검색용 비정규 테이블.',
     1000, 'INTERNAL', 'false', NULL, 'BRONZE')
) AS t(urn, name, display_name, summary, description, row_count, sensitivity, contains_pii, pii_fields, tier)
JOIN catalog_datasources ds ON ds.datasource_id = 'sakila-mysql'
ON CONFLICT (urn) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2.1 스키마 — 컬럼 정의 (PK/PII 표시 포함)
--     (dataset_id, field_path) 중복 방지: WHERE NOT EXISTS
-- ---------------------------------------------------------------------------

-- film (13)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('film_id',              'NUMBER', 'SMALLINT UNSIGNED', '영화 식별자',                        'false', 'true',  0, NULL),
    ('title',                'STRING', 'VARCHAR(128)',      '영화 제목',                          'false', 'false', 1, NULL),
    ('description',          'STRING', 'TEXT',              '줄거리 요약',                        'true',  'false', 2, NULL),
    ('release_year',         'NUMBER', 'YEAR',              '개봉 연도',                          'true',  'false', 3, NULL),
    ('language_id',          'NUMBER', 'TINYINT UNSIGNED',  '음성 언어 (language FK)',            'false', 'false', 4, NULL),
    ('original_language_id', 'NUMBER', 'TINYINT UNSIGNED',  '원어 언어 (language FK)',            'true',  'false', 5, NULL),
    ('rental_duration',      'NUMBER', 'TINYINT UNSIGNED',  '기본 대여 기간(일)',                 'false', 'false', 6, NULL),
    ('rental_rate',          'NUMBER', 'DECIMAL(4,2)',      '대여 요금',                          'false', 'false', 7, NULL),
    ('length',               'NUMBER', 'SMALLINT UNSIGNED', '상영 시간(분)',                      'true',  'false', 8, NULL),
    ('replacement_cost',     'NUMBER', 'DECIMAL(5,2)',      '분실/파손 시 교체 비용',             'false', 'false', 9, NULL),
    ('rating',               'ENUM',   'ENUM',              '관람등급 (G/PG/PG-13/R/NC-17)',      'true',  'false', 10, NULL),
    ('special_features',     'ARRAY',  'SET',               '부가 기능 (예고편/코멘터리 등)',     'true',  'false', 11, NULL),
    ('last_update',          'DATE',   'TIMESTAMP',         '최종 수정 일시',                     'false', 'false', 12, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.film.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- actor (4)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('actor_id',    'NUMBER', 'SMALLINT UNSIGNED', '배우 식별자',     'false', 'true',  0, NULL),
    ('first_name',  'STRING', 'VARCHAR(45)',       '이름',            'false', 'false', 1, 'NAME'),
    ('last_name',   'STRING', 'VARCHAR(45)',       '성',              'false', 'false', 2, 'NAME'),
    ('last_update', 'DATE',   'TIMESTAMP',         '최종 수정 일시',  'false', 'false', 3, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.actor.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- customer (9)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('customer_id', 'NUMBER',  'SMALLINT UNSIGNED', '고객 식별자',               'false', 'true',  0, NULL),
    ('store_id',    'NUMBER',  'TINYINT UNSIGNED',  '소속 점포 (store FK)',      'false', 'false', 1, NULL),
    ('first_name',  'STRING',  'VARCHAR(45)',       '이름',                      'false', 'false', 2, 'NAME'),
    ('last_name',   'STRING',  'VARCHAR(45)',       '성',                        'false', 'false', 3, 'NAME'),
    ('email',       'STRING',  'VARCHAR(50)',       '이메일 주소',               'true',  'false', 4, 'EMAIL'),
    ('address_id',  'NUMBER',  'SMALLINT UNSIGNED', '주소 (address FK)',         'false', 'false', 5, 'ADDRESS'),
    ('active',      'BOOLEAN', 'BOOLEAN',           '활성 회원 여부',            'false', 'false', 6, NULL),
    ('create_date', 'DATE',    'DATETIME',          '가입 일시',                 'false', 'false', 7, NULL),
    ('last_update', 'DATE',    'TIMESTAMP',         '최종 수정 일시',            'false', 'false', 8, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.customer.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- rental (7)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('rental_id',    'NUMBER', 'INT',               '대여 식별자',                    'false', 'true',  0, NULL),
    ('rental_date',  'DATE',   'DATETIME',          '대여 일시',                      'false', 'false', 1, NULL),
    ('inventory_id', 'NUMBER', 'MEDIUMINT UNSIGNED','대여 재고 (inventory FK)',       'false', 'false', 2, NULL),
    ('customer_id',  'NUMBER', 'SMALLINT UNSIGNED', '대여 고객 (customer FK)',        'false', 'false', 3, NULL),
    ('return_date',  'DATE',   'DATETIME',          '반납 일시 (NULL=미반납)',        'true',  'false', 4, NULL),
    ('staff_id',     'NUMBER', 'TINYINT UNSIGNED',  '처리 직원 (staff FK)',           'false', 'false', 5, NULL),
    ('last_update',  'DATE',   'TIMESTAMP',         '최종 수정 일시',                 'false', 'false', 6, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.rental.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- payment (7)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('payment_id',   'NUMBER', 'SMALLINT UNSIGNED', '결제 식별자',               'false', 'true',  0, NULL),
    ('customer_id',  'NUMBER', 'SMALLINT UNSIGNED', '결제 고객 (customer FK)',   'false', 'false', 1, NULL),
    ('staff_id',     'NUMBER', 'TINYINT UNSIGNED',  '처리 직원 (staff FK)',      'false', 'false', 2, NULL),
    ('rental_id',    'NUMBER', 'INT',               '대상 대여 건 (rental FK)',  'true',  'false', 3, NULL),
    ('amount',       'NUMBER', 'DECIMAL(5,2)',      '결제 금액',                 'false', 'false', 4, NULL),
    ('payment_date', 'DATE',   'DATETIME',          '결제 일시',                 'false', 'false', 5, NULL),
    ('last_update',  'DATE',   'TIMESTAMP',         '최종 수정 일시',            'false', 'false', 6, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.payment.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- inventory (4)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('inventory_id', 'NUMBER', 'MEDIUMINT UNSIGNED', '재고 식별자 (실물 DVD)',  'false', 'true',  0, NULL),
    ('film_id',      'NUMBER', 'SMALLINT UNSIGNED',  '영화 (film FK)',          'false', 'false', 1, NULL),
    ('store_id',     'NUMBER', 'TINYINT UNSIGNED',   '보유 점포 (store FK)',    'false', 'false', 2, NULL),
    ('last_update',  'DATE',   'TIMESTAMP',          '최종 수정 일시',          'false', 'false', 3, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.inventory.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- store (4)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('store_id',         'NUMBER', 'TINYINT UNSIGNED',  '점포 식별자',            'false', 'true',  0, NULL),
    ('manager_staff_id', 'NUMBER', 'TINYINT UNSIGNED',  '관리 직원 (staff FK)',   'false', 'false', 1, NULL),
    ('address_id',       'NUMBER', 'SMALLINT UNSIGNED', '점포 주소 (address FK)', 'false', 'false', 2, NULL),
    ('last_update',      'DATE',   'TIMESTAMP',         '최종 수정 일시',         'false', 'false', 3, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.store.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- staff (11)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('staff_id',    'NUMBER',  'TINYINT UNSIGNED',  '직원 식별자',             'false', 'true',  0, NULL),
    ('first_name',  'STRING',  'VARCHAR(45)',       '이름',                    'false', 'false', 1, 'NAME'),
    ('last_name',   'STRING',  'VARCHAR(45)',       '성',                      'false', 'false', 2, 'NAME'),
    ('address_id',  'NUMBER',  'SMALLINT UNSIGNED', '주소 (address FK)',       'false', 'false', 3, 'ADDRESS'),
    ('picture',     'BYTES',   'BLOB',              '증명 사진',               'true',  'false', 4, 'PHOTO'),
    ('email',       'STRING',  'VARCHAR(50)',       '이메일 주소',             'true',  'false', 5, 'EMAIL'),
    ('store_id',    'NUMBER',  'TINYINT UNSIGNED',  '소속 점포 (store FK)',    'false', 'false', 6, NULL),
    ('active',      'BOOLEAN', 'BOOLEAN',           '재직 여부',               'false', 'false', 7, NULL),
    ('username',    'STRING',  'VARCHAR(16)',       '로그인 계정',             'false', 'false', 8, 'CREDENTIAL'),
    ('password',    'STRING',  'VARCHAR(40)',       '비밀번호 해시(SHA-1)',    'true',  'false', 9, 'CREDENTIAL'),
    ('last_update', 'DATE',    'TIMESTAMP',         '최종 수정 일시',          'false', 'false', 10, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.staff.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- address (9)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('address_id',  'NUMBER', 'SMALLINT UNSIGNED', '주소 식별자',          'false', 'true',  0, NULL),
    ('address',     'STRING', 'VARCHAR(50)',       '주소 1행',             'false', 'false', 1, 'ADDRESS'),
    ('address2',    'STRING', 'VARCHAR(50)',       '주소 2행',             'true',  'false', 2, 'ADDRESS'),
    ('district',    'STRING', 'VARCHAR(20)',       '구/지역',              'false', 'false', 3, 'ADDRESS'),
    ('city_id',     'NUMBER', 'SMALLINT UNSIGNED', '도시 (city FK)',       'false', 'false', 4, NULL),
    ('postal_code', 'STRING', 'VARCHAR(10)',       '우편번호',             'true',  'false', 5, 'ADDRESS'),
    ('phone',       'STRING', 'VARCHAR(20)',       '전화번호',             'false', 'false', 6, 'PHONE'),
    ('location',    'BYTES',  'GEOMETRY',          '좌표(공간 데이터)',    'false', 'false', 7, NULL),
    ('last_update', 'DATE',   'TIMESTAMP',         '최종 수정 일시',       'false', 'false', 8, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.address.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- city (4)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('city_id',     'NUMBER', 'SMALLINT UNSIGNED', '도시 식별자',        'false', 'true',  0, NULL),
    ('city',        'STRING', 'VARCHAR(50)',       '도시명',             'false', 'false', 1, NULL),
    ('country_id',  'NUMBER', 'SMALLINT UNSIGNED', '국가 (country FK)',  'false', 'false', 2, NULL),
    ('last_update', 'DATE',   'TIMESTAMP',         '최종 수정 일시',     'false', 'false', 3, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.city.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- country (3)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('country_id',  'NUMBER', 'SMALLINT UNSIGNED', '국가 식별자',     'false', 'true',  0, NULL),
    ('country',     'STRING', 'VARCHAR(50)',       '국가명',          'false', 'false', 1, NULL),
    ('last_update', 'DATE',   'TIMESTAMP',         '최종 수정 일시',  'false', 'false', 2, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.country.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- category (3)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('category_id', 'NUMBER', 'TINYINT UNSIGNED', '카테고리 식별자',  'false', 'true',  0, NULL),
    ('name',        'STRING', 'VARCHAR(25)',      '장르명',           'false', 'false', 1, NULL),
    ('last_update', 'DATE',   'TIMESTAMP',        '최종 수정 일시',   'false', 'false', 2, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.category.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- language (3)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('language_id', 'NUMBER', 'TINYINT UNSIGNED', '언어 식별자',     'false', 'true',  0, NULL),
    ('name',        'STRING', 'CHAR(20)',         '언어명',          'false', 'false', 1, NULL),
    ('last_update', 'DATE',   'TIMESTAMP',        '최종 수정 일시',  'false', 'false', 2, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.language.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- film_actor (3)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('actor_id',    'NUMBER', 'SMALLINT UNSIGNED', '배우 (actor FK, 복합 PK)', 'false', 'true',  0, NULL),
    ('film_id',     'NUMBER', 'SMALLINT UNSIGNED', '영화 (film FK, 복합 PK)',  'false', 'true',  1, NULL),
    ('last_update', 'DATE',   'TIMESTAMP',         '최종 수정 일시',           'false', 'false', 2, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.film_actor.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- film_category (3)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('film_id',     'NUMBER', 'SMALLINT UNSIGNED', '영화 (film FK, 복합 PK)',        'false', 'true',  0, NULL),
    ('category_id', 'NUMBER', 'TINYINT UNSIGNED',  '카테고리 (category FK, 복합 PK)', 'false', 'true',  1, NULL),
    ('last_update', 'DATE',   'TIMESTAMP',         '최종 수정 일시',                  'false', 'false', 2, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.film_category.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);

-- film_text (3)
INSERT INTO catalog_dataset_schemas
    (dataset_id, field_path, field_type, native_type, description, nullable, is_primary_key, is_indexed, ordinal, pii_type)
SELECT d.id, f.field_path, f.field_type, f.native_type, f.description, f.nullable, f.is_pk, f.is_pk, f.ordinal, f.pii_type
FROM (VALUES
    ('film_id',     'NUMBER', 'SMALLINT',     '영화 식별자',                'false', 'true',  0, NULL),
    ('title',       'STRING', 'VARCHAR(255)', '영화 제목 (FULLTEXT 인덱스)', 'false', 'false', 1, NULL),
    ('description', 'STRING', 'TEXT',         '줄거리 (FULLTEXT 인덱스)',    'true',  'false', 2, NULL)
) AS f(field_path, field_type, native_type, description, nullable, is_pk, ordinal, pii_type)
JOIN catalog_datasets d ON d.urn = 'sakila-mysql.sakila.film_text.dataset'
WHERE NOT EXISTS (SELECT 1 FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id AND s.field_path = f.field_path);


-- ---------------------------------------------------------------------------
-- 2.2 DDL — Sakila 원본 MySQL CREATE TABLE 문
--     재실행 시 동일 값으로 갱신되므로 idempotent.
-- ---------------------------------------------------------------------------

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE film (
  film_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  title VARCHAR(128) NOT NULL,
  description TEXT DEFAULT NULL,
  release_year YEAR DEFAULT NULL,
  language_id TINYINT UNSIGNED NOT NULL,
  original_language_id TINYINT UNSIGNED DEFAULT NULL,
  rental_duration TINYINT UNSIGNED NOT NULL DEFAULT 3,
  rental_rate DECIMAL(4,2) NOT NULL DEFAULT 4.99,
  length SMALLINT UNSIGNED DEFAULT NULL,
  replacement_cost DECIMAL(5,2) NOT NULL DEFAULT 19.99,
  rating ENUM('G','PG','PG-13','R','NC-17') DEFAULT 'G',
  special_features SET('Trailers','Commentaries','Deleted Scenes','Behind the Scenes') DEFAULT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (film_id),
  KEY idx_title (title),
  KEY idx_fk_language_id (language_id),
  KEY idx_fk_original_language_id (original_language_id),
  CONSTRAINT fk_film_language FOREIGN KEY (language_id) REFERENCES language (language_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_film_language_original FOREIGN KEY (original_language_id) REFERENCES language (language_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.film.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE actor (
  actor_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  first_name VARCHAR(45) NOT NULL,
  last_name VARCHAR(45) NOT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (actor_id),
  KEY idx_actor_last_name (last_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.actor.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE customer (
  customer_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  store_id TINYINT UNSIGNED NOT NULL,
  first_name VARCHAR(45) NOT NULL,
  last_name VARCHAR(45) NOT NULL,
  email VARCHAR(50) DEFAULT NULL,
  address_id SMALLINT UNSIGNED NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  create_date DATETIME NOT NULL,
  last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (customer_id),
  KEY idx_fk_store_id (store_id),
  KEY idx_fk_address_id (address_id),
  KEY idx_last_name (last_name),
  CONSTRAINT fk_customer_address FOREIGN KEY (address_id) REFERENCES address (address_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_customer_store FOREIGN KEY (store_id) REFERENCES store (store_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.customer.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE rental (
  rental_id INT NOT NULL AUTO_INCREMENT,
  rental_date DATETIME NOT NULL,
  inventory_id MEDIUMINT UNSIGNED NOT NULL,
  customer_id SMALLINT UNSIGNED NOT NULL,
  return_date DATETIME DEFAULT NULL,
  staff_id TINYINT UNSIGNED NOT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (rental_id),
  UNIQUE KEY (rental_date, inventory_id, customer_id),
  KEY idx_fk_inventory_id (inventory_id),
  KEY idx_fk_customer_id (customer_id),
  KEY idx_fk_staff_id (staff_id),
  CONSTRAINT fk_rental_staff FOREIGN KEY (staff_id) REFERENCES staff (staff_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_rental_inventory FOREIGN KEY (inventory_id) REFERENCES inventory (inventory_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_rental_customer FOREIGN KEY (customer_id) REFERENCES customer (customer_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.rental.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE payment (
  payment_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  customer_id SMALLINT UNSIGNED NOT NULL,
  staff_id TINYINT UNSIGNED NOT NULL,
  rental_id INT DEFAULT NULL,
  amount DECIMAL(5,2) NOT NULL,
  payment_date DATETIME NOT NULL,
  last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (payment_id),
  KEY idx_fk_staff_id (staff_id),
  KEY idx_fk_customer_id (customer_id),
  CONSTRAINT fk_payment_rental FOREIGN KEY (rental_id) REFERENCES rental (rental_id) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_payment_customer FOREIGN KEY (customer_id) REFERENCES customer (customer_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_payment_staff FOREIGN KEY (staff_id) REFERENCES staff (staff_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.payment.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE inventory (
  inventory_id MEDIUMINT UNSIGNED NOT NULL AUTO_INCREMENT,
  film_id SMALLINT UNSIGNED NOT NULL,
  store_id TINYINT UNSIGNED NOT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (inventory_id),
  KEY idx_fk_film_id (film_id),
  KEY idx_store_id_film_id (store_id, film_id),
  CONSTRAINT fk_inventory_store FOREIGN KEY (store_id) REFERENCES store (store_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_inventory_film FOREIGN KEY (film_id) REFERENCES film (film_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.inventory.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE store (
  store_id TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
  manager_staff_id TINYINT UNSIGNED NOT NULL,
  address_id SMALLINT UNSIGNED NOT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (store_id),
  UNIQUE KEY idx_unique_manager (manager_staff_id),
  KEY idx_fk_address_id (address_id),
  CONSTRAINT fk_store_staff FOREIGN KEY (manager_staff_id) REFERENCES staff (staff_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_store_address FOREIGN KEY (address_id) REFERENCES address (address_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.store.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE staff (
  staff_id TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
  first_name VARCHAR(45) NOT NULL,
  last_name VARCHAR(45) NOT NULL,
  address_id SMALLINT UNSIGNED NOT NULL,
  picture BLOB DEFAULT NULL,
  email VARCHAR(50) DEFAULT NULL,
  store_id TINYINT UNSIGNED NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  username VARCHAR(16) NOT NULL,
  password VARCHAR(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (staff_id),
  KEY idx_fk_store_id (store_id),
  KEY idx_fk_address_id (address_id),
  CONSTRAINT fk_staff_store FOREIGN KEY (store_id) REFERENCES store (store_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_staff_address FOREIGN KEY (address_id) REFERENCES address (address_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.staff.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE address (
  address_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  address VARCHAR(50) NOT NULL,
  address2 VARCHAR(50) DEFAULT NULL,
  district VARCHAR(20) NOT NULL,
  city_id SMALLINT UNSIGNED NOT NULL,
  postal_code VARCHAR(10) DEFAULT NULL,
  phone VARCHAR(20) NOT NULL,
  location GEOMETRY NOT NULL SRID 0,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (address_id),
  KEY idx_fk_city_id (city_id),
  SPATIAL KEY idx_location (location),
  CONSTRAINT fk_address_city FOREIGN KEY (city_id) REFERENCES city (city_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.address.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE city (
  city_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  city VARCHAR(50) NOT NULL,
  country_id SMALLINT UNSIGNED NOT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (city_id),
  KEY idx_fk_country_id (country_id),
  CONSTRAINT fk_city_country FOREIGN KEY (country_id) REFERENCES country (country_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.city.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE country (
  country_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  country VARCHAR(50) NOT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (country_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.country.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE category (
  category_id TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name VARCHAR(25) NOT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.category.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE language (
  language_id TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name CHAR(20) NOT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (language_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.language.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE film_actor (
  actor_id SMALLINT UNSIGNED NOT NULL,
  film_id SMALLINT UNSIGNED NOT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (actor_id, film_id),
  KEY idx_fk_film_id (film_id),
  CONSTRAINT fk_film_actor_actor FOREIGN KEY (actor_id) REFERENCES actor (actor_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_film_actor_film FOREIGN KEY (film_id) REFERENCES film (film_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.film_actor.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE film_category (
  film_id SMALLINT UNSIGNED NOT NULL,
  category_id TINYINT UNSIGNED NOT NULL,
  last_update TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (film_id, category_id),
  CONSTRAINT fk_film_category_film FOREIGN KEY (film_id) REFERENCES film (film_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_film_category_category FOREIGN KEY (category_id) REFERENCES category (category_id) ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.film_category.dataset';

UPDATE catalog_datasets SET ddl = $ddl$CREATE TABLE film_text (
  film_id SMALLINT NOT NULL,
  title VARCHAR(255) NOT NULL,
  description TEXT,
  PRIMARY KEY (film_id),
  FULLTEXT KEY idx_title_description (title, description)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4;$ddl$
WHERE urn = 'sakila-mysql.sakila.film_text.dataset';


-- ---------------------------------------------------------------------------
-- 2.3 상세 설명 (마크다운) — 재실행 시 동일 값 갱신 (idempotent)
-- ---------------------------------------------------------------------------

UPDATE catalog_datasets SET description = $md$## 개요
DVD 대여점이 보유한 **영화 상품 마스터**. 대여 비즈니스의 기준 데이터로, 모든 재고(inventory)·대여(rental)·매출 분석이 이 테이블에서 출발한다.

## 주요 컬럼
- `rental_duration` / `rental_rate` — 기본 대여 기간(일)과 1회 대여 요금. 연체료 산정 기준
- `replacement_cost` — 분실·파손 시 고객 청구 비용
- `rating` — MPAA 관람등급 (G/PG/PG-13/R/NC-17) → 표준 *관람등급코드* 그룹 참조
- `special_features` — 예고편/코멘터리 등 부가 기능 (SET 타입)

## 관계
- `language_id`, `original_language_id` → **language** (음성/원어)
- **film_actor** ↔ actor (출연 배우, 다대다)
- **film_category** ↔ category (장르, 다대다)
- **inventory** — 점포별 실물 DVD 재고의 원본

## 활용
영화별 대여 빈도·매출 집계, 장르별 트렌드 분석, 재고 최적화(회전율 낮은 타이틀 식별)의 기준 차원.$md$
WHERE urn = 'sakila-mysql.sakila.film.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
**대여~반납 트랜잭션 원장**. 누가(customer) 무엇을(inventory) 언제 빌려가고 반납했는지를 기록하는 핵심 팩트 테이블.

## 주요 컬럼
- `rental_date` / `return_date` — 대여·반납 일시. `return_date IS NULL` 이면 **대여 중**
- `inventory_id` — 실물 DVD 단위 추적 (영화가 아닌 재고 개체 기준)
- `staff_id` — 대여 처리 직원 (매장 운영 분석용)
- 복합 UNIQUE (`rental_date`, `inventory_id`, `customer_id`) — 중복 대여 방지

## 관계
- → **inventory** → film/store (어느 점포의 어떤 영화인지 역추적)
- → **customer**, **staff**
- ← **payment** (대여 건별 결제)

## 활용
연체 감지(`return_date IS NULL AND rental_date + rental_duration < now()`), 일별 대여량 추이, 고객 대여 이력 조회.$md$
WHERE urn = 'sakila-mysql.sakila.rental.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
**대여 요금 결제 원장**. 매출 분석의 단일 진실 공급원(SSOT)이며, 회계 정산의 근거 데이터.

## 주요 컬럼
- `amount` — 결제 금액 (기본 대여료 + 연체료 포함)
- `payment_date` — 결제 일시 (매출 인식 기준일)
- `rental_id` — 대상 대여 건. 대여 삭제 시 SET NULL (결제 이력은 보존)

## 관계
- → **customer** (결제 주체), **staff** (수납 직원), **rental** (대상 거래)

## 활용
일/월별 매출 집계, 점포·직원별 수납 실적, 고객 LTV 산출. `rental_id IS NULL` 행은 원거래가 삭제된 이력성 결제.$md$
WHERE urn = 'sakila-mysql.sakila.payment.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
대여점 **회원(고객) 마스터**. 개인정보 포함 — `CONFIDENTIAL` 등급, 접근 통제 및 마스킹 대상.

## 주요 컬럼
- `email` — 마케팅 발송 키 (**PII: EMAIL**)
- `address_id` — 주소 연결 (**PII: ADDRESS**)
- `active` — 활성 회원 여부 (휴면/탈퇴 구분)
- `create_date` — 가입 일시 (코호트 분석 기준)
- `store_id` — 주 거래 점포

## 관계
- → **store**, **address** / ← **rental**, **payment**

## 활용
활성 회원 집계, 가입 코호트별 잔존율, 우수 고객(대여·결제 상위) 식별. PII 컬럼은 분석 시 가명처리 필수.$md$
WHERE urn = 'sakila-mysql.sakila.customer.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
**실물 DVD 재고 개체**. 같은 영화라도 점포·수량별로 행이 분리되는 재고 단위 테이블.

## 주요 컬럼
- `inventory_id` — 실물 개체 식별자 (대여는 이 단위로 발생)
- `film_id` × `store_id` — 어느 점포가 어떤 영화를 몇 장 보유하는지 결정

## 관계
- → **film**, **store** / ← **rental**

## 활용
대여 가능 여부 판단(해당 inventory 의 미반납 rental 존재 여부), 점포별 보유량·가동률 분석.$md$
WHERE urn = 'sakila-mysql.sakila.inventory.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
DVD 대여 **영업점 마스터**. 재고·고객·직원이 모두 점포 단위로 귀속되는 조직 차원의 최상위 엔티티.

## 주요 컬럼
- `manager_staff_id` — 점장 (UNIQUE: 1인 1점포)
- `address_id` — 점포 소재지

## 관계
- ← **inventory**, **customer**, **staff**

## 활용
점포별 매출·대여량·재고 비교의 기준 차원.$md$
WHERE urn = 'sakila-mysql.sakila.store.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
점포 **직원 마스터**. 대여/결제 처리의 담당자이자 시스템 로그인 계정. 개인정보·자격증명 포함 — `CONFIDENTIAL`.

## 주요 컬럼
- `username` / `password` — 로그인 자격증명 (**PII: CREDENTIAL**, SHA-1 해시)
- `email`, `picture` — 연락처·증명 사진 (**PII**)
- `active` — 재직 여부

## 관계
- → **store**, **address** / ← **rental**, **payment** (처리 담당)

## 활용
직원별 처리 실적 분석. 자격증명 컬럼은 어떤 분석 환경으로도 반출 금지.$md$
WHERE urn = 'sakila-mysql.sakila.staff.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
영화 **출연 배우 마스터**. film_actor 매핑을 통해 영화와 다대다로 연결된다.

## 주요 컬럼
- `first_name` / `last_name` — 배우 이름 (**PII: NAME**, 공인 정보 성격)

## 활용
배우별 출연작 검색, 인기 배우(출연작 대여량 기준) 분석.$md$
WHERE urn = 'sakila-mysql.sakila.actor.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
고객·직원·점포가 **공용으로 참조하는 주소 마스터**. 연락처(전화)와 공간 좌표 포함 — `CONFIDENTIAL`.

## 주요 컬럼
- `address`, `district`, `postal_code` — 주소 체계 (**PII: ADDRESS**)
- `phone` — 전화번호 (**PII: PHONE**)
- `location` — GEOMETRY 좌표 (SPATIAL KEY, 상권 분석용)

## 관계
- → **city** → country / ← customer, staff, store

## 활용
지역별 고객 분포, 점포 상권 분석. PII 컬럼은 마스킹 후 사용.$md$
WHERE urn = 'sakila-mysql.sakila.address.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
주소 체계의 **도시 마스터**. country 의 하위 계층.

## 관계
- → **country** / ← **address**

## 활용
지역(도시) 단위 고객·매출 분포 집계의 차원 테이블.$md$
WHERE urn = 'sakila-mysql.sakila.city.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
주소 체계의 **최상위 국가 마스터**.

## 관계
- ← **city** ← address

## 활용
국가 단위 롤업 집계의 차원 테이블.$md$
WHERE urn = 'sakila-mysql.sakila.country.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
영화 **장르 분류 마스터** (Action/Comedy/Drama 등 16종). film_category 매핑으로 영화와 다대다 연결.

## 활용
장르별 대여량·매출 분석, 추천(선호 장르) 피처의 기준 차원. 용어집 *장르* 용어와 연결됨.$md$
WHERE urn = 'sakila-mysql.sakila.category.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
영화 **언어 마스터**. film 의 음성 언어(`language_id`)와 원어(`original_language_id`) 양쪽에서 참조된다.

## 활용
언어별 카탈로그 구성 분석. 더빙/자막 전략 수립의 기준 차원.$md$
WHERE urn = 'sakila-mysql.sakila.language.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
**영화 × 배우 다대다 매핑**. 복합 PK (`actor_id`, `film_id`).

## 활용
배우별 출연작 조회, 영화별 캐스팅 조회의 브리지 테이블.$md$
WHERE urn = 'sakila-mysql.sakila.film_actor.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
**영화 × 장르 다대다 매핑**. 복합 PK (`film_id`, `category_id`). Sakila 에서는 영화당 장르 1개가 부여되어 있다.

## 활용
장르 차원 분석의 브리지 테이블.$md$
WHERE urn = 'sakila-mysql.sakila.film_category.dataset';

UPDATE catalog_datasets SET description = $md$## 개요
영화 제목·줄거리의 **전문 검색(FULLTEXT) 전용 비정규 테이블** (MyISAM). film 과 동일 키(film_id)를 공유한다.

## 활용
`MATCH(title, description) AGAINST(...)` 키워드 검색. 정합성은 트리거로 film 과 동기화되는 파생 데이터.$md$
WHERE urn = 'sakila-mysql.sakila.film_text.dataset';

-- ---------------------------------------------------------------------------
-- 3. 태그 + 데이터셋 태그 + 소유자
-- ---------------------------------------------------------------------------

INSERT INTO catalog_tags (name, description, color)
VALUES
    ('sakila', 'Sakila 샘플 DB (MySQL DVD 대여점)', '#f59e0b'),
    ('demo',   '데모/실습용 데이터',                 '#8b5cf6'),
    ('미디어', '미디어·엔터테인먼트 도메인',         '#10b981')
ON CONFLICT (name) DO NOTHING;

-- 전체 sakila 데이터셋에 sakila + demo 태그
INSERT INTO catalog_dataset_tags (dataset_id, tag_id)
SELECT d.id, t.id
FROM catalog_datasets d
JOIN catalog_tags t ON t.name IN ('sakila', 'demo')
WHERE d.urn LIKE 'sakila-mysql.sakila.%'
  AND NOT EXISTS (SELECT 1 FROM catalog_dataset_tags dt WHERE dt.dataset_id = d.id AND dt.tag_id = t.id);

-- 핵심 테이블에 미디어 태그
INSERT INTO catalog_dataset_tags (dataset_id, tag_id)
SELECT d.id, t.id
FROM catalog_datasets d
JOIN catalog_tags t ON t.name = '미디어'
WHERE d.urn IN ('sakila-mysql.sakila.film.dataset', 'sakila-mysql.sakila.actor.dataset', 'sakila-mysql.sakila.category.dataset')
  AND NOT EXISTS (SELECT 1 FROM catalog_dataset_tags dt WHERE dt.dataset_id = d.id AND dt.tag_id = t.id);

-- 핵심 테이블 소유자 (기술/비즈니스)
INSERT INTO catalog_owners (dataset_id, owner_name, owner_type)
SELECT d.id, o.owner_name, o.owner_type
FROM (VALUES
    ('sakila-mysql.sakila.film.dataset',     'admin',  'TECHNICAL_OWNER'),
    ('sakila-mysql.sakila.film.dataset',     'mjkim',  'BUSINESS_OWNER'),
    ('sakila-mysql.sakila.rental.dataset',   'admin',  'TECHNICAL_OWNER'),
    ('sakila-mysql.sakila.rental.dataset',   'khshin', 'BUSINESS_OWNER'),
    ('sakila-mysql.sakila.payment.dataset',  'admin',  'TECHNICAL_OWNER'),
    ('sakila-mysql.sakila.payment.dataset',  'tkoh',   'BUSINESS_OWNER'),
    ('sakila-mysql.sakila.customer.dataset', 'admin',  'TECHNICAL_OWNER'),
    ('sakila-mysql.sakila.customer.dataset', 'ejcho',  'DATA_STEWARD')
) AS o(urn, owner_name, owner_type)
JOIN catalog_datasets d ON d.urn = o.urn
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_owners ow
    WHERE ow.dataset_id = d.id AND ow.owner_name = o.owner_name AND ow.owner_type = o.owner_type
);

-- ---------------------------------------------------------------------------
-- 4. 용어집 — '영화 대여' 카테고리 + 용어 + 데이터셋 연결
-- ---------------------------------------------------------------------------

INSERT INTO catalog_glossary_terms (name, description, term_type)
VALUES ('영화 대여', 'DVD 대여점(Sakila) 도메인 비즈니스 용어', 'CATEGORY')
ON CONFLICT (name) DO NOTHING;

INSERT INTO catalog_glossary_terms (name, description, parent_id, term_type)
SELECT t.name, t.description, p.id, 'TERM'
FROM (VALUES
    ('대여',     '고객이 점포의 실물 DVD(재고)를 빌려가는 트랜잭션. 대여일시와 반납일시로 대여 기간을 관리한다.'),
    ('반납',     '대여한 DVD 를 점포에 돌려주는 행위. 기본 대여 기간 초과 시 연체료가 발생한다.'),
    ('연체료',   '기본 대여 기간(rental_duration)을 초과해 반납할 때 일 단위로 부과되는 추가 요금.'),
    ('대여료',   '영화별로 책정된 1회 대여 기본 요금(rental_rate).'),
    ('교체비용', '대여 중 분실·파손 시 고객에게 청구하는 비용(replacement_cost).'),
    ('관람등급', '영화 시청 가능 연령 분류 (G/PG/PG-13/R/NC-17). 미국 MPAA 등급 체계.'),
    ('영화재고', '점포가 보유한 실물 DVD 단위. 같은 영화도 점포·수량별로 개별 재고로 관리된다.'),
    ('대여점포', 'DVD 대여 영업점. 재고와 고객·직원이 점포 단위로 귀속된다.'),
    ('활성회원', '현재 대여 서비스를 이용할 수 있는 상태의 고객(active=true).'),
    ('장르',     '영화 분류 체계 (Action/Comedy/Drama 등 16종).')
) AS t(name, description)
JOIN catalog_glossary_terms p ON p.name = '영화 대여' AND p.term_type = 'CATEGORY'
ON CONFLICT (name) DO NOTHING;

-- 용어 ↔ 데이터셋 연결
INSERT INTO catalog_dataset_glossary_terms (dataset_id, term_id)
SELECT d.id, g.id
FROM (VALUES
    ('sakila-mysql.sakila.rental.dataset',    '대여'),
    ('sakila-mysql.sakila.rental.dataset',    '반납'),
    ('sakila-mysql.sakila.payment.dataset',   '연체료'),
    ('sakila-mysql.sakila.film.dataset',      '대여료'),
    ('sakila-mysql.sakila.film.dataset',      '교체비용'),
    ('sakila-mysql.sakila.film.dataset',      '관람등급'),
    ('sakila-mysql.sakila.inventory.dataset', '영화재고'),
    ('sakila-mysql.sakila.store.dataset',     '대여점포'),
    ('sakila-mysql.sakila.customer.dataset',  '활성회원'),
    ('sakila-mysql.sakila.category.dataset',  '장르')
) AS m(urn, term_name)
JOIN catalog_datasets d ON d.urn = m.urn
JOIN catalog_glossary_terms g ON g.name = m.term_name AND g.term_type = 'TERM'
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_dataset_glossary_terms x
    WHERE x.dataset_id = d.id AND x.term_id = g.id
);

-- ---------------------------------------------------------------------------
-- 5. 데이터 표준 — 영화대여 표준 사전
-- ---------------------------------------------------------------------------

INSERT INTO catalog_standard_dictionary (dict_name, description, version, status, effective_date, created_by)
VALUES ('영화대여 표준 사전', 'DVD 대여(Sakila) 도메인 데이터 표준 사전', '1.0', 'ACTIVE', CURRENT_DATE, 'admin')
ON CONFLICT (dict_name) DO NOTHING;

-- 5.1 표준 단어
INSERT INTO catalog_standard_word (dictionary_id, word_name, word_english, word_abbr, word_type, description, status)
SELECT d.id, w.word_name, w.word_english, w.word_abbr, w.word_type, w.description, 'ACTIVE'
FROM (VALUES
    ('영화',   'Film',      'FLM',  'GENERAL', '대여 상품인 영화'),
    ('대여',   'Rental',    'RENT', 'GENERAL', 'DVD 대여 트랜잭션'),
    ('반납',   'Return',    'RTN',  'GENERAL', '대여 반납'),
    ('고객',   'Customer',  'CUST', 'GENERAL', '대여점 회원'),
    ('결제',   'Payment',   'PAY',  'GENERAL', '요금 결제'),
    ('재고',   'Inventory', 'INV',  'GENERAL', '실물 DVD 재고'),
    ('점포',   'Store',     'STR',  'GENERAL', '대여 영업점'),
    ('배우',   'Actor',     'ACT',  'GENERAL', '영화 출연 배우'),
    ('등급',   'Rating',    'RTG',  'GENERAL', '관람등급'),
    ('번호',   'Number',    'NO',   'SUFFIX',  '식별 번호 접미어'),
    ('코드',   'Code',      'CD',   'SUFFIX',  '분류 코드 접미어'),
    ('일자',   'Date',      'DT',   'SUFFIX',  '날짜 접미어'),
    ('금액',   'Amount',    'AMT',  'SUFFIX',  '금액 접미어'),
    ('명',     'Name',      'NM',   'SUFFIX',  '명칭 접미어')
) AS w(word_name, word_english, word_abbr, word_type, description)
JOIN catalog_standard_dictionary d ON d.dict_name = '영화대여 표준 사전'
ON CONFLICT (dictionary_id, word_name) DO NOTHING;

-- 5.2 표준 도메인
INSERT INTO catalog_standard_domain (dictionary_id, domain_name, domain_group, data_type, data_length, data_precision, data_scale, description, status)
SELECT d.id, dom.domain_name, dom.domain_group, dom.data_type, dom.data_length, dom.data_precision, dom.data_scale, dom.description, 'ACTIVE'
FROM (VALUES
    ('번호', '문자형', 'VARCHAR', 20,   NULL, NULL, '식별 번호 형식'),
    ('코드', '문자형', 'VARCHAR', 10,   NULL, NULL, '분류 코드 형식'),
    ('명',   '문자형', 'VARCHAR', 100,  NULL, NULL, '명칭 형식'),
    ('일자', '날짜형', 'DATE',    NULL, NULL, NULL, '날짜 형식 (YYYY-MM-DD)'),
    ('금액', '숫자형', 'NUMERIC', NULL, 15,   2,    '금액 형식')
) AS dom(domain_name, domain_group, data_type, data_length, data_precision, data_scale, description)
JOIN catalog_standard_dictionary d ON d.dict_name = '영화대여 표준 사전'
ON CONFLICT (dictionary_id, domain_name) DO NOTHING;

-- 5.3 코드 그룹: 관람등급코드 (MPAA)
INSERT INTO catalog_code_group (dictionary_id, group_name, group_english, description, status)
SELECT d.id, '관람등급코드', 'Film Rating Code', '영화 관람등급 분류 (미국 MPAA 체계)', 'ACTIVE'
FROM catalog_standard_dictionary d WHERE d.dict_name = '영화대여 표준 사전'
ON CONFLICT (dictionary_id, group_name) DO NOTHING;

INSERT INTO catalog_code_value (code_group_id, code_value, code_name, code_english, sort_order)
SELECT g.id, v.code_value, v.code_name, v.code_english, v.sort_order
FROM (VALUES
    ('G',     '전체관람가',        'General Audiences',          1),
    ('PG',    '보호자 지도 권장',  'Parental Guidance Suggested', 2),
    ('PG-13', '13세 미만 주의',    'Parents Strongly Cautioned',  3),
    ('R',     '청소년 관람 제한',  'Restricted',                  4),
    ('NC-17', '17세 이하 관람 불가', 'Adults Only',               5)
) AS v(code_value, code_name, code_english, sort_order)
JOIN catalog_code_group g ON g.group_name = '관람등급코드'
ON CONFLICT (code_group_id, code_value) DO NOTHING;

-- 5.4 표준 용어
INSERT INTO catalog_standard_term (dictionary_id, term_name, term_english, term_abbr, physical_name, domain_id, description, created_by, status)
SELECT d.id, t.term_name, t.term_english, t.term_abbr, t.physical_name, dom.id, t.description, 'admin', 'ACTIVE'
FROM (VALUES
    ('대여번호',   'Rental Number',   'RENT_NO',  'rent_no',  '번호', '대여 트랜잭션 식별 번호 (rental.rental_id)'),
    ('고객번호',   'Customer Number', 'CUST_NO',  'cust_no',  '번호', '고객 식별 번호 (customer.customer_id)'),
    ('재고번호',   'Inventory Number', 'INV_NO',  'inv_no',   '번호', '실물 DVD 재고 식별 번호 (inventory.inventory_id)'),
    ('점포코드',   'Store Code',      'STR_CD',   'str_cd',   '코드', '점포 분류 코드 (store.store_id)'),
    ('등급코드',   'Rating Code',     'RTG_CD',   'rtg_cd',   '코드', '관람등급 코드 (film.rating, 관람등급코드 그룹 참조)'),
    ('대여일자',   'Rental Date',     'RENT_DT',  'rent_dt',  '일자', '대여 발생 일자 (rental.rental_date)'),
    ('반납일자',   'Return Date',     'RTN_DT',   'rtn_dt',   '일자', '반납 완료 일자 (rental.return_date)'),
    ('결제금액',   'Payment Amount',  'PAY_AMT',  'pay_amt',  '금액', '결제 금액 (payment.amount)'),
    ('영화명',     'Film Name',       'FLM_NM',   'flm_nm',   '명',   '영화 제목 (film.title)')
) AS t(term_name, term_english, term_abbr, physical_name, domain_name, description)
JOIN catalog_standard_dictionary d ON d.dict_name = '영화대여 표준 사전'
JOIN catalog_standard_domain dom ON dom.dictionary_id = d.id AND dom.domain_name = t.domain_name
ON CONFLICT (dictionary_id, term_name) DO NOTHING;

-- 5.5 용어 구성 단어 매핑 (형태소 분해)
INSERT INTO catalog_standard_term_words (term_id, word_id, ordinal)
SELECT t.id, w.id, m.ordinal
FROM (VALUES
    ('대여번호', '대여', 1), ('대여번호', '번호', 2),
    ('고객번호', '고객', 1), ('고객번호', '번호', 2),
    ('재고번호', '재고', 1), ('재고번호', '번호', 2),
    ('점포코드', '점포', 1), ('점포코드', '코드', 2),
    ('등급코드', '등급', 1), ('등급코드', '코드', 2),
    ('대여일자', '대여', 1), ('대여일자', '일자', 2),
    ('반납일자', '반납', 1), ('반납일자', '일자', 2),
    ('결제금액', '결제', 1), ('결제금액', '금액', 2),
    ('영화명',   '영화', 1), ('영화명',   '명',   2)
) AS m(term_name, word_name, ordinal)
JOIN catalog_standard_dictionary d ON d.dict_name = '영화대여 표준 사전'
JOIN catalog_standard_term t ON t.dictionary_id = d.id AND t.term_name = m.term_name
JOIN catalog_standard_word w ON w.dictionary_id = d.id AND w.word_name = m.word_name
ON CONFLICT (term_id, word_id, ordinal) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 5.6 표준 단어 확장 (컬럼 매핑 커버리지용)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_standard_word (dictionary_id, word_name, word_english, word_abbr, word_type, description, status)
SELECT d.id, w.word_name, w.word_english, w.word_abbr, w.word_type, w.description, 'ACTIVE'
FROM (VALUES
    ('직원',     'Staff',    'STF',  'GENERAL', '점포 직원'),
    ('주소',     'Address',  'ADDR', 'GENERAL', '주소 정보'),
    ('도시',     'City',     'CTY',  'GENERAL', '도시'),
    ('국가',     'Country',  'CNTY', 'GENERAL', '국가'),
    ('언어',     'Language', 'LANG', 'GENERAL', '영화 언어'),
    ('카테고리', 'Category', 'CTGR', 'GENERAL', '영화 장르 분류'),
    ('이메일',   'Email',    'EML',  'GENERAL', '이메일'),
    ('전화',     'Phone',    'TEL',  'GENERAL', '전화'),
    ('우편',     'Postal',   'ZIP',  'GENERAL', '우편'),
    ('가입',     'Join',     'JOIN', 'GENERAL', '회원 가입')
) AS w(word_name, word_english, word_abbr, word_type, description)
JOIN catalog_standard_dictionary d ON d.dict_name = '영화대여 표준 사전'
ON CONFLICT (dictionary_id, word_name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 5.7 표준 용어 확장
-- ---------------------------------------------------------------------------

INSERT INTO catalog_standard_term (dictionary_id, term_name, term_english, term_abbr, physical_name, domain_id, description, created_by, status)
SELECT d.id, t.term_name, t.term_english, t.term_abbr, t.physical_name, dom.id, t.description, 'admin', 'ACTIVE'
FROM (VALUES
    ('영화번호',     'Film Number',     'FLM_NO',   'flm_no',   '번호', '영화 식별 번호 (film.film_id)'),
    ('배우번호',     'Actor Number',    'ACT_NO',   'act_no',   '번호', '배우 식별 번호 (actor.actor_id)'),
    ('직원번호',     'Staff Number',    'STF_NO',   'stf_no',   '번호', '직원 식별 번호 (staff.staff_id)'),
    ('주소번호',     'Address Number',  'ADDR_NO',  'addr_no',  '번호', '주소 식별 번호 (address.address_id)'),
    ('도시번호',     'City Number',     'CTY_NO',   'cty_no',   '번호', '도시 식별 번호 (city.city_id)'),
    ('국가번호',     'Country Number',  'CNTY_NO',  'cnty_no',  '번호', '국가 식별 번호 (country.country_id)'),
    ('언어번호',     'Language Number', 'LANG_NO',  'lang_no',  '번호', '언어 식별 번호 (language.language_id)'),
    ('카테고리번호', 'Category Number', 'CTGR_NO',  'ctgr_no',  '번호', '장르 식별 번호 (category.category_id)'),
    ('결제번호',     'Payment Number',  'PAY_NO',   'pay_no',   '번호', '결제 식별 번호 (payment.payment_id)'),
    ('전화번호',     'Phone Number',    'TEL_NO',   'tel_no',   '번호', '전화번호 (address.phone)'),
    ('우편번호',     'Postal Number',   'ZIP_NO',   'zip_no',   '번호', '우편번호 (address.postal_code)'),
    ('결제일자',     'Payment Date',    'PAY_DT',   'pay_dt',   '일자', '결제 일자 (payment.payment_date)'),
    ('가입일자',     'Join Date',       'JOIN_DT',  'join_dt',  '일자', '회원 가입 일자 (customer.create_date)'),
    ('대여금액',     'Rental Amount',   'RENT_AMT', 'rent_amt', '금액', '대여 기본 요금 (film.rental_rate)'),
    ('도시명',       'City Name',       'CTY_NM',   'cty_nm',   '명',   '도시 명칭 (city.city)'),
    ('국가명',       'Country Name',    'CNTY_NM',  'cnty_nm',  '명',   '국가 명칭 (country.country)'),
    ('언어명',       'Language Name',   'LANG_NM',  'lang_nm',  '명',   '언어 명칭 (language.name)'),
    ('카테고리명',   'Category Name',   'CTGR_NM',  'ctgr_nm',  '명',   '장르 명칭 (category.name)'),
    ('고객명',       'Customer Name',   'CUST_NM',  'cust_nm',  '명',   '고객 이름 (customer.first/last_name)'),
    ('배우명',       'Actor Name',      'ACT_NM',   'act_nm',   '명',   '배우 이름 (actor.first/last_name)'),
    ('직원명',       'Staff Name',      'STF_NM',   'stf_nm',   '명',   '직원 이름 (staff.first/last_name)'),
    ('이메일주소',   'Email Address',   'EML_ADDR', 'eml_addr', '명',   '이메일 주소 (customer/staff.email)')
) AS t(term_name, term_english, term_abbr, physical_name, domain_name, description)
JOIN catalog_standard_dictionary d ON d.dict_name = '영화대여 표준 사전'
JOIN catalog_standard_domain dom ON dom.dictionary_id = d.id AND dom.domain_name = t.domain_name
ON CONFLICT (dictionary_id, term_name) DO NOTHING;

-- 확장 용어 구성 단어 매핑
INSERT INTO catalog_standard_term_words (term_id, word_id, ordinal)
SELECT t.id, w.id, m.ordinal
FROM (VALUES
    ('영화번호',     '영화',     1), ('영화번호',     '번호', 2),
    ('배우번호',     '배우',     1), ('배우번호',     '번호', 2),
    ('직원번호',     '직원',     1), ('직원번호',     '번호', 2),
    ('주소번호',     '주소',     1), ('주소번호',     '번호', 2),
    ('도시번호',     '도시',     1), ('도시번호',     '번호', 2),
    ('국가번호',     '국가',     1), ('국가번호',     '번호', 2),
    ('언어번호',     '언어',     1), ('언어번호',     '번호', 2),
    ('카테고리번호', '카테고리', 1), ('카테고리번호', '번호', 2),
    ('결제번호',     '결제',     1), ('결제번호',     '번호', 2),
    ('전화번호',     '전화',     1), ('전화번호',     '번호', 2),
    ('우편번호',     '우편',     1), ('우편번호',     '번호', 2),
    ('결제일자',     '결제',     1), ('결제일자',     '일자', 2),
    ('가입일자',     '가입',     1), ('가입일자',     '일자', 2),
    ('대여금액',     '대여',     1), ('대여금액',     '금액', 2),
    ('도시명',       '도시',     1), ('도시명',       '명',   2),
    ('국가명',       '국가',     1), ('국가명',       '명',   2),
    ('언어명',       '언어',     1), ('언어명',       '명',   2),
    ('카테고리명',   '카테고리', 1), ('카테고리명',   '명',   2),
    ('고객명',       '고객',     1), ('고객명',       '명',   2),
    ('배우명',       '배우',     1), ('배우명',       '명',   2),
    ('직원명',       '직원',     1), ('직원명',       '명',   2),
    ('이메일주소',   '이메일',   1), ('이메일주소',   '주소', 2)
) AS m(term_name, word_name, ordinal)
JOIN catalog_standard_dictionary d ON d.dict_name = '영화대여 표준 사전'
JOIN catalog_standard_term t ON t.dictionary_id = d.id AND t.term_name = m.term_name
JOIN catalog_standard_word w ON w.dictionary_id = d.id AND w.word_name = m.word_name
ON CONFLICT (term_id, word_id, ordinal) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 5.8 표준 용어 ↔ 데이터셋 컬럼 매핑
--     MATCHED: 의미·용도 일치 / SIMILAR: 의미 일치하나 물리명이 표준 약어와 다름
--     (Sakila 는 자체 명명 규칙을 쓰므로 식별자 계열은 SIMILAR 가 현실적이나,
--      데모 가독성을 위해 의미 정확 대응은 MATCHED, 분할/변형 컬럼만 SIMILAR 로 표기)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_term_column_mapping (term_id, dataset_id, schema_id, mapping_type)
SELECT t.id, d.id, sc.id, m.mapping_type
FROM (VALUES
    -- 기존 용어
    ('대여번호',     'sakila-mysql.sakila.rental.dataset',        'rental_id',            'MATCHED'),
    ('대여번호',     'sakila-mysql.sakila.payment.dataset',       'rental_id',            'MATCHED'),
    ('고객번호',     'sakila-mysql.sakila.customer.dataset',      'customer_id',          'MATCHED'),
    ('고객번호',     'sakila-mysql.sakila.rental.dataset',        'customer_id',          'MATCHED'),
    ('고객번호',     'sakila-mysql.sakila.payment.dataset',       'customer_id',          'MATCHED'),
    ('재고번호',     'sakila-mysql.sakila.inventory.dataset',     'inventory_id',         'MATCHED'),
    ('재고번호',     'sakila-mysql.sakila.rental.dataset',        'inventory_id',         'MATCHED'),
    ('점포코드',     'sakila-mysql.sakila.store.dataset',         'store_id',             'MATCHED'),
    ('점포코드',     'sakila-mysql.sakila.customer.dataset',      'store_id',             'MATCHED'),
    ('점포코드',     'sakila-mysql.sakila.inventory.dataset',     'store_id',             'MATCHED'),
    ('점포코드',     'sakila-mysql.sakila.staff.dataset',         'store_id',             'MATCHED'),
    ('등급코드',     'sakila-mysql.sakila.film.dataset',          'rating',               'SIMILAR'),
    ('대여일자',     'sakila-mysql.sakila.rental.dataset',        'rental_date',          'MATCHED'),
    ('반납일자',     'sakila-mysql.sakila.rental.dataset',        'return_date',          'MATCHED'),
    ('결제금액',     'sakila-mysql.sakila.payment.dataset',       'amount',               'SIMILAR'),
    ('영화명',       'sakila-mysql.sakila.film.dataset',          'title',                'SIMILAR'),
    ('영화명',       'sakila-mysql.sakila.film_text.dataset',     'title',                'SIMILAR'),
    -- 확장 용어
    ('영화번호',     'sakila-mysql.sakila.film.dataset',          'film_id',              'MATCHED'),
    ('영화번호',     'sakila-mysql.sakila.inventory.dataset',     'film_id',              'MATCHED'),
    ('영화번호',     'sakila-mysql.sakila.film_actor.dataset',    'film_id',              'MATCHED'),
    ('영화번호',     'sakila-mysql.sakila.film_category.dataset', 'film_id',              'MATCHED'),
    ('영화번호',     'sakila-mysql.sakila.film_text.dataset',     'film_id',              'MATCHED'),
    ('배우번호',     'sakila-mysql.sakila.actor.dataset',         'actor_id',             'MATCHED'),
    ('배우번호',     'sakila-mysql.sakila.film_actor.dataset',    'actor_id',             'MATCHED'),
    ('직원번호',     'sakila-mysql.sakila.staff.dataset',         'staff_id',             'MATCHED'),
    ('직원번호',     'sakila-mysql.sakila.rental.dataset',        'staff_id',             'MATCHED'),
    ('직원번호',     'sakila-mysql.sakila.payment.dataset',       'staff_id',             'MATCHED'),
    ('직원번호',     'sakila-mysql.sakila.store.dataset',         'manager_staff_id',     'SIMILAR'),
    ('주소번호',     'sakila-mysql.sakila.address.dataset',       'address_id',           'MATCHED'),
    ('주소번호',     'sakila-mysql.sakila.customer.dataset',      'address_id',           'MATCHED'),
    ('주소번호',     'sakila-mysql.sakila.staff.dataset',         'address_id',           'MATCHED'),
    ('주소번호',     'sakila-mysql.sakila.store.dataset',         'address_id',           'MATCHED'),
    ('도시번호',     'sakila-mysql.sakila.city.dataset',          'city_id',              'MATCHED'),
    ('도시번호',     'sakila-mysql.sakila.address.dataset',       'city_id',              'MATCHED'),
    ('국가번호',     'sakila-mysql.sakila.country.dataset',       'country_id',           'MATCHED'),
    ('국가번호',     'sakila-mysql.sakila.city.dataset',          'country_id',           'MATCHED'),
    ('언어번호',     'sakila-mysql.sakila.language.dataset',      'language_id',          'MATCHED'),
    ('언어번호',     'sakila-mysql.sakila.film.dataset',          'language_id',          'MATCHED'),
    ('언어번호',     'sakila-mysql.sakila.film.dataset',          'original_language_id', 'SIMILAR'),
    ('카테고리번호', 'sakila-mysql.sakila.category.dataset',      'category_id',          'MATCHED'),
    ('카테고리번호', 'sakila-mysql.sakila.film_category.dataset', 'category_id',          'MATCHED'),
    ('결제번호',     'sakila-mysql.sakila.payment.dataset',       'payment_id',           'MATCHED'),
    ('전화번호',     'sakila-mysql.sakila.address.dataset',       'phone',                'SIMILAR'),
    ('우편번호',     'sakila-mysql.sakila.address.dataset',       'postal_code',          'SIMILAR'),
    ('결제일자',     'sakila-mysql.sakila.payment.dataset',       'payment_date',         'MATCHED'),
    ('가입일자',     'sakila-mysql.sakila.customer.dataset',      'create_date',          'SIMILAR'),
    ('대여금액',     'sakila-mysql.sakila.film.dataset',          'rental_rate',          'SIMILAR'),
    ('도시명',       'sakila-mysql.sakila.city.dataset',          'city',                 'SIMILAR'),
    ('국가명',       'sakila-mysql.sakila.country.dataset',       'country',              'SIMILAR'),
    ('언어명',       'sakila-mysql.sakila.language.dataset',      'name',                 'SIMILAR'),
    ('카테고리명',   'sakila-mysql.sakila.category.dataset',      'name',                 'SIMILAR'),
    ('고객명',       'sakila-mysql.sakila.customer.dataset',      'first_name',           'SIMILAR'),
    ('고객명',       'sakila-mysql.sakila.customer.dataset',      'last_name',            'SIMILAR'),
    ('배우명',       'sakila-mysql.sakila.actor.dataset',         'first_name',           'SIMILAR'),
    ('배우명',       'sakila-mysql.sakila.actor.dataset',         'last_name',            'SIMILAR'),
    ('직원명',       'sakila-mysql.sakila.staff.dataset',         'first_name',           'SIMILAR'),
    ('직원명',       'sakila-mysql.sakila.staff.dataset',         'last_name',            'SIMILAR'),
    ('이메일주소',   'sakila-mysql.sakila.customer.dataset',      'email',                'SIMILAR'),
    ('이메일주소',   'sakila-mysql.sakila.staff.dataset',         'email',                'SIMILAR')
) AS m(term_name, urn, field_path, mapping_type)
JOIN catalog_standard_dictionary dict ON dict.dict_name = '영화대여 표준 사전'
JOIN catalog_standard_term t ON t.dictionary_id = dict.id AND t.term_name = m.term_name
JOIN catalog_datasets d ON d.urn = m.urn
JOIN catalog_dataset_schemas sc ON sc.dataset_id = d.id AND sc.field_path = m.field_path
ON CONFLICT (term_id, schema_id) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 6. 리니지 — Sakila 데이터 흐름 DAG
--    FK 참조 관계(조인 의존): REFERENCE — 그래프에서 점선·회색으로 표시
--    film → film_text(트리거 동기화 파생): TRANSFORM — 실제 데이터 흐름, 실선
-- ---------------------------------------------------------------------------

INSERT INTO argus_dataset_lineage
    (source_dataset_id, target_dataset_id, relation_type, lineage_source, description, created_by, query_count)
SELECT s.id, t.id, e.relation_type, 'MANUAL', e.description, 'admin', 0
FROM (VALUES
    -- 주소 체계: country → city → address → (customer/staff/store)
    ('sakila-mysql.sakila.country.dataset',   'sakila-mysql.sakila.city.dataset',          'REFERENCE', '도시가 국가를 참조 (country_id)'),
    ('sakila-mysql.sakila.city.dataset',      'sakila-mysql.sakila.address.dataset',       'REFERENCE', '주소가 도시를 참조 (city_id)'),
    ('sakila-mysql.sakila.address.dataset',   'sakila-mysql.sakila.customer.dataset',      'REFERENCE', '고객 주소 (address_id)'),
    ('sakila-mysql.sakila.address.dataset',   'sakila-mysql.sakila.staff.dataset',         'REFERENCE', '직원 주소 (address_id)'),
    ('sakila-mysql.sakila.address.dataset',   'sakila-mysql.sakila.store.dataset',         'REFERENCE', '점포 소재지 (address_id)'),
    -- 조직: store ↔ staff
    ('sakila-mysql.sakila.store.dataset',     'sakila-mysql.sakila.staff.dataset',         'REFERENCE', '직원 소속 점포 (store_id)'),
    ('sakila-mysql.sakila.staff.dataset',     'sakila-mysql.sakila.store.dataset',         'REFERENCE', '점포 관리자 지정 (manager_staff_id)'),
    ('sakila-mysql.sakila.store.dataset',     'sakila-mysql.sakila.customer.dataset',      'REFERENCE', '고객 주 거래 점포 (store_id)'),
    -- 영화 카탈로그: language/actor/category → film 계열
    ('sakila-mysql.sakila.language.dataset',  'sakila-mysql.sakila.film.dataset',          'REFERENCE', '영화 음성/원어 언어 (language_id, original_language_id)'),
    ('sakila-mysql.sakila.actor.dataset',     'sakila-mysql.sakila.film_actor.dataset',    'REFERENCE', '출연 배우 매핑 (actor_id)'),
    ('sakila-mysql.sakila.film.dataset',      'sakila-mysql.sakila.film_actor.dataset',    'REFERENCE', '출연 영화 매핑 (film_id)'),
    ('sakila-mysql.sakila.film.dataset',      'sakila-mysql.sakila.film_category.dataset', 'REFERENCE', '영화-장르 매핑 (film_id)'),
    ('sakila-mysql.sakila.category.dataset',  'sakila-mysql.sakila.film_category.dataset', 'REFERENCE', '장르 분류 매핑 (category_id)'),
    ('sakila-mysql.sakila.film.dataset',      'sakila-mysql.sakila.film_text.dataset',     'TRANSFORM',    'film 의 제목·줄거리를 트리거로 동기화하는 전문 검색 파생 테이블'),
    -- 재고/거래: film·store → inventory → rental → payment
    ('sakila-mysql.sakila.film.dataset',      'sakila-mysql.sakila.inventory.dataset',     'REFERENCE', '재고가 보유 영화를 참조 (film_id)'),
    ('sakila-mysql.sakila.store.dataset',     'sakila-mysql.sakila.inventory.dataset',     'REFERENCE', '재고 보유 점포 (store_id)'),
    ('sakila-mysql.sakila.inventory.dataset', 'sakila-mysql.sakila.rental.dataset',        'REFERENCE', '대여 대상 재고 (inventory_id)'),
    ('sakila-mysql.sakila.customer.dataset',  'sakila-mysql.sakila.rental.dataset',        'REFERENCE', '대여 고객 (customer_id)'),
    ('sakila-mysql.sakila.staff.dataset',     'sakila-mysql.sakila.rental.dataset',        'REFERENCE', '대여 처리 직원 (staff_id)'),
    ('sakila-mysql.sakila.rental.dataset',    'sakila-mysql.sakila.payment.dataset',       'REFERENCE', '대여 건에 대한 결제 (rental_id)'),
    ('sakila-mysql.sakila.customer.dataset',  'sakila-mysql.sakila.payment.dataset',       'REFERENCE', '결제 고객 (customer_id)'),
    ('sakila-mysql.sakila.staff.dataset',     'sakila-mysql.sakila.payment.dataset',       'REFERENCE', '결제 수납 직원 (staff_id)')
) AS e(src_urn, tgt_urn, relation_type, description)
JOIN catalog_datasets s ON s.urn = e.src_urn
JOIN catalog_datasets t ON t.urn = e.tgt_urn
-- unique 키 구성이 DB 세대에 따라 3컬럼/4컬럼으로 갈려 ON CONFLICT 대신 NOT EXISTS 사용
WHERE NOT EXISTS (
    SELECT 1 FROM argus_dataset_lineage l
    WHERE l.source_dataset_id = s.id AND l.target_dataset_id = t.id
      AND l.relation_type = e.relation_type AND l.lineage_source = 'MANUAL'
);

-- 6.1 컬럼 수준 매핑 (영향 분석용)
INSERT INTO argus_dataset_column_mapping (dataset_lineage_id, source_column, target_column, transform_type)
SELECT l.id, m.source_column, m.target_column, m.transform_type
FROM (VALUES
    ('sakila-mysql.sakila.country.dataset',   'sakila-mysql.sakila.city.dataset',          'country_id',   'country_id',           'DIRECT'),
    ('sakila-mysql.sakila.city.dataset',      'sakila-mysql.sakila.address.dataset',       'city_id',      'city_id',              'DIRECT'),
    ('sakila-mysql.sakila.address.dataset',   'sakila-mysql.sakila.customer.dataset',      'address_id',   'address_id',           'DIRECT'),
    ('sakila-mysql.sakila.address.dataset',   'sakila-mysql.sakila.staff.dataset',         'address_id',   'address_id',           'DIRECT'),
    ('sakila-mysql.sakila.address.dataset',   'sakila-mysql.sakila.store.dataset',         'address_id',   'address_id',           'DIRECT'),
    ('sakila-mysql.sakila.store.dataset',     'sakila-mysql.sakila.staff.dataset',         'store_id',     'store_id',             'DIRECT'),
    ('sakila-mysql.sakila.staff.dataset',     'sakila-mysql.sakila.store.dataset',         'staff_id',     'manager_staff_id',     'DIRECT'),
    ('sakila-mysql.sakila.store.dataset',     'sakila-mysql.sakila.customer.dataset',      'store_id',     'store_id',             'DIRECT'),
    ('sakila-mysql.sakila.language.dataset',  'sakila-mysql.sakila.film.dataset',          'language_id',  'language_id',          'DIRECT'),
    ('sakila-mysql.sakila.language.dataset',  'sakila-mysql.sakila.film.dataset',          'language_id',  'original_language_id', 'DIRECT'),
    ('sakila-mysql.sakila.actor.dataset',     'sakila-mysql.sakila.film_actor.dataset',    'actor_id',     'actor_id',             'DIRECT'),
    ('sakila-mysql.sakila.film.dataset',      'sakila-mysql.sakila.film_actor.dataset',    'film_id',      'film_id',              'DIRECT'),
    ('sakila-mysql.sakila.film.dataset',      'sakila-mysql.sakila.film_category.dataset', 'film_id',      'film_id',              'DIRECT'),
    ('sakila-mysql.sakila.category.dataset',  'sakila-mysql.sakila.film_category.dataset', 'category_id',  'category_id',          'DIRECT'),
    ('sakila-mysql.sakila.film.dataset',      'sakila-mysql.sakila.film_text.dataset',     'film_id',      'film_id',              'DIRECT'),
    ('sakila-mysql.sakila.film.dataset',      'sakila-mysql.sakila.film_text.dataset',     'title',        'title',                'DIRECT'),
    ('sakila-mysql.sakila.film.dataset',      'sakila-mysql.sakila.film_text.dataset',     'description',  'description',          'DIRECT'),
    ('sakila-mysql.sakila.film.dataset',      'sakila-mysql.sakila.inventory.dataset',     'film_id',      'film_id',              'DIRECT'),
    ('sakila-mysql.sakila.store.dataset',     'sakila-mysql.sakila.inventory.dataset',     'store_id',     'store_id',             'DIRECT'),
    ('sakila-mysql.sakila.inventory.dataset', 'sakila-mysql.sakila.rental.dataset',        'inventory_id', 'inventory_id',         'DIRECT'),
    ('sakila-mysql.sakila.customer.dataset',  'sakila-mysql.sakila.rental.dataset',        'customer_id',  'customer_id',          'DIRECT'),
    ('sakila-mysql.sakila.staff.dataset',     'sakila-mysql.sakila.rental.dataset',        'staff_id',     'staff_id',             'DIRECT'),
    ('sakila-mysql.sakila.rental.dataset',    'sakila-mysql.sakila.payment.dataset',       'rental_id',    'rental_id',            'DIRECT'),
    ('sakila-mysql.sakila.customer.dataset',  'sakila-mysql.sakila.payment.dataset',       'customer_id',  'customer_id',          'DIRECT'),
    ('sakila-mysql.sakila.staff.dataset',     'sakila-mysql.sakila.payment.dataset',       'staff_id',     'staff_id',             'DIRECT')
) AS m(src_urn, tgt_urn, source_column, target_column, transform_type)
JOIN catalog_datasets s ON s.urn = m.src_urn
JOIN catalog_datasets t ON t.urn = m.tgt_urn
JOIN argus_dataset_lineage l
  ON l.source_dataset_id = s.id AND l.target_dataset_id = t.id AND l.lineage_source = 'MANUAL'
ON CONFLICT (dataset_lineage_id, source_column, target_column) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 7. 데이터셋 기본 사전 — 표준 용어 탭의 사전 선택 기억
--    (catalog_dataset_properties 의 argus.standard_dictionary_id 키)
--    사용자가 UI 에서 바꾼 선택은 보존: ON CONFLICT DO NOTHING
-- ---------------------------------------------------------------------------

INSERT INTO catalog_dataset_properties (dataset_id, property_key, property_value)
SELECT d.id, 'argus.standard_dictionary_id', dict.id::text
FROM catalog_datasets d
CROSS JOIN (SELECT id FROM catalog_standard_dictionary WHERE dict_name = '영화대여 표준 사전') dict
WHERE d.urn LIKE 'sakila-mysql.sakila.%'
ON CONFLICT (dataset_id, property_key) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 8. 조직(3-depth) + 시스템 + 데이터 소스 배정
--    데이터다이나믹스 > 미디어사업본부 > 콘텐츠유통팀 > [DVD 대여 시스템] > sakila-mysql
--    code 가 UNIQUE 라 멱등 키로 사용 (루트 조직은 parent_id NULL 이라
--    (parent_id, name) unique 가 중복을 막지 못하므로 ON CONFLICT (code) 필수)
-- ---------------------------------------------------------------------------

INSERT INTO catalog_organizations (code, name, parent_id, description, sort_order)
VALUES ('data-dynamics', '데이터다이나믹스', NULL, '가상의 데모 회사 — 전사 루트 조직', 0)
ON CONFLICT (code) DO NOTHING;

INSERT INTO catalog_organizations (code, name, parent_id, description, sort_order)
SELECT 'media-biz-division', '미디어사업본부', p.id, '미디어·엔터테인먼트 사업 부문', 0
FROM catalog_organizations p WHERE p.code = 'data-dynamics'
ON CONFLICT (code) DO NOTHING;

INSERT INTO catalog_organizations (code, name, parent_id, description, sort_order)
SELECT 'content-dist-team', '콘텐츠유통팀', p.id, 'DVD/디지털 콘텐츠 유통 운영 조직', 0
FROM catalog_organizations p WHERE p.code = 'media-biz-division'
ON CONFLICT (code) DO NOTHING;

INSERT INTO catalog_systems (code, name, org_id, summary, description, owner, status, sort_order)
SELECT 'dvd-rental', 'DVD 대여 시스템', o.id,
       'Sakila 기반 DVD 대여점 운영 시스템',
       '점포·재고·회원·대여·결제를 관리하는 OLTP 시스템. 운영 DB 는 MySQL(Sakila 스키마).',
       'admin', 'ACTIVE', 0
FROM catalog_organizations o WHERE o.code = 'content-dist-team'
ON CONFLICT (code) DO NOTHING;

-- sakila 데이터 소스를 시스템에 배정 (재실행 시 동일 값 갱신)
UPDATE catalog_datasources ds
SET system_id = sys.id
FROM catalog_systems sys
WHERE sys.code = 'dvd-rental' AND ds.datasource_id = 'sakila-mysql';


-- ---------------------------------------------------------------------------
-- 9. 분류체계 (Taxonomy) — 업무 도메인(트리) + 데이터 유형(평면)
--    루트 카테고리는 parent_id NULL 이라 (taxonomy, parent, name) unique 가
--    중복을 못 막으므로 WHERE NOT EXISTS 로 멱등 처리.
-- ---------------------------------------------------------------------------

INSERT INTO catalog_taxonomies (code, name, description, sort_order)
VALUES
    ('business-domain', '업무 도메인', '비즈니스 기능 관점의 데이터 분류 (고객/상품/운영/거래)', 0),
    ('data-type',       '데이터 유형', '데이터 모델링 역할 관점의 분류 (마스터/트랜잭션/매핑/파생)', 1)
ON CONFLICT (name) DO NOTHING;

-- 9.1 업무 도메인 — 루트 카테고리
INSERT INTO catalog_categories (taxonomy_id, parent_id, code, name, description, sort_order)
SELECT t.id, NULL, c.code, c.name, c.description, c.sort_order
FROM (VALUES
    ('domain-customer', '고객', '회원과 고객 접점(주소·지역) 데이터', 0),
    ('domain-product',  '상품', '대여 상품(영화)과 그 부속 분류·출연 정보', 1),
    ('domain-ops',      '운영', '재고·점포·인사 등 운영 기반 데이터', 2),
    ('domain-trade',    '거래', '대여·결제 트랜잭션', 3)
) AS c(code, name, description, sort_order)
JOIN catalog_taxonomies t ON t.name = '업무 도메인'
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_categories x
    WHERE x.taxonomy_id = t.id AND x.parent_id IS NULL AND x.name = c.name
);

-- 9.1 업무 도메인 — 하위 카테고리
INSERT INTO catalog_categories (taxonomy_id, parent_id, code, name, description, sort_order)
SELECT t.id, p.id, c.code, c.name, c.description, c.sort_order
FROM (VALUES
    ('고객', 'domain-customer-member',  '회원 관리',     '대여 서비스 회원 정보', 0),
    ('고객', 'domain-customer-contact', '주소·지역',     '고객/점포 공용 주소 체계 (주소→도시→국가)', 1),
    ('상품', 'domain-product-catalog',  '영화 카탈로그', '영화 마스터와 전문 검색 파생, 언어', 0),
    ('상품', 'domain-product-meta',     '장르·출연',     '장르 분류와 배우 출연 정보', 1),
    ('운영', 'domain-ops-inventory',    '재고 관리',     '점포별 실물 DVD 재고', 0),
    ('운영', 'domain-ops-store',        '점포·인사',     '영업점과 직원', 1),
    ('거래', 'domain-trade-rental',     '대여',          '대여~반납 트랜잭션', 0),
    ('거래', 'domain-trade-payment',    '결제',          '요금 결제·매출', 1)
) AS c(parent_name, code, name, description, sort_order)
JOIN catalog_taxonomies t ON t.name = '업무 도메인'
JOIN catalog_categories p ON p.taxonomy_id = t.id AND p.parent_id IS NULL AND p.name = c.parent_name
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_categories x
    WHERE x.taxonomy_id = t.id AND x.parent_id = p.id AND x.name = c.name
);

-- 9.2 데이터 유형 — 평면 카테고리
INSERT INTO catalog_categories (taxonomy_id, parent_id, code, name, description, sort_order)
SELECT t.id, NULL, c.code, c.name, c.description, c.sort_order
FROM (VALUES
    ('type-master',      '마스터',     '기준 정보 (참조의 대상이 되는 원천 엔티티)', 0),
    ('type-transaction', '트랜잭션',   '업무 행위가 발생할 때마다 쌓이는 이력성 데이터', 1),
    ('type-mapping',     '매핑',       '엔티티 간 다대다 연결 전용 브리지 테이블', 2),
    ('type-derived',     '파생',       '원천에서 자동 생성·동기화되는 2차 데이터', 3)
) AS c(code, name, description, sort_order)
JOIN catalog_taxonomies t ON t.name = '데이터 유형'
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_categories x
    WHERE x.taxonomy_id = t.id AND x.parent_id IS NULL AND x.name = c.name
);

-- 9.3 데이터셋 ↔ 카테고리 매핑 (업무 도메인 하위분류 + 데이터 유형, 데이터셋당 2건)
INSERT INTO catalog_dataset_categories (dataset_id, category_id)
SELECT d.id, cat.id
FROM (VALUES
    -- 업무 도메인 매핑 (taxonomy, category, dataset urn)
    ('업무 도메인', '회원 관리',     'sakila-mysql.sakila.customer.dataset'),
    ('업무 도메인', '주소·지역',     'sakila-mysql.sakila.address.dataset'),
    ('업무 도메인', '주소·지역',     'sakila-mysql.sakila.city.dataset'),
    ('업무 도메인', '주소·지역',     'sakila-mysql.sakila.country.dataset'),
    ('업무 도메인', '영화 카탈로그', 'sakila-mysql.sakila.film.dataset'),
    ('업무 도메인', '영화 카탈로그', 'sakila-mysql.sakila.film_text.dataset'),
    ('업무 도메인', '영화 카탈로그', 'sakila-mysql.sakila.language.dataset'),
    ('업무 도메인', '장르·출연',     'sakila-mysql.sakila.category.dataset'),
    ('업무 도메인', '장르·출연',     'sakila-mysql.sakila.film_category.dataset'),
    ('업무 도메인', '장르·출연',     'sakila-mysql.sakila.actor.dataset'),
    ('업무 도메인', '장르·출연',     'sakila-mysql.sakila.film_actor.dataset'),
    ('업무 도메인', '재고 관리',     'sakila-mysql.sakila.inventory.dataset'),
    ('업무 도메인', '점포·인사',     'sakila-mysql.sakila.store.dataset'),
    ('업무 도메인', '점포·인사',     'sakila-mysql.sakila.staff.dataset'),
    ('업무 도메인', '대여',          'sakila-mysql.sakila.rental.dataset'),
    ('업무 도메인', '결제',          'sakila-mysql.sakila.payment.dataset'),
    -- 데이터 유형 매핑
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.film.dataset'),
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.actor.dataset'),
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.customer.dataset'),
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.store.dataset'),
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.staff.dataset'),
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.address.dataset'),
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.city.dataset'),
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.country.dataset'),
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.category.dataset'),
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.language.dataset'),
    ('데이터 유형', '마스터',     'sakila-mysql.sakila.inventory.dataset'),
    ('데이터 유형', '트랜잭션',   'sakila-mysql.sakila.rental.dataset'),
    ('데이터 유형', '트랜잭션',   'sakila-mysql.sakila.payment.dataset'),
    ('데이터 유형', '매핑',       'sakila-mysql.sakila.film_actor.dataset'),
    ('데이터 유형', '매핑',       'sakila-mysql.sakila.film_category.dataset'),
    ('데이터 유형', '파생',       'sakila-mysql.sakila.film_text.dataset')
) AS m(taxonomy_name, category_name, urn)
JOIN catalog_taxonomies t ON t.name = m.taxonomy_name
JOIN catalog_categories cat ON cat.taxonomy_id = t.id AND cat.name = m.category_name
JOIN catalog_datasets d ON d.urn = m.urn
ON CONFLICT (dataset_id, category_id) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 10. 품질 규칙 — 데이터셋별 2~5건 (NOT_NULL/UNIQUE/MIN/ACCEPTED_VALUES/REGEX/ROW_COUNT)
--     (dataset_id, rule_name) 기준 NOT EXISTS 로 멱등 처리
-- ---------------------------------------------------------------------------

INSERT INTO catalog_quality_rule
    (dataset_id, rule_name, check_type, column_name, expected_value, threshold, severity, is_active)
SELECT d.id, r.rule_name, r.check_type, r.column_name, r.expected_value, r.threshold, r.severity, 'true'
FROM (VALUES
    -- film (5)
    ('sakila-mysql.sakila.film.dataset',          'film_id 필수',            'NOT_NULL',        'film_id',      NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.film.dataset',          'film_id 유일',            'UNIQUE',          'film_id',      NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.film.dataset',          '대여료 0 이상',           'MIN_VALUE',       'rental_rate',  '0',                    100.0, 'WARNING'),
    ('sakila-mysql.sakila.film.dataset',          '관람등급 허용값',         'ACCEPTED_VALUES', 'rating',       'G,PG,PG-13,R,NC-17',   100.0, 'WARNING'),
    ('sakila-mysql.sakila.film.dataset',          '영화 1000편 이상',        'ROW_COUNT',       NULL,           '1000',                 100.0, 'INFO'),
    -- actor (3)
    ('sakila-mysql.sakila.actor.dataset',         'actor_id 필수',           'NOT_NULL',        'actor_id',     NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.actor.dataset',         'actor_id 유일',           'UNIQUE',          'actor_id',     NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.actor.dataset',         '배우 200명 이상',         'ROW_COUNT',       NULL,           '200',                  100.0, 'INFO'),
    -- customer (4)
    ('sakila-mysql.sakila.customer.dataset',      'customer_id 유일',        'UNIQUE',          'customer_id',  NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.customer.dataset',      '이메일 형식',             'REGEX',           'email',        '^[^@]+@[^@]+$',        100.0, 'WARNING'),
    ('sakila-mysql.sakila.customer.dataset',      '소속 점포 필수',          'NOT_NULL',        'store_id',     NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.customer.dataset',      '회원 500명 이상',         'ROW_COUNT',       NULL,           '500',                  100.0, 'INFO'),
    -- rental (4)
    ('sakila-mysql.sakila.rental.dataset',        'rental_id 유일',          'UNIQUE',          'rental_id',    NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.rental.dataset',        '대여 재고 필수',          'NOT_NULL',        'inventory_id', NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.rental.dataset',        '대여 고객 필수',          'NOT_NULL',        'customer_id',  NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.rental.dataset',        '대여 16,000건 이상',      'ROW_COUNT',       NULL,           '16000',                100.0, 'INFO'),
    ('sakila-mysql.sakila.rental.dataset',        '고아 대여 검출 (재고 참조 끊김)', 'CUSTOM_SQL', NULL,
     'SELECT count(*) FROM rental r LEFT JOIN inventory i ON i.inventory_id = r.inventory_id WHERE i.inventory_id IS NULL',
                                                                                                        100.0, 'BREAKING'),
    ('sakila-mysql.sakila.rental.dataset',        '반납일 시간 역전 검출',   'CUSTOM_SQL',      NULL,
     'SELECT count(*) FROM rental WHERE return_date IS NOT NULL AND return_date < rental_date',
                                                                                                        100.0, 'WARNING'),
    ('sakila-mysql.sakila.rental.dataset',        '대여 기간 이상치 검사 (IQR)', 'CUSTOM_PYTHON', NULL,
     '{"module": "rental_checks", "fn": "rental_duration_outlier", "params": {"max_outlier_pct": 1.0}}',
                                                                                                        100.0, 'INFO'),
    -- payment (4)
    ('sakila-mysql.sakila.payment.dataset',       'payment_id 유일',         'UNIQUE',          'payment_id',   NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.payment.dataset',       '결제 금액 0 이상',        'MIN_VALUE',       'amount',       '0',                    100.0, 'BREAKING'),
    ('sakila-mysql.sakila.payment.dataset',       '결제 고객 필수',          'NOT_NULL',        'customer_id',  NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.payment.dataset',       '결제 16,000건 이상',      'ROW_COUNT',       NULL,           '16000',                100.0, 'INFO'),
    -- inventory (3)
    ('sakila-mysql.sakila.inventory.dataset',     'inventory_id 유일',       'UNIQUE',          'inventory_id', NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.inventory.dataset',     '보유 영화 필수',          'NOT_NULL',        'film_id',      NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.inventory.dataset',     '보유 점포 필수',          'NOT_NULL',        'store_id',     NULL,                   100.0, 'BREAKING'),
    -- store (2)
    ('sakila-mysql.sakila.store.dataset',         'store_id 유일',           'UNIQUE',          'store_id',     NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.store.dataset',         '점포 2개 이상',           'ROW_COUNT',       NULL,           '2',                    100.0, 'INFO'),
    -- staff (3)
    ('sakila-mysql.sakila.staff.dataset',         'staff_id 유일',           'UNIQUE',          'staff_id',     NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.staff.dataset',         '로그인 계정 필수',        'NOT_NULL',        'username',     NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.staff.dataset',         '직원 비밀번호 해시 필수', 'NOT_NULL',        'password',     NULL,                   100.0, 'WARNING'),
    ('sakila-mysql.sakila.staff.dataset',         '이메일 형식',             'REGEX',           'email',        '^[^@]+@[^@]+$',        100.0, 'WARNING'),
    -- address (3)
    ('sakila-mysql.sakila.address.dataset',       'address_id 유일',         'UNIQUE',          'address_id',   NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.address.dataset',       '도시 참조 필수',          'NOT_NULL',        'city_id',      NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.address.dataset',       '전화번호 입력률 90%',     'NOT_NULL',        'phone',        NULL,                    90.0, 'INFO'),
    ('sakila-mysql.sakila.address.dataset',       '상세 주소(address2) 입력 필수', 'NOT_NULL',  'address2',     NULL,                   100.0, 'INFO'),
    -- city (2)
    ('sakila-mysql.sakila.city.dataset',          'city_id 유일',            'UNIQUE',          'city_id',      NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.city.dataset',          '국가 참조 필수',          'NOT_NULL',        'country_id',   NULL,                   100.0, 'BREAKING'),
    -- country (2)
    ('sakila-mysql.sakila.country.dataset',       'country_id 유일',         'UNIQUE',          'country_id',   NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.country.dataset',       '국가명 필수',             'NOT_NULL',        'country',      NULL,                   100.0, 'BREAKING'),
    -- category (3)
    ('sakila-mysql.sakila.category.dataset',      'category_id 유일',        'UNIQUE',          'category_id',  NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.category.dataset',      '장르명 필수',             'NOT_NULL',        'name',         NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.category.dataset',      '장르명 유일',             'UNIQUE',          'name',         NULL,                   100.0, 'WARNING'),
    -- language (2)
    ('sakila-mysql.sakila.language.dataset',      'language_id 유일',        'UNIQUE',          'language_id',  NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.language.dataset',      '언어명 필수',             'NOT_NULL',        'name',         NULL,                   100.0, 'BREAKING'),
    -- film_actor (2)
    ('sakila-mysql.sakila.film_actor.dataset',    '배우 참조 필수',          'NOT_NULL',        'actor_id',     NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.film_actor.dataset',    '영화 참조 필수',          'NOT_NULL',        'film_id',      NULL,                   100.0, 'BREAKING'),
    -- film_category (2)
    ('sakila-mysql.sakila.film_category.dataset', '영화 참조 필수',          'NOT_NULL',        'film_id',      NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.film_category.dataset', '장르 참조 필수',          'NOT_NULL',        'category_id',  NULL,                   100.0, 'BREAKING'),
    -- film_text (2)
    ('sakila-mysql.sakila.film_text.dataset',     'film_id 유일',            'UNIQUE',          'film_id',      NULL,                   100.0, 'BREAKING'),
    ('sakila-mysql.sakila.film_text.dataset',     '제목 필수',               'NOT_NULL',        'title',        NULL,                   100.0, 'WARNING')
) AS r(urn, rule_name, check_type, column_name, expected_value, threshold, severity)
JOIN catalog_datasets d ON d.urn = r.urn
WHERE NOT EXISTS (
    SELECT 1 FROM catalog_quality_rule q
    WHERE q.dataset_id = d.id AND q.rule_name = r.rule_name
);

-- ---------------------------------------------------------------------------
-- 11. 스키마 변경 이력 (SchemaSnapshot) — 핵심 테이블에 진화 스토리 시드
--     change_summary 를 데이터셋별 멱등 키로 사용 (서버 포맷: "추가 N개, 변경 N개, ...")
-- ---------------------------------------------------------------------------

-- 11.1 최초 동기화 스냅숏 — 16개 전체 (30일 전, 현재 스키마 기준)
INSERT INTO catalog_schema_snapshots (dataset_id, synced_at, schema_json, field_count, change_summary, changes_json)
SELECT d.id, now() - INTERVAL '30 days',
       (SELECT coalesce(json_agg(json_build_object(
                'field_path', s.field_path, 'field_type', s.field_type,
                'native_type', coalesce(s.native_type, ''), 'nullable', coalesce(s.nullable, 'true'),
                'is_primary_key', coalesce(s.is_primary_key, 'false'),
                'is_unique', coalesce(s.is_unique, 'false'),
                'is_indexed', coalesce(s.is_indexed, 'false'), 'ordinal', s.ordinal
              ) ORDER BY s.ordinal), '[]'::json)::text
        FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id),
       (SELECT count(*) FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id),
       '최초 동기화', '[]'
FROM catalog_datasets d
WHERE d.urn LIKE 'sakila-mysql.sakila.%'
  AND NOT EXISTS (
      SELECT 1 FROM catalog_schema_snapshots x
      WHERE x.dataset_id = d.id AND x.change_summary = '최초 동기화'
  );

-- 11.2 film: 대여료 정밀도 변경 (14일 전, DECIMAL(3,1) → DECIMAL(4,2))
INSERT INTO catalog_schema_snapshots (dataset_id, synced_at, schema_json, field_count, change_summary, changes_json)
SELECT d.id, now() - INTERVAL '14 days',
       (SELECT json_agg(json_build_object(
                'field_path', s.field_path, 'field_type', s.field_type,
                'native_type', coalesce(s.native_type, ''), 'nullable', coalesce(s.nullable, 'true'),
                'is_primary_key', coalesce(s.is_primary_key, 'false'),
                'is_unique', coalesce(s.is_unique, 'false'),
                'is_indexed', coalesce(s.is_indexed, 'false'), 'ordinal', s.ordinal
              ) ORDER BY s.ordinal)::text
        FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id),
       (SELECT count(*) FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id),
       '변경 1개',
       '[{"type": "MODIFY", "field": "rental_rate",
          "before": {"field_type": "NUMBER", "native_type": "DECIMAL(3,1)"},
          "after":  {"field_type": "NUMBER", "native_type": "DECIMAL(4,2)"}}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.film.dataset'
  AND NOT EXISTS (
      SELECT 1 FROM catalog_schema_snapshots x
      WHERE x.dataset_id = d.id AND x.change_summary = '변경 1개'
  );

-- 11.3 film: 부가기능 컬럼 추가 (3일 전, special_features)
INSERT INTO catalog_schema_snapshots (dataset_id, synced_at, schema_json, field_count, change_summary, changes_json)
SELECT d.id, now() - INTERVAL '3 days',
       (SELECT json_agg(json_build_object(
                'field_path', s.field_path, 'field_type', s.field_type,
                'native_type', coalesce(s.native_type, ''), 'nullable', coalesce(s.nullable, 'true'),
                'is_primary_key', coalesce(s.is_primary_key, 'false'),
                'is_unique', coalesce(s.is_unique, 'false'),
                'is_indexed', coalesce(s.is_indexed, 'false'), 'ordinal', s.ordinal
              ) ORDER BY s.ordinal)::text
        FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id),
       (SELECT count(*) FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id),
       '추가 1개',
       '[{"type": "ADD", "field": "special_features",
          "after": {"field_type": "ARRAY", "native_type": "SET",
                    "nullable": "true", "ordinal": 11}}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.film.dataset'
  AND NOT EXISTS (
      SELECT 1 FROM catalog_schema_snapshots x
      WHERE x.dataset_id = d.id AND x.change_summary = '추가 1개'
  );

-- 11.4 customer: 이메일 길이 확장 (7일 전, VARCHAR(50) → VARCHAR(100) 가정의 데모 이력)
INSERT INTO catalog_schema_snapshots (dataset_id, synced_at, schema_json, field_count, change_summary, changes_json)
SELECT d.id, now() - INTERVAL '7 days',
       (SELECT json_agg(json_build_object(
                'field_path', s.field_path, 'field_type', s.field_type,
                'native_type', coalesce(s.native_type, ''), 'nullable', coalesce(s.nullable, 'true'),
                'is_primary_key', coalesce(s.is_primary_key, 'false'),
                'is_unique', coalesce(s.is_unique, 'false'),
                'is_indexed', coalesce(s.is_indexed, 'false'), 'ordinal', s.ordinal
              ) ORDER BY s.ordinal)::text
        FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id),
       (SELECT count(*) FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id),
       '변경 1개',
       '[{"type": "MODIFY", "field": "email",
          "before": {"field_type": "STRING", "native_type": "VARCHAR(50)"},
          "after":  {"field_type": "STRING", "native_type": "VARCHAR(100)"}}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.customer.dataset'
  AND NOT EXISTS (
      SELECT 1 FROM catalog_schema_snapshots x
      WHERE x.dataset_id = d.id AND x.change_summary = '변경 1개'
  );

-- 11.5 rental: 반납일자 NULL 허용 변경 (10일 전)
INSERT INTO catalog_schema_snapshots (dataset_id, synced_at, schema_json, field_count, change_summary, changes_json)
SELECT d.id, now() - INTERVAL '10 days',
       (SELECT json_agg(json_build_object(
                'field_path', s.field_path, 'field_type', s.field_type,
                'native_type', coalesce(s.native_type, ''), 'nullable', coalesce(s.nullable, 'true'),
                'is_primary_key', coalesce(s.is_primary_key, 'false'),
                'is_unique', coalesce(s.is_unique, 'false'),
                'is_indexed', coalesce(s.is_indexed, 'false'), 'ordinal', s.ordinal
              ) ORDER BY s.ordinal)::text
        FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id),
       (SELECT count(*) FROM catalog_dataset_schemas s WHERE s.dataset_id = d.id),
       '변경 1개',
       '[{"type": "MODIFY", "field": "return_date",
          "before": {"field_type": "DATE", "native_type": "DATETIME", "nullable": "false"},
          "after":  {"field_type": "DATE", "native_type": "DATETIME", "nullable": "true"}}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.rental.dataset'
  AND NOT EXISTS (
      SELECT 1 FROM catalog_schema_snapshots x
      WHERE x.dataset_id = d.id AND x.change_summary = '변경 1개'
  );

-- ---------------------------------------------------------------------------
-- 12. 수집된 품질 정보 스냅숏 (quality/python-quality.py 실측 결과를 시드화)
--     데모 초기 상태에서 품질 탭에 점수·결과·프로파일이 바로 보이도록 한다.
--     실데이터(MariaDB Sakila) 기준 실측값이며, 이미 수집 이력이 있는 DB 에는
--     덮어쓰지 않는다 (데이터셋/규칙별 NOT EXISTS).
-- ---------------------------------------------------------------------------

-- 12.1 데이터셋 row_count — 실측값으로 갱신 (재실행 시 동일 값)
UPDATE catalog_datasets SET row_count = 200 WHERE urn = 'sakila-mysql.sakila.actor.dataset';
UPDATE catalog_datasets SET row_count = 603 WHERE urn = 'sakila-mysql.sakila.address.dataset';
UPDATE catalog_datasets SET row_count = 16 WHERE urn = 'sakila-mysql.sakila.category.dataset';
UPDATE catalog_datasets SET row_count = 600 WHERE urn = 'sakila-mysql.sakila.city.dataset';
UPDATE catalog_datasets SET row_count = 109 WHERE urn = 'sakila-mysql.sakila.country.dataset';
UPDATE catalog_datasets SET row_count = 599 WHERE urn = 'sakila-mysql.sakila.customer.dataset';
UPDATE catalog_datasets SET row_count = 1000 WHERE urn = 'sakila-mysql.sakila.film.dataset';
UPDATE catalog_datasets SET row_count = 5462 WHERE urn = 'sakila-mysql.sakila.film_actor.dataset';
UPDATE catalog_datasets SET row_count = 1000 WHERE urn = 'sakila-mysql.sakila.film_category.dataset';
UPDATE catalog_datasets SET row_count = 1000 WHERE urn = 'sakila-mysql.sakila.film_text.dataset';
UPDATE catalog_datasets SET row_count = 4581 WHERE urn = 'sakila-mysql.sakila.inventory.dataset';
UPDATE catalog_datasets SET row_count = 6 WHERE urn = 'sakila-mysql.sakila.language.dataset';
UPDATE catalog_datasets SET row_count = 16044 WHERE urn = 'sakila-mysql.sakila.payment.dataset';
UPDATE catalog_datasets SET row_count = 16044 WHERE urn = 'sakila-mysql.sakila.rental.dataset';
UPDATE catalog_datasets SET row_count = 2 WHERE urn = 'sakila-mysql.sakila.staff.dataset';
UPDATE catalog_datasets SET row_count = 2 WHERE urn = 'sakila-mysql.sakila.store.dataset';

-- 12.2 컬럼 프로파일 (데이터셋별 최신 1건, 전체 컬럼 통계 JSON)
INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 1000, '[{"column_name": "film_id", "column_type": "NUMBER", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1000, "unique_percent": 100.0, "min_value": "1", "max_value": "1000", "mean_value": 500.5, "top_values": null}, {"column_name": "title", "column_type": "STRING", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1000, "unique_percent": 100.0, "min_value": "ACADEMY DINOSAUR", "max_value": "ZORRO ARK", "mean_value": null, "top_values": null}, {"column_name": "description", "column_type": "STRING", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1000, "unique_percent": 100.0, "min_value": "A Action-Packed Character Study of a Astronaut And a Explorer who must Reach a Monkey in A MySQL Convention", "max_value": "A Unbelieveable Yarn of a Student And a Database Administrator who must Outgun a Husband in An Abandoned Mine Shaft", "mean_value": null, "top_values": null}, {"column_name": "release_year", "column_type": "NUMBER", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 0.1, "min_value": "2006", "max_value": "2006", "mean_value": 2006.0, "top_values": [{"value": "2006", "count": 1000}]}, {"column_name": "language_id", "column_type": "NUMBER", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 0.1, "min_value": "1", "max_value": "1", "mean_value": 1.0, "top_values": [{"value": "1", "count": 1000}]}, {"column_name": "original_language_id", "column_type": "NUMBER", "total_count": 1000, "null_count": 1000, "null_percent": 100.0, "unique_count": 0, "unique_percent": 0.0, "min_value": null, "max_value": null, "mean_value": null, "top_values": null}, {"column_name": "rental_duration", "column_type": "NUMBER", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 5, "unique_percent": 0.5, "min_value": "3", "max_value": "7", "mean_value": 4.985, "top_values": [{"value": "6", "count": 212}, {"value": "3", "count": 203}, {"value": "4", "count": 203}, {"value": "7", "count": 191}, {"value": "5", "count": 191}]}, {"column_name": "rental_rate", "column_type": "NUMBER", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 3, "unique_percent": 0.3, "min_value": "0.99", "max_value": "4.99", "mean_value": 2.98, "top_values": [{"value": "0.99", "count": 341}, {"value": "4.99", "count": 336}, {"value": "2.99", "count": 323}]}, {"column_name": "length", "column_type": "NUMBER", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 140, "unique_percent": 14.0, "min_value": "46", "max_value": "185", "mean_value": 115.272, "top_values": null}, {"column_name": "replacement_cost", "column_type": "NUMBER", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 21, "unique_percent": 2.1, "min_value": "9.99", "max_value": "29.99", "mean_value": 19.984, "top_values": null}, {"column_name": "rating", "column_type": "ENUM", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 5, "unique_percent": 0.5, "min_value": "G", "max_value": "R", "mean_value": null, "top_values": [{"value": "PG-13", "count": 223}, {"value": "NC-17", "count": 210}, {"value": "R", "count": 195}, {"value": "PG", "count": 194}, {"value": "G", "count": 178}]}, {"column_name": "special_features", "column_type": "ARRAY", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 15, "unique_percent": 1.5, "min_value": "Behind the Scenes", "max_value": "Trailers,Deleted Scenes,Behind the Scenes", "mean_value": null, "top_values": [{"value": "Trailers,Commentaries,Behind the Scenes", "count": 79}, {"value": "Trailers", "count": 72}, {"value": "Trailers,Commentaries", "count": 72}, {"value": "Trailers,Behind the Scenes", "count": 72}, {"value": "Deleted Scenes,Behind the Scenes", "count": 71}]}, {"column_name": "last_update", "column_type": "DATE", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 0.1, "min_value": "2006-02-15 05:03:42", "max_value": "2006-02-15 05:03:42", "mean_value": null, "top_values": [{"value": "2006-02-15 05:03:42", "count": 1000}]}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.film.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 200, '[{"column_name": "actor_id", "column_type": "INT64", "total_count": 200, "null_count": 0, "null_percent": 0.0, "unique_count": 200, "unique_percent": 100.0, "min_value": "1", "max_value": "200", "mean_value": 100.5, "top_values": null}, {"column_name": "first_name", "column_type": "STR", "total_count": 200, "null_count": 0, "null_percent": 0.0, "unique_count": 128, "unique_percent": 64.0, "min_value": "ADAM", "max_value": "ZERO", "mean_value": null, "top_values": null}, {"column_name": "last_name", "column_type": "STR", "total_count": 200, "null_count": 0, "null_percent": 0.0, "unique_count": 121, "unique_percent": 60.5, "min_value": "AKROYD", "max_value": "ZELLWEGER", "mean_value": null, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 200, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 0.5, "min_value": "2006-02-15 04:34:33", "max_value": "2006-02-15 04:34:33", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.actor.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 599, '[{"column_name": "customer_id", "column_type": "INT64", "total_count": 599, "null_count": 0, "null_percent": 0.0, "unique_count": 599, "unique_percent": 100.0, "min_value": "1", "max_value": "599", "mean_value": 300.0, "top_values": null}, {"column_name": "store_id", "column_type": "INT64", "total_count": 599, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 0.33, "min_value": "1", "max_value": "2", "mean_value": 1.4557595993322203, "top_values": null}, {"column_name": "first_name", "column_type": "STR", "total_count": 599, "null_count": 0, "null_percent": 0.0, "unique_count": 591, "unique_percent": 98.66, "min_value": "AARON", "max_value": "ZACHARY", "mean_value": null, "top_values": null}, {"column_name": "last_name", "column_type": "STR", "total_count": 599, "null_count": 0, "null_percent": 0.0, "unique_count": 599, "unique_percent": 100.0, "min_value": "ABNEY", "max_value": "YOUNG", "mean_value": null, "top_values": null}, {"column_name": "email", "column_type": "STR", "total_count": 599, "null_count": 0, "null_percent": 0.0, "unique_count": 599, "unique_percent": 100.0, "min_value": "AARON.SELBY@sakilacustomer.org", "max_value": "ZACHARY.HITE@sakilacustomer.org", "mean_value": null, "top_values": null}, {"column_name": "address_id", "column_type": "INT64", "total_count": 599, "null_count": 0, "null_percent": 0.0, "unique_count": 599, "unique_percent": 100.0, "min_value": "5", "max_value": "605", "mean_value": 304.7245409015025, "top_values": null}, {"column_name": "active", "column_type": "INT64", "total_count": 599, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 0.33, "min_value": "0", "max_value": "1", "mean_value": 0.9749582637729549, "top_values": null}, {"column_name": "create_date", "column_type": "DATETIME64[US]", "total_count": 599, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 0.33, "min_value": "2006-02-14 22:04:36", "max_value": "2006-02-14 22:04:37", "mean_value": null, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 599, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 0.17, "min_value": "2006-02-15 04:57:20", "max_value": "2006-02-15 04:57:20", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.customer.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 16044, '[{"column_name": "rental_id", "column_type": "NUMBER", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 16044, "unique_percent": 100.0, "min_value": "1", "max_value": "16049", "mean_value": 8025.3715, "top_values": null}, {"column_name": "rental_date", "column_type": "DATE", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 15815, "unique_percent": 98.57, "min_value": "2005-05-24 22:53:30", "max_value": "2006-02-14 15:16:03", "mean_value": null, "top_values": null}, {"column_name": "inventory_id", "column_type": "NUMBER", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 4580, "unique_percent": 28.55, "min_value": "1", "max_value": "4581", "mean_value": 2291.8426, "top_values": null}, {"column_name": "customer_id", "column_type": "NUMBER", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 599, "unique_percent": 3.73, "min_value": "1", "max_value": "599", "mean_value": 297.1432, "top_values": null}, {"column_name": "return_date", "column_type": "DATE", "total_count": 16044, "null_count": 183, "null_percent": 1.14, "unique_count": 15836, "unique_percent": 98.7, "min_value": "2005-05-25 23:55:21", "max_value": "2005-09-02 02:35:22", "mean_value": null, "top_values": null}, {"column_name": "staff_id", "column_type": "NUMBER", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 0.01, "min_value": "1", "max_value": "2", "mean_value": 1.4989, "top_values": null}, {"column_name": "last_update", "column_type": "DATE", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 0.01, "min_value": "2006-02-15 21:30:53", "max_value": "2006-02-23 04:12:08", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.rental.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 16044, '[{"column_name": "payment_id", "column_type": "INT64", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 16044, "unique_percent": 100.0, "min_value": "1", "max_value": "16049", "mean_value": 8024.48373223635, "top_values": null}, {"column_name": "customer_id", "column_type": "INT64", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 599, "unique_percent": 3.73, "min_value": "1", "max_value": "599", "mean_value": 297.14316878583895, "top_values": null}, {"column_name": "staff_id", "column_type": "INT64", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 0.01, "min_value": "1", "max_value": "2", "mean_value": 1.4980054849164797, "top_values": null}, {"column_name": "rental_id", "column_type": "INT64", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 16044, "unique_percent": 100.0, "min_value": "1", "max_value": "16049", "mean_value": 8025.371478434306, "top_values": null}, {"column_name": "amount", "column_type": "FLOAT64", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 19, "unique_percent": 0.12, "min_value": "0.0", "max_value": "11.99", "mean_value": 4.2013562702567935, "top_values": null}, {"column_name": "payment_date", "column_type": "DATETIME64[US]", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 15815, "unique_percent": 98.57, "min_value": "2005-05-24 22:53:30", "max_value": "2006-02-14 15:16:03", "mean_value": null, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 16044, "null_count": 0, "null_percent": 0.0, "unique_count": 704, "unique_percent": 4.39, "min_value": "2006-02-15 22:12:30", "max_value": "2006-02-15 22:24:13", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.payment.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 4581, '[{"column_name": "inventory_id", "column_type": "INT64", "total_count": 4581, "null_count": 0, "null_percent": 0.0, "unique_count": 4581, "unique_percent": 100.0, "min_value": "1", "max_value": "4581", "mean_value": 2291.0, "top_values": null}, {"column_name": "film_id", "column_type": "INT64", "total_count": 4581, "null_count": 0, "null_percent": 0.0, "unique_count": 958, "unique_percent": 20.91, "min_value": "1", "max_value": "1000", "mean_value": 500.9362584588518, "top_values": null}, {"column_name": "store_id", "column_type": "INT64", "total_count": 4581, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 0.04, "min_value": "1", "max_value": "2", "mean_value": 1.5044750054573237, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 4581, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 0.02, "min_value": "2006-02-15 05:09:17", "max_value": "2006-02-15 05:09:17", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.inventory.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 2, '[{"column_name": "store_id", "column_type": "INT64", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 100.0, "min_value": "1", "max_value": "2", "mean_value": 1.5, "top_values": null}, {"column_name": "manager_staff_id", "column_type": "INT64", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 100.0, "min_value": "1", "max_value": "2", "mean_value": 1.5, "top_values": null}, {"column_name": "address_id", "column_type": "INT64", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 100.0, "min_value": "1", "max_value": "2", "mean_value": 1.5, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 50.0, "min_value": "2006-02-15 04:57:12", "max_value": "2006-02-15 04:57:12", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.store.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 2, '[{"column_name": "staff_id", "column_type": "INT64", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 100.0, "min_value": "1", "max_value": "2", "mean_value": 1.5, "top_values": null}, {"column_name": "first_name", "column_type": "STR", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 100.0, "min_value": "Jon", "max_value": "Mike", "mean_value": null, "top_values": null}, {"column_name": "last_name", "column_type": "STR", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 100.0, "min_value": "Hillyer", "max_value": "Stephens", "mean_value": null, "top_values": null}, {"column_name": "address_id", "column_type": "INT64", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 100.0, "min_value": "3", "max_value": "4", "mean_value": 3.5, "top_values": null}, {"column_name": "picture", "column_type": "OBJECT", "total_count": 2, "null_count": 1, "null_percent": 50.0, "unique_count": 1, "unique_percent": 50.0, "min_value": null, "max_value": null, "mean_value": null, "top_values": null}, {"column_name": "email", "column_type": "STR", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 100.0, "min_value": "Jon.Stephens@sakilastaff.com", "max_value": "Mike.Hillyer@sakilastaff.com", "mean_value": null, "top_values": null}, {"column_name": "store_id", "column_type": "INT64", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 100.0, "min_value": "1", "max_value": "2", "mean_value": 1.5, "top_values": null}, {"column_name": "active", "column_type": "INT64", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 50.0, "min_value": "1", "max_value": "1", "mean_value": 1.0, "top_values": null}, {"column_name": "username", "column_type": "STR", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 2, "unique_percent": 100.0, "min_value": "Jon", "max_value": "Mike", "mean_value": null, "top_values": null}, {"column_name": "password", "column_type": "STR", "total_count": 2, "null_count": 1, "null_percent": 50.0, "unique_count": 1, "unique_percent": 50.0, "min_value": "8cb2237d0679ca88db6464eac60da96345513964", "max_value": "8cb2237d0679ca88db6464eac60da96345513964", "mean_value": null, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 2, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 50.0, "min_value": "2006-02-15 03:57:16", "max_value": "2006-02-15 03:57:16", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.staff.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 603, '[{"column_name": "address_id", "column_type": "INT64", "total_count": 603, "null_count": 0, "null_percent": 0.0, "unique_count": 603, "unique_percent": 100.0, "min_value": "1", "max_value": "605", "mean_value": 302.71973466003317, "top_values": null}, {"column_name": "address", "column_type": "STR", "total_count": 603, "null_count": 0, "null_percent": 0.0, "unique_count": 603, "unique_percent": 100.0, "min_value": "1 Valle de Santiago Avenue", "max_value": "999 Sanaa Loop", "mean_value": null, "top_values": null}, {"column_name": "address2", "column_type": "STR", "total_count": 603, "null_count": 4, "null_percent": 0.66, "unique_count": 1, "unique_percent": 0.17, "min_value": "", "max_value": "", "mean_value": null, "top_values": null}, {"column_name": "district", "column_type": "STR", "total_count": 603, "null_count": 0, "null_percent": 0.0, "unique_count": 378, "unique_percent": 62.69, "min_value": "", "max_value": "al-Sharqiya", "mean_value": null, "top_values": null}, {"column_name": "city_id", "column_type": "INT64", "total_count": 603, "null_count": 0, "null_percent": 0.0, "unique_count": 599, "unique_percent": 99.34, "min_value": "1", "max_value": "600", "mean_value": 300.5257048092869, "top_values": null}, {"column_name": "postal_code", "column_type": "STR", "total_count": 603, "null_count": 0, "null_percent": 0.0, "unique_count": 597, "unique_percent": 99.0, "min_value": "", "max_value": "99865", "mean_value": null, "top_values": null}, {"column_name": "phone", "column_type": "STR", "total_count": 603, "null_count": 0, "null_percent": 0.0, "unique_count": 602, "unique_percent": 99.83, "min_value": "", "max_value": "99883471275", "mean_value": null, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 603, "null_count": 0, "null_percent": 0.0, "unique_count": 247, "unique_percent": 40.96, "min_value": "2014-09-25 22:29:59", "max_value": "2014-09-25 22:34:11", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.address.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 600, '[{"column_name": "city_id", "column_type": "INT64", "total_count": 600, "null_count": 0, "null_percent": 0.0, "unique_count": 600, "unique_percent": 100.0, "min_value": "1", "max_value": "600", "mean_value": 300.5, "top_values": null}, {"column_name": "city", "column_type": "STR", "total_count": 600, "null_count": 0, "null_percent": 0.0, "unique_count": 599, "unique_percent": 99.83, "min_value": "A Coruña (La Coruña)", "max_value": "Šostka", "mean_value": null, "top_values": null}, {"column_name": "country_id", "column_type": "INT64", "total_count": 600, "null_count": 0, "null_percent": 0.0, "unique_count": 109, "unique_percent": 18.17, "min_value": "1", "max_value": "109", "mean_value": 56.4, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 600, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 0.17, "min_value": "2006-02-15 04:45:25", "max_value": "2006-02-15 04:45:25", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.city.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 109, '[{"column_name": "country_id", "column_type": "INT64", "total_count": 109, "null_count": 0, "null_percent": 0.0, "unique_count": 109, "unique_percent": 100.0, "min_value": "1", "max_value": "109", "mean_value": 55.0, "top_values": null}, {"column_name": "country", "column_type": "STR", "total_count": 109, "null_count": 0, "null_percent": 0.0, "unique_count": 109, "unique_percent": 100.0, "min_value": "Afghanistan", "max_value": "Zambia", "mean_value": null, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 109, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 0.92, "min_value": "2006-02-15 04:44:00", "max_value": "2006-02-15 04:44:00", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.country.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 16, '[{"column_name": "category_id", "column_type": "INT64", "total_count": 16, "null_count": 0, "null_percent": 0.0, "unique_count": 16, "unique_percent": 100.0, "min_value": "1", "max_value": "16", "mean_value": 8.5, "top_values": null}, {"column_name": "name", "column_type": "STR", "total_count": 16, "null_count": 0, "null_percent": 0.0, "unique_count": 16, "unique_percent": 100.0, "min_value": "Action", "max_value": "Travel", "mean_value": null, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 16, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 6.25, "min_value": "2006-02-15 04:46:27", "max_value": "2006-02-15 04:46:27", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.category.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 6, '[{"column_name": "language_id", "column_type": "INT64", "total_count": 6, "null_count": 0, "null_percent": 0.0, "unique_count": 6, "unique_percent": 100.0, "min_value": "1", "max_value": "6", "mean_value": 3.5, "top_values": null}, {"column_name": "name", "column_type": "STR", "total_count": 6, "null_count": 0, "null_percent": 0.0, "unique_count": 6, "unique_percent": 100.0, "min_value": "English", "max_value": "Mandarin", "mean_value": null, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 6, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 16.67, "min_value": "2006-02-15 05:02:19", "max_value": "2006-02-15 05:02:19", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.language.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 5462, '[{"column_name": "actor_id", "column_type": "INT64", "total_count": 5462, "null_count": 0, "null_percent": 0.0, "unique_count": 200, "unique_percent": 3.66, "min_value": "1", "max_value": "200", "mean_value": 100.95239838886854, "top_values": null}, {"column_name": "film_id", "column_type": "INT64", "total_count": 5462, "null_count": 0, "null_percent": 0.0, "unique_count": 997, "unique_percent": 18.25, "min_value": "1", "max_value": "1000", "mean_value": 501.14243866715486, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 5462, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 0.02, "min_value": "2006-02-15 05:05:03", "max_value": "2006-02-15 05:05:03", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.film_actor.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 1000, '[{"column_name": "film_id", "column_type": "INT64", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1000, "unique_percent": 100.0, "min_value": "1", "max_value": "1000", "mean_value": 500.5, "top_values": null}, {"column_name": "category_id", "column_type": "INT64", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 16, "unique_percent": 1.6, "min_value": "1", "max_value": "16", "mean_value": 8.478, "top_values": null}, {"column_name": "last_update", "column_type": "DATETIME64[US]", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1, "unique_percent": 0.1, "min_value": "2006-02-15 05:07:09", "max_value": "2006-02-15 05:07:09", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.film_category.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

INSERT INTO catalog_data_profile (dataset_id, row_count, profile_json)
SELECT d.id, 1000, '[{"column_name": "film_id", "column_type": "INT64", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1000, "unique_percent": 100.0, "min_value": "1", "max_value": "1000", "mean_value": 500.5, "top_values": null}, {"column_name": "title", "column_type": "STR", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1000, "unique_percent": 100.0, "min_value": "ACADEMY DINOSAUR", "max_value": "ZORRO ARK", "mean_value": null, "top_values": null}, {"column_name": "description", "column_type": "STR", "total_count": 1000, "null_count": 0, "null_percent": 0.0, "unique_count": 1000, "unique_percent": 100.0, "min_value": "A Action-Packed Character Study of a Astronaut And a Explorer who must Reach a Monkey in A MySQL Convention", "max_value": "A Unbelieveable Yarn of a Student And a Database Administrator who must Outgun a Husband in An Abandoned Mine Shaft", "mean_value": null, "top_values": null}]'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.film_text.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_data_profile p WHERE p.dataset_id = d.id);

-- 12.3 규칙별 검증 결과 (규칙별 최신 1건)
INSERT INTO catalog_quality_result (rule_id, dataset_id, passed, actual_value, detail)
SELECT q.id, d.id, v.passed, v.actual_value, v.detail
FROM (VALUES
    ('sakila-mysql.sakila.address.dataset', '전화번호 입력률 90%', 'true', '100.0%', '비-NULL 100.0% (임계값 90.0%)'),
    ('sakila-mysql.sakila.country.dataset', '국가명 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.customer.dataset', '회원 500명 이상', 'true', '599', '행 수 599 (기대 ≥ 500)'),
    ('sakila-mysql.sakila.film_text.dataset', 'film_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.address.dataset', '도시 참조 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.inventory.dataset', 'inventory_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.category.dataset', '장르명 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.address.dataset', 'address_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.customer.dataset', 'customer_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.category.dataset', 'category_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.customer.dataset', '소속 점포 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.payment.dataset', '결제 16,000건 이상', 'true', '16044', '행 수 16044 (기대 ≥ 16000)'),
    ('sakila-mysql.sakila.rental.dataset', 'rental_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.actor.dataset', '배우 200명 이상', 'true', '200', '행 수 200 (기대 ≥ 200)'),
    ('sakila-mysql.sakila.customer.dataset', '이메일 형식', 'true', '위반 0행', '정규식 ''^[^@]+@[^@]+$'' 기준 위반 0행 (전체 데이터 검사)'),
    ('sakila-mysql.sakila.language.dataset', '언어명 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.store.dataset', '점포 2개 이상', 'true', '2', '행 수 2 (기대 ≥ 2)'),
    ('sakila-mysql.sakila.payment.dataset', '결제 고객 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.payment.dataset', 'payment_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.staff.dataset', '이메일 형식', 'true', '위반 0행', '정규식 ''^[^@]+@[^@]+$'' 기준 위반 0행 (전체 데이터 검사)'),
    ('sakila-mysql.sakila.inventory.dataset', '보유 영화 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.country.dataset', 'country_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.actor.dataset', 'actor_id 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.language.dataset', 'language_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.rental.dataset', '대여 재고 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.category.dataset', '장르명 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.film_category.dataset', '영화 참조 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.payment.dataset', '결제 금액 0 이상', 'true', '0.0', '최소값 0.0 (기대 ≥ 0.0)'),
    ('sakila-mysql.sakila.film_text.dataset', '제목 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.rental.dataset', '대여 고객 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.film.dataset', 'film_id 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.store.dataset', 'store_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.film.dataset', '대여료 0 이상', 'true', '0.99', '최소값 0.99 (기대 ≥ 0.0)'),
    ('sakila-mysql.sakila.film.dataset', 'film_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.film.dataset', '관람등급 허용값', 'true', '위반 0행', '허용 값 [''G'', ''PG'', ''PG-13'', ''R'', ''NC-17''] 기준 위반 0행 (전체 데이터 검사)'),
    ('sakila-mysql.sakila.staff.dataset', '로그인 계정 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.film.dataset', '영화 1000편 이상', 'true', '1000', '행 수 1000 (기대 ≥ 1000)'),
    ('sakila-mysql.sakila.film_category.dataset', '장르 참조 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.city.dataset', '국가 참조 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.city.dataset', 'city_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.staff.dataset', 'staff_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.actor.dataset', 'actor_id 유일', 'true', '100.0%', '고유값 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.film_actor.dataset', '영화 참조 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.rental.dataset', '대여 16,000건 이상', 'true', '16044', '행 수 16044 (기대 ≥ 16000)'),
    ('sakila-mysql.sakila.film_actor.dataset', '배우 참조 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)'),
    ('sakila-mysql.sakila.inventory.dataset', '보유 점포 필수', 'true', '100.0%', '비-NULL 100.0% (임계값 100.0%)')
) AS v(urn, rule_name, passed, actual_value, detail)
JOIN catalog_datasets d ON d.urn = v.urn
JOIN catalog_quality_rule q ON q.dataset_id = d.id AND q.rule_name = v.rule_name
WHERE NOT EXISTS (SELECT 1 FROM catalog_quality_result x WHERE x.rule_id = q.id);

-- 12.4 품질 점수 (데이터셋별 최신 1건)
INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 5, 5, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.film.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 3, 3, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.actor.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 4, 4, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.customer.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 4, 4, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.rental.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 4, 4, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.payment.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 3, 3, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.inventory.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 2, 2, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.store.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 75.0, 4, 3, 0, 1
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.staff.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 75.0, 4, 3, 0, 1
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.address.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 2, 2, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.city.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 2, 2, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.country.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 3, 3, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.category.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 2, 2, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.language.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 2, 2, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.film_actor.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 2, 2, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.film_category.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);

INSERT INTO catalog_quality_score (dataset_id, score, total_rules, passed_rules, warning_rules, failed_rules)
SELECT d.id, 100.0, 2, 2, 0, 0
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.film_text.dataset'
  AND NOT EXISTS (SELECT 1 FROM catalog_quality_score s WHERE s.dataset_id = d.id);


-- 12.5 실패 데모 결과 — 실데이터의 실제 결함 2건 (위반 행 샘플 포함)
--     staff: Jon Stephens 의 password NULL / address: address2 NULL 4건.
--     품질 탭의 "위반 샘플 보기" 데모용. (규칙별 결과 없을 때만 삽입)
INSERT INTO catalog_quality_result (rule_id, dataset_id, passed, actual_value, detail, failed_samples)
SELECT q.id, d.id, 'false', v.actual_value, v.detail, v.failed_samples
FROM (VALUES
    ('sakila-mysql.sakila.address.dataset', '상세 주소(address2) 입력 필수', '99.3%', '비-NULL 99.3% (임계값 100.0%)', '[{"address_id": "1", "address": "47 MySakila Drive", "address2": null, "district": "Alberta", "city_id": "300", "postal_code": "", "phone": "", "last_update": "2014-09-25 22:30:27"}, {"address_id": "2", "address": "28 MySQL Boulevard", "address2": null, "district": "QLD", "city_id": "576", "postal_code": "", "phone": "", "last_update": "2014-09-25 22:30:09"}, {"address_id": "3", "address": "23 Workhaven Lane", "address2": null, "district": "Alberta", "city_id": "300", "postal_code": "", "phone": "14033335568", "last_update": "2014-09-25 22:30:27"}, {"address_id": "4", "address": "1411 Lillydale Drive", "address2": null, "district": "QLD", "city_id": "576", "postal_code": "", "phone": "6172235589", "last_update": "2014-09-25 22:30:09"}]'),
    ('sakila-mysql.sakila.staff.dataset', '직원 비밀번호 해시 필수', '50.0%', '비-NULL 50.0% (임계값 100.0%)', '[{"staff_id": "2", "first_name": "Jon", "last_name": "Stephens", "address_id": "4", "email": "Jon.Stephens@sakilastaff.com", "store_id": "2", "active": "1", "username": "Jon", "password": null, "last_update": "2006-02-15 03:57:16"}]')
) AS v(urn, rule_name, actual_value, detail, failed_samples)
JOIN catalog_datasets d ON d.urn = v.urn
JOIN catalog_quality_rule q ON q.dataset_id = d.id AND q.rule_name = v.rule_name
WHERE NOT EXISTS (SELECT 1 FROM catalog_quality_result x WHERE x.rule_id = q.id);

-- 12.6 데이터셋 품질 점수/상태 — 실측 (staff·address 는 실패 데모로 WARN)
UPDATE catalog_datasets SET quality_score = 100, quality_status = 'GOOD'
WHERE urn LIKE 'sakila-mysql.%' AND urn NOT IN
    ('sakila-mysql.sakila.staff.dataset', 'sakila-mysql.sakila.address.dataset');
UPDATE catalog_datasets SET quality_score = 75, quality_status = 'WARN'
WHERE urn IN ('sakila-mysql.sakila.staff.dataset', 'sakila-mysql.sakila.address.dataset');

-- 12.7 품질 실패 데모 알림 (거버넌스 > 알림 — QUALITY_FAILED)
INSERT INTO argus_lineage_alert (alert_type, severity, source_dataset_id, rule_id, change_summary, change_detail, status)
SELECT 'QUALITY_FAILED', v.severity, d.id, ar.id, v.change_summary, v.change_detail, 'OPEN'
FROM (VALUES
    ('sakila-mysql.sakila.address.dataset', 'INFO', '품질 검증 실패: sakila.address — 상세 주소(address2) 입력 필수 (점수 75.0%)', '{"score": 75.0, "failed_rules": [{"rule_name": "상세 주소(address2) 입력 필수", "check_type": "NOT_NULL", "severity": "INFO", "actual": "99.3%", "detail": "비-NULL 99.3% (임계값 100.0%)"}]}'),
    ('sakila-mysql.sakila.staff.dataset', 'WARNING', '품질 검증 실패: sakila.staff — 직원 비밀번호 해시 필수 (점수 75.0%)', '{"score": 75.0, "failed_rules": [{"rule_name": "직원 비밀번호 해시 필수", "check_type": "NOT_NULL", "severity": "WARNING", "actual": "50.0%", "detail": "비-NULL 50.0% (임계값 100.0%)"}]}')
) AS v(urn, severity, change_summary, change_detail)
JOIN catalog_datasets d ON d.urn = v.urn
LEFT JOIN argus_alert_rule ar ON ar.rule_name = 'Sakila 품질 검증 실패 통지'
WHERE NOT EXISTS (
    SELECT 1 FROM argus_lineage_alert x WHERE x.change_summary = v.change_summary
);

-- ---------------------------------------------------------------------------
-- 13. 알림 규칙 (거버넌스 > 알림) — 스키마 변경/컬럼 감시 데모 규칙 6건
--     rule_name 기준 NOT EXISTS 로 멱등 처리
-- ---------------------------------------------------------------------------

-- 13.1 전사 기본 모니터링 (범위 ALL — scope_id 없음)
INSERT INTO argus_alert_rule
    (rule_name, description, scope_type, scope_id, trigger_type, trigger_config,
     severity_override, channels, notify_owners, subscribers, is_active, created_by)
SELECT '전사 스키마 변경 모니터링',
       '모든 데이터셋의 스키마 변경을 인앱 알림으로 수집한다 (기본 안전망).',
       'ALL', NULL, 'ANY', '{}',
       NULL, 'IN_APP', 'true', NULL, 'true', 'admin'
WHERE NOT EXISTS (SELECT 1 FROM argus_alert_rule r WHERE r.rule_name = '전사 스키마 변경 모니터링');

-- 13.2 Sakila 데이터 소스 — 파괴적 변경(DROP/MODIFY)만 메일 통지
INSERT INTO argus_alert_rule
    (rule_name, description, scope_type, scope_id, trigger_type, trigger_config,
     severity_override, channels, notify_owners, subscribers, is_active, created_by)
SELECT 'Sakila 파괴적 스키마 변경',
       'Sakila 데이터 소스 전체에서 컬럼 삭제/타입 변경 발생 시 운영팀에 메일 통지.',
       'DATASOURCE', ds.id, 'SCHEMA_CHANGE', '{"change_types": ["DROP", "MODIFY"]}',
       NULL, 'IN_APP,EMAIL', 'true', 'khshin@data-dynamics.io', 'true', 'admin'
FROM catalog_datasources ds
WHERE ds.datasource_id = 'sakila-mysql'
  AND NOT EXISTS (SELECT 1 FROM argus_alert_rule r WHERE r.rule_name = 'Sakila 파괴적 스키마 변경');

-- 13.3 rental 핵심 컬럼 감시 — 대여/반납 일시와 고객 FK (치명 강제)
INSERT INTO argus_alert_rule
    (rule_name, description, scope_type, scope_id, trigger_type, trigger_config,
     severity_override, channels, notify_owners, subscribers, is_active, created_by)
SELECT 'rental 핵심 컬럼 감시',
       '대여 원장의 핵심 컬럼(rental_date/return_date/customer_id) 변경은 연체·정산 로직에 직결되므로 치명으로 승격해 즉시 통지.',
       'DATASET', d.id, 'COLUMN_WATCH',
       '{"columns": ["rental_date", "return_date", "customer_id"], "change_types": ["DROP", "MODIFY"]}',
       'BREAKING', 'IN_APP,EMAIL', 'true', 'khshin@data-dynamics.io,iuhong@data-dynamics.io', 'true', 'admin'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.rental.dataset'
  AND NOT EXISTS (SELECT 1 FROM argus_alert_rule r WHERE r.rule_name = 'rental 핵심 컬럼 감시');

-- 13.4 payment 금액 컬럼 감시 — 회계 영향 (치명 강제)
INSERT INTO argus_alert_rule
    (rule_name, description, scope_type, scope_id, trigger_type, trigger_config,
     severity_override, channels, notify_owners, subscribers, is_active, created_by)
SELECT 'payment 금액 컬럼 감시',
       '결제 금액(amount) 컬럼의 타입/정밀도 변경은 회계 정산에 영향 — 치명으로 승격.',
       'DATASET', d.id, 'COLUMN_WATCH',
       '{"columns": ["amount"], "change_types": ["DROP", "MODIFY"]}',
       'BREAKING', 'IN_APP,EMAIL', 'true', 'tkoh@data-dynamics.io', 'true', 'admin'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.payment.dataset'
  AND NOT EXISTS (SELECT 1 FROM argus_alert_rule r WHERE r.rule_name = 'payment 금액 컬럼 감시');

-- 13.5 film → film_text 파생 매핑 감시 — 매핑된 컬럼 변경 시에만 발동
INSERT INTO argus_alert_rule
    (rule_name, description, scope_type, scope_id, trigger_type, trigger_config,
     severity_override, channels, notify_owners, subscribers, is_active, created_by)
SELECT 'film 검색 파생 매핑 감시',
       'film → film_text 트리거 동기화에 사용되는 컬럼 매핑(film_id/title/description)이 깨지면 전문 검색이 무너진다.',
       'LINEAGE', l.id, 'MAPPING_BROKEN', '{}',
       'WARNING', 'IN_APP', 'true', 'mjkim@data-dynamics.io', 'true', 'admin'
FROM argus_dataset_lineage l
JOIN catalog_datasets s ON s.id = l.source_dataset_id AND s.urn = 'sakila-mysql.sakila.film.dataset'
JOIN catalog_datasets t ON t.id = l.target_dataset_id AND t.urn = 'sakila-mysql.sakila.film_text.dataset'
WHERE NOT EXISTS (SELECT 1 FROM argus_alert_rule r WHERE r.rule_name = 'film 검색 파생 매핑 감시');

-- 13.6 Sakila 품질 검증 실패 통지 — 검증/배치 반입 시 실패 규칙이 있으면 발동
INSERT INTO argus_alert_rule
    (rule_name, description, scope_type, scope_id, trigger_type, trigger_config,
     severity_override, channels, notify_owners, subscribers, is_active, created_by)
SELECT 'Sakila 품질 검증 실패 통지',
       '품질 검증(서버 실행 또는 배치 반입)에서 실패한 규칙이 생기면 데이터 소유자와 운영 담당자에게 알린다.',
       'DATASOURCE', ds.id, 'QUALITY_FAILED', '{}',
       NULL, 'IN_APP', 'true', 'khshin@data-dynamics.io', 'true', 'admin'
FROM catalog_datasources ds
WHERE ds.datasource_id = 'sakila-mysql'
  AND NOT EXISTS (SELECT 1 FROM argus_alert_rule r WHERE r.rule_name = 'Sakila 품질 검증 실패 통지');

-- 13.7 customer PII 컬럼 감시 — 개인정보 컬럼 추가/변경 감시 (비활성 예시)
INSERT INTO argus_alert_rule
    (rule_name, description, scope_type, scope_id, trigger_type, trigger_config,
     severity_override, channels, notify_owners, subscribers, is_active, created_by)
SELECT 'customer PII 컬럼 변경 감시',
       '고객 PII 컬럼(email/first_name/last_name) 변경 감시 — 개인정보 영향 평가 트리거용. 데모에서는 비활성 상태의 규칙 예시.',
       'DATASET', d.id, 'COLUMN_WATCH',
       '{"columns": ["email", "first_name", "last_name"], "change_types": ["ADD", "DROP", "MODIFY"]}',
       'WARNING', 'IN_APP', 'false', 'ejcho@data-dynamics.io', 'false', 'admin'
FROM catalog_datasets d
WHERE d.urn = 'sakila-mysql.sakila.customer.dataset'
  AND NOT EXISTS (SELECT 1 FROM argus_alert_rule r WHERE r.rule_name = 'customer PII 컬럼 변경 감시');

-- 14. 소유권 보정 — 시드 자산의 생성자를 admin 으로 (행 단위 소유권 기본값)
UPDATE catalog_datasets SET created_by = 'admin' WHERE created_by IS NULL;
UPDATE catalog_oci_models SET created_by = 'admin' WHERE created_by IS NULL;

COMMIT;


-- 시드 후 권장 작업:
--   설정 > 임베딩 > 백필 실행 — sakila 데이터셋 16개와 용어집이 시맨틱/통합 검색에 노출됨
