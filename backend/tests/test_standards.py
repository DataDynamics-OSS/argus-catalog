# SPDX-License-Identifier: Apache-2.0
"""데이터 표준 서비스·권한 테스트 (in-memory SQLite, 자기완결형).

검증:
- 스키마: 이름 공백 정리, 상태·유형 허용 목록, 수정 시 필수 필드 null 거부, 물리명 정규화
- 이름 중복 409 (사전·단어·도메인·코드 그룹·코드 값·용어·물리명)
- 사전 간 참조 차단 (용어→도메인, 도메인→코드 그룹)
- 형태소 분석: 구성 단어 항상 연결, 금칙어 탐지·대체어 제안, 금칙어 용어 생성 400
- 수동 매핑: 다른 데이터셋 컬럼 400, 같은 사전 안 중복 매핑 409
- 자동 매핑: 다른 사전 매핑 보존, 맞지 않게 된 자동 매핑 제거, 수동(SIMILAR) 매핑 유지
- 초기화: 사전 단위 삭제
- 준수율: 사전 단위는 이 사전을 적용한 데이터셋 컬럼만 분모
- 변경 이력: 변경자 기록, 사전 단위 필터(삭제된 항목 포함)
- standards.edit 는 명시 허용형(미설정이면 admin 전용)

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_standards
    # 또는: pytest tests/test_standards.py
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.catalog.models as cm
import app.standard.models as sm
from app.permissions import router as perm
from app.permissions.models import Permission
from app.standard import service
from app.standard.schemas import (
    CodeGroupCreate,
    CodeValueCreate,
    CodeValueUpdate,
    DictionaryCreate,
    DictionaryUpdate,
    DomainCreate,
    DomainUpdate,
    TermCreate,
    TermMappingCreate,
    TermUpdate,
    WordCreate,
    WordUpdate,
)


def _test_schema_validation() -> None:
    w = WordCreate(dictionary_id=1, word_name=" 고객 ", word_english="Customer", word_abbr="CUST")
    assert w.word_name == "고객"
    t = TermCreate(dictionary_id=1, term_name="고객명", physical_name=" CUST_NM ")
    assert t.physical_name == "cust_nm"
    assert DomainCreate(dictionary_id=1, domain_name="명", data_type="varchar").data_type == "VARCHAR"
    bad = [
        (WordCreate, {"dictionary_id": 1, "word_name": "  ", "word_english": "a", "word_abbr": "b"}),
        (WordCreate, {"dictionary_id": 1, "word_name": "a", "word_english": "a", "word_abbr": "b", "word_type": "X"}),
        (WordCreate, {"dictionary_id": 1, "word_name": "a", "word_english": "a", "word_abbr": "b", "is_forbidden": "Y"}),
        (WordUpdate, {"word_name": None}),
        (WordUpdate, {"status": "GONE"}),
        (TermCreate, {"dictionary_id": 1, "term_name": "x", "physical_name": "1abc"}),
        (TermCreate, {"dictionary_id": 1, "term_name": "x", "physical_name": "cust-nm"}),
        (TermUpdate, {"physical_name": None}),
        (DomainCreate, {"dictionary_id": 1, "domain_name": "x", "data_type": "INT", "data_length": -1}),
        (DomainUpdate, {"data_type": None}),
        (DictionaryUpdate, {"dict_name": "x" * 201}),
        (CodeValueUpdate, {"code_name": None}),
        (TermMappingCreate, {"term_id": 1, "dataset_id": 1, "schema_id": 1, "mapping_type": "FOO"}),
    ]
    for model, payload in bad:
        try:
            model(**payload)
        except ValidationError:
            continue
        raise AssertionError(f"검증 통과하면 안 됨: {model.__name__} {payload}")
    # 선택 필드는 null 로 비울 수 있다
    assert DomainUpdate(code_group_id=None).model_dump(exclude_unset=True) == {"code_group_id": None}
    assert "created_by" not in TermCreate.model_fields and "created_by" not in DictionaryCreate.model_fields


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    tables = [
        cm.Base.metadata.tables["catalog_datasources"],
        cm.Base.metadata.tables["catalog_datasets"],
        cm.Base.metadata.tables["catalog_dataset_schemas"],
        cm.DatasetProperty.__table__,
        sm.StandardDictionary.__table__,
        sm.StandardWord.__table__,
        sm.CodeGroup.__table__,
        sm.CodeValue.__table__,
        sm.StandardDomain.__table__,
        sm.StandardTerm.__table__,
        sm.StandardTermWord.__table__,
        sm.TermColumnMapping.__table__,
        sm.StandardChangeLog.__table__,
        Permission.__table__,
    ]
    pending, seen = list(tables), set()
    while pending:
        t = pending.pop()
        if t.name in seen:
            continue
        seen.add(t.name)
        if t not in tables:
            tables.append(t)
        pending.extend(fk.column.table for fk in t.foreign_keys)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: cm.Base.metadata.create_all(sc, tables=tables))
    return async_sessionmaker(engine, expire_on_commit=False)


async def _expect(coro, status: int) -> str:
    try:
        await coro
    except service.StandardError as e:
        assert e.status_code == status, (e.status_code, e.message)
        return e.message
    raise AssertionError(f"StandardError({status}) 가 나야 함")


async def _run_dictionary_content() -> None:
    session_factory = await _make_session()
    async with session_factory() as db:
        d = await service.create_dictionary(db, DictionaryCreate(dict_name="공통"), "kim")
        other = await service.create_dictionary(db, DictionaryCreate(dict_name="재무"), "kim")
        assert d.created_by == "kim"
        await _expect(service.create_dictionary(db, DictionaryCreate(dict_name="공통")), 409)
        await _expect(service.update_dictionary(db, other.id, DictionaryUpdate(dict_name="공통")), 409)
        await _expect(service.create_word(db, WordCreate(
            dictionary_id=9999, word_name="x", word_english="x", word_abbr="X")), 404)

        async def word(name, eng, abbr, **kw):
            return await service.create_word(db, WordCreate(
                dictionary_id=d.id, word_name=name, word_english=eng, word_abbr=abbr, **kw), "kim")

        cust = await word("고객", "Customer", "CUST")
        nm = await word("명", "Name", "NM", word_type="SUFFIX")
        await word("번호", "Number", "NO", word_type="SUFFIX")
        await word("손님", "Guest", "GST", is_forbidden="true", synonym_group_id=1)
        await word("고객님", "Customer", "CUSTM", synonym_group_id=1)
        await _expect(word("고객", "Customer2", "CUST2"), 409)
        await _expect(service.update_word(db, nm.id, WordUpdate(word_name="고객")), 409)

        # --- 코드 그룹·도메인 --------------------------------------------------------
        cg = await service.create_code_group(db, CodeGroupCreate(dictionary_id=d.id, group_name="성별"), "kim")
        other_cg = await service.create_code_group(db, CodeGroupCreate(dictionary_id=other.id, group_name="성별"), "kim")
        await _expect(service.create_code_group(db, CodeGroupCreate(dictionary_id=d.id, group_name="성별")), 409)
        await service.add_code_value(db, cg.id, CodeValueCreate(code_value="M", code_name="남"), "kim")
        f = await service.add_code_value(db, cg.id, CodeValueCreate(code_value="F", code_name="여"), "kim")
        await _expect(service.add_code_value(db, cg.id, CodeValueCreate(code_value="M", code_name="x")), 409)
        await _expect(service.update_code_value(db, f.id, CodeValueUpdate(code_value="M")), 409)

        name_dom = await service.create_domain(db, DomainCreate(
            dictionary_id=d.id, domain_name="명", data_type="VARCHAR", data_length=100), "kim")
        await _expect(service.create_domain(db, DomainCreate(
            dictionary_id=d.id, domain_name="명", data_type="VARCHAR")), 409)
        await _expect(service.create_domain(db, DomainCreate(
            dictionary_id=d.id, domain_name="성별코드", data_type="CHAR", code_group_id=other_cg.id)), 400)
        g_dom = await service.create_domain(db, DomainCreate(
            dictionary_id=d.id, domain_name="성별코드", data_type="CHAR", code_group_id=cg.id), "kim")
        assert g_dom.code_group_name == "성별"
        other_dom = await service.create_domain(db, DomainCreate(
            dictionary_id=other.id, domain_name="명", data_type="VARCHAR"), "kim")

        # --- 형태소 분석·금칙어 ------------------------------------------------------
        a = await service.analyze_term(db, d.id, "고객 명")
        assert [w.word_name for w in a.words] == ["고객", "명"] and a.physical_name == "cust_nm"
        assert a.recommended_domain and a.recommended_domain.id == name_dom.id and not a.forbidden_words
        a2 = await service.analyze_term(db, d.id, "손님명")
        assert [(f.word_name, f.suggestion) for f in a2.forbidden_words] == [("손님", "고객님")]
        await _expect(service.create_term(db, TermCreate(dictionary_id=d.id, term_name="손님명")), 400)

        # 영문·약어·물리명을 모두 직접 줘도 구성 단어는 연결된다
        t = await service.create_term(db, TermCreate(
            dictionary_id=d.id, term_name="고객명", term_english="Customer Name",
            term_abbr="CUST_NM", physical_name="cust_nm"), "kim")
        assert [w.word_name for w in t.words] == ["고객", "명"] and t.domain_id == name_dom.id
        assert t.created_by == "kim"
        await _expect(service.create_term(db, TermCreate(dictionary_id=d.id, term_name="고객명")), 409)
        await _expect(service.create_term(db, TermCreate(
            dictionary_id=d.id, term_name="고객이름", physical_name="cust_nm")), 409)        # 물리명 중복
        await _expect(service.create_term(db, TermCreate(
            dictionary_id=d.id, term_name="고객번호", domain_id=other_dom.id)), 400)        # 다른 사전 도메인
        t_no = await service.create_term(db, TermCreate(dictionary_id=d.id, term_name="고객번호"), "lee")
        assert t_no.physical_name == "cust_no"
        await _expect(service.update_term(db, t_no.id, TermUpdate(term_name="손님번호")), 400)
        await _expect(service.update_term(db, t_no.id, TermUpdate(physical_name="cust_nm")), 409)
        renamed = await service.update_term(db, t_no.id, TermUpdate(term_name="고객 번호"))
        assert renamed and [w.word_name for w in renamed.words] == ["고객", "번호"]

        # 검색 — %, _ 는 문자 그대로
        assert {x.term_name for x in await service.list_terms(db, d.id, "cust_")} == {"고객명", "고객 번호"}
        assert await service.list_terms(db, d.id, "%") == []
        assert [x.term_name for x in await service.list_terms(db, d.id, "Name")] == ["고객명"]

        # --- 사용 현황·삭제 ----------------------------------------------------------
        usage = await service.word_usage(db, cust.id)
        assert usage and {u.name for u in usage.terms} == {"고객명", "고객 번호"}
        cg_usage = await service.code_group_usage(db, cg.id)
        assert cg_usage and [u.name for u in cg_usage.domains] == ["성별코드"] and cg_usage.value_count == 2
        assert await service.delete_code_group(db, cg.id, "kim")
        assert (await service.get_domain_response(db, g_dom.id)).code_group_id is None
        assert await service.delete_domain(db, name_dom.id, "kim")
        assert (await service.get_term_response(db, t.id)).domain_id is None
        await db.commit()

        words = {w.word_name: w for w in await service.list_words(db, d.id)}
        assert words["고객"].term_count == 2 and words["명"].term_count == 1
        dicts = {x.dict_name: x for x in await service.list_dictionaries(db)}
        assert dicts["공통"].term_count == 2 and dicts["공통"].word_count == 5

        # --- 변경 이력 ---------------------------------------------------------------
        logs = await service.list_change_logs(db, dictionary_id=d.id, page_size=200)
        items = logs["items"]
        assert logs["total"] == len(items) and all(i.changed_by in ("kim", "lee", None) for i in items)
        kinds = {(i.entity_type, i.change_type) for i in items}
        for k in [("DICTIONARY", "CREATE"), ("WORD", "CREATE"), ("CODE_GROUP", "DELETE"),
                  ("CODE_VALUE", "CREATE"), ("DOMAIN", "DELETE"), ("TERM", "UPDATE")]:
            assert k in kinds, k
        deleted_group = next(i for i in items if i.entity_type == "CODE_GROUP" and i.change_type == "DELETE")
        assert deleted_group.entity_name == "성별" and deleted_group.old_value is None
        other_logs = await service.list_change_logs(db, dictionary_id=other.id, page_size=200)
        assert all(i.entity_name != "고객명" for i in other_logs["items"])
        assert any(i.entity_type == "CODE_GROUP" for i in other_logs["items"])


async def _run_mappings() -> None:
    session_factory = await _make_session()
    async with session_factory() as db:
        d = await service.create_dictionary(db, DictionaryCreate(dict_name="공통"), "kim")
        d2 = await service.create_dictionary(db, DictionaryCreate(dict_name="재무"), "kim")
        for dd in (d, d2):
            await service.create_word(db, WordCreate(dictionary_id=dd.id, word_name="고객", word_english="Customer", word_abbr="CUST"))
            await service.create_word(db, WordCreate(dictionary_id=dd.id, word_name="명", word_english="Name", word_abbr="NM", word_type="SUFFIX"))
            await service.create_word(db, WordCreate(dictionary_id=dd.id, word_name="나이", word_english="Age", word_abbr="AGE"))
        dom = await service.create_domain(db, DomainCreate(dictionary_id=d.id, domain_name="명", data_type="VARCHAR"))
        t_nm = await service.create_term(db, TermCreate(dictionary_id=d.id, term_name="고객명"))
        t_age = await service.create_term(db, TermCreate(dictionary_id=d.id, term_name="나이"))
        assert t_nm.domain_id == dom.id
        t2_nm = await service.create_term(db, TermCreate(dictionary_id=d2.id, term_name="고객명"))

        src = cm.Datasource(datasource_id="s1", name="src", type="trino", origin="PROD")
        db.add(src)
        await db.flush()
        ds = cm.Dataset(urn="x.1.dataset", name="t1", datasource_id=src.id, origin="PROD", status="active")
        ds_other = cm.Dataset(urn="x.2.dataset", name="t2", datasource_id=src.id, origin="PROD", status="active")
        ds_unrelated = cm.Dataset(urn="x.3.dataset", name="t3", datasource_id=src.id, origin="PROD", status="active")
        db.add_all([ds, ds_other, ds_unrelated])
        await db.flush()
        c_nm = cm.DatasetSchema(dataset_id=ds.id, field_path="CUST_NM", field_type="varchar(50)", ordinal=0)
        c_age = cm.DatasetSchema(dataset_id=ds.id, field_path="age", field_type="INT", ordinal=1)
        c_x = cm.DatasetSchema(dataset_id=ds.id, field_path="x", field_type="INT", ordinal=2)
        c_o = cm.DatasetSchema(dataset_id=ds_other.id, field_path="y", field_type="INT", ordinal=0)
        db.add_all([c_nm, c_age, c_x, c_o] + [
            cm.DatasetSchema(dataset_id=ds_unrelated.id, field_path=f"u{i}", field_type="INT", ordinal=i)
            for i in range(10)])
        await db.commit()

        # 다른 사전의 매핑은 자동 매핑에 영향받지 않는다
        r2 = await service.auto_map_dataset(db, d2.id, ds.id)
        assert r2.created == 1 and r2.matched == 1

        r = await service.auto_map_dataset(db, d.id, ds.id)
        assert (r.created, r.matched, r.unmapped) == (2, 2, 1), r          # varchar(50) ↔ VARCHAR 호환
        status = await service.get_dataset_term_mapping(db, d.id, ds.id)
        assert {c.column_name: c.mapping_type for c in status.columns} == {"CUST_NM": "MATCHED", "age": "MATCHED", "x": None}
        assert (await service.get_dataset_term_mapping(db, d2.id, ds.id)).compliance.matched == 1

        # 수동 매핑 검증
        await _expect(service.create_term_mapping(db, TermMappingCreate(
            term_id=t_age.id, dataset_id=ds.id, schema_id=c_o.id, mapping_type="SIMILAR")), 400)
        await _expect(service.create_term_mapping(db, TermMappingCreate(
            term_id=t_age.id, dataset_id=ds.id, schema_id=c_nm.id, mapping_type="SIMILAR")), 409)
        await _expect(service.create_term_mapping(db, TermMappingCreate(
            term_id=9999, dataset_id=ds.id, schema_id=c_x.id)), 404)
        manual = await service.create_term_mapping(db, TermMappingCreate(
            term_id=t_age.id, dataset_id=ds.id, schema_id=c_x.id, mapping_type="SIMILAR"))
        assert manual.column_name == "x" and manual.term_name == "나이"
        await db.commit()

        # 용어 물리명이 바뀌면 맞지 않게 된 자동 매핑은 지우고 수동 매핑은 남긴다
        await service.update_term(db, t_age.id, TermUpdate(physical_name="cust_age"))
        r = await service.auto_map_dataset(db, d.id, ds.id)
        assert (r.removed, r.matched, r.similar, r.unmapped) == (1, 1, 1, 1), r
        await db.commit()

        # 준수율 — 사전 단위는 이 사전을 적용한 데이터셋 컬럼만 (ds_unrelated 의 10개 컬럼 제외)
        stats = await service.get_compliance_stats(db, d.id)
        assert (stats.total_columns, stats.matched, stats.similar, stats.dataset_count) == (3, 1, 1, 1), stats
        await service.set_dataset_dictionary(db, ds_other.id, d.id)
        await db.commit()
        stats = await service.get_compliance_stats(db, d.id)
        assert (stats.total_columns, stats.dataset_count) == (4, 2)
        assert stats.compliance_rate == 25.0

        # 초기화 — 사전 단위
        assert await service.delete_dataset_term_mappings(db, ds.id, d.id) == 2
        await db.commit()
        left = await service.list_term_mappings(db, dataset_id=ds.id)
        assert [m.term_id for m in left] == [t2_nm.id]

        # 용어 삭제 시 매핑 정리
        assert await service.delete_term(db, t2_nm.id, "kim")
        await db.commit()
        assert int(await db.scalar(select(func.count(sm.TermColumnMapping.id))) or 0) == 0


async def _run_permission() -> None:
    session_factory = await _make_session()
    user = SimpleNamespace(is_admin=False, is_superuser=False)
    admin = SimpleNamespace(is_admin=True, is_superuser=False)
    async with session_factory() as db:
        assert "standards.edit" in perm.DEFAULT_DENY_FEATURES
        assert await perm.check_feature_granted(db, admin, "standards.edit")
        assert not await perm.check_feature_granted(db, user, "standards.edit")
        await perm._put_matrix(db, "FEATURE", {"standards.edit": ["argus-user"]})
        assert await perm.check_feature_granted(db, user, "standards.edit")


def test_standards() -> None:
    """pytest 진입점."""
    _test_schema_validation()
    asyncio.run(_run_dictionary_content())
    asyncio.run(_run_mappings())
    asyncio.run(_run_permission())


if __name__ == "__main__":
    test_standards()
    print("test_standards OK")
