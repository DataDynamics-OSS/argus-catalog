# SPDX-License-Identifier: Apache-2.0
"""일 단위 롤링 파일 핸들러를 사용하는 로깅 설정."""

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.config import settings

LOG_FORMAT = (
    "%(levelname)s %(asctime)s.%(msecs)03d %(process)d %(programname)s"
    " %(filename)s:%(funcName)s:%(lineno)d - %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _ProgramNameFilter(logging.Filter):
    """모든 레코드에 ``programname`` 속성(앱 이름)을 붙여 LOG_FORMAT 의 %(programname)s 를 채운다."""

    def __init__(self, program_name: str) -> None:
        super().__init__()
        self.program_name = program_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.programname = self.program_name  # type: ignore[attr-defined]
        return True


class _DailyFileHandler(TimedRotatingFileHandler):
    """자정마다 롤링하고, 지난 파일을 ``{stem}_{YYYYMMDD}{ext}`` 이름으로 남기는 파일 핸들러."""

    def __init__(self, log_dir: Path, filename: str, backup_count: int) -> None:
        log_file = log_dir / filename
        super().__init__(
            filename=str(log_file),
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self.suffix = "%Y%m%d"
        self._log_dir = log_dir
        self._base_stem = Path(filename).stem
        self._base_ext = Path(filename).suffix or ".log"
        self.namer = self._namer

    def _namer(self, default_name: str) -> str:
        """기본 이름 ``app.log.20260101`` → ``app_20260101.log`` 로 바꾼다."""
        parts = default_name.rsplit(".", 1)
        if len(parts) == 2:
            date_suffix = parts[1]
            return str(self._log_dir / f"{self._base_stem}_{date_suffix}{self._base_ext}")
        return default_name


def setup_logging() -> None:
    """루트 로거를 설정 파일 기준(레벨·디렉터리·보관 개수)으로 초기화한다.

    기존 루트 핸들러를 모두 지우고 일 단위 롤링 파일 핸들러 하나만 단다. 로그 레벨이 DEBUG 면
    SQLAlchemy 엔진 로그(SQL)도 DEBUG 로, 아니면 WARNING 이상만 남긴다.
    """
    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)
    program_filter = _ProgramNameFilter(settings.app_name)

    file_handler = _DailyFileHandler(
        log_dir=log_dir,
        filename=settings.log_filename,
        backup_count=settings.log_rolling_backup_count,
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(program_filter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)

    sa_logger = logging.getLogger("sqlalchemy.engine")
    if log_level <= logging.DEBUG:
        sa_logger.setLevel(logging.DEBUG)
    else:
        sa_logger.setLevel(logging.WARNING)
    root_logger.info("로깅 초기화: level=%s file=%s backup_count=%d",
                     logging.getLevelName(log_level), log_dir / settings.log_filename,
                     settings.log_rolling_backup_count)
