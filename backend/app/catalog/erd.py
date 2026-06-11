"""ER 다이어그램 빌더 — 카탈로그에 저장된 메타데이터만으로 ERD 를 구성한다.

관계(FK) 도출 — 3단 폴백:
  1) DDL 파싱: ``CONSTRAINT ... FOREIGN KEY (col) REFERENCES tbl (col)`` —
     가장 정확 (제약명·컬럼 쌍·ON DELETE 까지). DB 접속 불필요.
  2) 리니지 REFERENCE 엣지: DDL 이 없는 데이터셋의 폴백.
  3) (미구현) 라이브 information_schema — 메타데이터 동기화가 DDL 을
     채우는 방식으로 흡수하는 것이 바람직.

PK/nullable/unique 는 스키마(catalog_dataset_schemas)에서 직접 읽는다.
범위는 중심 데이터셋 + FK 로 직접 연결된 이웃(1-hop)으로 제한한다.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, DatasetLineage, DatasetSchema

logger = logging.getLogger(__name__)

# MySQL/MariaDB/PostgreSQL/Oracle/MSSQL 의 FK 구문을 폭넓게 수용:
#   [CONSTRAINT name] FOREIGN KEY (a[, b]) REFERENCES [schema.]tbl (x[, y])
_FK_RE = re.compile(
    r"(?:CONSTRAINT\s+[`\"\[]?(?P<name>\w+)[`\"\]]?\s+)?"
    r"FOREIGN\s+KEY\s*\(\s*(?P<cols>[^)]+?)\s*\)\s*"
    r"REFERENCES\s+[`\"\[]?(?:\w+[`\"\]]?\.[`\"\[]?)?(?P<ref_table>\w+)[`\"\]]?\s*"
    r"\(\s*(?P<ref_cols>[^)]+?)\s*\)",
    re.IGNORECASE,
)

_PK_RE = re.compile(r"PRIMARY\s+KEY\s*\(\s*(?P<cols>[^)]+?)\s*\)", re.IGNORECASE)


def _split_cols(raw: str) -> list[str]:
    return [c.strip().strip('`"[]') for c in raw.split(",") if c.strip()]


def parse_ddl_keys(ddl: str | None) -> dict:
    """DDL 에서 PK 컬럼과 FK 제약 목록을 추출한다."""
    if not ddl:
        return {"pk_columns": [], "fks": []}
    pk: list[str] = []
    m = _PK_RE.search(ddl)
    if m:
        pk = _split_cols(m.group("cols"))
    fks = [
        {
            "constraint_name": m.group("name"),
            "columns": _split_cols(m.group("cols")),
            "ref_table": m.group("ref_table"),
            "ref_columns": _split_cols(m.group("ref_cols")),
        }
        for m in _FK_RE.finditer(ddl)
    ]
    return {"pk_columns": pk, "fks": fks}


def _table_name(dataset_name: str) -> str:
    """데이터셋 이름의 마지막 세그먼트 = 테이블명 (예: 'sakila.staff' → 'staff')."""
    return dataset_name.rsplit(".", 1)[-1].lower()


async def build_erd(session: AsyncSession, dataset_id: int) -> dict:
    """중심 데이터셋 + FK 직접 연결 이웃(1-hop)의 ERD."""
    center = (await session.execute(
        select(Dataset).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()
    if not center:
        return {"tables": [], "relations": []}
    return await _build(session, center.datasource_id, center_id=dataset_id)


async def build_datasource_erd(session: AsyncSession, datasource_pk: int) -> dict:
    """데이터 소스 전체 ERD — 모든 테이블과 FK 관계."""
    return await _build(session, datasource_pk, center_id=None)


async def _build(session: AsyncSession, datasource_pk: int, center_id: int | None) -> dict:
    # 같은 데이터소스의 전체 데이터셋 — 테이블명으로 FK 참조 대상을 해석
    siblings = (await session.execute(
        select(Dataset).where(Dataset.datasource_id == datasource_pk)
    )).scalars().all()
    if not siblings:
        return {"tables": [], "relations": []}
    by_table: dict[str, Dataset] = {_table_name(d.name): d for d in siblings}
    parsed: dict[int, dict] = {d.id: parse_ddl_keys(d.ddl) for d in siblings}

    # ── 관계 수집 (전체 형제 기준 — 이후 1-hop 으로 필터) ──
    relations: list[dict] = []
    seen: set[tuple] = set()
    for d in siblings:
        for fk in parsed[d.id]["fks"]:
            ref = by_table.get(fk["ref_table"].lower())
            if not ref:
                # FK 가 가리키는 테이블이 카탈로그에 없음 — 동기화 누락이거나 스코프 밖.
                # ERD 는 해당 관계만 생략하고 계속 진행한다.
                logger.warning(
                    "ERD: FK 참조 대상이 카탈로그에 없음 — %s.%s → %s (제약=%s)",
                    _table_name(d.name), ",".join(fk["columns"]),
                    fk["ref_table"], fk["constraint_name"],
                )
                continue
            key = (d.id, ref.id, tuple(fk["columns"]))
            if key in seen:
                continue
            seen.add(key)
            relations.append({
                "source_dataset_id": d.id,        # FK 보유(자식) 테이블
                "source_columns": fk["columns"],
                "target_dataset_id": ref.id,      # 참조 대상(부모) 테이블
                "target_columns": fk["ref_columns"],
                "constraint_name": fk["constraint_name"],
                "origin": "DDL",
            })

    # ── 폴백: DDL 에서 FK 가 안 나온 쌍은 리니지 REFERENCE 로 보강 ──
    sibling_ids = {d.id for d in siblings}
    pair_set = {(r["source_dataset_id"], r["target_dataset_id"]) for r in relations}
    lineage_rows = (await session.execute(
        select(DatasetLineage).where(
            DatasetLineage.relation_type == "REFERENCE",
            DatasetLineage.source_dataset_id.in_(sibling_ids),
            DatasetLineage.target_dataset_id.in_(sibling_ids),
        )
    )).scalars().all()
    for l in lineage_rows:
        # 리니지 REFERENCE 는 부모(참조 대상) → 자식 방향이므로 ERD 방향(자식 → 부모)으로 뒤집는다
        pair = (l.target_dataset_id, l.source_dataset_id)
        if pair in pair_set or (pair[1], pair[0]) in pair_set:
            continue
        pair_set.add(pair)
        relations.append({
            "source_dataset_id": l.target_dataset_id,
            "source_columns": [],
            "target_dataset_id": l.source_dataset_id,
            "target_columns": [],
            "constraint_name": None,
            "origin": "LINEAGE",
        })

    # ── 스코프: center 모드는 1-hop, datasource 모드는 전체 ──
    if center_id is not None:
        relations = [
            r for r in relations
            if r["source_dataset_id"] == center_id or r["target_dataset_id"] == center_id
        ]
        scope_ids = {center_id}
        for r in relations:
            scope_ids.add(r["source_dataset_id"])
            scope_ids.add(r["target_dataset_id"])
    else:
        scope_ids = {d.id for d in siblings}

    # ── 테이블(노드) 구성: 스키마 + DDL PK 병합 ──
    schema_rows = (await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id.in_(scope_ids))
        .order_by(DatasetSchema.id)
    )).scalars().all()
    cols_by_ds: dict[int, list] = {}
    for f in schema_rows:
        cols_by_ds.setdefault(f.dataset_id, []).append(f)

    # FK 컬럼 표시용: dataset_id → {컬럼명 → 참조 테이블명}
    fk_cols: dict[int, dict[str, str]] = {}
    name_by_id = {d.id: d.name for d in siblings}
    for r in relations:
        for c in r["source_columns"]:
            fk_cols.setdefault(r["source_dataset_id"], {})[c.lower()] = (
                _table_name(name_by_id[r["target_dataset_id"]])
            )

    tables = []
    for d in siblings:
        if d.id not in scope_ids:
            continue
        ddl_pk = {c.lower() for c in parsed[d.id]["pk_columns"]}
        columns = []
        for f in cols_by_ds.get(d.id, []):
            col = f.field_path
            columns.append({
                "name": col,
                "type": f.native_type or f.field_type,
                "nullable": f.nullable == "true",
                "is_pk": f.is_primary_key == "true" or col.lower() in ddl_pk,
                "is_unique": f.is_unique == "true",
                "fk_to": fk_cols.get(d.id, {}).get(col.lower()),
            })
        tables.append({
            "dataset_id": d.id,
            "name": _table_name(d.name),
            "full_name": d.name,
            "quality_status": d.quality_status,
            "is_center": d.id == center_id,
            "columns": columns,
        })

    # ── 카디널리티: FK 컬럼이 그 자체로 유일하면 1:1, 아니면 N:1 (자식 N — 부모 1) ──
    # 주의: 복합 PK 의 일부 컬럼(예: film_actor.film_id)은 단독으로 유일하지 않으므로
    # "단일 컬럼 PK" 인 경우에만 PK 를 유일성 근거로 인정한다.
    for r in relations:
        card = "N:1"
        src_cols = {c["name"].lower(): c for t in tables if t["dataset_id"] == r["source_dataset_id"]
                    for c in t["columns"]}
        src_pk_count = len(parsed.get(r["source_dataset_id"], {}).get("pk_columns", []))
        if r["source_columns"] and len(r["source_columns"]) == 1:
            c0 = src_cols.get(r["source_columns"][0].lower())
            if c0 and (c0["is_unique"] or (c0["is_pk"] and src_pk_count == 1)):
                card = "1:1"
        r["cardinality"] = card

    logger.info(
        "ERD 생성 완료: datasource_pk=%d, center=%s, 테이블=%d, 관계=%d (ddl=%d, lineage=%d)",
        datasource_pk, center_id, len(tables), len(relations),
        sum(1 for r in relations if r["origin"] == "DDL"),
        sum(1 for r in relations if r["origin"] == "LINEAGE"),
    )
    return {"tables": tables, "relations": relations}
