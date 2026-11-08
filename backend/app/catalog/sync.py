# SPDX-License-Identifier: Apache-2.0
"""데이터 소스 메타데이터 동기화(백엔드 내장 — MySQL/MariaDB·PostgreSQL 만).

외부 데이터 소스에 접속해 테이블/컬럼 메타데이터를 카탈로그로 동기화한다.
흐름: 접속 설정 복호화 → INFORMATION_SCHEMA/pg_catalog 조회 → 데이터셋·스키마 upsert(스냅샷 포함)
→ 원본에서 사라진 데이터셋 removed 표시 → 커밋 → AI 설명 생성 예약 → 테이블별 샘플 parquet 저장.
그 밖의 유형은 metadata-sync 확장 서비스가 담당한다.
"""

import asyncio
import io
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiomysql
import asyncpg
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import (
    Dataset,
    DatasetSchema,
    Datasource,
    DatasourceConfiguration,
)
from app.core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------

@dataclass
class ColumnInfo:
    """원본 DB 에서 읽은 컬럼 1개(data_type 은 카탈로그 공통 타입, native_type 은 원본 타입)."""

    name: str
    data_type: str
    native_type: str
    nullable: bool
    ordinal: int
    column_key: str = ""
    column_default: str | None = None
    comment: str = ""
    is_primary_key: bool = False
    is_unique: bool = False
    is_indexed: bool = False


@dataclass
class TableInfo:
    """원본 DB 에서 읽은 테이블/뷰 1개 — database 는 MySQL DB 명 또는 PostgreSQL 스키마명."""

    database: str
    name: str
    table_type: str  # BASE TABLE, VIEW, SYSTEM VIEW
    engine: str | None = None
    table_comment: str = ""
    columns: list[ColumnInfo] = field(default_factory=list)
    datasource_properties: dict | None = None  # 데이터 소스별 고유 메타데이터 (JSON)


@dataclass
class SyncResult:
    """동기화 결과 요약. ``errors`` 가 비어 있지 않으면 라우터가 첫 오류로 400 을 낸다."""

    datasource_id: str
    databases_scanned: list[str] = field(default_factory=list)
    tables_created: int = 0
    tables_updated: int = 0
    tables_removed: int = 0
    tables_total: int = 0
    samples_uploaded: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# MySQL / MariaDB 타입 매핑
# ---------------------------------------------------------------------------

MYSQL_TYPE_MAP = {
    "tinyint": "NUMBER", "smallint": "NUMBER", "mediumint": "NUMBER",
    "int": "NUMBER", "bigint": "NUMBER", "decimal": "NUMBER",
    "float": "NUMBER", "double": "NUMBER",
    "char": "STRING", "varchar": "STRING",
    "tinytext": "STRING", "text": "STRING", "mediumtext": "STRING", "longtext": "STRING",
    "binary": "BYTES", "varbinary": "BYTES",
    "tinyblob": "BYTES", "blob": "BYTES", "mediumblob": "BYTES", "longblob": "BYTES",
    "date": "DATE", "datetime": "DATE", "timestamp": "DATE", "time": "DATE", "year": "DATE",
    "json": "MAP", "enum": "ENUM", "set": "ARRAY",
    "geometry": "STRING", "point": "STRING", "linestring": "STRING", "polygon": "STRING",
}


def _map_field_type(native_type: str) -> str:
    """MySQL/MariaDB 컬럼 타입을 카탈로그 공통 필드 타입으로 매핑한다(괄호 앞 기본 타입 기준, 모르면 STRING)."""
    base = native_type.split("(")[0].strip().lower()
    return MYSQL_TYPE_MAP.get(base, "STRING")


# ---------------------------------------------------------------------------
# MariaDB / MySQL 메타데이터 리더
# ---------------------------------------------------------------------------

SYSTEM_DATABASES = {"information_schema", "performance_schema", "mysql", "sys"}


async def _read_mysql_metadata(
    host: str, port: int, user: str, password: str, database: str | None = None,
    ssl=None,
) -> list[TableInfo]:
    """MySQL/MariaDB 에 접속해 INFORMATION_SCHEMA 에서 테이블·컬럼 메타데이터를 읽는다.

    ``database`` 가 없으면 시스템 DB 를 뺀 전체 DB 를 스캔한다. 테이블마다 SHOW CREATE TABLE 로
    DDL 을 시도하며(실패는 건너뜀), 접속·조회 오류는 호출 측으로 전파된다.
    """

    logger.info("[MySQL] %s:%d 접속 중", host, port)
    conn = await aiomysql.connect(
        host=host, port=port, user=user, password=password,
        db="information_schema", charset="utf8mb4", connect_timeout=_CONNECT_TIMEOUT, ssl=ssl,
    )
    logger.info("[MySQL] 접속 성공")
    tables: list[TableInfo] = []

    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 대상 데이터베이스 결정
            if database:
                target_dbs = [database]
            else:
                await cur.execute("SELECT SCHEMA_NAME FROM SCHEMATA")
                rows = await cur.fetchall()
                target_dbs = [
                    r["SCHEMA_NAME"] for r in rows
                    if r["SCHEMA_NAME"].lower() not in SYSTEM_DATABASES
                ]
            logger.info("[MySQL] 대상 데이터베이스: %s", target_dbs)

            for db in target_dbs:
                # 확장 메타데이터와 함께 테이블 조회
                await cur.execute(
                    "SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_COMMENT, "
                    "ROW_FORMAT, TABLE_ROWS, AVG_ROW_LENGTH, DATA_LENGTH, "
                    "INDEX_LENGTH, AUTO_INCREMENT, TABLE_COLLATION, "
                    "CREATE_TIME, UPDATE_TIME, CREATE_OPTIONS "
                    "FROM TABLES WHERE TABLE_SCHEMA = %s",
                    (db,),
                )
                table_rows = await cur.fetchall()
                logger.info("[MySQL] 데이터베이스 '%s': 테이블 %d개 발견", db, len(table_rows))

                for tr in table_rows:
                    # 데이터 소스별 고유 속성 구성
                    props: dict = {"table": {}, "columns": {}}
                    tbl_props = props["table"]
                    if tr.get("ENGINE"):
                        tbl_props["engine"] = tr["ENGINE"]
                    if tr.get("ROW_FORMAT"):
                        tbl_props["row_format"] = tr["ROW_FORMAT"]
                    if tr.get("TABLE_ROWS") is not None:
                        tbl_props["estimated_rows"] = tr["TABLE_ROWS"]
                    if tr.get("AVG_ROW_LENGTH") is not None:
                        tbl_props["avg_row_length"] = tr["AVG_ROW_LENGTH"]
                    if tr.get("DATA_LENGTH") is not None:
                        tbl_props["data_size"] = tr["DATA_LENGTH"]
                    if tr.get("INDEX_LENGTH") is not None:
                        tbl_props["index_size"] = tr["INDEX_LENGTH"]
                    if tr.get("AUTO_INCREMENT") is not None:
                        tbl_props["auto_increment"] = tr["AUTO_INCREMENT"]
                    if tr.get("TABLE_COLLATION"):
                        tbl_props["collation"] = tr["TABLE_COLLATION"]
                    if tr.get("CREATE_TIME"):
                        tbl_props["create_time"] = str(tr["CREATE_TIME"])
                    if tr.get("UPDATE_TIME"):
                        tbl_props["update_time"] = str(tr["UPDATE_TIME"])
                    if tr.get("CREATE_OPTIONS"):
                        tbl_props["create_options"] = tr["CREATE_OPTIONS"]

                    table = TableInfo(
                        database=db,
                        name=tr["TABLE_NAME"],
                        table_type=tr["TABLE_TYPE"],
                        engine=tr.get("ENGINE"),
                        table_comment=tr.get("TABLE_COMMENT") or "",
                        datasource_properties=props,
                    )

                    # 확장 메타데이터와 함께 컬럼 조회
                    await cur.execute(
                        "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE, "
                        "ORDINAL_POSITION, COLUMN_KEY, COLUMN_DEFAULT, COLUMN_COMMENT, "
                        "EXTRA, CHARACTER_SET_NAME, COLLATION_NAME "
                        "FROM COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                        "ORDER BY ORDINAL_POSITION",
                        (db, table.name),
                    )
                    col_rows = await cur.fetchall()
                    # 복합 PK 의 각 컬럼도 COLUMN_KEY=PRI 다 — 단일 컬럼 PK 일 때만 "고유"로 본다
                    # (ERD 카디널리티가 is_unique 로 1:1 을 판정하므로 오판 방지).
                    pk_count = sum(1 for r in col_rows if r.get("COLUMN_KEY") == "PRI")

                    for cr in col_rows:
                        col_key = cr.get("COLUMN_KEY") or ""
                        table.columns.append(ColumnInfo(
                            name=cr["COLUMN_NAME"],
                            data_type=_map_field_type(cr["DATA_TYPE"]),
                            native_type=cr["COLUMN_TYPE"],
                            nullable=cr["IS_NULLABLE"] == "YES",
                            ordinal=cr["ORDINAL_POSITION"],
                            column_key=col_key,
                            column_default=cr.get("COLUMN_DEFAULT"),
                            comment=cr.get("COLUMN_COMMENT") or "",
                            is_primary_key=col_key == "PRI",
                            is_unique=(col_key == "PRI" and pk_count == 1) or col_key == "UNI",
                            is_indexed=col_key in ("PRI", "MUL", "UNI"),
                        ))

                        # 컬럼 단위 속성
                        col_props: dict = {}
                        if cr.get("COLUMN_KEY"):
                            col_props["key"] = cr["COLUMN_KEY"]
                        if cr.get("EXTRA"):
                            col_props["extra"] = cr["EXTRA"]
                        if cr.get("COLUMN_DEFAULT") is not None:
                            col_props["default"] = str(cr["COLUMN_DEFAULT"])
                        if cr.get("CHARACTER_SET_NAME"):
                            col_props["charset"] = cr["CHARACTER_SET_NAME"]
                        if cr.get("COLLATION_NAME"):
                            col_props["collation"] = cr["COLLATION_NAME"]
                        if col_props:
                            props["columns"][cr["COLUMN_NAME"]] = col_props

                    # CREATE TABLE DDL 수집
                    try:
                        await cur.execute(
                            f"SHOW CREATE TABLE {_mysql_ident(db)}.{_mysql_ident(table.name)}"
                        )
                        ddl_row = await cur.fetchone()
                        if ddl_row:
                            # SHOW CREATE TABLE 은 (table_name, create_sql) 을 반환
                            # 뷰의 경우 (view_name, create_sql, charset, collation)
                            ddl_key = "Create Table" if "Create Table" in ddl_row else "Create View"
                            props["ddl"] = ddl_row.get(ddl_key, "")
                    except Exception as e:
                        logger.info("[MySQL] DDL 수집 생략 %s.%s: %s", db, table.name, e)

                    logger.debug("[MySQL] %s.%s: type=%s, engine=%s, cols=%d, ddl=%s",
                                 db, table.name, table.table_type,
                                 table.engine, len(table.columns), bool(props.get("ddl")))
                    tables.append(table)

    finally:
        conn.close()
        logger.info("[MySQL] 접속 종료. 수집한 전체 테이블: %d", len(tables))

    return tables


# ---------------------------------------------------------------------------
# PostgreSQL 타입 매핑
# ---------------------------------------------------------------------------

PG_TYPE_MAP = {
    "smallint": "NUMBER", "integer": "NUMBER", "bigint": "NUMBER",
    "int2": "NUMBER", "int4": "NUMBER", "int8": "NUMBER",
    "decimal": "NUMBER", "numeric": "NUMBER",
    "real": "NUMBER", "double precision": "NUMBER", "float4": "NUMBER", "float8": "NUMBER",
    "serial": "NUMBER", "bigserial": "NUMBER",
    "character varying": "STRING", "varchar": "STRING",
    "character": "STRING", "char": "STRING",
    "text": "STRING", "name": "STRING", "citext": "STRING",
    "bytea": "BYTES",
    "date": "DATE", "timestamp without time zone": "DATE",
    "timestamp with time zone": "DATE", "time without time zone": "DATE",
    "time with time zone": "DATE", "interval": "DATE",
    "boolean": "BOOLEAN", "bool": "BOOLEAN",
    "json": "MAP", "jsonb": "MAP",
    "uuid": "STRING", "inet": "STRING", "cidr": "STRING", "macaddr": "STRING",
    "xml": "STRING", "money": "NUMBER",
    "point": "STRING", "line": "STRING", "polygon": "STRING", "geometry": "STRING",
    "ARRAY": "ARRAY", "USER-DEFINED": "STRING",
}

SYSTEM_SCHEMAS_PG = {"pg_catalog", "information_schema", "pg_toast"}


def _map_pg_field_type(udt_name: str, data_type: str) -> str:
    """PostgreSQL 컬럼 타입을 카탈로그 공통 필드 타입으로 매핑한다(data_type → udt_name 순, 모르면 STRING)."""
    if data_type == "ARRAY":
        return "ARRAY"
    if data_type == "USER-DEFINED":
        return "STRING"
    return PG_TYPE_MAP.get(data_type, PG_TYPE_MAP.get(udt_name, "STRING"))


# 원격 DB 접속/쿼리 타임아웃(초) — 응답 없는 서버에서 동기화 요청이 무한 대기하지 않도록.
_CONNECT_TIMEOUT = 10
_QUERY_TIMEOUT = 120


def _mysql_ident(name: str) -> str:
    """MySQL 식별자 인용 — 백틱을 이중화한다(테이블명은 원본 DB 가 준 신뢰할 수 없는 값).

    aiomysql 은 MULTI_STATEMENTS 를 항상 켜므로, 이스케이프 없이 f-string 으로 넣으면
    ``x`;DROP TABLE ...`` 같은 이름으로 동기화 계정 권한의 임의 문장이 실행된다.
    """
    return "`" + str(name).replace("`", "``") + "`"


def _pg_ident(name: str) -> str:
    """PostgreSQL 식별자 인용 — 큰따옴표를 이중화한다."""
    return '"' + str(name).replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# PostgreSQL 메타데이터 리더
# ---------------------------------------------------------------------------

async def _safe_pg_fetch(conn, query: str, *args) -> list:
    """쿼리를 실행하고, 권한 오류 시 빈 리스트를 반환한다."""
    try:
        return await conn.fetch(query, *args)
    except Exception as e:
        logger.debug("쿼리 생략(권한 또는 오류): %s", e)
        return []


async def _safe_pg_fetchrow(conn, query: str, *args):
    """한 행을 반환하는 쿼리를 실행하고, 오류 시 None 을 반환한다."""
    try:
        return await conn.fetchrow(query, *args)
    except Exception as e:
        logger.debug("단건 쿼리 생략(권한 또는 오류): %s", e)
        return None


def _single_unique_columns(constraint_rows) -> set[str]:
    """단일 컬럼 PK/UNIQUE 제약의 컬럼 집합 (복합 제약 구성 컬럼은 단독 유일이 아님)."""
    return {
        cr["columns"][0]
        for cr in constraint_rows
        if cr["contype"] in ("p", "u") and len(cr["columns"] or []) == 1
    }


def _pg_fk_ddl(cst) -> str:
    """pg_constraint FK 행 → `CONSTRAINT n FOREIGN KEY ("c") REFERENCES s.t ("rc")`.

    참조 컬럼 목록이 없으면 ERD 의 FK 파서(`erd.parse_ddl_keys`)가 관계를 인식하지 못한다.
    """
    fk_cols = ", ".join(f'"{c}"' for c in (cst["columns"] or []))
    clause = f"CONSTRAINT {cst['conname']} FOREIGN KEY ({fk_cols}) REFERENCES {cst['ref_table']}"
    ref_cols = cst.get("ref_columns")
    if ref_cols:
        clause += " (" + ", ".join(f'"{c}"' for c in ref_cols) + ")"
    return clause


def _pg_view_ddl(schema: str, name: str, definition: str | None) -> str | None:
    """`pg_get_viewdef` 결과 → `CREATE OR REPLACE VIEW "s"."v" AS <정의>;` (정의가 없으면 None)."""
    body = (definition or "").strip().rstrip(";").strip()
    if not body:
        return None
    return f"CREATE OR REPLACE VIEW {_pg_ident(schema)}.{_pg_ident(name)} AS\n{body};"


async def _read_pg_metadata(
    host: str, port: int, user: str, password: str,
    database: str, schema: str | None = None, ssl=None,
) -> list[TableInfo]:
    """PostgreSQL 에 접속해 테이블·컬럼 메타데이터를 읽는다.

    ``schema`` 가 없으면 시스템 스키마를 뺀 전체 스키마를 스캔한다(목록 조회 실패는 전파).
    코멘트·크기·소유자·인덱스·제약 등 부가 정보 조회 실패는 빈 값으로 넘어간다(_safe_pg_*).
    """

    logger.info("[PostgreSQL] %s:%d/%s 접속 중", host, port, database)
    conn = await asyncpg.connect(
        host=host, port=port, user=user, password=password, database=database,
        timeout=_CONNECT_TIMEOUT, command_timeout=_QUERY_TIMEOUT, ssl=ssl,
    )
    logger.info("[PostgreSQL] 접속 성공")
    tables: list[TableInfo] = []

    try:
        # 대상 스키마 결정
        if schema:
            target_schemas = [schema]
        else:
            # 목록 조회 실패는 삼키지 않는다 — 빈 목록으로 보면 기존 데이터셋·스키마가 지워진다.
            rows = await conn.fetch(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast') "
                "AND schema_name NOT LIKE 'pg_temp%'",
            )
            target_schemas = [r["schema_name"] for r in rows]
        logger.info("[PostgreSQL] 대상 스키마: %s", target_schemas)

        for sch in target_schemas:
            # 테이블과 뷰 조회
            table_rows = await conn.fetch(
                "SELECT table_name, table_type "
                "FROM information_schema.tables "
                "WHERE table_schema = $1 AND table_type IN ('BASE TABLE', 'VIEW')",
                sch,
            )

            logger.info("[PostgreSQL] 스키마 '%s': 테이블 %d개 발견", sch, len(table_rows))

            for tr in table_rows:
                tbl_name = tr["table_name"]
                props: dict = {"table": {}, "columns": {}, "indexes": []}
                tbl_props = props["table"]

                # 테이블 코멘트
                comment_row = await _safe_pg_fetchrow(
                    conn,
                    "SELECT obj_description(c.oid) AS comment "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = $1 AND c.relname = $2",
                    sch, tbl_name,
                )
                table_comment = (comment_row["comment"] or "") if comment_row else ""

                # pg_class 에서 테이블 단위 데이터 소스 속성
                pg_row = await _safe_pg_fetchrow(
                    conn,
                    "SELECT c.reltuples::bigint AS estimated_rows, "
                    "c.relpersistence::text, c.relkind::text, c.relhasindex AS has_indexes, "
                    "c.relhastriggers AS has_triggers, "
                    "pg_total_relation_size(c.oid) AS total_size, "
                    "pg_table_size(c.oid) AS table_size, "
                    "pg_indexes_size(c.oid) AS index_size "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = $1 AND c.relname = $2",
                    sch, tbl_name,
                )
                if pg_row:
                    persistence_map = {"p": "permanent", "t": "temporary", "u": "unlogged"}
                    kind_map = {
                        "r": "table", "v": "view", "m": "materialized view",
                        "f": "foreign table", "p": "partitioned table",
                    }
                    tbl_props["estimated_rows"] = pg_row["estimated_rows"]
                    tbl_props["persistence"] = persistence_map.get(
                        pg_row["relpersistence"], pg_row["relpersistence"])
                    tbl_props["kind"] = kind_map.get(
                        pg_row["relkind"], pg_row["relkind"])
                    tbl_props["has_indexes"] = pg_row["has_indexes"]
                    tbl_props["has_triggers"] = pg_row["has_triggers"]
                    tbl_props["total_size"] = pg_row["total_size"]
                    tbl_props["table_size"] = pg_row["table_size"]
                    tbl_props["index_size"] = pg_row["index_size"]

                # 테이블 소유자
                owner_row = await _safe_pg_fetchrow(
                    conn,
                    "SELECT tableowner FROM pg_tables "
                    "WHERE schemaname = $1 AND tablename = $2",
                    sch, tbl_name,
                )
                if not owner_row:
                    owner_row = await _safe_pg_fetchrow(
                        conn,
                        "SELECT viewowner AS tableowner FROM pg_views "
                        "WHERE schemaname = $1 AND viewname = $2",
                        sch, tbl_name,
                    )
                if owner_row and owner_row["tableowner"]:
                    tbl_props["owner"] = owner_row["tableowner"]

                # 인덱스
                idx_rows = await _safe_pg_fetch(
                    conn,
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = $1 AND tablename = $2",
                    sch, tbl_name,
                )
                for ir in idx_rows:
                    props["indexes"].append({
                        "name": ir["indexname"],
                        "definition": ir["indexdef"],
                    })

                # 제약조건 (PK, FK, UNIQUE, CHECK)
                constraint_rows = await _safe_pg_fetch(
                    conn,
                    "SELECT con.conname::text, con.contype::text, "
                    "array_agg(att.attname::text ORDER BY u.ord) AS columns, "
                    "CASE WHEN con.contype = 'f' THEN "
                    "  (SELECT nspname || '.' || relname FROM pg_class fc "
                    "   JOIN pg_namespace fn ON fn.oid = fc.relnamespace "
                    "   WHERE fc.oid = con.confrelid) "
                    "END AS ref_table, "
                    # FK 참조 컬럼 — DDL 의 REFERENCES t (cols) 에 필요(ERD 의 FK 파싱이 컬럼 목록을 요구)
                    "CASE WHEN con.contype = 'f' THEN "
                    "  (SELECT array_agg(fa.attname::text ORDER BY fu.ord) "
                    "   FROM unnest(con.confkey) WITH ORDINALITY AS fu(attnum, ord) "
                    "   JOIN pg_attribute fa ON fa.attrelid = con.confrelid AND fa.attnum = fu.attnum) "
                    "END AS ref_columns "
                    "FROM pg_constraint con "
                    "JOIN pg_class c ON c.oid = con.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord) "
                    "JOIN pg_attribute att ON att.attrelid = c.oid AND att.attnum = u.attnum "
                    "WHERE n.nspname = $1 AND c.relname = $2 "
                    "GROUP BY con.conname, con.contype, con.confrelid, con.confkey",
                    sch, tbl_name,
                )
                contype_map = {"p": "PRIMARY KEY", "f": "FOREIGN KEY", "u": "UNIQUE", "c": "CHECK"}
                # 단독으로 유일한 컬럼 = 단일 컬럼 PK/UNIQUE 제약. 복합 제약의 구성 컬럼은 제외.
                single_unique_cols = _single_unique_columns(constraint_rows)
                col_constraints: dict[str, list] = {}  # 컬럼명 -> [제약조건 정보]
                for cr in constraint_rows:
                    ctype = contype_map.get(cr["contype"], cr["contype"])
                    for col_name in (cr["columns"] or []):
                        entry: dict = {"type": ctype}
                        if cr["ref_table"]:
                            entry["references"] = cr["ref_table"]
                        col_constraints.setdefault(col_name, []).append(entry)

                table = TableInfo(
                    database=sch,
                    name=tbl_name,
                    table_type=tr["table_type"],
                    engine=None,
                    table_comment=table_comment,
                    datasource_properties=props,
                )

                # 컬럼 조회
                col_rows = await conn.fetch(
                    "SELECT column_name, data_type, udt_name, is_nullable, "
                    "ordinal_position, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = $1 AND table_name = $2 "
                    "ORDER BY ordinal_position",
                    sch, tbl_name,
                )

                for cr in col_rows:
                    col_name = cr["column_name"]

                    # 컬럼 코멘트
                    col_comment_row = await _safe_pg_fetchrow(
                        conn,
                        "SELECT col_description(c.oid, a.attnum) AS comment "
                        "FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "JOIN pg_attribute a ON a.attrelid = c.oid "
                        "WHERE n.nspname = $1 AND c.relname = $2 AND a.attname = $3",
                        sch, tbl_name, col_name,
                    )
                    col_comment = (col_comment_row["comment"] or "") if col_comment_row else ""

                    native_type = cr["udt_name"]
                    if cr["data_type"] == "ARRAY":
                        native_type = f"{cr['udt_name']}[]"

                    # 제약조건 + 인덱스로부터 PK 및 인덱스 여부 판단
                    col_cons = col_constraints.get(col_name, [])
                    is_pk = any(c.get("type") == "PRIMARY KEY" for c in col_cons if isinstance(c, dict))
                    is_uniq = col_name in single_unique_cols
                    is_idx = is_pk or is_uniq or any(
                        f'"{col_name}"' in (ir["indexdef"] or "")
                        or f"({col_name})" in (ir["indexdef"] or "")
                        or f"({col_name}," in (ir["indexdef"] or "")
                        or f" {col_name})" in (ir["indexdef"] or "")
                        or f" {col_name}," in (ir["indexdef"] or "")
                        for ir in idx_rows
                    )

                    table.columns.append(ColumnInfo(
                        name=col_name,
                        data_type=_map_pg_field_type(cr["udt_name"], cr["data_type"]),
                        native_type=native_type,
                        nullable=cr["is_nullable"] == "YES",
                        ordinal=cr["ordinal_position"],
                        column_key="",
                        column_default=cr.get("column_default"),
                        comment=col_comment,
                        is_primary_key=is_pk,
                        is_unique=is_uniq,
                        is_indexed=is_idx,
                    ))

                    # 컬럼 단위 속성
                    col_props: dict = {}
                    if cr.get("column_default") is not None:
                        col_props["default"] = str(cr["column_default"])
                    if col_name in col_constraints:
                        col_props["constraints"] = col_constraints[col_name]
                    if col_props:
                        props["columns"][col_name] = col_props

                # 뷰는 원천 정의(pg_get_viewdef)로 CREATE VIEW DDL 을 만든다 (컬럼 재구성 CREATE TABLE 아님)
                if tr["table_type"] == "VIEW":
                    view_row = await _safe_pg_fetchrow(
                        conn,
                        "SELECT pg_get_viewdef(c.oid, true) AS definition "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = $1 AND c.relname = $2",
                        sch, tbl_name,
                    )
                    view_ddl = _pg_view_ddl(sch, tbl_name, view_row["definition"] if view_row else None)
                    if view_ddl:
                        props["ddl"] = view_ddl

                # CREATE TABLE DDL 수집 (PostgreSQL) — 테이블이거나 뷰 정의를 못 읽은 경우 재구성
                if not props.get("ddl"):
                    try:
                        # 컬럼 목록과 PK/FK/UNIQUE 제약으로 CREATE TABLE 문을 재구성한다(근사치 — 원본 DDL 아님)
                        ddl_parts = [f'CREATE TABLE "{sch}"."{tbl_name}" (']
                        col_defs = []
                        for cr in col_rows:
                            col_def = f'  "{cr["column_name"]}" {cr["udt_name"]}'
                            if cr["is_nullable"] == "NO":
                                col_def += " NOT NULL"
                            if cr.get("column_default") is not None:
                                col_def += f" DEFAULT {cr['column_default']}"
                            col_defs.append(col_def)
                        # 기본 키 제약조건 추가
                        for cst in constraint_rows:
                            if cst["contype"] == "p":
                                pk_cols = ", ".join(f'"{c}"' for c in cst["columns"])
                                col_defs.append(f"  CONSTRAINT {cst['conname']} PRIMARY KEY ({pk_cols})")
                        # 외래 키 제약조건 추가
                        for cst in constraint_rows:
                            if cst["contype"] == "f" and cst.get("ref_table"):
                                col_defs.append(f"  {_pg_fk_ddl(cst)}")
                        # 유니크 제약조건 추가 (UNIQUE 제약만 — 인덱스로만 만든 유일성은 포함하지 않음)
                        for cst in constraint_rows:
                            if cst["contype"] == "u":
                                u_cols = ", ".join(f'"{c}"' for c in cst["columns"])
                                col_defs.append(
                                    f"  CONSTRAINT {cst['conname']} UNIQUE ({u_cols})"
                                )
                        ddl_parts.append(",\n".join(col_defs))
                        ddl_parts.append(");")
                        props["ddl"] = "\n".join(ddl_parts)
                    except Exception as e:
                        logger.info("[PostgreSQL] DDL 생성 생략 %s.%s: %s", sch, tbl_name, e)

                logger.debug("[PostgreSQL] %s.%s: type=%s, owner=%s, cols=%d, indexes=%d, ddl=%s",
                             sch, tbl_name, tr["table_type"],
                             tbl_props.get("owner", "?"), len(table.columns),
                             len(props["indexes"]), bool(props.get("ddl")))
                tables.append(table)

    finally:
        await conn.close()
        logger.info("[PostgreSQL] 접속 종료. 수집한 전체 테이블: %d", len(tables))

    return tables


async def _fetch_pg_sample_rows(
    host: str, port: int, user: str, password: str,
    database: str, schema: str, table_name: str, limit: int = 100, ssl=None,
) -> bytes | None:
    """PostgreSQL 에서 샘플 행(최대 ``limit``)을 조회해 parquet 바이트로 반환한다.

    모든 값은 문자열로 바꾼다. 빈 테이블이거나 오류면 None(오류는 경고 로그).
    """
    try:
        conn = await asyncpg.connect(
            host=host, port=port, user=user, password=password, database=database,
            timeout=_CONNECT_TIMEOUT, command_timeout=_QUERY_TIMEOUT, ssl=ssl,
        )
        try:
            rows = await conn.fetch(
                f"SELECT * FROM {_pg_ident(schema)}.{_pg_ident(table_name)} LIMIT $1", limit,
            )
        finally:
            await conn.close()

        if not rows:
            return None

        # dict 리스트로 변환, 모든 값은 STRING 으로
        if not rows:
            return None
        col_names = list(rows[0].keys())
        columns: dict[str, list] = {k: [] for k in col_names}
        for row in rows:
            for k in col_names:
                v = row[k]
                columns[k].append(str(v) if v is not None else None)

        arrow_table = pa.table(columns)
        buf = io.BytesIO()
        pq.write_table(arrow_table, buf)
        return buf.getvalue()

    except Exception as e:
        logger.warning("PG 샘플 조회 실패 %s.%s: %s", schema, table_name, e)
        return None


async def _fetch_sample_rows(
    host: str, port: int, user: str, password: str,
    database: str, table_name: str, limit: int = 100, ssl=None,
) -> bytes | None:
    """MySQL 테이블에서 최대 `limit` 행을 조회해 parquet 바이트로 반환한다.

    모든 값은 문자열로 바꾼다. 빈 테이블이거나 오류면 None(오류는 경고 로그).
    """
    try:
        conn = await aiomysql.connect(
            host=host, port=port, user=user, password=password,
            db=database, charset="utf8mb4", connect_timeout=_CONNECT_TIMEOUT, ssl=ssl,
        )
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    f"SELECT * FROM {_mysql_ident(table_name)} LIMIT %s", (limit,)
                )
                rows = await cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return None

        # pyarrow Table → parquet 바이트로 변환 (모든 컬럼은 STRING)
        columns: dict[str, list] = {}
        for key in rows[0].keys():
            col_values = []
            for row in rows:
                v = row[key]
                col_values.append(str(v) if v is not None else None)
            columns[key] = col_values

        arrow_table = pa.table(columns)
        buf = io.BytesIO()
        pq.write_table(arrow_table, buf)
        return buf.getvalue()

    except Exception as e:
        logger.warning("샘플 조회 실패 %s.%s: %s", database, table_name, e)
        return None


def _write_sample_file(datasource_id: str, dataset_name: str, parquet_bytes: bytes) -> Path:
    """data_dir/samples/{datasource_id}/{dataset_name}/sample.parquet 에 기록한다.

    테이블명은 외부 DB 에서 온 값이므로 samples 디렉터리 밖으로 벗어나지 않는지 확인한다.
    """
    root = (settings.data_dir / "samples").resolve()
    ds_root = (root / datasource_id).resolve()
    dest_dir = (ds_root / dataset_name).resolve()
    # samples 루트뿐 아니라 이 데이터 소스 디렉터리 안으로 한정 — "x/../../다른DS/..." 같은
    # 테이블명으로 다른 데이터 소스의 샘플을 덮어쓰지 못하게 한다.
    if ds_root.parent != root or not dest_dir.is_relative_to(ds_root) or dest_dir == ds_root:
        logger.warning("샘플 경로 탈출 시도 차단: datasource_id=%s dataset=%s", datasource_id, dataset_name)
        raise ValueError(f"잘못된 샘플 경로: {datasource_id}/{dataset_name}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "sample.parquet"
    dest.write_bytes(parquet_bytes)
    return dest


async def _save_sample_parquet(datasource_id: str, dataset_name: str, parquet_bytes: bytes) -> bool:
    """parquet 샘플을 카탈로그 샘플 저장소에 직접 저장한다.

    예전에는 자기 자신의 ``POST /catalog/samples/upload`` 를 HTTP 로 호출했는데,
    인증 헤더가 없어 401(관리자 전용) 이고 요청 Host 기반 URL 이라 컨테이너 안에서
    접속도 안 돼 항상 실패했다. 같은 프로세스이므로 파일을 바로 쓴다
    (업로드 엔드포인트와 같은 경로 규칙 — ``catalog.router._sample_dir_by_datasource``).
    """
    try:
        dest = await asyncio.to_thread(_write_sample_file, datasource_id, dataset_name, parquet_bytes)
        logger.debug("샘플 parquet 저장 완료: %s (%d bytes)", dest, len(parquet_bytes))
        return True
    except Exception as e:
        logger.warning("샘플 저장 오류 %s/%s: %s", datasource_id, dataset_name, e)
    return False


# ---------------------------------------------------------------------------
# 동기화 로직
# ---------------------------------------------------------------------------

def _generate_urn(datasource_id: str, path: str, entity_type: str = "dataset") -> str:
    """URN ``{datasource_id}.{path}.{entity_type}`` 생성."""
    # 환경(DEV/STAGING/PROD)은 URN 에 포함하지 않는다. app.catalog.service._generate_urn 과 동일 포맷.
    return f"{datasource_id}.{path}.{entity_type}"


# 내장 동기화가 지원하는 데이터 소스 유형(그 밖은 metadata-sync 가 담당)
SUPPORTED_TYPES = {"mysql", "postgresql"}

# 동기화 후 예약한 백그라운드 태스크(AI 설명 생성) 강참조
_background_tasks: set[asyncio.Task] = set()


def _in_sync_scope(dataset_name: str, database: str | None) -> bool:
    """데이터셋(``{db}.{table}``)이 이번 동기화의 스캔 범위에 드는지.

    - 범위 미지정(전체 스캔): 모든 데이터셋. 원본에서 DB/스키마가 통째로 사라진 경우도
      제거로 반영된다(예전에는 스캔 결과에 나타난 DB 만 비교해서 누락).
    - 범위 지정: 정확히 그 DB 의 테이블만. 접두사 비교만 하면 ``a`` 를 스캔할 때
      ``a.b`` DB 의 테이블까지 걸리므로, 나머지 부분에 점이 없을 때만 해당으로 본다
      (이름에 점이 있는 테이블은 제거 반영에서 빠지는 쪽을 택한다).
    """
    if not database:
        return True
    prefix = f"{database}."
    return dataset_name.startswith(prefix) and "." not in dataset_name[len(prefix):]


async def sync_datasource_metadata(
    session: AsyncSession,
    datasource_id_str: str,
    database: str | None = None,
) -> SyncResult:
    """외부 데이터 소스의 메타데이터를 카탈로그로 동기화한다.

    지원: mysql (MySQL/MariaDB), postgresql (PostgreSQL).

    Args:
        session: DB 세션
        datasource_id_str: datasource_id 문자열 (예: "mysql-19d0bfe954e2cfdaa")
        database: MySQL 은 데이터베이스명, PostgreSQL 은 스키마명. 선택.

    Returns:
        요약 통계를 담은 SyncResult. 사전 검증·접속 실패는 예외 대신 ``errors`` 에 담아 반환한다.

    부작용: 데이터셋·스키마·스냅샷을 커밋하고, AI 설명 생성 태스크를 예약하며,
    data_dir/samples 아래에 샘플 parquet 파일을 쓴다.
    """
    result = SyncResult(datasource_id=datasource_id_str)
    started = time.monotonic()

    # 1. 데이터 소스 해석
    row = await session.execute(
        select(Datasource).where(Datasource.datasource_id == datasource_id_str)
    )
    datasource = row.scalars().first()
    if not datasource:
        result.errors.append(f"Datasource not found: {datasource_id_str}")
        return result

    if datasource.type not in SUPPORTED_TYPES:
        logger.warning("내장 동기화 미지원 유형: datasource=%s type=%s", datasource_id_str, datasource.type)
        result.errors.append(f"Sync not supported for datasource type: {datasource.type}")
        return result

    # 2. 접속 설정 로드
    cfg_row = await session.execute(
        select(DatasourceConfiguration).where(
            DatasourceConfiguration.datasource_id == datasource.id
        )
    )
    cfg = cfg_row.scalars().first()
    if not cfg:
        logger.warning("동기화 중단 — 접속 설정 없음: datasource=%s", datasource_id_str)
        result.errors.append(f"No configuration found for datasource: {datasource_id_str}")
        return result

    from app.catalog.datasource_secrets import DatasourceSecretError, unseal_config

    try:
        config = unseal_config(cfg.config_json)
    except DatasourceSecretError as e:
        logger.warning("동기화 중단 — 비밀값 복호화 실패: datasource=%s", datasource_id_str)
        result.errors.append(str(e))
        return result
    user = config.get("username", "root")
    password = config.get("password", "")

    # 3. 원격 DB 에서 메타데이터 읽기
    is_pg = datasource.type == "postgresql"
    default_port = 5432 if is_pg else 3306
    from app.catalog.service import validate_connection_target

    try:
        host, port = validate_connection_target(config, default_port)
    except ValueError as e:
        logger.warning("동기화 중단 — 접속 설정 오류: datasource=%s reason=%s", datasource_id_str, e)
        result.errors.append(f"접속 설정 오류: {e}")
        return result
    from app.catalog.service import connection_ssl

    ssl_opt = connection_ssl(config, pg=is_pg)
    # MySQL 은 접속 설정의 database 를 스캔 범위로 쓴다(화면에서 필수 입력). 비어 있으면 전체
    # 사용자 DB. PostgreSQL 의 database 는 접속 DB 이고, 스캔 범위(스키마)는 인자로만 받는다.
    if not is_pg and not database:
        database = (config.get("database") or "").strip() or None
    logger.info("메타데이터 동기화 시작 %s:%d (datasource=%s, type=%s, 범위=%s)",
                host, port, datasource_id_str, datasource.type, database or "전체")
    try:
        if is_pg:
            pg_database = config.get("database") or "postgres"
            tables = await _read_pg_metadata(
                host, port, user, password, pg_database, database, ssl=ssl_opt)
        else:
            tables = await _read_mysql_metadata(host, port, user, password, database, ssl=ssl_opt)
    except Exception as e:
        logger.warning("원격 DB 메타데이터 조회 실패 (datasource=%s): %r", datasource_id_str, e)
        from app.catalog.service import _classify_connection_error

        # 원시 예외 메시지(내부 주소·경로)는 응답에 싣지 않는다 — 상세는 위 로그
        result.errors.append(f"원격 DB 메타데이터 조회 실패: {_classify_connection_error(e, _CONNECT_TIMEOUT)}")
        return result

    result.databases_scanned = sorted({t.database for t in tables})
    result.tables_total = len(tables)
    logger.info("데이터베이스 전반에서 테이블 %d개 발견: %s",
                len(tables), result.databases_scanned)

    # 4. 데이터셋 + 스키마 필드 upsert (신규 데이터셋은 데이터 소스의 SYNC 카탈로그에 소속)
    from app.catalog.catalogs_service import sync_catalog_id
    catalog_id = await sync_catalog_id(session, datasource.id)
    for table in tables:
        path = f"{table.database}.{table.name}"
        urn = _generate_urn(datasource_id_str, path)
        qualified_name = f"{datasource_id_str}.{path}"

        # 데이터셋이 이미 존재하는지 확인 (신 URN 또는 데이터 소스 + 이름 기준)
        ds_row = await session.execute(
            select(Dataset).where(Dataset.urn == urn)
        )
        dataset = ds_row.scalars().first()
        if not dataset:
            # 폴백: datasource_id + name 으로 조회 (구 URN 포맷 마이그레이션용)
            ds_row = await session.execute(
                select(Dataset).where(
                    Dataset.datasource_id == datasource.id,
                    Dataset.name == f"{table.database}.{table.name}",
                )
            )
            dataset = ds_row.scalars().first()

        table_type = "VIEW" if "VIEW" in table.table_type.upper() else "TABLE"
        # 데이터셋 환경은 데이터 소스 환경을 미러한다 (데이터 소스 환경 변경 시 소속 데이터셋도
        # 함께 갱신되며, metadata-sync 도 같은 규칙). 예전에는 항상 PROD 로 생성했다.
        origin = datasource.origin or "DEV"
        # 원천에서 읽은 DDL 은 datasource_properties 에도 남지만, 화면 DDL 탭·ERD(FK 파싱)는
        # Dataset.ddl 컬럼을 본다 — metadata-sync 와 같이 컬럼에도 채운다.
        ddl = (table.datasource_properties or {}).get("ddl") or None

        if dataset:
            # 기존 항목 갱신 (이전에 제거된 경우 복원도 함께)
            logger.debug("동기화 upsert [UPDATE]: %s (id=%d)", urn, dataset.id)
            dataset.urn = urn
            dataset.name = f"{table.database}.{table.name}"
            dataset.qualified_name = qualified_name
            dataset.description = table.table_comment or dataset.description
            dataset.table_type = table_type
            dataset.origin = origin
            dataset.datasource_properties = json.dumps(
                table.datasource_properties, ensure_ascii=False) if table.datasource_properties else None
            if ddl:
                dataset.ddl = ddl
            dataset.is_synced = "true"
            dataset.status = "active"
            if dataset.catalog_id is None:
                dataset.catalog_id = catalog_id
            result.tables_updated += 1
        else:
            # 신규 생성
            dataset = Dataset(
                urn=urn,
                name=f"{table.database}.{table.name}",
                datasource_id=datasource.id,
                catalog_id=catalog_id,
                description=table.table_comment or None,
                origin=origin,
                qualified_name=qualified_name,
                table_type=table_type,
                datasource_properties=json.dumps(
                    table.datasource_properties, ensure_ascii=False) if table.datasource_properties else None,
                ddl=ddl,
                is_synced="true",
                status="active",
            )
            session.add(dataset)
            await session.flush()
            logger.debug("동기화 upsert [CREATE]: %s (id=%d)", urn, dataset.id)
            result.tables_created += 1

        # 스키마 필드 동기화: 변경 감지 → 스냅샷 저장 → field_path 기준 diff-upsert
        existing_fields_result = await session.execute(
            select(DatasetSchema).where(DatasetSchema.dataset_id == dataset.id)
        )
        existing_fields = list(existing_fields_result.scalars().all())

        # 컬럼을 하나도 못 읽었는데 기존 스키마가 있으면 갱신하지 않는다 — 권한 부족·조회 실패로
        # 빈 결과가 온 것을 "컬럼 전부 삭제"로 반영하지 않기 위함(컬럼 없는 테이블은 드묾).
        if not table.columns and existing_fields:
            logger.warning("컬럼 0개 — 기존 스키마 유지: %s (기존 %d개)", urn, len(existing_fields))
            continue

        from app.catalog.service import apply_schema_fields, save_schema_snapshot
        await save_schema_snapshot(
            session, dataset.id, existing_fields, table.columns, from_sync=True,
        )
        await apply_schema_fields(session, dataset.id, existing_fields, [
            {
                "field_path": col.name,
                "field_type": col.data_type,
                "native_type": col.native_type,
                "description": col.comment or None,
                "nullable": "true" if col.nullable else "false",
                "is_primary_key": "true" if col.is_primary_key else "false",
                "is_unique": "true" if col.is_unique else "false",
                "is_indexed": "true" if col.is_indexed else "false",
                "ordinal": col.ordinal,
            }
            for col in table.columns
        ])

    # 4b. 원본에 더 이상 존재하지 않는 데이터셋을 제거됨으로 표시
    synced_names = {f"{t.database}.{t.name}" for t in tables}
    existing_rows = await session.execute(
        select(Dataset).where(
            Dataset.datasource_id == datasource.id,
            Dataset.status != "removed",
        )
    )
    active = list(existing_rows.scalars().all())
    if not tables and active:
        # 테이블을 하나도 못 읽었는데 카탈로그에는 있다 — 권한 부족·범위 오설정일 가능성이 커서
        # "전부 삭제됨"으로 반영하지 않는다.
        logger.warning("원본 테이블 0개 — 제거 반영 생략 (datasource=%s, 기존 %d개)",
                       datasource_id_str, len(active))
    else:
        for ds in active:
            if ds.name in synced_names:
                continue
            # 이번 스캔 범위 밖(다른 DB/스키마) 데이터셋은 원본 존재 여부를 알 수 없으므로 그대로 둔다
            if _in_sync_scope(ds.name, database):
                ds.status = "removed"
                result.tables_removed += 1
                logger.info("제거됨으로 표시: %s", ds.urn)

    await session.commit()
    logger.info("동기화 완료: datasource=%s created=%d, updated=%d, removed=%d, total=%d, elapsed=%.1fs",
                datasource_id_str, result.tables_created, result.tables_updated,
                result.tables_removed, result.tables_total, time.monotonic() - started)

    # 4c. 설명이 비어 있는 데이터셋에 대해 AI 메타데이터 생성 트리거
    try:
        from app.ai.service import generate_descriptions_post_sync
        empty_desc_ids = []
        for table in tables:
            if not table.table_comment:
                urn = _generate_urn(
                    datasource_id_str, f"{table.database}.{table.name}",
                )
                ds_result = await session.execute(
                    select(Dataset.id, Dataset.description).where(Dataset.urn == urn)
                )
                ds_row = ds_result.first()
                if ds_row and not ds_row.description:
                    empty_desc_ids.append(ds_row.id)
        if empty_desc_ids:
            # 태스크 참조를 보관한다 — 참조가 없으면 실행 중 GC 될 수 있다(asyncio 문서 권고)
            task = asyncio.create_task(generate_descriptions_post_sync(empty_desc_ids))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
            logger.info("데이터셋 %d개에 대한 AI 설명 생성 예약",
                        len(empty_desc_ids))
    except Exception as e:
        logger.warning("동기화 후 AI 생성 예약 실패: %s", e)

    # 5. 샘플 데이터를 조회해 parquet 로 저장(테이블마다 새 접속 — 실패한 테이블은 건너뜀)
    logger.info("테이블 %d개의 샘플 데이터 수집 중...", len(tables))
    for table in tables:
        # 뷰는 건너뜀 — 샘플 데이터는 베이스 테이블에서만
        if "VIEW" in table.table_type.upper():
            continue

        dataset_name = f"{table.database}.{table.name}"
        if is_pg:
            parquet_bytes = await _fetch_pg_sample_rows(
                host, port, user, password, pg_database, table.database, table.name, ssl=ssl_opt,
            )
        else:
            parquet_bytes = await _fetch_sample_rows(
                host, port, user, password, table.database, table.name, ssl=ssl_opt,
            )
        if parquet_bytes:
            logger.debug("샘플 저장 중: %s/%s (%d bytes)",
                         datasource_id_str, dataset_name, len(parquet_bytes))
            ok = await _save_sample_parquet(datasource_id_str, dataset_name, parquet_bytes)
            if ok:
                result.samples_uploaded += 1
        else:
            logger.debug("샘플 데이터 없음 %s/%s (빈 테이블 또는 오류)",
                         datasource_id_str, dataset_name)

    logger.info("샘플 저장 완료: datasource=%s 파일 %d개, 전체 소요=%.1fs",
                datasource_id_str, result.samples_uploaded, time.monotonic() - started)
    return result
