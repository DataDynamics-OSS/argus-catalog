-- ============================================================================
-- Argus Catalog — 데모 시드 데이터 (PostgreSQL)
--
-- 구성:
--   1. 역할(argus_roles) 3종 보정
--   2. 사용자 20명 — admin 1 + 일반 사용자 19 (비밀번호 = username, SHA-256)
--   3. 용어집(비즈니스 용어) — 반도체 / 물류 카테고리 + 용어 24건
--   4. 데이터 표준 — 반도체/물류 표준 사전 (단어·도메인·코드·표준용어·구성단어)
--
-- 실행:
--   psql -U <user> -d <db> -f argus-catalog-seed-demo.sql
--
-- 특징:
--   - 재실행 안전(idempotent): 전 구간 ON CONFLICT DO NOTHING / WHERE NOT EXISTS
--   - 스키마(argus-catalog-postgresql.sql) 적용 후 실행할 것
--   - 시드 후 권장: 설정 > 임베딩 > 백필 실행 (용어집 시맨틱 검색 활성화)
--
-- 데모 계정 (비밀번호 = username):
--   admin / admin (관리자), mjkim·shlee·jypark 외 16명 (일반 사용자)
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. 역할 보정 (이미 있으면 무시)
-- ---------------------------------------------------------------------------

INSERT INTO argus_roles (role_id, name, description)
VALUES
    ('argus-admin',     'Administrator', 'Full administrative access'),
    ('argus-superuser', 'Superuser',     'Elevated access without user management'),
    ('argus-user',      'User',          'Standard user with limited access')
ON CONFLICT (role_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. 사용자 — admin 1명 + 일반 사용자 19명
--    password_hash = SHA-256(username)  ※ 데모 전용, 운영 사용 금지
-- ---------------------------------------------------------------------------

INSERT INTO argus_users
    (username, email, first_name, last_name, organization, department, password_hash, status, role_id)
SELECT u.username, u.email, u.first_name, u.last_name, u.organization, u.department,
       u.password_hash, 'active', r.id
FROM (VALUES
    -- 관리자
    ('admin',  'architect@data-dynamics.io', '병곤', '김', '데이터다이나믹스', '데이터플랫폼팀',
     '8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918', 'argus-admin'),
    -- 반도체 도메인 (10명)
    ('mjkim',  'mjkim@data-dynamics.io',  '민지', '김', '데이터다이나믹스', '반도체공정팀',
     'bed3fe59b7c73db10170b8b80e05a4cd2ee0b281ddf63f910a1030bd3f76880b', 'argus-user'),
    ('shlee',  'shlee@data-dynamics.io',  '상현', '이', '데이터다이나믹스', '반도체공정팀',
     'e2869faaf249d18f7ca47d746aac71138c4e6306c095977134c47cd9c60920de', 'argus-user'),
    ('jypark', 'jypark@data-dynamics.io', '지영', '박', '데이터다이나믹스', '수율분석팀',
     '14f9d02c1251195ffbcb01d55b143dcf914139130c4703ac695c7bc0c6c0e8fc', 'argus-user'),
    ('dhchoi', 'dhchoi@data-dynamics.io', '동혁', '최', '데이터다이나믹스', '수율분석팀',
     'd141e6efeb90d4b16055f3d647faea02f6d25da74c1d2f8205fcfd086e23050f', 'argus-user'),
    ('yjjung', 'yjjung@data-dynamics.io', '유진', '정', '데이터다이나믹스', '설비기술팀',
     '7358f7722f5382f676683d544d144670f82fea0794ba0b1540566bead0d77142', 'argus-user'),
    ('swhan',  'swhan@data-dynamics.io',  '성우', '한', '데이터다이나믹스', '설비기술팀',
     '152ddc2d3752146772dac367adb1acf10790810a906658c60bd5e92b22215add', 'argus-user'),
    ('hjkang', 'hjkang@data-dynamics.io', '현주', '강', '데이터다이나믹스', '품질관리팀',
     '5ba1db554ae1cfd7266084be68f35b457bec578013de65c32c115efcd99370c2', 'argus-user'),
    ('mskim',  'mskim@data-dynamics.io',  '민수', '김', '데이터다이나믹스', '품질관리팀',
     '87a8ac7644718cfe39e9a1ea5ad32bc9cc76af9ebb4b8f6e9339dde84b389083', 'argus-user'),
    ('jwlee',  'jwlee@data-dynamics.io',  '지원', '이', '데이터다이나믹스', '공정데이터팀',
     'e58fd59d39b5a8db358a369ca9828f8f0a4c8467e54d8cac6f70d5f51a16fd51', 'argus-user'),
    ('sypark', 'sypark@data-dynamics.io', '서연', '박', '데이터다이나믹스', '공정데이터팀',
     '5d2df773b84328e9bf4bd54145cb37e571fe95bb897be76d29cccba0c3ec8aa0', 'argus-user'),
    -- 물류 도메인 (9명)
    ('khshin', 'khshin@data-dynamics.io', '경호', '신', '데이터다이나믹스', '물류운영팀',
     '8a6f0982f4e1f06f2d19cadab871fbe3e90492acdabbd3f7e03d4d7abbd9c797', 'argus-user'),
    ('ejcho',  'ejcho@data-dynamics.io',  '은지', '조', '데이터다이나믹스', '물류운영팀',
     '675a4995af49ed4ab40cf53eff07dccbe384e4873386e5ecbdb3d0dc8a227389', 'argus-user'),
    ('tkoh',   'tkoh@data-dynamics.io',   '태경', '오', '데이터다이나믹스', 'SCM팀',
     'c40fcb17ee97e5f9562780f1f222ac2c7b51e5f4943b7a9bc8e03bcddb5931b4', 'argus-user'),
    ('hyseo',  'hyseo@data-dynamics.io',  '하윤', '서', '데이터다이나믹스', 'SCM팀',
     '90354512513e6382f004e67aa973ad98f13e3184a35b1297b0372dff45c3c60b', 'argus-user'),
    ('jhyoon', 'jhyoon@data-dynamics.io', '재호', '윤', '데이터다이나믹스', '풀필먼트팀',
     'd453a6fc16f0b379a2aadf3de7f625471ba159536cf856133265237d7080a319', 'argus-user'),
    ('sbmoon', 'sbmoon@data-dynamics.io', '수빈', '문', '데이터다이나믹스', '풀필먼트팀',
     '85e846995dc0b7b0f12cc1d4031961c2277cb7d16326b3b13602d95798446095', 'argus-user'),
    ('nryu',   'nryu@data-dynamics.io',   '나연', '류', '데이터다이나믹스', '배송관제팀',
     '308c6d788b2e68c0fe38262be3115814a91eefd6620b8925062727e545fb7ba3', 'argus-user'),
    ('gwjang', 'gwjang@data-dynamics.io', '건우', '장', '데이터다이나믹스', '배송관제팀',
     '0923dd0744b3a822b6e5772a13a4271ce100223b5839d108ca0ac37225c35228', 'argus-user'),
    ('iuhong', 'iuhong@data-dynamics.io', '이안', '홍', '데이터다이나믹스', '물류데이터팀',
     'ce18f819169840123ec8f393596411f2ea7945e6ff64d87552dfdc39df7d7411', 'argus-user')
) AS u(username, email, first_name, last_name, organization, department, password_hash, role_code)
JOIN argus_roles r ON r.role_id = u.role_code
ON CONFLICT (username) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. 용어집 (거버넌스 > 용어집)
--    카테고리(반도체/물류) → 하위 용어. name 이 UNIQUE 라 재실행 안전.
-- ---------------------------------------------------------------------------

INSERT INTO catalog_glossary_terms (name, description, term_type)
VALUES
    ('반도체', '반도체 제조 도메인 비즈니스 용어', 'CATEGORY'),
    ('물류',   '물류·SCM 도메인 비즈니스 용어',     'CATEGORY')
ON CONFLICT (name) DO NOTHING;

-- 반도체 용어
INSERT INTO catalog_glossary_terms (name, description, parent_id, term_type)
SELECT t.name, t.description, p.id, 'TERM'
FROM (VALUES
    ('웨이퍼',        '반도체 집적회로 제조의 기판이 되는 얇은 실리콘 원판. 직경(200mm/300mm)별로 공정 라인이 구분된다.'),
    ('로트',          '동일 조건으로 함께 처리되는 웨이퍼 묶음(통상 25매). 생산 추적·품질 관리의 기본 단위.'),
    ('포토리소그래피', '감광액을 도포한 웨이퍼에 마스크 패턴을 노광·현상하여 회로 패턴을 전사하는 공정.'),
    ('식각',          '에칭(Etching). 웨이퍼 표면에서 불필요한 물질을 화학적(습식)·물리적(건식)으로 제거하는 공정.'),
    ('증착',          '웨이퍼 표면에 박막을 입히는 공정. CVD(화학기상증착)·PVD(물리기상증착)·ALD 등으로 구분.'),
    ('이온주입',      '불순물 이온을 가속해 웨이퍼에 주입, 전기적 특성을 부여하는 공정(도핑).'),
    ('CMP',           '화학적 기계 연마(Chemical Mechanical Polishing). 웨이퍼 표면을 평탄화하는 공정.'),
    ('EUV',           '극자외선(Extreme Ultraviolet) 노광 기술. 7nm 이하 초미세 공정의 핵심 장비/기술.'),
    ('수율',          '투입 대비 양품 비율(%). 다이 수율·웨이퍼 수율·패키지 수율 등으로 세분화하여 관리.'),
    ('다이',          '웨이퍼에서 절단된 개별 칩 단위. 다이당 비용·다이 수율이 수익성의 핵심 지표.'),
    ('팹',            'Fab(Fabrication Facility). 웨이퍼 가공이 이뤄지는 반도체 제조 공장/클린룸 라인.'),
    ('패키징',        '가공이 끝난 다이를 외부 환경에서 보호하고 기판과 연결 가능하게 만드는 후공정.')
) AS t(name, description)
JOIN catalog_glossary_terms p ON p.name = '반도체' AND p.term_type = 'CATEGORY'
ON CONFLICT (name) DO NOTHING;

-- 물류 용어
INSERT INTO catalog_glossary_terms (name, description, parent_id, term_type)
SELECT t.name, t.description, p.id, 'TERM'
FROM (VALUES
    ('입고',          '상품이 창고에 도착해 검수·적치되는 프로세스. 입고 예정(ASN) 대비 실적으로 정확도를 관리.'),
    ('출고',          '주문에 따라 상품을 피킹·패킹하여 창고에서 반출하는 프로세스.'),
    ('재고회전율',    '일정 기간 재고가 판매·소진된 횟수. 매출원가 ÷ 평균재고. 재고 효율성의 핵심 지표.'),
    ('리드타임',      '발주부터 입고(또는 주문부터 배송 완료)까지 걸리는 총 소요 시간.'),
    ('안전재고',      '수요·공급 변동에 대비해 유지하는 최소 재고 수준. 결품 방지와 재고 비용의 균형점.'),
    ('SKU',           'Stock Keeping Unit. 재고 관리의 최소 단위 식별자(상품·옵션·규격 단위).'),
    ('풀필먼트',      '주문 접수부터 피킹·패킹·배송·반품까지 일괄 대행하는 물류 서비스.'),
    ('크로스도킹',    '입고 상품을 보관 없이 곧바로 출고로 연결하는 무재고 환적 방식.'),
    ('라스트마일',    '배송 거점에서 최종 고객까지의 마지막 배송 구간. 비용·고객 경험의 최대 변수.'),
    ('WMS',           'Warehouse Management System. 입출고·재고·로케이션을 관리하는 창고 관리 시스템.'),
    ('3PL',           'Third Party Logistics. 제3자 물류. 화주의 물류 기능을 외부 전문 업체에 위탁하는 형태.'),
    ('운송장',        '화물 운송 계약의 증빙 문서. 운송장 번호로 배송 추적의 기본 키 역할을 한다.')
) AS t(name, description)
JOIN catalog_glossary_terms p ON p.name = '물류' AND p.term_type = 'CATEGORY'
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 4. 데이터 표준 (거버넌스 > 데이터 표준)
-- ---------------------------------------------------------------------------

-- 4.1 표준 사전
INSERT INTO catalog_standard_dictionary (dict_name, description, version, status, effective_date, created_by)
VALUES
    ('반도체 표준 사전', '반도체 제조(전공정/후공정) 도메인 데이터 표준 사전', '1.0', 'ACTIVE', CURRENT_DATE, 'admin'),
    ('물류 표준 사전',   '물류·SCM 도메인 데이터 표준 사전',                   '1.0', 'ACTIVE', CURRENT_DATE, 'admin')
ON CONFLICT (dict_name) DO NOTHING;

-- 4.2 표준 단어 — 반도체
INSERT INTO catalog_standard_word (dictionary_id, word_name, word_english, word_abbr, word_type, description, status)
SELECT d.id, w.word_name, w.word_english, w.word_abbr, w.word_type, w.description, 'ACTIVE'
FROM (VALUES
    ('웨이퍼', 'Wafer',      'WFR',  'GENERAL', '반도체 기판 실리콘 원판'),
    ('로트',   'Lot',        'LOT',  'GENERAL', '동일 조건 처리 웨이퍼 묶음'),
    ('공정',   'Process',    'PROC', 'GENERAL', '제조 공정 단계'),
    ('설비',   'Equipment',  'EQP',  'GENERAL', '제조 설비/장비'),
    ('수율',   'Yield',      'YLD',  'GENERAL', '투입 대비 양품 비율'),
    ('검사',   'Inspection', 'INSP', 'GENERAL', '품질 검사'),
    ('칩',     'Chip',       'CHIP', 'GENERAL', '개별 다이/칩'),
    ('마스크', 'Mask',       'MSK',  'GENERAL', '노광용 포토마스크'),
    ('번호',   'Number',     'NO',   'SUFFIX',  '식별 번호 접미어'),
    ('코드',   'Code',       'CD',   'SUFFIX',  '분류 코드 접미어'),
    ('일자',   'Date',       'DT',   'SUFFIX',  '날짜 접미어'),
    ('수량',   'Quantity',   'QTY',  'SUFFIX',  '개수/수량 접미어'),
    ('율',     'Rate',       'RT',   'SUFFIX',  '비율 접미어'),
    ('명',     'Name',       'NM',   'SUFFIX',  '명칭 접미어')
) AS w(word_name, word_english, word_abbr, word_type, description)
JOIN catalog_standard_dictionary d ON d.dict_name = '반도체 표준 사전'
ON CONFLICT (dictionary_id, word_name) DO NOTHING;

-- 4.2 표준 단어 — 물류
INSERT INTO catalog_standard_word (dictionary_id, word_name, word_english, word_abbr, word_type, description, status)
SELECT d.id, w.word_name, w.word_english, w.word_abbr, w.word_type, w.description, 'ACTIVE'
FROM (VALUES
    ('창고',   'Warehouse', 'WH',   'GENERAL', '물류 창고/센터'),
    ('재고',   'Inventory', 'INV',  'GENERAL', '보관 중인 상품 수량'),
    ('주문',   'Order',     'ORD',  'GENERAL', '고객 주문'),
    ('배송',   'Delivery',  'DLV',  'GENERAL', '상품 배송'),
    ('운송장', 'Waybill',   'WB',   'GENERAL', '운송 증빙 문서'),
    ('상품',   'Product',   'PRD',  'GENERAL', '판매 상품'),
    ('입고',   'Inbound',   'IB',   'GENERAL', '창고 입고'),
    ('출고',   'Outbound',  'OB',   'GENERAL', '창고 출고'),
    ('운임',   'Freight',   'FRT',  'GENERAL', '운송 요금'),
    ('번호',   'Number',    'NO',   'SUFFIX',  '식별 번호 접미어'),
    ('코드',   'Code',      'CD',   'SUFFIX',  '분류 코드 접미어'),
    ('일자',   'Date',      'DT',   'SUFFIX',  '날짜 접미어'),
    ('수량',   'Quantity',  'QTY',  'SUFFIX',  '개수/수량 접미어'),
    ('금액',   'Amount',    'AMT',  'SUFFIX',  '금액 접미어'),
    ('상태',   'Status',    'ST',   'SUFFIX',  '상태 접미어')
) AS w(word_name, word_english, word_abbr, word_type, description)
JOIN catalog_standard_dictionary d ON d.dict_name = '물류 표준 사전'
ON CONFLICT (dictionary_id, word_name) DO NOTHING;

-- 4.3 표준 도메인 (양쪽 사전 공통 형식)
INSERT INTO catalog_standard_domain (dictionary_id, domain_name, domain_group, data_type, data_length, data_precision, data_scale, description, status)
SELECT d.id, dom.domain_name, dom.domain_group, dom.data_type, dom.data_length, dom.data_precision, dom.data_scale, dom.description, 'ACTIVE'
FROM (VALUES
    ('번호', '문자형', 'VARCHAR', 20,   NULL, NULL, '식별 번호 형식 (예: LOT-2024-00001)'),
    ('코드', '문자형', 'VARCHAR', 10,   NULL, NULL, '분류 코드 형식'),
    ('명',   '문자형', 'VARCHAR', 100,  NULL, NULL, '명칭 형식'),
    ('일자', '날짜형', 'DATE',    NULL, NULL, NULL, '날짜 형식 (YYYY-MM-DD)'),
    ('수량', '숫자형', 'NUMERIC', NULL, 12,   0,    '정수 수량 형식'),
    ('율',   '숫자형', 'NUMERIC', NULL, 5,    2,    '백분율 형식 (0.00 ~ 100.00)'),
    ('금액', '숫자형', 'NUMERIC', NULL, 15,   2,    '금액 형식')
) AS dom(domain_name, domain_group, data_type, data_length, data_precision, data_scale, description)
CROSS JOIN catalog_standard_dictionary d
WHERE d.dict_name IN ('반도체 표준 사전', '물류 표준 사전')
ON CONFLICT (dictionary_id, domain_name) DO NOTHING;

-- 4.4 코드 그룹 + 코드 값
INSERT INTO catalog_code_group (dictionary_id, group_name, group_english, description, status)
SELECT d.id, '공정상태코드', 'Process Status Code', '공정 진행 상태 분류', 'ACTIVE'
FROM catalog_standard_dictionary d WHERE d.dict_name = '반도체 표준 사전'
ON CONFLICT (dictionary_id, group_name) DO NOTHING;

INSERT INTO catalog_code_value (code_group_id, code_value, code_name, code_english, sort_order)
SELECT g.id, v.code_value, v.code_name, v.code_english, v.sort_order
FROM (VALUES
    ('WAIT',  '대기',     'Waiting',    1),
    ('RUN',   '진행',     'Running',    2),
    ('DONE',  '완료',     'Completed',  3),
    ('HOLD',  '보류',     'On Hold',    4),
    ('SCRAP', '폐기',     'Scrapped',   5)
) AS v(code_value, code_name, code_english, sort_order)
JOIN catalog_code_group g ON g.group_name = '공정상태코드'
ON CONFLICT (code_group_id, code_value) DO NOTHING;

INSERT INTO catalog_code_group (dictionary_id, group_name, group_english, description, status)
SELECT d.id, '배송상태코드', 'Delivery Status Code', '배송 진행 상태 분류', 'ACTIVE'
FROM catalog_standard_dictionary d WHERE d.dict_name = '물류 표준 사전'
ON CONFLICT (dictionary_id, group_name) DO NOTHING;

INSERT INTO catalog_code_value (code_group_id, code_value, code_name, code_english, sort_order)
SELECT g.id, v.code_value, v.code_name, v.code_english, v.sort_order
FROM (VALUES
    ('READY',     '배송준비', 'Ready',        1),
    ('SHIP',      '출고완료', 'Shipped',      2),
    ('TRANSIT',   '배송중',   'In Transit',   3),
    ('DELIVERED', '배송완료', 'Delivered',    4),
    ('RETURN',    '반품',     'Returned',     5)
) AS v(code_value, code_name, code_english, sort_order)
JOIN catalog_code_group g ON g.group_name = '배송상태코드'
ON CONFLICT (code_group_id, code_value) DO NOTHING;

-- 4.5 표준 용어 — 반도체 (단어 조합 + 도메인 연결)
INSERT INTO catalog_standard_term (dictionary_id, term_name, term_english, term_abbr, physical_name, domain_id, description, created_by, status)
SELECT d.id, t.term_name, t.term_english, t.term_abbr, t.physical_name, dom.id, t.description, 'admin', 'ACTIVE'
FROM (VALUES
    ('웨이퍼번호', 'Wafer Number',      'WFR_NO',   'wfr_no',   '번호', '웨이퍼 개별 식별 번호'),
    ('로트번호',   'Lot Number',        'LOT_NO',   'lot_no',   '번호', '생산 로트 식별 번호'),
    ('공정코드',   'Process Code',      'PROC_CD',  'proc_cd',  '코드', '공정 단계 분류 코드'),
    ('설비코드',   'Equipment Code',    'EQP_CD',   'eqp_cd',   '코드', '제조 설비 분류 코드'),
    ('수율율',     'Yield Rate',        'YLD_RT',   'yld_rt',   '율',   '투입 대비 양품 비율(%)'),
    ('검사일자',   'Inspection Date',   'INSP_DT',  'insp_dt',  '일자', '품질 검사 수행 일자'),
    ('칩수량',     'Chip Quantity',     'CHIP_QTY', 'chip_qty', '수량', '웨이퍼당 칩(다이) 수량')
) AS t(term_name, term_english, term_abbr, physical_name, domain_name, description)
JOIN catalog_standard_dictionary d ON d.dict_name = '반도체 표준 사전'
JOIN catalog_standard_domain dom ON dom.dictionary_id = d.id AND dom.domain_name = t.domain_name
ON CONFLICT (dictionary_id, term_name) DO NOTHING;

-- 4.5 표준 용어 — 물류
INSERT INTO catalog_standard_term (dictionary_id, term_name, term_english, term_abbr, physical_name, domain_id, description, created_by, status)
SELECT d.id, t.term_name, t.term_english, t.term_abbr, t.physical_name, dom.id, t.description, 'admin', 'ACTIVE'
FROM (VALUES
    ('운송장번호', 'Waybill Number',    'WB_NO',    'wb_no',    '번호', '운송장 식별 번호(배송 추적 키)'),
    ('주문번호',   'Order Number',      'ORD_NO',   'ord_no',   '번호', '고객 주문 식별 번호'),
    ('창고코드',   'Warehouse Code',    'WH_CD',    'wh_cd',    '코드', '물류 창고/센터 분류 코드'),
    ('배송상태코드', 'Delivery Status Code', 'DLV_ST_CD', 'dlv_st_cd', '코드', '배송 진행 상태 코드(배송상태코드 그룹 참조)'),
    ('입고일자',   'Inbound Date',      'IB_DT',    'ib_dt',    '일자', '창고 입고 일자'),
    ('재고수량',   'Inventory Quantity', 'INV_QTY', 'inv_qty',  '수량', 'SKU 별 보관 재고 수량'),
    ('운임금액',   'Freight Amount',    'FRT_AMT',  'frt_amt',  '금액', '운송 요금 금액')
) AS t(term_name, term_english, term_abbr, physical_name, domain_name, description)
JOIN catalog_standard_dictionary d ON d.dict_name = '물류 표준 사전'
JOIN catalog_standard_domain dom ON dom.dictionary_id = d.id AND dom.domain_name = t.domain_name
ON CONFLICT (dictionary_id, term_name) DO NOTHING;

-- 4.6 용어 구성 단어 매핑 (형태소 분해: 용어 = 단어1 + 단어2)
--    예: 웨이퍼번호 = 웨이퍼(1) + 번호(2)
INSERT INTO catalog_standard_term_words (term_id, word_id, ordinal)
SELECT t.id, w.id, m.ordinal
FROM (VALUES
    -- 반도체
    ('반도체 표준 사전', '웨이퍼번호', '웨이퍼', 1), ('반도체 표준 사전', '웨이퍼번호', '번호', 2),
    ('반도체 표준 사전', '로트번호',   '로트',   1), ('반도체 표준 사전', '로트번호',   '번호', 2),
    ('반도체 표준 사전', '공정코드',   '공정',   1), ('반도체 표준 사전', '공정코드',   '코드', 2),
    ('반도체 표준 사전', '설비코드',   '설비',   1), ('반도체 표준 사전', '설비코드',   '코드', 2),
    ('반도체 표준 사전', '수율율',     '수율',   1), ('반도체 표준 사전', '수율율',     '율',   2),
    ('반도체 표준 사전', '검사일자',   '검사',   1), ('반도체 표준 사전', '검사일자',   '일자', 2),
    ('반도체 표준 사전', '칩수량',     '칩',     1), ('반도체 표준 사전', '칩수량',     '수량', 2),
    -- 물류
    ('물류 표준 사전', '운송장번호',   '운송장', 1), ('물류 표준 사전', '운송장번호',   '번호', 2),
    ('물류 표준 사전', '주문번호',     '주문',   1), ('물류 표준 사전', '주문번호',     '번호', 2),
    ('물류 표준 사전', '창고코드',     '창고',   1), ('물류 표준 사전', '창고코드',     '코드', 2),
    ('물류 표준 사전', '입고일자',     '입고',   1), ('물류 표준 사전', '입고일자',     '일자', 2),
    ('물류 표준 사전', '재고수량',     '재고',   1), ('물류 표준 사전', '재고수량',     '수량', 2),
    ('물류 표준 사전', '운임금액',     '운임',   1), ('물류 표준 사전', '운임금액',     '금액', 2)
) AS m(dict_name, term_name, word_name, ordinal)
JOIN catalog_standard_dictionary d ON d.dict_name = m.dict_name
JOIN catalog_standard_term t ON t.dictionary_id = d.id AND t.term_name = m.term_name
JOIN catalog_standard_word w ON w.dictionary_id = d.id AND w.word_name = m.word_name
ON CONFLICT (term_id, word_id, ordinal) DO NOTHING;

-- 배송상태코드(3단어 조합): 배송 + 상태 + 코드
INSERT INTO catalog_standard_term_words (term_id, word_id, ordinal)
SELECT t.id, w.id, m.ordinal
FROM (VALUES
    ('배송', 1), ('상태', 2), ('코드', 3)
) AS m(word_name, ordinal)
JOIN catalog_standard_dictionary d ON d.dict_name = '물류 표준 사전'
JOIN catalog_standard_term t ON t.dictionary_id = d.id AND t.term_name = '배송상태코드'
JOIN catalog_standard_word w ON w.dictionary_id = d.id AND w.word_name = m.word_name
ON CONFLICT (term_id, word_id, ordinal) DO NOTHING;

COMMIT;

-- 시드 후 권장 작업:
--   1. 백엔드 재기동 없이 즉시 로그인 가능 (admin/admin 등)
--   2. 설정 > 임베딩 > 백필 실행 — 용어집 용어가 시맨틱/통합 검색에 노출됨
