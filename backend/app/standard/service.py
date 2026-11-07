# SPDX-License-Identifier: Apache-2.0
"""데이터 표준 서비스 레이어(비즈니스 로직).

표준 사전·단어·도메인·용어·코드(그룹/값) 의 CRUD, 형태소 분석, 자동 매핑,
표준 준수율(compliance) 계산까지를 담당한다. 라우터는 이 모듈의 함수를 호출하고
HTTP 변환·예외 매핑만 수행한다.

무결성 원칙 (DB 스키마 변경 없이 앱 레벨에서 보장):
- 이름 중복은 DB UNIQUE 위반까지 가기 전에 사전 검사해 구체적인 409 로 알린다.
- 용어의 도메인, 도메인의 코드 그룹은 같은 사전 소속이어야 한다.
- 용어의 물리명은 사전 안에서 유일해야 한다(자동 매핑이 물리명 기준이므로).
- 한 컬럼은 한 사전 안에서 하나의 용어에만 매핑된다.
- 금칙어가 포함된 용어는 만들 수 없다(같은 이음동의어 그룹의 표준 단어를 제안).

변경 이력: 모든 변경(사전·단어·도메인·용어·코드 그룹·코드 값)을 변경자와 함께 남긴다.
CREATE/DELETE 항목의 값에는 ``{"dictionary_id": N, "name": ...}`` JSON 을 저장해,
대상이 삭제된 뒤에도 사전 단위로 이력을 걸러 볼 수 있게 한다.

로깅 정책: 변경 동작(create/update/delete/auto-map) 은 INFO 로 결과를 한 줄 기록.
도메인 위반(예: 알 수 없는 단어 매칭 실패, 미지원 타입)은 WARNING 으로 남긴다.
"""

import json
import logging
import re

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, DatasetSchema
from app.standard.models import (
    CodeGroup,
    CodeValue,
    StandardChangeLog,
    StandardDictionary,
    StandardDomain,
    StandardTerm,
    StandardTermWord,
    StandardWord,
    TermColumnMapping,
)
from app.standard.schemas import (
    AutoMapResult,
    ChangeLogEntry,
    CodeGroupCreate,
    CodeGroupResponse,
    CodeGroupUpdate,
    CodeValueCreate,
    CodeValueResponse,
    CodeValueUpdate,
    ColumnTermStatus,
    ComplianceStats,
    DatasetTermMapping,
    DictionaryCreate,
    DictionaryResponse,
    DictionaryUpdate,
    DomainCreate,
    DomainResponse,
    DomainUpdate,
    ForbiddenWordInfo,
    MorphemeResult,
    StandardUsage,
    TermCreate,
    TermMappingCreate,
    TermMappingResponse,
    TermResponse,
    TermUpdate,
    TermWordInfo,
    UsageRef,
    WordCreate,
    WordResponse,
    WordUpdate,
)

logger = logging.getLogger(__name__)


class StandardError(Exception):
    """비즈니스 규칙 위반 — 라우터가 ``status_code`` 그대로 HTTP 오류로 변환한다."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _like_escape(s: str) -> str:
    """LIKE 와일드카드(%, _)와 이스케이프 문자를 문자 그대로 매칭되도록 이스케이프한다."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _marker(dictionary_id: int, name: str) -> str:
    """CREATE/DELETE 이력 값 — 사전 단위 필터용 JSON (키 순서 고정)."""
    return json.dumps({"dictionary_id": dictionary_id, "name": name}, ensure_ascii=False)


def _marker_prefix(dictionary_id: int) -> str:
    """``_marker`` JSON 의 앞부분 — 이력 값 LIKE 'prefix%' 검색용(json.dumps 의 키 순서·공백과 일치해야 함)."""
    return '{"dictionary_id": %d,' % dictionary_id


async def _require_dictionary(session: AsyncSession, dictionary_id: int) -> StandardDictionary:
    """사전을 조회하고 없으면 StandardError(404)."""
    d = await get_dictionary(session, dictionary_id)
    if not d:
        raise StandardError("사전을 찾을 수 없습니다.", 404)
    return d


async def _ensure_unique(session: AsyncSession, column, value, scope: list, exclude_id_col=None,
                         exclude_id: int | None = None, message: str = "이미 있습니다.") -> None:
    """``column == value`` 인 행이 범위(scope) 안에 있으면 StandardError(409).

    수정 시에는 ``exclude_id_col != exclude_id`` 로 자기 자신을 제외한다.
    """
    stmt = select(func.count()).select_from(column.class_).where(column == value, *scope)
    if exclude_id is not None and exclude_id_col is not None:
        stmt = stmt.where(exclude_id_col != exclude_id)
    if (await session.execute(stmt)).scalar():
        raise StandardError(message, 409)


async def _apply_changes(session: AsyncSession, obj, changes: dict, entity_type: str, actor: str | None) -> list[str]:
    """필드 부분 갱신 + 변경 이력 기록. 실제로 바뀐 필드 목록을 반환."""
    changed: list[str] = []
    for k, v in changes.items():
        old = getattr(obj, k)
        if old != v:
            await _log_change(
                session, entity_type, obj.id, "UPDATE", k,
                None if old is None else str(old), None if v is None else str(v), actor,
            )
            changed.append(k)
            setattr(obj, k, v)
    return changed


async def _count_by(session: AsyncSession, key_col, count_expr, ids, *where) -> dict[int, int]:
    """``key_col IN ids`` 그룹별 개수 — 목록 응답의 N+1 쿼리를 한 번으로."""
    ids = list(ids)
    if not ids:
        return {}
    stmt = select(key_col, func.count(count_expr)).where(key_col.in_(ids), *where).group_by(key_col)
    return {k: c for k, c in (await session.execute(stmt)).all()}


# ---------------------------------------------------------------------------
# 사전 CRUD
# ---------------------------------------------------------------------------

def _dict_msg(name: str) -> str:
    return f"사전 '{name}' 이(가) 이미 있습니다."


async def create_dictionary(session: AsyncSession, data: DictionaryCreate, actor: str | None = None) -> DictionaryResponse:
    await _ensure_unique(session, StandardDictionary.dict_name, data.dict_name, [], message=_dict_msg(data.dict_name))
    d = StandardDictionary(**data.model_dump(), created_by=actor)
    session.add(d)
    await session.flush()
    await session.refresh(d)
    await _log_change(session, "DICTIONARY", d.id, "CREATE", new_value=_marker(d.id, d.dict_name), actor=actor)
    logger.info("사전 생성: id=%d, name=%s", d.id, d.dict_name)
    return (await _dict_responses(session, [d]))[0]


async def list_dictionaries(session: AsyncSession) -> list[DictionaryResponse]:
    """전체 사전을 이름순으로, 하위 항목 개수와 함께 반환한다."""
    rows = (await session.execute(
        select(StandardDictionary).order_by(StandardDictionary.dict_name)
    )).scalars().all()
    return await _dict_responses(session, list(rows))


async def get_dictionary(session: AsyncSession, dict_id: int):
    """사전 ORM 행(없으면 None)."""
    return (await session.execute(
        select(StandardDictionary).where(StandardDictionary.id == dict_id)
    )).scalar_one_or_none()


async def get_dictionary_response(session: AsyncSession, dict_id: int) -> DictionaryResponse | None:
    d = await get_dictionary(session, dict_id)
    return (await _dict_responses(session, [d]))[0] if d else None


async def update_dictionary(session: AsyncSession, dict_id: int, data: DictionaryUpdate, actor: str | None = None):
    """사전 필드 부분 갱신. 변경된 필드는 change log 에 기록."""
    d = await get_dictionary(session, dict_id)
    if not d:
        return None
    changes = data.model_dump(exclude_unset=True)
    if "dict_name" in changes and changes["dict_name"] != d.dict_name:
        await _ensure_unique(session, StandardDictionary.dict_name, changes["dict_name"], [],
                             StandardDictionary.id, d.id, _dict_msg(changes["dict_name"]))
    changed = await _apply_changes(session, d, changes, "DICTIONARY", actor)
    await session.flush()
    await session.refresh(d)
    if changed:
        logger.info("사전 수정: id=%d, fields=%s", d.id, changed)
    return (await _dict_responses(session, [d]))[0]


async def delete_dictionary(session: AsyncSession, dict_id: int, actor: str | None = None) -> bool:
    """사전 삭제. CASCADE 로 단어/도메인/용어/코드·컬럼 매핑까지 함께 제거됨에 유의."""
    d = await get_dictionary(session, dict_id)
    if not d:
        return False
    name = d.dict_name
    await _log_change(session, "DICTIONARY", d.id, "DELETE", old_value=_marker(d.id, name), actor=actor)
    await session.delete(d)
    await session.flush()
    logger.info("사전 삭제: id=%d, name=%s", dict_id, name)
    return True


async def _dict_responses(session: AsyncSession, dicts: list[StandardDictionary]) -> list[DictionaryResponse]:
    """사전 응답 — 단어·도메인·용어·코드 그룹·매핑 개수를 사전별 집계 쿼리로 채운다."""
    ids = [d.id for d in dicts]
    wc = await _count_by(session, StandardWord.dictionary_id, StandardWord.id, ids)
    dc = await _count_by(session, StandardDomain.dictionary_id, StandardDomain.id, ids)
    tc = await _count_by(session, StandardTerm.dictionary_id, StandardTerm.id, ids)
    cc = await _count_by(session, CodeGroup.dictionary_id, CodeGroup.id, ids)
    mc: dict[int, int] = {}
    if ids:
        mc = {k: c for k, c in (await session.execute(
            select(StandardTerm.dictionary_id, func.count(TermColumnMapping.id))
            .join(TermColumnMapping, TermColumnMapping.term_id == StandardTerm.id)
            .where(StandardTerm.dictionary_id.in_(ids))
            .group_by(StandardTerm.dictionary_id)
        )).all()}
    return [
        DictionaryResponse(
            id=d.id, dict_name=d.dict_name, description=d.description,
            version=d.version, status=d.status,
            effective_date=d.effective_date, expiry_date=d.expiry_date,
            created_by=d.created_by, created_at=d.created_at, updated_at=d.updated_at,
            word_count=wc.get(d.id, 0), domain_count=dc.get(d.id, 0), term_count=tc.get(d.id, 0),
            code_group_count=cc.get(d.id, 0), mapping_count=mc.get(d.id, 0),
        )
        for d in dicts
    ]


# ---------------------------------------------------------------------------
# 단어 CRUD
# ---------------------------------------------------------------------------

def _word_msg(name: str) -> str:
    return f"단어 '{name}' 이(가) 이미 이 사전에 있습니다."


async def create_word(session: AsyncSession, data: WordCreate, actor: str | None = None) -> WordResponse:
    await _require_dictionary(session, data.dictionary_id)
    await _ensure_unique(session, StandardWord.word_name, data.word_name,
                         [StandardWord.dictionary_id == data.dictionary_id], message=_word_msg(data.word_name))
    w = StandardWord(**data.model_dump())
    session.add(w)
    await session.flush()
    await session.refresh(w)
    await _log_change(session, "WORD", w.id, "CREATE", new_value=_marker(w.dictionary_id, w.word_name), actor=actor)
    logger.info("표준 단어 생성: id=%d, name=%s, type=%s", w.id, w.word_name, w.word_type)
    return (await _word_responses(session, [w]))[0]


async def list_words(session: AsyncSession, dictionary_id: int, word_type: str | None = None) -> list[WordResponse]:
    stmt = select(StandardWord).where(StandardWord.dictionary_id == dictionary_id)
    if word_type:
        stmt = stmt.where(StandardWord.word_type == word_type)
    stmt = stmt.order_by(StandardWord.word_name)
    return await _word_responses(session, list((await session.execute(stmt)).scalars().all()))


async def get_word(session: AsyncSession, word_id: int):
    return (await session.execute(select(StandardWord).where(StandardWord.id == word_id))).scalar_one_or_none()


async def get_word_response(session: AsyncSession, word_id: int) -> WordResponse | None:
    w = await get_word(session, word_id)
    return (await _word_responses(session, [w]))[0] if w else None


async def update_word(session: AsyncSession, word_id: int, data: WordUpdate, actor: str | None = None) -> WordResponse | None:
    """단어 필드 부분 갱신. 변경된 필드별로 ``StandardChangeLog`` 에 변경 이력을 남긴다."""
    w = await get_word(session, word_id)
    if not w:
        return None
    changes = data.model_dump(exclude_unset=True)
    if "word_name" in changes and changes["word_name"] != w.word_name:
        await _ensure_unique(session, StandardWord.word_name, changes["word_name"],
                             [StandardWord.dictionary_id == w.dictionary_id],
                             StandardWord.id, w.id, _word_msg(changes["word_name"]))
    changed = await _apply_changes(session, w, changes, "WORD", actor)
    await session.flush()
    await session.refresh(w)
    if changed:
        logger.info("표준 단어 수정: id=%d, name=%s, fields=%s", w.id, w.word_name, changed)
    return (await _word_responses(session, [w]))[0]


async def delete_word(session: AsyncSession, word_id: int, actor: str | None = None) -> bool:
    """단어 삭제. 이 단어를 쓰는 용어의 구성 단어 연결을 먼저 지운다(용어 자체는 유지)."""
    w = await get_word(session, word_id)
    if not w:
        return False
    await _log_change(session, "WORD", w.id, "DELETE", old_value=_marker(w.dictionary_id, w.word_name), actor=actor)
    logger.info("표준 단어 삭제: id=%d, name=%s", w.id, w.word_name)
    await session.execute(delete(StandardTermWord).where(StandardTermWord.word_id == w.id))
    await session.delete(w)
    await session.flush()
    return True


async def word_usage(session: AsyncSession, word_id: int) -> StandardUsage | None:
    """이 단어로 구성된 용어 목록. 단어가 없으면 None."""
    if not await get_word(session, word_id):
        return None
    rows = (await session.execute(
        select(StandardTerm.id, StandardTerm.term_name)
        .join(StandardTermWord, StandardTermWord.term_id == StandardTerm.id)
        .where(StandardTermWord.word_id == word_id)
        .distinct().order_by(StandardTerm.term_name)
    )).all()
    return StandardUsage(terms=[UsageRef(id=i, name=n) for i, n in rows])


async def _word_responses(session: AsyncSession, words: list[StandardWord]) -> list[WordResponse]:
    tc = await _count_by(session, StandardTermWord.word_id, func.distinct(StandardTermWord.term_id), [w.id for w in words])
    out = []
    for w in words:
        r = WordResponse.model_validate(w)
        r.term_count = tc.get(w.id, 0)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 도메인 CRUD
# ---------------------------------------------------------------------------

def _domain_msg(name: str) -> str:
    return f"도메인 '{name}' 이(가) 이미 이 사전에 있습니다."


async def _check_code_group_scope(session: AsyncSession, code_group_id: int | None, dictionary_id: int) -> None:
    """도메인에 연결할 코드 그룹이 같은 사전 소속인지 확인(아니면 400)."""
    if code_group_id is None:
        return
    cg = await get_code_group(session, code_group_id)
    if not cg or cg.dictionary_id != dictionary_id:
        raise StandardError("같은 사전의 코드 그룹만 연결할 수 있습니다.", 400)


async def create_domain(session: AsyncSession, data: DomainCreate, actor: str | None = None) -> DomainResponse:
    await _require_dictionary(session, data.dictionary_id)
    await _ensure_unique(session, StandardDomain.domain_name, data.domain_name,
                         [StandardDomain.dictionary_id == data.dictionary_id], message=_domain_msg(data.domain_name))
    await _check_code_group_scope(session, data.code_group_id, data.dictionary_id)
    d = StandardDomain(**data.model_dump())
    session.add(d)
    await session.flush()
    await session.refresh(d)
    await _log_change(session, "DOMAIN", d.id, "CREATE", new_value=_marker(d.dictionary_id, d.domain_name), actor=actor)
    logger.info("표준 도메인 생성: id=%d, name=%s, type=%s", d.id, d.domain_name, d.data_type)
    return (await _domain_responses(session, [d]))[0]


async def list_domains(session: AsyncSession, dictionary_id: int) -> list[DomainResponse]:
    rows = (await session.execute(
        select(StandardDomain).where(StandardDomain.dictionary_id == dictionary_id)
        .order_by(StandardDomain.domain_name)
    )).scalars().all()
    return await _domain_responses(session, list(rows))


async def get_domain(session: AsyncSession, domain_id: int):
    return (await session.execute(select(StandardDomain).where(StandardDomain.id == domain_id))).scalar_one_or_none()


async def get_domain_response(session: AsyncSession, domain_id: int) -> DomainResponse | None:
    d = await get_domain(session, domain_id)
    return (await _domain_responses(session, [d]))[0] if d else None


async def update_domain(session: AsyncSession, domain_id: int, data: DomainUpdate, actor: str | None = None) -> DomainResponse | None:
    """도메인 필드 부분 갱신. 변경된 필드는 change log 에 기록."""
    d = await get_domain(session, domain_id)
    if not d:
        return None
    changes = data.model_dump(exclude_unset=True)
    if "domain_name" in changes and changes["domain_name"] != d.domain_name:
        await _ensure_unique(session, StandardDomain.domain_name, changes["domain_name"],
                             [StandardDomain.dictionary_id == d.dictionary_id],
                             StandardDomain.id, d.id, _domain_msg(changes["domain_name"]))
    if "code_group_id" in changes:
        await _check_code_group_scope(session, changes["code_group_id"], d.dictionary_id)
    changed = await _apply_changes(session, d, changes, "DOMAIN", actor)
    await session.flush()
    await session.refresh(d)
    if changed:
        logger.info("표준 도메인 수정: id=%d, name=%s, fields=%s", d.id, d.domain_name, changed)
    return (await _domain_responses(session, [d]))[0]


async def delete_domain(session: AsyncSession, domain_id: int, actor: str | None = None) -> bool:
    """도메인 삭제. 참조 중인 용어의 ``domain_id`` 는 NULL 로 떨어진다."""
    d = await get_domain(session, domain_id)
    if not d:
        return False
    name = d.domain_name
    await _log_change(session, "DOMAIN", d.id, "DELETE", old_value=_marker(d.dictionary_id, name), actor=actor)
    # FK(ON DELETE SET NULL) 가 없는 환경에서도 같은 결과가 되도록 명시적으로 끊는다
    for t in (await session.execute(select(StandardTerm).where(StandardTerm.domain_id == d.id))).scalars().all():
        t.domain_id = None
    await session.delete(d)
    await session.flush()
    logger.info("표준 도메인 삭제: id=%d, name=%s", domain_id, name)
    return True


async def domain_usage(session: AsyncSession, domain_id: int) -> StandardUsage | None:
    """이 도메인을 쓰는 용어 목록. 도메인이 없으면 None."""
    if not await get_domain(session, domain_id):
        return None
    rows = (await session.execute(
        select(StandardTerm.id, StandardTerm.term_name)
        .where(StandardTerm.domain_id == domain_id).order_by(StandardTerm.term_name)
    )).all()
    return StandardUsage(terms=[UsageRef(id=i, name=n) for i, n in rows])


async def _domain_responses(session: AsyncSession, domains: list[StandardDomain]) -> list[DomainResponse]:
    cg_ids = {d.code_group_id for d in domains if d.code_group_id}
    cg_names: dict[int, str] = {}
    if cg_ids:
        cg_names = {i: n for i, n in (await session.execute(
            select(CodeGroup.id, CodeGroup.group_name).where(CodeGroup.id.in_(cg_ids))
        )).all()}
    tc = await _count_by(session, StandardTerm.domain_id, StandardTerm.id, [d.id for d in domains])
    return [
        DomainResponse(
            id=d.id, dictionary_id=d.dictionary_id, domain_name=d.domain_name,
            domain_group=d.domain_group, data_type=d.data_type,
            data_length=d.data_length, data_precision=d.data_precision, data_scale=d.data_scale,
            description=d.description, code_group_id=d.code_group_id,
            code_group_name=cg_names.get(d.code_group_id) if d.code_group_id else None,
            status=d.status, created_at=d.created_at, updated_at=d.updated_at,
            term_count=tc.get(d.id, 0),
        )
        for d in domains
    ]


# ---------------------------------------------------------------------------
# 코드 그룹 / 값 CRUD
# ---------------------------------------------------------------------------

def _group_msg(name: str) -> str:
    return f"코드 그룹 '{name}' 이(가) 이미 이 사전에 있습니다."


def _value_msg(code: str) -> str:
    return f"코드 '{code}' 이(가) 이미 이 그룹에 있습니다."


async def create_code_group(session: AsyncSession, data: CodeGroupCreate, actor: str | None = None) -> CodeGroupResponse:
    """코드 그룹 생성."""
    await _require_dictionary(session, data.dictionary_id)
    await _ensure_unique(session, CodeGroup.group_name, data.group_name,
                         [CodeGroup.dictionary_id == data.dictionary_id], message=_group_msg(data.group_name))
    cg = CodeGroup(**data.model_dump())
    session.add(cg)
    await session.flush()
    await session.refresh(cg)
    await _log_change(session, "CODE_GROUP", cg.id, "CREATE", new_value=_marker(cg.dictionary_id, cg.group_name), actor=actor)
    logger.info("코드 그룹 생성: id=%d, name=%s", cg.id, cg.group_name)
    return (await _code_group_responses(session, [cg]))[0]


async def list_code_groups(session: AsyncSession, dictionary_id: int) -> list[CodeGroupResponse]:
    rows = (await session.execute(
        select(CodeGroup).where(CodeGroup.dictionary_id == dictionary_id).order_by(CodeGroup.group_name)
    )).scalars().all()
    return await _code_group_responses(session, list(rows))


async def get_code_group(session: AsyncSession, group_id: int):
    return (await session.execute(select(CodeGroup).where(CodeGroup.id == group_id))).scalar_one_or_none()


async def get_code_group_response(session: AsyncSession, group_id: int) -> CodeGroupResponse | None:
    cg = await get_code_group(session, group_id)
    return (await _code_group_responses(session, [cg]))[0] if cg else None


async def update_code_group(session: AsyncSession, group_id: int, data: CodeGroupUpdate,
                            actor: str | None = None) -> CodeGroupResponse | None:
    """코드 그룹 필드 부분 갱신."""
    cg = await get_code_group(session, group_id)
    if not cg:
        return None
    changes = data.model_dump(exclude_unset=True)
    if "group_name" in changes and changes["group_name"] != cg.group_name:
        await _ensure_unique(session, CodeGroup.group_name, changes["group_name"],
                             [CodeGroup.dictionary_id == cg.dictionary_id],
                             CodeGroup.id, cg.id, _group_msg(changes["group_name"]))
    changed = await _apply_changes(session, cg, changes, "CODE_GROUP", actor)
    await session.flush()
    await session.refresh(cg)
    if changed:
        logger.info("코드 그룹 수정: id=%d, fields=%s", cg.id, changed)
    return (await _code_group_responses(session, [cg]))[0]


async def delete_code_group(session: AsyncSession, group_id: int, actor: str | None = None) -> bool:
    """코드 그룹 삭제(코드 값들도 함께 제거, 이 그룹을 쓰던 도메인은 연결 해제)."""
    cg = await get_code_group(session, group_id)
    if not cg:
        return False
    name = cg.group_name
    await _log_change(session, "CODE_GROUP", cg.id, "DELETE", old_value=_marker(cg.dictionary_id, name), actor=actor)
    for d in (await session.execute(select(StandardDomain).where(StandardDomain.code_group_id == cg.id))).scalars().all():
        d.code_group_id = None
    await session.execute(delete(CodeValue).where(CodeValue.code_group_id == cg.id))
    await session.delete(cg)
    await session.flush()
    logger.info("코드 그룹 삭제: id=%d, name=%s", group_id, name)
    return True


async def code_group_usage(session: AsyncSession, group_id: int) -> StandardUsage | None:
    """이 코드 그룹을 쓰는 도메인 목록과 코드 값 수. 그룹이 없으면 None."""
    if not await get_code_group(session, group_id):
        return None
    rows = (await session.execute(
        select(StandardDomain.id, StandardDomain.domain_name)
        .where(StandardDomain.code_group_id == group_id).order_by(StandardDomain.domain_name)
    )).all()
    vc = (await session.execute(select(func.count(CodeValue.id)).where(CodeValue.code_group_id == group_id))).scalar() or 0
    return StandardUsage(domains=[UsageRef(id=i, name=n) for i, n in rows], value_count=vc)


async def _get_code_value(session: AsyncSession, value_id: int):
    return (await session.execute(select(CodeValue).where(CodeValue.id == value_id))).scalar_one_or_none()


async def add_code_value(session: AsyncSession, group_id: int, data: CodeValueCreate,
                         actor: str | None = None) -> CodeValueResponse:
    """기존 코드 그룹에 코드 값을 추가."""
    cg = await get_code_group(session, group_id)
    if not cg:
        raise StandardError("코드 그룹을 찾을 수 없습니다.", 404)
    await _ensure_unique(session, CodeValue.code_value, data.code_value,
                         [CodeValue.code_group_id == group_id], message=_value_msg(data.code_value))
    cv = CodeValue(code_group_id=group_id, **data.model_dump())
    session.add(cv)
    await session.flush()
    await session.refresh(cv)
    await _log_change(session, "CODE_VALUE", cv.id, "CREATE",
                      new_value=_marker(cg.dictionary_id, f"{cg.group_name}/{cv.code_value}"), actor=actor)
    logger.info("코드 값 생성: id=%d, group_id=%d, value=%s", cv.id, group_id, cv.code_value)
    return CodeValueResponse.model_validate(cv)


async def update_code_value(session: AsyncSession, value_id: int, data: CodeValueUpdate,
                            actor: str | None = None) -> CodeValueResponse | None:
    """코드 값 갱신 (보낸 필드만)."""
    cv = await _get_code_value(session, value_id)
    if not cv:
        return None
    changes = data.model_dump(exclude_unset=True)
    if "code_value" in changes and changes["code_value"] != cv.code_value:
        await _ensure_unique(session, CodeValue.code_value, changes["code_value"],
                             [CodeValue.code_group_id == cv.code_group_id],
                             CodeValue.id, cv.id, _value_msg(changes["code_value"]))
    changed = await _apply_changes(session, cv, changes, "CODE_VALUE", actor)
    await session.flush()
    await session.refresh(cv)
    if changed:
        logger.info("코드 값 수정: id=%d, fields=%s", cv.id, changed)
    return CodeValueResponse.model_validate(cv)


async def delete_code_value(session: AsyncSession, value_id: int, actor: str | None = None) -> bool:
    """코드 값 삭제."""
    cv = await _get_code_value(session, value_id)
    if not cv:
        return False
    cg = await get_code_group(session, cv.code_group_id)
    code = cv.code_value
    if cg:
        await _log_change(session, "CODE_VALUE", cv.id, "DELETE",
                          old_value=_marker(cg.dictionary_id, f"{cg.group_name}/{code}"), actor=actor)
    await session.delete(cv)
    await session.flush()
    logger.info("코드 값 삭제: id=%d, value=%s", value_id, code)
    return True


async def _code_group_responses(session: AsyncSession, groups: list[CodeGroup]) -> list[CodeGroupResponse]:
    ids = [g.id for g in groups]
    values_by_group: dict[int, list[CodeValue]] = {i: [] for i in ids}
    if ids:
        for v in (await session.execute(
            select(CodeValue).where(CodeValue.code_group_id.in_(ids))
            .order_by(CodeValue.sort_order, CodeValue.code_value)
        )).scalars().all():
            values_by_group[v.code_group_id].append(v)
    dc = await _count_by(session, StandardDomain.code_group_id, StandardDomain.id, ids)
    return [
        CodeGroupResponse(
            id=cg.id, dictionary_id=cg.dictionary_id, group_name=cg.group_name,
            group_english=cg.group_english, description=cg.description,
            status=cg.status, created_at=cg.created_at, updated_at=cg.updated_at,
            values=[CodeValueResponse.model_validate(v) for v in values_by_group.get(cg.id, [])],
            domain_count=dc.get(cg.id, 0),
        )
        for cg in groups
    ]


# ---------------------------------------------------------------------------
# 용어 CRUD + 형태소 분석
# ---------------------------------------------------------------------------

async def analyze_term(session: AsyncSession, dictionary_id: int, term_name: str) -> MorphemeResult:
    """용어를 표준 단어 사전에 대해 greedy longest-match 로 형태소 분해.

    분해된 단어, 자동 생성된 영문/약어/물리명, 추천 도메인을 함께 반환한다.
    일치하지 않는 부분은 ``unmatched_parts`` 로, 금칙어는 대체어 제안과 함께
    ``forbidden_words`` 로 분리해 UI 에서 강조한다. 공백은 구분자로만 취급한다.
    """
    term_name = term_name.strip()
    words = (await session.execute(
        select(StandardWord)
        .where(StandardWord.dictionary_id == dictionary_id, StandardWord.status == "ACTIVE")
    )).scalars().all()

    word_map = {w.word_name: w for w in words}
    sorted_names = sorted(word_map.keys(), key=len, reverse=True)

    # greedy longest-match: 남은 문자열 앞에서 가장 긴 단어부터 맞춰 보고, 어느 단어와도
    # 맞지 않는 글자는 buf 에 모아 미매칭 조각으로 낸다.
    remaining = term_name
    matched_words: list[tuple[int, StandardWord]] = []
    unmatched: list[str] = []
    buf = ""
    ordinal = 1

    while remaining:
        if remaining[0].isspace():
            if buf:
                unmatched.append(buf)
                buf = ""
            remaining = remaining[1:]
            continue
        for name in sorted_names:
            if remaining.startswith(name):
                if buf:
                    unmatched.append(buf)
                    buf = ""
                matched_words.append((ordinal, word_map[name]))
                remaining = remaining[len(name):]
                ordinal += 1
                break
        else:
            buf += remaining[0]
            remaining = remaining[1:]
    if buf:
        unmatched.append(buf)

    # 단어 사전에 매칭되지 않은 부분이 있으면 향후 단어 등록을 위해 흔적을 남긴다
    if unmatched:
        logger.warning(
            "용어 분석: 미매칭 부분: dict_id=%d, term=%s, unmatched=%s",
            dictionary_id, term_name, unmatched,
        )

    # 금칙어 — 같은 이음동의어 그룹의 금칙어 아닌 단어를 대체어로 제안
    forbidden: list[ForbiddenWordInfo] = []
    for _, w in matched_words:
        if w.is_forbidden != "true" or any(f.word_name == w.word_name for f in forbidden):
            continue
        suggestion = None
        if w.synonym_group_id is not None:
            alts = sorted(
                (x for x in words
                 if x.synonym_group_id == w.synonym_group_id and x.is_forbidden != "true" and x.id != w.id),
                key=lambda x: x.word_name,
            )
            suggestion = alts[0].word_name if alts else None
        forbidden.append(ForbiddenWordInfo(word_name=w.word_name, suggestion=suggestion))

    # 영문·약어·물리명을 자동 생성 — 물리명은 약어를 소문자화하고 [a-z0-9_] 밖의 문자를 '_' 로 치환
    term_english = " ".join(w.word_english for _, w in matched_words)
    term_abbr = "_".join(w.word_abbr for _, w in matched_words)
    physical_name = re.sub(r"[^a-z0-9_]", "_", term_abbr.lower())
    if physical_name and physical_name[0].isdigit():
        physical_name = f"_{physical_name}"

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
        )).scalars().first()
        if domain:
            recommended_domain = (await _domain_responses(session, [domain]))[0]

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
        forbidden_words=forbidden,
    )


def _raise_if_forbidden(analysis: MorphemeResult) -> None:
    """분석 결과에 금칙어가 있으면 대체어 제안을 담아 StandardError(400)."""
    if not analysis.forbidden_words:
        return
    parts = [
        f"'{f.word_name}'" + (f" (→ '{f.suggestion}')" if f.suggestion else "")
        for f in analysis.forbidden_words
    ]
    raise StandardError(f"금칙어가 포함되어 있습니다: {', '.join(parts)}", 400)


def _term_msg(name: str) -> str:
    return f"용어 '{name}' 이(가) 이미 이 사전에 있습니다."


def _physical_msg(name: str) -> str:
    return f"물리명 '{name}' 을(를) 쓰는 용어가 이미 이 사전에 있습니다."


async def _check_domain_scope(session: AsyncSession, domain_id: int | None, dictionary_id: int) -> None:
    """용어에 연결할 도메인이 같은 사전 소속인지 확인(아니면 400)."""
    if domain_id is None:
        return
    d = await get_domain(session, domain_id)
    if not d or d.dictionary_id != dictionary_id:
        raise StandardError("같은 사전의 도메인만 연결할 수 있습니다.", 400)


async def _replace_term_words(session: AsyncSession, term_id: int, words: list[TermWordInfo]) -> None:
    """용어의 구성 단어 연결을 분석 결과로 통째로 교체한다."""
    await session.execute(delete(StandardTermWord).where(StandardTermWord.term_id == term_id))
    for wi in words:
        session.add(StandardTermWord(term_id=term_id, word_id=wi.word_id, ordinal=wi.ordinal))


async def create_term(session: AsyncSession, data: TermCreate, actor: str | None = None) -> TermResponse:
    """용어 생성. 구성 단어는 항상 형태소 분석으로 연결하고,
    영문/약어/물리명이 비어 있으면 분석 결과로 자동 채운다."""
    await _require_dictionary(session, data.dictionary_id)
    scope = [StandardTerm.dictionary_id == data.dictionary_id]
    await _ensure_unique(session, StandardTerm.term_name, data.term_name, scope, message=_term_msg(data.term_name))

    analysis = await analyze_term(session, data.dictionary_id, data.term_name)
    _raise_if_forbidden(analysis)
    term_english = (data.term_english or analysis.term_english)[:200]
    term_abbr = (data.term_abbr or analysis.term_abbr)[:100]
    physical_name = (data.physical_name or analysis.physical_name)[:100]
    domain_id = data.domain_id
    if domain_id is None and analysis.recommended_domain:
        domain_id = analysis.recommended_domain.id
    await _check_domain_scope(session, domain_id, data.dictionary_id)
    await _ensure_unique(session, StandardTerm.physical_name, physical_name, scope, message=_physical_msg(physical_name))

    t = StandardTerm(
        dictionary_id=data.dictionary_id,
        term_name=data.term_name,
        term_english=term_english,
        term_abbr=term_abbr,
        physical_name=physical_name,
        domain_id=domain_id,
        description=data.description,
        created_by=actor,
    )
    session.add(t)
    await session.flush()
    await session.refresh(t)

    await _replace_term_words(session, t.id, analysis.words)
    await session.flush()

    await _log_change(session, "TERM", t.id, "CREATE", new_value=_marker(t.dictionary_id, t.term_name), actor=actor)
    logger.info("표준 용어 생성: id=%d, name=%s, physical=%s", t.id, t.term_name, t.physical_name)
    return (await _term_responses(session, [t]))[0]


async def list_terms(session: AsyncSession, dictionary_id: int, search: str | None = None) -> list[TermResponse]:
    """용어 목록. ``search`` 는 용어명·물리명·영문명 부분 일치(%, _ 는 문자 그대로)."""
    stmt = select(StandardTerm).where(StandardTerm.dictionary_id == dictionary_id)
    if search and search.strip():
        pat = f"%{_like_escape(search.strip())}%"
        stmt = stmt.where(or_(
            StandardTerm.term_name.ilike(pat, escape="\\"),
            StandardTerm.physical_name.ilike(pat, escape="\\"),
            StandardTerm.term_english.ilike(pat, escape="\\"),
        ))
    stmt = stmt.order_by(StandardTerm.term_name)
    return await _term_responses(session, list((await session.execute(stmt)).scalars().all()))


async def get_term(session: AsyncSession, term_id: int):
    return (await session.execute(select(StandardTerm).where(StandardTerm.id == term_id))).scalar_one_or_none()


async def get_term_response(session: AsyncSession, term_id: int) -> TermResponse | None:
    t = await get_term(session, term_id)
    return (await _term_responses(session, [t]))[0] if t else None


async def update_term(session: AsyncSession, term_id: int, data: TermUpdate, actor: str | None = None) -> TermResponse | None:
    """용어 필드 부분 갱신. 이름이 바뀌면 금칙어를 검사하고 구성 단어를 다시 연결한다."""
    t = await get_term(session, term_id)
    if not t:
        return None
    changes = data.model_dump(exclude_unset=True)
    scope = [StandardTerm.dictionary_id == t.dictionary_id]
    analysis = None
    if "term_name" in changes and changes["term_name"] != t.term_name:
        await _ensure_unique(session, StandardTerm.term_name, changes["term_name"], scope,
                             StandardTerm.id, t.id, _term_msg(changes["term_name"]))
        analysis = await analyze_term(session, t.dictionary_id, changes["term_name"])
        _raise_if_forbidden(analysis)
    if "physical_name" in changes and changes["physical_name"] != t.physical_name:
        await _ensure_unique(session, StandardTerm.physical_name, changes["physical_name"], scope,
                             StandardTerm.id, t.id, _physical_msg(changes["physical_name"]))
    if "domain_id" in changes:
        await _check_domain_scope(session, changes["domain_id"], t.dictionary_id)
    changed = await _apply_changes(session, t, changes, "TERM", actor)
    if analysis is not None:
        await _replace_term_words(session, t.id, analysis.words)
    await session.flush()
    await session.refresh(t)
    if changed:
        logger.info("표준 용어 수정: id=%d, name=%s, fields=%s", t.id, t.term_name, changed)
    return (await _term_responses(session, [t]))[0]


async def delete_term(session: AsyncSession, term_id: int, actor: str | None = None) -> bool:
    """용어 삭제. 구성 단어 연결·컬럼 매핑도 함께 정리된다."""
    t = await get_term(session, term_id)
    if not t:
        return False
    name = t.term_name
    await _log_change(session, "TERM", t.id, "DELETE", old_value=_marker(t.dictionary_id, name), actor=actor)
    await session.execute(delete(StandardTermWord).where(StandardTermWord.term_id == t.id))
    await session.execute(delete(TermColumnMapping).where(TermColumnMapping.term_id == t.id))
    await session.delete(t)
    await session.flush()
    logger.info("표준 용어 삭제: id=%d, name=%s", term_id, name)
    return True


async def term_usage(session: AsyncSession, term_id: int) -> StandardUsage | None:
    """이 용어로 매핑된 컬럼 수. 용어가 없으면 None."""
    if not await get_term(session, term_id):
        return None
    mc = (await session.execute(
        select(func.count(TermColumnMapping.id)).where(TermColumnMapping.term_id == term_id)
    )).scalar() or 0
    return StandardUsage(mapping_count=mc)


async def _term_responses(session: AsyncSession, terms: list[StandardTerm]) -> list[TermResponse]:
    ids = [t.id for t in terms]
    domain_ids = {t.domain_id for t in terms if t.domain_id}
    domains: dict[int, StandardDomain] = {}
    if domain_ids:
        domains = {d.id: d for d in (await session.execute(
            select(StandardDomain).where(StandardDomain.id.in_(domain_ids))
        )).scalars().all()}
    words_by_term: dict[int, list[TermWordInfo]] = {i: [] for i in ids}
    if ids:
        for tw, w in (await session.execute(
            select(StandardTermWord, StandardWord)
            .join(StandardWord, StandardTermWord.word_id == StandardWord.id)
            .where(StandardTermWord.term_id.in_(ids))
            .order_by(StandardTermWord.term_id, StandardTermWord.ordinal)
        )).all():
            words_by_term[tw.term_id].append(TermWordInfo(
                word_id=w.id, word_name=w.word_name, word_english=w.word_english,
                word_abbr=w.word_abbr, word_type=w.word_type, ordinal=tw.ordinal,
            ))
    mc = await _count_by(session, TermColumnMapping.term_id, TermColumnMapping.id, ids)
    out = []
    for t in terms:
        d = domains.get(t.domain_id) if t.domain_id else None
        out.append(TermResponse(
            id=t.id, dictionary_id=t.dictionary_id, term_name=t.term_name,
            term_english=t.term_english, term_abbr=t.term_abbr, physical_name=t.physical_name,
            domain_id=t.domain_id, domain_name=d.domain_name if d else None,
            domain_data_type=d.data_type if d else None,
            description=t.description, status=t.status, created_by=t.created_by,
            created_at=t.created_at, updated_at=t.updated_at,
            words=words_by_term.get(t.id, []), mapping_count=mc.get(t.id, 0),
        ))
    return out


# ---------------------------------------------------------------------------
# 용어-컬럼 매핑
# ---------------------------------------------------------------------------

async def create_term_mapping(session: AsyncSession, data: TermMappingCreate) -> TermMappingResponse:
    """수동 용어-컬럼 매핑 생성. 자동 매핑은 ``auto_map_dataset`` 사용.

    컬럼은 해당 데이터셋 소속이어야 하고, 같은 사전 안에서 이미 매핑된 컬럼이면 409.
    """
    term = await get_term(session, data.term_id)
    if not term:
        raise StandardError("용어를 찾을 수 없습니다.", 404)
    col = (await session.execute(
        select(DatasetSchema).where(DatasetSchema.id == data.schema_id)
    )).scalar_one_or_none()
    if not col or col.dataset_id != data.dataset_id:
        raise StandardError("컬럼이 이 데이터셋에 속하지 않습니다.", 400)
    existing = (await session.execute(
        select(TermColumnMapping.id)
        .join(StandardTerm, TermColumnMapping.term_id == StandardTerm.id)
        .where(TermColumnMapping.schema_id == data.schema_id,
               StandardTerm.dictionary_id == term.dictionary_id)
    )).scalars().first()
    if existing:
        raise StandardError(f"컬럼 '{col.field_path}' 은(는) 이미 이 사전의 용어에 매핑되어 있습니다.", 409)
    m = TermColumnMapping(**data.model_dump())
    session.add(m)
    await session.flush()
    await session.refresh(m)
    logger.info(
        "용어 매핑 생성: id=%d, term_id=%d, schema_id=%d, type=%s",
        m.id, m.term_id, m.schema_id, m.mapping_type,
    )
    return (await _mapping_responses(session, [m]))[0]


async def list_term_mappings(
    session: AsyncSession, term_id: int | None = None, dataset_id: int | None = None,
) -> list[TermMappingResponse]:
    """매핑 목록. term_id·dataset_id 로 선택 필터(둘 다 없으면 전체)."""
    stmt = select(TermColumnMapping)
    if term_id:
        stmt = stmt.where(TermColumnMapping.term_id == term_id)
    if dataset_id:
        stmt = stmt.where(TermColumnMapping.dataset_id == dataset_id)
    stmt = stmt.order_by(TermColumnMapping.id)
    return await _mapping_responses(session, list((await session.execute(stmt)).scalars().all()))


async def get_term_mapping(session: AsyncSession, mapping_id: int):
    return (await session.execute(
        select(TermColumnMapping).where(TermColumnMapping.id == mapping_id)
    )).scalar_one_or_none()


async def delete_term_mapping(session: AsyncSession, mapping_id: int) -> bool:
    """용어-컬럼 매핑 삭제."""
    m = await get_term_mapping(session, mapping_id)
    if not m:
        return False
    term_id, schema_id = m.term_id, m.schema_id
    await session.delete(m)
    await session.flush()
    logger.info("용어 매핑 삭제: id=%d, term_id=%d, schema_id=%d", mapping_id, term_id, schema_id)
    return True


async def delete_dataset_term_mappings(session: AsyncSession, dataset_id: int, dictionary_id: int | None = None) -> int:
    """데이터셋의 용어 매핑을 일괄 삭제. ``dictionary_id`` 가 있으면 그 사전의 매핑만."""
    stmt = delete(TermColumnMapping).where(TermColumnMapping.dataset_id == dataset_id)
    if dictionary_id is not None:
        stmt = stmt.where(TermColumnMapping.term_id.in_(
            select(StandardTerm.id).where(StandardTerm.dictionary_id == dictionary_id)
        ))
    result = await session.execute(stmt.execution_options(synchronize_session=False))
    deleted = result.rowcount or 0
    await session.flush()
    logger.info("용어 매핑 초기화: dataset_id=%d, dictionary_id=%s, deleted=%d", dataset_id, dictionary_id, deleted)
    return deleted


async def _mapping_responses(session: AsyncSession, mappings: list[TermColumnMapping]) -> list[TermMappingResponse]:
    term_ids = {m.term_id for m in mappings}
    ds_ids = {m.dataset_id for m in mappings}
    col_ids = {m.schema_id for m in mappings}
    terms = {i: n for i, n in (await session.execute(
        select(StandardTerm.id, StandardTerm.term_name).where(StandardTerm.id.in_(term_ids))
    )).all()} if term_ids else {}
    dss = {i: n for i, n in (await session.execute(
        select(Dataset.id, Dataset.name).where(Dataset.id.in_(ds_ids))
    )).all()} if ds_ids else {}
    cols = {i: n for i, n in (await session.execute(
        select(DatasetSchema.id, DatasetSchema.field_path).where(DatasetSchema.id.in_(col_ids))
    )).all()} if col_ids else {}
    return [
        TermMappingResponse(
            id=m.id, term_id=m.term_id, term_name=terms.get(m.term_id),
            dataset_id=m.dataset_id, dataset_name=dss.get(m.dataset_id),
            schema_id=m.schema_id, column_name=cols.get(m.schema_id),
            mapping_type=m.mapping_type, created_at=m.created_at,
        )
        for m in mappings
    ]


# ---------------------------------------------------------------------------
# 표준 준수율(Compliance)
# ---------------------------------------------------------------------------

_TYPE_PRIORITY = {"MATCHED": 0, "SIMILAR": 1, "VIOLATION": 2}


def _best_by_schema(mappings) -> dict[int, TermColumnMapping]:
    """컬럼별 대표 매핑 하나 — 과거 중복 데이터가 있어도 한 컬럼을 두 번 세지 않게."""
    best: dict[int, TermColumnMapping] = {}
    for m in mappings:
        cur = best.get(m.schema_id)
        if cur is None or _TYPE_PRIORITY.get(m.mapping_type, 9) < _TYPE_PRIORITY.get(cur.mapping_type, 9):
            best[m.schema_id] = m
    return best


def _stats(total: int, mapping_types: list[str], dataset_count: int = 0) -> ComplianceStats:
    """준수율 통계 — compliance_rate 는 MATCHED 만 준수로 센다(전체 컬럼 대비 %)."""
    matched = mapping_types.count("MATCHED")
    similar = mapping_types.count("SIMILAR")
    violation = mapping_types.count("VIOLATION")
    rate = (matched / total * 100) if total > 0 else 0.0
    return ComplianceStats(
        total_columns=total, matched=matched, similar=similar, violation=violation,
        unmapped=max(total - matched - similar - violation, 0),
        compliance_rate=round(rate, 1), dataset_count=dataset_count,
    )


async def get_compliance_stats(
    session: AsyncSession, dictionary_id: int, dataset_id: int | None = None,
) -> ComplianceStats:
    """표준 준수율 계산.

    - ``dataset_id`` 지정: 그 데이터셋의 전체 컬럼 대비.
    - 미지정: 이 사전을 적용한 데이터셋(이 사전 용어로 매핑된 컬럼이 있거나 기본 사전으로
      지정된 데이터셋)의 컬럼 전체 대비. 사전과 무관한 카탈로그 전체 컬럼은 분모에 넣지 않는다.
    """
    from app.catalog.models import DatasetProperty

    if dataset_id:
        ds_ids = [dataset_id]
    else:
        mapped = (await session.execute(
            select(TermColumnMapping.dataset_id)
            .join(StandardTerm, TermColumnMapping.term_id == StandardTerm.id)
            .where(StandardTerm.dictionary_id == dictionary_id).distinct()
        )).scalars().all()
        selected = (await session.execute(
            select(DatasetProperty.dataset_id).where(
                DatasetProperty.property_key == DATASET_DICTIONARY_PROPERTY,
                DatasetProperty.property_value == str(dictionary_id),
            )
        )).scalars().all()
        ds_ids = sorted(set(mapped) | set(selected))
    if not ds_ids:
        logger.debug("준수율 대상 데이터셋 없음: dictionary_id=%d", dictionary_id)
        return ComplianceStats()

    total = (await session.execute(
        select(func.count(DatasetSchema.id)).where(DatasetSchema.dataset_id.in_(ds_ids))
    )).scalar() or 0
    if total == 0:
        return ComplianceStats(dataset_count=len(ds_ids))

    rows = (await session.execute(
        select(TermColumnMapping)
        .join(StandardTerm, TermColumnMapping.term_id == StandardTerm.id)
        .join(DatasetSchema, TermColumnMapping.schema_id == DatasetSchema.id)
        .where(StandardTerm.dictionary_id == dictionary_id, DatasetSchema.dataset_id.in_(ds_ids))
    )).scalars().all()
    best = _best_by_schema(rows)
    logger.debug(
        "준수율 계산: dictionary_id=%d datasets=%d columns=%d mapped=%d",
        dictionary_id, len(ds_ids), total, len(best),
    )
    return _stats(total, [m.mapping_type for m in best.values()], len(ds_ids))


# ---------------------------------------------------------------------------
# 자동 매핑
# ---------------------------------------------------------------------------

_AUTO_TYPES = {"MATCHED", "VIOLATION"}  # 자동 매핑이 만드는 유형 — SIMILAR 는 수동 매핑으로 보존


async def auto_map_dataset(
    session: AsyncSession, dictionary_id: int, dataset_id: int,
) -> AutoMapResult:
    """데이터셋 컬럼을 표준 용어와 자동 매핑한다. ``physical_name`` 동치를 기준.

    매핑 로직:
    1. 컬럼의 field_path와 용어의 physical_name을 비교 (소문자 정규화)
    2. 정확히 일치하면:
       - 데이터 타입도 호환 → MATCHED
       - 데이터 타입 불일치 → VIOLATION
    3. 이 사전의 기존 매핑이 있으면(유형 무관 — SIMILAR 포함) 첫 행을 업데이트하고 나머지 중복은 삭제,
       없으면 새로 생성
    4. 일치하는 용어가 없는 컬럼은 자동 매핑(MATCHED/VIOLATION)을 제거하고 수동 매핑(SIMILAR)은 하나만 유지
    다른 사전의 매핑은 건드리지 않는다.
    """
    await _require_dictionary(session, dictionary_id)
    if not (await session.execute(select(Dataset.id).where(Dataset.id == dataset_id))).scalar():
        raise StandardError("데이터셋을 찾을 수 없습니다.", 404)

    columns = (await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id)
    )).scalars().all()

    terms = (await session.execute(
        select(StandardTerm).where(
            StandardTerm.dictionary_id == dictionary_id,
            StandardTerm.status == "ACTIVE",
        )
    )).scalars().all()
    term_by_physical = {t.physical_name.lower(): t for t in terms}
    logger.debug(
        "자동 매핑 시작: dataset_id=%d dict_id=%d columns=%d terms=%d",
        dataset_id, dictionary_id, len(columns), len(terms),
    )

    domain_ids = {t.domain_id for t in terms if t.domain_id}
    domain_cache: dict[int, StandardDomain] = {}
    if domain_ids:
        domain_cache = {d.id: d for d in (await session.execute(
            select(StandardDomain).where(StandardDomain.id.in_(domain_ids))
        )).scalars().all()}

    # 이 사전의 기존 매핑만 대상
    existing_rows = (await session.execute(
        select(TermColumnMapping)
        .join(StandardTerm, TermColumnMapping.term_id == StandardTerm.id)
        .where(TermColumnMapping.dataset_id == dataset_id, StandardTerm.dictionary_id == dictionary_id)
        .order_by(TermColumnMapping.id)
    )).scalars().all()
    existing_by_schema: dict[int, list[TermColumnMapping]] = {}
    for m in existing_rows:
        existing_by_schema.setdefault(m.schema_id, []).append(m)

    result = AutoMapResult()

    for col in columns:
        term = term_by_physical.get((col.field_path or "").lower())
        existing = existing_by_schema.get(col.id, [])

        if not term:
            kept = None
            for m in existing:
                if m.mapping_type in _AUTO_TYPES or kept is not None:
                    await session.delete(m)
                    result.removed += 1
                else:
                    kept = m
            if kept is not None:
                result.similar += 1
            else:
                result.unmapped += 1
            continue

        mapping_type = "MATCHED"
        domain = domain_cache.get(term.domain_id) if term.domain_id else None
        if domain and not _types_compatible(col.field_type, col.native_type, domain):
            mapping_type = "VIOLATION"

        if existing:
            primary, extras = existing[0], existing[1:]
            # 같은 사전 안에서 중복된 매핑(과거 데이터)은 하나로 정리
            for m in extras:
                await session.delete(m)
                result.removed += 1
            if extras:
                await session.flush()
            if primary.term_id != term.id or primary.mapping_type != mapping_type:
                primary.term_id = term.id
                primary.mapping_type = mapping_type
                result.updated += 1
        else:
            session.add(TermColumnMapping(
                term_id=term.id, dataset_id=dataset_id, schema_id=col.id, mapping_type=mapping_type,
            ))
            result.created += 1

        if mapping_type == "MATCHED":
            result.matched += 1
        else:
            result.violation += 1

    await session.flush()
    logger.info(
        "자동 매핑 완료: dataset_id=%d, dict_id=%d, created=%d, updated=%d, removed=%d, "
        "matched=%d, violation=%d, unmapped=%d",
        dataset_id, dictionary_id, result.created, result.updated, result.removed,
        result.matched, result.violation, result.unmapped,
    )
    return result


_TYPE_GROUPS = [
    {"VARCHAR", "VARCHAR2", "NVARCHAR", "NVARCHAR2", "CHAR", "NCHAR", "CHARACTER", "CHARACTER VARYING",
     "STRING", "TEXT", "CLOB"},
    {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT", "NUMBER", "LONG", "SHORT", "BYTE"},
    {"DECIMAL", "NUMERIC", "NUMBER", "FLOAT", "DOUBLE", "REAL", "DOUBLE PRECISION"},
    {"DATE", "TIMESTAMP", "DATETIME", "TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP WITHOUT TIME ZONE"},
    {"BOOLEAN", "BOOL", "BIT"},
]


def _base_type(t: str | None) -> str:
    """'varchar(100)' → 'VARCHAR' 처럼 길이·정밀도 표기를 지운 대문자 타입명."""
    return re.sub(r"\s+", " ", re.sub(r"\(.*?\)", "", t or "")).strip().upper()


def _types_compatible(
    col_type: str | None, native_type: str | None, domain: StandardDomain,
) -> bool:
    """컬럼 데이터 타입이 도메인 타입과 호환되는지 확인. ``matched`` / ``violation`` 판정에 사용.

    정규화 타입(field_type)과 원본 타입(native_type) 중 하나라도 호환되면 호환으로 본다.
    """
    dt = _base_type(domain.data_type)
    candidates = [c for c in (_base_type(col_type), _base_type(native_type)) if c]
    if not candidates:
        return True
    for ct in candidates:
        if ct == dt or any(ct in g and dt in g for g in _TYPE_GROUPS):
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

    mappings = (await session.execute(
        select(TermColumnMapping)
        .join(StandardTerm, TermColumnMapping.term_id == StandardTerm.id)
        .where(
            TermColumnMapping.dataset_id == dataset_id,
            StandardTerm.dictionary_id == dictionary_id,
        )
    )).scalars().all()
    mapping_by_schema = _best_by_schema(mappings)

    term_ids = {m.term_id for m in mappings}
    term_cache: dict[int, StandardTerm] = {}
    if term_ids:
        term_cache = {t.id: t for t in (await session.execute(
            select(StandardTerm).where(StandardTerm.id.in_(term_ids))
        )).scalars().all()}
    domain_ids = {t.domain_id for t in term_cache.values() if t.domain_id}
    domain_cache: dict[int, StandardDomain] = {}
    if domain_ids:
        domain_cache = {d.id: d for d in (await session.execute(
            select(StandardDomain).where(StandardDomain.id.in_(domain_ids))
        )).scalars().all()}

    col_statuses: list[ColumnTermStatus] = []
    types: list[str] = []

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
            types.append(m.mapping_type)
        else:
            col_statuses.append(ColumnTermStatus(
                schema_id=col.id,
                column_name=col.field_path,
                column_type=col.field_type,
                native_type=col.native_type,
            ))

    return DatasetTermMapping(
        dataset_id=dataset_id,
        dictionary_id=dictionary_id,
        columns=col_statuses,
        compliance=_stats(len(columns), types, 1),
    )


# ---------------------------------------------------------------------------
# 변경 이력
# ---------------------------------------------------------------------------

async def _log_change(
    session: AsyncSession,
    entity_type: str, entity_id: int, change_type: str,
    field_name: str | None = None, old_value: str | None = None, new_value: str | None = None,
    actor: str | None = None,
) -> None:
    """변경 이력 1건을 세션에 추가한다(commit 은 호출자 트랜잭션과 함께)."""
    session.add(StandardChangeLog(
        entity_type=entity_type, entity_id=entity_id, change_type=change_type,
        field_name=field_name, old_value=old_value, new_value=new_value, changed_by=actor,
    ))


def _dictionary_scope(dictionary_id: int):
    """사전 단위 이력 필터 — 현재 이 사전에 속한 대상 + CREATE/DELETE 마커가 이 사전인 항목."""
    marker = f"{_like_escape(_marker_prefix(dictionary_id))}%"
    log = StandardChangeLog
    cv_ids = select(CodeValue.id).join(CodeGroup, CodeValue.code_group_id == CodeGroup.id).where(
        CodeGroup.dictionary_id == dictionary_id)
    return or_(
        and_(log.entity_type == "DICTIONARY", log.entity_id == dictionary_id),
        and_(log.entity_type == "WORD", log.entity_id.in_(
            select(StandardWord.id).where(StandardWord.dictionary_id == dictionary_id))),
        and_(log.entity_type == "DOMAIN", log.entity_id.in_(
            select(StandardDomain.id).where(StandardDomain.dictionary_id == dictionary_id))),
        and_(log.entity_type == "TERM", log.entity_id.in_(
            select(StandardTerm.id).where(StandardTerm.dictionary_id == dictionary_id))),
        and_(log.entity_type == "CODE_GROUP", log.entity_id.in_(
            select(CodeGroup.id).where(CodeGroup.dictionary_id == dictionary_id))),
        and_(log.entity_type == "CODE_VALUE", log.entity_id.in_(cv_ids)),
        log.old_value.like(marker, escape="\\"),
        log.new_value.like(marker, escape="\\"),
    )


async def _entity_names(session: AsyncSession, rows) -> dict[tuple[str, int], str]:
    """이력 행들의 (entity_type, entity_id) → 현재 이름. 삭제된 대상은 결과에 없다."""
    want: dict[str, set[int]] = {}
    for r in rows:
        want.setdefault(r.entity_type, set()).add(r.entity_id)
    names: dict[tuple[str, int], str] = {}
    sources = {
        "DICTIONARY": (StandardDictionary.id, StandardDictionary.dict_name),
        "WORD": (StandardWord.id, StandardWord.word_name),
        "DOMAIN": (StandardDomain.id, StandardDomain.domain_name),
        "TERM": (StandardTerm.id, StandardTerm.term_name),
        "CODE_GROUP": (CodeGroup.id, CodeGroup.group_name),
    }
    for et, ids in want.items():
        if et in sources:
            id_col, name_col = sources[et]
            for i, n in (await session.execute(select(id_col, name_col).where(id_col.in_(ids)))).all():
                names[(et, i)] = n
        elif et == "CODE_VALUE":
            for i, g, v in (await session.execute(
                select(CodeValue.id, CodeGroup.group_name, CodeValue.code_value)
                .join(CodeGroup, CodeValue.code_group_id == CodeGroup.id).where(CodeValue.id.in_(ids))
            )).all():
                names[(et, i)] = f"{g}/{v}"
    return names


def _marker_name(value: str | None) -> str | None:
    """이력 값이 CREATE/DELETE 마커 JSON 이면 그 name, 아니면 None."""
    if not value or not value.startswith('{"dictionary_id"'):
        return None
    try:
        return json.loads(value).get("name")
    except (ValueError, AttributeError):
        return None


async def list_change_logs(
    session: AsyncSession, entity_type: str | None = None, entity_id: int | None = None,
    page: int = 1, page_size: int = 50, dictionary_id: int | None = None,
) -> dict:
    """변경 이력 페이지 — ``{"items": [...], "total": n}``, 최신순."""
    conds = []
    if entity_type:
        conds.append(StandardChangeLog.entity_type == entity_type)
    if entity_id:
        conds.append(StandardChangeLog.entity_id == entity_id)
    if dictionary_id:
        conds.append(_dictionary_scope(dictionary_id))
    total = (await session.execute(select(func.count(StandardChangeLog.id)).where(*conds))).scalar() or 0
    rows = (await session.execute(
        select(StandardChangeLog).where(*conds)
        .order_by(StandardChangeLog.changed_at.desc(), StandardChangeLog.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    names = await _entity_names(session, rows)
    items = []
    for r in rows:
        old_marker, new_marker = _marker_name(r.old_value), _marker_name(r.new_value)
        items.append(ChangeLogEntry(
            id=r.id, entity_type=r.entity_type, entity_id=r.entity_id,
            entity_name=names.get((r.entity_type, r.entity_id)) or old_marker or new_marker,
            change_type=r.change_type, field_name=r.field_name,
            old_value=None if old_marker else r.old_value,
            new_value=None if new_marker else r.new_value,
            changed_by=r.changed_by, changed_at=r.changed_at,
        ))
    return {"items": items, "total": total}


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
    )).scalars().first()
    if val is None:
        return None
    try:
        dict_id = int(val)
    except ValueError:
        logger.warning("데이터셋 기본 사전 값 손상 — 무시: dataset_id=%d value=%r", dataset_id, val)
        return None
    # 사전이 삭제된 경우 None 으로 간주 (탭에서 첫 사전 기본 선택으로 폴백)
    exists = (await session.execute(
        select(StandardDictionary.id).where(StandardDictionary.id == dict_id)
    )).scalar()
    return dict_id if exists else None


async def set_dataset_dictionary(
    session: AsyncSession, dataset_id: int, dictionary_id: int,
) -> None:
    """데이터셋의 기본 사전 선택을 upsert (커밋은 호출자). 사전/데이터셋이 없으면 StandardError(404)."""
    from app.catalog.models import DatasetProperty

    await _require_dictionary(session, dictionary_id)
    if not (await session.execute(
        select(Dataset.id).where(Dataset.id == dataset_id)
    )).scalar():
        raise StandardError("데이터셋을 찾을 수 없습니다.", 404)

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
    await session.flush()
    logger.info("데이터셋 기본 사전 설정: dataset=%d dict=%d", dataset_id, dictionary_id)
