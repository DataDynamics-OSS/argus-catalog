# SPDX-License-Identifier: Apache-2.0
"""ER 다이어그램 빌더 — 카탈로그에 저장된 메타데이터만으로 ERD 를 구성한다.

관계(FK) 도출 — 3단 폴백:
  1) DDL 파싱: ``CONSTRAINT ... FOREIGN KEY (col) REFERENCES tbl (col)`` —
     가장 정확 (제약명·컬럼 쌍·ON DELETE 까지). DB 접속 불필요.
  2) FK 리니지 엣지(lineage_source=FK, 구 데이터의 relation_type=REFERENCE 포함):
     DDL 이 없는 데이터셋의 폴백.
  3) (미구현) 라이브 information_schema — 메타데이터 동기화가 DDL 을
     채우는 방식으로 흡수하는 것이 바람직.

PK/nullable/unique 는 스키마(catalog_dataset_schemas)에서 직접 읽는다.
범위는 중심 데이터셋 + FK 로 직접 연결된 이웃(1-hop)으로 제한한다.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Dataset, DatasetLineage, DatasetSchema

logger = logging.getLogger(__name__)

# MySQL/MariaDB/PostgreSQL/Oracle/MSSQL 의 FK 구문을 폭넓게 수용:
#   [CONSTRAINT name] FOREIGN KEY (a[, b]) REFERENCES [schema.]tbl (x[, y])
_FK_RE = re.compile(
    r"(?:CONSTRAINT\s+[`\"\[]?(?P<name>\w+)[`\"\]]?\s+)?"
    r"FOREIGN\s+KEY\s*\(\s*(?P<cols>[^)]+?)\s*\)\s*"
    r"REFERENCES\s+(?:[`\"\[]?(?P<ref_schema>\w+)[`\"\]]?\.)?[`\"\[]?(?P<ref_table>\w+)[`\"\]]?\s*"
    r"\(\s*(?P<ref_cols>[^)]+?)\s*\)",
    re.IGNORECASE,
)

_PK_RE = re.compile(r"PRIMARY\s+KEY\s*\(\s*(?P<cols>[^)]+?)\s*\)", re.IGNORECASE)


def _split_cols(raw: str) -> list[str]:
    """"a, `b`, [c]" 같은 컬럼 목록 문자열을 인용 부호를 벗긴 이름 목록으로 나눈다."""
    return [c.strip().strip('`"[]') for c in raw.split(",") if c.strip()]


def parse_ddl_keys(ddl: str | None) -> dict:
    """DDL 에서 PK 컬럼과 FK 제약 목록을 추출한다.

    반환: ``{"pk_columns": [...], "fks": [{constraint_name, columns, ref_schema, ref_table, ref_columns}]}``.
    PK 는 첫 ``PRIMARY KEY (...)`` 절만 본다(컬럼 인라인 ``col INT PRIMARY KEY`` 는 인식하지 않음).
    """
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
            "ref_schema": m.group("ref_schema"),
            "ref_table": m.group("ref_table"),
            "ref_columns": _split_cols(m.group("ref_cols")),
        }
        for m in _FK_RE.finditer(ddl)
    ]
    return {"pk_columns": pk, "fks": fks}


def _table_name(dataset_name: str) -> str:
    """데이터셋 이름의 마지막 세그먼트 = 테이블명 (예: 'sakila.staff' → 'staff')."""
    return dataset_name.rsplit(".", 1)[-1].lower()


def _qualifier(dataset_name: str) -> str:
    """테이블명 앞 부분 = DB(MySQL)·스키마(PostgreSQL) (예: 'sakila.staff' → 'sakila')."""
    return dataset_name.rsplit(".", 1)[0].lower() if "." in dataset_name else ""


class _Resolver:
    """FK 참조 대상 테이블 → 데이터셋 해석.

    테이블명만으로 찾으면 다른 스키마의 같은 이름 테이블이 서로 덮어쓰므로
    (스키마, 테이블) 로 찾는다. FK 에 스키마가 없으면 자식과 같은 스키마를 먼저 보고,
    그래도 없으면 이름이 유일할 때만 연결한다(모호하면 연결하지 않음).
    """

    def __init__(self, datasets) -> None:
        self.by_full: dict[tuple[str, str], object] = {}
        self.by_table: dict[str, list] = {}
        for d in datasets:
            self.by_full[(_qualifier(d.name), _table_name(d.name))] = d
            self.by_table.setdefault(_table_name(d.name), []).append(d)

    def resolve(self, child, ref_schema: str | None, ref_table: str):
        """FK 참조 대상 데이터셋을 찾는다. 없거나 모호하면 None."""
        t = ref_table.lower()
        if ref_schema:
            return self.by_full.get((ref_schema.lower(), t))
        same = self.by_full.get((_qualifier(child.name), t))
        if same is not None:
            return same
        cands = self.by_table.get(t, [])
        return cands[0] if len(cands) == 1 else None


async def build_erd(session: AsyncSession, dataset_id: int) -> dict:
    """중심 데이터셋 + FK 직접 연결 이웃(1-hop)의 ERD. 데이터셋이 없으면 빈 ERD."""
    center = (await session.execute(
        select(Dataset).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()
    if not center:
        logger.debug("ERD: 중심 데이터셋 없음 — 빈 ERD 반환: dataset_id=%d", dataset_id)
        return {"tables": [], "relations": []}
    return await _build(session, center.datasource_id, center_id=dataset_id)


async def build_datasource_erd(session: AsyncSession, datasource_pk: int) -> dict:
    """데이터 소스 전체 ERD — 모든 테이블과 FK 관계."""
    return await _build(session, datasource_pk, center_id=None)


async def _build(session: AsyncSession, datasource_pk: int, center_id: int | None) -> dict:
    """ERD 공통 빌더.

    ``center_id`` 가 있으면 그 데이터셋과 직접 연결된 관계·테이블만, None 이면 데이터 소스 전체.
    반환: ``{"tables": [...], "relations": [...]}`` — relation 은 자식(source) → 부모(target) 방향이며
    ``origin`` 은 DDL/LINEAGE, ``cardinality`` 는 1:1 또는 N:1.
    """
    # 같은 데이터소스의 활성 데이터셋 — (스키마, 테이블)로 FK 참조 대상을 해석.
    # 원본에서 사라진(removed) 데이터셋은 제외하되, 중심 데이터셋은 상태와 무관하게 포함.
    cond = Dataset.status != "removed"
    if center_id is not None:
        cond = or_(cond, Dataset.id == center_id)
    siblings = (await session.execute(
        select(Dataset).where(Dataset.datasource_id == datasource_pk, cond)
    )).scalars().all()
    if not siblings:
        logger.debug("ERD: 데이터 소스에 데이터셋 없음: datasource_pk=%d", datasource_pk)
        return {"tables": [], "relations": []}
    resolver = _Resolver(siblings)
    parsed: dict[int, dict] = {d.id: parse_ddl_keys(d.ddl) for d in siblings}

    # ── 관계 수집 (전체 형제 기준 — 이후 1-hop 으로 필터) ──
    relations: list[dict] = []
    seen: set[tuple] = set()
    for d in siblings:
        for fk in parsed[d.id]["fks"]:
            ref = resolver.resolve(d, fk["ref_schema"], fk["ref_table"])
            if not ref:
                # FK 가 가리키는 테이블이 카탈로그에 없음 — 동기화 누락이거나 스코프 밖.
                # ERD 는 해당 관계만 생략하고 계속 진행한다.
                logger.debug(
                    "ERD: FK 참조 대상을 카탈로그에서 찾지 못함(없음·모호) — %s.%s → %s (제약=%s)",
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

    # ── 폴백: DDL 에서 FK 가 안 나온 쌍은 FK 리니지로 보강 ──
    # metadata-sync 가 등록하는 FK 리니지는 relation_type=READ_WRITE, lineage_source=FK
    # (service.replace_fk_lineage_for_target) 이므로 lineage_source 로 고른다. REFERENCE 는 구 데이터 호환.
    sibling_ids = {d.id for d in siblings}
    pair_set = {(r["source_dataset_id"], r["target_dataset_id"]) for r in relations}
    lineage_rows = (await session.execute(
        select(DatasetLineage).where(
            or_(DatasetLineage.lineage_source == "FK",
                DatasetLineage.relation_type == "REFERENCE"),
            DatasetLineage.source_dataset_id.in_(sibling_ids),
            DatasetLineage.target_dataset_id.in_(sibling_ids),
        )
    )).scalars().all()
    for l in lineage_rows:
        # FK 리니지는 부모(참조 대상) → 자식 방향이므로 ERD 방향(자식 → 부모)으로 뒤집는다
        pair = (l.target_dataset_id, l.source_dataset_id)
        # 같은 쌍이 DDL 로 (어느 방향이든) 이미 잡혔으면 중복 추가하지 않는다
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
    # "단일 컬럼 PK" 인 경우에만 PK 를 유일성 근거로 인정한다. 복합 PK 컬럼에 저장된 is_unique=true
    # (예전 동기화·스키마 편집 그리드가 "PK 면 고유" 로 채운 값)도 유일성 근거로 보지 않는다.
    cols_by_id = {t["dataset_id"]: {c["name"].lower(): c for c in t["columns"]} for t in tables}
    for r in relations:
        card = "N:1"
        src_cols = cols_by_id.get(r["source_dataset_id"], {})
        src_pk_count = max(
            len(parsed.get(r["source_dataset_id"], {}).get("pk_columns", [])),
            sum(1 for c in src_cols.values() if c["is_pk"]),
        )
        if r["source_columns"] and len(r["source_columns"]) == 1:
            c0 = src_cols.get(r["source_columns"][0].lower())
            in_composite_pk = bool(c0 and c0["is_pk"] and src_pk_count > 1)
            if c0 and not in_composite_pk and (c0["is_unique"] or c0["is_pk"]):
                card = "1:1"
        r["cardinality"] = card

    logger.info(
        "ERD 생성 완료: datasource_pk=%d, center=%s, 테이블=%d, 관계=%d (ddl=%d, lineage=%d)",
        datasource_pk, center_id, len(tables), len(relations),
        sum(1 for r in relations if r["origin"] == "DDL"),
        sum(1 for r in relations if r["origin"] == "LINEAGE"),
    )
    return {"tables": tables, "relations": relations}
