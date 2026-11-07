# SPDX-License-Identifier: Apache-2.0
"""모델 스토어 라우터 — S3 기반 모델 아티팩트 관리 API.

``/api/v1/model-store`` 경로에서 다음을 제공한다:
  - 모델 파일 업로드 (직접 / presigned URL)
  - 모델 파일 다운로드 (presigned URL)
  - 버전 파일 리스팅, OCI manifest 조회, 버전 확정(manifest 생성 + DB 갱신)
  - 버킷 브라우저(목록·서버 경유 스트리밍 다운로드·presigned URL)
  - HuggingFace Hub 임포트 (``import/huggingface``, 관리자)
  - 에어갭 환경용 로컬 디렉토리 임포트 (``import/local``, 관리자)

저장소 연결 실패는 503(``_STORAGE_DOWN`` 안내), 그 밖의 저장소 오류는 500 으로 바꾼다.

로깅 정책: 업로드/다운로드/임포트는 INFO 로 모델·버전·파일 수를 기록.
400 / 403 / 404 / 409 는 WARNING 으로 식별자와 함께 남긴다.
"""

import json
import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import download_link
from app.core.auth import AdminUser, CurrentUser, OptionalUser, TokenUser
from app.core.config import settings
from app.core.database import get_session
from app.models import model_store, service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/model-store", tags=["model-store"])


async def _require_browse_scope(current: TokenUser, session: AsyncSession, path: str) -> None:
    """저장소 탐색 범위 — 관리자는 버킷 전체, 그 밖의 사용자는 OCI 모델 한 개의 경로 안에서만.

    버킷 전체 탐색(설정 › OCI 모델 파일)은 관리자 전용 메뉴다. 일반 사용자는 OCI 모델 상세의 파일
    탭에서 ``/<모델 이름>/v<버전>/…`` 만 보므로, 첫 경로가 등록된 OCI 모델인 경우만 허용한다.
    OCI 모델 허브 메뉴를 막은 역할은 이 경로도 쓸 수 없다.
    """
    if current.is_admin:
        return
    from sqlalchemy import select

    from app.oci_hub.models import OciModel
    from app.permissions.router import check_menu

    # 루트("/")·상대 경로 조각은 버킷 전체 탐색으로 보고 거부한다
    parts = [p for p in (path or "").split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        logger.warning("저장소 탐색 거부(관리자 아님, 범위 밖): path=%s user=%s", path, current.username)
        raise HTTPException(status_code=403, detail="저장소 전체 탐색은 관리자만 할 수 있습니다.")
    if not await check_menu(session, current, "oci-hub"):
        logger.warning("저장소 탐색 거부(oci-hub 메뉴 권한 없음): user=%s", current.username)
        raise HTTPException(status_code=403, detail="메뉴 접근 권한이 없습니다: oci-hub")
    found = (await session.execute(
        # storage_prefix 는 "<이름>/" (등록·반입) 또는 끝 "/" 없이 저장된 예전 데이터가 있다
        select(OciModel.id).where(OciModel.storage_prefix.in_((parts[0], f"{parts[0]}/")))
    )).first()
    if not found:
        logger.warning("저장소 탐색 거부(OCI 모델 경로 아님): path=%s user=%s", path, current.username)
        raise HTTPException(status_code=403, detail="OCI 모델 경로만 탐색할 수 있습니다.")


# ---------------------------------------------------------------------------
# 요청/응답 스키마
# ---------------------------------------------------------------------------

class UploadUrlRequest(BaseModel):
    filename: str = Field(..., description="Name of the file to upload")


class UploadUrlResponse(BaseModel):
    url: str
    key: str
    expires_in: int


class DownloadUrlResponse(BaseModel):
    url: str
    key: str
    expires_in: int


class FileInfo(BaseModel):
    filename: str
    key: str
    size: int
    last_modified: str = ""


class FinalizeRequest(BaseModel):
    annotations: dict[str, str] | None = None


class FinalizeResponse(BaseModel):
    status: str
    file_count: int
    total_size: int
    manifest: dict | None = None


class HuggingFaceImportRequest(BaseModel):
    hf_model_id: str = Field(..., description="HuggingFace model ID (e.g. 'bert-base-uncased')")
    model_name: str = Field(..., description="Target model name (e.g. 'argus.ml.bert')")
    revision: str = Field("main", description="HuggingFace revision/branch")
    description: str | None = None
    owner: str | None = None


class LocalImportRequest(BaseModel):
    local_dir: str = Field(..., description="Path to local directory containing model files")
    model_name: str = Field(..., description="Target model name")
    description: str | None = None
    owner: str | None = None
    source: str = Field("local", description="Source label")


class ImportResponse(BaseModel):
    model_name: str
    version: int
    file_count: int
    total_size: int
    storage_location: str


# ---------------------------------------------------------------------------
# 업로드 엔드포인트
# ---------------------------------------------------------------------------

async def _assert_store_writer(current: TokenUser, session: AsyncSession, model_name: str) -> None:
    """model-store 쓰기(업로드·확정) 가드 — 대상 모델이 등록돼 있어야 하고(OCI 허브 또는 MLflow),
    관리자 또는 그 모델의 생성자·소유자만 허용한다. 등록되지 않은 이름에 파일이 쌓이는 것을 막는다."""
    from sqlalchemy import select

    from app.models.models import RegisteredModel
    from app.oci_hub.models import OciModel

    oci = (await session.execute(select(OciModel).where(OciModel.name == model_name))).scalars().first()
    reg = None if oci else (await session.execute(select(RegisteredModel).where(
        RegisteredModel.name == model_name, RegisteredModel.status != "deleted",
    ))).scalars().first()
    target = oci or reg
    if target is None:
        logger.warning("model-store 쓰기 거부(등록되지 않은 모델): %s", model_name)
        raise HTTPException(
            status_code=404,
            detail=f"'{model_name}' 은(는) 등록된 모델이 아닙니다. OCI 허브(POST /api/v1/oci-models) 또는 "
                   "MLflow 모델(POST /api/v1/models) 로 먼저 등록하세요.",
        )
    if current.is_admin or current.username in {target.created_by, target.owner} - {None, ""}:
        return
    logger.warning("model-store 쓰기 거부(권한 없음): %s user=%s", model_name, current.username)
    raise HTTPException(status_code=403, detail="모델의 소유자 또는 관리자만 파일을 올리거나 확정할 수 있습니다.")


@router.post("/{model_name}/versions/{version}/upload")
async def upload_file(current: CurrentUser,
    model_name: str,
    version: int,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
):
    """모델 버전에 단일 파일을 업로드 (multipart/form-data). 모델 소유자·생성자·관리자.

    파일 내용을 메모리에 모두 읽어 올린다. 잘못된 파일 이름 400, 저장소 장애 503."""
    await _assert_store_writer(current, session, model_name)
    try:
        data = await file.read()
        filename = file.filename or "uploaded_file"
        result = await model_store.upload_file(model_name, version, filename, data)
        return result
    except ValueError as e:
        logger.warning("업로드 거부(잘못된 파일 이름): %s v%d: %s", model_name, version, e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("업로드 오류: %s", e)
        raise _s3_http_error(e) from e


@router.post("/{model_name}/versions/{version}/upload-url", response_model=UploadUrlResponse)
async def get_upload_url(current: CurrentUser,
    model_name: str,
    version: int,
    body: UploadUrlRequest,
    session: AsyncSession = Depends(get_session),
):
    """클라이언트 → S3 직접 업로드용 presigned PUT URL 발급. 모델 소유자·생성자·관리자."""
    await _assert_store_writer(current, session, model_name)
    try:
        return await model_store.generate_upload_url(model_name, version, body.filename)
    except ValueError as e:
        logger.warning("업로드 URL 거부(잘못된 파일 이름): %s v%d: %s", model_name, version, e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("업로드 URL 오류: %s", e)
        raise _s3_http_error(e) from e


# ---------------------------------------------------------------------------
# 다운로드 엔드포인트
# ---------------------------------------------------------------------------

@router.get("/{model_name}/versions/{version}/download-url", response_model=DownloadUrlResponse)
async def get_download_url(_user: CurrentUser,
    model_name: str,
    version: int,
    filename: str = Query(..., description="File to download"),
    request: Request = None,
    session: AsyncSession = Depends(get_session),
):
    """단일 파일에 대한 presigned download URL 발급 (로그인 필요).

    파일이 실제로 있을 때만 URL 을 주고 다운로드 횟수를 센다 — 없는 파일 404, 잘못된 이름 400."""
    try:
        result = await model_store.generate_download_url(model_name, version, filename)
    except ValueError as e:
        logger.warning("다운로드 URL 거부(잘못된 파일 이름): %s v%d: %s", model_name, version, e)
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        logger.warning("다운로드 URL 실패(파일 없음): %s v%d file=%s", model_name, version, filename)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("다운로드 URL 오류: %s", e)
        raise _s3_http_error(e) from e
    try:
        client_ip = request.client.host if request and request.client else None
        user_agent = request.headers.get("user-agent") if request else None

        from app.models.download_log import log_download
        await log_download(session, model_name, version, "download", client_ip, user_agent)

        # 해당되는 경우 OCI 다운로드 로그에도 기록
        from app.oci_hub.download_log import log_oci_download
        await log_oci_download(session, model_name, version, "download", client_ip, user_agent)
        return result
    except Exception as e:
        logger.error("다운로드 URL 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{model_name}/versions/{version}/download-urls")
async def get_download_urls(_user: CurrentUser,
    model_name: str,
    version: int,
    request: Request = None,
    session: AsyncSession = Depends(get_session),
):
    """버전 내 모든 파일에 대해 presigned download URL 을 일괄 발급 (로그인 필요). 파일이 없으면 404."""
    try:
        urls = await model_store.generate_download_urls(model_name, version)
    except Exception as e:
        logger.error("다운로드 URL 일괄 오류: %s", e)
        raise _s3_http_error(e) from e
    if not urls:
        logger.warning("다운로드 URL 일괄 발급 실패(파일 없음): %s v%d", model_name, version)
        raise HTTPException(status_code=404, detail=f"{model_name} v{version} 의 파일이 저장소에 없습니다.")
    try:
        client_ip = request.client.host if request and request.client else None
        user_agent = request.headers.get("user-agent") if request else None

        from app.models.download_log import log_download
        await log_download(session, model_name, version, "pull", client_ip, user_agent)

        # 해당되는 경우 OCI 다운로드 로그 기록 + 카운터 증가
        from app.oci_hub.download_log import log_oci_download
        await log_oci_download(session, model_name, version, "pull", client_ip, user_agent)
        logger.info("모델 pull URL 발급: %s v%d files=%d", model_name, version, len(urls))
        return {"files": urls}
    except Exception as e:
        logger.error("다운로드 URL 일괄 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# S3 버킷 브라우저 (OCI 모델 파일 페이지용)
# ---------------------------------------------------------------------------

@router.get("/browse/list")
async def browse_s3_directory(_user: CurrentUser,
    path: str = Query("/", description="Directory path in S3 bucket"),
    session: AsyncSession = Depends(get_session),
):
    """S3 객체/폴더를 디렉토리처럼 리스팅 (관리자는 버킷 전체, 그 밖에는 OCI 모델 경로만)."""
    await _require_browse_scope(_user, session, path)
    try:
        return await model_store.list_s3_directory(prefix=path.lstrip("/"))
    except Exception as e:
        logger.error("S3 탐색 오류: %s", e)
        raise _s3_http_error(e) from e


_STORAGE_DOWN = "오브젝트 스토리지에 연결할 수 없습니다. MinIO 실행 여부와 설정을 확인하세요."


def _s3_http_error(e: Exception) -> HTTPException:
    """저장소 호출 실패 → 연결 불가면 503(안내 문구), 그 밖은 500."""
    if _storage_unavailable(e):
        return HTTPException(status_code=503, detail=_STORAGE_DOWN)
    return HTTPException(status_code=500, detail=str(e))


def _storage_unavailable(e: Exception) -> bool:
    """예외 메시지로 저장소 연결 불가 여부를 추정한다(연결·엔드포인트·타임아웃·MinIO 기본 포트 9000)."""
    msg = str(e).lower()
    return any(k in msg for k in ("connect", "endpoint", "timed out", "9000"))


async def _head_or_http(path: str) -> dict:
    """S3 객체 존재·크기 확인 — 없음 404, 저장소 장애 503, 그 밖 500."""
    try:
        return await model_store.head_s3_object(path)
    except FileNotFoundError as e:
        logger.warning("S3 객체 없음: path=%s", path)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error("S3 객체 확인 오류: %s", e)
        if _storage_unavailable(e):
            raise HTTPException(status_code=503, detail=_STORAGE_DOWN)
        raise _s3_http_error(e) from e


async def _import_failed(session: AsyncSession, model_name: str, version: int | None, what: str,
                         e: Exception) -> HTTPException:
    """반입 도중 실패 — 만들어 둔 버전을 FAILED_REGISTRATION 으로 남기고 알맞은 HTTP 오류를 돌려준다.

    예전에는 버전이 PENDING_REGISTRATION 으로 남아 '진행 중'처럼 보였다. 저장소 장애는 503.
    """
    logger.error("%s 오류 (%s v%s): %s", what, model_name, version, e)
    down = _storage_unavailable(e)
    if version is not None:
        from app.models.schemas import ModelVersionFinalize, ModelVersionStatus
        try:
            await session.rollback()
            await service.finalize_model_version(session, model_name, version, ModelVersionFinalize(
                status=ModelVersionStatus.FAILED_REGISTRATION,
                status_message=(_STORAGE_DOWN if down else f"{what} 실패: {e}")[:1000],
            ))
        except Exception as fe:  # noqa: BLE001 — 원래 오류를 돌려주는 것이 우선
            logger.warning("실패 상태 기록 못 함 (%s v%s): %s", model_name, version, fe)
    if down:
        return HTTPException(status_code=503, detail=_STORAGE_DOWN)
    return HTTPException(status_code=500, detail=f"{what} 실패: {e}")


def _check_local_dir(local_dir: str) -> None:
    """로컬 반입 대상 확인 — 모델·버전을 만들기 전에 해서, 잘못된 경로가 빈 버전을 남기지 않게 한다."""
    path = Path(local_dir)
    if not path.is_dir():
        logger.warning("로컬 반입 거부(디렉터리 없음): %s", local_dir)
        raise HTTPException(status_code=400,
                            detail=f"디렉터리가 없습니다(카탈로그 서버 기준 경로): {local_dir}")
    # 숨김 파일(최상위 조각이 "." 으로 시작)이 아닌 파일이 하나라도 있으면 통과
    for f in path.rglob("*"):
        if f.is_file() and not str(f.relative_to(path)).startswith("."):
            return
    logger.warning("로컬 반입 거부(가져올 파일 없음): %s", local_dir)
    raise HTTPException(status_code=400, detail=f"가져올 파일이 없습니다: {local_dir}")


@router.post("/browse/download-link")
async def browse_s3_download_link(user: CurrentUser,
    path: str = Query(..., description="File path in S3 bucket"),
    session: AsyncSession = Depends(get_session),
):
    """브라우저가 링크로 바로 내려받을 수 있는 짧은 토큰(로그인 필요).

    ``GET /browse/file?path=…&token=…`` 로 쓴다. 파일은 서버가 저장소에서 읽어 그대로 흘려보내므로
    저장소 주소가 브라우저에서 닿지 않아도(예: ``http://minio:9000``) 내려받을 수 있다."""
    await _require_browse_scope(user, session, path)
    await _head_or_http(path)
    logger.info("저장소 파일 다운로드 토큰 발급: path=%s user=%s", path, user.username)
    return {"token": download_link.issue("s3", path, user.sub), "expires_in": download_link.DEFAULT_TTL_SECONDS}


@router.get("/browse/file")
async def browse_s3_file(user: OptionalUser,
    path: str = Query(..., description="File path in S3 bucket"),
    token: str | None = Query(None, description="POST /browse/download-link 로 받은 다운로드 토큰"),
    inline: bool = Query(False, description="true 면 브라우저 안에서 열기(inline)"),
    session: AsyncSession = Depends(get_session),
):
    """S3 객체를 서버가 읽어 스트리밍으로 내려준다(로그인 또는 이 파일용 다운로드 토큰 필요).

    파일 브라우저·OCI 상세 파일 탭의 보기·다운로드용. presigned URL 과 달리 저장소 주소에 의존하지 않는다."""
    if user is None:
        if not token:
            logger.warning("저장소 파일 다운로드 거부(인증 없음): path=%s", path)
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        download_link.verify(token, "s3", path)  # 발급 단계에서 범위를 확인했다
    else:
        await _require_browse_scope(user, session, path)
    head = await _head_or_http(path)
    logger.debug("저장소 파일 스트리밍: path=%s size=%s inline=%s", path, head["size"], inline)
    name = head["key"].rsplit("/", 1)[-1]
    media_type = mimetypes.guess_type(name)[0] or head.get("content_type") or "application/octet-stream"
    return StreamingResponse(
        model_store.iter_s3_object(path), media_type=media_type,
        headers={"Content-Disposition": download_link.content_disposition(name, inline),
                 "Content-Length": str(head["size"])},
    )


@router.get("/browse/download")
async def browse_s3_download(_user: CurrentUser,
    path: str = Query(..., description="File path in S3 bucket"),
    session: AsyncSession = Depends(get_session),
):
    """경로(S3 key) 로 임의 객체의 presigned download URL 발급 (파일 브라우저용, 로그인 필요).

    버킷 전체를 경로로 지정할 수 있어 모델 파일 자산이 그대로 노출되므로 익명 접근을 막는다.
    서명은 저장소에 묻지 않고 만들어지므로 먼저 파일을 확인한다 — 없으면 404, 저장소 장애면 503."""
    await _require_browse_scope(_user, session, path)
    await _head_or_http(path)
    try:
        url = await model_store.generate_s3_download_url(path)
        return {"url": url}
    except Exception as e:
        logger.error("S3 다운로드 URL 오류: %s", e)
        raise _s3_http_error(e) from e


# ---------------------------------------------------------------------------
# 목록 / 매니페스트
# ---------------------------------------------------------------------------

@router.get("/{model_name}/versions/{version}/files", response_model=list[FileInfo])
async def list_version_files(
    _user: CurrentUser,
    model_name: str,
    version: int,
):
    """모델 버전의 전체 파일을 S3 에서 메타데이터와 함께 나열(로그인 사용자)."""
    try:
        return await model_store.list_files(model_name, version)
    except Exception as e:
        logger.error("파일 목록 조회 오류: %s", e)
        raise _s3_http_error(e) from e


@router.get("/{model_name}/versions/{version}/manifest")
async def get_manifest(
    _user: CurrentUser,
    model_name: str,
    version: int,
):
    """버전의 OCI ``manifest.json`` 을 조회(로그인 사용자). 없으면 404, 저장소에 닿지 않으면 503."""
    try:
        data = await model_store.download_file(model_name, version, "manifest.json")
        return json.loads(data)
    except Exception as e:
        if _storage_unavailable(e):  # 장애를 '없음' 으로 착각하지 않게
            logger.error("manifest 조회 중 저장소 장애 %s v%d: %s", model_name, version, e)
            raise HTTPException(status_code=503, detail=_STORAGE_DOWN) from e
        logger.warning("%s v%d 의 manifest 를 찾을 수 없음: %s", model_name, version, e)
        raise HTTPException(status_code=404, detail="manifest을(를) 찾을 수 없습니다.")


# ---------------------------------------------------------------------------
# 확정 (manifest 생성 + DB 갱신)
# ---------------------------------------------------------------------------

@router.delete("/{model_name}/versions/{version}/files")
async def delete_unfinalized_files(current: CurrentUser,
    model_name: str,
    version: int,
    session: AsyncSession = Depends(get_session),
):
    """확정되지 않은 버전의 저장소 파일을 지운다 — 끊긴 push·업로드가 남긴 파일 정리. 모델 소유자·생성자·관리자.

    그 번호의 버전 기록이 있으면(확정됨·가져오는 중·MLflow 등록 대기 등) 409 — 그런 버전은 모델 화면·버전 삭제 API 로 지운다.
    지울 파일이 없으면 ``deleted`` 0."""
    from sqlalchemy import select

    from app.models.models import ModelVersion, RegisteredModel
    from app.oci_hub.models import OciModel, OciModelVersion

    await _assert_store_writer(current, session, model_name)
    oci_ver = (await session.execute(
        select(OciModelVersion.status).join(OciModel, OciModel.id == OciModelVersion.model_id)
        .where(OciModel.name == model_name, OciModelVersion.version == version)
    )).scalars().first()
    mlflow_ver = (await session.execute(
        select(ModelVersion.status).join(RegisteredModel, RegisteredModel.id == ModelVersion.model_id)
        .where(RegisteredModel.name == model_name, ModelVersion.version == version)
    )).scalars().first()
    status = oci_ver or mlflow_ver
    if status is not None:
        logger.warning("확정 안 된 파일 삭제 거부: %s v%d 는 버전 기록이 있음(%s)", model_name, version, status)
        raise HTTPException(
            status_code=409,
            detail=f"{model_name} v{version} 은(는) 등록된 버전({status})이라 파일만 지울 수 없습니다 — 버전을 삭제하면 파일도 함께 지워집니다(가져오는 중이면 끝난 뒤).",
        )
    try:
        deleted = await model_store.delete_version_files(model_name, version)
    except Exception as e:
        logger.error("확정 안 된 파일 삭제 오류 %s v%d: %s", model_name, version, e)
        raise _s3_http_error(e) from e
    logger.info("확정 안 된 파일 삭제: %s v%d (%d개) by %s", model_name, version, deleted, current.username)
    return {"model_name": model_name, "version": version, "deleted": deleted}


@router.post("/{model_name}/versions/{version}/finalize", response_model=FinalizeResponse)
async def finalize_version(current: CurrentUser,
    model_name: str,
    version: int,
    body: FinalizeRequest | None = None,
    session: AsyncSession = Depends(get_session),
):
    """버전 확정 흐름: S3 파일 스캔 → manifest 생성 → DB 갱신(버전 READY·파일 수·크기·catalog_models).

    대상은 MLflow 등록 모델(``/api/v1/models``)의 **이미 만든 버전**이다 — 모델·버전이 없으면 404.
    OCI 허브 모델은 ``/api/v1/oci-models/{name}/versions/{v}/finalize`` 를 쓴다.
    """
    # DB 대상 먼저 확인 — 없는 모델/버전에 manifest 만 만들고 READY 라고 답하던 문제 방지
    model = await service.get_registered_model_by_name(session, model_name)
    if model:
        await _assert_store_writer(current, session, model_name)
    if not model:
        logger.warning("model-store 확정 실패(MLflow 모델 없음): %s", model_name)
        raise HTTPException(
            status_code=404,
            detail=f"등록된 MLflow 모델 '{model_name}' 이(가) 없습니다. "
                   "OCI 허브 모델은 /api/v1/oci-models/{name}/versions/{v}/finalize 를 사용하세요.",
        )
    existing = await service.get_model_version(session, model_name, version)
    if not existing:
        logger.warning("model-store 확정 실패(버전 없음): %s v%d", model_name, version)
        raise HTTPException(
            status_code=404,
            detail=f"모델 '{model_name}' 의 버전 {version} 이(가) 없습니다. 먼저 버전을 생성하세요.",
        )
    # 실패로 마감된 버전은 manifest 를 만들기 "전에" 거부 — 예전에는 manifest.json 을 저장소에 써 둔 뒤 409 였다
    if existing.status == "FAILED_REGISTRATION":
        logger.warning("model-store 확정 거부(FAILED 버전): %s v%d", model_name, version)
        raise HTTPException(
            status_code=409,
            detail=f"버전 {version} 은(는) 실패(FAILED_REGISTRATION)로 마감되어 확정할 수 없습니다.",
        )

    try:
        files = [f for f in await model_store.list_files(model_name, version) if f["filename"] != "manifest.json"]
    except Exception as e:
        logger.error("확정 오류(S3 조회): %s v%d: %s", model_name, version, e)
        raise _s3_http_error(e) from e  # 연결 불가면 503(공통 안내 문구), 그 밖은 500
    if not files:
        logger.warning("model-store 확정 거부(업로드된 파일 없음): %s v%d", model_name, version)
        raise HTTPException(status_code=400, detail="이 버전에 해당하는 파일을 찾을 수 없습니다.")
    file_count, total_size = len(files), sum(f["size"] for f in files)

    try:
        annotations = body.annotations if body else None
        manifest = await model_store.generate_manifest(model_name, version, annotations)
        # MLflow 메타 파일 원본 — 있으면 catalog_models 파싱(로컬 확정과 동일)
        names = {f["filename"] for f in files}
        meta_files: dict[str, bytes] = {}
        for fname in ("MLmodel", "requirements.txt", "conda.yaml", "python_env.yaml"):
            if fname in names:
                meta_files[fname] = await model_store.download_file(model_name, version, fname)
    except Exception as e:
        logger.error("확정 오류(manifest): %s v%d: %s", model_name, version, e)
        raise _s3_http_error(e) from e

    try:
        ver = await service.finalize_store_version(
            session, model_name, version,
            file_count=file_count, total_size=total_size,
            manifest=manifest, meta_files=meta_files,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    logger.info("model-store 확정: %s v%d (files=%d, size=%d)", model_name, version, file_count, total_size)
    return FinalizeResponse(
        status=ver.status,
        file_count=file_count,
        total_size=total_size,
        manifest=manifest,
    )


# ---------------------------------------------------------------------------
# 임포트: HuggingFace
# ---------------------------------------------------------------------------

@router.post("/import/huggingface", response_model=ImportResponse)
async def import_huggingface(current: AdminUser,
    body: HuggingFaceImportRequest,
    session: AsyncSession = Depends(get_session),
):
    """HuggingFace Hub 에서 모델을 가져와 S3 에 업로드 (import-hf 흐름).

    모델을 다운로드해 S3 에 저장하고 DB 레코드를 생성한다. 저장소·revision 을 먼저 확인해 없으면 404/400,
    HF 에 닿지 않으면 502 로 바로 알린다(모델·버전을 만들지 않음).

    요청 안에서 동기적으로 다운로드·업로드하므로 대용량 모델은 오래 걸린다(OCI 허브의 HF 임포트는 백그라운드).
    같은 이름의 모델이 있으면 그 모델에 새 버전을 추가하고, 모델을 S3 저장소 모델로 바꾼다.
    중간 실패 시 만든 버전은 FAILED_REGISTRATION 으로 남긴다.
    """
    logger.info("model-store HF 임포트 요청: hf=%s -> %s by %s", body.hf_model_id, body.model_name, current.username)
    from app.oci_hub import service as oci_service

    try:
        await oci_service._check_hf_repo(body.hf_model_id, body.revision)
    except oci_service.OciUpstreamError as e:
        logger.warning("HuggingFace 임포트 거부 (hf=%s): %s", body.hf_model_id, e)
        raise HTTPException(status_code=e.status_code, detail=str(e))

    version: int | None = None
    try:
        # 등록 모델 생성 또는 조회
        from app.models.schemas import RegisteredModelCreate
        try:
            model = await service.create_registered_model(
                session,
                RegisteredModelCreate(
                    name=body.model_name,
                    description=body.description,
                    owner=body.owner,
                ),
                created_by=current.username,
            )
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False))
        except ValueError:
            # 이미 존재하는 모델 — 조회 (소프트 삭제된 모델은 조회되지 않아 404)
            model = await service.get_registered_model_by_name(session, body.model_name)
            if not model:
                raise HTTPException(status_code=404, detail=f"모델 '{body.model_name}'을(를) 찾을 수 없습니다.")

        # storage_type 을 s3 로 갱신
        from sqlalchemy import update

        from app.models.models import RegisteredModel
        await session.execute(
            update(RegisteredModel).where(
                RegisteredModel.name == body.model_name
            ).values(
                storage_type="s3",
                bucket_name=settings.os_bucket,
                storage_location=f"s3://{settings.os_bucket}/{body.model_name}",
            )
        )

        # 버전 생성
        from app.models.schemas import ModelVersionCreate
        version_resp = await service.create_model_version(
            session,
            ModelVersionCreate(model_name=body.model_name),
        )
        version = version_resp.version

        # HuggingFace 에서 임포트
        metadata = await model_store.import_from_huggingface(
            hf_model_id=body.hf_model_id,
            model_name=body.model_name,
            version=version,
            revision=body.revision,
        )

        # 버전을 READY 로 갱신
        from app.models.schemas import ModelVersionFinalize, ModelVersionStatus
        await service.finalize_model_version(
            session, body.model_name, version,
            ModelVersionFinalize(status=ModelVersionStatus.READY),
        )
        # S3 버전의 위치·파일 수·크기 기록 (finalize_model_version 은 로컬 디렉터리만 센다)
        await service.record_store_artifacts(
            session, body.model_name, version, metadata["file_count"], metadata["total_size"],
        )

        # catalog_models 메타데이터 저장
        import json as _json

        from app.models.models import CatalogModel
        cm = CatalogModel(
            model_version_id=version_resp.id,
            model_name=body.model_name,
            version=version,
            source_type="huggingface",
            manifest=_json.dumps(metadata.get("manifest")),
        )
        # HF config 메타데이터 — 전용 컬럼이 없어 기존 컬럼에 담는다(model_type → serialization_format).
        # transformers_version 은 MLflow 버전이 아니므로 mlflow_version 이 아닌 대표 flavor
        # (transformers) 의 버전으로 저장한다 — 화면의 "프레임워크" 에 ``transformers 4.x`` 로 표시된다.
        if metadata.get("model_type"):
            cm.serialization_format = metadata["model_type"]
        if metadata.get("transformers_version"):
            cm.flavor = "transformers"
            cm.flavor_version = str(metadata["transformers_version"])[:50]

        session.add(cm)
        await session.commit()

        storage_loc = f"s3://{settings.os_bucket}/{body.model_name}/v{version}/"
        logger.info("HuggingFace 임포트 완료: %s -> %s v%d", body.hf_model_id, body.model_name, version)

        return ImportResponse(
            model_name=body.model_name,
            version=version,
            file_count=metadata["file_count"],
            total_size=metadata["total_size"],
            storage_location=storage_loc,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise await _import_failed(session, body.model_name, version, "HuggingFace 반입", e) from e


# ---------------------------------------------------------------------------
# 임포트: 로컬 디렉토리 (에어갭)
# ---------------------------------------------------------------------------

@router.post("/import/local", response_model=ImportResponse)
async def import_local(current: AdminUser,
    body: LocalImportRequest,
    session: AsyncSession = Depends(get_session),
):
    """로컬 디렉토리(에어갭/사내 망) 에서 모델 파일을 S3 로 임포트.

    에어갭 시나리오에서 사용한다: 먼저 USB/SCP 로 파일을 전송한 뒤,
    로컬 파일시스템에서 MinIO 로 임포트한다. 경로가 없거나 파일이 없으면 400(아무것도 만들지 않음).
    ``local_dir`` 은 카탈로그 서버 기준 경로이며 관리자만 호출할 수 있다.
    """
    logger.info("model-store 로컬 임포트 요청: dir=%s -> %s by %s", body.local_dir, body.model_name, current.username)
    _check_local_dir(body.local_dir)
    version: int | None = None
    try:
        # 등록 모델 생성 또는 조회
        from app.models.schemas import RegisteredModelCreate
        try:
            model = await service.create_registered_model(
                session,
                RegisteredModelCreate(
                    name=body.model_name,
                    description=body.description,
                    owner=body.owner,
                ),
                created_by=current.username,
            )
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False))
        except ValueError:
            # 이미 존재하는 모델 — 조회 (소프트 삭제된 모델은 조회되지 않아 404)
            model = await service.get_registered_model_by_name(session, body.model_name)
            if not model:
                raise HTTPException(status_code=404, detail=f"모델 '{body.model_name}'을(를) 찾을 수 없습니다.")

        # storage_type 을 s3 로 갱신
        from sqlalchemy import update

        from app.models.models import RegisteredModel
        await session.execute(
            update(RegisteredModel).where(
                RegisteredModel.name == body.model_name
            ).values(
                storage_type="s3",
                bucket_name=settings.os_bucket,
                storage_location=f"s3://{settings.os_bucket}/{body.model_name}",
            )
        )

        # 버전 생성
        from app.models.schemas import ModelVersionCreate
        version_resp = await service.create_model_version(
            session,
            ModelVersionCreate(model_name=body.model_name),
        )
        version = version_resp.version

        # 로컬에서 임포트
        metadata = await model_store.import_from_local_directory(
            local_dir=body.local_dir,
            model_name=body.model_name,
            version=version,
            source=body.source,
        )

        # 확정
        from app.models.schemas import ModelVersionFinalize, ModelVersionStatus
        await service.finalize_model_version(
            session, body.model_name, version,
            ModelVersionFinalize(status=ModelVersionStatus.READY),
        )
        # S3 버전의 위치·파일 수·크기 기록 (finalize_model_version 은 로컬 디렉터리만 센다)
        await service.record_store_artifacts(
            session, body.model_name, version, metadata["file_count"], metadata["total_size"],
        )

        # catalog_models 저장
        import json as _json

        from app.models.models import CatalogModel
        cm = CatalogModel(
            model_version_id=version_resp.id,
            model_name=body.model_name,
            version=version,
            source_type=body.source,
            manifest=_json.dumps(metadata.get("manifest")),
        )
        session.add(cm)
        await session.commit()

        storage_loc = f"s3://{settings.os_bucket}/{body.model_name}/v{version}/"
        logger.info("로컬 반입 완료(버전 READY): %s -> %s v%d (파일 %d개)",
                    body.local_dir, body.model_name, version, metadata["file_count"])

        return ImportResponse(
            model_name=body.model_name,
            version=version,
            file_count=metadata["file_count"],
            total_size=metadata["total_size"],
            storage_location=storage_loc,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise await _import_failed(session, body.model_name, version, "로컬 반입", e) from e
