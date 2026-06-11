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

로깅 정책: 변경 동작(업로드 / 디렉토리 생성 / 삭제 / 이름 변경) 은 INFO 로
영향을 받은 경로·항목 수를 기록한다. 권한·경로 오류는 WARNING 으로,
예기치 않은 예외는 ERROR 로 남긴다. 디렉토리 listing / stat / preview /
download 는 조회 성격이라 INFO 를 생략한다.
"""

import logging
import mimetypes

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.core.auth import AdminUser
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


# =========================================================================== #
# 디렉토리 목록
# =========================================================================== #


@router.get("/list", response_model=ListDirectoryResponse)
async def list_directory(
    path: str = Query("/", description="Directory path"),
    root_sub: str | None = Query(None, description="Subdirectory of data_dir to use as root"),
):
    """주어진 경로 아래의 파일/디렉토리를 나열한다."""
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
    path: str = Query(..., description="File or directory path"),
    root_sub: str | None = Query(None),
):
    """파일 또는 디렉토리의 상세 메타데이터(크기·권한·시간 등)."""
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


@router.get("/download")
async def download_file(
    path: str = Query(..., description="File path"),
    root_sub: str | None = Query(None),
):
    """파일을 다운로드(MIME 타입 추정 + ``Content-Disposition: attachment``)."""
    try:
        data, filename = await service.read_file(path, root_sub=root_sub)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IsADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("다운로드 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
    """지정한 디렉토리에 파일을 업로드."""
    try:
        content = await file.read()
        filename = file.filename or "uploaded_file"
        saved_path = await service.save_uploaded_file(path, filename, content, root_sub=root_sub)
        logger.info("파일시스템 업로드: %s (%d bytes, root_sub=%s)", saved_path, len(content), root_sub)
        return {"path": saved_path, "size": len(content)}
    except NotADirectoryError as e:
        logger.warning("업로드 거부 (디렉토리 아님): %s", path)
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
    """새 디렉토리 생성."""
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
):
    """파일/디렉토리 일괄 삭제. 디렉토리는 하위 항목과 함께 재귀 삭제."""
    try:
        result = await service.delete_paths(body.paths, root_sub=root_sub)
        logger.info("파일시스템 삭제: %d개 경로 (root_sub=%s)", len(body.paths), root_sub)
        return result
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
):
    """파일/디렉토리 이름 변경 또는 이동(같은 호출로 처리)."""
    try:
        result = await service.rename(body.source_path, body.destination_path, root_sub=root_sub)
        logger.info("파일시스템 이름 변경: %s -> %s (root_sub=%s)", body.source_path, body.destination_path, root_sub)
        return result
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

_PREVIEW_FORMATS = {
    "parquet": "table",
    "xlsx": "table",
    "xls": "table",
    "docx": "document",
    "pptx": "document",
}


@router.get(
    "/preview",
    response_model=TablePreviewResponse | DocumentPreviewResponse,
)
async def preview_file(
    path: str = Query(..., description="File path"),
    sheet: str | None = Query(None, description="Sheet name (xlsx/xls only)"),
    max_rows: int = Query(1000, ge=1, le=10000),
    root_sub: str | None = Query(None),
):
    """서버에서 변환해 파일 미리보기(parquet/xlsx → 테이블, docx/pptx → 문서 텍스트)."""
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
        if ext in ("xlsx", "xls"):
            return await service.preview_xlsx(path, sheet=sheet, max_rows=max_rows, root_sub=root_sub)
        if ext == "docx":
            return await service.preview_docx(path, root_sub=root_sub)
        if ext == "pptx":
            return await service.preview_pptx(path, root_sub=root_sub)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error("미리보기 오류: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
