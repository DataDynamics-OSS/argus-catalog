# SPDX-License-Identifier: Apache-2.0
"""데이터 표준(Data Standards) API 엔드포인트.

표준 사전(Dictionary) 단위로 단어(Word)·도메인(Domain)·용어(Term)·코드 그룹·코드 값을
관리하고, 용어를 데이터셋 컬럼에 매핑해 표준 준수율(compliance) 을 계산하는 흐름을
제공한다. 모든 mutate 엔드포인트는 service 호출 후 명시적으로 ``session.commit()`` 한다.

로깅 정책:
- 정상 처리 결과(생성·수정·삭제 등)는 INFO 로 식별자/이름 한 줄 기록
  (실제 INFO 발생은 service 레이어에 집중되어 있고, 라우터는 404·비즈니스 거부 등
  예외 분기에만 WARNING 을 남긴다)
- 자원 없음(404), 잘못된 입력 분기는 WARNING
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser
from app.core.database import get_session
from app.standard import service
from app.standard.schemas import (
    AutoMapResult,
    CodeGroupCreate,
    CodeGroupResponse,
    CodeGroupUpdate,
    CodeValueCreate,
    CodeValueResponse,
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


# ---------------------------------------------------------------------------
# 사전
# ---------------------------------------------------------------------------

@router.post("/dictionaries", response_model=DictionaryResponse, status_code=201)
async def create_dictionary(_guard: AdminUser, data: DictionaryCreate, session: AsyncSession = Depends(get_session)):
    """표준 사전 생성. ``dict_name`` 은 UNIQUE 라 중복 시 DB 제약에서 차단된다."""
    result = await service.create_dictionary(session, data)
    await session.commit()
    return result


@router.get("/dictionaries", response_model=list[DictionaryResponse])
async def list_dictionaries(session: AsyncSession = Depends(get_session)):
    """전체 사전 목록을 이름 오름차순으로 반환한다."""
    return await service.list_dictionaries(session)


@router.get("/dictionaries/{dict_id}", response_model=DictionaryResponse)
async def get_dictionary(dict_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 사전 단건 조회. 없으면 404."""
    d = await service.get_dictionary(session, dict_id)
    if not d:
        logger.warning("get_dictionary 대상 없음: dict_id=%d", dict_id)
        raise HTTPException(status_code=404, detail="사전을 찾을 수 없습니다.")
    return await service._build_dict_response(session, d)


@router.put("/dictionaries/{dict_id}", response_model=DictionaryResponse)
async def update_dictionary(_guard: AdminUser, dict_id: int, data: DictionaryUpdate, session: AsyncSession = Depends(get_session)):
    """사전 필드 부분 갱신(``DictionaryUpdate`` 의 None 아닌 필드만 반영)."""
    result = await service.update_dictionary(session, dict_id, data)
    if not result:
        logger.warning("update_dictionary 대상 없음: dict_id=%d", dict_id)
        raise HTTPException(status_code=404, detail="사전을 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/dictionaries/{dict_id}", status_code=204)
async def delete_dictionary(_guard: AdminUser, dict_id: int, session: AsyncSession = Depends(get_session)):
    """사전 삭제. CASCADE 로 단어·도메인·용어·코드까지 함께 제거된다."""
    if not await service.delete_dictionary(session, dict_id):
        logger.warning("delete_dictionary 대상 없음: dict_id=%d", dict_id)
        raise HTTPException(status_code=404, detail="사전을 찾을 수 없습니다.")
    await session.commit()


# ---------------------------------------------------------------------------
# 단어
# ---------------------------------------------------------------------------

@router.post("/words", response_model=WordResponse, status_code=201)
async def create_word(_guard: AdminUser, data: WordCreate, session: AsyncSession = Depends(get_session)):
    """표준 단어 생성. ``(dictionary_id, word_name)`` UNIQUE."""
    result = await service.create_word(session, data)
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
    w = await service.get_word(session, word_id)
    if not w:
        logger.warning("get_word 대상 없음: word_id=%d", word_id)
        raise HTTPException(status_code=404, detail="단어를 찾을 수 없습니다.")
    return WordResponse.model_validate(w)


@router.put("/words/{word_id}", response_model=WordResponse)
async def update_word(_guard: AdminUser, word_id: int, data: WordUpdate, session: AsyncSession = Depends(get_session)):
    """단어 필드 부분 갱신."""
    result = await service.update_word(session, word_id, data)
    if not result:
        logger.warning("update_word 대상 없음: word_id=%d", word_id)
        raise HTTPException(status_code=404, detail="단어를 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/words/{word_id}", status_code=204)
async def delete_word(_guard: AdminUser, word_id: int, session: AsyncSession = Depends(get_session)):
    """단어 삭제. 이 단어를 포함하는 용어들의 ``StandardTermWord`` 매핑도 함께 정리된다."""
    if not await service.delete_word(session, word_id):
        logger.warning("delete_word 대상 없음: word_id=%d", word_id)
        raise HTTPException(status_code=404, detail="단어를 찾을 수 없습니다.")
    await session.commit()


# ---------------------------------------------------------------------------
# 도메인
# ---------------------------------------------------------------------------

@router.post("/domains", response_model=DomainResponse, status_code=201)
async def create_domain(_guard: AdminUser, data: DomainCreate, session: AsyncSession = Depends(get_session)):
    """표준 도메인 생성. ``data_type`` (예: VARCHAR/NUMBER) + 길이/정밀도/스케일 메타 포함."""
    result = await service.create_domain(session, data)
    await session.commit()
    return result


@router.get("/domains", response_model=list[DomainResponse])
async def list_domains(dictionary_id: int = Query(...), session: AsyncSession = Depends(get_session)):
    """사전 내 도메인 목록 조회."""
    return await service.list_domains(session, dictionary_id)


@router.get("/domains/{domain_id}", response_model=DomainResponse)
async def get_domain(domain_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 도메인 단건 조회."""
    d = await service.get_domain(session, domain_id)
    if not d:
        logger.warning("get_domain 대상 없음: domain_id=%d", domain_id)
        raise HTTPException(status_code=404, detail="도메인을 찾을 수 없습니다.")
    return await service._build_domain_response(session, d)


@router.put("/domains/{domain_id}", response_model=DomainResponse)
async def update_domain(_guard: AdminUser, domain_id: int, data: DomainUpdate, session: AsyncSession = Depends(get_session)):
    """도메인 필드 부분 갱신."""
    result = await service.update_domain(session, domain_id, data)
    if not result:
        logger.warning("update_domain 대상 없음: domain_id=%d", domain_id)
        raise HTTPException(status_code=404, detail="도메인을 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/domains/{domain_id}", status_code=204)
async def delete_domain(_guard: AdminUser, domain_id: int, session: AsyncSession = Depends(get_session)):
    """도메인 삭제. 이 도메인을 참조하는 용어의 ``domain_id`` 는 NULL 로 떨어진다."""
    if not await service.delete_domain(session, domain_id):
        logger.warning("delete_domain 대상 없음: domain_id=%d", domain_id)
        raise HTTPException(status_code=404, detail="도메인을 찾을 수 없습니다.")
    await session.commit()


# ---------------------------------------------------------------------------
# 코드 그룹 / 값
# ---------------------------------------------------------------------------

@router.post("/code-groups", response_model=CodeGroupResponse, status_code=201)
async def create_code_group(_guard: AdminUser, data: CodeGroupCreate, session: AsyncSession = Depends(get_session)):
    """코드 그룹 생성. ``(dictionary_id, group_name)`` UNIQUE."""
    result = await service.create_code_group(session, data)
    await session.commit()
    return result


@router.get("/code-groups", response_model=list[CodeGroupResponse])
async def list_code_groups(dictionary_id: int = Query(...), session: AsyncSession = Depends(get_session)):
    """사전 내 코드 그룹 목록 조회."""
    return await service.list_code_groups(session, dictionary_id)


@router.get("/code-groups/{group_id}", response_model=CodeGroupResponse)
async def get_code_group(group_id: int, session: AsyncSession = Depends(get_session)):
    """코드 그룹 단건 조회. 그룹에 속한 코드 값(``values``) 도 함께 반환된다."""
    cg = await service.get_code_group(session, group_id)
    if not cg:
        logger.warning("get_code_group 대상 없음: group_id=%d", group_id)
        raise HTTPException(status_code=404, detail="코드 그룹을 찾을 수 없습니다.")
    return await service._build_code_group_response(session, cg)


@router.put("/code-groups/{group_id}", response_model=CodeGroupResponse)
async def update_code_group(_guard: AdminUser, group_id: int, data: CodeGroupUpdate, session: AsyncSession = Depends(get_session)):
    """코드 그룹 필드 부분 갱신."""
    result = await service.update_code_group(session, group_id, data)
    if not result:
        logger.warning("update_code_group 대상 없음: group_id=%d", group_id)
        raise HTTPException(status_code=404, detail="코드 그룹을 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/code-groups/{group_id}", status_code=204)
async def delete_code_group(_guard: AdminUser, group_id: int, session: AsyncSession = Depends(get_session)):
    """코드 그룹 삭제. 그룹에 속한 코드 값들도 CASCADE 로 함께 삭제된다."""
    if not await service.delete_code_group(session, group_id):
        logger.warning("delete_code_group 대상 없음: group_id=%d", group_id)
        raise HTTPException(status_code=404, detail="코드 그룹을 찾을 수 없습니다.")
    await session.commit()


@router.post("/code-groups/{group_id}/values", response_model=CodeValueResponse, status_code=201)
async def add_code_value(_guard: AdminUser, group_id: int, data: CodeValueCreate, session: AsyncSession = Depends(get_session)):
    """지정된 그룹에 코드 값 추가."""
    cg = await service.get_code_group(session, group_id)
    if not cg:
        logger.warning("add_code_value: 상위 그룹 없음: group_id=%d", group_id)
        raise HTTPException(status_code=404, detail="코드 그룹을 찾을 수 없습니다.")
    result = await service.add_code_value(session, group_id, data)
    await session.commit()
    return result


@router.put("/code-values/{value_id}", response_model=CodeValueResponse)
async def update_code_value(_guard: AdminUser, value_id: int, data: CodeValueCreate, session: AsyncSession = Depends(get_session)):
    """코드 값 갱신."""
    result = await service.update_code_value(session, value_id, data)
    if not result:
        logger.warning("update_code_value 대상 없음: value_id=%d", value_id)
        raise HTTPException(status_code=404, detail="코드 값을 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/code-values/{value_id}", status_code=204)
async def delete_code_value(_guard: AdminUser, value_id: int, session: AsyncSession = Depends(get_session)):
    """코드 값 삭제."""
    if not await service.delete_code_value(session, value_id):
        logger.warning("delete_code_value 대상 없음: value_id=%d", value_id)
        raise HTTPException(status_code=404, detail="코드 값을 찾을 수 없습니다.")
    await session.commit()


# ---------------------------------------------------------------------------
# 용어 + 형태소 분석
# ---------------------------------------------------------------------------

@router.get("/terms/analyze", response_model=MorphemeResult)
async def analyze_term(
    dictionary_id: int = Query(...),
    term_name: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
):
    """용어를 형태소 분석하여 단어 분해, 영문 약어, 도메인 추천 결과를 반환."""
    return await service.analyze_term(session, dictionary_id, term_name)


@router.post("/terms", response_model=TermResponse, status_code=201)
async def create_term(_guard: AdminUser, data: TermCreate, session: AsyncSession = Depends(get_session)):
    """표준 용어 생성. 영문/약어/물리명이 비어 있으면 형태소 분석으로 자동 채워진다."""
    result = await service.create_term(session, data)
    await session.commit()
    return result


@router.get("/terms", response_model=list[TermResponse])
async def list_terms(
    dictionary_id: int = Query(...),
    search: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """사전 내 용어 목록. ``search`` 가 주어지면 이름/물리명에서 부분 일치 검색."""
    return await service.list_terms(session, dictionary_id, search)


@router.get("/terms/{term_id}", response_model=TermResponse)
async def get_term(term_id: int, session: AsyncSession = Depends(get_session)):
    """ID 로 용어 단건 조회. 구성 단어와 매핑 수 정보가 함께 채워진다."""
    t = await service.get_term(session, term_id)
    if not t:
        logger.warning("get_term 대상 없음: term_id=%d", term_id)
        raise HTTPException(status_code=404, detail="용어를 찾을 수 없습니다.")
    return await service._build_term_response(session, t)


@router.put("/terms/{term_id}", response_model=TermResponse)
async def update_term(_guard: AdminUser, term_id: int, data: TermUpdate, session: AsyncSession = Depends(get_session)):
    """용어 필드 부분 갱신."""
    result = await service.update_term(session, term_id, data)
    if not result:
        logger.warning("update_term 대상 없음: term_id=%d", term_id)
        raise HTTPException(status_code=404, detail="용어를 찾을 수 없습니다.")
    await session.commit()
    return result


@router.delete("/terms/{term_id}", status_code=204)
async def delete_term(_guard: AdminUser, term_id: int, session: AsyncSession = Depends(get_session)):
    """용어 삭제. ``StandardTermWord`` / ``TermColumnMapping`` 도 CASCADE 로 정리된다."""
    if not await service.delete_term(session, term_id):
        logger.warning("delete_term 대상 없음: term_id=%d", term_id)
        raise HTTPException(status_code=404, detail="용어를 찾을 수 없습니다.")
    await session.commit()


# ---------------------------------------------------------------------------
# 용어-컬럼 매핑
# ---------------------------------------------------------------------------

@router.post("/mappings", response_model=TermMappingResponse, status_code=201)
async def create_mapping(_guard: AdminUser, data: TermMappingCreate, session: AsyncSession = Depends(get_session)):
    """용어 ↔ 데이터셋 컬럼 매핑 생성(수동). 자동 매핑은 ``/mappings/auto-map`` 사용."""
    result = await service.create_term_mapping(session, data)
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
async def delete_mapping(_guard: AdminUser, mapping_id: int, session: AsyncSession = Depends(get_session)):
    """매핑 삭제."""
    if not await service.delete_term_mapping(session, mapping_id):
        logger.warning("delete_mapping 대상 없음: mapping_id=%d", mapping_id)
        raise HTTPException(status_code=404, detail="매핑을 찾을 수 없습니다.")
    await session.commit()


@router.delete("/datasets/{dataset_id}/mappings", status_code=200)
async def reset_dataset_mappings(_guard: AdminUser,
    dataset_id: int,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 표준 용어 매핑을 일괄 초기화."""
    deleted = await service.delete_dataset_term_mappings(session, dataset_id)
    await session.commit()
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# 자동 매핑 + 데이터셋 용어 매핑
# ---------------------------------------------------------------------------

@router.post("/mappings/auto-map", response_model=AutoMapResult)
async def auto_map_dataset(_guard: AdminUser,
    dictionary_id: int = Query(...),
    dataset_id: int = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 컬럼을 표준 용어와 자동 매핑한다.

    컬럼의 field_path와 용어의 physical_name을 비교하여
    MATCHED (타입 호환) 또는 VIOLATION (타입 불일치)으로 매핑.
    """
    result = await service.auto_map_dataset(session, dictionary_id, dataset_id)
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
    """표준 준수율을 계산한다."""
    return await service.get_compliance_stats(session, dictionary_id, dataset_id)


# ---------------------------------------------------------------------------
# 변경 이력
# ---------------------------------------------------------------------------

@router.get("/change-logs")
async def list_change_logs(
    entity_type: str | None = Query(None),
    entity_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """표준 변경 이력 페이지네이션 조회. ``entity_type``/``entity_id`` 로 대상 필터링."""
    return await service.list_change_logs(session, entity_type, entity_id, page, page_size)


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
async def set_dataset_dictionary(_guard: AdminUser,
    dataset_id: int,
    req: DatasetDictionarySelection,
    session: AsyncSession = Depends(get_session),
):
    """데이터셋의 기본 사전 선택을 저장한다 (표준 용어 탭에서 호출)."""
    try:
        await service.set_dataset_dictionary(session, dataset_id, req.dictionary_id)
    except ValueError as e:
        logger.warning("set_dataset_dictionary 실패: dataset_id=%d reason=%s", dataset_id, e)
        raise HTTPException(status_code=404, detail=str(e))
    return {"dictionary_id": req.dictionary_id}
