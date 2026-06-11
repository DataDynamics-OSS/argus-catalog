"""데이터 소스 메타데이터 동기화.

외부 데이터 소스에 접속해 테이블/컬럼 메타데이터를 카탈로그로 동기화한다.
"""

import io
import json
import logging
from dataclasses import dataclass, field

import aiomysql
import asyncpg
import httpx
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------

@dataclass
class ColumnInfo:
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
    database: str
    name: str
    table_type: str  # BASE TABLE, VIEW, SYSTEM VIEW
    engine: str | None = None
    table_comment: str = ""
    columns: list[ColumnInfo] = field(default_factory=list)
    datasource_properties: dict | None = None  # 데이터 소스별 고유 메타데이터 (JSON)


@dataclass
class SyncResult:
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
    """MySQL/MariaDB 컬럼 타입을 카탈로그 공통 필드 타입으로 매핑한다."""
    base = native_type.split("(")[0].strip().lower()
    return MYSQL_TYPE_MAP.get(base, "STRING")


# ---------------------------------------------------------------------------
# MariaDB / MySQL 메타데이터 리더
# ---------------------------------------------------------------------------

SYSTEM_DATABASES = {"information_schema", "performance_schema", "mysql", "sys"}


async def _read_mysql_metadata(
    host: str, port: int, user: str, password: str, database: str | None = None,
) -> list[TableInfo]:
    """MySQL/MariaDB 에 접속해 INFORMATION_SCHEMA 에서 테이블·컬럼 메타데이터를 읽는다."""

    logger.info("[MySQL] %s:%d 접속 중", host, port)
    conn = await aiomysql.connect(
        host=host, port=port, user=user, password=password,
        db="information_schema", charset="utf8mb4",
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
                            is_unique=col_key in ("PRI", "UNI"),
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
                            f"SHOW CREATE TABLE `{db}`.`{table.name}`"
                        )
                        ddl_row = await cur.fetchone()
                        if ddl_row:
                            # SHOW CREATE TABLE 은 (table_name, create_sql) 을 반환
                            # 뷰의 경우 (view_name, create_sql, charset, collation)
                            ddl_key = "Create Table" if "Create Table" in ddl_row else "Create View"
                            props["ddl"] = ddl_row.get(ddl_key, "")
                    except Exception as e:
                        logger.info("[MySQL] DDL 수집 생략 %s.%s: %s", db, table.name, e)

                    logger.info("[MySQL] %s.%s: type=%s, engine=%s, cols=%d, ddl=%s",
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
    """PostgreSQL 컬럼 타입을 카탈로그 공통 필드 타입으로 매핑한다."""
    if data_type == "ARRAY":
        return "ARRAY"
    if data_type == "USER-DEFINED":
        return "STRING"
    return PG_TYPE_MAP.get(data_type, PG_TYPE_MAP.get(udt_name, "STRING"))


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
    except Exception:
        return None


async def _read_pg_metadata(
    host: str, port: int, user: str, password: str,
    database: str, schema: str | None = None,
) -> list[TableInfo]:
    """PostgreSQL 에 접속해 테이블·컬럼 메타데이터를 읽는다."""

    logger.info("[PostgreSQL] %s:%d/%s 접속 중", host, port, database)
    conn = await asyncpg.connect(
        host=host, port=port, user=user, password=password, database=database,
    )
    logger.info("[PostgreSQL] 접속 성공")
    tables: list[TableInfo] = []

    try:
        # 대상 스키마 결정
        if schema:
            target_schemas = [schema]
        else:
            rows = await _safe_pg_fetch(
                conn,
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast') "
                "AND schema_name NOT LIKE 'pg_temp%'",
            )
            target_schemas = [r["schema_name"] for r in rows]
        logger.info("[PostgreSQL] 대상 스키마: %s", target_schemas)

        for sch in target_schemas:
            # 테이블과 뷰 조회
            table_rows = await _safe_pg_fetch(
                conn,
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
                    "END AS ref_table "
                    "FROM pg_constraint con "
                    "JOIN pg_class c ON c.oid = con.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord) "
                    "JOIN pg_attribute att ON att.attrelid = c.oid AND att.attnum = u.attnum "
                    "WHERE n.nspname = $1 AND c.relname = $2 "
                    "GROUP BY con.conname, con.contype, con.confrelid",
                    sch, tbl_name,
                )
                contype_map = {"p": "PRIMARY KEY", "f": "FOREIGN KEY", "u": "UNIQUE", "c": "CHECK"}
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
                col_rows = await _safe_pg_fetch(
                    conn,
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
                    is_uniq = is_pk or any(
                        c.get("type") == "UNIQUE" for c in col_cons if isinstance(c, dict)
                    )
                    is_idx = is_uniq or any(
                        f'"{col_name}"' in (ir.get("indexdef", "") if isinstance(ir, dict) else "")
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

                # CREATE TABLE DDL 수집 (PostgreSQL)
                try:
                    # pg_dump 방식: pg_get_tabledef 또는 컬럼에서 재구성
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
                            fk_cols = ", ".join(f'"{c}"' for c in cst["columns"])
                            col_defs.append(
                                f"  CONSTRAINT {cst['conname']} FOREIGN KEY ({fk_cols}) "
                                f"REFERENCES {cst['ref_table']}"
                            )
                    # 유니크 제약조건 추가 (인덱스와 동일하면 생략)
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

                logger.info("[PostgreSQL] %s.%s: type=%s, owner=%s, cols=%d, indexes=%d, ddl=%s",
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
    database: str, schema: str, table_name: str, limit: int = 100,
) -> bytes | None:
    """PostgreSQL 에서 샘플 행을 조회해 parquet 바이트로 반환한다."""
    try:
        conn = await asyncpg.connect(
            host=host, port=port, user=user, password=password, database=database,
        )
        try:
            rows = await conn.fetch(
                f'SELECT * FROM "{schema}"."{table_name}" LIMIT $1', limit,
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
    database: str, table_name: str, limit: int = 100,
) -> bytes | None:
    """테이블에서 최대 `limit` 행을 조회해 parquet 바이트로 반환한다."""
    try:
        conn = await aiomysql.connect(
            host=host, port=port, user=user, password=password,
            db=database, charset="utf8mb4",
        )
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    f"SELECT * FROM `{table_name}` LIMIT %s", (limit,)
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


async def _upload_sample_parquet(
    catalog_url: str, datasource_id: str, dataset_name: str, parquet_bytes: bytes,
) -> bool:
    """parquet 샘플을 HTTP 로 카탈로그 서버에 업로드한다."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{catalog_url}/api/v1/catalog/samples/upload",
                content=parquet_bytes,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Datasource-Id": datasource_id,
                    "X-Dataset-Name": dataset_name,
                },
                timeout=30.0,
            )
            if resp.status_code == 200:
                return True
            logger.warning("샘플 업로드 실패 (%d): %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("샘플 업로드 오류 %s/%s: %s", datasource_id, dataset_name, e)
    return False


# ---------------------------------------------------------------------------
# 동기화 로직
# ---------------------------------------------------------------------------

def _generate_urn(datasource_id: str, path: str, entity_type: str = "dataset") -> str:
    # 환경(DEV/STAGING/PROD)은 URN 에 포함하지 않는다. app.catalog.service._generate_urn 과 동일 포맷.
    return f"{datasource_id}.{path}.{entity_type}"


SUPPORTED_TYPES = {"mysql", "postgresql"}


async def sync_datasource_metadata(
    session: AsyncSession,
    datasource_id_str: str,
    database: str | None = None,
    catalog_url: str = "http://127.0.0.1:4600",
) -> SyncResult:
    """외부 데이터 소스의 메타데이터를 카탈로그로 동기화한다.

    지원: mysql (MySQL/MariaDB), postgresql (PostgreSQL).

    Args:
        session: DB 세션
        datasource_id_str: datasource_id 문자열 (예: "mysql-19d0bfe954e2cfdaa")
        database: MySQL 은 데이터베이스명, PostgreSQL 은 스키마명. 선택.
        catalog_url: 샘플 업로드용 카탈로그 서버 base URL.

    Returns:
        요약 통계를 담은 SyncResult.
    """
    result = SyncResult(datasource_id=datasource_id_str)

    # 1. 데이터 소스 해석
    row = await session.execute(
        select(Datasource).where(Datasource.datasource_id == datasource_id_str)
    )
    datasource = row.scalars().first()
    if not datasource:
        result.errors.append(f"Datasource not found: {datasource_id_str}")
        return result

    if datasource.type not in SUPPORTED_TYPES:
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
        result.errors.append(f"No configuration found for datasource: {datasource_id_str}")
        return result

    config = json.loads(cfg.config_json)
    host = config.get("host", "localhost")
    user = config.get("username", "root")
    password = config.get("password", "")

    # 3. 원격 DB 에서 메타데이터 읽기
    is_pg = datasource.type == "postgresql"
    default_port = 5432 if is_pg else 3306
    port = int(config.get("port", default_port))
    logger.info("메타데이터 동기화 시작 %s:%d (datasource=%s, type=%s)",
                host, port, datasource_id_str, datasource.type)
    try:
        if is_pg:
            pg_database = config.get("database", "postgres")
            tables = await _read_pg_metadata(host, port, user, password, pg_database, database)
        else:
            tables = await _read_mysql_metadata(host, port, user, password, database)
    except Exception as e:
        logger.warning("원격 DB 접속 실패 (datasource=%s): %s", datasource_id_str, e)
        result.errors.append(f"Connection failed: {e}")
        return result

    result.databases_scanned = sorted({t.database for t in tables})
    result.tables_total = len(tables)
    logger.info("데이터베이스 전반에서 테이블 %d개 발견: %s",
                len(tables), result.databases_scanned)

    # 4. 데이터셋 + 스키마 필드 upsert
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

        if dataset:
            # 기존 항목 갱신 (이전에 제거된 경우 복원도 함께)
            logger.info("동기화 upsert [UPDATE]: %s (id=%d)", urn, dataset.id)
            dataset.urn = urn
            dataset.name = f"{table.database}.{table.name}"
            dataset.qualified_name = qualified_name
            dataset.description = table.table_comment or dataset.description
            dataset.table_type = table_type
            dataset.datasource_properties = json.dumps(
                table.datasource_properties, ensure_ascii=False) if table.datasource_properties else None
            dataset.is_synced = "true"
            dataset.status = "active"
            result.tables_updated += 1
        else:
            # 신규 생성
            dataset = Dataset(
                urn=urn,
                name=f"{table.database}.{table.name}",
                datasource_id=datasource.id,
                description=table.table_comment or None,
                origin="PROD",
                qualified_name=qualified_name,
                table_type=table_type,
                datasource_properties=json.dumps(
                    table.datasource_properties, ensure_ascii=False) if table.datasource_properties else None,
                is_synced="true",
                status="active",
            )
            session.add(dataset)
            await session.flush()
            logger.info("동기화 upsert [CREATE]: %s (id=%d)", urn, dataset.id)
            result.tables_created += 1

        # 스키마 필드 동기화: 변경 감지 → 스냅샷 저장 → 삭제 후 재삽입
        existing_fields_result = await session.execute(
            select(DatasetSchema).where(DatasetSchema.dataset_id == dataset.id)
        )
        existing_fields = existing_fields_result.scalars().all()

        # 사용자가 설정한 컬럼 logical name 은 sync 에서 알 수 없으므로 보존.
        preserved_display_names = {
            f.field_path: f.display_name for f in existing_fields if f.display_name
        }

        # 변경이 감지되면 스키마 스냅샷 저장
        from app.catalog.service import save_schema_snapshot
        await save_schema_snapshot(
            session, dataset.id, existing_fields, table.columns, from_sync=True,
        )

        for f in existing_fields:
            await session.delete(f)

        for col in table.columns:
            session.add(DatasetSchema(
                dataset_id=dataset.id,
                field_path=col.name,
                display_name=preserved_display_names.get(col.name),
                field_type=col.data_type,
                native_type=col.native_type,
                description=col.comment or None,
                nullable="true" if col.nullable else "false",
                is_primary_key="true" if col.is_primary_key else "false",
                is_unique="true" if col.is_unique else "false",
                is_indexed="true" if col.is_indexed else "false",
                ordinal=col.ordinal,
            ))

    # 4b. 원본에 더 이상 존재하지 않는 데이터셋을 제거됨으로 표시
    #     이 데이터 소스의 전체 데이터셋이 아니라 스캔한 데이터베이스 범위 내에서만 비교
    synced_urns = {
        _generate_urn(datasource_id_str, f"{t.database}.{t.name}")
        for t in tables
    }
    scanned_db_prefixes = [
        f"{datasource_id_str}.{db}." for db in result.databases_scanned
    ]
    existing_rows = await session.execute(
        select(Dataset).where(
            Dataset.datasource_id == datasource.id,
            Dataset.status != "removed",
        )
    )
    for ds in existing_rows.scalars().all():
        # 스캔한 데이터베이스에 속한 데이터셋만 확인
        belongs_to_scanned_db = any(
            ds.urn.startswith(prefix) for prefix in scanned_db_prefixes
        )
        if belongs_to_scanned_db and ds.urn not in synced_urns:
            ds.status = "removed"
            result.tables_removed += 1
            logger.info("제거됨으로 표시: %s", ds.urn)

    await session.commit()
    logger.info("동기화 완료: created=%d, updated=%d, removed=%d, total=%d",
                result.tables_created, result.tables_updated,
                result.tables_removed, result.tables_total)

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
            import asyncio
            asyncio.create_task(generate_descriptions_post_sync(empty_desc_ids))
            logger.info("데이터셋 %d개에 대한 AI 설명 생성 예약",
                        len(empty_desc_ids))
    except Exception as e:
        logger.warning("동기화 후 AI 생성 예약 실패: %s", e)

    # 5. 샘플 데이터를 조회해 parquet 로 업로드
    logger.info("테이블 %d개의 샘플 데이터 수집 중...", len(tables))
    for table in tables:
        # 뷰는 건너뜀 — 샘플 데이터는 베이스 테이블에서만
        if "VIEW" in table.table_type.upper():
            continue

        dataset_name = f"{table.database}.{table.name}"
        if is_pg:
            pg_database = config.get("database", "postgres")
            parquet_bytes = await _fetch_pg_sample_rows(
                host, port, user, password, pg_database, table.database, table.name,
            )
        else:
            parquet_bytes = await _fetch_sample_rows(
                host, port, user, password, table.database, table.name,
            )
        if parquet_bytes:
            logger.info("샘플 업로드 중: %s/%s (%d bytes)",
                        datasource_id_str, dataset_name, len(parquet_bytes))
            ok = await _upload_sample_parquet(
                catalog_url, datasource_id_str, dataset_name, parquet_bytes,
            )
            if ok:
                result.samples_uploaded += 1
        else:
            logger.info("샘플 데이터 없음 %s/%s (빈 테이블 또는 오류)",
                        datasource_id_str, dataset_name)

    logger.info("샘플 업로드 완료: 파일 %d개", result.samples_uploaded)
    return result
