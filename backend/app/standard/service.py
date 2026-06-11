"""데이터 표준 서비스 레이어(비즈니스 로직).

표준 사전·단어·도메인·용어·코드(그룹/값) 의 CRUD, 형태소 분석, 자동 매핑,
표준 준수율(compliance) 계산까지를 담당한다. 라우터는 이 모듈의 함수를 호출하고
HTTP 변환·예외 매핑만 수행한다.

로깅 정책: 변경 동작(create/update/delete/auto-map) 은 INFO 로 결과를 한 줄 기록.
도메인 위반(예: 알 수 없는 단어 매칭 실패, 미지원 타입)은 WARNING 으로 남긴다.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.standard.models import (
    CodeGroup, CodeValue, StandardChangeLog, StandardDictionary,
    StandardDomain, StandardTerm, StandardTermWord, StandardWord,
    TermColumnMapping,
)
from app.standard.schemas import (
    AutoMapResult, CodeGroupCreate, CodeGroupResponse, CodeGroupUpdate,
    CodeValueCreate, CodeValueResponse, ColumnTermStatus, ComplianceStats,
    DatasetTermMapping, DictionaryCreate, DictionaryResponse, DictionaryUpdate,
    DomainCreate, DomainResponse, DomainUpdate, MorphemeResult, TermCreate,
    TermMappingCreate, TermMappingResponse, TermResponse, TermUpdate,
    TermWordInfo, WordCreate, WordResponse, WordUpdate,
)
from app.catalog.models import Dataset, DatasetSchema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 사전 CRUD
# ---------------------------------------------------------------------------

async def create_dictionary(session: AsyncSession, data: DictionaryCreate) -> DictionaryResponse:
    d = StandardDictionary(**data.model_dump())
    session.add(d)
    await session.flush()
    await session.refresh(d)
    logger.info("사전 생성: id=%d, name=%s", d.id, d.dict_name)
    return await _build_dict_response(session, d)


async def list_dictionaries(session: AsyncSession) -> list[DictionaryResponse]:
    rows = (await session.execute(
        select(StandardDictionary).order_by(StandardDictionary.dict_name)
    )).scalars().all()
    return [await _build_dict_response(session, d) for d in rows]


async def get_dictionary(session: AsyncSession, dict_id: int):
    return (await session.execute(
        select(StandardDictionary).where(StandardDictionary.id == dict_id)
    )).scalar_one_or_none()


async def update_dictionary(session: AsyncSession, dict_id: int, data: DictionaryUpdate):
    """사전 필드 부분 갱신. 변경된 필드 키 목록을 INFO 로 기록한다."""
    d = await get_dictionary(session, dict_id)
    if not d:
        return None
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(d, k, v)
    await session.flush()
    await session.refresh(d)
    logger.info("사전 수정: id=%d, fields=%s", d.id, list(changes.keys()))
    return await _build_dict_response(session, d)


async def delete_dictionary(session: AsyncSession, dict_id: int) -> bool:
    """사전 삭제. CASCADE 로 단어/도메인/용어/코드까지 함께 제거됨에 유의."""
    d = await get_dictionary(session, dict_id)
    if not d:
        return False
    name = d.dict_name
    await session.delete(d)
    await session.flush()
    logger.info("사전 삭제: id=%d, name=%s", dict_id, name)
    return True


async def _build_dict_response(session: AsyncSession, d: StandardDictionary) -> DictionaryResponse:
    wc = (await session.execute(select(func.count(StandardWord.id)).where(StandardWord.dictionary_id == d.id))).scalar() or 0
    dc = (await session.execute(select(func.count(StandardDomain.id)).where(StandardDomain.dictionary_id == d.id))).scalar() or 0
    tc = (await session.execute(select(func.count(StandardTerm.id)).where(StandardTerm.dictionary_id == d.id))).scalar() or 0
    cc = (await session.execute(select(func.count(CodeGroup.id)).where(CodeGroup.dictionary_id == d.id))).scalar() or 0
    return DictionaryResponse(
        id=d.id, dict_name=d.dict_name, description=d.description,
        version=d.version, status=d.status,
        effective_date=d.effective_date, expiry_date=d.expiry_date,
        created_by=d.created_by, created_at=d.created_at, updated_at=d.updated_at,
        word_count=wc, domain_count=dc, term_count=tc, code_group_count=cc,
    )


# ---------------------------------------------------------------------------
# 단어 CRUD
# ---------------------------------------------------------------------------

async def create_word(session: AsyncSession, data: WordCreate) -> WordResponse:
    w = StandardWord(**data.model_dump())
    session.add(w)
    await session.flush()
    await session.refresh(w)
    await _log_change(session, "WORD", w.id, "CREATE")
    logger.info("표준 단어 생성: id=%d, name=%s, type=%s", w.id, w.word_name, w.word_type)
    return WordResponse.model_validate(w)


async def list_words(session: AsyncSession, dictionary_id: int, word_type: str | None = None) -> list[WordResponse]:
    stmt = select(StandardWord).where(StandardWord.dictionary_id == dictionary_id)
    if word_type:
        stmt = stmt.where(StandardWord.word_type == word_type)
    stmt = stmt.order_by(StandardWord.word_name)
    return [WordResponse.model_validate(w) for w in (await session.execute(stmt)).scalars().all()]


async def get_word(session: AsyncSession, word_id: int):
    return (await session.execute(select(StandardWord).where(StandardWord.id == word_id))).scalar_one_or_none()


async def update_word(session: AsyncSession, word_id: int, data: WordUpdate) -> WordResponse | None:
    """단어 필드 부분 갱신. 변경된 필드별로 ``StandardChangeLog`` 에 변경 이력을 남긴다."""
    w = await get_word(session, word_id)
    if not w:
        return None
    changed_fields: list[str] = []
    for k, v in data.model_dump(exclude_unset=True).items():
        old = getattr(w, k)
        if old != v:
            await _log_change(session, "WORD", w.id, "UPDATE", k, str(old), str(v))
            changed_fields.append(k)
        setattr(w, k, v)
    await session.flush()
    await session.refresh(w)
    if changed_fields:
        logger.info("표준 단어 수정: id=%d, name=%s, fields=%s", w.id, w.word_name, changed_fields)
    return WordResponse.model_validate(w)


async def delete_word(session: AsyncSession, word_id: int) -> bool:
    w = await get_word(session, word_id)
    if not w:
        return False
    await _log_change(session, "WORD", w.id, "DELETE")
    logger.info("표준 단어 삭제: id=%d, name=%s", w.id, w.word_name)
    await session.delete(w)
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# 도메인 CRUD
# ---------------------------------------------------------------------------

async def create_domain(session: AsyncSession, data: DomainCreate) -> DomainResponse:
    d = StandardDomain(**data.model_dump())
    session.add(d)
    await session.flush()
    await session.refresh(d)
    await _log_change(session, "DOMAIN", d.id, "CREATE")
    logger.info("표준 도메인 생성: id=%d, name=%s, type=%s", d.id, d.domain_name, d.data_type)
    return await _build_domain_response(session, d)


async def list_domains(session: AsyncSession, dictionary_id: int) -> list[DomainResponse]:
    rows = (await session.execute(
        select(StandardDomain).where(StandardDomain.dictionary_id == dictionary_id)
        .order_by(StandardDomain.domain_name)
    )).scalars().all()
    return [await _build_domain_response(session, d) for d in rows]


async def get_domain(session: AsyncSession, domain_id: int):
    return (await session.execute(select(StandardDomain).where(StandardDomain.id == domain_id))).scalar_one_or_none()


async def update_domain(session: AsyncSession, domain_id: int, data: DomainUpdate) -> DomainResponse | None:
    """도메인 필드 부분 갱신. 변경된 필드는 change log 에 기록."""
    d = await get_domain(session, domain_id)
    if not d:
        return None
    changed_fields: list[str] = []
    for k, v in data.model_dump(exclude_unset=True).items():
        old = getattr(d, k)
        if old != v:
            await _log_change(session, "DOMAIN", d.id, "UPDATE", k, str(old), str(v))
            changed_fields.append(k)
        setattr(d, k, v)
    await session.flush()
    await session.refresh(d)
    if changed_fields:
        logger.info("표준 도메인 수정: id=%d, name=%s, fields=%s", d.id, d.domain_name, changed_fields)
    return await _build_domain_response(session, d)


async def delete_domain(session: AsyncSession, domain_id: int) -> bool:
    """도메인 삭제. 참조 중인 용어의 ``domain_id`` 는 NULL 로 떨어진다(ON DELETE SET NULL)."""
    d = await get_domain(session, domain_id)
    if not d:
        return False
    name = d.domain_name
    await _log_change(session, "DOMAIN", d.id, "DELETE")
    await session.delete(d)
    await session.flush()
    logger.info("표준 도메인 삭제: id=%d, name=%s", domain_id, name)
    return True


async def _build_domain_response(session: AsyncSession, d: StandardDomain) -> DomainResponse:
    cg_name = None
    if d.code_group_id:
        cg = (await session.execute(select(CodeGroup.group_name).where(CodeGroup.id == d.code_group_id))).scalar_one_or_none()
        cg_name = cg
    return DomainResponse(
        id=d.id, dictionary_id=d.dictionary_id, domain_name=d.domain_name,
        domain_group=d.domain_group, data_type=d.data_type,
        data_length=d.data_length, data_precision=d.data_precision, data_scale=d.data_scale,
        description=d.description, code_group_id=d.code_group_id, code_group_name=cg_name,
        status=d.status, created_at=d.created_at, updated_at=d.updated_at,
    )


# ---------------------------------------------------------------------------
# 코드 그룹 / 값 CRUD
# ---------------------------------------------------------------------------

async def create_code_group(session: AsyncSession, data: CodeGroupCreate) -> CodeGroupResponse:
    """코드 그룹 생성."""
    cg = CodeGroup(**data.model_dump())
    session.add(cg)
    await session.flush()
    await session.refresh(cg)
    await _log_change(session, "CODE_GROUP", cg.id, "CREATE")
    logger.info("코드 그룹 생성: id=%d, name=%s", cg.id, cg.group_name)
    return await _build_code_group_response(session, cg)


async def list_code_groups(session: AsyncSession, dictionary_id: int) -> list[CodeGroupResponse]:
    rows = (await session.execute(
        select(CodeGroup).where(CodeGroup.dictionary_id == dictionary_id).order_by(CodeGroup.group_name)
    )).scalars().all()
    return [await _build_code_group_response(session, cg) for cg in rows]


async def get_code_group(session: AsyncSession, group_id: int):
    return (await session.execute(select(CodeGroup).where(CodeGroup.id == group_id))).scalar_one_or_none()


async def add_code_value(session: AsyncSession, group_id: int, data: CodeValueCreate) -> CodeValueResponse:
    """기존 코드 그룹에 코드 값을 추가."""
    cv = CodeValue(code_group_id=group_id, **data.model_dump())
    session.add(cv)
    await session.flush()
    await session.refresh(cv)
    logger.info("코드 값 생성: id=%d, group_id=%d, value=%s", cv.id, group_id, cv.code_value)
    return CodeValueResponse.model_validate(cv)


async def update_code_group(session: AsyncSession, group_id: int, data) -> CodeGroupResponse | None:
    """코드 그룹 필드 부분 갱신."""
    cg = await get_code_group(session, group_id)
    if not cg:
        return None
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(cg, k, v)
    await session.flush()
    await session.refresh(cg)
    logger.info("코드 그룹 수정: id=%d, fields=%s", cg.id, list(changes.keys()))
    return await _build_code_group_response(session, cg)


async def delete_code_group(session: AsyncSession, group_id: int) -> bool:
    """코드 그룹 삭제(코드 값들은 CASCADE 로 함께 제거)."""
    cg = await get_code_group(session, group_id)
    if not cg:
        return False
    name = cg.group_name
    await session.delete(cg)
    await session.flush()
    logger.info("코드 그룹 삭제: id=%d, name=%s", group_id, name)
    return True


async def update_code_value(session: AsyncSession, value_id: int, data) -> CodeValueResponse | None:
    """코드 값 갱신."""
    cv = (await session.execute(select(CodeValue).where(CodeValue.id == value_id))).scalar_one_or_none()
    if not cv:
        return None
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(cv, k, v)
    await session.flush()
    await session.refresh(cv)
    logger.info("코드 값 수정: id=%d, fields=%s", cv.id, list(changes.keys()))
    return CodeValueResponse.model_validate(cv)


async def delete_code_value(session: AsyncSession, value_id: int) -> bool:
    """코드 값 삭제."""
    cv = (await session.execute(select(CodeValue).where(CodeValue.id == value_id))).scalar_one_or_none()
    if not cv:
        return False
    code = cv.code_value
    await session.delete(cv)
    await session.flush()
    logger.info("코드 값 삭제: id=%d, value=%s", value_id, code)
    return True


async def _build_code_group_response(session: AsyncSession, cg: CodeGroup) -> CodeGroupResponse:
    values = (await session.execute(
        select(CodeValue).where(CodeValue.code_group_id == cg.id).order_by(CodeValue.sort_order)
    )).scalars().all()
    return CodeGroupResponse(
        id=cg.id, dictionary_id=cg.dictionary_id, group_name=cg.group_name,
        group_english=cg.group_english, description=cg.description,
        status=cg.status, created_at=cg.created_at, updated_at=cg.updated_at,
        values=[CodeValueResponse.model_validate(v) for v in values],
    )


# ---------------------------------------------------------------------------
# 용어 CRUD + 형태소 분석
# ---------------------------------------------------------------------------

async def analyze_term(session: AsyncSession, dictionary_id: int, term_name: str) -> MorphemeResult:
    """용어를 표준 단어 사전에 대해 greedy longest-match 로 형태소 분해.

    분해된 단어, 자동 생성된 영문/약어/물리명, 추천 도메인을 함께 반환한다.
    일치하지 않는 부분은 ``unmatched_parts`` 로 분리해 UI 에서 강조한다.
    """
    words = (await session.execute(
        select(StandardWord)
        .where(StandardWord.dictionary_id == dictionary_id, StandardWord.status == "ACTIVE")
        .order_by(func.length(StandardWord.word_name).desc())
    )).scalars().all()

    word_map = {w.word_name: w for w in words}
    sorted_names = sorted(word_map.keys(), key=len, reverse=True)

    # greedy longest-match 분해
    remaining = term_name
    matched_words: list[tuple[int, StandardWord]] = []
    unmatched: list[str] = []
    ordinal = 1

    while remaining:
        found = False
        for name in sorted_names:
            if remaining.startswith(name):
                w = word_map[name]
                matched_words.append((ordinal, w))
                remaining = remaining[len(name):]
                ordinal += 1
                found = True
                break
        if not found:
            unmatched.append(remaining[0])
            remaining = remaining[1:]

    # 단어 사전에 매칭되지 않은 글자가 있으면 향후 단어 등록을 위해 흔적을 남긴다
    if unmatched:
        logger.warning(
            "용어 분석: 미매칭 부분: dict_id=%d, term=%s, unmatched=%s",
            dictionary_id, term_name, "".join(unmatched),
        )

    # 영문·약어·물리명을 자동 생성
    term_english = " ".join(w.word_english for _, w in matched_words)
    term_abbr = "_".join(w.word_abbr for _, w in matched_words)
    physical_name = term_abbr.lower()

    # 마지막 SUFFIX 단어 기준 도메인 추천
    recommended_domain = None
    suffix_words = [w for _, w in matched_words if w.word_type == "SUFFIX"]
    if suffix_words:
        last_suffix = suffix_words[-1]
        domain = (await session.execute(
            select(StandardDomain)
            .where(
                StandardDomain.dictionary_id == dictionary_id,
                StandardDomain.domain_name == last_suffix.word_name,
                StandardDomain.status == "ACTIVE",
            )
        )).scalar_one_or_none()
        if domain:
            recommended_domain = await _build_domain_response(session, domain)

    word_infos = [
        TermWordInfo(
            word_id=w.id, word_name=w.word_name, word_english=w.word_english,
            word_abbr=w.word_abbr, word_type=w.word_type, ordinal=o,
        )
        for o, w in matched_words
    ]

    return MorphemeResult(
        words=word_infos,
        term_english=term_english or term_name,
        term_abbr=term_abbr or term_name.upper(),
        physical_name=physical_name or term_name.lower(),
        recommended_domain=recommended_domain,
        unmatched_parts=unmatched,
    )


async def create_term(session: AsyncSession, data: TermCreate) -> TermResponse:
    """용어 생성. 영문/약어/물리명이 비어 있으면 ``analyze_term`` 으로 자동 채운다."""
    # 영문명·약어·물리명이 비어 있으면 형태소 분석으로 자동 생성
    if not data.term_english or not data.term_abbr or not data.physical_name:
        analysis = await analyze_term(session, data.dictionary_id, data.term_name)
        term_english = data.term_english or analysis.term_english
        term_abbr = data.term_abbr or analysis.term_abbr
        physical_name = data.physical_name or analysis.physical_name
        domain_id = data.domain_id
        if not domain_id and analysis.recommended_domain:
            domain_id = analysis.recommended_domain.id
        word_infos = analysis.words
    else:
        term_english = data.term_english
        term_abbr = data.term_abbr
        physical_name = data.physical_name
        domain_id = data.domain_id
        word_infos = []

    t = StandardTerm(
        dictionary_id=data.dictionary_id,
        term_name=data.term_name,
        term_english=term_english,
        term_abbr=term_abbr,
        physical_name=physical_name,
        domain_id=domain_id,
        description=data.description,
        created_by=data.created_by,
    )
    session.add(t)
    await session.flush()
    await session.refresh(t)

    # 용어를 구성 단어와 연결
    for wi in word_infos:
        tw = StandardTermWord(term_id=t.id, word_id=wi.word_id, ordinal=wi.ordinal)
        session.add(tw)
    await session.flush()

    await _log_change(session, "TERM", t.id, "CREATE")
    logger.info("표준 용어 생성: id=%d, name=%s, physical=%s", t.id, t.term_name, t.physical_name)
    return await _build_term_response(session, t)


async def list_terms(session: AsyncSession, dictionary_id: int, search: str | None = None) -> list[TermResponse]:
    stmt = select(StandardTerm).where(StandardTerm.dictionary_id == dictionary_id)
    if search:
        stmt = stmt.where(StandardTerm.term_name.ilike(f"%{search}%"))
    stmt = stmt.order_by(StandardTerm.term_name)
    rows = (await session.execute(stmt)).scalars().all()
    return [await _build_term_response(session, t) for t in rows]


async def get_term(session: AsyncSession, term_id: int):
    return (await session.execute(select(StandardTerm).where(StandardTerm.id == term_id))).scalar_one_or_none()


async def update_term(session: AsyncSession, term_id: int, data: TermUpdate) -> TermResponse | None:
    """용어 필드 부분 갱신. 변경된 필드는 change log 와 INFO 양쪽에 기록."""
    t = await get_term(session, term_id)
    if not t:
        return None
    changed_fields: list[str] = []
    for k, v in data.model_dump(exclude_unset=True).items():
        old = getattr(t, k)
        if old != v:
            await _log_change(session, "TERM", t.id, "UPDATE", k, str(old), str(v))
            changed_fields.append(k)
        setattr(t, k, v)
    await session.flush()
    await session.refresh(t)
    if changed_fields:
        logger.info("표준 용어 수정: id=%d, name=%s, fields=%s", t.id, t.term_name, changed_fields)
    return await _build_term_response(session, t)


async def delete_term(session: AsyncSession, term_id: int) -> bool:
    """용어 삭제. 구성 단어 매핑·컬럼 매핑은 CASCADE 로 함께 정리된다."""
    t = await get_term(session, term_id)
    if not t:
        return False
    name = t.term_name
    await _log_change(session, "TERM", t.id, "DELETE")
    await session.delete(t)
    await session.flush()
    logger.info("표준 용어 삭제: id=%d, name=%s", term_id, name)
    return True


async def _build_term_response(session: AsyncSession, t: StandardTerm) -> TermResponse:
    # 도메인 정보
    domain_name = domain_data_type = None
    if t.domain_id:
        d = await get_domain(session, t.domain_id)
        if d:
            domain_name = d.domain_name
            domain_data_type = d.data_type

    # 구성 단어
    tw_rows = (await session.execute(
        select(StandardTermWord, StandardWord)
        .join(StandardWord, StandardTermWord.word_id == StandardWord.id)
        .where(StandardTermWord.term_id == t.id)
        .order_by(StandardTermWord.ordinal)
    )).all()
    word_list = [
        TermWordInfo(
            word_id=w.id, word_name=w.word_name, word_english=w.word_english,
            word_abbr=w.word_abbr, word_type=w.word_type, ordinal=tw.ordinal,
        )
        for tw, w in tw_rows
    ]

    # 매핑 수
    mc = (await session.execute(
        select(func.count(TermColumnMapping.id)).where(TermColumnMapping.term_id == t.id)
    )).scalar() or 0

    return TermResponse(
        id=t.id, dictionary_id=t.dictionary_id, term_name=t.term_name,
        term_english=t.term_english, term_abbr=t.term_abbr, physical_name=t.physical_name,
        domain_id=t.domain_id, domain_name=domain_name, domain_data_type=domain_data_type,
        description=t.description, status=t.status, created_by=t.created_by,
        created_at=t.created_at, updated_at=t.updated_at, words=word_list, mapping_count=mc,
    )


# ---------------------------------------------------------------------------
# 용어-컬럼 매핑
# ---------------------------------------------------------------------------

async def create_term_mapping(session: AsyncSession, data: TermMappingCreate) -> TermMappingResponse:
    """수동 용어-컬럼 매핑 생성. 자동 매핑은 ``auto_map_dataset`` 사용."""
    m = TermColumnMapping(**data.model_dump())
    session.add(m)
    await session.flush()
    await session.refresh(m)
    logger.info(
        "용어 매핑 생성: id=%d, term_id=%d, schema_id=%d, type=%s",
        m.id, m.term_id, m.schema_id, m.mapping_type,
    )
    return await _build_mapping_response(session, m)


async def list_term_mappings(
    session: AsyncSession, term_id: int | None = None, dataset_id: int | None = None,
) -> list[TermMappingResponse]:
    stmt = select(TermColumnMapping)
    if term_id:
        stmt = stmt.where(TermColumnMapping.term_id == term_id)
    if dataset_id:
        stmt = stmt.where(TermColumnMapping.dataset_id == dataset_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [await _build_mapping_response(session, m) for m in rows]


async def delete_term_mapping(session: AsyncSession, mapping_id: int) -> bool:
    """용어-컬럼 매핑 삭제."""
    m = (await session.execute(select(TermColumnMapping).where(TermColumnMapping.id == mapping_id))).scalar_one_or_none()
    if not m:
        return False
    term_id, schema_id = m.term_id, m.schema_id
    await session.delete(m)
    await session.flush()
    logger.info("용어 매핑 삭제: id=%d, term_id=%d, schema_id=%d", mapping_id, term_id, schema_id)
    return True


async def delete_dataset_term_mappings(session: AsyncSession, dataset_id: int) -> int:
    """데이터셋의 용어 매핑을 일괄 삭제. UI 의 "매핑 초기화" 가 호출."""
    from sqlalchemy import delete as sql_delete
    result = await session.execute(
        sql_delete(TermColumnMapping).where(TermColumnMapping.dataset_id == dataset_id)
    )
    deleted = result.rowcount or 0
    await session.flush()
    logger.info("용어 매핑 초기화: dataset_id=%d, deleted=%d", dataset_id, deleted)
    return deleted


async def _build_mapping_response(session: AsyncSession, m: TermColumnMapping) -> TermMappingResponse:
    term_name = (await session.execute(select(StandardTerm.term_name).where(StandardTerm.id == m.term_id))).scalar_one_or_none()
    ds_name = (await session.execute(select(Dataset.name).where(Dataset.id == m.dataset_id))).scalar_one_or_none()
    col_name = (await session.execute(select(DatasetSchema.field_path).where(DatasetSchema.id == m.schema_id))).scalar_one_or_none()
    return TermMappingResponse(
        id=m.id, term_id=m.term_id, term_name=term_name,
        dataset_id=m.dataset_id, dataset_name=ds_name,
        schema_id=m.schema_id, column_name=col_name,
        mapping_type=m.mapping_type, created_at=m.created_at,
    )


# ---------------------------------------------------------------------------
# 표준 준수율(Compliance)
# ---------------------------------------------------------------------------

async def get_compliance_stats(
    session: AsyncSession, dictionary_id: int, dataset_id: int | None = None,
) -> ComplianceStats:
    """표준 준수율 계산. ``dataset_id`` 가 주어지면 단일 데이터셋, 아니면 전체 컬럼 대상."""
    # 전체 컬럼 수
    col_stmt = select(func.count(DatasetSchema.id))
    if dataset_id:
        col_stmt = col_stmt.where(DatasetSchema.dataset_id == dataset_id)
    total = (await session.execute(col_stmt)).scalar() or 0

    if total == 0:
        return ComplianceStats()

    # 타입별 매핑 컬럼 수
    map_stmt = (
        select(TermColumnMapping.mapping_type, func.count(TermColumnMapping.id))
        .join(StandardTerm, TermColumnMapping.term_id == StandardTerm.id)
        .where(StandardTerm.dictionary_id == dictionary_id)
    )
    if dataset_id:
        map_stmt = map_stmt.where(TermColumnMapping.dataset_id == dataset_id)
    map_stmt = map_stmt.group_by(TermColumnMapping.mapping_type)

    rows = (await session.execute(map_stmt)).all()
    matched = similar = violation = 0
    for mt, cnt in rows:
        if mt == "MATCHED":
            matched = cnt
        elif mt == "SIMILAR":
            similar = cnt
        elif mt == "VIOLATION":
            violation = cnt

    mapped_total = matched + similar + violation
    unmapped = total - mapped_total
    rate = (matched / total * 100) if total > 0 else 0.0

    return ComplianceStats(
        total_columns=total, matched=matched, similar=similar,
        violation=violation, unmapped=unmapped, compliance_rate=round(rate, 1),
    )


# ---------------------------------------------------------------------------
# 자동 매핑
# ---------------------------------------------------------------------------

async def auto_map_dataset(
    session: AsyncSession, dictionary_id: int, dataset_id: int,
) -> AutoMapResult:
    """데이터셋 컬럼을 표준 용어와 자동 매핑한다. ``physical_name`` 동치를 기준.

    매핑 로직:
    1. 컬럼의 field_path와 용어의 physical_name을 비교 (소문자 정규화)
    2. 정확히 일치하면:
       - 데이터 타입도 호환 → MATCHED
       - 데이터 타입 불일치 → VIOLATION
    3. 이미 매핑이 있으면 업데이트, 없으면 새로 생성
    """
    # 데이터셋의 모든 컬럼 조회
    columns = (await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id)
    )).scalars().all()

    # 사전의 모든 활성 용어 조회 (physical_name → term 맵)
    terms = (await session.execute(
        select(StandardTerm).where(
            StandardTerm.dictionary_id == dictionary_id,
            StandardTerm.status == "ACTIVE",
        )
    )).scalars().all()
    term_by_physical = {t.physical_name.lower(): t for t in terms}

    # 용어별 도메인 정보 캐시
    domain_cache: dict[int, StandardDomain | None] = {}
    for t in terms:
        if t.domain_id and t.domain_id not in domain_cache:
            domain_cache[t.domain_id] = (await session.execute(
                select(StandardDomain).where(StandardDomain.id == t.domain_id)
            )).scalar_one_or_none()

    # 기존 매핑 조회
    existing_mappings = (await session.execute(
        select(TermColumnMapping).where(TermColumnMapping.dataset_id == dataset_id)
    )).scalars().all()
    existing_by_schema = {m.schema_id: m for m in existing_mappings}

    result = AutoMapResult()

    for col in columns:
        col_name = col.field_path.lower()
        term = term_by_physical.get(col_name)

        if not term:
            result.unmapped += 1
            continue

        # 타입 호환성 확인
        mapping_type = "MATCHED"
        if term.domain_id:
            domain = domain_cache.get(term.domain_id)
            if domain:
                if not _types_compatible(col.field_type, col.native_type, domain):
                    mapping_type = "VIOLATION"

        if col.id in existing_by_schema:
            # 기존 매핑 업데이트
            existing = existing_by_schema[col.id]
            if existing.term_id != term.id or existing.mapping_type != mapping_type:
                existing.term_id = term.id
                existing.mapping_type = mapping_type
                result.updated += 1
        else:
            # 새 매핑 생성
            m = TermColumnMapping(
                term_id=term.id,
                dataset_id=dataset_id,
                schema_id=col.id,
                mapping_type=mapping_type,
            )
            session.add(m)
            result.created += 1

        if mapping_type == "MATCHED":
            result.matched += 1
        elif mapping_type == "VIOLATION":
            result.violation += 1

    await session.flush()
    logger.info("자동 매핑 완료: dataset_id=%d, created=%d, matched=%d, violation=%d, unmapped=%d",
                dataset_id, result.created, result.matched, result.violation, result.unmapped)
    return result


def _types_compatible(
    col_type: str | None, native_type: str | None, domain: StandardDomain,
) -> bool:
    """컬럼 데이터 타입이 도메인 타입과 호환되는지 확인. ``matched`` / ``violation`` 판정에 사용."""
    if not col_type:
        return True

    ct = (col_type or "").upper()
    dt = domain.data_type.upper()

    # 정확히 일치
    if ct == dt:
        return True

    # 호환 타입 그룹
    varchar_types = {"VARCHAR", "CHAR", "CHARACTER VARYING", "STRING", "TEXT", "NVARCHAR"}
    int_types = {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "NUMBER"}
    decimal_types = {"DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL", "DOUBLE PRECISION"}
    date_types = {"DATE", "TIMESTAMP", "DATETIME", "TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE"}

    for group in [varchar_types, int_types, decimal_types, date_types]:
        if ct in group and dt in group:
            return True

    return False


async def get_dataset_term_mapping(
    session: AsyncSession, dictionary_id: int, dataset_id: int,
) -> DatasetTermMapping:
    """데이터셋의 전체 컬럼별 매핑 현황(매핑된 용어 + 미매핑 컬럼)을 한 번에 반환."""
    columns = (await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id)
        .order_by(DatasetSchema.ordinal)
    )).scalars().all()

    # 이 데이터셋의 매핑 조회
    mappings = (await session.execute(
        select(TermColumnMapping)
        .join(StandardTerm, TermColumnMapping.term_id == StandardTerm.id)
        .where(
            TermColumnMapping.dataset_id == dataset_id,
            StandardTerm.dictionary_id == dictionary_id,
        )
    )).scalars().all()
    mapping_by_schema = {m.schema_id: m for m in mappings}

    # 용어 정보 캐시
    term_cache: dict[int, StandardTerm] = {}
    domain_cache: dict[int, StandardDomain | None] = {}
    for m in mappings:
        if m.term_id not in term_cache:
            t = (await session.execute(
                select(StandardTerm).where(StandardTerm.id == m.term_id)
            )).scalar_one_or_none()
            if t:
                term_cache[m.term_id] = t
                if t.domain_id and t.domain_id not in domain_cache:
                    domain_cache[t.domain_id] = (await session.execute(
                        select(StandardDomain).where(StandardDomain.id == t.domain_id)
                    )).scalar_one_or_none()

    col_statuses: list[ColumnTermStatus] = []
    matched = similar = violation = 0

    for col in columns:
        m = mapping_by_schema.get(col.id)
        if m and m.term_id in term_cache:
            term = term_cache[m.term_id]
            domain = domain_cache.get(term.domain_id) if term.domain_id else None
            col_statuses.append(ColumnTermStatus(
                schema_id=col.id,
                column_name=col.field_path,
                column_type=col.field_type,
                native_type=col.native_type,
                mapping_id=m.id,
                mapping_type=m.mapping_type,
                term_id=term.id,
                term_name=term.term_name,
                term_physical_name=term.physical_name,
                term_data_type=domain.data_type if domain else None,
                term_data_length=domain.data_length if domain else None,
            ))
            if m.mapping_type == "MATCHED":
                matched += 1
            elif m.mapping_type == "SIMILAR":
                similar += 1
            elif m.mapping_type == "VIOLATION":
                violation += 1
        else:
            col_statuses.append(ColumnTermStatus(
                schema_id=col.id,
                column_name=col.field_path,
                column_type=col.field_type,
                native_type=col.native_type,
            ))

    total = len(columns)
    unmapped = total - matched - similar - violation
    rate = (matched / total * 100) if total > 0 else 0.0

    return DatasetTermMapping(
        dataset_id=dataset_id,
        dictionary_id=dictionary_id,
        columns=col_statuses,
        compliance=ComplianceStats(
            total_columns=total, matched=matched, similar=similar,
            violation=violation, unmapped=unmapped, compliance_rate=round(rate, 1),
        ),
    )


# ---------------------------------------------------------------------------
# 변경 이력
# ---------------------------------------------------------------------------

async def _log_change(
    session: AsyncSession,
    entity_type: str, entity_id: int, change_type: str,
    field_name: str | None = None, old_value: str | None = None, new_value: str | None = None,
) -> None:
    log = StandardChangeLog(
        entity_type=entity_type, entity_id=entity_id, change_type=change_type,
        field_name=field_name, old_value=old_value, new_value=new_value,
    )
    session.add(log)


async def list_change_logs(
    session: AsyncSession, entity_type: str | None = None, entity_id: int | None = None,
    page: int = 1, page_size: int = 50,
) -> list[dict]:
    stmt = select(StandardChangeLog)
    if entity_type:
        stmt = stmt.where(StandardChangeLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(StandardChangeLog.entity_id == entity_id)
    stmt = stmt.order_by(StandardChangeLog.changed_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id, "entity_type": r.entity_type, "entity_id": r.entity_id,
            "change_type": r.change_type, "field_name": r.field_name,
            "old_value": r.old_value, "new_value": r.new_value,
            "changed_by": r.changed_by, "changed_at": r.changed_at.isoformat() if r.changed_at else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 데이터셋별 기본 사전 (표준 용어 탭의 사전 선택 기억)
#   catalog_dataset_properties 의 단일 키로 저장 — 스키마 변경 없이 dataset 단위 영속화.
# ---------------------------------------------------------------------------

DATASET_DICTIONARY_PROPERTY = "argus.standard_dictionary_id"


async def get_dataset_dictionary(session: AsyncSession, dataset_id: int) -> int | None:
    """데이터셋에 저장된 기본 사전 ID. 없거나 값이 손상됐으면 None."""
    from app.catalog.models import DatasetProperty

    val = (await session.execute(
        select(DatasetProperty.property_value).where(
            DatasetProperty.dataset_id == dataset_id,
            DatasetProperty.property_key == DATASET_DICTIONARY_PROPERTY,
        )
    )).scalar()
    if val is None:
        return None
    try:
        dict_id = int(val)
    except ValueError:
        return None
    # 사전이 삭제된 경우 None 으로 간주 (탭에서 첫 사전 기본 선택으로 폴백)
    exists = (await session.execute(
        select(StandardDictionary.id).where(StandardDictionary.id == dict_id)
    )).scalar()
    return dict_id if exists else None


async def set_dataset_dictionary(
    session: AsyncSession, dataset_id: int, dictionary_id: int,
) -> None:
    """데이터셋의 기본 사전 선택을 upsert. 사전/데이터셋 미존재 시 ValueError."""
    from app.catalog.models import DatasetProperty

    if not (await session.execute(
        select(StandardDictionary.id).where(StandardDictionary.id == dictionary_id)
    )).scalar():
        raise ValueError(f"사전을 찾을 수 없습니다: id={dictionary_id}")
    if not (await session.execute(
        select(Dataset.id).where(Dataset.id == dataset_id)
    )).scalar():
        raise ValueError(f"데이터셋을 찾을 수 없습니다: id={dataset_id}")

    row = (await session.execute(
        select(DatasetProperty).where(
            DatasetProperty.dataset_id == dataset_id,
            DatasetProperty.property_key == DATASET_DICTIONARY_PROPERTY,
        )
    )).scalars().first()
    if row:
        row.property_value = str(dictionary_id)
    else:
        session.add(DatasetProperty(
            dataset_id=dataset_id,
            property_key=DATASET_DICTIONARY_PROPERTY,
            property_value=str(dictionary_id),
        ))
    await session.commit()
    logger.info("데이터셋 기본 사전 설정: dataset=%d dict=%d", dataset_id, dictionary_id)
