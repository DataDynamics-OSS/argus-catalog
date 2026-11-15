# SPDX-License-Identifier: Apache-2.0
"""Avro 스키마에 영향을 주는 변경을 커밋 시점에 잡아 캐시를 지우고 새 버전을 등록한다.

Avro 는 데이터셋 이름·설명, 스키마 필드, Avro 편집본 속성(``argus.avro_schema``)으로 만든다. 이 값들은 화면 편집,
수집(metadata-sync·내장 동기화), AI 제안 적용, 변경 요청 적용 등 여러 경로가 ORM 으로 고치므로, 호출 지점마다
무효화를 넣지 않고 SQLAlchemy 세션 이벤트 하나로 모은다.

- ``before_flush``: 새로 추가·변경·삭제되는 객체 중 위 값에 해당하는 것의 데이터셋 id 를 세션에 적어 둔다.
- ``after_commit``: 적어 둔 데이터셋의 외부 API 캐시(``avro:``·``metadata:``·``urn_map:``)와 레지스트리 최신 버전 캐시를
  지우고, 켜져 있으면 백그라운드 등록을 예약한다. 롤백돼도 지우기만 하므로(다음 조회 때 다시 만듦) 해가 없다.

ORM 을 거치지 않는 대량 SQL(``delete(...)``)은 잡히지 않으므로 그런 곳은 ``mark_changed`` 를 직접 부른다.
같은 프로세스의 캐시만 지운다 — 다른 프로세스(예: 변경 요청을 적용하는 catalog-worker)에서 바뀐 값은 캐시 TTL 뒤에 반영된다.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.catalog.models import Dataset, DatasetProperty, DatasetSchema

logger = logging.getLogger(__name__)

_INFO_KEY = "argus_avro_changed"
AVRO_OVERRIDE_PROPERTY = "argus.avro_schema"   # app.external.service.AVRO_OVERRIDE_PROPERTY 와 같음
_DATASET_ATTRS = ("name", "description", "qualified_name", "urn")
_FIELD_ATTRS = ("field_path", "field_type", "native_type", "nullable", "description", "ordinal", "dataset_id")


def mark_changed(session, dataset_id: int) -> None:
    """이 세션이 커밋될 때 데이터셋의 Avro 관련 캐시를 지우도록 표시한다(AsyncSession·Session 모두)."""
    sync = getattr(session, "sync_session", session)
    sync.info.setdefault(_INFO_KEY, set()).add(int(dataset_id))


def _changed(obj, attrs) -> bool:
    state = inspect(obj)
    return any(state.attrs[a].history.has_changes() for a in attrs)


def _affected(obj, kind: str) -> int | None:
    """객체가 Avro 입력이면 그 데이터셋 id. kind 는 new / dirty / deleted."""
    if isinstance(obj, DatasetSchema):
        if kind == "dirty" and not _changed(obj, _FIELD_ATTRS):
            return None
        return obj.dataset_id
    if isinstance(obj, DatasetProperty):
        if obj.property_key != AVRO_OVERRIDE_PROPERTY:
            return None
        if kind == "dirty" and not _changed(obj, ("property_value",)):
            return None
        return obj.dataset_id
    if isinstance(obj, Dataset):
        if kind == "new":           # 새 데이터셋은 아직 캐시에 없다
            return None
        if kind == "dirty" and not _changed(obj, _DATASET_ATTRS):
            return None
        return obj.id
    return None


@event.listens_for(Session, "before_flush")
def _collect(session: Session, _flush_context, _instances) -> None:
    ids: set[int] = set()
    for kind, objs in (("new", session.new), ("dirty", session.dirty), ("deleted", session.deleted)):
        for obj in objs:
            dataset_id = _affected(obj, kind)
            if dataset_id is not None:
                ids.add(int(dataset_id))
    if ids:
        session.info.setdefault(_INFO_KEY, set()).update(ids)


def _flush_marks(session: Session) -> None:
    ids = session.info.pop(_INFO_KEY, None)
    if ids:
        on_changed(ids)


event.listen(Session, "after_commit", _flush_marks)
event.listen(Session, "after_rollback", _flush_marks)


# ---------------------------------------------------------------------------
# 무효화와 백그라운드 등록
# ---------------------------------------------------------------------------

_session_factory = None          # start_background 가 넣는다 — None 이면 백그라운드 등록을 하지 않음
_pending: set[int] = set()
_worker: asyncio.Task | None = None
_COALESCE_SECONDS = 1.0          # 한 요청·한 수집 배치의 연속 커밋을 한 번으로 합친다


def on_changed(dataset_ids) -> None:
    """데이터셋들의 캐시를 지우고 등록을 예약한다(동기 — 커밋 이벤트에서 호출)."""
    from app.external.service import forget_dataset_nowait
    from app.schema_registry.service import forget_latest_nowait

    for dataset_id in dataset_ids:
        removed = forget_dataset_nowait(dataset_id) + forget_latest_nowait(dataset_id)
        if removed:
            logger.debug("Avro 입력 변경 — 캐시 무효화: dataset_id=%d entries=%d", dataset_id, removed)
    _schedule(dataset_ids)


def start_background(session_factory) -> None:
    """서버 기동 때 부른다 — 이후 변경된 데이터셋의 새 버전을 백그라운드로 등록한다."""
    global _session_factory
    _session_factory = session_factory
    logger.info("스키마 레지스트리 변경 시 미리 등록 사용")


def _schedule(dataset_ids) -> None:
    global _worker
    if _session_factory is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _pending.update(int(i) for i in dataset_ids)
    if _worker is None or _worker.done():
        _worker = loop.create_task(_drain())


async def _drain() -> None:
    """대기열을 하나씩 처리한다 — 대량 수집에서도 DB 연결을 하나만 쓴다."""
    from app.schema_registry.service import register_current

    await asyncio.sleep(_COALESCE_SECONDS)
    done = 0
    while _pending:
        dataset_id = _pending.pop()
        try:
            async with _session_factory() as session:
                await register_current(session, dataset_id)
            done += 1
        except Exception as e:  # noqa: BLE001 — 등록 실패는 다음 조회 때 다시 시도된다
            logger.warning("스키마 레지스트리 미리 등록 실패: dataset_id=%d: %s", dataset_id, e)
    if done:
        logger.debug("스키마 레지스트리 미리 등록: %d건", done)
