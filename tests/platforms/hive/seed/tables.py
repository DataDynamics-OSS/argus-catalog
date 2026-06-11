"""Hive Metastore 시드 — Thrift API 로 database/table metadata 직접 생성.

HiveServer2 가 없으므로 SQL 을 못 쓴다. ``hmsclient`` 의 thrift 인터페이스로
DB / 테이블 / 컬럼 metadata 만 직접 등록. LOCATION 은 ``s3a://warehouse/...`` 로
지정 — 실제 데이터 파일은 만들지 않고 metadata 만 평가하므로 S3 접근 불필요.
"""

from __future__ import annotations

from typing import Any

from hmsclient.genthrift.hive_metastore.ttypes import (
    Database,
    FieldSchema,
    SerDeInfo,
    StorageDescriptor,
    Table,
)


DATABASES = ("sales",)
S3_WAREHOUSE = "s3a://warehouse"


def _field(name: str, typ: str, comment: str = "") -> FieldSchema:
    return FieldSchema(name=name, type=typ, comment=comment)


def _table(
    db: str,
    name: str,
    cols: list[FieldSchema],
    *,
    partition_cols: list[FieldSchema] | None = None,
    parameters: dict[str, str] | None = None,
) -> Table:
    sd = StorageDescriptor(
        cols=cols,
        location=f"{S3_WAREHOUSE}/{db}/{name}",
        inputFormat="org.apache.hadoop.mapred.TextInputFormat",
        outputFormat="org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
        compressed=False,
        numBuckets=-1,
        serdeInfo=SerDeInfo(
            name=None,
            serializationLib="org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe",
            parameters={"field.delim": ","},
        ),
        bucketCols=[],
        sortCols=[],
        parameters={},
        skewedInfo=None,
        storedAsSubDirectories=False,
    )
    return Table(
        tableName=name,
        dbName=db,
        owner="argus-tests",
        sd=sd,
        partitionKeys=partition_cols or [],
        parameters=parameters or {"comment": f"Test {db}.{name}"},
        tableType="MANAGED_TABLE",
    )


def seed_all(hms: Any) -> None:
    """database/table metadata 를 (재)생성."""
    drop_all(hms)
    for db in DATABASES:
        hms.create_database(Database(
            name=db,
            description=f"Test database {db}",
            locationUri=f"{S3_WAREHOUSE}/{db}.db",
            parameters={},
        ))

    # sales.orders — 평면 테이블
    hms.create_table(_table(
        "sales", "orders",
        cols=[
            _field("order_id", "bigint", "주문 ID"),
            _field("customer_id", "bigint"),
            _field("amount_cents", "bigint", "결제 금액(센트)"),
            _field("status", "string"),
        ],
        parameters={"comment": "테스트용 주문 데이터셋"},
    ))

    # sales.events — 일별 파티션 (ds)
    hms.create_table(_table(
        "sales", "events",
        cols=[
            _field("event_id", "bigint"),
            _field("user_id", "bigint"),
            _field("event_type", "string"),
        ],
        partition_cols=[_field("ds", "string", "파티션 일자")],
    ))


def drop_all(hms: Any) -> None:
    """테스트용 database/table 제거."""
    for db in DATABASES:
        try:
            tables = hms.get_all_tables(db)
            for t in tables:
                try:
                    hms.drop_table(db, t, deleteData=False)
                except Exception:
                    pass
            hms.drop_database(db, deleteData=False, cascade=True)
        except Exception:
            pass
