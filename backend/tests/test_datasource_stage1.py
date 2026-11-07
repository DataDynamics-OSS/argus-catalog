# SPDX-License-Identifier: Apache-2.0
"""데이터 소스 1단계 보강 단위 테스트 (자기완결형, in-memory SQLite).

- 접속 설정 비밀값: 암호화 저장·응답 마스킹·빈 값 유지·레거시 평문·연결 테스트 채움
- 접속 설정 조회 권한: datasources.connection 은 명시 허용형(기본 거부)
- 재동기화: 컬럼 diff-upsert(행 id·pii_type·사용자 설명 보존), 컬럼 0개 방어
- 삭제: removed 데이터셋만 남은 데이터 소스 삭제
- 샘플 경로: 다른 데이터 소스 디렉터리로의 탈출 차단
- SQL 식별자 인용

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m tests.test_datasource_stage1
    # 또는: pytest tests/test_datasource_stage1.py
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from sqlalchemy import func, select

import app.catalog.models as cm
from app.catalog import datasource_secrets as secrets
from app.catalog import service
from app.catalog import sync as catalog_sync
from app.catalog.router import _sample_dir_by_datasource
from app.catalog.schemas import DatasourceCreate
from app.core.config import settings
from app.permissions.router import DEFAULT_DENY_FEATURES


async def _make_session():
    # 생성 시 메타 시드·삭제 시 참조 정리가 쓰는 테이블까지 만드는 공용 팩토리
    from tests.test_datasource_stage2 import _make_session as make

    return await make()


# ---------------------------------------------------------------------------
# 비밀값 규칙
# ---------------------------------------------------------------------------

def _test_secret_rules() -> None:
    assert secrets.is_secret_key("password")
    assert secrets.is_secret_key("ldap_password")
    assert secrets.is_secret_key("secret_access_key")
    assert secrets.is_secret_key("credentials_json")
    assert secrets.is_secret_key("token")
    assert not secrets.is_secret_key("host")
    assert not secrets.is_secret_key("kerberos_keytab")  # 경로일 뿐

    sealed = secrets.seal_config({"host": "db", "password": "p@ss"})
    assert sealed["host"] == "db"
    assert sealed["password"].startswith("enc:") and "p@ss" not in sealed["password"]
    assert secrets.unseal_config(sealed)["password"] == "p@ss"

    masked, fields = secrets.mask_config(json.dumps(sealed))
    assert masked == {"host": "db", "password": ""} and fields == ["password"]

    # 빈 값 = 변경 안 함(기존 암호문 유지), 키가 빠지면 버림, 새 값이면 교체
    kept = secrets.seal_config({"host": "db2", "password": ""}, json.dumps(sealed))
    assert kept["password"] == sealed["password"] and kept["host"] == "db2"
    dropped = secrets.seal_config({"host": "db"}, json.dumps(sealed))
    assert "password" not in dropped
    replaced = secrets.seal_config({"password": "new"}, json.dumps(sealed))
    assert secrets.unseal_config(replaced)["password"] == "new"
    # 클라이언트가 보낸 enc: 값으로 덮어쓰지 않는다
    forged = secrets.seal_config({"password": "enc:forged"}, json.dumps(sealed))
    assert forged["password"] == sealed["password"]

    # 레거시 평문: 그대로 읽히고, 빈 값으로 다시 저장하면 암호문으로 바뀐다
    legacy = json.dumps({"password": "plain"})
    assert secrets.unseal_config(legacy)["password"] == "plain"
    migrated = secrets.seal_config({"password": ""}, legacy)
    assert migrated["password"].startswith("enc:")
    assert secrets.unseal_config(migrated)["password"] == "plain"

    # 연결 테스트: 빈 비밀값만 저장값으로 채운다
    filled = secrets.fill_stored_secrets({"host": "x", "password": ""}, json.dumps(sealed))
    assert filled == {"host": "x", "password": "p@ss"}
    typed = secrets.fill_stored_secrets({"password": "typed"}, json.dumps(sealed))
    assert typed["password"] == "typed"


def _test_permission_default_deny() -> None:
    assert "datasources.connection" in DEFAULT_DENY_FEATURES


async def _test_configuration_service() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="pg", type="postgresql"))
        saved = await service.save_datasource_configuration(
            db, ds.id, {"host": "h", "username": "u", "password": "secret!"},
        )
        assert saved["config"]["password"] == "" and saved["secret_fields"] == ["password"]

        raw = await service.get_datasource_stored_config(db, ds.id)
        assert "secret!" not in raw

        # 마스킹된 응답을 그대로 다시 저장해도 비밀번호 유지
        await service.save_datasource_configuration(db, ds.id, saved["config"])
        raw2 = await service.get_datasource_stored_config(db, ds.id)
        assert secrets.unseal_config(raw2)["password"] == "secret!"

        got = await service.get_datasource_configuration(db, ds.id)
        assert got["config"]["password"] == "" and got["config"]["host"] == "h"


# ---------------------------------------------------------------------------
# 재동기화 — diff-upsert
# ---------------------------------------------------------------------------

async def _seed_dataset(db, ds_id: int, status: str = "active") -> cm.Dataset:
    d = cm.Dataset(
        urn=f"x.db.t{status}.dataset", name=f"db.t{status}", datasource_id=ds_id,
        origin="DEV", qualified_name=f"x.db.t{status}", status=status,
    )
    db.add(d)
    await db.flush()
    return d


async def _test_apply_schema_fields() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        d = await _seed_dataset(db, ds.id)
        keep = cm.DatasetSchema(
            dataset_id=d.id, field_path="id", field_type="NUMBER", ordinal=0,
            description="사용자 설명", display_name="아이디", pii_type="ID",
            is_partition_key="true",
        )
        gone = cm.DatasetSchema(dataset_id=d.id, field_path="old", field_type="STRING", ordinal=1)
        db.add_all([keep, gone])
        await db.commit()
        keep_id = keep.id

        existing = list((await db.execute(
            select(cm.DatasetSchema).where(cm.DatasetSchema.dataset_id == d.id)
        )).scalars().all())
        await service.apply_schema_fields(db, d.id, existing, [
            # 원본 코멘트 없음, 파티션 키 정보 없음(백엔드 sync 형태)
            {"field_path": "id", "field_type": "NUMBER", "description": None,
             "is_primary_key": "true", "ordinal": 0},
            {"field_path": "name", "field_type": "STRING", "description": "이름", "ordinal": 1},
        ])
        await db.commit()

        rows = {r.field_path: r for r in (await db.execute(
            select(cm.DatasetSchema).where(cm.DatasetSchema.dataset_id == d.id)
        )).scalars().all()}
        assert set(rows) == {"id", "name"}
        r = rows["id"]
        assert r.id == keep_id                      # 행 유지 → 용어 매핑 보존
        assert r.pii_type == "ID"                   # 동기화 대상 아님 → 보존
        assert r.description == "사용자 설명"       # 빈 원본 코멘트로 지우지 않음
        assert r.display_name == "아이디"
        assert r.is_primary_key == "true"           # 동기화 속성은 갱신
        assert r.is_partition_key == "true"         # 키가 없으면 기존 유지
        assert rows["name"].description == "이름"


async def _test_sync_keeps_schema_on_zero_columns() -> None:
    """원격 조회가 컬럼 0개를 돌려줘도 기존 스키마를 지우지 않는다."""
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        await service.save_datasource_configuration(db, ds.id, {"host": "h", "password": "pw"})
        dsid = ds.datasource_id
        d = cm.Dataset(
            urn=f"{dsid}.db.t.dataset", name="db.t", datasource_id=ds.id, origin="DEV",
            qualified_name=f"{dsid}.db.t", status="active", description="d",
        )
        db.add(d)
        await db.flush()
        db.add(cm.DatasetSchema(dataset_id=d.id, field_path="c", field_type="STRING",
                                ordinal=0, pii_type="EMAIL"))
        await db.commit()
        d_id = d.id

    seen_password: list[str] = []

    async def fake_read(host, port, user, password, database=None, ssl=None):
        seen_password.append(password)
        return [catalog_sync.TableInfo(database="db", name="t", table_type="BASE TABLE",
                                       table_comment="c", columns=[])]

    async def no_sample(*a, **k):
        return None

    orig_read, orig_sample = catalog_sync._read_mysql_metadata, catalog_sync._fetch_sample_rows
    catalog_sync._read_mysql_metadata = fake_read
    catalog_sync._fetch_sample_rows = no_sample
    try:
        async with make_db() as db:
            res = await catalog_sync.sync_datasource_metadata(db, dsid)
        assert not res.errors, res.errors
        assert seen_password == ["pw"]          # 암호문을 복호화해 접속
    finally:
        catalog_sync._read_mysql_metadata = orig_read
        catalog_sync._fetch_sample_rows = orig_sample

    async with make_db() as db:
        rows = (await db.execute(
            select(cm.DatasetSchema).where(cm.DatasetSchema.dataset_id == d_id)
        )).scalars().all()
        assert [(r.field_path, r.pii_type) for r in rows] == [("c", "EMAIL")]


# ---------------------------------------------------------------------------
# 삭제 — removed 데이터셋만 남은 경우
# ---------------------------------------------------------------------------

async def _test_delete_with_removed_only() -> None:
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        await _seed_dataset(db, ds.id, status="removed")
        await db.commit()

        assert await service.get_datasource_dataset_count(db, ds.id) == 0
        impact = await service.get_datasource_delete_impact(db, ds.id)
        assert impact["dataset_count"] == 0 and impact["removed_dataset_count"] == 1

        assert await service.delete_datasource(db, ds.id, force=False)
        left = await db.scalar(select(func.count()).select_from(cm.Dataset))
        assert left == 0


async def _test_delete_without_force_keeps_active_guard() -> None:
    """force 가 아니면 활성 데이터셋은 지우지 않는다(라우터가 409 로 막는 전제)."""
    make_db = await _make_session()
    async with make_db() as db:
        ds = await service.create_datasource(db, DatasourceCreate(name="m", type="mysql"))
        await _seed_dataset(db, ds.id, status="active")
        await _seed_dataset(db, ds.id, status="removed")
        await db.commit()
        assert await service.delete_datasource(db, ds.id, force=True)
        assert await db.scalar(select(func.count()).select_from(cm.Dataset)) == 0


# ---------------------------------------------------------------------------
# 샘플 경로·식별자 인용
# ---------------------------------------------------------------------------

def _test_sample_paths() -> None:
    orig = settings.data_dir
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = Path(tmp)
        try:
            ok = catalog_sync._write_sample_file("pg-aaa", "public.users", b"x")
            assert ok.parent.parent.name == "pg-aaa"
            for bad_ds, bad_name in [
                ("pg-aaa", "public.x/../../pg-bbb/public.users"),  # 다른 데이터 소스로 탈출
                ("pg-aaa", "../pg-bbb"),
                ("pg-aaa", ".."),
                ("../etc", "x"),
                ("a/b", "x"),
            ]:
                try:
                    catalog_sync._write_sample_file(bad_ds, bad_name, b"x")
                    raise AssertionError(f"expected ValueError: {bad_ds}/{bad_name}")
                except ValueError:
                    pass
                try:
                    _sample_dir_by_datasource(bad_ds, bad_name)
                    raise AssertionError(f"expected ValueError(router): {bad_ds}/{bad_name}")
                except ValueError:
                    pass
            assert not (Path(tmp) / "samples" / "pg-bbb").exists()
            assert _sample_dir_by_datasource("pg-aaa", "db.t").name == "db.t"
        finally:
            settings.data_dir = orig


def _test_ident_quoting() -> None:
    assert catalog_sync._mysql_ident("x`;DROP TABLE t;#") == "`x``;DROP TABLE t;#`"
    assert catalog_sync._pg_ident('a"b') == '"a""b"'


async def _run() -> None:
    _test_secret_rules()
    _test_permission_default_deny()
    await _test_configuration_service()
    await _test_apply_schema_fields()
    await _test_sync_keeps_schema_on_zero_columns()
    await _test_delete_with_removed_only()
    await _test_delete_without_force_keeps_active_guard()
    _test_sample_paths()
    _test_ident_quoting()


def test_datasource_stage1() -> None:
    """pytest 진입점."""
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_datasource_stage1 OK")
