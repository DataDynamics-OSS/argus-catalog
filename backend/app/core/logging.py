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
    def __init__(self, program_name: str) -> None:
        super().__init__()
        self.program_name = program_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.programname = self.program_name  # type: ignore[attr-defined]
        return True


class _DailyFileHandler(TimedRotatingFileHandler):
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
        parts = default_name.rsplit(".", 1)
        if len(parts) == 2:
            date_suffix = parts[1]
            return str(self._log_dir / f"{self._base_stem}_{date_suffix}{self._base_ext}")
        return default_name


def setup_logging() -> None:
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
