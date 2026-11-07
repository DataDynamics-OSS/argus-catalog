# SPDX-License-Identifier: Apache-2.0
"""데이터 표준(Data Standards) API 엔드포인트.

표준 사전(Dictionary) 단위로 단어(Word)·도메인(Domain)·용어(Term)·코드 그룹·코드 값을
관리하고, 용어를 데이터셋 컬럼에 매핑해 표준 준수율(compliance) 을 계산하는 흐름을
제공한다. 모든 mutate 엔드포인트는 service 호출 후 명시적으로 ``session.commit()`` 한다.

권한:
- 사전·단어·도메인·코드·용어 편집 — ``standards.edit`` 명시 허용형 기능 권한
  (매트릭스에 설정이 없으면 admin 전용).
- 데이터셋 단위 매핑(자동 매핑·초기화·수동 매핑·기본 사전 선택) — 데이터셋 수정과 같은
  기준(데이터셋 생성자 또는 관리자).

로깅 정책:
- 정상 처리 결과(생성·수정·삭제 등)는 INFO 로 식별자/이름 한 줄 기록
  (실제 INFO 발생은 service 레이어에 집중되어 있고, 라우터는 404·비즈니스 거부 등
  예외 분기에만 WARNING 을 남긴다)
- 자원 없음(404), 잘못된 입력 분기는 WARNING
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import service as catalog_service
from app.core.auth import CurrentUser, TokenUser, assert_owner_or_admin
from app.core.database import get_session
from app.permissions.router import require_feature_granted
from app.standard import service
from app.standard.schemas import (
    AutoMapResult,
    CodeGroupCreate,
    CodeGroupResponse,
    CodeGroupUpdate,
    CodeValueCreate,
    CodeValueResponse,
    CodeValueUpdate,
    ComplianceStats,
    DatasetDictionarySelection,
    DatasetTermMapping,
    DictionaryCreate,
    DictionaryResponse,
    DictionaryUpdate,
    DomainCreate,
    DomainResponse,
    DomainUpdate,
    MorphemeResult,
    StandardUsage,
    TermCreate,
    TermMappingCreate,
    TermMappingResponse,
    TermResponse,
    TermUpdate,
    WordCreate,
    WordResponse,
    WordUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/standards", tags=["standards"])

STANDARDS_EDIT_FEATURE = "standards.edit"


async def _standards_editor(
    current: TokenUser = Depends(require_feature_granted(STANDARDS_EDIT_FEATURE)),
) -> TokenUser:
    """표준 사전 편집 권한 의존성 — 통과하면 현재 사용자(변경 이력의 변경자)를 돌려준다."""
    return current


async def _require_dataset_editor(current, session: AsyncSession, dataset_id: int) -> None:
    """데이터셋 단위 매핑 — 데이터셋 수정과 같은 기준(생성자 또는 관리자). 데이터셋이 없으면 404."""
    created_by = await catalog_service.get_dataset_created_by(session, dataset_id)
    # created_by 가 None 이면 데이터셋이 없거나(404) 생성자 정보가 없는 과거 데이터(admin 만 허용)다.
    if created_by is None and not await catalog_service.dataset_exists(session, dataset_id):
        logger.warning("매핑 대상 데이터셋 없음: dataset_id=%d", dataset_id)
        raise HTTPException(status_code=404, detail="데이터셋을 찾을 수 없습니다.")
    assert_owner_or_admin(current, created_by, "데이터셋")


def _http(e: service.StandardError) -> HTTPException:
    """비즈니스 규칙 위반(StandardError)을 같은 상태 코드의 HTTPException 으로 변환한다."""
    logger.warning("표준 요청 거부(%d): %s", e.status_code, e.message)
    return HTTPException(status_code=e.status_code, detail=e.message)


def _not_found(what: str, op: str, ident: int) -> HTTPException:
    """404 응답을 만들고 대상 없음을 WARNING 으로 남긴다."""
    logger.warning("%s 대상 없음: id=%d", op, ident)
    return HTTPException(status_code=404, detail=f"{what}을(를) 찾을 수 없습니다.")


# ---------------------------------------------------------------------------
# 사전
# ---------------------------------------------------------------------------

@router.post("/dictionaries", response_model=DictionaryResponse, status_code=201)
async def create_dictionary(data: DictionaryCreate, current: TokenUser = Depends(_standards_editor),
                            session: AsyncSession = Depends(get_session)):
    """표준 사전 생성. 이름이 중복되면 409."""
    try:
        result = await service.create_dictionary(session, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    await session.commit()
    return result


@router.get("/dictionaries", response_model=list[DictionaryResponse])
async def list_dictionaries(session: AsyncSession = Depends(get_session)):
    """전체 사전 목록을 이름 오름차순으로 반환한다."""
    logger.debug("GET 사전 목록")
    return await service.list_dictionaries(session)


@router.get("/dictionaries/{dict_id}", response_model=DictionaryResponse)
async def get_dictionary(dict_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 사전 단건 조회. 없으면 404."""
    result = await service.get_dictionary_response(session, dict_id)
    if not result:
        raise _not_found("사전", "get_dictionary", dict_id)
    return result


@router.put("/dictionaries/{dict_id}", response_model=DictionaryResponse)
async def update_dictionary(dict_id: int, data: DictionaryUpdate, current: TokenUser = Depends(_standards_editor),
                            session: AsyncSession = Depends(get_session)):
    """사전 필드 부분 갱신(보낸 필드만 반영)."""
    try:
        result = await service.update_dictionary(session, dict_id, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    if not result:
        raise _not_found("사전", "update_dictionary", dict_id)
    await session.commit()
    return result


@router.delete("/dictionaries/{dict_id}", status_code=204)
async def delete_dictionary(dict_id: int, current: TokenUser = Depends(_standards_editor),
                            session: AsyncSession = Depends(get_session)):
    """사전 삭제. 단어·도메인·용어·코드·컬럼 매핑까지 함께 제거된다."""
    if not await service.delete_dictionary(session, dict_id, current.username):
        raise _not_found("사전", "delete_dictionary", dict_id)
    await session.commit()


# ---------------------------------------------------------------------------
# 단어
# ---------------------------------------------------------------------------

@router.post("/words", response_model=WordResponse, status_code=201)
async def create_word(data: WordCreate, current: TokenUser = Depends(_standards_editor),
                      session: AsyncSession = Depends(get_session)):
    """표준 단어 생성. 사전 안에서 이름이 중복되면 409."""
    try:
        result = await service.create_word(session, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    await session.commit()
    return result


@router.get("/words", response_model=list[WordResponse])
async def list_words(
    dictionary_id: int = Query(...),
    word_type: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """단어 목록 조회. ``word_type`` (GENERAL/SUFFIX/PREFIX) 으로 추가 필터링 가능."""
    return await service.list_words(session, dictionary_id, word_type)


@router.get("/words/{word_id}", response_model=WordResponse)
async def get_word(word_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 단어 단건 조회."""
    result = await service.get_word_response(session, word_id)
    if not result:
        raise _not_found("단어", "get_word", word_id)
    return result


@router.get("/words/{word_id}/usage", response_model=StandardUsage)
async def get_word_usage(word_id: int, session: AsyncSession = Depends(get_session)):
    """이 단어로 구성된 용어 목록 — 삭제 전 영향 확인용."""
    result = await service.word_usage(session, word_id)
    if result is None:
        raise _not_found("단어", "get_word_usage", word_id)
    return result


@router.put("/words/{word_id}", response_model=WordResponse)
async def update_word(word_id: int, data: WordUpdate, current: TokenUser = Depends(_standards_editor),
                      session: AsyncSession = Depends(get_session)):
    """단어 필드 부분 갱신."""
    try:
        result = await service.update_word(session, word_id, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    if not result:
        raise _not_found("단어", "update_word", word_id)
    await session.commit()
    return result


@router.delete("/words/{word_id}", status_code=204)
async def delete_word(word_id: int, current: TokenUser = Depends(_standards_editor),
                      session: AsyncSession = Depends(get_session)):
    """단어 삭제. 이 단어를 포함하는 용어들의 구성 단어 연결도 함께 정리된다."""
    if not await service.delete_word(session, word_id, current.username):
        raise _not_found("단어", "delete_word", word_id)
    await session.commit()


# ---------------------------------------------------------------------------
# 도메인
# ---------------------------------------------------------------------------

@router.post("/domains", response_model=DomainResponse, status_code=201)
async def create_domain(data: DomainCreate, current: TokenUser = Depends(_standards_editor),
                        session: AsyncSession = Depends(get_session)):
    """표준 도메인 생성. 코드 그룹은 같은 사전 소속만 연결 가능."""
    try:
        result = await service.create_domain(session, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    await session.commit()
    return result


@router.get("/domains", response_model=list[DomainResponse])
async def list_domains(dictionary_id: int = Query(...), session: AsyncSession = Depends(get_session)):
    """사전 내 도메인 목록 조회."""
    return await service.list_domains(session, dictionary_id)


@router.get("/domains/{domain_id}", response_model=DomainResponse)
async def get_domain(domain_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 도메인 단건 조회."""
    result = await service.get_domain_response(session, domain_id)
    if not result:
        raise _not_found("도메인", "get_domain", domain_id)
    return result


@router.get("/domains/{domain_id}/usage", response_model=StandardUsage)
async def get_domain_usage(domain_id: int, session: AsyncSession = Depends(get_session)):
    """이 도메인을 쓰는 용어 목록 — 삭제 전 영향 확인용."""
    result = await service.domain_usage(session, domain_id)
    if result is None:
        raise _not_found("도메인", "get_domain_usage", domain_id)
    return result


@router.put("/domains/{domain_id}", response_model=DomainResponse)
async def update_domain(domain_id: int, data: DomainUpdate, current: TokenUser = Depends(_standards_editor),
                        session: AsyncSession = Depends(get_session)):
    """도메인 필드 부분 갱신."""
    try:
        result = await service.update_domain(session, domain_id, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    if not result:
        raise _not_found("도메인", "update_domain", domain_id)
    await session.commit()
    return result


@router.delete("/domains/{domain_id}", status_code=204)
async def delete_domain(domain_id: int, current: TokenUser = Depends(_standards_editor),
                        session: AsyncSession = Depends(get_session)):
    """도메인 삭제. 이 도메인을 참조하던 용어의 도메인은 비워진다."""
    if not await service.delete_domain(session, domain_id, current.username):
        raise _not_found("도메인", "delete_domain", domain_id)
    await session.commit()


# ---------------------------------------------------------------------------
# 코드 그룹 / 값
# ---------------------------------------------------------------------------

@router.post("/code-groups", response_model=CodeGroupResponse, status_code=201)
async def create_code_group(data: CodeGroupCreate, current: TokenUser = Depends(_standards_editor),
                            session: AsyncSession = Depends(get_session)):
    """코드 그룹 생성. 사전 안에서 이름이 중복되면 409."""
    try:
        result = await service.create_code_group(session, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    await session.commit()
    return result


@router.get("/code-groups", response_model=list[CodeGroupResponse])
async def list_code_groups(dictionary_id: int = Query(...), session: AsyncSession = Depends(get_session)):
    """사전 내 코드 그룹 목록 조회."""
    return await service.list_code_groups(session, dictionary_id)


@router.get("/code-groups/{group_id}", response_model=CodeGroupResponse)
async def get_code_group(group_id: int, session: AsyncSession = Depends(get_session)):
    """코드 그룹 단건 조회. 그룹에 속한 코드 값(``values``) 도 함께 반환된다."""
    result = await service.get_code_group_response(session, group_id)
    if not result:
        raise _not_found("코드 그룹", "get_code_group", group_id)
    return result


@router.get("/code-groups/{group_id}/usage", response_model=StandardUsage)
async def get_code_group_usage(group_id: int, session: AsyncSession = Depends(get_session)):
    """이 코드 그룹을 쓰는 도메인과 코드 값 수 — 삭제 전 영향 확인용."""
    result = await service.code_group_usage(session, group_id)
    if result is None:
        raise _not_found("코드 그룹", "get_code_group_usage", group_id)
    return result


@router.put("/code-groups/{group_id}", response_model=CodeGroupResponse)
async def update_code_group(group_id: int, data: CodeGroupUpdate, current: TokenUser = Depends(_standards_editor),
                            session: AsyncSession = Depends(get_session)):
    """코드 그룹 필드 부분 갱신."""
    try:
        result = await service.update_code_group(session, group_id, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    if not result:
        raise _not_found("코드 그룹", "update_code_group", group_id)
    await session.commit()
    return result


@router.delete("/code-groups/{group_id}", status_code=204)
async def delete_code_group(group_id: int, current: TokenUser = Depends(_standards_editor),
                            session: AsyncSession = Depends(get_session)):
    """코드 그룹 삭제. 코드 값은 함께 삭제되고, 이 그룹을 쓰던 도메인은 연결이 해제된다."""
    if not await service.delete_code_group(session, group_id, current.username):
        raise _not_found("코드 그룹", "delete_code_group", group_id)
    await session.commit()


@router.post("/code-groups/{group_id}/values", response_model=CodeValueResponse, status_code=201)
async def add_code_value(group_id: int, data: CodeValueCreate, current: TokenUser = Depends(_standards_editor),
                         session: AsyncSession = Depends(get_session)):
    """지정된 그룹에 코드 값 추가. 그룹 안에서 코드가 중복되면 409."""
    try:
        result = await service.add_code_value(session, group_id, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    await session.commit()
    return result


@router.put("/code-values/{value_id}", response_model=CodeValueResponse)
async def update_code_value(value_id: int, data: CodeValueUpdate, current: TokenUser = Depends(_standards_editor),
                            session: AsyncSession = Depends(get_session)):
    """코드 값 부분 갱신 (보낸 필드만 반영)."""
    try:
        result = await service.update_code_value(session, value_id, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    if not result:
        raise _not_found("코드 값", "update_code_value", value_id)
    await session.commit()
    return result


@router.delete("/code-values/{value_id}", status_code=204)
async def delete_code_value(value_id: int, current: TokenUser = Depends(_standards_editor),
                            session: AsyncSession = Depends(get_session)):
    """코드 값 삭제."""
    if not await service.delete_code_value(session, value_id, current.username):
        raise _not_found("코드 값", "delete_code_value", value_id)
    await session.commit()


# ---------------------------------------------------------------------------
# 용어 + 형태소 분석
# ---------------------------------------------------------------------------

@router.get("/terms/analyze", response_model=MorphemeResult)
async def analyze_term(
    dictionary_id: int = Query(...),
    term_name: str = Query(..., min_length=1, max_length=200),
    session: AsyncSession = Depends(get_session),
):
    """용어를 형태소 분석하여 단어 분해, 영문 약어, 도메인 추천, 금칙어 결과를 반환."""
    logger.debug("용어 형태소 분석: dictionary_id=%d term=%s", dictionary_id, term_name)
    return await service.analyze_term(session, dictionary_id, term_name)


@router.post("/terms", response_model=TermResponse, status_code=201)
async def create_term(data: TermCreate, current: TokenUser = Depends(_standards_editor),
                      session: AsyncSession = Depends(get_session)):
    """표준 용어 생성. 영문/약어/물리명이 비어 있으면 형태소 분석으로 자동 채워진다.
    금칙어가 포함되면 400, 이름·물리명이 사전 안에서 중복되면 409."""
    try:
        result = await service.create_term(session, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    await session.commit()
    return result


@router.get("/terms", response_model=list[TermResponse])
async def list_terms(
    dictionary_id: int = Query(...),
    search: str | None = Query(None, max_length=200),
    session: AsyncSession = Depends(get_session),
):
    """사전 내 용어 목록. ``search`` 가 주어지면 용어명·물리명·영문명에서 부분 일치 검색."""
    return await service.list_terms(session, dictionary_id, search)


@router.get("/terms/{term_id}", response_model=TermResponse)
async def get_term(term_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 용어 단건 조회. 구성 단어와 매핑 수 정보가 함께 채워진다."""
    result = await service.get_term_response(session, term_id)
    if not result:
        raise _not_found("용어", "get_term", term_id)
    return result


@router.get("/terms/{term_id}/usage", response_model=StandardUsage)
async def get_term_usage(term_id: int, session: AsyncSession = Depends(get_session)):
    """이 용어로 매핑된 컬럼 수 — 삭제 전 영향 확인용."""
    result = await service.term_usage(session, term_id)
    if result is None:
        raise _not_found("용어", "get_term_usage", term_id)
    return result


@router.put("/terms/{term_id}", response_model=TermResponse)
async def update_term(term_id: int, data: TermUpdate, current: TokenUser = Depends(_standards_editor),
                      session: AsyncSession = Depends(get_session)):
    """용어 필드 부분 갱신. 이름이 바뀌면 구성 단어를 다시 분석한다."""
    try:
        result = await service.update_term(session, term_id, data, current.username)
    except service.StandardError as e:
        raise _http(e) from e
    if not result:
        raise _not_found("용어", "update_term", term_id)
    await session.commit()
    return result


@router.delete("/terms/{term_id}", status_code=204)
async def delete_term(term_id: int, current: TokenUser = Depends(_standards_editor),
                      session: AsyncSession = Depends(get_session)):
    """용어 삭제. 구성 단어 연결과 컬럼 매핑도 함께 정리된다."""
    if not await service.delete_term(session, term_id, current.username):
        raise _not_found("용어", "delete_term", term_id)
    await session.commit()


# ---------------------------------------------------------------------------
# 용어-컬럼 매핑 (데이터셋 생성자 또는 관리자)
# ---------------------------------------------------------------------------

@router.post("/mappings", response_model=TermMappingResponse, status_code=201)
async def create_mapping(current: CurrentUser, data: TermMappingCreate, session: AsyncSession = Depends(get_session)):
    """용어 ↔ 데이터셋 컬럼 매핑 생성(수동). 자동 매핑은 ``/mappings/auto-map`` 사용."""
    await _require_dataset_editor(current, session, data.dataset_id)
    try:
        result = await service.create_term_mapping(session, data)
    except service.StandardError as e:
        raise _http(e) from e
    await session.commit()
    return result


@router.get("/mappings", response_model=list[TermMappingResponse])
async def list_mappings(
    term_id: int | None = Query(None),
    dataset_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """매핑 목록 조회. ``term_id`` 또는 ``dataset_id`` 로 필터 가능(미지정 시 전체)."""
    return await service.list_term_mappings(session, term_id, dataset_id)


@router.delete("/mappings/{mapping_id}", status_code=204)
async def delete_mapping(current: CurrentUser, mapping_id: int, session: AsyncSession = Depends(get_session)):
    """매핑 삭제."""
    m = await service.get_term_mapping(session, mapping_id)
    if not m:
        raise _not_found("매핑", "delete_mapping", mapping_id)
    await _require_dataset_editor(current, session, m.dataset_id)
    await service.delete_term_mapping(session, mapping_id)
    await session.commit()


@router.delete("/datasets/{dataset_id}/mappings", status_code=200)
async def reset_dataset_mappings(
    current: CurrentUser,
    dataset_id: int,
    dictionary_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 표준 용어 매핑을 일괄 초기화. ``dictionary_id`` 가 있으면 그 사전의 매핑만."""
    await _require_dataset_editor(current, session, dataset_id)
    logger.info(
        "데이터셋 용어 매핑 초기화 요청: dataset_id=%d dictionary_id=%s user=%s",
        dataset_id, dictionary_id, current.username,
    )
    deleted = await service.delete_dataset_term_mappings(session, dataset_id, dictionary_id)
    await session.commit()
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# 자동 매핑 + 데이터셋 용어 매핑
# ---------------------------------------------------------------------------

@router.post("/mappings/auto-map", response_model=AutoMapResult)
async def auto_map_dataset(
    current: CurrentUser,
    dictionary_id: int = Query(...),
    dataset_id: int = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 컬럼을 표준 용어와 자동 매핑한다.

    컬럼의 field_path와 용어의 physical_name을 비교하여
    MATCHED (타입 호환) 또는 VIOLATION (타입 불일치)으로 매핑. 더 이상 맞지 않는
    자동 매핑은 지우고, 물리명이 맞는 용어가 없는 컬럼의 수동 매핑(SIMILAR)은 유지한다
    (물리명이 맞는 용어가 있으면 기존 매핑을 그 용어의 MATCHED/VIOLATION 으로 갱신).
    """
    await _require_dataset_editor(current, session, dataset_id)
    logger.debug(
        "자동 매핑 요청: dataset_id=%d dictionary_id=%d user=%s",
        dataset_id, dictionary_id, current.username,
    )
    try:
        result = await service.auto_map_dataset(session, dictionary_id, dataset_id)
    except service.StandardError as e:
        raise _http(e) from e
    await session.commit()
    return result


@router.get("/mappings/dataset", response_model=DatasetTermMapping)
async def get_dataset_term_mapping(
    dictionary_id: int = Query(...),
    dataset_id: int = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 전체 컬럼-용어 매핑 현황을 조회한다."""
    return await service.get_dataset_term_mapping(session, dictionary_id, dataset_id)


# ---------------------------------------------------------------------------
# 표준 준수율(Compliance)
# ---------------------------------------------------------------------------

@router.get("/compliance", response_model=ComplianceStats)
async def get_compliance(
    dictionary_id: int = Query(...),
    dataset_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """표준 준수율. ``dataset_id`` 가 없으면 이 사전을 적용한 데이터셋들의 컬럼 기준."""
    logger.debug("GET 표준 준수율: dictionary_id=%d dataset_id=%s", dictionary_id, dataset_id)
    return await service.get_compliance_stats(session, dictionary_id, dataset_id)


# ---------------------------------------------------------------------------
# 변경 이력
# ---------------------------------------------------------------------------

@router.get("/change-logs")
async def list_change_logs(
    dictionary_id: int | None = Query(None),
    entity_type: str | None = Query(None),
    entity_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """표준 변경 이력 페이지네이션 조회 — ``{"items": [...], "total": n}``.
    ``dictionary_id`` 로 사전 단위, ``entity_type``/``entity_id`` 로 대상 단위 필터링."""
    return await service.list_change_logs(session, entity_type, entity_id, page, page_size, dictionary_id)


# ---------------------------------------------------------------------------
# 데이터셋별 기본 사전 (표준 용어 탭의 사전 선택 기억)
# ---------------------------------------------------------------------------

@router.get("/datasets/{dataset_id}/dictionary")
async def get_dataset_dictionary(
    dataset_id: int,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋에 저장된 기본 사전 ID 를 반환한다 (없으면 null)."""
    return {"dictionary_id": await service.get_dataset_dictionary(session, dataset_id)}


@router.put("/datasets/{dataset_id}/dictionary")
async def set_dataset_dictionary(
    current: CurrentUser,
    dataset_id: int,
    req: DatasetDictionarySelection,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 기본 사전 선택을 저장한다 (표준 용어 탭에서 호출)."""
    await _require_dataset_editor(current, session, dataset_id)
    try:
        await service.set_dataset_dictionary(session, dataset_id, req.dictionary_id)
    except service.StandardError as e:
        # WARNING 은 _http() 가 남기므로 여기서는 대상 식별용 DEBUG 만.
        logger.debug("set_dataset_dictionary 실패: dataset_id=%d", dataset_id)
        raise _http(e) from e
    await session.commit()
    return {"dictionary_id": req.dictionary_id}
