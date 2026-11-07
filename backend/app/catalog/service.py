# SPDX-License-Identifier: Apache-2.0
"""데이터 카탈로그 서비스 계층.

데이터셋, 데이터 소스(접속 설정·연결 테스트·삭제 영향), 태그, 용어집 용어, 소유자,
스키마 필드·변경 이력, 대시보드 통계, 데이터 파이프라인, 데이터셋 리니지(FK 포함)에 대한
작업을 제공한다. 대부분의 쓰기 함수는 스스로 커밋하지만, 파이프라인·리니지 CRUD 는
flush 만 하고 커밋은 라우터가 한다.
"""

import logging
import os
import time

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import (
    DataPipeline,
    Dataset,
    DatasetColumnMapping,
    DatasetGlossaryTerm,
    DatasetLineage,
    DatasetProperty,
    DatasetSchema,
    DatasetTag,
    Datasource,
    DatasourceConfiguration,
    DatasourceDataType,
    DatasourceFeature,
    DatasourceStorageFormat,
    DatasourceTableType,
    GlossaryTerm,
    Owner,
    SchemaSnapshot,
    System,
    Tag,
)
from app.catalog.schemas import (
    CatalogStats,
    ColumnMappingCreate,
    ColumnMappingResponse,
    DatasetCreate,
    DatasetLineageCreate,
    DatasetLineageResponse,
    DatasetPropertyResponse,
    DatasetResponse,
    DatasetSummary,
    DatasetUpdate,
    DatasourceCreate,
    DatasourceDataTypeResponse,
    DatasourceFeatureResponse,
    DatasourceMetadataResponse,
    DatasourceResponse,
    DatasourceStorageFormatResponse,
    DatasourceTableTypeResponse,
    DatasourceUpdate,
    GlossaryTermCreate,
    GlossaryTermResponse,
    OwnerCreate,
    OwnerResponse,
    PaginatedDatasets,
    PaginatedSchemaSnapshots,
    PipelineCreate,
    PipelineResponse,
    PipelineUpdate,
    SchemaChangeEntry,
    SchemaFieldResponse,
    SchemaSnapshotResponse,
    TagCreate,
    TagResponse,
    TagUpdate,
    TagUsage,
    TagUsageRef,
)

logger = logging.getLogger(__name__)


def _generate_datasource_id(datasource_type: str) -> str:
    """데이터 소스 ID 생성: {type}-{timestamp_hex}{random_hex}.

    형식: ms 타임스탬프 hex(최소 10자 zero-pad — 2004년 이후 값은 11자) + 6자 랜덤 hex
    → 현재 suffix 는 17자.
    같은 밀리초여도 랜덤 suffix 가 달라진다(2^24 = 1600만 경우의 수).
    """
    timestamp_ms = int(time.time() * 1000)
    ts_hex = format(timestamp_ms, "010x")
    rand_hex = os.urandom(3).hex()  # 6자 hex = 24비트
    return f"{datasource_type}-{ts_hex}{rand_hex}"


import re as _re

# 구 URN 의 환경 suffix (`.DEV.dataset` 등) 를 제거해 신 포맷으로 정규화한다.
# 마이그레이션 / 해소 폴백 / alias 에서 공용.
_ENV_URN_SUFFIX = _re.compile(r"\.(DEV|STAGING|PROD)\.dataset$")


def strip_env_urn(urn: str) -> str:
    """``{datasource}.{path}.{ENV}.dataset`` → ``{datasource}.{path}.dataset``."""
    return _ENV_URN_SUFFIX.sub(".dataset", urn)


def _generate_urn(datasource_id: str, path: str, entity_type: str = "dataset") -> str:
    """URN 생성: {datasource_id}.{path}.{type}

    환경(DEV/STAGING/PROD)은 URN 에 포함하지 않는다 — datasource_id 가 이미 인스턴스
    고유값이라 유일성에 충분하고, 환경은 ``Dataset.origin`` 컬럼으로 표현한다.
    이렇게 하면 서버 승격(DEV→PROD) 시에도 URN 이 불변이다.
    """
    return f"{datasource_id}.{path}.{entity_type}"


# ---------------------------------------------------------------------------
# 데이터 소스 작업
# ---------------------------------------------------------------------------

async def _datasource_response(session: AsyncSession, datasource: Datasource) -> DatasourceResponse:
    """응답 변환 + 소속 시스템 이름(system_name) 채우기 — 예전에는 항상 null 이었다."""
    resp = DatasourceResponse.model_validate(datasource)
    if datasource.system_id is not None:
        resp.system_name = await session.scalar(
            select(System.name).where(System.id == datasource.system_id))
    return resp


async def list_datasources(session: AsyncSession) -> list[DatasourceResponse]:
    """등록된 모든 데이터 소스를 이름순으로 조회한다(소속 시스템 이름 포함)."""
    rows = (await session.execute(
        select(Datasource, System.name)
        .outerjoin(System, System.id == Datasource.system_id)
        .order_by(Datasource.name)
    )).all()
    out = []
    for ds, system_name in rows:
        resp = DatasourceResponse.model_validate(ds)
        resp.system_name = system_name
        out.append(resp)
    return out


async def create_datasource(session: AsyncSession, req: DatasourceCreate) -> DatasourceResponse:
    """데이터 소스를 등록하고 유형별 메타데이터를 같은 트랜잭션에서 시드한 뒤 커밋한다."""
    datasource = Datasource(
        datasource_id=_generate_datasource_id(req.type),
        name=req.name,
        type=req.type,
        logo_url=req.logo_url,
        origin=req.origin.value,
    )
    session.add(datasource)
    await session.flush()
    # 유형별 메타데이터(데이터 타입·테이블 타입 등)를 같은 트랜잭션으로 채운다 — 예전에는
    # 서버 기동 시 유형별 첫 데이터 소스에만 시드되어 새 데이터 소스는 비어 있었다.
    await _seed_metadata_for(session, datasource)
    await session.commit()
    await session.refresh(datasource)
    logger.info("데이터 소스 생성: %s (id=%d)", datasource.name, datasource.id)
    return await _datasource_response(session, datasource)


async def get_datasource(session: AsyncSession, datasource_id: int) -> DatasourceResponse | None:
    """ID 로 데이터 소스 하나를 조회한다. 없으면 None."""
    result = await session.execute(select(Datasource).where(Datasource.id == datasource_id))
    datasource = result.scalars().first()
    if not datasource:
        return None
    return await _datasource_response(session, datasource)


async def update_datasource(
    session: AsyncSession, datasource_id: int, req: DatasourceUpdate
) -> DatasourceResponse | None:
    """데이터 소스 이름·환경(origin)을 수정한다. 없으면 None 반환.

    origin 이 바뀌면 소속 데이터셋 전체의 origin 도 함께 갱신한다.
    """
    result = await session.execute(select(Datasource).where(Datasource.id == datasource_id))
    datasource = result.scalars().first()
    if not datasource:
        logger.warning("수정할 데이터 소스를 찾을 수 없음: id=%d", datasource_id)
        return None
    if req.name is not None:
        datasource.name = req.name
    if req.origin is not None and req.origin.value != datasource.origin:
        # 환경 변경 → 소속 데이터셋 origin 도 함께 갱신 (dataset.origin 은 datasource origin 을 미러).
        # URN 에는 origin 이 없으므로 식별자는 불변.
        from sqlalchemy import update as _sql_update
        old_origin = datasource.origin
        datasource.origin = req.origin.value
        result = await session.execute(
            _sql_update(Dataset)
            .where(Dataset.datasource_id == datasource_id)
            .values(origin=req.origin.value)
        )
        logger.info(
            "데이터 소스 origin 변경: id=%d %s->%s, 데이터셋 %d개 갱신",
            datasource_id, old_origin, req.origin.value, result.rowcount or 0,
        )
    await session.commit()
    await session.refresh(datasource)
    logger.info("데이터 소스 수정: %s (id=%d)", datasource.name, datasource.id)
    return await _datasource_response(session, datasource)


async def test_datasource_connection(datasource_type: str, config: dict) -> dict:
    """주어진 연결 설정으로 실제 접속을 시도해 성공 여부를 반환한다.

    백엔드가 드라이버를 보유한 유형만 직접 테스트한다 — PostgreSQL 호환
    (postgresql / greenplum / redshift, asyncpg)과 MySQL 호환(mysql / mariadb / starrocks, aiomysql).
    그 외 유형은 metadata-sync 서비스가 담당하므로 여기서는 미지원(``unsupported=True``)으로 응답한다.
    반환: ``{"ok", "message"[, "latency_ms"][, "unsupported"]}`` — 실패해도 예외를 내지 않는다.
    """
    import asyncio
    import time

    t = (datasource_type or "").lower()
    # PG 호환(greenplum/redshift)·MySQL 호환(mariadb/starrocks)도 같은 드라이버로 접속 확인
    pg_like = t in ("postgresql", "greenplum", "redshift")
    my_like = t in ("mysql", "mariadb", "starrocks")
    if not (pg_like or my_like):
        logger.debug("연결 테스트 미지원 유형: type=%s", t)
        return {"ok": False, "unsupported": True,
                "message": f"이 유형({datasource_type})은 서버에서 연결 테스트를 지원하지 않습니다."}
    try:
        host, port = validate_connection_target(config, 5432 if pg_like else 3306)
    except ValueError as e:
        logger.warning("연결 테스트 거부 — 접속 대상 형식 오류: type=%s reason=%s", t, e)
        return {"ok": False, "message": str(e)}
    user = config.get("username", "")
    password = config.get("password", "")
    database = config.get("database")
    ssl_opt = connection_ssl(config, pg=pg_like)
    timeout = 8
    logger.debug("연결 테스트 시작: type=%s host=%s port=%s ssl=%s", t, host, port, ssl_opt is not None)
    start = time.monotonic()
    try:
        if pg_like:
            import asyncpg
            conn = await asyncpg.connect(
                host=host, port=port, user=user, password=password,
                database=database or "postgres", timeout=timeout, command_timeout=timeout,
                ssl=ssl_opt,
            )
            try:
                await conn.fetchval("SELECT 1", timeout=timeout)
            finally:
                await conn.close()
        else:
            import aiomysql
            conn = await asyncio.wait_for(
                aiomysql.connect(
                    host=host, port=port, user=user, password=password,
                    db=database or None, connect_timeout=timeout, ssl=ssl_opt,
                ),
                timeout=timeout,
            )
            try:
                async with conn.cursor() as cur:
                    await asyncio.wait_for(cur.execute("SELECT 1"), timeout=timeout)
            finally:
                conn.close()
    except Exception as e:  # noqa: BLE001 — 사용자에게는 분류된 사유만, 상세는 로그로
        logger.warning("연결 테스트 실패 type=%s host=%s port=%s: %r", t, host, port, e)
        return {"ok": False, "message": _classify_connection_error(e, timeout)}

    latency_ms = int((time.monotonic() - start) * 1000)
    logger.info("연결 테스트 성공: type=%s host=%s port=%s latency_ms=%d", t, host, port, latency_ms)
    return {"ok": True, "message": "연결 성공", "latency_ms": latency_ms}


def connection_ssl(config: dict, pg: bool):
    """접속 설정의 ``ssl_enabled`` 를 드라이버 ssl 인자로 변환한다.

    - 꺼져 있으면 None (드라이버 기본값: asyncpg 는 prefer, aiomysql 은 평문).
    - 켜져 있으면 암호화를 강제한다. 인증서 검증용 CA 설정 항목이 없으므로
      PostgreSQL ``sslmode=require`` 와 같은 수준(암호화, 서버 인증서 미검증)이다.
    """
    v = config.get("ssl_enabled")
    on = v is True or str(v).strip().lower() in ("true", "1", "yes", "on")
    if not on:
        return None
    if pg:
        return "require"
    import ssl as _ssl

    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    return ctx


# 호스트명(영숫자로 시작·끝, 중간에 . _ -) 또는 IPv4/IPv6(대괄호 허용). 공백·'/'·',' 등은 거부.
_HOST_RE = _re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:.]+\]?$")


def validate_connection_target(config: dict, default_port: int) -> tuple[str, int]:
    """접속 대상 검증 — 호스트명/IP 만 허용(유닉스 소켓 경로·다중 호스트 목록 거부), 포트 범위.

    반환: ``(host, port)`` — 포트가 비어 있으면 ``default_port``. 형식 오류는 ``ValueError``.
    """
    host = str(config.get("host") or "").strip()
    if not host:
        raise ValueError("호스트를 입력하세요.")
    if len(host) > 253 or not _HOST_RE.match(host):
        raise ValueError("호스트는 호스트명 또는 IP 주소만 입력할 수 있습니다.")
    raw_port = config.get("port")
    if raw_port in (None, ""):
        return host, default_port
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        raise ValueError("포트는 숫자여야 합니다.") from None
    if not 1 <= port <= 65535:
        raise ValueError("포트는 1~65535 범위여야 합니다.")
    return host, port


def _classify_connection_error(e: Exception, timeout: int) -> str:
    """드라이버 예외 → 사용자용 사유. 원시 메시지(내부망 지문·경로)는 응답에 싣지 않는다."""
    import asyncio
    import socket

    name = type(e).__name__
    text = str(e).lower()
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return f"연결 시간 초과 ({timeout}초)"
    if isinstance(e, socket.gaierror):
        return "호스트를 찾을 수 없습니다."
    if isinstance(e, ConnectionRefusedError) or "refused" in text:
        return "연결이 거부되었습니다(호스트·포트를 확인하세요)."
    # MySQL 1044 "Access denied for user … to database 'x'" — 계정 인증은 통과했고 DB 가 없거나
    # 권한이 없다(MySQL 은 권한 없는 사용자에게 DB 존재 여부를 숨긴다). PG 의 CONNECT 권한 부족도 같다.
    if ("access denied" in text and "to database" in text) or "permission denied for database" in text:
        return "데이터베이스를 찾을 수 없거나 접근 권한이 없습니다."
    if (
        "password" in name.lower() or "authentication" in text or "access denied" in text
        or "invalidauthorization" in name.lower()
    ):
        return "인증에 실패했습니다(계정·비밀번호를 확인하세요)."
    if "does not exist" in text or "unknown database" in text:
        return "데이터베이스를 찾을 수 없습니다."
    return "연결에 실패했습니다(상세 사유는 서버 로그를 확인하세요)."



async def get_datasource_delete_impact(session: AsyncSession, datasource_id: int) -> dict:
    """데이터 소스 삭제 시 영향 — 데이터셋 수 + 타 데이터 소스과 연결된 리니지 엣지 수.

    external_lineage 는 이 데이터 소스의 데이터셋과 *다른* 데이터 소스의 데이터셋을 잇는 엣지로,
    cascade 삭제 시 함께 사라진다(상대 데이터 소스 리니지에 영향).
    """
    from sqlalchemy import and_, not_, or_

    dataset_count = await get_datasource_dataset_count(session, datasource_id)
    removed_count = await session.scalar(
        select(func.count()).select_from(Dataset).where(
            Dataset.datasource_id == datasource_id, Dataset.status == "removed",
        )
    )
    dp = select(Dataset.id).where(Dataset.datasource_id == datasource_id).scalar_subquery()
    src_in = DatasetLineage.source_dataset_id.in_(dp)
    tgt_in = DatasetLineage.target_dataset_id.in_(dp)
    external_lineage = await session.scalar(
        select(func.count())
        .select_from(DatasetLineage)
        .where(or_(and_(src_in, not_(tgt_in)), and_(tgt_in, not_(src_in))))
    )
    refs = await _datasource_reference_counts(session, datasource_id)
    # 페더레이션 노출 허용 목록은 설정 파일의 "데이터 소스 이름" 기준이라 삭제해도 남는다.
    # 같은 이름으로 다시 만들면 자동으로 노출되므로 삭제 화면에서 알린다.
    from app.core.config import settings as _settings

    ds_name = await session.scalar(select(Datasource.name).where(Datasource.id == datasource_id))
    federation_allowlisted = bool(
        ds_name and ds_name in (_settings.federation_export_datasource_allowlist or []))
    return {
        "federation_allowlisted": federation_allowlisted,
        "dataset_count": dataset_count,
        "removed_dataset_count": int(removed_count or 0),
        "external_lineage_count": int(external_lineage or 0),
        **refs,
    }


async def _datasource_reference_counts(session: AsyncSession, datasource_id: int) -> dict:
    """삭제 시 함께 사라지거나 연결이 끊기는 항목 수(삭제 영향 표시용)."""
    from app.alert.models import AlertRule
    from app.change_mgmt.models import ChangeRequest
    from app.mcp_servers.models import McpDeployment
    from app.models.models import RegisteredModel

    ds_ids = select(Dataset.id).where(Dataset.datasource_id == datasource_id).scalar_subquery()
    alert_rules = await session.scalar(
        select(func.count()).select_from(AlertRule).where(or_(
            (AlertRule.scope_type == "DATASOURCE") & (AlertRule.scope_id == datasource_id),
            (AlertRule.scope_type == "DATASET") & AlertRule.scope_id.in_(ds_ids),
        ))
    )
    change_requests = await session.scalar(
        select(func.count()).select_from(ChangeRequest).where(ChangeRequest.dataset_id.in_(ds_ids))
    )
    models = await session.scalar(
        select(func.count()).select_from(RegisteredModel)
        .where(RegisteredModel.datasource_id == datasource_id)
    )
    mcp = await session.scalar(
        select(func.count()).select_from(McpDeployment)
        .where(McpDeployment.datasource_id == datasource_id)
    )
    return {
        "alert_rule_count": int(alert_rules or 0),
        "change_request_count": int(change_requests or 0),
        "model_count": int(models or 0),
        "mcp_deployment_count": int(mcp or 0),
    }


async def delete_datasource(
    session: AsyncSession, datasource_id: int, force: bool = False
) -> bool:
    """데이터 소스 삭제. force=True 면 소속 데이터셋(및 모든 하위)을 먼저 삭제한다.

    데이터셋 하위(스키마/리니지/컬럼매핑/댓글/임베딩/스냅샷/변경관리/URN alias)는 모두
    ``ON DELETE CASCADE`` 라 데이터셋 행만 지우면 DB 가 연쇄 삭제한다.

    force 가 아니어도 원본에서 사라져 ``removed`` 로 표시된 데이터셋은 함께 정리한다 —
    활성 데이터셋 수(삭제 가드 기준)에는 잡히지 않지만 FK 로 남아 삭제를 막기 때문이다.
    """
    from sqlalchemy import delete as _sql_delete

    result = await session.execute(select(Datasource).where(Datasource.id == datasource_id))
    datasource = result.scalars().first()
    if not datasource:
        logger.warning("삭제할 데이터 소스를 찾을 수 없음: id=%d", datasource_id)
        return False
    from app.core.config import settings as _settings

    if datasource.name in (_settings.federation_export_datasource_allowlist or []):
        logger.warning("삭제한 데이터 소스 '%s' 가 페더레이션 노출 허용 목록 설정에 남아 있습니다 — "
                       "같은 이름으로 다시 만들면 자동 노출되니 설정에서 제거하세요.", datasource.name)
    doomed = select(Dataset.id).where(Dataset.datasource_id == datasource_id)
    if not force:
        doomed = doomed.where(Dataset.status == "removed")
    doomed_ids = list((await session.execute(doomed)).scalars().all())
    # 연쇄 삭제될 CR 의 진행 중 워크플로우 — 삭제 뒤엔 CR 행이 없어 미리 모은다(커밋 후 취소)
    from app.change_mgmt.service import cancel_workflows_best_effort, in_flight_workflow_ids
    workflow_ids = await in_flight_workflow_ids(session, doomed_ids)
    await _cleanup_datasource_references(session, datasource, doomed_ids)
    stmt = _sql_delete(Dataset).where(Dataset.datasource_id == datasource_id)
    if not force:
        stmt = stmt.where(Dataset.status == "removed")
    res = await session.execute(stmt)
    if res.rowcount:
        logger.info("데이터 소스 id=%d 의 데이터셋 %d개 연쇄 삭제 (force=%s)",
                    datasource_id, res.rowcount, force)
    await session.delete(datasource)
    await session.commit()
    logger.info("데이터 소스 삭제: %s (id=%d, force=%s)", datasource.name, datasource.id, force)
    # 삭제가 확정된 뒤 best-effort 취소 — Temporal 장애여도 삭제는 성공(IT CHG-T26)
    await cancel_workflows_best_effort(workflow_ids)
    return True


async def _cleanup_datasource_references(
    session: AsyncSession, datasource: Datasource, dataset_ids: list[int],
) -> None:
    """FK 가 없어 DB cascade 로 정리되지 않는 참조를 같은 트랜잭션에서 정리한다.

    - 알림 규칙(scope DATASOURCE/DATASET): 대상이 사라져 영원히 발화하지 않으므로 삭제
    - 댓글(entity_type='dataset', entity_id=str(id)): 다형 참조라 FK 없음 → 삭제
    - 쿼리 리니지(argus_query_lineage)의 dataset_id: NULL 로(테이블명은 남아 재해소 가능)
    - 관계 관측치(relationship_observations.datasource_id 문자열): 삭제
    """
    from sqlalchemy import bindparam as _bindparam
    from sqlalchemy import delete as _sql_delete
    from sqlalchemy import text as _text

    from app.alert.models import AlertRule
    from app.comments.models import Comment

    rule_cond = (AlertRule.scope_type == "DATASOURCE") & (AlertRule.scope_id == datasource.id)
    if dataset_ids:
        rule_cond = or_(
            rule_cond,
            (AlertRule.scope_type == "DATASET") & AlertRule.scope_id.in_(dataset_ids),
        )
    await session.execute(_sql_delete(AlertRule).where(rule_cond))

    if dataset_ids:
        await session.execute(_sql_delete(Comment).where(
            Comment.entity_type == "dataset",
            Comment.entity_id.in_([str(i) for i in dataset_ids]),
        ))
        for col in ("source_dataset_id", "target_dataset_id"):
            await session.execute(
                _text(f"UPDATE argus_query_lineage SET {col} = NULL WHERE {col} IN :ids")
                .bindparams(_bindparam("ids", expanding=True)),
                {"ids": dataset_ids},
            )
    # 데이터 소스 전체 삭제일 때만(이 함수는 데이터 소스 삭제 경로에서만 호출됨)
    await session.execute(
        _text("DELETE FROM relationship_observations WHERE datasource_id = :dsid"),
        {"dsid": datasource.datasource_id},
    )


async def remove_datasource_samples(datasource_id_str: str) -> None:
    """데이터 소스 샘플 디렉터리(data_dir/samples/{datasource_id}) 삭제 — 커밋 후 호출."""
    import asyncio
    import shutil

    from app.core.config import settings

    root = (settings.data_dir / "samples").resolve()
    target = (root / datasource_id_str).resolve()
    if target.parent != root or not target.is_dir():
        return
    try:
        await asyncio.to_thread(shutil.rmtree, target)
        logger.info("데이터 소스 샘플 디렉터리 삭제: %s", target)
    except OSError as e:
        logger.warning("샘플 디렉터리 삭제 실패 %s: %s", target, e)


async def get_datasource_configuration(
    session: AsyncSession, datasource_id: int
) -> dict | None:
    """데이터 소스의 설정 dict 를 반환한다. 없으면 None.

    비밀값(비밀번호·토큰 등)은 응답에 싣지 않는다 — 빈 문자열로 마스킹하고 저장 여부만
    ``secret_fields`` 로 알린다(``datasource_secrets.mask_config``).
    """
    from app.catalog.datasource_secrets import mask_config

    result = await session.execute(
        select(DatasourceConfiguration).where(
            DatasourceConfiguration.datasource_id == datasource_id
        )
    )
    row = result.scalars().first()
    if not row:
        return None
    config, secret_fields = mask_config(row.config_json)
    return {
        "id": row.id,
        "datasource_id": row.datasource_id,
        "config": config,
        "secret_fields": secret_fields,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def get_datasource_stored_config(session: AsyncSession, datasource_id: int) -> str | None:
    """저장된 config_json 원문(비밀값은 암호문). 없으면 None — 서버 내부 접속용."""
    result = await session.execute(
        select(DatasourceConfiguration.config_json).where(
            DatasourceConfiguration.datasource_id == datasource_id
        )
    )
    return result.scalar_one_or_none()


async def save_datasource_configuration(
    session: AsyncSession, datasource_id: int, config_dict: dict
) -> dict:
    """데이터 소스 설정을 upsert 하고 저장된 레코드를 반환한다.

    비밀값은 암호화해 저장하고, 빈 비밀값은 "변경 안 함"으로 보고 기존 값을 유지한다
    (응답에서 마스킹된 config 를 그대로 다시 저장해도 비밀번호가 지워지지 않도록).
    """
    import json as _json

    from app.catalog.datasource_secrets import guard_secret_reuse, seal_config

    result = await session.execute(
        select(DatasourceConfiguration).where(
            DatasourceConfiguration.datasource_id == datasource_id
        )
    )
    row = result.scalars().first()

    if config_dict.get("host") not in (None, ""):
        validate_connection_target(config_dict, 1)  # 형식 오류면 ValueError → 400
    elif config_dict.get("port") not in (None, ""):
        validate_connection_target({**config_dict, "host": "localhost"}, 1)
    # 접속 대상이 바뀌면 비밀값을 다시 받아야 한다 — 비워 둔 채 host 만 바꿔 저장하면
    # 이후 동기화·연결 테스트가 저장된 비밀번호를 새 서버로 보내게 된다(ValueError → 400).
    guard_secret_reuse(config_dict, row.config_json if row else None)
    sealed = seal_config(config_dict, row.config_json if row else None)
    config_text = _json.dumps(sealed, ensure_ascii=False)

    if row:
        row.config_json = config_text
    else:
        row = DatasourceConfiguration(
            datasource_id=datasource_id,
            config_json=config_text,
        )
        session.add(row)

    await session.commit()
    await session.refresh(row)
    logger.info("데이터 소스 설정 저장: datasource_id=%d", datasource_id)
    return await get_datasource_configuration(session, datasource_id)


async def get_datasource_dataset_count(session: AsyncSession, datasource_id: int) -> int:
    """주어진 데이터 소스에 속한 데이터셋 수를 센다(removed 제외 — 삭제 가드 기준)."""
    result = await session.execute(
        select(func.count()).select_from(Dataset).where(
            Dataset.datasource_id == datasource_id,
            Dataset.status != "removed",
        )
    )
    return result.scalar() or 0


# ---------------------------------------------------------------------------
# 태그 작업
# ---------------------------------------------------------------------------

async def list_tags(session: AsyncSession) -> list[TagResponse]:
    """전체 태그 목록을 이름 오름차순으로 조회."""
    result = await session.execute(select(Tag).order_by(Tag.name))
    return [TagResponse.model_validate(t) for t in result.scalars().all()]


class TagError(Exception):
    """태그 요청 검증 실패 — 라우터가 ``status_code`` 그대로 HTTP 오류로 변환한다."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def _ensure_unique_tag_name(session: AsyncSession, name: str, exclude_id: int | None = None) -> None:
    """대소문자만 다른 이름도 같은 태그로 본다 (PII / pii 중복 방지)."""
    stmt = select(Tag.name).where(func.lower(Tag.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Tag.id != exclude_id)
    existing = (await session.execute(stmt.limit(1))).scalar()
    if existing is not None:
        raise TagError(f"같은 이름의 태그가 이미 있습니다: {existing}", 409)


async def create_tag(session: AsyncSession, req: TagCreate) -> TagResponse:
    """태그 생성(이름·설명·색상). 이름 중복(대소문자 무시)이면 409."""
    await _ensure_unique_tag_name(session, req.name)
    tag = Tag(name=req.name, description=req.description, color=req.color)
    session.add(tag)
    await session.commit()
    await session.refresh(tag)
    logger.info("태그 생성: %s (id=%d)", tag.name, tag.id)
    return TagResponse.model_validate(tag)


async def update_tag(session: AsyncSession, tag_id: int, req: TagUpdate) -> TagResponse | None:
    """태그 이름·설명·색상 부분 수정 — 연결(데이터셋·모델·알림 규칙)은 그대로 유지된다. 없으면 None."""
    tag = (await session.execute(select(Tag).where(Tag.id == tag_id))).scalars().first()
    if not tag:
        return None
    data = req.model_dump(exclude_unset=True)
    # name/color 의 명시적 null 은 '변경 없음' (name 은 NOT NULL, color 는 배지 표시에 필요)
    data = {k: v for k, v in data.items() if not (v is None and k in ("name", "color"))}
    if "name" in data and data["name"] != tag.name:
        await _ensure_unique_tag_name(session, data["name"], exclude_id=tag_id)
    for k, v in data.items():
        setattr(tag, k, v)
    await session.commit()
    await session.refresh(tag)
    logger.info("태그 수정: id=%d, name=%s", tag.id, tag.name)
    return TagResponse.model_validate(tag)


async def _tag_alert_rules(session: AsyncSession, tag_id: int) -> list[TagUsageRef]:
    """TAG 범위로 이 태그를 감시하는 알림 규칙 — scope_id 는 FK 가 없어 삭제 시 직접 확인해야 한다."""
    from app.alert.models import AlertRule

    rows = (await session.execute(
        select(AlertRule.id, AlertRule.rule_name, AlertRule.is_active)
        .where(AlertRule.scope_type == "TAG", AlertRule.scope_id == tag_id)
        .order_by(AlertRule.rule_name)
    )).all()
    return [TagUsageRef(id=r.id, name=r.rule_name, is_active=str(r.is_active).lower() == "true") for r in rows]


async def _count_by_dataset(session: AsyncSession, dataset_col, dataset_ids: list[int]) -> dict[int, int]:
    """``dataset_col`` (자식 테이블의 dataset_id 컬럼) 기준 데이터셋별 행 수."""
    if not dataset_ids:
        return {}
    result = await session.execute(
        select(dataset_col, func.count())
        .where(dataset_col.in_(dataset_ids))
        .group_by(dataset_col)
    )
    return {ds_id: n for ds_id, n in result.all()}


async def get_tag_usage(session: AsyncSession, tag_id: int) -> TagUsage | None:
    """태그 사용 현황(데이터셋·OCI 모델·TAG 범위 알림 규칙) 조회. 태그 삭제 전 영향 확인용.

    태그가 없으면 None. 데이터셋은 removed 상태도 포함한다.
    """
    result = await session.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalars().first()
    if not tag:
        return None

    tag_resp = TagResponse.model_validate(tag)

    # 이 태그를 사용하는 데이터셋 조회
    dataset_query = (
        select(
            Dataset.id,
            Dataset.urn,
            Dataset.name,
            Dataset.display_name,
            Datasource.name.label("datasource_name"),
            Datasource.type.label("datasource_type"),
            Dataset.summary,
            Dataset.description,
            Dataset.origin,
            Dataset.status,
            Dataset.is_synced,
            Dataset.created_at,
            Dataset.updated_at,
        )
        .join(Datasource, Dataset.datasource_id == Datasource.id)
        .where(
            Dataset.id.in_(
                select(DatasetTag.dataset_id).where(DatasetTag.tag_id == tag_id)
            )
        )
        .order_by(Dataset.name)
    )
    result = await session.execute(dataset_query)
    rows = result.all()

    # 데이터셋 목록(list_datasets)과 같은 기준의 개수 — 예전에는 0 고정이었다.
    # 태그가 많은 데이터셋에 붙어 있을 수 있어 데이터셋별 조회 대신 GROUP BY 한 번씩
    ids = [row.id for row in rows]
    tag_counts = await _count_by_dataset(session, DatasetTag.dataset_id, ids)
    owner_counts = await _count_by_dataset(session, Owner.dataset_id, ids)
    field_counts = await _count_by_dataset(session, DatasetSchema.dataset_id, ids)

    datasets = [
        DatasetSummary(
            id=row.id,
            urn=row.urn,
            name=row.name,
            display_name=row.display_name,
            datasource_name=row.datasource_name,
            datasource_type=row.datasource_type,
            summary=row.summary,
            description=row.description,
            origin=row.origin,
            status=row.status,
            is_synced=row.is_synced or "false",
            tag_count=tag_counts.get(row.id, 0),
            owner_count=owner_counts.get(row.id, 0),
            schema_field_count=field_counts.get(row.id, 0),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]

    # 태그는 OCI 모델 허브와도 공유된다 — 삭제하면 모델 연결도 CASCADE 로 함께 지워진다
    from app.oci_hub.models import OciModel, OciModelTag

    oci_rows = (await session.execute(
        select(OciModel.id, OciModel.name)
        .join(OciModelTag, OciModelTag.model_id == OciModel.id)
        .where(OciModelTag.tag_id == tag_id)
        .order_by(OciModel.name)
    )).all()

    return TagUsage(
        tag=tag_resp, datasets=datasets, total_datasets=len(datasets),
        oci_models=[TagUsageRef(id=r.id, name=r.name) for r in oci_rows],
        alert_rules=await _tag_alert_rules(session, tag_id),
    )


async def delete_tag(session: AsyncSession, tag_id: int) -> bool:
    """태그 삭제. 데이터셋·OCI 모델 연결은 CASCADE 로 함께 제거된다.

    TAG 범위 알림 규칙이 있으면 409 (``TagError``) — 규칙의 ``scope_id`` 는 FK 가 없어
    그대로 지우면 규칙이 오류 없이 영영 발동하지 않게 되므로, 규칙을 먼저 정리하게 한다.
    """
    result = await session.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalars().first()
    if not tag:
        logger.warning("삭제할 태그를 찾을 수 없음: id=%d", tag_id)
        return False
    rules = await _tag_alert_rules(session, tag_id)
    if rules:
        names = ", ".join(r.name for r in rules[:3]) + (f" 외 {len(rules) - 3}개" if len(rules) > 3 else "")
        raise TagError(
            f"태그를 삭제할 수 없습니다: 이 태그를 감시 범위로 쓰는 알림 규칙 {len(rules)}개가 있습니다 ({names}). "
            "규칙의 범위를 바꾸거나 규칙을 먼저 삭제하세요.",
            409,
        )
    await session.delete(tag)
    await session.commit()
    logger.info("태그 삭제: %s (id=%d)", tag.name, tag.id)
    return True


# ---------------------------------------------------------------------------
# 용어집 용어 작업
# ---------------------------------------------------------------------------

class GlossaryError(Exception):
    """용어집 요청 검증 실패 — 라우터가 ``status_code`` 그대로 HTTP 오류로 변환한다."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def list_glossary_terms(session: AsyncSession) -> list[GlossaryTermResponse]:
    """모든 용어집 용어를 이름순으로 조회한다."""
    result = await session.execute(select(GlossaryTerm).order_by(GlossaryTerm.name))
    return [GlossaryTermResponse.model_validate(t) for t in result.scalars().all()]


async def _ensure_unique_glossary_name(
    session: AsyncSession, name: str, term_type: str, exclude_id: int | None = None,
) -> None:
    """이름 중복이면 409 — DB UNIQUE(term_type, name) 위반을 구체적인 안내로 바꾼다.

    분류끼리·용어끼리만 이름이 겹칠 수 없다(분류 '고객' 과 용어 '고객' 은 공존 가능).
    """
    stmt = select(GlossaryTerm.id).where(GlossaryTerm.name == name, GlossaryTerm.term_type == term_type)
    if exclude_id is not None:
        stmt = stmt.where(GlossaryTerm.id != exclude_id)
    if (await session.execute(stmt.limit(1))).first():
        kind = "분류" if term_type == "CATEGORY" else "용어"
        raise GlossaryError(f"같은 이름의 {kind}가 이미 있습니다: {name}", 409)


async def _validate_glossary_parent(
    session: AsyncSession, parent_id: int | None, term_id: int | None = None,
) -> None:
    """상위 항목 검증 — 존재하는 분류(CATEGORY)여야 하고, 자기 자신·하위로는 옮길 수 없다."""
    if parent_id is None:
        return
    if term_id is not None and parent_id == term_id:
        raise GlossaryError("자기 자신을 상위 분류로 지정할 수 없습니다.")
    parent = (await session.execute(
        select(GlossaryTerm).where(GlossaryTerm.id == parent_id)
    )).scalars().first()
    if not parent:
        raise GlossaryError("상위 분류를 찾을 수 없습니다.", 404)
    if parent.term_type != "CATEGORY":
        raise GlossaryError("용어 아래에는 항목을 둘 수 없습니다. 분류를 상위로 지정하세요.")
    if term_id is None:
        return
    # 새 상위의 조상 사슬에 자기 자신이 있으면 순환 — 기존 데이터가 이미 순환이어도 멈추도록 방문 집합 사용
    seen: set[int] = set()
    cur: int | None = parent.parent_id
    while cur is not None and cur not in seen:
        if cur == term_id:
            raise GlossaryError("하위 분류 아래로는 옮길 수 없습니다.")
        seen.add(cur)
        cur = (await session.execute(
            select(GlossaryTerm.parent_id).where(GlossaryTerm.id == cur)
        )).scalar()


async def create_glossary_term(
    session: AsyncSession, req: GlossaryTermCreate
) -> GlossaryTermResponse:
    """용어집 항목을 만들고 커밋한 뒤 임베딩을 백그라운드로 요청한다.

    이름 중복 409, 상위가 없으면 404, 상위가 분류가 아니면 400(모두 ``GlossaryError``).
    """
    await _ensure_unique_glossary_name(session, req.name, req.term_type)
    await _validate_glossary_parent(session, req.parent_id)
    term = GlossaryTerm(
        name=req.name, description=req.description,
        parent_id=req.parent_id,
        term_type=req.term_type,
    )
    session.add(term)
    await session.commit()
    await session.refresh(term)
    logger.info("용어집 용어 생성: %s (id=%d)", term.name, term.id)

    # 시맨틱 검색용 임베딩 (백그라운드, 실패해도 생성 흐름에 영향 없음)
    from app.embedding.service import embed_entity_background
    await embed_entity_background("glossary_term", term.id)

    return GlossaryTermResponse.model_validate(term)


_GLOSSARY_UPDATABLE = ("name", "description", "parent_id", "term_type")


async def update_glossary_term(
    session: AsyncSession, term_id: int, data: dict,
) -> GlossaryTermResponse | None:
    """용어집 항목 부분 갱신. 없으면 None, 검증 실패는 ``GlossaryError``."""
    result = await session.execute(
        select(GlossaryTerm).where(GlossaryTerm.id == term_id)
    )
    term = result.scalars().first()
    if not term:
        return None
    # name/term_type 은 NOT NULL — 명시적 null 은 '변경 없음'으로 본다
    data = {k: v for k, v in data.items()
            if k in _GLOSSARY_UPDATABLE and not (v is None and k in ("name", "term_type"))}

    new_name = data.get("name", term.name)
    new_type = data.get("term_type", term.term_type)
    if new_name != term.name or new_type != term.term_type:
        await _ensure_unique_glossary_name(session, new_name, new_type, exclude_id=term_id)
    if "parent_id" in data and data["parent_id"] != term.parent_id:
        await _validate_glossary_parent(session, data["parent_id"], term_id)
    if data.get("term_type") == "TERM" and term.term_type == "CATEGORY":
        if await count_glossary_term_children(session, term_id):
            raise GlossaryError("하위 항목이 있는 분류는 용어로 바꿀 수 없습니다.")

    renamed = "name" in data and data["name"] != term.name
    for k, v in data.items():
        setattr(term, k, v)
    await session.commit()
    await session.refresh(term)
    logger.info("용어집 용어 수정: id=%d, name=%s", term.id, term.name)

    from app.embedding.service import embed_entity_background
    await embed_entity_background("glossary_term", term.id)
    # 임베딩 문장에 '상위 분류: <이름>' 이 들어가므로, 분류 이름이 바뀌면 직속 하위도 다시 임베딩한다
    if renamed and term.term_type == "CATEGORY":
        child_ids = (await session.execute(
            select(GlossaryTerm.id).where(GlossaryTerm.parent_id == term_id)
        )).scalars().all()
        for cid in child_ids:
            await embed_entity_background("glossary_term", cid)

    return GlossaryTermResponse.model_validate(term)


async def count_glossary_term_children(session: AsyncSession, term_id: int) -> int:
    """직속 하위 용어 수 — 삭제 가드용.

    ``catalog_glossary_terms.parent_id`` 자기참조 FK 는 ON DELETE 가 없어, 하위 용어가
    있으면 삭제가 차단된다(분류·조직과 동일). 라우터에서 미리 막아 친절한 409 를 준다.
    """
    return int(await session.scalar(
        select(func.count(GlossaryTerm.id)).where(GlossaryTerm.parent_id == term_id)
    ) or 0)


async def glossary_term_children_by_type(session: AsyncSession, term_id: int) -> tuple[int, int]:
    """직속 하위 항목을 (분류 수, 용어 수) 로 나눠 센다 — 삭제 거부 안내 문구용."""
    rows = (await session.execute(
        select(GlossaryTerm.term_type, func.count(GlossaryTerm.id))
        .where(GlossaryTerm.parent_id == term_id)
        .group_by(GlossaryTerm.term_type)
    )).all()
    counts = {t: int(n) for t, n in rows}
    return counts.get("CATEGORY", 0), sum(n for t, n in counts.items() if t != "CATEGORY")


async def delete_glossary_term(session: AsyncSession, term_id: int) -> bool:
    """용어집 용어를 삭제하고 모든 데이터셋 연결을 제거한다(하위 용어가 없을 때).

    데이터셋 매핑(``catalog_dataset_glossary_terms``)은 CASCADE 로 정리되지만, 하위 용어
    (``parent_id`` 자기참조)는 CASCADE 가 없으므로 호출 전에 ``count_glossary_term_children``
    (라우터는 ``glossary_term_children_by_type``)으로 막아야 한다.
    """
    result = await session.execute(
        select(GlossaryTerm).where(GlossaryTerm.id == term_id)
    )
    term = result.scalars().first()
    if not term:
        logger.warning("삭제할 용어집 용어를 찾을 수 없음: id=%d", term_id)
        return False
    # 임베딩 행 정리 (다형 테이블이라 FK CASCADE 가 없음)
    from app.embedding.service import delete_entity_embedding
    await delete_entity_embedding(session, "glossary_term", term_id)

    await session.delete(term)
    await session.commit()
    logger.info("용어집 용어 삭제: %s (id=%d)", term.name, term.id)
    return True


# ---------------------------------------------------------------------------
# 데이터셋 작업
# ---------------------------------------------------------------------------

async def _build_dataset_response(
    session: AsyncSession, dataset: Dataset
) -> DatasetResponse:
    """관련 엔티티를 모두 포함한 전체 DatasetResponse 를 구성한다."""
    # 데이터 소스
    result = await session.execute(select(Datasource).where(Datasource.id == dataset.datasource_id))
    datasource = result.scalars().first()

    # 스키마 필드
    result = await session.execute(
        select(DatasetSchema)
        .where(DatasetSchema.dataset_id == dataset.id)
        .order_by(DatasetSchema.ordinal)
    )
    schema_fields = [SchemaFieldResponse.model_validate(f) for f in result.scalars().all()]

    # 태그
    result = await session.execute(
        select(Tag)
        .join(DatasetTag, DatasetTag.tag_id == Tag.id)
        .where(DatasetTag.dataset_id == dataset.id)
    )
    tags = [TagResponse.model_validate(t) for t in result.scalars().all()]

    # 소유자
    result = await session.execute(
        select(Owner).where(Owner.dataset_id == dataset.id)
    )
    owners = [OwnerResponse.model_validate(o) for o in result.scalars().all()]

    # 용어집 용어
    result = await session.execute(
        select(GlossaryTerm)
        .join(DatasetGlossaryTerm, DatasetGlossaryTerm.term_id == GlossaryTerm.id)
        .where(DatasetGlossaryTerm.dataset_id == dataset.id)
    )
    glossary_terms = [GlossaryTermResponse.model_validate(t) for t in result.scalars().all()]

    # 속성
    result = await session.execute(
        select(DatasetProperty).where(DatasetProperty.dataset_id == dataset.id)
    )
    properties = [DatasetPropertyResponse.model_validate(p) for p in result.scalars().all()]

    import json as _json
    pp = None
    if dataset.datasource_properties:
        try:
            pp = _json.loads(dataset.datasource_properties)
        except (ValueError, TypeError):
            pass

    return DatasetResponse(
        id=dataset.id,
        urn=dataset.urn,
        created_by=dataset.created_by,
        name=dataset.name,
        display_name=dataset.display_name,
        datasource=await _datasource_response(session, datasource),
        summary=dataset.summary,
        description=dataset.description,
        origin=dataset.origin,
        qualified_name=dataset.qualified_name,
        table_type=dataset.table_type,
        storage_format=dataset.storage_format,
        ddl=dataset.ddl,
        status=dataset.status,
        is_synced=dataset.is_synced or "false",
        ingestion_frequency=dataset.ingestion_frequency,
        ingestion_time=dataset.ingestion_time,
        ingestion_day=dataset.ingestion_day,
        ingestion_timezone=dataset.ingestion_timezone,
        ingestion_cron=dataset.ingestion_cron,
        ingestion_mode=dataset.ingestion_mode,
        update_type=dataset.update_type,
        freshness_sla=dataset.freshness_sla,
        last_ingested_at=dataset.last_ingested_at,
        retention_days=dataset.retention_days,
        purge_days=dataset.purge_days,
        data_category=dataset.data_category,
        data_format=dataset.data_format,
        compression=dataset.compression,
        encoding=dataset.encoding,
        row_count=dataset.row_count,
        byte_size=dataset.byte_size,
        file_count=dataset.file_count,
        sensitivity=dataset.sensitivity,
        contains_pii=(dataset.contains_pii == "true") if dataset.contains_pii is not None else None,
        pii_fields=dataset.pii_fields,
        compliance_tags=dataset.compliance_tags,
        tier=dataset.tier,
        certification=dataset.certification,
        steward=dataset.steward,
        view_count=dataset.view_count or 0,
        query_count=dataset.query_count or 0,
        last_accessed_at=dataset.last_accessed_at,
        quality_score=dataset.quality_score,
        quality_status=dataset.quality_status,
        show_quality_score=(dataset.show_quality_score == "true"),
        note=dataset.note,
        datasource_properties=pp,
        schema_fields=schema_fields,
        tags=tags,
        owners=owners,
        glossary_terms=glossary_terms,
        properties=properties,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


async def dataset_exists(session: AsyncSession, dataset_id: int) -> bool:
    """데이터셋 존재 여부 — 소유권 가드에서 '생성자 NULL' 과 '없음' 을 구분하는 용도."""
    return (await session.execute(select(Dataset.id).where(Dataset.id == dataset_id))).scalar() is not None


async def get_dataset_urn(session: AsyncSession, dataset_id: int) -> str | None:
    """데이터셋 URN(캐시 키 등). 없으면 None."""
    return (await session.execute(
        select(Dataset.urn).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()


async def get_dataset_created_by(session: AsyncSession, dataset_id: int) -> str | None:
    """데이터셋의 생성자(created_by) — 소유권 체크용. 데이터셋이 없으면 None."""
    return (await session.execute(
        select(Dataset.created_by).where(Dataset.id == dataset_id)
    )).scalar_one_or_none()


async def create_dataset(session: AsyncSession, req: DatasetCreate, created_by: str | None = None) -> DatasetResponse:
    """데이터셋을 스키마 필드·태그·소유자·속성과 함께 만들고 커밋한다.

    URN 은 ``{datasource_id}.{qualified_name 또는 name}.dataset``, qualified_name 은
    ``{datasource_id}.{path}`` 로 저장한다. 커밋 후 임베딩을 요청하고 쿼리 리니지를 점증 해석한다
    (해석 실패는 경고만). 데이터 소스가 없으면 ``ValueError``.
    """
    # URN 생성을 위한 데이터 소스 조회
    result = await session.execute(select(Datasource).where(Datasource.id == req.datasource_id))
    datasource = result.scalars().first()
    if not datasource:
        logger.warning("데이터셋 생성 거부 — 데이터 소스 없음: datasource_id=%d name=%s", req.datasource_id, req.name)
        raise ValueError(f"Datasource with id {req.datasource_id} not found")

    path = req.qualified_name or req.name
    urn = _generate_urn(datasource.datasource_id, path)
    qualified_name = f"{datasource.datasource_id}.{path}"

    import json as _json
    dataset = Dataset(
        urn=urn,
        name=req.name,
        display_name=req.display_name,
        datasource_id=req.datasource_id,
        summary=req.summary,
        description=req.description,
        created_by=created_by,
        # origin 생략 시 데이터 소스 환경을 그대로 따른다(데이터셋 origin 은 데이터 소스 origin 미러).
        origin=req.origin.value if req.origin is not None else (datasource.origin or "PROD"),
        qualified_name=qualified_name,
        table_type=req.table_type,
        storage_format=req.storage_format,
        ddl=req.ddl,
        # DatasourceSpecificCard 가 columns/indexes 를 읽는 자리 — JSON 직렬화 저장.
        datasource_properties=(
            _json.dumps(req.datasource_properties, ensure_ascii=False)
            if req.datasource_properties else None
        ),
        status="active",
    )
    session.add(dataset)
    await session.flush()

    # 스키마 필드 추가
    for idx, field in enumerate(req.schema_fields):
        schema_field = DatasetSchema(
            dataset_id=dataset.id,
            field_path=field.field_path,
            display_name=field.display_name,
            field_type=field.field_type,
            native_type=field.native_type,
            description=field.description,
            nullable=field.nullable,
            is_primary_key=field.is_primary_key,
            is_unique=field.is_unique,
            is_indexed=field.is_indexed,
            is_partition_key=field.is_partition_key,
            is_distribution_key=field.is_distribution_key,
            ordinal=field.ordinal or idx,
        )
        session.add(schema_field)

    # 태그 부착
    for tag_id in req.tags:
        session.add(DatasetTag(dataset_id=dataset.id, tag_id=tag_id))

    # 소유자 추가
    for owner in req.owners:
        session.add(Owner(
            dataset_id=dataset.id,
            owner_name=owner.owner_name,
            owner_type=owner.owner_type.value,
        ))

    # 속성 추가
    for prop in req.properties:
        session.add(DatasetProperty(
            dataset_id=dataset.id,
            property_key=prop.key,
            property_value=prop.value,
        ))

    await session.commit()
    await session.refresh(dataset)
    logger.info("데이터셋 생성: %s (id=%d, urn=%s)", dataset.name, dataset.id, dataset.urn)

    # 시맨틱 검색용 백그라운드 임베딩 트리거
    from app.embedding.service import embed_dataset_background
    await embed_dataset_background(dataset.id)

    # 쿼리 lineage 의 dataset_id 점증 해석 — 이 데이터셋명과 매칭되는 lineage 엣지를 연결.
    try:
        from app.catalog.lineage_resolve import resolve_lineage_for_dataset
        await resolve_lineage_for_dataset(session, dataset.id, dataset.qualified_name)
    except Exception as e:
        logger.warning("생성 시 리니지 해석 실패 (%s): %s", dataset.name, e)

    return await _build_dataset_response(session, dataset)


async def get_dataset(session: AsyncSession, dataset_id: int) -> DatasetResponse | None:
    """ID 로 데이터셋 하나를 모든 연관 관계와 함께 조회한다."""
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalars().first()
    if not dataset:
        return None
    return await _build_dataset_response(session, dataset)


async def get_dataset_by_urn(session: AsyncSession, urn: str) -> DatasetResponse | None:
    """URN 으로 데이터셋 하나를 모든 연관 관계와 함께 조회한다.

    구 URN(`...DEV.dataset`)으로 들어와도 해소되도록 폴백한다:
    직접 매칭 → 환경 suffix 제거 정규화 → URN alias 테이블.
    """
    result = await session.execute(select(Dataset).where(Dataset.urn == urn))
    dataset = result.scalars().first()

    if dataset is None:
        normalized = strip_env_urn(urn)
        if normalized != urn:
            result = await session.execute(select(Dataset).where(Dataset.urn == normalized))
            dataset = result.scalars().first()

    if dataset is None:
        from app.catalog.models import DatasetUrnAlias
        result = await session.execute(
            select(Dataset)
            .join(DatasetUrnAlias, DatasetUrnAlias.dataset_id == Dataset.id)
            .where(DatasetUrnAlias.old_urn == urn)
        )
        dataset = result.scalars().first()

    if not dataset:
        return None
    return await _build_dataset_response(session, dataset)


async def update_dataset(
    session: AsyncSession, dataset_id: int, req: DatasetUpdate
) -> DatasetResponse | None:
    """데이터셋 부분 수정 — None 인 필드는 변경하지 않는다. 없으면 None.

    qualified_name 이 바뀌면 URN 을 다시 만든다(origin 은 URN 에 없어 불변). 커밋 후 재임베딩한다.
    """
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalars().first()
    if not dataset:
        logger.debug("수정 대상 데이터셋 없음: dataset_id=%d", dataset_id)
        return None

    if req.name is not None:
        dataset.name = req.name
    if req.display_name is not None:
        # 빈 문자열을 보내면 명시적으로 logical name 을 지우는 것으로 간주.
        dataset.display_name = req.display_name or None
    if req.summary is not None:
        dataset.summary = req.summary
    if req.description is not None:
        dataset.description = req.description
    if req.origin is not None:
        dataset.origin = req.origin.value
    if req.qualified_name is not None:
        dataset.qualified_name = req.qualified_name
    if req.table_type is not None:
        dataset.table_type = req.table_type
    if req.storage_format is not None:
        dataset.storage_format = req.storage_format
    if req.ddl is not None:
        # 빈 문자열도 의미 있는 값으로 받아들여 그대로 저장. None(또는 생략)은 '변경 없음' 이라
        # 이 분기를 타지 않으므로, 이 API 로는 DDL 을 NULL 로 되돌릴 수 없다(빈 문자열로 비움).
        dataset.ddl = req.ddl
    if req.datasource_properties is not None:
        import json as _json
        dataset.datasource_properties = (
            _json.dumps(req.datasource_properties, ensure_ascii=False)
            if req.datasource_properties else None
        )
    if req.status is not None:
        dataset.status = req.status.value

    # --- 확장 메타데이터 (단순 passthrough; None 이면 미변경) ---
    _ext_fields = (
        "ingestion_frequency", "ingestion_time", "ingestion_day", "ingestion_timezone",
        "ingestion_cron", "ingestion_mode", "update_type", "freshness_sla",
        "last_ingested_at", "retention_days", "purge_days",
        "data_category", "data_format", "compression", "encoding", "row_count", "byte_size", "file_count",
        "sensitivity", "pii_fields", "compliance_tags",
        "tier", "certification", "steward", "quality_status", "note",
    )
    for f in _ext_fields:
        v = getattr(req, f, None)
        if v is not None:
            setattr(dataset, f, v)
    # bool → "true"/"false" 문자열로 저장
    if req.contains_pii is not None:
        dataset.contains_pii = "true" if req.contains_pii else "false"
    if req.show_quality_score is not None:
        dataset.show_quality_score = "true" if req.show_quality_score else "false"

    # qualified_name 이 바뀔 때만 URN 을 재생성한다.
    # origin 은 더 이상 URN 에 포함되지 않으므로 승격(origin 변경) 시 URN 은 불변이다.
    if req.qualified_name is not None:
        datasource_row = await session.execute(
            select(Datasource).where(Datasource.id == dataset.datasource_id)
        )
        datasource = datasource_row.scalars().first()
        if datasource:
            qn = dataset.qualified_name or dataset.name
            prefix = f"{datasource.datasource_id}."
            path = qn[len(prefix):] if qn.startswith(prefix) else qn
            dataset.urn = _generate_urn(datasource.datasource_id, path)
            # 생성 시와 같은 저장 형식({datasource_id}.{path})으로 맞춘다 — metadata-sync 는
            # 갱신 때 접두사 없는 경로(db.t)를 보내 생성 시와 형식이 달라졌다.
            dataset.qualified_name = f"{prefix}{path}"

    await session.commit()
    await session.refresh(dataset)
    logger.info("데이터셋 수정: %s (id=%d)", dataset.name, dataset.id)

    # 시맨틱 검색용 재임베딩
    from app.embedding.service import embed_dataset_background
    await embed_dataset_background(dataset.id)

    return await _build_dataset_response(session, dataset)


async def delete_dataset(session: AsyncSession, dataset_id: int) -> bool:
    """데이터셋과 모든 연관 관계(태그, 소유자, 스키마 등 — DB CASCADE)를 삭제한다. 없으면 False."""
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    dataset = result.scalars().first()
    if not dataset:
        logger.debug("삭제 대상 데이터셋 없음: dataset_id=%d", dataset_id)
        return False
    # 연쇄 삭제될 CR 의 진행 중 워크플로우 — 삭제 뒤엔 CR 행이 없어 미리 모은다(커밋 후 취소)
    from app.change_mgmt.service import cancel_workflows_best_effort, in_flight_workflow_ids
    workflow_ids = await in_flight_workflow_ids(session, [dataset.id])
    await session.delete(dataset)
    await session.commit()
    logger.info("데이터셋 삭제: %s (id=%d)", dataset.name, dataset.id)
    # 삭제가 확정된 뒤 best-effort 취소 — Temporal 장애여도 삭제는 성공(IT CHG-T26)
    await cancel_workflows_best_effort(workflow_ids)
    return True


async def list_datasets(
    session: AsyncSession,
    search: str | None = None,
    datasource: str | None = None,
    origin: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    org_id: int | None = None,
    system_id: int | None = None,
    category_id: int | None = None,
    taxonomy_id: int | None = None,
    uncategorized: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedDatasets:
    """선택적 필터·페이지네이션으로 데이터셋 목록을 조회한다.

    ``org_id`` 는 해당 조직 + 모든 하위 조직(서브트리)의 시스템에 속한 데이터 소스의
    데이터셋을 포함한다. ``system_id`` 는 해당 시스템의 데이터 소스만 필터.
    ``category_id`` 는 해당 분류 + 모든 하위 분류(서브트리)에 매핑된 데이터셋.
    ``taxonomy_id`` + ``uncategorized=True`` 는 해당 분류체계에 미매핑된 데이터셋.
    ``status`` 를 주지 않으면 removed 데이터셋은 제외한다. 정렬은 updated_at 내림차순.
    """
    base = (
        select(
            Dataset.id,
            Dataset.urn,
            Dataset.name,
            Dataset.display_name,
            Datasource.name.label("datasource_name"),
            Datasource.type.label("datasource_type"),
            Dataset.summary,
            Dataset.description,
            Dataset.origin,
            Dataset.status,
            Dataset.is_synced,
            Dataset.created_by,
            Dataset.created_at,
            Dataset.updated_at,
        )
        .join(Datasource, Dataset.datasource_id == Datasource.id)
    )

    if search:
        # 검색어의 % _ 는 글자로 매칭(와일드카드로 해석하면 "%" 하나로 전체가 나온다)
        from app.core.sql import LIKE_ESCAPE, like_contains

        pattern = like_contains(search)
        base = base.where(
            or_(
                Dataset.name.ilike(pattern, escape=LIKE_ESCAPE),
                Dataset.display_name.ilike(pattern, escape=LIKE_ESCAPE),
                Dataset.summary.ilike(pattern, escape=LIKE_ESCAPE),
                Dataset.description.ilike(pattern, escape=LIKE_ESCAPE),
                Dataset.urn.ilike(pattern, escape=LIKE_ESCAPE),
                Dataset.qualified_name.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )

    if datasource:
        base = base.where(Datasource.datasource_id == datasource)

    if system_id is not None:
        base = base.where(Datasource.system_id == system_id)

    if org_id is not None:
        # 조직 서브트리(자기 + 하위)의 시스템에 속한 데이터 소스만
        from app.catalog.topology_service import _descendant_org_ids
        org_ids = {org_id} | await _descendant_org_ids(session, org_id)
        base = base.where(
            Datasource.system_id.in_(
                select(System.id).where(System.org_id.in_(org_ids))
            )
        )

    if category_id is not None:
        # 분류 서브트리(자기 + 하위)에 매핑된 데이터셋
        from app.catalog.models import Category, DatasetCategory
        from app.catalog.taxonomy_service import _descendant_category_ids
        cat_ids = {category_id} | await _descendant_category_ids(session, category_id)
        base = base.where(
            Dataset.id.in_(
                select(DatasetCategory.dataset_id).where(DatasetCategory.category_id.in_(cat_ids))
            )
        )

    if taxonomy_id is not None and uncategorized:
        # 해당 분류체계의 어떤 카테고리에도 매핑되지 않은 데이터셋
        from app.catalog.models import Category, DatasetCategory
        mapped_sub = (
            select(DatasetCategory.dataset_id)
            .join(Category, Category.id == DatasetCategory.category_id)
            .where(Category.taxonomy_id == taxonomy_id)
        )
        base = base.where(Dataset.id.notin_(mapped_sub))

    if origin:
        base = base.where(Dataset.origin == origin)

    if status:
        base = base.where(Dataset.status == status)
    else:
        base = base.where(Dataset.status != "removed")

    if tag:
        base = base.where(
            Dataset.id.in_(
                select(DatasetTag.dataset_id)
                .join(Tag, DatasetTag.tag_id == Tag.id)
                .where(Tag.name == tag)
            )
        )

    # 개수 집계
    count_query = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    # 페이지네이션
    offset = (page - 1) * page_size
    query = base.order_by(Dataset.updated_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)
    rows = result.all()

    logger.debug(
        "데이터셋 목록 조회: search=%s datasource=%s status=%s org_id=%s system_id=%s category_id=%s "
        "total=%d page=%d rows=%d",
        search, datasource, status, org_id, system_id, category_id, total, page, len(rows),
    )

    # 개수 정보를 포함한 요약 구성(행마다 집계 쿼리 3회 — 페이지 크기만큼만)
    items = []
    for row in rows:
        # 각 데이터셋의 개수 조회
        tag_count = (await session.execute(
            select(func.count()).where(DatasetTag.dataset_id == row.id)
        )).scalar() or 0
        owner_count = (await session.execute(
            select(func.count()).where(Owner.dataset_id == row.id)
        )).scalar() or 0
        field_count = (await session.execute(
            select(func.count()).where(DatasetSchema.dataset_id == row.id)
        )).scalar() or 0

        items.append(DatasetSummary(
            id=row.id,
            urn=row.urn,
            name=row.name,
            display_name=row.display_name,
            datasource_name=row.datasource_name,
            datasource_type=row.datasource_type,
            summary=row.summary,
            description=row.description,
            origin=row.origin,
            status=row.status,
            is_synced=row.is_synced or "false",
            tag_count=tag_count,
            owner_count=owner_count,
            schema_field_count=field_count,
            created_by=row.created_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        ))

    return PaginatedDatasets(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# 데이터셋 연관 관계 작업
# ---------------------------------------------------------------------------

async def add_dataset_tag(session: AsyncSession, dataset_id: int, tag_id: int) -> bool:
    """데이터셋에 태그를 연결한다. 데이터셋이 없으면 False, 태그가 없으면 404(``TagError``).

    이미 연결돼 있으면 아무것도 하지 않는다(멱등) — 연타·재시도로 같은 연결이 중복 생성되지 않게.
    """
    if (await session.execute(select(Dataset.id).where(Dataset.id == dataset_id))).scalar() is None:
        logger.warning("태그 추가 대상 데이터셋을 찾을 수 없음: dataset_id=%d", dataset_id)
        return False
    if (await session.execute(select(Tag.id).where(Tag.id == tag_id))).scalar() is None:
        raise TagError("태그를 찾을 수 없습니다.", 404)
    exists = (await session.execute(
        select(DatasetTag.id).where(DatasetTag.dataset_id == dataset_id, DatasetTag.tag_id == tag_id).limit(1)
    )).scalar()
    if exists is not None:
        return True
    session.add(DatasetTag(dataset_id=dataset_id, tag_id=tag_id))
    await session.commit()
    logger.info("태그 %d 를 데이터셋 %d 에 추가", tag_id, dataset_id)
    return True


async def remove_dataset_tag(session: AsyncSession, dataset_id: int, tag_id: int) -> bool:
    """데이터셋-태그 연결 제거. 과거에 생긴 중복 연결도 함께 지운다. 연결이 없으면 False."""
    rows = (await session.execute(
        select(DatasetTag)
        .where(DatasetTag.dataset_id == dataset_id, DatasetTag.tag_id == tag_id)
    )).scalars().all()
    if not rows:
        logger.debug("분리할 태그 연결 없음: dataset_id=%d tag_id=%d", dataset_id, tag_id)
        return False
    for dt in rows:
        await session.delete(dt)
    await session.commit()
    logger.info("태그 %d 를 데이터셋 %d 에서 제거 (연결 %d건)", tag_id, dataset_id, len(rows))
    return True


async def add_dataset_owner(
    session: AsyncSession, dataset_id: int, req: OwnerCreate
) -> OwnerResponse | None:
    """데이터셋에 소유자를 추가한다."""
    result = await session.execute(select(Dataset).where(Dataset.id == dataset_id))
    if not result.scalars().first():
        logger.warning("소유자 추가 대상 데이터셋을 찾을 수 없음: dataset_id=%d", dataset_id)
        return None
    owner = Owner(
        dataset_id=dataset_id,
        owner_name=req.owner_name,
        owner_type=req.owner_type.value,
    )
    session.add(owner)
    await session.commit()
    await session.refresh(owner)
    logger.info("소유자 '%s' (%s) 를 데이터셋 %d 에 추가", req.owner_name, req.owner_type.value, dataset_id)
    return OwnerResponse.model_validate(owner)


async def remove_dataset_owner(session: AsyncSession, owner_id: int, dataset_id: int | None = None) -> bool:
    """데이터셋에서 소유자를 제거한다. ``dataset_id`` 를 주면 그 데이터셋의 소유자 행만 대상으로 한다."""
    stmt = select(Owner).where(Owner.id == owner_id)
    if dataset_id is not None:
        stmt = stmt.where(Owner.dataset_id == dataset_id)
    result = await session.execute(stmt)
    owner = result.scalars().first()
    if not owner:
        logger.debug("제거 대상 소유자 없음: owner_id=%d dataset_id=%s", owner_id, dataset_id)
        return False
    await session.delete(owner)
    await session.commit()
    logger.info("소유자 제거: id=%d", owner_id)
    return True


async def add_dataset_glossary_term(
    session: AsyncSession, dataset_id: int, term_id: int
) -> bool:
    """데이터셋에 용어집 용어를 연결한다. 데이터셋이 없으면 False.

    용어가 없으면 404, 분류(CATEGORY)면 400 (``GlossaryError``). 이미 연결돼 있으면
    아무것도 하지 않는다(멱등) — 연타·재시도로 같은 연결이 중복 생성되지 않게.
    """
    result = await session.execute(select(Dataset.id).where(Dataset.id == dataset_id))
    if result.scalar() is None:
        logger.warning("용어집 용어 추가 대상 데이터셋을 찾을 수 없음: dataset_id=%d", dataset_id)
        return False
    term_type = (await session.execute(
        select(GlossaryTerm.term_type).where(GlossaryTerm.id == term_id)
    )).scalar()
    if term_type is None:
        raise GlossaryError("용어를 찾을 수 없습니다.", 404)
    if term_type != "TERM":
        raise GlossaryError("분류는 데이터셋에 연결할 수 없습니다. 용어를 선택하세요.")
    exists = (await session.execute(
        select(DatasetGlossaryTerm.id).where(
            DatasetGlossaryTerm.dataset_id == dataset_id,
            DatasetGlossaryTerm.term_id == term_id,
        ).limit(1)
    )).scalar()
    if exists is not None:
        return True
    session.add(DatasetGlossaryTerm(dataset_id=dataset_id, term_id=term_id))
    await session.commit()
    logger.info("용어집 용어 %d 를 데이터셋 %d 에 추가", term_id, dataset_id)
    return True


async def remove_dataset_glossary_term(
    session: AsyncSession, dataset_id: int, term_id: int
) -> bool:
    """데이터셋에서 용어집 용어 연결을 제거한다. 과거에 생긴 중복 연결도 함께 지운다."""
    result = await session.execute(
        select(DatasetGlossaryTerm)
        .where(DatasetGlossaryTerm.dataset_id == dataset_id,
               DatasetGlossaryTerm.term_id == term_id)
    )
    rows = result.scalars().all()
    if not rows:
        return False
    for dgt in rows:
        await session.delete(dgt)
    await session.commit()
    logger.info("용어집 용어 %d 를 데이터셋 %d 에서 제거 (연결 %d건)", term_id, dataset_id, len(rows))
    return True


# 동기화가 알 수 없는(사용자·AI 가 채우는) 컬럼 속성은 빈 값이 오면 기존 값을 유지한다.
_SCHEMA_KEEP_IF_EMPTY = ("display_name", "description")
_SCHEMA_SYNC_ATTRS = (
    "field_type", "native_type", "nullable", "is_primary_key", "is_unique", "is_indexed",
    "is_partition_key", "is_distribution_key", "ordinal",
)


async def apply_schema_fields(
    session: AsyncSession,
    dataset_id: int,
    existing: list[DatasetSchema],
    fields: list[dict],
) -> list[DatasetSchema]:
    """스키마 필드를 ``field_path`` 기준으로 diff-upsert 한다(전체 삭제 후 재삽입 대신).

    - 유지되는 컬럼은 행을 그대로 두고 속성만 갱신 → 행 id 가 바뀌지 않아 표준 용어 매핑
      (``catalog_term_column_mapping.schema_id``, ON DELETE CASCADE)과 ``pii_type`` 등
      동기화 대상이 아닌 값이 보존된다.
    - ``display_name``/``description`` 은 빈 값이 오면 기존 값 유지(원본 코멘트가 없을 때
      사용자 입력을 지우지 않음). 값이 있으면 원본 값으로 갱신.
    - 그 밖의 동기화 속성은 dict 에 키가 있을 때만 갱신(백엔드 내장 sync 처럼 파티션/분산 키를
      모르는 호출자가 기존 값을 지우지 않도록).
    - 사라진 컬럼만 삭제, 새 컬럼만 추가.
    """
    by_path: dict[str, DatasetSchema] = {}
    for f in existing:
        if f.field_path in by_path:
            await session.delete(f)  # 과거 중복 행 정리
        else:
            by_path[f.field_path] = f

    result: list[DatasetSchema] = []
    seen: set[str] = set()
    for idx, data in enumerate(fields):
        path = data["field_path"]
        if path in seen:
            continue
        seen.add(path)
        row = by_path.get(path)
        if row is None:
            row = DatasetSchema(
                dataset_id=dataset_id,
                field_path=path,
                display_name=data.get("display_name") or None,
                field_type=data["field_type"],
                native_type=data.get("native_type"),
                description=data.get("description") or None,
                nullable=data.get("nullable", "true"),
                is_primary_key=data.get("is_primary_key", "false"),
                is_unique=data.get("is_unique", "false"),
                is_indexed=data.get("is_indexed", "false"),
                is_partition_key=data.get("is_partition_key", "false"),
                is_distribution_key=data.get("is_distribution_key", "false"),
                ordinal=data.get("ordinal", idx),
            )
            session.add(row)
        else:
            for attr in _SCHEMA_KEEP_IF_EMPTY:
                value = data.get(attr)
                if value:
                    setattr(row, attr, value)
            for attr in _SCHEMA_SYNC_ATTRS:
                if attr in data and data[attr] is not None:
                    setattr(row, attr, data[attr])
        result.append(row)

    for path, row in by_path.items():
        if path not in seen:
            await session.delete(row)
    return result


async def update_schema_fields(
    session: AsyncSession, dataset_id: int, fields: list[dict]
) -> list[SchemaFieldResponse]:
    """데이터셋의 스키마 필드 전체를 교체한다(요청에 없는 필드는 삭제).

    교체 전에 변경 이력 스냅샷을 남기고(실패해도 진행), field_path 기준 diff-upsert 로
    행 id 를 보존한다. 커밋 후 재임베딩을 요청한다.
    """
    from types import SimpleNamespace

    # 갱신 전에 기존 fields 를 가져와 변경 이력 스냅샷을 만든다. metadata-sync 어댑터가
    # 호출하는 경로라 dict payload 가 들어오는데, ``save_schema_snapshot`` 의 from_sync=True
    # 모드는 ``col.name`` 같이 attribute 접근을 요구하므로 SimpleNamespace 로 변환해 넘긴다.
    existing_result = await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id)
    )
    old_fields = list(existing_result.scalars().all())
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
        for idx, f in enumerate(fields)
    ]
    try:
        await save_schema_snapshot(
            session, dataset_id, old_fields, new_columns_obj, from_sync=True,
        )
    except Exception as e:
        # snapshot 실패가 schema 갱신 자체를 막아서는 안 된다 (예: schema_snapshots 권한 이슈).
        logger.warning("스키마 스냅샷 저장 실패 dataset_id=%d: %s", dataset_id, e)

    # 전체 삭제 후 재삽입 대신 field_path 기준 diff-upsert — 표준 용어 매핑·pii_type·
    # 사용자 설명이 재동기화마다 사라지지 않도록.
    new_fields = await apply_schema_fields(session, dataset_id, old_fields, fields)

    await session.commit()
    logger.info("스키마 필드 교체: dataset_id=%d before=%d after=%d", dataset_id, len(old_fields), len(new_fields))
    for f in new_fields:
        await session.refresh(f)
    # 스키마 변경 → 검색용 임베딩 source_text 도 바뀌어야 한다. 백그라운드로 재임베딩.
    from app.embedding.service import embed_dataset_background
    await embed_dataset_background(dataset_id)
    return [SchemaFieldResponse.model_validate(f) for f in new_fields]


# 카탈로그가 직접 관리하는 내부 속성 키 접두어(argus.standard_dictionary_id, argus.avro_schema 등)
INTERNAL_PROPERTY_PREFIX = "argus."


async def update_dataset_properties(
    session: AsyncSession, dataset_id: int, properties: list[dict],
) -> list[DatasetProperty]:
    """데이터셋의 properties 를 전체 교체.

    metadata-sync 가 datasource-specific 메타(예: Iceberg snapshot id) 를 매번
    덮어쓰기 위해 사용. ``properties`` 는 ``[{key, value}, ...]`` 형식.

    단, 카탈로그 내부 키(``argus.`` 접두어 — 표준 사전 선택·Avro 편집본 등)는 payload 에
    없으면 기존 값을 유지한다(동기화가 모르는 키라 매번 지워지던 문제). 내부 키를 지우려면
    같은 키를 빈 값으로 보낸다.
    """
    from sqlalchemy import delete as sql_delete

    payload: list[tuple[str, str]] = []
    seen: set[str] = set()
    for p in properties:
        key = p.get("key") or p.get("property_key")
        value = p.get("value") if "value" in p else p.get("property_value")
        if not key or key in seen:
            continue
        seen.add(key)
        payload.append((key, "" if value is None else str(value)))

    # payload 에 없는 내부 키는 보존 대상
    existing = (await session.execute(
        select(DatasetProperty).where(DatasetProperty.dataset_id == dataset_id)
    )).scalars().all()
    preserved = [
        (row.property_key, row.property_value) for row in existing
        if row.property_key.startswith(INTERNAL_PROPERTY_PREFIX) and row.property_key not in seen
    ]

    # 1) 기존 행을 먼저 ``DELETE`` 로 즉시 제거하고 flush. ``session.delete`` 만 큐잉하면
    #    SQLAlchemy 가 INSERT 를 먼저 보내 unique 제약(dataset_id, property_key) 을
    #    위반할 수 있다.
    await session.execute(
        sql_delete(DatasetProperty).where(DatasetProperty.dataset_id == dataset_id)
    )
    await session.flush()

    new_props: list[DatasetProperty] = []
    for key, value in payload + preserved:
        # 내부 키를 빈 값으로 보내면 삭제(예: Avro 편집본 초기화)
        if key.startswith(INTERNAL_PROPERTY_PREFIX) and value == "":
            continue
        row = DatasetProperty(
            dataset_id=dataset_id,
            property_key=key,
            property_value=value,
        )
        session.add(row)
        new_props.append(row)

    await session.commit()
    for p in new_props:
        await session.refresh(p)
    logger.info("데이터셋 속성 교체: dataset_id=%d count=%d preserved_internal=%d",
                dataset_id, len(new_props), len(preserved))
    return new_props


# ---------------------------------------------------------------------------
# 대시보드 / 통계
# ---------------------------------------------------------------------------

def _utc_hour_bucket(session: AsyncSession, col):
    """created_at 을 UTC 기준 '시' 단위로 자르는 식 — DB 세션 시간대에 영향받지 않는다.

    PostgreSQL 은 ``timezone('UTC', ts)`` 로 UTC 벽시계로 바꾼 뒤 date_trunc,
    SQLite(단위 테스트)는 저장된 UTC 문자열을 strftime 으로 자른다.
    """
    if session.get_bind().dialect.name == "sqlite":
        return func.strftime("%Y-%m-%d %H:00:00", col)
    return func.date_trunc("hour", func.timezone("UTC", col))


def _as_utc(v):
    """버킷 값(naive datetime 또는 SQLite 문자열)을 UTC aware datetime 으로."""
    from datetime import datetime, timezone

    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc)


async def get_catalog_stats(session: AsyncSession, tz: str | None = None) -> CatalogStats:
    """대시보드 통계.

    ``tz`` 는 브라우저의 IANA 시간대(예: Asia/Seoul) — 7일·30일 추이를 그 시간대의
    날짜로 묶는다. 없거나 알 수 없는 값이면 UTC.
    """
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from sqlalchemy import text

    try:
        zone = ZoneInfo(tz) if tz else timezone.utc
    except (ValueError, KeyError, OSError):
        # ZoneInfoNotFoundError 는 KeyError 하위 — 잘못된 이름은 UTC 로 대체
        logger.warning("알 수 없는 시간대 — UTC 로 대체: tz=%s", tz)
        zone = timezone.utc

    active_filter = Dataset.status != "removed"

    total_datasets = (await session.execute(
        select(func.count()).select_from(Dataset).where(active_filter)
    )).scalar() or 0

    total_datasources = (await session.execute(
        select(func.count()).select_from(Datasource)
    )).scalar() or 0

    total_tags = (await session.execute(
        select(func.count()).select_from(Tag)
    )).scalar() or 0

    # 용어집 카드는 용어(TERM)만 — 분류(CATEGORY, 폴더) 행은 제외
    total_glossary_terms = (await session.execute(
        select(func.count()).select_from(GlossaryTerm)
        .where(GlossaryTerm.term_type == "TERM")
    )).scalar() or 0

    # 소유자 카드는 서로 다른 소유자 수 — 지정 행 수가 아니며 removed 데이터셋의 소유자는 제외
    total_owners = (await session.execute(
        select(func.count(func.distinct(Owner.owner_name)))
        .select_from(Owner)
        .join(Dataset, Dataset.id == Owner.dataset_id)
        .where(active_filter)
    )).scalar() or 0

    synced_datasets = (await session.execute(
        select(func.count()).select_from(Dataset)
        .where(active_filter, Dataset.is_synced == "true")
    )).scalar() or 0

    # 데이터 소스별 데이터셋 수 (막대 차트용) — 이름은 중복될 수 있어 id 로 묶고 이름은 라벨로만 쓴다
    result = await session.execute(
        select(Datasource.name, func.count(Dataset.id))
        .join(Dataset, Dataset.datasource_id == Datasource.id)
        .where(active_filter)
        .group_by(Datasource.id, Datasource.name)
        .order_by(func.count(Dataset.id).desc())
    )
    datasets_by_datasource = [
        {"datasource": name, "count": count} for name, count in result.all()
    ]

    # 데이터 소스 유형별 데이터셋 수 (도넛 차트용)
    result = await session.execute(
        select(Datasource.type, func.count(Dataset.id))
        .join(Dataset, Dataset.datasource_id == Datasource.id)
        .where(active_filter)
        .group_by(Datasource.type)
        .order_by(func.count(Dataset.id).desc())
    )
    datasets_by_datasource_type = [
        {"type": ptype, "count": count} for ptype, count in result.all()
    ]

    # origin 별 데이터셋 수 (도넛 차트용)
    result = await session.execute(
        select(Dataset.origin, func.count(Dataset.id))
        .where(active_filter)
        .group_by(Dataset.origin)
    )
    datasets_by_origin = [
        {"origin": origin, "count": count} for origin, count in result.all()
    ]

    # 데이터 소스별 스키마 필드 수 (막대 차트) — id 로 묶음(동명 데이터 소스 병합 방지)
    result = await session.execute(
        select(Datasource.name, func.count(DatasetSchema.id))
        .join(Dataset, Dataset.datasource_id == Datasource.id)
        .join(DatasetSchema, DatasetSchema.dataset_id == Dataset.id)
        .where(active_filter)
        .group_by(Datasource.id, Datasource.name)
        .order_by(func.count(DatasetSchema.id).desc())
        .limit(10)
    )
    schema_fields_by_datasource = [
        {"datasource": name, "count": count} for name, count in result.all()
    ]

    # 태그가 가장 많은 데이터셋 상위 목록 — id 로 묶음(다른 데이터 소스의 같은 테이블 이름 병합 방지)
    result = await session.execute(
        select(Dataset.name, func.count(DatasetTag.id).label("tag_count"))
        .join(DatasetTag, DatasetTag.dataset_id == Dataset.id)
        .where(active_filter)
        .group_by(Dataset.id, Dataset.name)
        .order_by(text("tag_count DESC"))
        .limit(10)
    )
    top_tagged_datasets = [
        {"name": name, "count": count} for name, count in result.all()
    ]

    # 신규 데이터셋 추이 — DB 에서는 UTC '시' 버킷으로만 집계하고(세션 시간대 무관),
    # 날짜 묶음은 요청한 시간대(tz)로 파이썬에서 한다. (30분 단위 오프셋 시간대는 경계가 최대 30분 어긋날 수 있음)
    now = datetime.now(timezone.utc)
    bucket = _utc_hour_bucket(session, Dataset.created_at).label("bucket")
    result = await session.execute(
        select(bucket, func.count().label("count"))
        .where(active_filter, Dataset.created_at >= now - timedelta(days=31))
        # 별칭으로 묶는다 — 식을 다시 쓰면 바인드 파라미터('hour','UTC')가 달라 PG 가 같은 식으로 보지 않음
        .group_by(text("bucket")).order_by(text("bucket"))
    )
    hourly = [(_as_utc(r.bucket), r.count) for r in result.all() if r.bucket is not None]

    # 시간별 (24시간) — 현재 시 포함 정확히 24개 버킷(창 시작·끝이 같은 시각 라벨로 겹치지 않음).
    # 라벨은 UTC ISO 시각 — 화면이 브라우저 로컬 시각으로 표시한다.
    start_1d = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    daily_datasets_1d = [
        {"date": h.isoformat(), "count": c} for h, c in hourly if h >= start_1d
    ]

    # 일별 (7일 / 30일) — 요청 시간대의 오늘 포함 N일
    today = now.astimezone(zone).date()
    by_day: dict = {}
    for h, c in hourly:
        d = h.astimezone(zone).date()
        by_day[d] = by_day.get(d, 0) + c
    daily_datasets_7d = [
        {"date": d.isoformat(), "count": c}
        for d, c in sorted(by_day.items()) if d >= today - timedelta(days=6)
    ]
    daily_datasets_30d = [
        {"date": d.isoformat(), "count": c}
        for d, c in sorted(by_day.items()) if d >= today - timedelta(days=29)
    ]

    # 최근 데이터셋
    recent = await list_datasets(session, page=1, page_size=10)

    return CatalogStats(
        total_datasets=total_datasets,
        total_datasources=total_datasources,
        total_tags=total_tags,
        total_glossary_terms=total_glossary_terms,
        total_owners=total_owners,
        synced_datasets=synced_datasets,
        datasets_by_datasource=datasets_by_datasource,
        datasets_by_origin=datasets_by_origin,
        datasets_by_datasource_type=datasets_by_datasource_type,
        schema_fields_by_datasource=schema_fields_by_datasource,
        top_tagged_datasets=top_tagged_datasets,
        daily_datasets_1d=daily_datasets_1d,
        daily_datasets_7d=daily_datasets_7d,
        daily_datasets_30d=daily_datasets_30d,
        recent_datasets=recent.items,
    )


# ---------------------------------------------------------------------------
# 스키마 이력
# ---------------------------------------------------------------------------

def _schema_field_to_dict(f) -> dict:
    """DatasetSchema ORM 객체를 비교 가능한 dict 로 변환한다."""
    return {
        "field_type": f.field_type,
        "native_type": f.native_type or "",
        "nullable": f.nullable or "true",
        "is_primary_key": f.is_primary_key or "false",
        "is_unique": f.is_unique or "false",
        "is_indexed": f.is_indexed or "false",
    }


def _schema_field_to_snapshot(f) -> dict:
    """DatasetSchema ORM 객체를 전체 스냅샷 항목으로 변환한다."""
    return {
        "field_path": f.field_path,
        "field_type": f.field_type,
        "native_type": f.native_type or "",
        "nullable": f.nullable or "true",
        "is_primary_key": f.is_primary_key or "false",
        "is_unique": f.is_unique or "false",
        "is_indexed": f.is_indexed or "false",
        "ordinal": f.ordinal,
    }


def _col_info_to_dict(col) -> dict:
    """동기화 ColumnInfo 를 비교 가능한 dict 로 변환한다."""
    return {
        "field_type": col.data_type,
        "native_type": col.native_type or "",
        "nullable": "true" if col.nullable else "false",
        "is_primary_key": "true" if col.is_primary_key else "false",
        "is_unique": "true" if col.is_unique else "false",
        "is_indexed": "true" if col.is_indexed else "false",
    }


def _col_info_to_snapshot(col) -> dict:
    """동기화 ColumnInfo 를 전체 스냅샷 항목으로 변환한다."""
    return {
        "field_path": col.name,
        "field_type": col.data_type,
        "native_type": col.native_type or "",
        "nullable": "true" if col.nullable else "false",
        "is_primary_key": "true" if col.is_primary_key else "false",
        "is_unique": "true" if col.is_unique else "false",
        "is_indexed": "true" if col.is_indexed else "false",
        "ordinal": col.ordinal,
    }


def detect_schema_changes(
    old_fields: list, new_columns: list, from_sync: bool = False,
) -> list[dict]:
    """기존 스키마 필드와 새 컬럼 간의 ADD/MODIFY/DROP 변경을 감지한다.

    Args:
        old_fields: DatasetSchema ORM 객체 목록(기존)
        new_columns: DatasetSchema ORM 객체 또는 동기화 ColumnInfo 객체 목록
        from_sync: True 면 new_columns 가 동기화에서 온 ColumnInfo 객체

    Returns:
        변경 dict 목록: [{type, field, before, after}, ...]
    """
    if from_sync:
        old_map = {f.field_path: _schema_field_to_dict(f) for f in old_fields}
        new_map = {c.name: _col_info_to_dict(c) for c in new_columns}
    else:
        old_map = {f.field_path: _schema_field_to_dict(f) for f in old_fields}
        new_map = {f.field_path: _schema_field_to_dict(f) for f in new_columns}

    changes = []
    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    # 추가됨
    for key in sorted(new_keys - old_keys):
        changes.append({"type": "ADD", "field": key, "before": None, "after": new_map[key]})

    # 삭제됨
    for key in sorted(old_keys - new_keys):
        changes.append({"type": "DROP", "field": key, "before": old_map[key], "after": None})

    # 변경됨
    for key in sorted(old_keys & new_keys):
        old_val = old_map[key]
        new_val = new_map[key]
        if old_val != new_val:
            # 변경된 속성만 기록
            before_diff = {k: v for k, v in old_val.items() if v != new_val.get(k)}
            after_diff = {k: v for k, v in new_val.items() if v != old_val.get(k)}
            changes.append({
                "type": "MODIFY", "field": key,
                "before": before_diff, "after": after_diff,
            })

    return changes


async def save_schema_snapshot(
    session: AsyncSession,
    dataset_id: int,
    old_fields: list,
    new_columns: list,
    from_sync: bool = False,
) -> SchemaSnapshot | None:
    """기존 스키마와 새 스키마를 비교해 변경이 있으면 스냅샷을 저장한다.

    저장된 스냅샷을 반환하며, 변경이 없으면 None.
    """
    import json as _json

    changes = detect_schema_changes(old_fields, new_columns, from_sync=from_sync)

    # 이 데이터셋의 첫 스냅샷인지 확인
    existing_snapshots = await session.execute(
        select(func.count()).where(SchemaSnapshot.dataset_id == dataset_id)
    )
    has_prior_snapshot = (existing_snapshots.scalar() or 0) > 0

    # 변경이 감지되었거나, 이전 스냅샷이 없는 경우(최초 기준점) 저장
    if not changes and has_prior_snapshot:
        logger.debug("스키마 변경 없음 — 스냅샷 생략: dataset_id=%d", dataset_id)
        return None

    # 새 컬럼에서 스냅샷 JSON 구성
    if from_sync:
        schema_entries = [_col_info_to_snapshot(c) for c in new_columns]
    else:
        schema_entries = [_schema_field_to_snapshot(f) for f in new_columns]

    # Build change summary (한글 — UI 의 변경 이력 tab 에 그대로 노출되는 값).
    added = sum(1 for c in changes if c["type"] == "ADD")
    modified = sum(1 for c in changes if c["type"] == "MODIFY")
    dropped = sum(1 for c in changes if c["type"] == "DROP")
    parts = []
    if added:
        parts.append(f"추가 {added}개")
    if modified:
        parts.append(f"변경 {modified}개")
    if dropped:
        parts.append(f"삭제 {dropped}개")
    summary = ", ".join(parts) if parts else ("최초 동기화" if not has_prior_snapshot else "변경 없음")

    snapshot = SchemaSnapshot(
        dataset_id=dataset_id,
        schema_json=_json.dumps(schema_entries, ensure_ascii=False),
        field_count=len(schema_entries),
        change_summary=summary,
        changes_json=_json.dumps(changes, ensure_ascii=False) if changes else "[]",
    )
    session.add(snapshot)
    await session.flush()

    # 스키마 변경을 분석해 리니지 알림 생성
    if changes:
        try:
            from app.alert.service import evaluate_rules_and_create_alerts
            # savepoint — 알림 평가가 실패해도 스냅샷 저장 트랜잭션은 살린다
            async with session.begin_nested():
                alerts = await evaluate_rules_and_create_alerts(session, dataset_id, changes)
            if alerts:
                logger.info("dataset_id=%d 에 대해 리니지 알림 %d건 생성", dataset_id, len(alerts))
        except Exception as e:
            logger.warning("dataset_id=%d 의 리니지 알림 생성 실패: %s", dataset_id, e)

    # 데이터셋당 최신 20개 스냅샷만 유지
    max_snapshots = 20
    count_result = await session.execute(
        select(func.count()).where(SchemaSnapshot.dataset_id == dataset_id)
    )
    total = count_result.scalar() or 0
    if total > max_snapshots:
        old_snaps = await session.execute(
            select(SchemaSnapshot)
            .where(SchemaSnapshot.dataset_id == dataset_id)
            .order_by(SchemaSnapshot.synced_at.asc())
            .limit(total - max_snapshots)
        )
        for old in old_snaps.scalars().all():
            await session.delete(old)
        logger.info("dataset_id=%d 의 오래된 스냅샷 %d개 정리", dataset_id, total - max_snapshots)

    logger.info("스키마 스냅샷 저장: dataset_id=%d, %s, fields=%d",
                dataset_id, summary, len(schema_entries))
    return snapshot


async def get_schema_history(
    session: AsyncSession,
    dataset_id: int,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedSchemaSnapshots:
    """데이터셋의 스키마 변경 이력을 최신순으로 페이지 조회한다(데이터셋이 없어도 빈 목록)."""
    import json as _json

    base = select(SchemaSnapshot).where(SchemaSnapshot.dataset_id == dataset_id)

    count_query = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    offset = (page - 1) * page_size
    query = base.order_by(SchemaSnapshot.synced_at.desc()).offset(offset).limit(page_size)
    result = await session.execute(query)

    items = []
    for snap in result.scalars().all():
        changes_raw = _json.loads(snap.changes_json) if snap.changes_json else []
        changes = [SchemaChangeEntry(**c) for c in changes_raw]
        items.append(SchemaSnapshotResponse(
            id=snap.id,
            dataset_id=snap.dataset_id,
            synced_at=snap.synced_at,
            field_count=snap.field_count,
            change_summary=snap.change_summary,
            changes=changes,
        ))

    return PaginatedSchemaSnapshots(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# 시드 데이터
# ---------------------------------------------------------------------------

async def seed_datasource_metadata(session: AsyncSession) -> None:
    """모든 데이터 소스에 유형별 메타데이터(데이터 타입, 테이블 타입, 저장 포맷, 기능)를 시드한다.

    이미 시드된 데이터 소스는 건너뛴다(멱등). 예전에는 유형별 첫 데이터 소스에만 시드했다.
    """
    datasources = (await session.execute(select(Datasource))).scalars().all()
    for datasource in datasources:
        await _seed_metadata_for(session, datasource)
    await session.commit()


async def _seed_metadata_for(session: AsyncSession, datasource: Datasource) -> bool:
    """데이터 소스 하나에 유형별 메타데이터를 추가한다(커밋하지 않음). 추가했으면 True."""
    from app.catalog.datasource_metadata import DATASOURCE_METADATA

    meta = DATASOURCE_METADATA.get(datasource.type)
    if not meta:
        return False
    # 이미 시드되었는지 확인(4개 테이블 중 하나라도)
    for model_cls in (DatasourceDataType, DatasourceTableType, DatasourceStorageFormat, DatasourceFeature):
        cnt = (await session.execute(
            select(func.count()).where(model_cls.datasource_id == datasource.id)
        )).scalar() or 0
        if cnt > 0:
            return False

    pid = datasource.id

    for idx, (type_name, cat, desc) in enumerate(meta.get("data_types", [])):
        session.add(DatasourceDataType(
            datasource_id=pid, type_name=type_name, type_category=cat,
            description=desc, ordinal=idx,
        ))

    for idx, (type_name, display, desc, is_def) in enumerate(meta.get("table_types", [])):
        session.add(DatasourceTableType(
            datasource_id=pid, type_name=type_name, display_name=display,
            description=desc, is_default=is_def, ordinal=idx,
        ))

    for idx, (fmt, display, desc, is_def) in enumerate(meta.get("storage_formats", [])):
        session.add(DatasourceStorageFormat(
            datasource_id=pid, format_name=fmt, display_name=display,
            description=desc, is_default=is_def, ordinal=idx,
        ))

    for idx, (key, display, desc, vtype, is_req) in enumerate(meta.get("features", [])):
        session.add(DatasourceFeature(
            datasource_id=pid, feature_key=key, display_name=display,
            description=desc, value_type=vtype, is_required=is_req, ordinal=idx,
        ))

    logger.info("데이터 소스 메타데이터 시드 완료: %s (id=%d)", datasource.type, pid)
    return True


# ---------------------------------------------------------------------------
# 데이터 소스 메타데이터 조회
# ---------------------------------------------------------------------------

async def get_datasource_metadata(
    session: AsyncSession, datasource_id: int
) -> DatasourceMetadataResponse | None:
    """데이터 소스의 유형별 메타데이터(ordinal 순)를 조회한다. 데이터 소스가 없으면 None."""
    result = await session.execute(select(Datasource).where(Datasource.id == datasource_id))
    datasource = result.scalars().first()
    if not datasource:
        return None

    data_types = (await session.execute(
        select(DatasourceDataType)
        .where(DatasourceDataType.datasource_id == datasource_id)
        .order_by(DatasourceDataType.ordinal)
    )).scalars().all()

    table_types = (await session.execute(
        select(DatasourceTableType)
        .where(DatasourceTableType.datasource_id == datasource_id)
        .order_by(DatasourceTableType.ordinal)
    )).scalars().all()

    storage_formats = (await session.execute(
        select(DatasourceStorageFormat)
        .where(DatasourceStorageFormat.datasource_id == datasource_id)
        .order_by(DatasourceStorageFormat.ordinal)
    )).scalars().all()

    features = (await session.execute(
        select(DatasourceFeature)
        .where(DatasourceFeature.datasource_id == datasource_id)
        .order_by(DatasourceFeature.ordinal)
    )).scalars().all()

    return DatasourceMetadataResponse(
        datasource=await _datasource_response(session, datasource),
        data_types=[DatasourceDataTypeResponse.model_validate(t) for t in data_types],
        table_types=[DatasourceTableTypeResponse.model_validate(t) for t in table_types],
        storage_formats=[DatasourceStorageFormatResponse.model_validate(f) for f in storage_formats],
        features=[DatasourceFeatureResponse.model_validate(f) for f in features],
    )


# ---------------------------------------------------------------------------
# 데이터 파이프라인 CRUD
# ---------------------------------------------------------------------------

async def create_pipeline(session: AsyncSession, data: PipelineCreate) -> PipelineResponse:
    """파이프라인을 추가하고 flush 한다(커밋은 호출 측)."""
    pipeline = DataPipeline(
        pipeline_name=data.pipeline_name,
        description=data.description,
        pipeline_type=data.pipeline_type.value,
        schedule=data.schedule,
        owner=data.owner,
    )
    session.add(pipeline)
    await session.flush()
    await session.refresh(pipeline)
    logger.info("파이프라인 생성: id=%d, name=%s, type=%s", pipeline.id, pipeline.pipeline_name, pipeline.pipeline_type)
    return PipelineResponse.model_validate(pipeline)


async def list_pipelines(session: AsyncSession) -> list[PipelineResponse]:
    """파이프라인 전체(이름순)."""
    result = await session.execute(
        select(DataPipeline).order_by(DataPipeline.pipeline_name)
    )
    return [PipelineResponse.model_validate(p) for p in result.scalars().all()]


async def get_pipeline(session: AsyncSession, pipeline_id: int) -> DataPipeline | None:
    """파이프라인 ORM 객체. 없으면 None."""
    result = await session.execute(
        select(DataPipeline).where(DataPipeline.id == pipeline_id)
    )
    return result.scalar_one_or_none()


async def update_pipeline(
    session: AsyncSession, pipeline_id: int, data: PipelineUpdate,
) -> PipelineResponse | None:
    """보낸 필드만 반영하고 flush 한다(커밋은 호출 측). 없으면 None."""
    pipeline = await get_pipeline(session, pipeline_id)
    if not pipeline:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "pipeline_type" and value is not None:
            value = value.value if hasattr(value, "value") else value
        if field == "status" and value is not None:
            value = value.value if hasattr(value, "value") else value
        setattr(pipeline, field, value)
    await session.flush()
    await session.refresh(pipeline)
    logger.info("파이프라인 수정: id=%d", pipeline_id)
    return PipelineResponse.model_validate(pipeline)


async def delete_pipeline(session: AsyncSession, pipeline_id: int) -> bool:
    """파이프라인 삭제(flush 만). 참조하던 리니지의 pipeline_id 는 FK SET NULL. 없으면 False."""
    pipeline = await get_pipeline(session, pipeline_id)
    if not pipeline:
        return False
    await session.delete(pipeline)
    await session.flush()
    logger.info("파이프라인 삭제: id=%d", pipeline_id)
    return True


# ---------------------------------------------------------------------------
# 타 데이터 소스 간 데이터셋 리니지
# ---------------------------------------------------------------------------

async def create_dataset_lineage(
    session: AsyncSession, data: DatasetLineageCreate,
) -> DatasetLineageResponse:
    """데이터셋 리니지와 컬럼 매핑을 추가하고 flush 한다(커밋은 호출 측)."""
    lineage = DatasetLineage(
        source_dataset_id=data.source_dataset_id,
        target_dataset_id=data.target_dataset_id,
        relation_type=data.relation_type.value,
        lineage_source=data.lineage_source.value,
        pipeline_id=data.pipeline_id,
        description=data.description,
        created_by=data.created_by,
    )
    session.add(lineage)
    await session.flush()
    await session.refresh(lineage)

    # 컬럼 매핑 생성
    for cm in data.column_mappings:
        mapping = DatasetColumnMapping(
            dataset_lineage_id=lineage.id,
            source_column=cm.source_column,
            target_column=cm.target_column,
            transform_type=cm.transform_type,
            transform_expr=cm.transform_expr,
        )
        session.add(mapping)
    await session.flush()

    logger.info("데이터셋 리니지 생성: id=%d, source=%d, target=%d, type=%s, mappings=%d",
                lineage.id, data.source_dataset_id, data.target_dataset_id,
                data.relation_type.value, len(data.column_mappings))
    return await _build_lineage_response(session, lineage.id)


async def list_dataset_lineages(
    session: AsyncSession,
    dataset_id: int | None = None,
    lineage_source: str | None = None,
) -> list[DatasetLineageResponse]:
    """데이터셋 리니지 목록(최신순). ``dataset_id`` 는 원본·대상 어느 쪽이든 매칭한다."""
    stmt = select(DatasetLineage)
    if dataset_id is not None:
        stmt = stmt.where(
            or_(
                DatasetLineage.source_dataset_id == dataset_id,
                DatasetLineage.target_dataset_id == dataset_id,
            )
        )
    if lineage_source is not None:
        stmt = stmt.where(DatasetLineage.lineage_source == lineage_source)
    stmt = stmt.order_by(DatasetLineage.created_at.desc())
    result = await session.execute(stmt)
    lineages = result.scalars().all()
    return [await _build_lineage_response(session, l.id) for l in lineages]


async def get_dataset_lineage(session: AsyncSession, lineage_id: int) -> DatasetLineage | None:
    """리니지 ORM 객체. 없으면 None."""
    result = await session.execute(
        select(DatasetLineage).where(DatasetLineage.id == lineage_id)
    )
    return result.scalar_one_or_none()


async def replace_fk_lineage_for_target(
    session: AsyncSession,
    target_dataset_id: int,
    entries: list,
) -> dict:
    """target dataset 의 ``lineage_source=FK`` lineage 를 통째 교체.

    metadata-sync 어댑터가 FK 제약을 발견할 때마다 호출. 다른 출처 (MANUAL /
    PIPELINE / QUERY_AGGREGATED) 의 lineage 는 건드리지 않는다.

    ``entries`` 의 각 항목:
      - source_urn: 참조 대상 dataset URN (부모)
      - columns: [{local, referenced}, ...]
      - description: optional

    Returns:
      {"created": N, "skipped_missing_source": M, "deleted": K}
      — M 은 부모 데이터셋이 아직 없는 항목과 자기참조 FK 항목을 합한 수.
    커밋은 하지 않는다(flush 만, 호출 측이 커밋).
    """
    from sqlalchemy import delete as sql_delete

    # 1) target 의 기존 FK lineage 제거 (column_mappings 는 FK CASCADE)
    del_result = await session.execute(
        sql_delete(DatasetLineage).where(
            DatasetLineage.target_dataset_id == target_dataset_id,
            DatasetLineage.lineage_source == "FK",
        )
    )
    deleted = del_result.rowcount or 0

    # target 의 schema (column → native_type) — 이 dataset 만 한 번 fetch.
    target_fields = await _column_native_type_map(session, target_dataset_id)

    # 2) 새 entries 로 재생성. source_urn → source_dataset_id lookup.
    created = 0
    skipped = 0
    for entry in entries:
        result = await session.execute(
            select(Dataset.id).where(Dataset.urn == entry.source_urn)
        )
        source_id = result.scalar_one_or_none()
        if source_id is None:
            # 부모 dataset 이 아직 sync 안 됨 — 다음 sync 때 다시 시도되도록 skip.
            skipped += 1
            continue
        if source_id == target_dataset_id:
            # 자기참조 FK 는 의미 약함 + 그래프 시각화 noise. skip.
            skipped += 1
            continue
        source_fields = await _column_native_type_map(session, source_id)

        lineage = DatasetLineage(
            source_dataset_id=source_id,
            target_dataset_id=target_dataset_id,
            relation_type="READ_WRITE",
            lineage_source="FK",
            description=entry.description,
        )
        session.add(lineage)
        await session.flush()
        for cp in entry.columns:
            src_type = source_fields.get(cp.referenced) or ""
            tgt_type = target_fields.get(cp.local) or ""
            # native_type 이 둘 다 알려져 있고 다르면 CAST, 그 외에는 DIRECT 로 분류.
            # 한 쪽이라도 모르는 상태에서 단순 문자열 비교만으로 CAST 로 단정짓는 건
            # noise 가 될 수 있어 DIRECT 로 유지.
            if src_type and tgt_type and src_type.strip().lower() != tgt_type.strip().lower():
                transform_type = "CAST"
                transform_expr = f"CAST({cp.local} AS {src_type})"
            else:
                transform_type = "DIRECT"
                transform_expr = None
            session.add(DatasetColumnMapping(
                dataset_lineage_id=lineage.id,
                source_column=cp.referenced,
                target_column=cp.local,
                transform_type=transform_type,
                transform_expr=transform_expr,
            ))
        created += 1

    await session.flush()
    logger.info("FK 리니지 교체: target_dataset_id=%d created=%d skipped=%d deleted=%d",
                target_dataset_id, created, skipped, deleted)
    return {"created": created, "skipped_missing_source": skipped, "deleted": deleted}


async def _column_native_type_map(
    session: AsyncSession, dataset_id: int,
) -> dict[str, str]:
    """dataset 의 ``field_path → native_type`` 매핑. transform_type 추론에 사용."""
    result = await session.execute(
        select(DatasetSchema.field_path, DatasetSchema.native_type)
        .where(DatasetSchema.dataset_id == dataset_id)
    )
    return {row[0]: (row[1] or "") for row in result.all()}


async def delete_dataset_lineage(session: AsyncSession, lineage_id: int) -> bool:
    """리니지 삭제(컬럼 매핑은 CASCADE, flush 만). 없으면 False."""
    lineage = await get_dataset_lineage(session, lineage_id)
    if not lineage:
        return False
    await session.delete(lineage)
    await session.flush()
    logger.info("데이터셋 리니지 삭제: id=%d", lineage_id)
    return True


async def update_dataset_lineage_column_mappings(
    session: AsyncSession,
    lineage_id: int,
    column_mappings: list[ColumnMappingCreate],
) -> DatasetLineageResponse | None:
    """리니지의 컬럼 매핑을 통째 교체한다(flush 만). 리니지가 없으면 None."""
    lineage = await get_dataset_lineage(session, lineage_id)
    if not lineage:
        return None

    # 기존 매핑 삭제
    existing = await session.execute(
        select(DatasetColumnMapping).where(
            DatasetColumnMapping.dataset_lineage_id == lineage_id
        )
    )
    for m in existing.scalars().all():
        await session.delete(m)

    # 새 매핑 생성
    for cm in column_mappings:
        mapping = DatasetColumnMapping(
            dataset_lineage_id=lineage_id,
            source_column=cm.source_column,
            target_column=cm.target_column,
            transform_type=cm.transform_type,
            transform_expr=cm.transform_expr,
        )
        session.add(mapping)
    await session.flush()
    logger.info("리니지 컬럼 매핑 교체: lineage_id=%d mappings=%d", lineage_id, len(column_mappings))

    return await _build_lineage_response(session, lineage_id)


async def _build_lineage_response(
    session: AsyncSession, lineage_id: int,
) -> DatasetLineageResponse:
    """리니지 응답 구성 — 양쪽 데이터셋·데이터 소스 이름, 파이프라인 이름, 컬럼 매핑 포함.

    호출 측이 리니지 존재를 보장해야 한다(없으면 AttributeError).
    """
    lineage = await get_dataset_lineage(session, lineage_id)

    # 소스 데이터셋 정보 조회 (중첩 데이터 소스를 포함한 DatasetResponse 반환)
    src = await get_dataset(session, lineage.source_dataset_id)
    tgt = await get_dataset(session, lineage.target_dataset_id)

    # 파이프라인 이름 조회
    pipeline_name = None
    if lineage.pipeline_id:
        pipeline = await get_pipeline(session, lineage.pipeline_id)
        if pipeline:
            pipeline_name = pipeline.pipeline_name

    # 컬럼 매핑 조회
    mappings_result = await session.execute(
        select(DatasetColumnMapping).where(
            DatasetColumnMapping.dataset_lineage_id == lineage_id
        )
    )
    column_mappings = [
        ColumnMappingResponse.model_validate(m) for m in mappings_result.scalars().all()
    ]

    return DatasetLineageResponse(
        id=lineage.id,
        source_dataset_id=lineage.source_dataset_id,
        target_dataset_id=lineage.target_dataset_id,
        source_dataset_name=src.name if src else None,
        target_dataset_name=tgt.name if tgt else None,
        source_datasource_type=src.datasource.type if src else None,
        target_datasource_type=tgt.datasource.type if tgt else None,
        source_datasource_name=src.datasource.name if src else None,
        target_datasource_name=tgt.datasource.name if tgt else None,
        relation_type=lineage.relation_type,
        lineage_source=lineage.lineage_source,
        pipeline_id=lineage.pipeline_id,
        pipeline_name=pipeline_name,
        description=lineage.description,
        created_by=lineage.created_by,
        query_count=lineage.query_count,
        last_seen_at=lineage.last_seen_at,
        created_at=lineage.created_at,
        column_mappings=column_mappings,
    )
