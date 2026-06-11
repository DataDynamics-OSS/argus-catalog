# SPDX-License-Identifier: Apache-2.0
"""데이터 품질 ORM 모델."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text, func

from app.core.database import Base


class DataProfile(Base):
    """데이터셋에 대한 원천 DB 프로파일링의 컬럼 단위 통계 스냅샷."""

    __tablename__ = "catalog_data_profile"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    row_count = Column(Integer, nullable=False, default=0)
    profile_json = Column(Text, nullable=False, default="[]")
    profiled_at = Column(DateTime(timezone=True), server_default=func.now())


class QualityRule(Base):
    """품질 검사 규칙 정의. 데이터셋에 대해 NOT_NULL, UNIQUE, MIN/MAX_VALUE, ROW_COUNT, FRESHNESS, ACCEPTED_VALUES, REGEX 를 지원한다."""

    __tablename__ = "catalog_quality_rule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    rule_name = Column(String(255), nullable=False)
    check_type = Column(String(50), nullable=False)       # NOT_NULL, UNIQUE, MIN_VALUE, MAX_VALUE, ACCEPTED_VALUES, REGEX, ROW_COUNT, FRESHNESS, CUSTOM_SQL
    column_name = Column(String(256))                      # 테이블 단위 검사면 NULL
    expected_value = Column(Text)                          # JSON: 기대값 또는 설정
    threshold = Column(Numeric(5, 2), default=100.00)      # 통과 임계값 %
    severity = Column(String(16), nullable=False, default="WARNING")
    is_active = Column(String(5), nullable=False, default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class QualityResult(Base):
    """단일 품질 규칙 평가의 실행 결과."""

    __tablename__ = "catalog_quality_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("catalog_quality_rule.id", ondelete="CASCADE"), nullable=False)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    passed = Column(String(5), nullable=False)
    actual_value = Column(Text)
    detail = Column(Text)
    failed_samples = Column(Text)  # 위반 행 샘플 JSON 배열 (배치 반입 시 최대 5행)
    checked_at = Column(DateTime(timezone=True), server_default=func.now())


class QualityScore(Base):
    """데이터셋의 집계 품질 점수. 특정 시점에 대해 score = passed/total * 100."""

    __tablename__ = "catalog_quality_score"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("catalog_datasets.id", ondelete="CASCADE"), nullable=False)
    score = Column(Numeric(5, 2), nullable=False, default=0)
    total_rules = Column(Integer, nullable=False, default=0)
    passed_rules = Column(Integer, nullable=False, default=0)
    warning_rules = Column(Integer, nullable=False, default=0)
    failed_rules = Column(Integer, nullable=False, default=0)
    scored_at = Column(DateTime(timezone=True), server_default=func.now())
