# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 내보내기 — 배포본 관리, DCAT JSON-LD · Croissant JSON-LD · 데이터 카드(Markdown) 생성 (TODO-096 3단계).

설계: design/ai-ready-public-data-guideline-review.md §6.4.

- **DCAT** — W3C DCAT v3 + 가이드라인 표 9~12 의 속성(dct·dcat·adms·owl·rai·dqv). 「국가 데이터 카탈로그 표준 가이드」의
  ``ndi`` 확장 속성은 원문을 확인하지 못해 넣지 않았다(검토 문서 §8).
- **Croissant** — MLCommons Croissant 1.0. 스키마를 ``cr:RecordSet``·``cr:Field`` 로, 배포본을 ``cr:FileObject`` 로 옮기고
  RAI 확장(``rai:dataBiases``·``rai:dataLimitations`` 등)을 붙인다. 배포본이 없으면 필드에 ``source`` 가 없다.
- **데이터 카드** — 가이드라인 부록 5 의 항목 순서. 값이 없으면 "작성 필요"와 채울 곳을 적는다.
  샘플 레코드는 넣지 않는다 — 실데이터는 기능 권한(``datasets.sample-view``)으로 따로 통제하는데 카드는 외부 API 로도 나가기 때문.

값은 데이터셋 값 → 설정의 기관 기본값(``ai_ready.load_defaults``) 순으로 쓴다. 기본값을 데이터셋에 써 넣지 않는 1단계 원칙과 같다.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import ai_ready
from app.catalog.models import (
    Category,
    Dataset,
    DatasetCategory,
    DatasetDistribution,
    DatasetLineage,
    DatasetSchema,
    DatasetTag,
    Datasource,
    Organization,
    System,
    Tag,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 어휘
# ---------------------------------------------------------------------------

# 형식 → MIME (dcat:mediaType · Croissant encodingFormat)
MEDIA_TYPES: dict[str, str] = {
    "CSV": "text/csv", "TSV": "text/tab-separated-values", "JSON": "application/json",
    "JSONL": "application/jsonl", "NDJSON": "application/x-ndjson", "XML": "application/xml",
    "PARQUET": "application/vnd.apache.parquet", "AVRO": "application/avro", "ORC": "application/x-orc",
    "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "XLS": "application/vnd.ms-excel",
    "ZIP": "application/zip", "GZIP": "application/gzip", "PDF": "application/pdf", "TXT": "text/plain",
    "GEOJSON": "application/geo+json", "PNG": "image/png", "JPEG": "image/jpeg", "JPG": "image/jpeg",
    "TIFF": "image/tiff", "MP4": "video/mp4", "WAV": "audio/wav", "HWP": "application/x-hwp",
}

CHECKSUM_ALGORITHMS = ("SHA-256", "SHA-512", "SHA-1", "MD5")
_CHECKSUM_LEN = {"SHA-256": 64, "SHA-512": 128, "SHA-1": 40, "MD5": 32}
_HEX_RE = re.compile(r"^[0-9a-f]+$")

# 갱신주기 → 빈도 어휘(가이드라인 예시의 http://purl.org/cld/freq/, 시간 단위는 EU 어휘에만 있음)
FREQUENCY_URIS: dict[str, str] = {
    "REALTIME": "http://purl.org/cld/freq/continuous",
    "HOURLY": "http://publications.europa.eu/resource/authority/frequency/HOURLY",
    "DAILY": "http://purl.org/cld/freq/daily",
    "WEEKLY": "http://purl.org/cld/freq/weekly",
    "MONTHLY": "http://purl.org/cld/freq/monthly",
    "MANUAL": "http://purl.org/cld/freq/irregular",
}
FREQUENCY_LABELS = {"REALTIME": "실시간", "HOURLY": "매시간", "DAILY": "매일", "WEEKLY": "매주", "MONTHLY": "매월",
                    "MANUAL": "비정기(수동)"}

LICENSE_URIS: dict[str, str] = {
    "KOGL-1": "https://www.kogl.or.kr/info/licenseType1.do",
    "KOGL-2": "https://www.kogl.or.kr/info/licenseType2.do",
    "KOGL-3": "https://www.kogl.or.kr/info/licenseType3.do",
    "KOGL-4": "https://www.kogl.or.kr/info/licenseType4.do",
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "CC-BY-NC-4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
    "CC-BY-NC-SA-4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "CC-BY-ND-4.0": "https://creativecommons.org/licenses/by-nd/4.0/",
    "CC-BY-NC-ND-4.0": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
}

LANG_URI = "http://id.loc.gov/vocabulary/iso639-1/"

DCAT_CONTEXT = {
    "dcat": "http://www.w3.org/ns/dcat#",
    "dct": "http://purl.org/dc/terms/",
    "adms": "http://www.w3.org/ns/adms#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "owl": "http://www.w3.org/2002/07/owl#",
    "vcard": "http://www.w3.org/2006/vcard/ns#",
    "dqv": "http://www.w3.org/ns/dqv#",
    "spdx": "http://spdx.org/rdf/terms#",
    "time": "http://www.w3.org/2006/time#",
    "prov": "http://www.w3.org/ns/prov#",
    "rai": "http://mlcommons.org/croissant/RAI/",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "argus": "urn:argus:ai-ready:",
}

# Croissant 1.0 표준 컨텍스트(https://github.com/mlcommons/croissant — docs/croissant-spec.md 의 예시와 같음)
CROISSANT_CONTEXT = {
    "@language": "ko",
    "@vocab": "https://schema.org/",
    "citeAs": "cr:citeAs",
    "column": "cr:column",
    "conformsTo": "dct:conformsTo",
    "cr": "http://mlcommons.org/croissant/",
    "rai": "http://mlcommons.org/croissant/RAI/",
    "data": {"@id": "cr:data", "@type": "@json"},
    "dataType": {"@id": "cr:dataType", "@type": "@vocab"},
    "dct": "http://purl.org/dc/terms/",
    "examples": {"@id": "cr:examples", "@type": "@json"},
    "extract": "cr:extract",
    "field": "cr:field",
    "fileProperty": "cr:fileProperty",
    "fileObject": "cr:fileObject",
    "fileSet": "cr:fileSet",
    "format": "cr:format",
    "includes": "cr:includes",
    "isLiveDataset": "cr:isLiveDataset",
    "jsonPath": "cr:jsonPath",
    "key": "cr:key",
    "md5": "cr:md5",
    "parentField": "cr:parentField",
    "path": "cr:path",
    "recordSet": "cr:recordSet",
    "references": "cr:references",
    "regex": "cr:regex",
    "repeated": "cr:repeated",
    "replace": "cr:replace",
    "sc": "https://schema.org/",
    "separator": "cr:separator",
    "source": "cr:source",
    "subField": "cr:subField",
    "transform": "cr:transform",
}


def media_type(fmt: str | None, explicit: str | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    return MEDIA_TYPES.get(str(fmt or "").strip().upper())


def croissant_type(field_type: str | None) -> str:
    t = str(field_type or "").lower()
    if "bool" in t:
        return "sc:Boolean"
    if any(h in t for h in ("timestamp", "datetime")):
        return "sc:DateTime"
    if "date" in t:
        return "sc:Date"
    if any(h in t for h in ("int", "long", "short", "serial")):
        return "sc:Integer"
    if any(h in t for h in ("decimal", "numeric", "number", "float", "double", "real", "money")):
        return "sc:Float"
    return "sc:Text"


# ---------------------------------------------------------------------------
# 배포본
# ---------------------------------------------------------------------------

DIST_FIELDS = ("title", "description", "format", "media_type", "access_url", "download_url", "byte_size",
               "checksum_algorithm", "checksum")


def normalize_distribution(data: dict) -> dict:
    """배포본 입력 다듬기·검증 — 틀리면 ValueError(사용자 문구)."""
    out: dict = {}
    out["title"] = ai_ready.norm_text(data.get("title"))
    out["description"] = ai_ready.norm_text(data.get("description"))
    fmt = ai_ready.norm_text(data.get("format"))
    out["format"] = fmt.upper() if fmt else None
    out["media_type"] = ai_ready.norm_text(data.get("media_type"))
    out["access_url"] = ai_ready.check_url(data.get("access_url"), "접속 URL")
    if not out["access_url"]:
        raise ValueError("접속 URL 은 필수입니다.")
    out["download_url"] = ai_ready.check_url(data.get("download_url"), "내려받기 URL")
    size = data.get("byte_size")
    if size is not None and (not isinstance(size, int) or size < 0):
        raise ValueError("용량은 0 이상의 정수(바이트)여야 합니다.")
    out["byte_size"] = size
    algo = ai_ready.norm_text(data.get("checksum_algorithm"))
    value = ai_ready.norm_text(data.get("checksum"))
    if value:
        value = value.lower()
        algo = (algo or "SHA-256").upper()
        if algo not in CHECKSUM_ALGORITHMS:
            raise ValueError(f"체크섬 알고리즘은 {', '.join(CHECKSUM_ALGORITHMS)} 중 하나여야 합니다.")
        if not _HEX_RE.match(value) or len(value) != _CHECKSUM_LEN[algo]:
            raise ValueError(f"{algo} 체크섬은 16진수 {_CHECKSUM_LEN[algo]}자여야 합니다.")
    else:
        algo = None
    out["checksum_algorithm"], out["checksum"] = algo, value
    return out


def distribution_dict(d: DatasetDistribution) -> dict:
    return {
        "id": d.id, "dataset_id": d.dataset_id, **{k: getattr(d, k) for k in DIST_FIELDS},
        "effective_media_type": media_type(d.format, d.media_type),
        "ordinal": d.ordinal, "created_at": d.created_at, "updated_at": d.updated_at,
    }


async def list_distributions(session: AsyncSession, dataset_id: int) -> list[DatasetDistribution]:
    return list((await session.execute(
        select(DatasetDistribution).where(DatasetDistribution.dataset_id == dataset_id)
        .order_by(DatasetDistribution.ordinal, DatasetDistribution.id)
    )).scalars().all())


async def create_distribution(session: AsyncSession, dataset_id: int, data: dict) -> DatasetDistribution:
    values = normalize_distribution(data)
    nxt = (await session.execute(
        select(func.coalesce(func.max(DatasetDistribution.ordinal), -1) + 1)
        .where(DatasetDistribution.dataset_id == dataset_id)
    )).scalar_one()
    row = DatasetDistribution(dataset_id=dataset_id, ordinal=nxt, **values)
    session.add(row)
    await session.flush()
    logger.info("배포본 추가: dataset_id=%d id=%d format=%s", dataset_id, row.id, row.format)
    return row


async def update_distribution(session: AsyncSession, dataset_id: int, dist_id: int, data: dict) -> DatasetDistribution | None:
    row = await session.get(DatasetDistribution, dist_id)
    if row is None or row.dataset_id != dataset_id:
        return None
    for k, v in normalize_distribution(data).items():
        setattr(row, k, v)
    await session.flush()
    logger.info("배포본 수정: dataset_id=%d id=%d", dataset_id, dist_id)
    return row


async def reorder_distributions(session: AsyncSession, dataset_id: int, ids: list[int]) -> list[DatasetDistribution]:
    """배포본 순서 바꾸기 — ``ids`` 는 이 데이터셋 배포본 id 전부를 새 순서로. 틀리면 ValueError(사용자 문구).

    순서(``ordinal``)는 DCAT ``dcat:distribution``·Croissant ``distribution``·데이터 카드·페더레이션 상세가 따른다.
    """
    rows = await list_distributions(session, dataset_id)
    if len(ids) != len(set(ids)) or set(ids) != {r.id for r in rows}:
        raise ValueError("배포본 목록이 바뀌었습니다. 새로 고친 뒤 다시 시도하세요.")
    by_id = {r.id: r for r in rows}
    for i, dist_id in enumerate(ids):
        by_id[dist_id].ordinal = i
    await session.flush()
    logger.info("배포본 순서 변경: dataset_id=%d ids=%s", dataset_id, ids)
    return [by_id[i] for i in ids]


async def delete_distribution(session: AsyncSession, dataset_id: int, dist_id: int) -> bool:
    row = await session.get(DatasetDistribution, dist_id)
    if row is None or row.dataset_id != dataset_id:
        return False
    await session.delete(row)
    await session.flush()
    logger.info("배포본 삭제: dataset_id=%d id=%d", dataset_id, dist_id)
    return True


# ---------------------------------------------------------------------------
# 내보내기 재료
# ---------------------------------------------------------------------------

class _Bundle:
    """데이터셋 하나를 내보내는 데 필요한 값 모음."""

    def __init__(self, ds: Dataset, defaults: dict):
        self.ds = ds
        self.defaults = defaults
        self.datasource_name: str | None = None
        self.org_name: str | None = None
        self.system_name: str | None = None
        self.columns: list[DatasetSchema] = []
        self.tags: list[str] = []
        self.themes: list[str] = []
        self.owners: list[tuple[str, str]] = []
        self.distributions: list[DatasetDistribution] = []
        self.upstream: list[str] = []
        self.downstream: list[str] = []
        self.versions: list[dict] = []
        self.check: dict | None = None

    def eff(self, name: str):
        v = getattr(self.ds, name, None)
        if v is not None and str(v).strip() != "":
            return v
        return self.defaults.get(name)


async def _bundle(session: AsyncSession, dataset_id: int, *, with_check: bool = True) -> _Bundle | None:
    from app.catalog import ai_ready_check

    ds = await session.get(Dataset, dataset_id)
    if ds is None:
        return None
    b = _Bundle(ds, await ai_ready.load_defaults(session))
    row = (await session.execute(
        select(Datasource.name, Organization.name, System.name)
        .select_from(Datasource)
        .outerjoin(System, System.id == Datasource.system_id)
        .outerjoin(Organization, Organization.id == System.org_id)
        .where(Datasource.id == ds.datasource_id)
    )).first()
    if row:
        b.datasource_name, b.org_name, b.system_name = row
    b.columns = list((await session.execute(
        select(DatasetSchema).where(DatasetSchema.dataset_id == dataset_id).order_by(DatasetSchema.ordinal, DatasetSchema.id)
    )).scalars().all())
    b.tags = [n for (n,) in (await session.execute(
        select(Tag.name).join(DatasetTag, DatasetTag.tag_id == Tag.id).where(DatasetTag.dataset_id == dataset_id).order_by(Tag.name)
    )).all()]
    b.themes = [n for (n,) in (await session.execute(
        select(Category.name).join(DatasetCategory, DatasetCategory.category_id == Category.id)
        .where(DatasetCategory.dataset_id == dataset_id).order_by(Category.name)
    )).all()]
    # 소유자 목록 + (목록에 없으면) 생성자 — 체크리스트 C30 · 알림과 같은 소유자 기준(DEF-138)
    from app.catalog.dataset_owner import owner_names

    b.owners = await owner_names(session, dataset_id, ds.created_by)
    b.distributions = await list_distributions(session, dataset_id)
    for src, tgt, sname, tname in (await session.execute(
        select(DatasetLineage.source_dataset_id, DatasetLineage.target_dataset_id,
               select(Dataset.name).where(Dataset.id == DatasetLineage.source_dataset_id).scalar_subquery(),
               select(Dataset.name).where(Dataset.id == DatasetLineage.target_dataset_id).scalar_subquery())
        .where(or_(DatasetLineage.source_dataset_id == dataset_id, DatasetLineage.target_dataset_id == dataset_id))
    )).all():
        if tgt == dataset_id and sname:
            b.upstream.append(sname)
        elif src == dataset_id and tname:
            b.downstream.append(tname)
    b.versions = await ai_ready.list_versions(session, dataset_id)
    if with_check:
        b.check = await ai_ready_check.evaluate_dataset(session, dataset_id)
    return b


def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _license_uri(b: _Bundle) -> str | None:
    code = b.eff("license")
    if code == "CUSTOM" or not code:
        return b.eff("license_url")
    return LICENSE_URIS.get(code) or b.eff("license_url")


def _languages(b: _Bundle) -> list[str]:
    return [x.strip() for x in str(b.eff("data_language") or "").split(",") if x.strip()]


def _title(b: _Bundle) -> str:
    return b.ds.display_name or b.ds.name


def _bool(v) -> bool | None:
    if v is None or str(v).strip() == "":
        return None
    return str(v).lower() == "true"


# ---------------------------------------------------------------------------
# DCAT
# ---------------------------------------------------------------------------

def _dcat_distribution(d: DatasetDistribution, base: str, ds_id: int) -> dict:
    out: dict = {
        "@id": f"{base}api/v1/external/datasets/{ds_id}/dcat#distribution-{d.id}",
        "@type": "dcat:Distribution",
        "dcat:accessURL": {"@id": d.access_url},
    }
    if d.title:
        out["dct:title"] = d.title
    if d.description:
        out["dct:description"] = d.description
    if d.format:
        out["dct:format"] = d.format
    mt = media_type(d.format, d.media_type)
    if mt:
        out["dcat:mediaType"] = mt
    if d.download_url:
        out["dcat:downloadURL"] = {"@id": d.download_url}
    if d.byte_size is not None:
        out["dcat:byteSize"] = {"@value": str(d.byte_size), "@type": "xsd:nonNegativeInteger"}
    if d.checksum:
        out["spdx:checksum"] = {"@type": "spdx:Checksum", "spdx:algorithm": d.checksum_algorithm,
                                "spdx:checksumValue": d.checksum}
    return out


def _dcat_dataset(b: _Bundle, base: str) -> dict:
    ds = b.ds
    out: dict = {
        "@id": f"{base}api/v1/external/datasets/{ds.id}/dcat",
        "@type": "dcat:Dataset",
        "dct:title": _title(b),
        "dct:identifier": ds.persistent_id or ds.urn,
        "adms:identifier": ds.urn,
    }

    def put(key: str, value):
        if value is None or value == [] or (isinstance(value, str) and not value.strip()):
            return
        out[key] = value

    put("dct:description", ds.description or ds.summary)
    put("dct:references", ds.legal_basis)
    put("dct:creator", {"@type": "foaf:Agent", "foaf:name": b.org_name} if b.org_name else None)
    pub = b.eff("publisher")
    put("dct:publisher", {"@type": "foaf:Agent", "foaf:name": pub} if pub else None)
    put("dcat:landingPage", {"@id": ds.landing_page} if ds.landing_page else None)
    freq = str(ds.ingestion_frequency or "").upper()
    put("dct:accrualPeriodicity", {"@id": FREQUENCY_URIS[freq]} if freq in FREQUENCY_URIS else None)
    put("dcat:theme", b.themes)
    put("dcat:keyword", b.tags)
    email = b.eff("contact_email")
    if email:
        out["dcat:contactPoint"] = {"@type": "vcard:Kind", "vcard:fn": pub or b.org_name or "문의",
                                    "vcard:hasEmail": {"@id": f"mailto:{email}"}}
    put("dct:language", [{"@id": LANG_URI + lang} for lang in _languages(b)])
    put("dcat:byteSize", {"@value": str(ds.byte_size), "@type": "xsd:nonNegativeInteger"} if ds.byte_size is not None else None)
    put("dcat:mediaType", media_type(ds.data_format))
    if ds.temporal_start or ds.temporal_end:
        t: dict = {"@type": "dct:PeriodOfTime"}
        if ds.temporal_start:
            t["dcat:startDate"] = {"@value": _iso(ds.temporal_start), "@type": "xsd:date"}
        if ds.temporal_end:
            t["dcat:endDate"] = {"@value": _iso(ds.temporal_end), "@type": "xsd:date"}
        out["dct:temporal"] = t
    put("dct:spatial", ds.spatial_coverage)
    put("dct:relation", sorted(set(b.upstream + b.downstream)))
    put("prov:wasDerivedFrom", b.upstream)
    # 계보 (표 10)
    put("owl:versionInfo", ds.data_version)
    put("adms:versionNotes", ds.version_notes)
    put("dct:issued", {"@value": _iso(ds.created_at), "@type": "xsd:dateTime"} if ds.created_at else None)
    modified = ds.last_ingested_at or ds.updated_at
    put("dct:modified", {"@value": _iso(modified), "@type": "xsd:dateTime"} if modified else None)
    # 이용 (표 11)
    lic = _license_uri(b)
    put("dct:license", {"@id": lic} if lic else None)
    code = b.eff("license")
    rights = ds.rights or (ai_ready.LICENSES.get(code) if code and code != "CUSTOM" else None)
    put("dct:rights", rights)
    uses = [u for u in str(ds.allowed_uses or "").split(",") if u] if ds.allowed_uses is not None else None
    if uses is not None:
        out["argus:allowedUses"] = uses
    # 품질 (표 12)
    put("rai:dataBiases", ds.data_biases)
    put("rai:knownLimitations", ds.known_limitations)
    put("rai:dataCollectionMissingData", ds.missing_data_info)
    put("rai:dataSocialImpact", ds.social_impact)
    if ds.quality_score is not None:
        out["dqv:hasQualityMeasurement"] = {
            "@type": "dqv:QualityMeasurement", "dqv:isMeasurementOf": {"@id": "argus:qualityScore"},
            "dqv:value": {"@value": str(ds.quality_score), "@type": "xsd:integer"},
        }
    if b.check:
        req, rec = b.check["required"], b.check["recommended"]
        out["dqv:hasQualityAnnotation"] = {
            "@type": "dqv:QualityAnnotation",
            "dct:title": "AI-Ready 체크리스트 점검 (공공데이터의 인공지능 친화적 관리 가이드라인 V1.0 부록 4)",
            "argus:requiredRate": req["rate"], "argus:recommendedRate": rec["rate"],
            "argus:minRequirementsMet": b.check["min_requirements"]["met"],
        }
    syn, ann = _bool(ds.contains_synthetic), _bool(ds.contains_machine_annotation)
    if syn is not None:
        out["argus:containsSyntheticData"] = syn
    if ann is not None:
        out["argus:containsMachineAnnotation"] = ann
    put("argus:privacyMeasures", ds.privacy_measures)
    put("dcat:distribution", [_dcat_distribution(d, base, ds.id) for d in b.distributions])
    return out


async def build_dcat(session: AsyncSession, dataset_id: int, base: str) -> dict | None:
    """데이터셋 하나의 DCAT JSON-LD. ``base`` 는 요청 기준 URL(끝 ``/`` 포함)."""
    b = await _bundle(session, dataset_id)
    if b is None:
        return None
    return {"@context": DCAT_CONTEXT, **_dcat_dataset(b, base)}


async def build_dcat_catalog(session: AsyncSession, base: str, *, page: int = 1, size: int = 100) -> dict:
    """활성 데이터셋 전체의 DCAT 카탈로그(쪽 단위) — 포털이 수확(harvest)해 갈 수 있게."""
    total = (await session.execute(select(func.count()).select_from(Dataset).where(Dataset.status == "active"))).scalar_one()
    ids = [i for (i,) in (await session.execute(
        select(Dataset.id).where(Dataset.status == "active").order_by(Dataset.id).offset((page - 1) * size).limit(size)
    )).all()]
    datasets = []
    for i in ids:
        # 카탈로그 수확은 쪽마다 데이터셋이 많아 점검 결과(dqv:hasQualityAnnotation)는 빼고 만든다
        b = await _bundle(session, i, with_check=False)
        if b is not None:
            datasets.append(_dcat_dataset(b, base))
    defaults = await ai_ready.load_defaults(session)
    out = {
        "@context": DCAT_CONTEXT,
        "@id": f"{base}api/v1/external/dcat/catalog",
        "@type": "dcat:Catalog",
        "dct:title": "Argus Catalog",
        "dcat:dataset": datasets,
        "argus:page": page, "argus:size": size, "argus:total": total,
    }
    if defaults.get("publisher"):
        out["dct:publisher"] = {"@type": "foaf:Agent", "foaf:name": defaults["publisher"]}
    if page * size < total:
        out["argus:next"] = f"{base}api/v1/external/dcat/catalog?page={page + 1}&size={size}"
    return out


# ---------------------------------------------------------------------------
# Croissant
# ---------------------------------------------------------------------------

async def build_croissant(session: AsyncSession, dataset_id: int, base: str) -> dict | None:
    """Croissant 1.0 JSON-LD(+RAI)."""
    b = await _bundle(session, dataset_id)
    if b is None:
        return None
    ds = b.ds
    out: dict = {
        "@context": CROISSANT_CONTEXT,
        "@type": "sc:Dataset",
        "conformsTo": "http://mlcommons.org/croissant/1.0",
        "name": _title(b),
        "description": ds.description or ds.summary or _title(b),
        "url": ds.landing_page or f"{base}api/v1/external/datasets/{ds.id}/croissant",
        "identifier": ds.persistent_id or ds.urn,
    }

    def put(key: str, value):
        if value is None or value == [] or (isinstance(value, str) and not value.strip()):
            return
        out[key] = value

    put("license", _license_uri(b))
    put("version", ds.data_version)
    put("datePublished", _iso(ds.created_at))
    put("dateModified", _iso(ds.last_ingested_at or ds.updated_at))
    put("creator", {"@type": "sc:Organization", "name": b.org_name} if b.org_name else None)
    pub = b.eff("publisher")
    put("publisher", {"@type": "sc:Organization", "name": pub} if pub else None)
    put("keywords", b.tags + b.themes)
    put("inLanguage", _languages(b))
    if ds.temporal_start or ds.temporal_end:
        out["temporalCoverage"] = f"{_iso(ds.temporal_start) or '..'}/{_iso(ds.temporal_end) or '..'}"
    put("spatialCoverage", ds.spatial_coverage)
    # RAI
    put("rai:dataBiases", ds.data_biases)
    put("rai:dataLimitations", ds.known_limitations)
    put("rai:dataCollectionMissingData", ds.missing_data_info)
    put("rai:personalSensitiveInformation", ds.privacy_measures)
    put("rai:dataSocialImpact", ds.social_impact)
    syn = _bool(ds.contains_synthetic)
    if syn is not None:
        out["rai:hasSyntheticData"] = syn
    uses = [ai_ready.ALLOWED_USES.get(u, u) for u in str(ds.allowed_uses or "").split(",") if u]
    put("rai:dataUseCases", ", ".join(uses) if uses else None)

    dists = []
    for d in b.distributions:
        fo: dict = {"@type": "cr:FileObject", "@id": f"distribution-{d.id}", "name": d.title or f"distribution-{d.id}",
                    "contentUrl": d.download_url or d.access_url}
        mt = media_type(d.format, d.media_type)
        if mt:
            fo["encodingFormat"] = mt
        if d.byte_size is not None:
            fo["contentSize"] = f"{d.byte_size} B"
        if d.checksum and d.checksum_algorithm == "SHA-256":
            fo["sha256"] = d.checksum
        elif d.checksum and d.checksum_algorithm == "MD5":
            fo["md5"] = d.checksum
        dists.append(fo)
    put("distribution", dists)

    rs_id = ds.name.rsplit(".", 1)[-1] or f"dataset-{ds.id}"
    # 원천이 표 형식이면 첫 배포본에서 컬럼을 읽는다 — 표 형식 배포본이 없으면 source 를 두지 않는다
    tabular = next((f"distribution-{d.id}" for d in b.distributions
                    if str(d.format or "").upper() in ("CSV", "TSV", "PARQUET", "JSONL", "JSON")), None)
    fields = []
    for c in b.columns:
        f: dict = {"@type": "cr:Field", "@id": f"{rs_id}/{c.field_path}", "name": c.field_path,
                   "dataType": croissant_type(c.field_type)}
        # 데이터 사전 — 단위는 schema.org unitText, 산정기준은 Croissant 에 맞는 속성이 없어 설명 뒤에 붙인다
        desc = c.description or c.display_name
        if c.calculation:
            desc = f"{desc}\n산정기준: {c.calculation}" if desc else f"산정기준: {c.calculation}"
        if desc:
            f["description"] = desc
        if c.unit:
            f["sc:unitText"] = c.unit
        if tabular:
            f["source"] = {"fileObject": {"@id": tabular}, "extract": {"column": c.field_path}}
        fields.append(f)
    if fields:
        rs: dict = {"@type": "cr:RecordSet", "@id": rs_id, "name": rs_id, "field": fields}
        keys = [{"@id": f"{rs_id}/{c.field_path}"} for c in b.columns if str(c.is_primary_key).lower() == "true"]
        if keys:
            rs["key"] = keys if len(keys) > 1 else keys[0]
        out["recordSet"] = [rs]
    return out


# ---------------------------------------------------------------------------
# 데이터 카드 (부록 5)
# ---------------------------------------------------------------------------

_TODO = "_작성 필요 — {where}_"


def _md(v) -> str:
    """표 칸에 넣을 수 있게 줄바꿈·파이프를 정리한다."""
    return str(v).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _cell(v, where: str) -> str:
    if v is None or (isinstance(v, str) and not v.strip()) or v == []:
        return _TODO.format(where=where)
    if isinstance(v, list):
        return _md(", ".join(str(x) for x in v))
    return _md(v)


def _strip_html(text: str | None) -> str | None:
    if not text:
        return text
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


async def build_data_card(session: AsyncSession, dataset_id: int, base: str) -> str | None:
    """가이드라인 부록 5 데이터 카드(Markdown)."""
    b = await _bundle(session, dataset_id)
    if b is None:
        return None
    ds = b.ds
    meta = "메타데이터 탭"
    lines: list[str] = [f"# 데이터 카드 — {_md(_title(b))}", ""]
    gen = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    lines += [f"> Argus Catalog 이 메타데이터에서 자동으로 만든 카드입니다({gen}). "
              "「공공데이터의 인공지능 친화적 관리 가이드라인」 V1.0 부록 5 의 항목 순서를 따릅니다.", ""]

    def section(title: str, rows: list[tuple[str, str]]):
        lines.extend([f"## {title}", "", "| 항목 | 내용 |", "|---|---|"])
        lines.extend(f"| {k} | {v} |" for k, v in rows)
        lines.append("")

    contact = b.eff("contact_email")
    owners = ", ".join(f"{n}({t})" for n, t in b.owners) or None
    freq = str(ds.ingestion_frequency or "").upper()
    freq_text = FREQUENCY_LABELS.get(freq, ds.ingestion_cron or ds.ingestion_frequency)
    if freq_text and ds.freshness_sla:
        freq_text = f"{freq_text} (최신성 기준: {ds.freshness_sla})"
    period = None
    if ds.temporal_start or ds.temporal_end:
        period = f"{_iso(ds.temporal_start) or '?'} ~ {_iso(ds.temporal_end) or '?'}"
    scope = "; ".join(x for x in (f"기간 {period}" if period else None,
                                  f"공간 {ds.spatial_coverage}" if ds.spatial_coverage else None) if x) or None
    access = [f"{d.title or d.format or '배포본'}: {d.download_url or d.access_url}" for d in b.distributions]
    section("데이터셋 개요", [
        ("데이터셋명/ID", _md(f"{_title(b)} / {ds.persistent_id or ds.urn}")),
        ("발행기관/담당부서/연락처", _cell(" / ".join(x for x in (b.eff("publisher"), b.org_name, contact) if x) or None,
                                         f"{meta} '제공기관'·'문의 연락처', 데이터 소스의 시스템 조직")),
        ("데이터셋 요약", _cell(_strip_html(ds.description) or ds.summary, "개요의 설명")),
        ("지원 테스크(활용 목적)", _cell(", ".join(ai_ready.ALLOWED_USES.get(u, u) for u in str(ds.allowed_uses or "").split(",") if u) or None,
                                       f"{meta} '허용 이용 유형'")),
        ("언어 정보", _cell(_languages(b), f"{meta} '언어'")),
        ("대상 범위", _cell(scope, f"{meta} '시간범위'·'공간범위'")),
        ("갱신 정보", _cell(freq_text, f"{meta} '갱신주기'")),
        ("제공 형태", _cell(access, f"{meta} '배포본'")),
    ])

    pks = [c.field_path for c in b.columns if str(c.is_primary_key).lower() == "true"]
    fmt = ", ".join(x for x in (ds.data_format, ds.encoding, f"압축 {ds.compression}" if ds.compression else None) if x) or None
    section("데이터셋 구조", [
        ("데이터 인스턴스(샘플)", "샘플 레코드는 카드에 넣지 않습니다 — Argus 데이터셋 상세의 샘플 탭(권한 필요)에서 봅니다."),
        ("데이터 필드 정의", f"아래 '필드 정의' 표 ({len(b.columns)}개 컬럼)" if b.columns else _TODO.format(where="스키마 수집")),
        ("키/식별자 규칙", _cell(f"기본키: {', '.join(pks)}" if pks else None, "스키마 탭의 기본키")),
        ("결측·특수값 규칙", _cell(ds.missing_value_notation, f"{meta} '결측·특수값 표기'")),
        ("분할 정보(Splits)", "해당 없음(원천 데이터 카탈로그는 학습/검증 분할을 제공하지 않음)"),
        ("포맷·인코딩·압축", _cell(fmt, f"{meta} '데이터 형식'·'인코딩'")),
        ("기계 이해 스키마 제공", _md(f"DCAT: {base}api/v1/external/datasets/{ds.id}/dcat , "
                                     f"Croissant: {base}api/v1/external/datasets/{ds.id}/croissant")),
    ])

    source = f"데이터 소스 {b.datasource_name}" if b.datasource_name else None
    if b.upstream:
        source = f"{source or ''}; 상류 데이터셋 {', '.join(b.upstream)}".strip("; ")
    qual = None
    if ds.quality_score is not None:
        qual = f"품질 점수 {ds.quality_score}점 (Argus 품질 진단)"
    section("데이터셋 생성", [
        ("수집 배경", _cell(ds.legal_basis, f"{meta} '관련법령'")),
        ("데이터 소스(출처)", _cell(source, "리니지")),
        ("정제·전처리 과정", _cell(ds.version_notes, f"{meta} '버전 노트'")),
        ("비식별 보호 조치", _cell(ds.privacy_measures if str(ds.contains_pii or '').lower() != "false" else "개인정보 없음",
                                f"{meta} '개인정보 보호조치'")),
        ("품질관리 절차", _cell(qual, "품질 탭의 진단")),
    ])

    uses = ", ".join(ai_ready.ALLOWED_USES.get(u, u) for u in str(ds.allowed_uses or "").split(",") if u) or None
    bias_limit = "; ".join(x for x in (ds.data_biases, ds.known_limitations) if x) or None
    section("사용 시 고려사항", [
        ("권장·사용 범위", _cell(uses, f"{meta} '허용 이용 유형'")),
        ("비권장/주의 사용", _cell(ds.rights, f"{meta} '저작권·이용 조건'")),
        ("사회적 영향", _cell(ds.social_impact, f"{meta} '사회적 영향'")),
        ("편향성 및 한계", _cell(bias_limit, f"{meta} '알려진 편향'·'한계'")),
        ("기술적 제약", _cell(ds.missing_data_info, f"{meta} '결측 정보'")),
        ("리스크 완화 권고", _cell(ds.risk_mitigation, f"{meta} '리스크 완화 권고'")),
    ])

    code = b.eff("license")
    lic = None
    if code:
        lic = ai_ready.LICENSES.get(code, code) + (f" ({_license_uri(b)})" if _license_uri(b) else "")
    section("추가 정보 및 라이선스", [
        ("라이선스", _cell(lic, f"{meta} '라이선스'")),
        ("작성자/관리자 정보", _cell(owners, "메타데이터 탭의 소유자")),
        ("버전/변경 이력", _cell(" — ".join(x for x in (ds.data_version, ds.version_notes) if x) or None,
                               f"{meta} '버전'·'버전 노트'")),
        ("관련 문서", _cell(ds.landing_page, f"{meta} '제공시스템 URL'")),
    ])
    # 기술적 사양(재현성·환경 설정·매개변수/규칙)은 학습용으로 가공·생성한 데이터의 파이프라인을 다시 돌리는 정보다.
    # 원천 데이터를 등록하는 카탈로그에는 해당하지 않는 경우가 많아 칸마다 "작성 필요"를 두지 않고 안내 한 줄만 둔다(TODO-150).
    lines += ["## 기술적 사양", "",
              "해당하는 경우에만 작성합니다 — 학습용으로 가공·생성한 데이터라면 재현 방법(전처리·생성 파이프라인), "
              "실행 환경, 매개변수·규칙을 함께 제공하세요. 정제·전처리 과정은 '데이터셋 생성' 절을 봅니다.", ""]

    if b.columns:
        lines += ["## 필드 정의", "", "| 컬럼 | 논리명 | 타입 | 단위 | NULL | 키 | 설명 | 산정기준 |",
                  "|---|---|---|---|---|---|---|---|"]
        for c in b.columns:
            key = "PK" if str(c.is_primary_key).lower() == "true" else ""
            lines.append(f"| {_md(c.field_path)} | {_md(c.display_name or '')} | {_md(c.native_type or c.field_type)} | "
                         f"{_md(c.unit or '')} | {'Y' if str(c.nullable).lower() == 'true' else 'N'} | {key} | "
                         f"{_md(c.description or '')} | {_md(c.calculation or '')} |")
        lines.append("")

    if b.versions:
        lines += ["## 버전 이력", "", "| 버전 | 변경 사항 | 담당자 | 일시 |", "|---|---|---|---|"]
        for v in b.versions:
            lines.append(f"| {_md(v['version'])} | {_md(v['notes'] or '')} | {_md(v['created_by'] or '')} | "
                         f"{_iso(v['created_at']) or ''} |")
        lines.append("")

    if b.check:
        req, rec = b.check["required"], b.check["recommended"]
        lines += ["## AI-Ready 점검", "",
                  f"- 필수 항목 충족률: {req['rate'] if req['rate'] is not None else '-'}% ({req['passed']}/{req['total']})",
                  f"- 권장 항목 충족률: {rec['rate'] if rec['rate'] is not None else '-'}% ({rec['passed']}/{rec['total']})",
                  f"- 공유 최소 요건: {'충족' if b.check['min_requirements']['met'] else '미충족'}", ""]
    return "\n".join(lines)


__all__ = [
    "MEDIA_TYPES", "CHECKSUM_ALGORITHMS", "media_type", "croissant_type", "normalize_distribution", "distribution_dict",
    "list_distributions", "create_distribution", "update_distribution", "delete_distribution", "reorder_distributions",
    "build_dcat", "build_dcat_catalog", "build_croissant", "build_data_card",
]
