# SPDX-License-Identifier: Apache-2.0
"""페더레이션 peer 레지스트리 — 인스턴스 CRUD·관측 요약·미러 탐색·URN 해석."""

import json
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.federation import breaker, capabilities, token_store
from app.federation.models import (
    FederatedDataset,
    FederatedInstance,
)
from app.federation.schemas import (
    FederatedBrowseDataset,
    FederatedBrowseDatasource,
    FederatedBrowseResponse,
    FederatedInstanceCreate,
    FederatedInstanceResponse,
    FederatedInstanceUpdate,
)

logger = logging.getLogger(__name__)

# HARVEST 미러를 이용해 로컬에서 검색하는 모드 (LIVE 는 요청 시 fan-out)
_MIRROR_MODES = ("HARVEST", "HYBRID")


def _escape_like(term: str) -> str:
    """LIKE 와일드카드(%, _)와 이스케이프 문자를 문자 그대로 매칭되도록 이스케이프한다."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Peer 레지스트리 CRUD
# ---------------------------------------------------------------------------

def _parse_display_fields(raw: str | None) -> list[str] | None:
    """display_fields JSON 문자열 → capability 키 리스트(검증). 손상 시 None(전부 표시)."""
    if not raw:
        return None
    try:
        keys = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("display_fields 손상 — 전부 표시로 대체: raw_len=%d", len(raw))
        return None
    if not isinstance(keys, list):
        logger.warning("display_fields 가 배열이 아님 — 전부 표시로 대체: type=%s", type(keys).__name__)
        return None
    return capabilities.validate([str(k) for k in keys])


def _serialize_display_fields(keys: list[str] | None) -> str | None:
    """capability 키 리스트 → 저장용 JSON 문자열(검증). None 은 None(전부 표시), 빈 리스트는 "[]"(표시 없음)."""
    if keys is None:
        return None
    valid = capabilities.validate(keys)
    return json.dumps(valid)


def _to_response(inst: FederatedInstance) -> FederatedInstanceResponse:
    """ORM 행 → 응답 스키마. 토큰 원문 대신 보유 여부(has_auth_token)만 내보낸다."""
    return FederatedInstanceResponse(
        id=inst.id,
        instance_key=inst.instance_key,
        name=inst.name,
        base_url=inst.base_url,
        has_auth_token=bool(inst.auth_token),
        mode=inst.mode,
        sync_interval_sec=inst.sync_interval_sec,
        status=inst.status,
        description=inst.description,
        display_fields=_parse_display_fields(inst.display_fields),
        created_at=inst.created_at,
        updated_at=inst.updated_at,
    )


async def list_instances(session: AsyncSession) -> list[FederatedInstanceResponse]:
    """등록된 peer 전체를 이름순으로 반환한다."""
    result = await session.execute(
        select(FederatedInstance).order_by(FederatedInstance.name)
    )
    return [_to_response(i) for i in result.scalars().all()]


async def get_instance(session: AsyncSession, instance_id: int) -> FederatedInstance | None:
    """id 로 peer 를 조회한다(없으면 None)."""
    result = await session.execute(
        select(FederatedInstance).where(FederatedInstance.id == instance_id)
    )
    return result.scalars().first()


async def create_instance(
    session: AsyncSession, req: FederatedInstanceCreate
) -> FederatedInstanceResponse:
    """peer 를 등록한다. 토큰은 암호화(seal)해 저장한다.

    instance_key 유일 제약 위반 시 커밋에서 IntegrityError 가 전파된다(라우터가 409 로 변환).
    """
    inst = FederatedInstance(
        instance_key=req.instance_key,
        name=req.name,
        base_url=req.base_url,
        auth_token=token_store.seal(req.auth_token),
        mode=req.mode,
        sync_interval_sec=req.sync_interval_sec,
        description=req.description,
        display_fields=_serialize_display_fields(req.display_fields),
    )
    session.add(inst)
    await session.commit()
    await session.refresh(inst)
    logger.info("페더레이션 peer 등록: %s (%s)", inst.instance_key, inst.base_url)
    return _to_response(inst)


async def update_instance(
    session: AsyncSession, inst: FederatedInstance, req: FederatedInstanceUpdate
) -> FederatedInstanceResponse:
    """요청에 명시된 필드만 부분 수정한다(토큰은 재암호화, display_fields 는 JSON 직렬화)."""
    data = req.model_dump(exclude_unset=True)
    if "auth_token" in data:
        # None/빈 값=토큰 제거, 그 외 암호문으로 저장
        data["auth_token"] = token_store.seal(data["auth_token"])
    if "display_fields" in data:
        # 리스트 → 저장용 JSON 문자열로 변환(None 이면 전부 표시로 초기화)
        data["display_fields"] = _serialize_display_fields(data["display_fields"])
    for field, value in data.items():
        setattr(inst, field, value)
    await session.commit()
    await session.refresh(inst)
    # 토큰 값은 남기지 않고 바뀐 필드 이름만 기록한다.
    logger.info("페더레이션 peer 수정: %s fields=%s", inst.instance_key, sorted(data))
    return _to_response(inst)


async def delete_instance(session: AsyncSession, inst: FederatedInstance) -> None:
    """peer 를 삭제한다. 미러 행은 FK CASCADE, 샘플 파일은 직접 삭제한다."""
    from app.federation import samples

    key = inst.instance_key
    instance_id = inst.id
    await session.delete(inst)
    await session.commit()
    # 미러 데이터(DB)는 CASCADE 로 정리되지만, 저장된 샘플 파일은 별도로 삭제한다.
    samples.delete_instance_samples(instance_id)
    logger.info("페더레이션 peer 삭제: %s", key)


async def list_active_peers(session: AsyncSession) -> list[FederatedInstance]:
    """연합 검색에 포함할 ACTIVE peer 목록."""
    result = await session.execute(
        select(FederatedInstance).where(FederatedInstance.status == "ACTIVE")
    )
    return list(result.scalars().all())


async def federation_stats(session: AsyncSession) -> "FederationStats":
    """페더레이션 관측 요약 — peer 별 미러 카운트·최근 동기화·breaker 상태."""
    from app.federation.models import (
        FederatedDataset,
        FederationLineage,
        FederationSyncRun,
    )
    from app.federation.schemas import FederationStats, FederationStatsInstance

    instances = (await session.execute(
        select(FederatedInstance).order_by(FederatedInstance.name)
    )).scalars().all()

    # 미러 카운트 집계(instance_id 별)
    ds_counts = dict((await session.execute(
        select(FederatedDataset.instance_id, func.count())
        .group_by(FederatedDataset.instance_id)
    )).all())
    lin_counts = dict((await session.execute(
        select(FederationLineage.instance_id, func.count())
        .group_by(FederationLineage.instance_id)
    )).all())

    bsnap = breaker.snapshot()
    logger.debug("페더레이션 관측 요약: instances=%d breakers=%d", len(instances), len(bsnap))

    rows: list[FederationStatsInstance] = []
    total_ds = 0
    total_lin = 0
    active = 0
    for inst in instances:
        mirror_ds = int(ds_counts.get(inst.id, 0))
        mirror_lin = int(lin_counts.get(inst.id, 0))
        total_ds += mirror_ds
        total_lin += mirror_lin
        if inst.status == "ACTIVE":
            active += 1

        last = (await session.execute(
            select(FederationSyncRun)
            .where(FederationSyncRun.instance_id == inst.id)
            .order_by(FederationSyncRun.started_at.desc())
            .limit(1)
        )).scalars().first()

        bstate = bsnap.get(inst.instance_key, {})
        rows.append(FederationStatsInstance(
            id=inst.id, instance_key=inst.instance_key, name=inst.name,
            mode=inst.mode, status=inst.status,
            mirror_datasets=mirror_ds, mirror_lineage=mirror_lin,
            last_sync_status=last.status if last else None,
            last_sync_started_at=last.started_at if last else None,
            last_sync_finished_at=last.finished_at if last else None,
            last_sync_seen=last.datasets_seen if last else None,
            last_sync_embedded=last.datasets_embedded if last else None,
            last_error=last.error if last else None,
            breaker_open=bool(bstate.get("open", False)),
            breaker_failures=int(bstate.get("failures", 0)),
        ))

    return FederationStats(
        total_instances=len(instances),
        active_instances=active,
        total_mirror_datasets=total_ds,
        total_mirror_lineage=total_lin,
        instances=rows,
    )


# 단일 인스턴스 탐색에서 한 번에 반환하는 미러 데이터셋 상한.
# 트리 UI 가 인스턴스 단위로 lazy-load 하므로 충분히 크게 잡되, 과대 인스턴스에서
# 응답이 폭증하지 않도록 cap 을 두고 truncated 플래그로 알린다.
_BROWSE_CAP = 2000

# datasource_name 이 NULL 인 미러 데이터셋의 표시용 그룹 이름.
_BROWSE_UNGROUPED = "(미지정)"


async def browse_instance_datasets(
    session: AsyncSession, inst: FederatedInstance, q: str | None = None,
) -> FederatedBrowseResponse:
    """단일 인스턴스의 HARVEST 미러 데이터셋을 데이터소스별로 묶어 반환한다.

    통합 검색과 달리 검색어가 없어도 동작하는 '둘러보기' 용도다. ``q`` 가 주어지면
    name/qualified_name/description 부분일치로 트리를 좁힌다. 반환은 데이터소스명
    오름차순, 각 그룹 안에서 데이터셋명 오름차순으로 정렬한다.
    """
    base = select(FederatedDataset).where(FederatedDataset.instance_id == inst.id)
    if q:
        like = f"%{_escape_like(q.lower())}%"
        base = base.where(
            or_(
                func.lower(FederatedDataset.name).like(like, escape="\\"),
                func.lower(FederatedDataset.qualified_name).like(like, escape="\\"),
                func.lower(FederatedDataset.description).like(like, escape="\\"),
            )
        )

    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    logger.debug(
        "미러 둘러보기: instance=%s q=%s total=%d", inst.instance_key, q, total
    )

    rows = (
        await session.execute(
            base.order_by(
                FederatedDataset.datasource_name.asc(),
                FederatedDataset.name.asc(),
            ).limit(_BROWSE_CAP)
        )
    ).scalars().all()

    # 데이터소스명 기준 그룹핑(정렬 순서 유지). type 은 그룹 내 첫 비어있지 않은 값 채택.
    groups: dict[str, FederatedBrowseDatasource] = {}
    for ds in rows:
        key = ds.datasource_name or _BROWSE_UNGROUPED
        grp = groups.get(key)
        if grp is None:
            grp = FederatedBrowseDatasource(
                datasource_name=key,
                datasource_type=ds.datasource_type,
                dataset_count=0,
                datasets=[],
            )
            groups[key] = grp
        if grp.datasource_type is None and ds.datasource_type:
            grp.datasource_type = ds.datasource_type
        grp.datasets.append(
            FederatedBrowseDataset(
                federated_urn=ds.federated_urn,
                remote_urn=ds.remote_urn,
                name=ds.name,
                display_name=ds.display_name,
                summary=ds.summary,
                description=ds.description,
                qualified_name=ds.qualified_name,
                origin=ds.origin,
                field_count=ds.field_count,
                remote_created_at=ds.remote_created_at,
                remote_updated_at=ds.remote_updated_at,
                harvested_at=ds.harvested_at,
            )
        )
        grp.dataset_count += 1

    return FederatedBrowseResponse(
        instance_id=inst.id,
        instance_key=inst.instance_key,
        instance_name=inst.name,
        total_datasets=total,
        truncated=total > len(rows),
        datasources=list(groups.values()),
    )


async def get_instance_by_key(
    session: AsyncSession, instance_key: str,
) -> FederatedInstance | None:
    """instance_key 로 peer 를 조회한다(없으면 None)."""
    result = await session.execute(
        select(FederatedInstance).where(FederatedInstance.instance_key == instance_key)
    )
    return result.scalars().first()


async def resolve_federated_urn(
    session: AsyncSession, federated_urn: str,
) -> tuple[FederatedInstance, str] | None:
    """``{instance_key}::{remote_urn}`` → (peer 인스턴스, remote_urn).

    구분자 ``::`` 가 없거나 인스턴스를 찾지 못하면 None.
    """
    if "::" not in federated_urn:
        return None
    instance_key, remote_urn = federated_urn.split("::", 1)
    instance = await get_instance_by_key(session, instance_key)
    if instance is None or not remote_urn:
        logger.debug("federated URN 해석 실패: instance_key=%s", instance_key)
        return None
    # 비활성(PAUSED 등) peer 로는 드릴다운·샘플·import 호출을 보내지 않는다.
    if instance.status != "ACTIVE":
        logger.info("비활성 peer 드릴다운 거부 [%s] status=%s", instance_key, instance.status)
        return None
    return instance, remote_urn


