#!/usr/bin/env python3
"""Export training data for fine-tuning from catalog DB + synthetic semiconductor data.

Produces ChatML-format JSONL files for supervised fine-tuning (SFT).

Sources:
  1. Manual metadata from catalog DB (datasets with descriptions, column descriptions, tags)
  2. Synthetic semiconductor fab data (standard table schemas with expert descriptions)

Usage:
    python export_training_data.py --output-dir ./training_data
    python export_training_data.py --output-dir ./training_data --db-url postgresql+asyncpg://argus:argus@localhost:5432/argus_catalog
"""

import argparse
import asyncio
import json
import random
from pathlib import Path

SYSTEM_PROMPT = (
    "You are a data catalog assistant that generates metadata for database tables. "
    "Always respond with valid JSON only, no markdown fences or extra text."
)

# ---------------------------------------------------------------------------
# Source 1: Catalog DB export
# ---------------------------------------------------------------------------

async def export_from_db(db_url: str) -> list[dict]:
    """Extract training samples from existing catalog metadata."""
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    samples = []

    async with async_session() as session:
        # 1) Dataset descriptions
        rows = await session.execute(text("""
            SELECT
                d.id, d.name, d.description,
                p.type AS platform_type, p.name AS platform_name
            FROM catalog_datasets d
            JOIN catalog_platforms p ON d.platform_id = p.id
            WHERE d.description IS NOT NULL
              AND d.description != ''
              AND d.status = 'active'
              AND LENGTH(d.description) >= 10
        """))

        for row in rows.fetchall():
            ds_id, ds_name, description, platform_type, platform_name = row

            # Load columns for this dataset
            col_rows = await session.execute(text("""
                SELECT field_path, field_type, native_type, description, nullable,
                       is_primary_key, is_unique, is_indexed
                FROM catalog_dataset_schemas
                WHERE dataset_id = :ds_id
                ORDER BY ordinal
            """), {"ds_id": ds_id})

            columns = []
            for c in col_rows.fetchall():
                columns.append({
                    "field_path": c[0], "field_type": c[1], "native_type": c[2],
                    "description": c[3], "nullable": c[4],
                    "is_primary_key": c[5], "is_unique": c[6], "is_indexed": c[7],
                })

            if not columns:
                continue

            # Build instruction
            parts = ds_name.split(".", 1)
            database = parts[0] if len(parts) > 1 else ""
            table_name = parts[1] if len(parts) > 1 else ds_name

            col_lines = []
            for c in columns[:50]:
                line_parts = [c["field_path"], f"({c['field_type']})"]
                if c["is_primary_key"] == "true":
                    line_parts.append("PK")
                if c["nullable"] == "false":
                    line_parts.append("NOT NULL")
                if c["description"]:
                    line_parts.append(f"-- {c['description']}")
                col_lines.append("  " + " ".join(line_parts))

            instruction = f"""Generate a concise description for this database table.

Table: {database}.{table_name}
Platform: {platform_type}
Columns:
{chr(10).join(col_lines)}

Respond in Korean.
JSON format: {{"description": "...", "confidence": 0.0-1.0}}"""

            output = json.dumps(
                {"description": description, "confidence": 0.95},
                ensure_ascii=False,
            )

            samples.append({
                "task": "description",
                "source": "catalog_db",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": output},
                ],
            })

        # 2) Column descriptions (group by dataset)
        ds_rows = await session.execute(text("""
            SELECT DISTINCT d.id, d.name, d.description, p.type AS platform_type
            FROM catalog_datasets d
            JOIN catalog_platforms p ON d.platform_id = p.id
            JOIN catalog_dataset_schemas s ON s.dataset_id = d.id
            WHERE s.description IS NOT NULL AND s.description != ''
              AND d.status = 'active'
        """))

        for ds_row in ds_rows.fetchall():
            ds_id, ds_name, ds_desc, platform_type = ds_row

            col_rows = await session.execute(text("""
                SELECT field_path, field_type, native_type, description,
                       nullable, is_primary_key, is_unique, is_indexed
                FROM catalog_dataset_schemas
                WHERE dataset_id = :ds_id AND description IS NOT NULL AND description != ''
                ORDER BY ordinal
            """), {"ds_id": ds_id})

            cols_with_desc = []
            for c in col_rows.fetchall():
                cols_with_desc.append({
                    "field_path": c[0], "field_type": c[1], "native_type": c[2],
                    "description": c[3], "nullable": c[4],
                    "is_primary_key": c[5], "is_unique": c[6], "is_indexed": c[7],
                })

            if len(cols_with_desc) < 2:
                continue

            parts = ds_name.split(".", 1)
            database = parts[0] if len(parts) > 1 else ""
            table_name = parts[1] if len(parts) > 1 else ds_name

            col_lines = []
            for i, c in enumerate(cols_with_desc[:80], 1):
                line_parts = [f"{i}. {c['field_path']} ({c['field_type']}"]
                if c.get("native_type"):
                    line_parts[0] += f", {c['native_type']}"
                line_parts[0] += ")"
                col_lines.append(" ".join(line_parts))

            instruction = f"""Generate descriptions for all columns in this table.

Table: {database}.{table_name}
"""
            if ds_desc:
                instruction += f"Table purpose: {ds_desc}\n"

            instruction += f"""
Columns:
{chr(10).join(col_lines)}

Respond in Korean.
JSON format: {{"columns": [{{"name": "col_name", "description": "...", "confidence": 0.0-1.0}}, ...]}}"""

            output_cols = [
                {"name": c["field_path"], "description": c["description"], "confidence": 0.95}
                for c in cols_with_desc
            ]

            output = json.dumps({"columns": output_cols}, ensure_ascii=False)

            samples.append({
                "task": "column_description",
                "source": "catalog_db",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": output},
                ],
            })

        # 3) Tag suggestions
        tag_rows = await session.execute(text("""
            SELECT d.id, d.name, d.description, p.type AS platform_type,
                   array_agg(t.name) AS tags
            FROM catalog_dataset_tags dt
            JOIN catalog_datasets d ON dt.dataset_id = d.id
            JOIN catalog_tags t ON dt.tag_id = t.id
            JOIN catalog_platforms p ON d.platform_id = p.id
            WHERE d.status = 'active'
            GROUP BY d.id, d.name, d.description, p.type
            HAVING count(t.id) >= 1
        """))

        for row in tag_rows.fetchall():
            ds_id, ds_name, ds_desc, platform_type, tags = row

            col_rows = await session.execute(text("""
                SELECT field_path FROM catalog_dataset_schemas
                WHERE dataset_id = :ds_id ORDER BY ordinal LIMIT 50
            """), {"ds_id": ds_id})
            col_names = [c[0] for c in col_rows.fetchall()]

            parts = ds_name.split(".", 1)
            database = parts[0] if len(parts) > 1 else ""
            table_name = parts[1] if len(parts) > 1 else ds_name

            instruction = f"""Suggest relevant classification tags for this database table.

Table: {database}.{table_name}
"""
            if ds_desc:
                instruction += f"Description: {ds_desc}\n"
            instruction += f"""Columns: {', '.join(col_names)}

Suggest 2-5 tags.
Respond in Korean.
JSON format: {{"tags": ["tag1", ...], "new_tags": [{{"name": "...", "description": "..."}}, ...]}}"""

            output = json.dumps(
                {"tags": tags, "new_tags": []},
                ensure_ascii=False,
            )

            samples.append({
                "task": "tag_suggestion",
                "source": "catalog_db",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": output},
                ],
            })

    await engine.dispose()
    return samples


# ---------------------------------------------------------------------------
# Source 2: Synthetic semiconductor fab data
# ---------------------------------------------------------------------------

# Realistic semiconductor MES/FDC/SPC/Yield table definitions
SEMI_TABLES = [
    {
        "database": "mes", "table_name": "tb_lot_mst", "platform_type": "postgresql",
        "description": "로트 마스터 테이블. 반도체 Fab에서 동일 조건으로 처리되는 웨이퍼 묶음(로트)의 기본 정보를 관리한다. 로트 생성부터 출하까지 전 생명주기를 추적한다.",
        "columns": [
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "로트 고유 식별 코드"},
            {"field_path": "prd_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "제품/디바이스 식별 코드"},
            {"field_path": "prd_nm", "field_type": "VARCHAR(200)", "description": "제품 표시 명칭"},
            {"field_path": "lot_qty", "field_type": "INTEGER", "description": "로트 내 웨이퍼 매수"},
            {"field_path": "lot_stat_cd", "field_type": "VARCHAR(20)", "nullable": "false", "description": "로트 현재 상태 코드 (WAIT/RUN/HOLD/BANK/COMP/SCRP)"},
            {"field_path": "prio", "field_type": "INTEGER", "description": "처리 우선순위 (높을수록 우선)"},
            {"field_path": "rte_id", "field_type": "VARCHAR(30)", "description": "공정 라우트 식별 코드"},
            {"field_path": "curr_stp_id", "field_type": "VARCHAR(30)", "description": "현재 공정 단계 식별 코드"},
            {"field_path": "curr_eqp_id", "field_type": "VARCHAR(30)", "description": "현재 처리 중인 장비 식별 코드"},
            {"field_path": "cust_id", "field_type": "VARCHAR(30)", "description": "고객(파운드리 발주처) 식별 코드"},
            {"field_path": "create_dtm", "field_type": "TIMESTAMP", "description": "로트 생성 일시"},
            {"field_path": "update_dtm", "field_type": "TIMESTAMP", "description": "최종 수정 일시"},
        ],
        "tags": ["MES", "로트관리"],
    },
    {
        "database": "mes", "table_name": "tb_lot_hist", "platform_type": "postgresql",
        "description": "로트 공정 이력 테이블. 로트가 각 공정 단계를 거칠 때마다 장비, 레시피, 처리 시간을 기록한다. 사이클 타임 분석 및 공정 추적에 활용된다.",
        "columns": [
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "로트 고유 식별 코드"},
            {"field_path": "stp_seq", "field_type": "INTEGER", "is_primary_key": "true", "nullable": "false", "description": "공정 단계 순번"},
            {"field_path": "stp_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "공정 단계 식별 코드"},
            {"field_path": "oper_id", "field_type": "VARCHAR(30)", "description": "오퍼레이션 식별 코드"},
            {"field_path": "eqp_id", "field_type": "VARCHAR(30)", "description": "처리 장비 식별 코드"},
            {"field_path": "chmb_id", "field_type": "VARCHAR(30)", "description": "처리 챔버 식별 코드"},
            {"field_path": "rcp_id", "field_type": "VARCHAR(50)", "description": "사용된 레시피 식별 코드"},
            {"field_path": "in_dtm", "field_type": "TIMESTAMP", "description": "공정 투입(Track-In) 일시"},
            {"field_path": "out_dtm", "field_type": "TIMESTAMP", "description": "공정 산출(Track-Out) 일시"},
            {"field_path": "proc_time", "field_type": "DECIMAL(12,3)", "description": "공정 처리 소요 시간 (초)"},
            {"field_path": "in_qty", "field_type": "INTEGER", "description": "투입 웨이퍼 수량"},
            {"field_path": "out_qty", "field_type": "INTEGER", "description": "산출 웨이퍼 수량"},
        ],
        "tags": ["MES", "공정이력"],
    },
    {
        "database": "mes", "table_name": "tb_wf_mst", "platform_type": "postgresql",
        "description": "웨이퍼 마스터 테이블. 로트에 속한 개별 웨이퍼의 식별 정보와 현재 상태를 관리한다. 웨이퍼 단위 추적 및 스크랩 관리에 사용된다.",
        "columns": [
            {"field_path": "wf_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "웨이퍼 고유 식별 코드"},
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "소속 로트 식별 코드"},
            {"field_path": "slot_no", "field_type": "SMALLINT", "nullable": "false", "description": "FOUP 내 슬롯 위치 (1~25)"},
            {"field_path": "wf_stat_cd", "field_type": "VARCHAR(20)", "nullable": "false", "description": "웨이퍼 상태 코드 (ACTIVE/SCRAPPED/CONSUMED/DUMMY/MONITOR)"},
            {"field_path": "scrp_rsn", "field_type": "VARCHAR(500)", "description": "폐기 사유 설명"},
            {"field_path": "scrp_dtm", "field_type": "TIMESTAMP", "description": "폐기 처리 일시"},
        ],
        "tags": ["MES", "웨이퍼관리"],
    },
    {
        "database": "mes", "table_name": "tb_eqp_mst", "platform_type": "postgresql",
        "description": "장비 마스터 테이블. Fab 내 모든 공정 장비의 기본 정보를 관리한다. 장비 유형, 베이 위치, 챔버 구성 등의 정보를 포함한다.",
        "columns": [
            {"field_path": "eqp_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "장비 고유 식별 코드"},
            {"field_path": "eqp_nm", "field_type": "VARCHAR(200)", "nullable": "false", "description": "장비 표시 명칭"},
            {"field_path": "eqp_tp_cd", "field_type": "VARCHAR(20)", "nullable": "false", "description": "장비 유형 코드 (ETCH/CVD/PVD/LITHO 등)"},
            {"field_path": "eqp_grp_id", "field_type": "VARCHAR(30)", "description": "장비 그룹 식별 코드"},
            {"field_path": "bay_no", "field_type": "VARCHAR(10)", "description": "클린룸 베이 번호"},
            {"field_path": "area_cd", "field_type": "VARCHAR(20)", "description": "Fab 영역 코드"},
            {"field_path": "vendor", "field_type": "VARCHAR(100)", "description": "장비 제조사명"},
            {"field_path": "chmb_cnt", "field_type": "SMALLINT", "description": "챔버 수"},
            {"field_path": "eqp_stat_cd", "field_type": "VARCHAR(20)", "nullable": "false", "description": "SEMI E10 장비 상태 코드 (PRD/STBY/ENG/SD/UD/NS)"},
        ],
        "tags": ["MES", "장비관리"],
    },
    {
        "database": "mes", "table_name": "tb_eqp_state_hist", "platform_type": "postgresql",
        "description": "장비 상태 이력 테이블. SEMI E10 표준 기반으로 장비의 상태 변경(가동/대기/정비/고장 등) 이력을 기록한다. 가동률(RAM) 및 OEE 산출의 기초 데이터이다.",
        "columns": [
            {"field_path": "eqp_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "장비 식별 코드"},
            {"field_path": "st_dtm", "field_type": "TIMESTAMP", "is_primary_key": "true", "nullable": "false", "description": "상태 시작 일시"},
            {"field_path": "ed_dtm", "field_type": "TIMESTAMP", "description": "상태 종료 일시"},
            {"field_path": "eqp_stat_cd", "field_type": "VARCHAR(20)", "nullable": "false", "description": "SEMI E10 장비 상태 코드"},
            {"field_path": "sub_stat_cd", "field_type": "VARCHAR(20)", "description": "하위 상태 코드 (PM/BM/SETUP 등)"},
            {"field_path": "rsn", "field_type": "VARCHAR(500)", "description": "상태 변경 사유"},
            {"field_path": "dur_sec", "field_type": "DECIMAL(12,3)", "description": "상태 지속 시간 (초)"},
        ],
        "tags": ["MES", "장비관리", "가동률"],
    },
    {
        "database": "fdc", "table_name": "tb_fdc_trace", "platform_type": "postgresql",
        "description": "FDC 센서 트레이스 데이터 테이블. 장비의 각 센서에서 수집된 시계열 데이터를 저장한다. 실시간 이상 감지(FDC) 및 가상 계측(VM) 모델의 입력 데이터로 활용된다.",
        "columns": [
            {"field_path": "trc_id", "field_type": "BIGINT", "is_primary_key": "true", "nullable": "false", "description": "트레이스 레코드 고유 ID"},
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "로트 식별 코드"},
            {"field_path": "wf_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "웨이퍼 식별 코드"},
            {"field_path": "eqp_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "장비 식별 코드"},
            {"field_path": "chmb_id", "field_type": "VARCHAR(30)", "description": "챔버 식별 코드"},
            {"field_path": "rcp_id", "field_type": "VARCHAR(50)", "description": "레시피 식별 코드"},
            {"field_path": "stp_id", "field_type": "VARCHAR(30)", "description": "공정 단계 식별 코드"},
            {"field_path": "snr_nm", "field_type": "VARCHAR(100)", "nullable": "false", "description": "센서 명칭"},
            {"field_path": "snr_val", "field_type": "DECIMAL(15,6)", "nullable": "false", "description": "센서 측정값"},
            {"field_path": "collect_dtm", "field_type": "TIMESTAMP", "nullable": "false", "description": "데이터 수집 일시"},
        ],
        "tags": ["FDC", "센서데이터"],
    },
    {
        "database": "fdc", "table_name": "tb_fdc_summary", "platform_type": "postgresql",
        "description": "FDC 공정 요약 데이터 테이블. 로트/웨이퍼 단위로 센서 트레이스 데이터의 통계값(평균, 최소, 최대, 표준편차)을 집계한다. FDC 모델 학습 및 이상 탐지 판정에 사용된다.",
        "columns": [
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "로트 식별 코드"},
            {"field_path": "wf_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "웨이퍼 식별 코드"},
            {"field_path": "eqp_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "장비 식별 코드"},
            {"field_path": "stp_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "공정 단계 식별 코드"},
            {"field_path": "snr_nm", "field_type": "VARCHAR(100)", "is_primary_key": "true", "nullable": "false", "description": "센서 명칭"},
            {"field_path": "avg_val", "field_type": "DECIMAL(15,6)", "description": "센서값 평균"},
            {"field_path": "min_val", "field_type": "DECIMAL(15,6)", "description": "센서값 최솟값"},
            {"field_path": "max_val", "field_type": "DECIMAL(15,6)", "description": "센서값 최댓값"},
            {"field_path": "stddev_val", "field_type": "DECIMAL(15,6)", "description": "센서값 표준편차"},
            {"field_path": "proc_st_dtm", "field_type": "TIMESTAMP", "description": "공정 시작 일시"},
            {"field_path": "proc_ed_dtm", "field_type": "TIMESTAMP", "description": "공정 종료 일시"},
        ],
        "tags": ["FDC", "공정요약"],
    },
    {
        "database": "fdc", "table_name": "tb_fdc_alarm", "platform_type": "postgresql",
        "description": "FDC 알람 이력 테이블. 센서값이 임계값을 벗어나거나 FDC 모델이 이상을 감지했을 때 발생한 알람을 기록한다. 알람 심각도, 발생/해제 시각, 관련 로트/장비 정보를 포함한다.",
        "columns": [
            {"field_path": "alm_id", "field_type": "BIGINT", "is_primary_key": "true", "nullable": "false", "description": "알람 고유 ID"},
            {"field_path": "eqp_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "알람 발생 장비 식별 코드"},
            {"field_path": "chmb_id", "field_type": "VARCHAR(30)", "description": "알람 발생 챔버 식별 코드"},
            {"field_path": "snr_nm", "field_type": "VARCHAR(100)", "description": "알람 발생 센서 명칭"},
            {"field_path": "alm_cd", "field_type": "VARCHAR(50)", "nullable": "false", "description": "알람 코드"},
            {"field_path": "severity", "field_type": "VARCHAR(20)", "nullable": "false", "description": "알람 심각도 (CRITICAL/WARNING/INFO)"},
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "description": "알람 발생 시 처리 중이던 로트"},
            {"field_path": "wf_id", "field_type": "VARCHAR(30)", "description": "알람 발생 시 처리 중이던 웨이퍼"},
            {"field_path": "alm_val", "field_type": "DECIMAL(15,6)", "description": "알람 발생 시 센서 측정값"},
            {"field_path": "thld_usl", "field_type": "DECIMAL(15,6)", "description": "상한 임계값"},
            {"field_path": "thld_lsl", "field_type": "DECIMAL(15,6)", "description": "하한 임계값"},
            {"field_path": "alm_st_dtm", "field_type": "TIMESTAMP", "nullable": "false", "description": "알람 발생 일시"},
            {"field_path": "alm_ed_dtm", "field_type": "TIMESTAMP", "description": "알람 해제 일시"},
        ],
        "tags": ["FDC", "알람"],
    },
    {
        "database": "spc", "table_name": "tb_spc_data", "platform_type": "postgresql",
        "description": "SPC 측정 데이터 테이블. 계측 장비에서 수집된 공정 파라미터 측정값과 규격/관리 한계 대비 판정 결과를 저장한다. 공정 안정성 모니터링 및 관리도(Control Chart) 생성에 활용된다.",
        "columns": [
            {"field_path": "spc_id", "field_type": "BIGINT", "is_primary_key": "true", "nullable": "false", "description": "SPC 데이터 고유 ID"},
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "로트 식별 코드"},
            {"field_path": "wf_id", "field_type": "VARCHAR(30)", "description": "웨이퍼 식별 코드"},
            {"field_path": "eqp_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "계측 장비 식별 코드"},
            {"field_path": "stp_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "공정 단계 식별 코드"},
            {"field_path": "param_nm", "field_type": "VARCHAR(100)", "nullable": "false", "description": "측정 파라미터 명칭 (예: THK, CD, OVL)"},
            {"field_path": "site_no", "field_type": "SMALLINT", "description": "웨이퍼 내 계측 사이트 번호"},
            {"field_path": "mval", "field_type": "DECIMAL(18,6)", "nullable": "false", "description": "실측값"},
            {"field_path": "tgt_val", "field_type": "DECIMAL(18,6)", "description": "목표값 (Target)"},
            {"field_path": "usl", "field_type": "DECIMAL(18,6)", "description": "규격 상한 (Upper Spec Limit)"},
            {"field_path": "lsl", "field_type": "DECIMAL(18,6)", "description": "규격 하한 (Lower Spec Limit)"},
            {"field_path": "ucl", "field_type": "DECIMAL(18,6)", "description": "관리 상한 (Upper Control Limit)"},
            {"field_path": "lcl", "field_type": "DECIMAL(18,6)", "description": "관리 하한 (Lower Control Limit)"},
            {"field_path": "jdg_cd", "field_type": "VARCHAR(10)", "nullable": "false", "description": "판정 결과 코드 (OK/OOC/OOS/TREND)"},
            {"field_path": "meas_dtm", "field_type": "TIMESTAMP", "nullable": "false", "description": "측정 일시"},
        ],
        "tags": ["SPC", "품질관리"],
    },
    {
        "database": "yield", "table_name": "tb_wafer_sort", "platform_type": "postgresql",
        "description": "웨이퍼 소트(EDS) 결과 테이블. 웨이퍼 레벨 전기적 테스트(Electrical Die Sorting) 결과를 다이 좌표별로 저장한다. 다이 수율 계산, 빈맵 분석, 결함 패턴 분류에 활용된다.",
        "columns": [
            {"field_path": "wf_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "웨이퍼 식별 코드"},
            {"field_path": "die_x", "field_type": "SMALLINT", "is_primary_key": "true", "nullable": "false", "description": "다이 X 좌표"},
            {"field_path": "die_y", "field_type": "SMALLINT", "is_primary_key": "true", "nullable": "false", "description": "다이 Y 좌표"},
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "소속 로트 식별 코드"},
            {"field_path": "prd_id", "field_type": "VARCHAR(30)", "description": "제품 식별 코드"},
            {"field_path": "hard_bin", "field_type": "SMALLINT", "nullable": "false", "description": "하드빈 코드 (최종 하드웨어 분류)"},
            {"field_path": "soft_bin", "field_type": "SMALLINT", "description": "소프트빈 코드 (소프트웨어 세부 분류)"},
            {"field_path": "pass_yn", "field_type": "CHAR(1)", "nullable": "false", "description": "양불 판정 (Y: 양품, N: 불량)"},
            {"field_path": "test_pgm", "field_type": "VARCHAR(100)", "description": "테스트 프로그램 명칭"},
            {"field_path": "test_dtm", "field_type": "TIMESTAMP", "description": "테스트 수행 일시"},
        ],
        "tags": ["수율", "테스트"],
    },
    {
        "database": "yield", "table_name": "tb_yield_summary", "platform_type": "postgresql",
        "description": "수율 요약 테이블. 로트/웨이퍼 단위로 양품수, 불량수, 수율을 집계한다. 일별/주별/월별 수율 트렌드 분석 및 수율 목표 대비 실적 모니터링에 사용된다.",
        "columns": [
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "로트 식별 코드"},
            {"field_path": "wf_id", "field_type": "VARCHAR(30)", "is_primary_key": "true", "nullable": "false", "description": "웨이퍼 식별 코드"},
            {"field_path": "prd_id", "field_type": "VARCHAR(30)", "description": "제품 식별 코드"},
            {"field_path": "good_qty", "field_type": "INTEGER", "nullable": "false", "description": "양품 다이 수"},
            {"field_path": "def_qty", "field_type": "INTEGER", "nullable": "false", "description": "불량 다이 수"},
            {"field_path": "tot_qty", "field_type": "INTEGER", "nullable": "false", "description": "전체 다이 수"},
            {"field_path": "die_yld", "field_type": "DECIMAL(7,4)", "nullable": "false", "description": "다이 수율 (%) = good_qty / tot_qty * 100"},
            {"field_path": "test_dtm", "field_type": "TIMESTAMP", "description": "테스트 수행 일시"},
        ],
        "tags": ["수율", "집계"],
    },
    {
        "database": "yield", "table_name": "tb_defect_map", "platform_type": "postgresql",
        "description": "웨이퍼 결함 맵 테이블. 검사 장비에서 검출된 결함의 웨이퍼 내 좌표, 크기, 유형을 저장한다. 결함 분포 분석, Kill Ratio 계산, 수율 손실 원인 분석에 활용된다.",
        "columns": [
            {"field_path": "dfct_id", "field_type": "BIGINT", "is_primary_key": "true", "nullable": "false", "description": "결함 고유 ID"},
            {"field_path": "wf_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "웨이퍼 식별 코드"},
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "로트 식별 코드"},
            {"field_path": "insp_eqp_id", "field_type": "VARCHAR(30)", "description": "검사 장비 식별 코드"},
            {"field_path": "stp_id", "field_type": "VARCHAR(30)", "description": "검사 공정 단계 식별 코드"},
            {"field_path": "dfct_x", "field_type": "DECIMAL(10,4)", "nullable": "false", "description": "결함 X 좌표 (um)"},
            {"field_path": "dfct_y", "field_type": "DECIMAL(10,4)", "nullable": "false", "description": "결함 Y 좌표 (um)"},
            {"field_path": "dfct_sz", "field_type": "DECIMAL(10,4)", "description": "결함 크기 (um)"},
            {"field_path": "dfct_tp_cd", "field_type": "VARCHAR(20)", "nullable": "false", "description": "결함 유형 코드 (PTCL/SCRT/PATTERN/BRIDGE/OPEN 등)"},
            {"field_path": "insp_tp_cd", "field_type": "VARCHAR(20)", "description": "검사 유형 코드 (OPTICAL/SEM/EBI)"},
            {"field_path": "insp_dtm", "field_type": "TIMESTAMP", "nullable": "false", "description": "검사 수행 일시"},
        ],
        "tags": ["수율", "결함관리"],
    },
    {
        "database": "mes", "table_name": "tb_hold_hist", "platform_type": "postgresql",
        "description": "홀드 이력 테이블. 로트/웨이퍼의 진행 보류(Hold)와 해제(Release) 이력을 기록한다. 홀드 사유, 기간, 해제자 정보를 추적하여 품질 이슈 대응 내역을 관리한다.",
        "columns": [
            {"field_path": "hold_id", "field_type": "BIGINT", "is_primary_key": "true", "nullable": "false", "description": "홀드 이력 고유 ID"},
            {"field_path": "lot_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "홀드 대상 로트 식별 코드"},
            {"field_path": "wf_id", "field_type": "VARCHAR(30)", "description": "홀드 대상 웨이퍼 (웨이퍼 단위 홀드 시)"},
            {"field_path": "hold_tp_cd", "field_type": "VARCHAR(20)", "nullable": "false", "description": "홀드 유형 코드 (QC/ENG/EQP/CUST/MTL)"},
            {"field_path": "hold_rsn", "field_type": "VARCHAR(500)", "nullable": "false", "description": "홀드 사유 상세 설명"},
            {"field_path": "hold_dtm", "field_type": "TIMESTAMP", "nullable": "false", "description": "홀드 설정 일시"},
            {"field_path": "release_dtm", "field_type": "TIMESTAMP", "description": "홀드 해제 일시"},
            {"field_path": "release_rsn", "field_type": "VARCHAR(500)", "description": "해제 사유"},
        ],
        "tags": ["MES", "품질관리"],
    },
    {
        "database": "mes", "table_name": "tb_pm_hist", "platform_type": "postgresql",
        "description": "예방정비(PM) 이력 테이블. 장비/챔버별 PM 수행 이력을 기록한다. PM 유형, 소요 시간, 수행 내용을 추적하여 MTBF/MTTR 산출 및 PM 주기 최적화에 활용된다.",
        "columns": [
            {"field_path": "pm_id", "field_type": "BIGINT", "is_primary_key": "true", "nullable": "false", "description": "PM 이력 고유 ID"},
            {"field_path": "eqp_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "PM 대상 장비 식별 코드"},
            {"field_path": "chmb_id", "field_type": "VARCHAR(30)", "description": "PM 대상 챔버 (챔버 PM인 경우)"},
            {"field_path": "pm_tp_cd", "field_type": "VARCHAR(20)", "nullable": "false", "description": "PM 유형 코드 (DAILY/WEEKLY/MONTHLY/QUARTERLY/ANNUAL/CHAMBER)"},
            {"field_path": "pm_st_dtm", "field_type": "TIMESTAMP", "nullable": "false", "description": "PM 시작 일시"},
            {"field_path": "pm_ed_dtm", "field_type": "TIMESTAMP", "description": "PM 종료 일시"},
            {"field_path": "pm_dur_sec", "field_type": "DECIMAL(12,3)", "description": "PM 소요 시간 (초)"},
            {"field_path": "pm_desc", "field_type": "TEXT", "description": "PM 수행 내용 상세"},
        ],
        "tags": ["MES", "장비관리", "PM"],
    },
    {
        "database": "mes", "table_name": "tb_rcp_mst", "platform_type": "postgresql",
        "description": "레시피 마스터 테이블. 장비별 공정 레시피의 기본 정보와 버전을 관리한다. 레시피 파라미터 설정값의 이력 관리 및 R2R(Run-to-Run) 제어의 기준 데이터로 활용된다.",
        "columns": [
            {"field_path": "rcp_id", "field_type": "VARCHAR(50)", "is_primary_key": "true", "nullable": "false", "description": "레시피 고유 식별 코드"},
            {"field_path": "rcp_nm", "field_type": "VARCHAR(200)", "nullable": "false", "description": "레시피 표시 명칭"},
            {"field_path": "eqp_grp_id", "field_type": "VARCHAR(30)", "nullable": "false", "description": "대상 장비 그룹 식별 코드"},
            {"field_path": "proc_tp_cd", "field_type": "VARCHAR(20)", "description": "공정 유형 코드"},
            {"field_path": "ver", "field_type": "VARCHAR(20)", "description": "레시피 버전"},
            {"field_path": "create_dtm", "field_type": "TIMESTAMP", "description": "레시피 생성 일시"},
            {"field_path": "update_dtm", "field_type": "TIMESTAMP", "description": "최종 수정 일시"},
        ],
        "tags": ["MES", "레시피관리"],
    },
]


def build_synthetic_samples() -> list[dict]:
    """Convert synthetic semiconductor table definitions to training samples."""
    samples = []

    # Load glossary from seed file
    seed_dir = Path(__file__).parent / "seed" / "semiconductor"
    glossary_words = []
    if (seed_dir / "words.json").exists():
        with open(seed_dir / "words.json", encoding="utf-8") as f:
            glossary_words = json.load(f)

    glossary_section = ""
    if glossary_words:
        # Build full glossary for inclusion
        glines = []
        for g in glossary_words[:60]:
            glines.append(f"  {g['word_abbr']}: {g['word_name']} ({g['word_english']})")
        glossary_section = (
            "\n== Terminology glossary (abbreviation → meaning) ==\n"
            + "\n".join(glines)
            + "\n"
        )

    for tbl in SEMI_TABLES:
        db = tbl["database"]
        tn = tbl["table_name"]
        pt = tbl["platform_type"]
        desc = tbl["description"]
        cols = tbl["columns"]

        # --- Description task ---
        col_lines = []
        for c in cols:
            parts = [c["field_path"], f"({c['field_type']})"]
            if c.get("is_primary_key") == "true":
                parts.append("PK")
            if c.get("nullable") == "false":
                parts.append("NOT NULL")
            col_lines.append("  " + " ".join(parts))

        instruction = f"""Generate a concise description for this database table.
{glossary_section}
Table: {db}.{tn}
Platform: {pt}
Columns:
{chr(10).join(col_lines)}

Respond in Korean.
JSON format: {{"description": "...", "confidence": 0.0-1.0}}"""

        output = json.dumps(
            {"description": desc, "confidence": 0.95},
            ensure_ascii=False,
        )

        samples.append({
            "task": "description",
            "source": "synthetic_semi",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": instruction},
                {"role": "assistant", "content": output},
            ],
        })

        # --- Column description task ---
        col_desc_lines = []
        for i, c in enumerate(cols, 1):
            col_desc_lines.append(f"{i}. {c['field_path']} ({c['field_type']})")

        col_instruction = f"""Generate descriptions for all columns in this table.
{glossary_section}
Table: {db}.{tn}
Table purpose: {desc}

Columns:
{chr(10).join(col_desc_lines)}

Respond in Korean.
JSON format: {{"columns": [{{"name": "col_name", "description": "...", "confidence": 0.0-1.0}}, ...]}}"""

        col_output = json.dumps(
            {"columns": [
                {"name": c["field_path"], "description": c["description"], "confidence": 0.95}
                for c in cols
            ]},
            ensure_ascii=False,
        )

        samples.append({
            "task": "column_description",
            "source": "synthetic_semi",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": col_instruction},
                {"role": "assistant", "content": col_output},
            ],
        })

        # --- Tag suggestion task ---
        col_names = [c["field_path"] for c in cols]
        tag_instruction = f"""Suggest relevant classification tags for this database table.

Table: {db}.{tn}
Description: {desc}
Columns: {', '.join(col_names)}

Suggest 2-5 tags.
Respond in Korean.
JSON format: {{"tags": ["tag1", ...], "new_tags": [{{"name": "...", "description": "..."}}, ...]}}"""

        tag_output = json.dumps(
            {"tags": tbl.get("tags", []), "new_tags": []},
            ensure_ascii=False,
        )

        samples.append({
            "task": "tag_suggestion",
            "source": "synthetic_semi",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": tag_instruction},
                {"role": "assistant", "content": tag_output},
            ],
        })

    return samples


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Export training data for fine-tuning")
    parser.add_argument(
        "--output-dir", default="./training_data",
        help="Output directory for JSONL files",
    )
    parser.add_argument(
        "--db-url",
        default="postgresql+asyncpg://argus:argus@localhost:5432/argus_catalog",
        help="Database URL for catalog DB",
    )
    parser.add_argument(
        "--split-ratio", type=float, default=0.9,
        help="Train/eval split ratio (default: 0.9)",
    )
    parser.add_argument(
        "--skip-db", action="store_true",
        help="Skip DB export, only use synthetic data",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_samples = []

    # Source 1: Catalog DB
    if not args.skip_db:
        print("Exporting from catalog DB...")
        try:
            db_samples = await export_from_db(args.db_url)
            print(f"  DB samples: {len(db_samples)}")
            all_samples.extend(db_samples)
        except Exception as e:
            print(f"  DB export failed: {e}")
            print("  Continuing with synthetic data only...")

    # Source 2: Synthetic semiconductor data
    print("Building synthetic semiconductor samples...")
    semi_samples = build_synthetic_samples()
    print(f"  Synthetic samples: {len(semi_samples)}")
    all_samples.extend(semi_samples)

    # Summary by task type
    by_task = {}
    by_source = {}
    for s in all_samples:
        task = s.get("task", "unknown")
        source = s.get("source", "unknown")
        by_task[task] = by_task.get(task, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1

    print(f"\nTotal samples: {len(all_samples)}")
    print(f"  By task:   {by_task}")
    print(f"  By source: {by_source}")

    # Shuffle and split
    random.seed(42)
    random.shuffle(all_samples)

    split_idx = int(len(all_samples) * args.split_ratio)
    train_samples = all_samples[:split_idx]
    eval_samples = all_samples[split_idx:]

    # Write JSONL (messages only, strip metadata)
    train_path = output_dir / "train.jsonl"
    eval_path = output_dir / "eval.jsonl"

    for path, samples in [(train_path, train_samples), (eval_path, eval_samples)]:
        with open(path, "w", encoding="utf-8") as f:
            for s in samples:
                record = {"messages": s["messages"]}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"\nOutput:")
    print(f"  Train: {train_path} ({len(train_samples)} samples)")
    print(f"  Eval:  {eval_path} ({len(eval_samples)} samples)")

    # Also save full metadata version for analysis
    full_path = output_dir / "all_samples.json"
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(all_samples, f, ensure_ascii=False, indent=2)
    print(f"  Full:  {full_path}")


if __name__ == "__main__":
    asyncio.run(main())
