"""데이터 표준 ORM 모델.

표준 사전, 단어, 도메인, 용어, 코드 그룹/값, 용어-컬럼 매핑, 변경 이력.
"""

from sqlalchemy import (
    Column, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)

from app.core.database import Base


class StandardDictionary(Base):
    """표준 사전. 여러 표준 분류를 동시에 운영하기 위한 컨테이너."""

    __tablename__ = "catalog_standard_dictionary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dict_name = Column(String(200), nullable=False, unique=True)
    description = Column(Text)
    version = Column(String(50))
    status = Column(String(20), nullable=False, default="ACTIVE")
    effective_date = Column(Date)
    expiry_date = Column(Date)
    created_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StandardWord(Base):
    """표준 단어. 용어를 구성하는 원자적 단위."""

    __tablename__ = "catalog_standard_word"
    __table_args__ = (UniqueConstraint("dictionary_id", "word_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dictionary_id = Column(Integer, ForeignKey("catalog_standard_dictionary.id", ondelete="CASCADE"), nullable=False)
    word_name = Column(String(100), nullable=False)       # 한글명: "고객"
    word_english = Column(String(100), nullable=False)     # 영문명: "Customer"
    word_abbr = Column(String(50), nullable=False)         # 영문약어: "CUST"
    description = Column(Text)
    word_type = Column(String(20), nullable=False, default="GENERAL")  # GENERAL, SUFFIX, PREFIX
    is_forbidden = Column(String(5), default="false")      # 금칙어 여부
    synonym_group_id = Column(Integer)                     # 이음동의어 그룹
    status = Column(String(20), nullable=False, default="ACTIVE")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CodeGroup(Base):
    """코드 그룹. 코드형 도메인의 허용 값 집합 정의."""

    __tablename__ = "catalog_code_group"
    __table_args__ = (UniqueConstraint("dictionary_id", "group_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dictionary_id = Column(Integer, ForeignKey("catalog_standard_dictionary.id", ondelete="CASCADE"), nullable=False)
    group_name = Column(String(200), nullable=False)
    group_english = Column(String(200))
    description = Column(Text)
    status = Column(String(20), nullable=False, default="ACTIVE")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CodeValue(Base):
    """코드 값. 코드 그룹에 속하는 개별 코드."""

    __tablename__ = "catalog_code_value"
    __table_args__ = (UniqueConstraint("code_group_id", "code_value"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    code_group_id = Column(Integer, ForeignKey("catalog_code_group.id", ondelete="CASCADE"), nullable=False)
    code_value = Column(String(100), nullable=False)       # "M"
    code_name = Column(String(200), nullable=False)        # "남성"
    code_english = Column(String(200))                     # "Male"
    description = Column(Text)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(String(5), default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class StandardDomain(Base):
    """표준 도메인. 데이터 타입 표준 정의."""

    __tablename__ = "catalog_standard_domain"
    __table_args__ = (UniqueConstraint("dictionary_id", "domain_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dictionary_id = Column(Integer, ForeignKey("catalog_standard_dictionary.id", ondelete="CASCADE"), nullable=False)
    domain_name = Column(String(100), nullable=False)      # "번호"
    domain_group = Column(String(100))                     # "문자형", "숫자형"
    data_type = Column(String(50), nullable=False)         # "VARCHAR"
    data_length = Column(Integer)                          # 20
    data_precision = Column(Integer)
    data_scale = Column(Integer)
    description = Column(Text)
    code_group_id = Column(Integer, ForeignKey("catalog_code_group.id", ondelete="SET NULL"))
    status = Column(String(20), nullable=False, default="ACTIVE")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StandardTerm(Base):
    """표준 용어. 단어의 조합 + 도메인 연결."""

    __tablename__ = "catalog_standard_term"
    __table_args__ = (UniqueConstraint("dictionary_id", "term_name"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dictionary_id = Column(Integer, ForeignKey("catalog_standard_dictionary.id", ondelete="CASCADE"), nullable=False)
    term_name = Column(String(200), nullable=False)        # "고객번호"
    term_english = Column(String(200), nullable=False)     # "Customer Number"
    term_abbr = Column(String(100), nullable=False)        # "CUST_NO"
    physical_name = Column(String(100), nullable=False)    # "cust_no"
    domain_id = Column(Integer, ForeignKey("catalog_standard_domain.id", ondelete="SET NULL"))
    description = Column(Text)
    status = Column(String(20), nullable=False, default="ACTIVE")
    created_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StandardTermWord(Base):
    """용어 구성 단어. 형태소 분해 결과."""

    __tablename__ = "catalog_standard_term_words"
    __table_args__ = (UniqueConstraint("term_id", "word_id", "ordinal"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    term_id = Column(Integer, ForeignKey("catalog_standard_term.id", ondelete="CASCADE"), nullable=False)
    word_id = Column(Integer, ForeignKey("catalog_standard_word.id", ondelete="CASCADE"), nullable=False)
    ordinal = Column(Integer, nullable=False)


class TermColumnMapping(Base):
    """표준 용어 ↔ 실제 컬럼 매핑."""

    __tablename__ = "catalog_term_column_mapping"
    __table_args__ = (UniqueConstraint("term_id", "schema_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    term_id = Column(Integer, ForeignKey("catalog_standard_term.id", ondelete="CASCADE"), nullable=False)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    schema_id = Column(Integer, ForeignKey("catalog_dataset_schemas.id", ondelete="CASCADE"), nullable=False)
    mapping_type = Column(String(20), nullable=False, default="MATCHED")  # MATCHED, SIMILAR, VIOLATION
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class StandardChangeLog(Base):
    """표준 데이터 변경 이력."""

    __tablename__ = "catalog_standard_change_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_type = Column(String(20), nullable=False)  # WORD, DOMAIN, TERM, CODE_GROUP, CODE_VALUE
    entity_id = Column(Integer, nullable=False)
    change_type = Column(String(20), nullable=False)  # CREATE, UPDATE, DELETE
    field_name = Column(String(100))
    old_value = Column(Text)
    new_value = Column(Text)
    changed_by = Column(String(200))
    changed_at = Column(DateTime(timezone=True), server_default=func.now())
