# SPDX-License-Identifier: Apache-2.0
"""Temporal 액티비티.

워크플로우(workflow.py) 가 호출하는 비결정적(non-deterministic) 작업.
DB I/O, 외부 HTTP 호출, 통지 발송 등은 반드시 액티비티로 분리한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from temporalio import activity

from app.catalog.models import (
    Dataset,
    DatasetColumnMapping,
    DatasetLineage,
    DatasetSchema,
)
from app.catalog.service import save_schema_snapshot
from app.change_mgmt import models, schemas
from app.core.database import async_session as async_session_factory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 액티비티 입출력 DTO (dataclass — Temporal 직렬화 친화)
# ---------------------------------------------------------------------------

@dataclass
class ImpactReport:
    change_type: str
    changed_columns: list[str]              # 변경되는 컬럼 (upsert + drop)
    affected_downstream_count: int          # 다운스트림 데이터셋 수 (다단계)
    affected_dataset_ids: list[int]
    affected_columns: list[str]             # 컬럼 수준 영향 ("<dataset_id>.<column>")
    affected_consumer_count: int
    affected_consumers: list[int]
    mission_critical_consumers: int
    risk_score: float                       # 0.0 ~ 1.0
    summary: str


@dataclass
class NotifyInput:
    cr_id: int
    stage: str                       # NotificationStage 값
    channels: list[str]              # ["EMAIL", "SLACK", ...]


@dataclass
class ApplyInput:
    cr_id: int


# ---------------------------------------------------------------------------
# 영향 분석
# ---------------------------------------------------------------------------

def _parse_schema_after(raw: str | None) -> tuple[list[dict], list[str]]:
    """CR 의 ``schema_after`` JSON 을 (upsert 필드 목록, drop 컬럼 목록) 으로 해석.

    지원 형식 (부분 변경 명세):
      1. 구조화: ``{"fields": [{"field_path": "...", "field_type": "...", ...}],
                    "drop": ["old_col"]}``
      2. 필드 배열: ``[{"field_path": "...", "field_type": "..."}, ...]``
      3. 평면 맵: ``{"email": "VARCHAR(255)"}``  (키=컬럼, 값=타입)
      4. 속성 맵: ``{"email": {"field_type": "VARCHAR(255)", "nullable": "false"}}``

    각 upsert 는 최소 ``field_path`` 와 ``field_type`` 를 갖도록 정규화한다.
    """
    if not raw:
        return [], []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        # JSON 이 아니면 변경 컬럼을 특정할 수 없다 — 영향 분석은 컬럼 0개로 진행.
        logger.warning("schema_after 가 JSON 이 아님 — 컬럼 단위 분석 생략")
        return [], []

    def _normalize(field_path: str, value) -> dict:
        if isinstance(value, dict):
            d = dict(value)
            d["field_path"] = field_path
            d.setdefault("field_type", d.pop("type", "STRING"))
            return d
        return {"field_path": field_path, "field_type": str(value)}

    # 1. 구조화 형식
    if isinstance(data, dict) and "fields" in data:
        upserts = []
        for f in data.get("fields", []):
            if isinstance(f, dict) and f.get("field_path"):
                f.setdefault("field_type", "STRING")
                upserts.append(f)
        drops = [str(c) for c in data.get("drop", [])]
        return upserts, drops

    # 2. 필드 배열
    if isinstance(data, list):
        upserts = []
        for f in data:
            if isinstance(f, dict) and f.get("field_path"):
                f.setdefault("field_type", "STRING")
                upserts.append(f)
        return upserts, []

    # 3 / 4. 평면 맵
    if isinstance(data, dict):
        return [_normalize(k, v) for k, v in data.items()], []

    return [], []


# 변경 유형별 기본 위험도
_BASE_RISK = {"BREAKING": 0.9, "NON_BREAKING": 0.4, "ADDITIVE": 0.2, "COSMETIC": 0.05}


async def _downstream_dataset_ids(
    session, root_id: int, max_hops: int = 5
) -> list[int]:
    """리니지 그래프를 따라 다운스트림(영향받는) 데이터셋을 다단계로 수집한다.

    A → B → C 에서 A 변경 시 B, C 모두 영향. 순환 방지를 위해 visited 추적.
    """
    visited: set[int] = set()
    frontier: set[int] = {root_id}
    for _ in range(max_hops):
        if not frontier:
            break
        rows = (
            await session.execute(
                select(DatasetLineage.target_dataset_id).where(
                    DatasetLineage.source_dataset_id.in_(frontier)
                )
            )
        ).scalars().all()
        nxt = {r for r in rows if r not in visited and r != root_id}
        visited |= nxt
        frontier = nxt
    return sorted(visited)


async def _affected_downstream_columns(
    session, root_id: int, changed_columns: set[str]
) -> list[str]:
    """변경 컬럼을 참조하는 직속 다운스트림 컬럼을 컬럼 매핑에서 찾는다.

    반환 형식: ``"<target_dataset_id>.<target_column>"``.
    """
    if not changed_columns:
        return []
    edges = (
        await session.execute(
            select(DatasetLineage).where(DatasetLineage.source_dataset_id == root_id)
        )
    ).scalars().all()
    affected: list[str] = []
    for edge in edges:
        maps = (
            await session.execute(
                select(DatasetColumnMapping).where(
                    DatasetColumnMapping.dataset_lineage_id == edge.id,
                    DatasetColumnMapping.source_column.in_(changed_columns),
                )
            )
        ).scalars().all()
        for m in maps:
            affected.append(f"{edge.target_dataset_id}.{m.target_column}")
    return sorted(set(affected))


@activity.defn(name="analyze_impact")
async def analyze_impact(cr_id: int) -> ImpactReport:
    """리니지 + 소비자 등록부 기준으로 영향 범위와 위험도를 산정한다.

    - 다운스트림 데이터셋: 리니지 그래프 다단계 traversal (간접 영향 포함)
    - 컬럼 수준 영향: 변경 컬럼을 참조하는 다운스트림 컬럼 매핑
    - 위험도: 변경 유형(base) + reach(다운스트림/소비자 규모) + 소비자 중요도 가중
    """
    async with async_session_factory() as session:
        cr = await session.get(models.ChangeRequest, cr_id)
        if cr is None:
            raise ValueError(f"CR not found: {cr_id}")

        # 변경 컬럼 추출 (schema_after 의 upsert + drop 키)
        upserts, drops = _parse_schema_after(cr.schema_after)
        changed_columns = {f["field_path"] for f in upserts} | set(drops)

        consumers = (
            await session.execute(
                select(models.Consumer).where(models.Consumer.dataset_id == cr.dataset_id)
            )
        ).scalars().all()
        mission_critical = sum(1 for c in consumers if c.criticality == "MISSION_CRITICAL")
        important = sum(1 for c in consumers if c.criticality == "IMPORTANT")

        downstream_ids = await _downstream_dataset_ids(session, cr.dataset_id)
        affected_columns = await _affected_downstream_columns(
            session, cr.dataset_id, changed_columns
        )

        # 위험도 산정
        base = _BASE_RISK.get(cr.change_type, 0.4)
        reach = min(len(downstream_ids) + len(consumers), 20) / 20.0
        crit_bump = 0.3 if mission_critical else (0.15 if important else 0.0)
        risk = round(min(1.0, base + 0.1 * reach + crit_bump), 2)

        report = ImpactReport(
            change_type=cr.change_type,
            changed_columns=sorted(changed_columns),
            affected_downstream_count=len(downstream_ids),
            affected_dataset_ids=downstream_ids,
            affected_columns=affected_columns,
            affected_consumer_count=len(consumers),
            affected_consumers=[c.id for c in consumers],
            mission_critical_consumers=mission_critical,
            risk_score=risk,
            summary=(
                f"다운스트림 {len(downstream_ids)}개 데이터셋, "
                f"컬럼 영향 {len(affected_columns)}건, "
                f"소비자 {len(consumers)}명(중대 {mission_critical}) "
                f"— 위험도 {risk:.2f}"
            ),
        )

        cr.impact_report = json.dumps(report.__dict__, ensure_ascii=False)
        await session.commit()
        logger.info("영향 분석 완료: cr=%s %s", cr.cr_code, report.summary)
        return report


# ---------------------------------------------------------------------------
# 결재 상태 기록
# ---------------------------------------------------------------------------

@activity.defn(name="record_decision")
async def record_decision(
    cr_id: int, step_order: int, decision: str, comment: str | None, decided_by: str
) -> None:
    """결재 단계의 결정 결과를 DB에 기록한다."""
    async with async_session_factory() as session:
        step = (
            await session.execute(
                select(models.ApprovalStep).where(
                    models.ApprovalStep.cr_id == cr_id,
                    models.ApprovalStep.step_order == step_order,
                )
            )
        ).scalar_one_or_none()
        if step is None:
            raise ValueError(f"ApprovalStep not found: cr={cr_id} order={step_order}")

        step.decision = decision
        step.comment = comment
        step.decided_at = datetime.now(timezone.utc)
        if decision == schemas.Decision.DELEGATED.value:
            step.delegated_to = decided_by
        await session.commit()


@activity.defn(name="update_cr_status")
async def update_cr_status(cr_id: int, status: str) -> None:
    async with async_session_factory() as session:
        cr = await session.get(models.ChangeRequest, cr_id)
        if cr is None:
            raise ValueError(f"CR not found: {cr_id}")
        cr.status = status
        if status == schemas.CRStatus.APPLIED.value:
            cr.applied_at = datetime.now(timezone.utc)
        await session.commit()


# ---------------------------------------------------------------------------
# 통지 (Notification)
# ---------------------------------------------------------------------------

def _build_notification_payload(cr, dataset_name: str, stage: str) -> dict:
    """소비자에게 보낼 통지 내용(템플릿)을 구성한다 (Before/After·일정·대응 가이드)."""
    return {
        "event": "schema_change_notification",
        "cr_code": cr.cr_code,
        "stage": stage,
        "dataset": dataset_name,
        "title": cr.title,
        "change_type": cr.change_type,
        "priority": cr.priority,
        "scheduled_at": cr.scheduled_at.isoformat() if cr.scheduled_at else None,
        "schema_before": _safe_json(cr.schema_before),
        "schema_after": _safe_json(cr.schema_after),
        "rollback_plan": cr.rollback_plan,
        "business_justification": cr.business_justification,
        "impact": _safe_json(cr.impact_report),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _safe_json(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


async def _deliver(channel: str, consumer, payload: dict) -> tuple[str, str | None]:
    """단일 채널로 통지를 전달하고 (status, error) 를 반환한다.

    - WEBHOOK : consumer.webhook_url 로 실제 HTTP POST (성공 DELIVERED / 실패 FAILED)
    - EMAIL / SLACK / SMS / IN_APP : 외부 게이트웨이 미구성 환경이므로 발송 기록(SENT)만 남긴다.
      (SMTP/Slack API 연동 시 이 분기에 어댑터를 추가한다.)
    """
    if channel == "WEBHOOK":
        if not consumer.webhook_url:
            return "FAILED", "webhook_url 미등록"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(consumer.webhook_url, json=payload)
            if resp.status_code >= 400:
                return "FAILED", f"HTTP {resp.status_code}"
            return "DELIVERED", None
        except Exception as e:  # 네트워크 오류 등
            return "FAILED", str(e)
    # 그 외 채널은 발송 시도 기록만 (실제 게이트웨이 연동 전)
    return "SENT", None


@activity.defn(name="send_notifications")
async def send_notifications(payload: NotifyInput) -> int:
    """등록된 소비자 전원에게 지정 채널로 통지를 발송하고 로그를 남긴다.

    WEBHOOK 은 실제 HTTP POST 로 전달하며, 그 외 채널은 발송 기록을 남긴다
    (이메일/Slack 게이트웨이는 환경에 따라 어댑터를 추가).
    """
    async with async_session_factory() as session:
        cr = await session.get(models.ChangeRequest, payload.cr_id)
        if cr is None:
            raise ValueError(f"CR not found: {payload.cr_id}")

        dataset = await session.get(Dataset, cr.dataset_id)
        dataset_name = dataset.name if dataset else str(cr.dataset_id)

        consumers = (
            await session.execute(
                select(models.Consumer).where(models.Consumer.dataset_id == cr.dataset_id)
            )
        ).scalars().all()

        content = _build_notification_payload(cr, dataset_name, payload.stage)

        count = 0
        for consumer in consumers:
            for channel in payload.channels:
                status, error = await _deliver(channel, consumer, content)
                log = models.NotificationLog(
                    cr_id=cr.id,
                    consumer_id=consumer.id,
                    channel=channel,
                    stage=payload.stage,
                    status=status,
                    sent_at=datetime.now(timezone.utc),
                    error=error,
                )
                session.add(log)
                count += 1
                logger.info(
                    "통지 %s: cr=%s consumer=%s channel=%s stage=%s%s",
                    status, cr.cr_code, consumer.consumer_name, channel, payload.stage,
                    f" error={error}" if error else "",
                )
        await session.commit()
        return count


@activity.defn(name="wait_acks_summary")
async def wait_acks_summary(cr_id: int) -> dict:
    """미확인 소비자 수를 집계해 반환 (워크플로우의 에스컬레이션 판단용)."""
    async with async_session_factory() as session:
        logs = (
            await session.execute(
                select(models.NotificationLog).where(models.NotificationLog.cr_id == cr_id)
            )
        ).scalars().all()
        total = len(logs)
        acked = sum(1 for log in logs if log.acked_at is not None)
        return {"total": total, "acked": acked, "pending": total - acked}


# ---------------------------------------------------------------------------
# 변경 적용
# ---------------------------------------------------------------------------

def _merge_schema(current: list[dict], upserts: list[dict], drops: list[str]) -> list[dict]:
    """현재 스키마에 CR 의 변경(upsert/drop)을 병합해 완전한 신 스키마를 만든다.

    - upsert: 기존 컬럼이면 속성 갱신, 신규면 추가
    - drop  : 해당 컬럼 제거
    ordinal 은 기존 순서를 유지하고 신규 컬럼은 뒤에 append.
    """
    by_path: dict[str, dict] = {f["field_path"]: dict(f) for f in current}
    max_ordinal = max((f.get("ordinal", i) for i, f in enumerate(current)), default=-1)

    for up in upserts:
        path = up["field_path"]
        if path in by_path:
            by_path[path].update({k: v for k, v in up.items() if k != "field_path"})
        else:
            max_ordinal += 1
            new_field = {"ordinal": max_ordinal}
            new_field.update(up)
            by_path[path] = new_field

    for d in drops:
        by_path.pop(d, None)

    return sorted(by_path.values(), key=lambda f: f.get("ordinal", 0))


@activity.defn(name="apply_change")
async def apply_change(payload: ApplyInput) -> None:
    """승인된 변경을 실제 카탈로그 스키마에 적용한다.

    1. 현재 ``catalog_dataset_schemas`` 필드를 읽어 CR 의 변경(upsert/drop)을 병합
    2. 변경 이력 스냅샷 저장(``save_schema_snapshot``) — alert rule 평가까지 연동됨
    3. 기존 필드를 신 스키마로 교체
    4. CR 상태를 APPLIED 로 전이

    검색용 임베딩 재생성은 워커 프로세스에 provider 가 없을 수 있어 여기서는 생략한다
    (동기화/편집 경로가 후속으로 처리).
    """
    from types import SimpleNamespace

    async with async_session_factory() as session:
        cr = await session.get(models.ChangeRequest, payload.cr_id)
        if cr is None:
            raise ValueError(f"CR not found: {payload.cr_id}")

        upserts, drops = _parse_schema_after(cr.schema_after)
        if not upserts and not drops:
            logger.warning(
                "apply_change: 적용할 스키마 변경이 없음 cr=%s — 상태만 APPLIED 로 전이",
                cr.cr_code,
            )
        else:
            old_fields = (
                await session.execute(
                    select(DatasetSchema).where(DatasetSchema.dataset_id == cr.dataset_id)
                )
            ).scalars().all()
            current = [
                {
                    "field_path": f.field_path,
                    "field_type": f.field_type,
                    "native_type": f.native_type,
                    "description": f.description,
                    "nullable": f.nullable,
                    "is_primary_key": f.is_primary_key,
                    "is_unique": f.is_unique,
                    "is_indexed": f.is_indexed,
                    "is_partition_key": f.is_partition_key,
                    "is_distribution_key": f.is_distribution_key,
                    "display_name": f.display_name,
                    "ordinal": f.ordinal,
                }
                for f in old_fields
            ]
            merged = _merge_schema(current, upserts, drops)

            # 변경 이력 스냅샷 (old ORM vs 신 스키마). from_sync=True 경로가 col.name 등
            # attribute 접근을 쓰므로 SimpleNamespace 로 변환해 넘긴다.
            new_columns_obj = [
                SimpleNamespace(
                    name=f["field_path"],
                    data_type=f["field_type"],
                    native_type=f.get("native_type") or "",
                    nullable=str(f.get("nullable", "true")).lower() == "true",
                    is_primary_key=str(f.get("is_primary_key", "false")).lower() == "true",
                    is_unique=str(f.get("is_unique", "false")).lower() == "true",
                    is_indexed=str(f.get("is_indexed", "false")).lower() == "true",
                    ordinal=int(f.get("ordinal", idx)),
                )
                for idx, f in enumerate(merged)
            ]
            try:
                await save_schema_snapshot(
                    session, cr.dataset_id, old_fields, new_columns_obj, from_sync=True
                )
            except Exception as e:
                logger.warning("apply_change: 스냅샷 저장 실패 cr=%s: %s", cr.cr_code, e)

            # 기존 필드 교체 (DELETE 후 재삽입 — full replace)
            await session.execute(
                sql_delete(DatasetSchema).where(DatasetSchema.dataset_id == cr.dataset_id)
            )
            await session.flush()
            for idx, f in enumerate(merged):
                session.add(
                    DatasetSchema(
                        dataset_id=cr.dataset_id,
                        field_path=f["field_path"],
                        display_name=f.get("display_name"),
                        field_type=f["field_type"],
                        native_type=f.get("native_type"),
                        description=f.get("description"),
                        nullable=f.get("nullable", "true"),
                        is_primary_key=f.get("is_primary_key", "false"),
                        is_unique=f.get("is_unique", "false"),
                        is_indexed=f.get("is_indexed", "false"),
                        is_partition_key=f.get("is_partition_key", "false"),
                        is_distribution_key=f.get("is_distribution_key", "false"),
                        ordinal=f.get("ordinal", idx),
                    )
                )

        logger.info(
            "변경 적용: cr=%s dataset=%s (+%d upsert, -%d drop)",
            cr.cr_code, cr.dataset_id, len(upserts), len(drops),
        )
        cr.status = schemas.CRStatus.APPLIED.value
        cr.applied_at = datetime.now(timezone.utc)
        await session.commit()
