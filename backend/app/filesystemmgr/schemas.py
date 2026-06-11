"""로컬 파일시스템 브라우저 Pydantic 스키마(요청·응답 DTO).

라우터(``app/filesystemmgr/router.py``) 와 서비스(``app/filesystemmgr/service.py``) 가
공유하는 파일/폴더 메타데이터 + 변경 동작(생성·삭제·이름 변경) +
미리보기 DTO 를 모아둔다.
"""

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# 공통
# --------------------------------------------------------------------------- #


class FileInfo(BaseModel):
    """단일 파일의 메타데이터."""

    key: str = Field(description="Absolute path")
    name: str = Field(description="Display name (basename)")
    size: int = Field(0, description="Size in bytes")
    last_modified: str = Field("", description="Last modified timestamp (ISO 8601)")
    owner: str = Field("", description="File owner")
    group: str = Field("", description="File group")
    permissions: str = Field("", description="Permission string (e.g. rwxr-xr-x)")


class FolderInfo(BaseModel):
    """디렉토리의 메타데이터."""

    key: str = Field(description="Absolute path (with trailing /)")
    name: str = Field(description="Display name (basename)")
    owner: str = Field("", description="Directory owner")
    group: str = Field("", description="Directory group")
    permissions: str = Field("", description="Permission string (e.g. rwxr-xr-x)")


# --------------------------------------------------------------------------- #
# 디렉토리 목록
# --------------------------------------------------------------------------- #


class ListDirectoryResponse(BaseModel):
    """디렉토리 목록 조회 응답."""

    folders: list[FolderInfo] = Field(default_factory=list)
    files: list[FileInfo] = Field(default_factory=list)
    current_path: str = Field(description="Absolute path of the listed directory")


# --------------------------------------------------------------------------- #
# 폴더 생성
# --------------------------------------------------------------------------- #


class CreateFolderRequest(BaseModel):
    """새 디렉토리 생성 요청."""

    path: str = Field(..., description="Absolute path of the new directory")


class CreateFolderResponse(BaseModel):
    """디렉토리 생성 응답."""

    path: str


# --------------------------------------------------------------------------- #
# 삭제
# --------------------------------------------------------------------------- #


class DeleteRequest(BaseModel):
    """파일 또는 디렉토리 삭제 요청."""

    paths: list[str] = Field(
        ..., min_length=1, max_length=1000, description="Absolute paths to delete"
    )


class DeleteResponse(BaseModel):
    """삭제 응답."""

    deleted: list[str]
    errors: list[dict] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 이름 변경 / 이동
# --------------------------------------------------------------------------- #


class RenameRequest(BaseModel):
    """파일/디렉토리 이름 변경 또는 이동 요청."""

    source_path: str = Field(..., description="Source absolute path")
    destination_path: str = Field(..., description="Destination absolute path")


class RenameResponse(BaseModel):
    """이름 변경/이동 응답."""

    source: str
    destination: str


# --------------------------------------------------------------------------- #
# 파일 메타데이터 (stat)
# --------------------------------------------------------------------------- #


class FileStatResponse(BaseModel):
    """파일/디렉토리 상세 메타데이터."""

    path: str
    name: str
    is_directory: bool
    size: int = 0
    last_modified: str = ""
    last_accessed: str = ""
    created: str = ""
    owner: str = ""
    group: str = ""
    permissions: str = ""
    permissions_octal: str = ""
    inode: int = 0
    hard_links: int = 0
    symlink_target: str | None = None


# --------------------------------------------------------------------------- #
# 파일 미리보기
# --------------------------------------------------------------------------- #


class TablePreviewResponse(BaseModel):
    """Parquet, XLSX/XLS 파일의 테이블 미리보기."""

    format: str = Field(..., description="Source format: parquet, xlsx, xls")
    columns: list[str] = Field(default_factory=list)
    rows: list[list] = Field(default_factory=list)
    total_rows: int = 0
    sheet_names: list[str] = Field(default_factory=list)
    active_sheet: str = ""


class DocumentPreviewResponse(BaseModel):
    """DOCX, PPTX 파일의 문서 미리보기."""

    format: str = Field(..., description="Source format: docx, pptx")
    html: str = ""
    slides: list[dict] | None = None
