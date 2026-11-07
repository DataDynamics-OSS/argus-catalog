# SPDX-License-Identifier: Apache-2.0
"""로컬 파일시스템 브라우저 라우터.

``/api/v1/filesystem`` 경로에서 다음을 제공한다:
  - 디렉토리 목록
  - 파일/디렉토리 생성·삭제·이름 변경
  - 파일 업로드 / 다운로드
  - 파일 미리보기 (parquet, xlsx, docx, pptx)
  - 파일 메타데이터(stat)

``root_sub`` 쿼리 파라미터로 브라우저를 ``data_dir`` 하위로 한정한다:
  - ``root_sub=model-artifacts`` → MLflow 모델 파일 브라우저(``/dashboard/mlflow-files``)
  - ``root_sub=oci-artifacts``   → OCI 모델 파일 브라우저(``/dashboard/oci-files``)
  - (없음)                       → 전체 data 디렉토리
  그 외 값은 거부한다(``service.ALLOWED_ROOT_SUBS``).

권한(OCI ``/model-store/browse/*`` 와 같은 규칙):
  - 관리자: 모든 root_sub 전체 조회·변경.
  - 그 밖의 로그인 사용자: ``root_sub=model-artifacts`` 에서 **삭제되지 않은 등록 MLflow 모델의 저장
    디렉터리 안**만 조회(목록·stat·미리보기·다운로드 링크·다운로드). 전체 탐색(``/``·상위 폴더),
    다른 root_sub, 소프트 삭제된 모델은 403. "MLflow 모델" 메뉴를 막은 역할도 403.
  - 변경(업로드·폴더 생성·삭제·이름 변경)은 관리자만.

등록 모델 보호: 삭제·이름 변경 대상이 등록된 MLflow 모델의 저장 디렉터리(그 자체·하위·상위)와
겹치면 HTTP 409 로 거부한다. 파일만 지우면 DB 의 버전은 READY 로 남아 정합성이 깨지므로,
모델 파일은 MLflow 모델 화면(모델/버전 삭제)에서 지워야 한다. 소프트 삭제된 모델도 복원할 수 있으므로
보호하며, 그 파일은 "삭제된 모델" 보기의 영구 삭제로만 지운다.

로깅 정책: 변경 동작(업로드 / 디렉토리 생성 / 삭제 / 이름 변경) 은 INFO 로
영향을 받은 경로·항목 수를 기록한다. 권한·경로 오류는 WARNING 으로,
예기치 않은 예외는 ERROR 로 남긴다. 디렉토리 listing / stat / preview /
download 는 조회 성격이라 INFO 를 생략한다.
"""

import logging
import mimetypes
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import download_link
from app.core.auth import AdminUser, CurrentUser, OptionalUser, TokenUser
from app.core.database import get_session
from app.filesystemmgr import service
from app.filesystemmgr.schemas import (
    CreateFolderRequest,
    CreateFolderResponse,
    DeleteRequest,
    DeleteResponse,
    DocumentPreviewResponse,
    FileStatResponse,
    ListDirectoryResponse,
    RenameRequest,
    RenameResponse,
    TablePreviewResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/filesystem", tags=["filesystem"])

MODEL_FILES_ROOT = "model-artifacts"


async def _require_read_scope(
    user: TokenUser, session: AsyncSession, path: str, root_sub: str | None,
) -> None:
    """조회 범위 — 관리자는 전체, 그 밖의 사용자는 등록된 MLflow 모델 한 개의 디렉터리 안에서만.

    MLflow 모델 파일 화면(전체 탐색)은 관리자 전용 메뉴다. 일반 사용자는 모델 상세의 "파일 보기"로
    ``/<모델 이름>/versions/<n>/…`` 만 보므로, 해석한 실제 경로가 삭제되지 않은 등록 모델의 저장
    디렉터리(그 자체·하위)인 경우만 허용한다. 경로 탈출(``..``·심볼릭 링크)은 해석 단계에서 403.
    """
    if user.is_admin:
        return
    from app.models.service import find_readable_model_for_path
    from app.permissions.router import check_menu

    if root_sub != MODEL_FILES_ROOT:
        raise HTTPException(status_code=403, detail="파일 탐색은 관리자만 할 수 있습니다.")
    if not await check_menu(session, user, "models"):
        raise HTTPException(status_code=403, detail="메뉴 접근 권한이 없습니다: models")
    try:
        resolved = service._resolve_path(path, root_sub)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not await find_readable_model_for_path(session, resolved):
        logger.warning("모델 파일 조회 범위 밖: user=%s path=%s", user.username, path)
        raise HTTPException(
            status_code=403,
            detail="등록된 MLflow 모델의 파일만 볼 수 있습니다(전체 탐색은 관리자 전용).",
        )


async def _guard_registered_model_paths(
    session: AsyncSession, paths: list[str], root_sub: str | None,
) -> None:
    """변경 대상 경로가 등록 모델의 저장소와 겹치면 409. 경로 탈출(ValueError)은 호출 측 처리에 맡긴다."""
    from app.models.service import find_registered_model_for_path

    for p in paths:
        resolved = service._resolve_path(p, root_sub)
        hit = await find_registered_model_for_path(session, resolved)
        if hit:
            model_name, is_parent = hit
            logger.warning("등록 모델 파일 변경 거부: %s (모델 %s, root_sub=%s)", p, model_name, root_sub)
            what = (
                f"'{p}' 아래에 등록된 모델 '{model_name}' 의 파일이 있습니다."
                if is_parent else f"'{p}' 는 등록된 모델 '{model_name}' 의 파일입니다."
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{what} 파일만 지우거나 옮기면 모델 버전과 실제 파일이 어긋나므로, "
                    "MLflow 모델 화면에서 모델·버전을 삭제하세요(이미 삭제한 모델은 '삭제된 모델'에서 영구 삭제)."
                ),
            )


# =========================================================================== #
# 디렉토리 목록
# =========================================================================== #


@router.get("/list", response_model=ListDirectoryResponse)
async def list_directory(
    user: CurrentUser,
    path: str = Query("/", description="Directory path"),
    root_sub: str | None = Query(None, description="Subdirectory of data_dir to use as root"),
    session: AsyncSession = Depends(get_session),
):
    """주어진 경로 아래의 파일/디렉토리를 나열한다(일반 사용자는 등록 모델 디렉터리 안만).

    없음 404, 디렉터리 아님 400, 조회 범위 밖·경로 탈출·허용되지 않은 ``root_sub`` 403.
    """
    logger.debug("디렉토리 목록 요청: path=%s root_sub=%s", path, root_sub)
    await _require_read_scope(user, session, path, root_sub)
    try:
        return await service.list_directory(path, root_sub=root_sub)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("디렉토리 목록 조회 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================== #
# 파일 메타데이터 (stat)
# =========================================================================== #


@router.get("/stat", response_model=FileStatResponse)
async def file_stat(
    user: CurrentUser,
    path: str = Query(..., description="File or directory path"),
    root_sub: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """파일 또는 디렉토리의 상세 메타데이터(크기·권한·시간 등). 없음 404, 조회 범위 밖·경로 위반 403."""
    logger.debug("파일 메타데이터 요청: path=%s root_sub=%s", path, root_sub)
    await _require_read_scope(user, session, path, root_sub)
    try:
        return await service.file_stat(path, root_sub=root_sub)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("파일 메타데이터 조회 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================== #
# 다운로드
# =========================================================================== #


def _fs_kind(root_sub: str | None) -> str:
    """다운로드 토큰의 용도(kind) 문자열 — ``root_sub`` 별로 달라 다른 루트의 같은 경로에 재사용되지 않는다."""
    return f"fs:{root_sub or ''}"


def _resolve_or_http(path: str, root_sub: str | None):
    """다운로드 대상 경로 확인 — 없음 404, 디렉터리 400, 경로 위반 403 으로 변환한다."""
    try:
        return service.resolve_download_file(path, root_sub=root_sub)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IsADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/download-link")
async def create_download_link(
    user: CurrentUser,
    path: str = Query(..., description="File path"),
    root_sub: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """브라우저가 링크로 바로 내려받을 수 있는 짧은 토큰을 발급한다(조회 범위 안의 파일만).

    ``GET /download?path=…&root_sub=…&token=…`` 로 쓰며, 토큰은 이 파일에만 유효하고 곧 만료된다."""
    await _require_read_scope(user, session, path, root_sub)
    _resolve_or_http(path, root_sub)  # 없는 파일·허용 안 된 경로는 발급 단계에서 알린다
    logger.info("파일 다운로드 토큰 발급: path=%s root_sub=%s user=%s", path, root_sub, user.username)
    return {"token": download_link.issue(_fs_kind(root_sub), path, user.sub),
            "expires_in": download_link.DEFAULT_TTL_SECONDS}


@router.get("/download")
async def download_file(
    user: OptionalUser,
    path: str = Query(..., description="File path"),
    root_sub: str | None = Query(None),
    token: str | None = Query(None, description="POST /download-link 로 받은 다운로드 토큰"),
    inline: bool = Query(False, description="true 면 브라우저 안에서 열기(inline)"),
    session: AsyncSession = Depends(get_session),
):
    """파일을 스트리밍으로 내려준다(MIME 타입 추정, 한글 파일 이름 지원).

    로그인(``Authorization``, 조회 범위 안의 파일) 또는 이 파일용 다운로드 토큰이 필요하다.
    토큰 없는 익명 요청은 401, 조회 범위 밖 파일은 403."""
    if user is None:
        if not token:
            logger.warning("파일 다운로드 거부(인증 없음): path=%s root_sub=%s", path, root_sub)
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        download_link.verify(token, _fs_kind(root_sub), path)  # 링크는 조회 범위를 검사한 뒤에만 발급한다
    else:
        await _require_read_scope(user, session, path, root_sub)
    resolved = _resolve_or_http(path, root_sub)
    logger.debug("파일 다운로드: path=%s root_sub=%s inline=%s", path, root_sub, inline)
    content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    return FileResponse(
        resolved, media_type=content_type,
        headers={"Content-Disposition": download_link.content_disposition(resolved.name, inline)},
    )


# =========================================================================== #
# 업로드
# =========================================================================== #


@router.post("/upload")
async def upload_file(_guard: AdminUser,
    file: UploadFile,
    path: str = Query(..., description="Destination directory path"),
    root_sub: str | None = Query(None),
):
    """지정한 디렉토리에 파일을 업로드(같은 이름이면 덮어씀).

    디렉터리 아님·잘못된 파일 이름(빈 값·``.``/``..``) 400, 경로 위반 403. 파일 이름은 디렉터리
    성분을 버린 마지막 이름만 쓴다.

    파일 내용을 메모리에 모두 읽은 뒤 저장한다."""
    try:
        content = await file.read()
        filename = file.filename or "uploaded_file"
        saved_path = await service.save_uploaded_file(path, filename, content, root_sub=root_sub)
        logger.info("파일시스템 업로드: %s (%d bytes, root_sub=%s)", saved_path, len(content), root_sub)
        return {"path": saved_path, "size": len(content)}
    except NotADirectoryError as e:
        logger.warning("업로드 거부 (디렉토리 아님): %s", path)
        raise HTTPException(status_code=400, detail=str(e))
    except service.InvalidFilenameError as e:
        logger.warning("업로드 거부 (잘못된 파일 이름): path=%s", path)
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        logger.warning("업로드 거부 (경로 위반): %s", path)
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("업로드 오류 (path=%s): %s", path, e)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================== #
# 폴더 생성
# =========================================================================== #


@router.post("/folders", response_model=CreateFolderResponse)
async def create_folder(_guard: AdminUser,
    body: CreateFolderRequest,
    root_sub: str | None = Query(None),
):
    """새 디렉토리 생성(상위 디렉터리도 함께, 이미 있어도 성공). 경로 위반 403."""
    try:
        result = await service.create_folder(body.path, root_sub=root_sub)
        logger.info("파일시스템 폴더 생성: %s (root_sub=%s)", body.path, root_sub)
        return result
    except ValueError as e:
        logger.warning("폴더 생성 거부 (경로 위반): %s", body.path)
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("폴더 생성 오류 (path=%s): %s", body.path, e)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================== #
# 삭제
# =========================================================================== #


@router.post("/delete", response_model=DeleteResponse)
async def delete_paths(_guard: AdminUser,
    body: DeleteRequest,
    root_sub: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """파일/디렉토리 일괄 삭제. 디렉토리는 하위 항목과 함께 재귀 삭제. 등록 모델 파일은 409."""
    try:
        await _guard_registered_model_paths(session, body.paths, root_sub)
        result = await service.delete_paths(body.paths, root_sub=root_sub)
        logger.info("파일시스템 삭제: %d개 경로 (root_sub=%s)", len(body.paths), root_sub)
        return result
    except HTTPException:
        raise
    except ValueError as e:
        logger.warning("삭제 거부 (경로 위반): %s", body.paths)
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("삭제 오류 (paths=%s): %s", body.paths, e)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================== #
# 이름 변경 / 이동
# =========================================================================== #


@router.post("/rename", response_model=RenameResponse)
async def rename(_guard: AdminUser,
    body: RenameRequest,
    root_sub: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """파일/디렉토리 이름 변경 또는 이동(같은 호출로 처리). 등록 모델 파일·경로로의 이동은 409."""
    try:
        await _guard_registered_model_paths(session, [body.source_path, body.destination_path], root_sub)
        result = await service.rename(body.source_path, body.destination_path, root_sub=root_sub)
        logger.info("파일시스템 이름 변경: %s -> %s (root_sub=%s)", body.source_path, body.destination_path, root_sub)
        return result
    except HTTPException:
        raise
    except FileNotFoundError as e:
        logger.warning("이름 변경 실패 (원본 없음): %s", body.source_path)
        raise HTTPException(status_code=404, detail=str(e))
    except FileExistsError as e:
        logger.warning("이름 변경 거부 (대상이 이미 존재): %s", body.destination_path)
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        logger.warning("이름 변경 거부 (경로 위반): src=%s, dst=%s", body.source_path, body.destination_path)
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("이름 변경 오류 (src=%s, dst=%s): %s", body.source_path, body.destination_path, e)
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================== #
# 파일 미리보기
# =========================================================================== #

# 구형 바이너리 .xls 는 openpyxl 이 읽지 못하므로(BadZipFile) 지원 목록에서 뺀다 → 400
_PREVIEW_FORMATS = {
    "parquet": "table",
    "xlsx": "table",
    "docx": "document",
    "pptx": "document",
}


@router.get(
    "/preview",
    response_model=TablePreviewResponse | DocumentPreviewResponse,
)
async def preview_file(
    user: CurrentUser,
    path: str = Query(..., description="File path"),
    sheet: str | None = Query(None, description="Sheet name (xlsx only)"),
    max_rows: int = Query(1000, ge=1, le=10000),
    root_sub: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """서버에서 변환해 파일 미리보기(parquet/xlsx → 테이블, docx → HTML, pptx → 슬라이드 텍스트).

    지원하지 않는 확장자(.xls 포함) 400, 확장자와 달리 손상·다른 형식인 파일(zip 아님) 400,
    없음 404, 조회 범위 밖·경로 위반 403. 형식은 확장자로만 판단한다.
    """
    logger.debug("미리보기 요청: path=%s sheet=%s max_rows=%d root_sub=%s", path, sheet, max_rows, root_sub)
    await _require_read_scope(user, session, path, root_sub)
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext not in _PREVIEW_FORMATS:
        logger.warning("미리보기 거부 (지원하지 않는 형식): %s", path)
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 미리보기 형식입니다: .{ext}. "
            f"지원 형식: {', '.join(_PREVIEW_FORMATS.keys())}",
        )

    try:
        if ext == "parquet":
            return await service.preview_parquet(path, max_rows=max_rows, root_sub=root_sub)
        if ext == "xlsx":
            return await service.preview_xlsx(path, sheet=sheet, max_rows=max_rows, root_sub=root_sub)
        if ext == "docx":
            return await service.preview_docx(path, root_sub=root_sub)
        if ext == "pptx":
            return await service.preview_pptx(path, root_sub=root_sub)
    except FileNotFoundError as e:
        logger.warning("미리보기 실패(파일 없음): path=%s", path)
        raise HTTPException(status_code=404, detail=str(e))
    except zipfile.BadZipFile as e:
        # xlsx/docx/pptx 는 zip 컨테이너 — 확장자만 맞고 내용이 다른(손상된) 파일은 클라이언트 오류
        logger.warning("미리보기 실패(손상되었거나 형식이 다른 파일): path=%s err=%s", path, e)
        raise HTTPException(status_code=400, detail=f"파일을 {ext} 형식으로 읽을 수 없습니다.")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("미리보기 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
