# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 메타데이터 — 행정안전부 「공공데이터의 인공지능 친화적 관리 가이드라인」 V1.0 항목.

설계: design/ai-ready-public-data-guideline-review.md (§5.2 메타데이터 항목, §6.1 입력 부담 원칙), TODO-096 1단계.

- 데이터셋 컬럼(``Dataset.legal_basis`` …)에 저장하는 큐레이션 항목이다. 수집이 덮어쓰지 않는다.
- 기관 공통 값(제공기관·언어·라이선스·연락처)은 설정 ``ai_ready_*`` 의 기본값을 쓰고, 데이터셋에 값이 있으면 그 값이 우선한다.
  기본값은 데이터셋에 써 넣지 않는다 — 설정을 바꾸면 비어 있는 데이터셋 전체에 바로 반영되게.
- 자동 채움 제안(``build_suggestions``)은 최신 프로파일에서 시간범위·결측 정보를 계산해 돌려줄 뿐 저장하지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 라이선스 코드 — 공공누리 1~4유형, Creative Commons 4.0 계열, CC0, 기관 이용약관(CUSTOM).
LICENSES: dict[str, str] = {
    "KOGL-1": "공공누리 제1유형 (출처표시)",
    "KOGL-2": "공공누리 제2유형 (출처표시 + 상업적 이용금지)",
    "KOGL-3": "공공누리 제3유형 (출처표시 + 변경금지)",
    "KOGL-4": "공공누리 제4유형 (출처표시 + 상업적 이용금지 + 변경금지)",
    "CC-BY-4.0": "CC BY 4.0",
    "CC-BY-SA-4.0": "CC BY-SA 4.0",
    "CC-BY-NC-4.0": "CC BY-NC 4.0",
    "CC-BY-NC-SA-4.0": "CC BY-NC-SA 4.0",
    "CC-BY-ND-4.0": "CC BY-ND 4.0",
    "CC-BY-NC-ND-4.0": "CC BY-NC-ND 4.0",
    "CC0-1.0": "CC0 1.0 (권리 포기)",
    "CUSTOM": "기관 이용약관 (URL 지정)",
}

# 허용 이용 유형 — 가이드라인 §3.4 "데이터별 4가지 활용 유형의 허용 여부를 메타데이터에 명시".
ALLOWED_USES: dict[str, str] = {
    "VIEW": "열람",
    "ANALYSIS": "분석",
    "COMBINE": "결합",
    "AI_TRAINING": "AI 학습",
}

# 데이터셋 컬럼 — 문자열(그대로 저장)·날짜·불리언("true"/"false")·목록(쉼표) 별로 나눈다.
STR_FIELDS = (
    "legal_basis", "publisher", "landing_page", "contact_email", "data_language", "persistent_id",
    "spatial_coverage", "data_version", "version_notes", "license", "license_url", "rights",
    "data_biases", "known_limitations", "missing_data_info", "missing_value_notation", "privacy_measures",
)
DATE_FIELDS = ("temporal_start", "temporal_end")
BOOL_FIELDS = ("contains_synthetic", "contains_machine_annotation")
LIST_FIELDS = ("allowed_uses",)
ALL_FIELDS = STR_FIELDS + DATE_FIELDS + BOOL_FIELDS + LIST_FIELDS

# 설정 키 — 카테고리 ``ai_ready`` (app/settings/service.py 의 시드 기본값과 같은 키).
SETTING_PUBLISHER = "ai_ready_publisher"
SETTING_LANGUAGE = "ai_ready_language"
SETTING_LICENSE = "ai_ready_license"
SETTING_LICENSE_URL = "ai_ready_license_url"
SETTING_CONTACT_EMAIL = "ai_ready_contact_email"

_LANG_RE = re.compile(r"^[a-z]{2}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# 값 검증 — DatasetUpdate·설정 저장이 함께 쓴다(ValueError 는 pydantic 이 422 로 바꾼다)
# ---------------------------------------------------------------------------

def norm_text(v: str | None) -> str | None:
    """앞뒤 공백을 떼고, 비면 None(= 값 지우기)."""
    if v is None:
        return None
    v = v.strip()
    return v or None


def check_url(v: str | None, label: str) -> str | None:
    """http(s) URL 만 허용."""
    v = norm_text(v)
    if v is not None and not re.match(r"^https?://\S+$", v, re.IGNORECASE):
        raise ValueError(f"{label}는 http:// 또는 https:// 로 시작하는 URL 이어야 합니다.")
    return v


def check_email(v: str | None) -> str | None:
    v = norm_text(v)
    if v is not None and not _EMAIL_RE.match(v):
        raise ValueError("연락처 이메일 형식이 올바르지 않습니다.")
    return v


def check_language(v: str | None) -> str | None:
    """ISO 639-1 두 글자 코드, 쉼표로 여러 개. 소문자로 맞추고 중복을 뺀다."""
    v = norm_text(v)
    if v is None:
        return None
    codes: list[str] = []
    for part in v.split(","):
        code = part.strip().lower()
        if not code:
            continue
        if not _LANG_RE.match(code):
            raise ValueError(f"언어는 ISO 639-1 두 글자 코드여야 합니다(예: ko, en): {part.strip()}")
        if code not in codes:
            codes.append(code)
    return ",".join(codes) or None


def check_license(v: str | None) -> str | None:
    v = norm_text(v)
    if v is not None and v not in LICENSES:
        raise ValueError(f"알 수 없는 라이선스 코드입니다: {v} (허용: {', '.join(LICENSES)})")
    return v


def check_allowed_uses(v: list[str] | None) -> list[str] | None:
    """허용 이용 유형 — 정의된 코드만, 정의 순서로, 중복 없이. 빈 목록은 그대로(= 모두 허용하지 않음)."""
    if v is None:
        return None
    wanted = {str(x).strip().upper() for x in v if str(x).strip()}
    unknown = wanted - set(ALLOWED_USES)
    if unknown:
        raise ValueError(f"알 수 없는 이용 유형입니다: {', '.join(sorted(unknown))} (허용: {', '.join(ALLOWED_USES)})")
    return [k for k in ALLOWED_USES if k in wanted]


# ---------------------------------------------------------------------------
# 저장·응답 변환
# ---------------------------------------------------------------------------

def apply_update(dataset, req) -> None:
    """요청에 **명시한** AI-Ready 항목만 데이터셋에 반영한다. null 을 보내면 지운다.

    다른 확장 메타데이터와 달리 null 을 '변경 없음'으로 보지 않는다 — 화면에서 값을 비우고 저장하면 지워져야 하고,
    수집(metadata-sync)은 이 항목을 보내지 않는다. 시간범위 앞뒤가 바뀌면 ValueError.
    """
    sent = req.model_fields_set
    for f in STR_FIELDS + DATE_FIELDS:
        if f in sent:
            setattr(dataset, f, getattr(req, f))
    for f in BOOL_FIELDS:
        if f in sent:
            v = getattr(req, f)
            setattr(dataset, f, None if v is None else ("true" if v else "false"))
    if "allowed_uses" in sent:
        uses = req.allowed_uses
        # 빈 목록은 "모두 허용하지 않음"으로 기록한다(""). null 은 "정하지 않음".
        dataset.allowed_uses = None if uses is None else ",".join(uses)
    start, end = dataset.temporal_start, dataset.temporal_end
    if start is not None and end is not None and start > end:
        raise ValueError("시간범위의 시작일이 종료일보다 늦습니다.")


async def record_version(session: AsyncSession, dataset, before: tuple, actor: str | None) -> None:
    """데이터 버전 이력(TODO-096 4단계) — 가장 최근 이력과 버전이 같으면 그 줄의 노트를 고치고, 다르면 한 줄 더한다.

    버전을 비우면 이력은 그대로 둔다(지난 버전은 보존). 비웠다가 같은 버전을 다시 적으면 새 줄을 만들지 않는다.
    이력이 없던 기존 버전(1단계에서 적은 값)에 노트를 고쳐도 그 버전으로 한 줄 만든다.
    """
    from app.catalog.models import DatasetVersion

    version = norm_text(dataset.data_version)
    notes = norm_text(dataset.version_notes)
    if not version or (version, notes) == (norm_text(before[0]), norm_text(before[1])):
        return
    row = (await session.execute(
        select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)
        .order_by(DatasetVersion.id.desc()).limit(1)
    )).scalars().first()
    if row is not None and row.version == version:
        row.notes = notes
        return
    session.add(DatasetVersion(dataset_id=dataset.id, version=version, notes=notes, created_by=actor))
    logger.info("데이터 버전 기록: dataset_id=%s version=%s by=%s", dataset.id, version, actor)


async def list_versions(session: AsyncSession, dataset_id: int) -> list[dict]:
    """데이터 버전 이력 — 최근 순."""
    from app.catalog.models import DatasetVersion

    rows = (await session.execute(
        select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.created_at.desc(), DatasetVersion.id.desc())
    )).scalars().all()
    return [{"id": r.id, "version": r.version, "notes": r.notes, "created_by": r.created_by,
             "created_at": r.created_at, "updated_at": r.updated_at} for r in rows]


def response_fields(dataset) -> dict:
    """``DatasetResponse`` 에 넣을 AI-Ready 항목."""
    out: dict = {f: getattr(dataset, f) for f in STR_FIELDS + DATE_FIELDS}
    for f in BOOL_FIELDS:
        v = getattr(dataset, f)
        out[f] = None if v is None else v == "true"
    raw = dataset.allowed_uses
    out["allowed_uses"] = None if raw is None else [x for x in raw.split(",") if x]
    return out


# ---------------------------------------------------------------------------
# 기관 기본값 (설정)
# ---------------------------------------------------------------------------

async def load_defaults(session: AsyncSession) -> dict:
    """설정의 기관 기본값 — 없거나 비면 None(언어만 기본 'ko')."""
    from app.settings.service import get_config_by_category

    cfg = await get_config_by_category(session, "ai_ready")
    return {
        "publisher": norm_text(cfg.get(SETTING_PUBLISHER)),
        "data_language": norm_text(cfg.get(SETTING_LANGUAGE)) or "ko",
        "license": norm_text(cfg.get(SETTING_LICENSE)),
        "license_url": norm_text(cfg.get(SETTING_LICENSE_URL)),
        "contact_email": norm_text(cfg.get(SETTING_CONTACT_EMAIL)),
    }


# ---------------------------------------------------------------------------
# 자동 채움 제안 — 최신 프로파일에서
# ---------------------------------------------------------------------------

_DATE_TYPE_HINTS = ("date", "time")  # DATE, DATETIME, TIMESTAMP, timestamp with time zone …
_MAX_MISSING_COLUMNS = 10


def _parse_day(v) -> date | None:
    """프로파일의 최소·최대값(문자열)에서 날짜를 읽는다. 앞 10자(YYYY-MM-DD)만 본다."""
    if v is None:
        return None
    s = str(v).strip()
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _temporal_from_columns(columns: list[dict]) -> dict | None:
    """날짜·시각 컬럼의 최소~최대 중 가장 넓은 범위를 고른다. 날짜 컬럼이 없거나 값을 못 읽으면 None."""
    best: tuple[int, date, date, str] | None = None
    for c in columns:
        ctype = str(c.get("column_type") or "").lower()
        if not any(h in ctype for h in _DATE_TYPE_HINTS):
            continue
        lo, hi = _parse_day(c.get("min_value")), _parse_day(c.get("max_value"))
        if lo is None or hi is None or lo > hi:
            continue
        span = (hi - lo).days
        if best is None or span > best[0]:
            best = (span, lo, hi, str(c.get("column_name") or ""))
    if best is None:
        return None
    return {"temporal_start": best[1], "temporal_end": best[2], "column": best[3]}


def _missing_from_columns(columns: list[dict], row_count: int, profiled_at: datetime | None) -> dict:
    """결측이 있는 컬럼을 결측률 높은 순으로 요약한 문장과 전체 결측률."""
    total_cells = 0
    null_cells = 0
    missing: list[tuple[float, str]] = []
    for c in columns:
        total = int(c.get("total_count") or row_count or 0)
        nulls = int(c.get("null_count") or 0)
        total_cells += total
        null_cells += nulls
        if nulls > 0:
            pct = float(c.get("null_percent") or (nulls / total * 100 if total else 0))
            missing.append((pct, str(c.get("column_name") or "")))
    missing.sort(key=lambda x: (-x[0], x[1]))
    overall = round(null_cells / total_cells * 100, 2) if total_cells else 0.0
    when = profiled_at.date().isoformat() if profiled_at else "날짜 미상"
    if not missing:
        text = f"결측값 없음 (프로파일 {when}, {row_count:,}행 기준)."
    else:
        shown = ", ".join(f"{name} {pct:g}%" for pct, name in missing[:_MAX_MISSING_COLUMNS])
        more = f" 외 {len(missing) - _MAX_MISSING_COLUMNS}개 컬럼" if len(missing) > _MAX_MISSING_COLUMNS else ""
        text = (f"전체 결측률 {overall:g}% (프로파일 {when}, {row_count:,}행 기준). "
                f"컬럼별 결측률: {shown}{more}. 결측 사유·처리 방식: ")
    return {"missing_data_info": text, "missing_ratio": overall, "missing_columns": len(missing)}


async def build_suggestions(session: AsyncSession, dataset_id: int) -> dict:
    """최신 프로파일에서 시간범위·결측 정보 제안을 만든다(저장하지 않음). 프로파일이 없으면 profile=None."""
    from app.quality.models import DataProfile

    profile = (await session.execute(
        select(DataProfile).where(DataProfile.dataset_id == dataset_id)
        .order_by(DataProfile.profiled_at.desc(), DataProfile.id.desc()).limit(1)
    )).scalars().first()
    if profile is None:
        return {"profile": None, "temporal": None, "missing": None}
    try:
        columns = json.loads(profile.profile_json or "[]")
    except (TypeError, ValueError):
        logger.warning("프로파일 JSON 을 읽지 못함: dataset_id=%d profile_id=%d", dataset_id, profile.id)
        columns = []
    if not isinstance(columns, list):
        columns = []
    columns = [c for c in columns if isinstance(c, dict)]
    return {
        "profile": {"id": profile.id, "row_count": profile.row_count, "profiled_at": profile.profiled_at},
        "temporal": _temporal_from_columns(columns),
        "missing": _missing_from_columns(columns, profile.row_count or 0, profile.profiled_at) if columns else None,
    }
