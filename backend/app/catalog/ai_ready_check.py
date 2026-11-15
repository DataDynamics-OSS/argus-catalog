# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 점검 — 「공공데이터의 인공지능 친화적 관리 가이드라인」 V1.0 부록 4 체크리스트 판정.

설계: design/ai-ready-public-data-guideline-review.md §5.1(항목별 판정 방식)·§6.2, TODO-096 2단계.

- 항목은 공통 33개(C01~C33, 필수 15·권장 18)와 유형별 5개(T1~T5). 표기는 가이드라인과 같게 적정(PASS)·미흡(FAIL)·해당없음(NA).
- 자동 판정은 저장하지 않고 조회할 때 카탈로그가 가진 정보(메타데이터·스키마·품질·표준·리니지)로 계산한다.
  계산할 수 없는 항목은 ``None``(판정 필요)이다.
- 수동 판정(``AiReadyJudgment``)이 있으면 자동 판정보다 우선한다. 지우면 자동 판정으로 돌아간다.
- 충족률은 필수·권장을 따로 낸다 — 적정 수 / (항목 수 − 해당없음). 판정 필요는 적정이 아니므로 분모에만 들어간다.
- 최소 요건(가이드라인 §3.1.1.2 의 공유 요건 4개 — 결측률·필수 메타데이터·갱신 주기·표준 포맷)은 별도 배지로 판정한다.

목록(관리 화면)에서 데이터셋 수백 개를 한 번에 판정하므로, 판정 재료는 ``_load_context`` 가 데이터셋 묶음 단위로 모아 온다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import ai_ready
from app.catalog.models import (
    AiReadyJudgment,
    Dataset,
    DatasetCategory,
    DatasetLineage,
    DatasetSchema,
    DatasetTag,
    Datasource,
    Organization,
    Owner,
    SchemaSnapshot,
    System,
)

logger = logging.getLogger(__name__)

PASS, FAIL, NA = "PASS", "FAIL", "NA"
STATUSES = (PASS, FAIL, NA)
REQUIRED, RECOMMENDED = "REQUIRED", "RECOMMENDED"

# 판정 방식 — 자동(카탈로그 정보로 계산), 수동(담당자가 판정), 밖(제공 플랫폼의 일 — 담당자가 판정)
AUTO, MANUAL, EXTERNAL = "AUTO", "MANUAL", "EXTERNAL"


@dataclass(frozen=True)
class Item:
    key: str
    group: str
    level: str
    text: str          # 가이드라인 점검 내용(요약하지 않은 문장)
    mode: str
    hint: str = ""     # Argus 에서 무엇으로 판정하는지·어디를 채우면 되는지


# 부록 4 [공통] — 순서와 문장은 가이드라인 그대로.
COMMON_ITEMS: tuple[Item, ...] = (
    Item("C01", "형식 및 구조", REQUIRED, "데이터를 특정 소프트웨어에 종속되지 않는 개방형 표준 포맷으로 제공하고 있는가?", AUTO,
         "메타데이터 탭 '데이터 형식'"),
    Item("C02", "형식 및 구조", REQUIRED, "데이터를 기계 이해 및 자동 처리가 가능하도록 구조화된 형태로 제공하고 있는가?", AUTO,
         "메타데이터 탭 '데이터 유형'(정형·반정형), 스키마"),
    Item("C03", "형식 및 구조", REQUIRED, "데이터 구조(스키마)는 필드(키)·자료형·관계·중첩 규칙등을 포함하여 명확히 정의되어 있는가?", AUTO,
         "스키마 탭 — 컬럼·자료형·기본키"),
    Item("C04", "형식 및 구조", REQUIRED, "메타데이터를 표준 스키마(예: DCAT)또는 기관·국가 적용 프로파일에 따라 기계 이해 가능한 형태로 제공하고 있는가?", AUTO,
         "외부 API 의 DCAT·Croissant JSON-LD(AI-Ready 탭 '내보내기')"),
    Item("C05", "형식 및 구조", REQUIRED, "메타데이터 작성 기준에서 정의한 필수 항목을 누락 없이 제공하고 있는가?", AUTO,
         "메타데이터 탭 — 설명·관련법령·제공기관·제공시스템·갱신주기·주제분류·키워드·연락처·언어·용량·형식·버전·라이선스, AI-Ready 탭 '배포본'"),
    Item("C06", "형식 및 구조", RECOMMENDED, "데이터 검증 기준(예: 허용값, 필수값, 형식, 관계 제약 등)을 제공하고 있는가?", AUTO,
         "품질 탭의 활성 규칙"),
    Item("C07", "형식 및 구조", RECOMMENDED, "대용량 데이터는 압축 제공 또는 대용량 처리에 적합한 포맷(예: Parquet 등)으로 제공하여 이용자가 용이하게 다운로드·활용할 수 있는가?", AUTO,
         "용량이 기준(1GB) 이상이면 압축 또는 Parquet·ORC·Avro"),
    Item("C08", "의미 및 표준", REQUIRED, "변수(항목)별 정의·단위·산정기준·코드값 의미를 포함하는 데이터 사전(또는 이에 준하는 문서)을 제공하고 있는가?", AUTO,
         "스키마 탭 컬럼 설명 채움률(80% 이상)"),
    Item("C09", "의미 및 표준", REQUIRED, "날짜·시간·코드 등 주요 값의 표기 형식은 표준 포맷에 따라 일관되게 적용되고 있는가?", AUTO,
         "품질 규칙 DATE_FORMAT·ACCEPTED_VALUES 의 최근 결과"),
    Item("C10", "의미 및 표준", REQUIRED, "동일한 개념은 동일한 라벨·용어로 일관되게 사용하고 있는가?", AUTO,
         "표준 용어 탭 — 컬럼 매핑과 위반(VIOLATION)"),
    Item("C11", "의미 및 표준", RECOMMENDED, "적용한 표준 포맷 및 적용 기준(예: 날짜·시간 형식, 코드 체계, 누락 값 표기 등)을 문서화하여 제공하고 있는가?", AUTO,
         "결측·특수값 표기 규칙 + 표준 용어 매핑 또는 형식·코드 규칙"),
    Item("C12", "의미 및 표준", RECOMMENDED, "도메인 표준 용어체계 또는 온톨로지가 존재하는 경우, 해당 표준과의 정합성 점검 절차(매핑·검증 절차 포함)을 마련·적용하고 있는가?", MANUAL,
         "외부 온톨로지 매핑은 Argus 밖 — 절차 문서를 근거로"),
    Item("C13", "품질 및 이력", REQUIRED, "결측값 및 특수값 표기 규칙을 통일하여 일관되게 적용하고 있는가?", AUTO,
         "메타데이터 탭 '결측·특수값 표기'"),
    Item("C14", "품질 및 이력", REQUIRED, "데이터 품질평가의 수행 여부 및 결과 내용을 메타데이터에 명시하고 있는가?", AUTO,
         "품질 점수(품질 진단 결과)"),
    Item("C15", "품질 및 이력", REQUIRED, "인공적으로 생성된 합성 데이터나 기계가 자동으로 단 주석이 포함된 경우 이를 명확히 구분하여 표기했는가?", AUTO,
         "메타데이터 탭 '합성 데이터 포함'·'기계 자동 주석 포함'"),
    Item("C16", "품질 및 이력", RECOMMENDED, "결측값 및 특수값의 정의, 발생 사유, 처리 원칙(예: 제외, 대체, 별도 코드 처리 등)의 정보를 제공하고 있는가?", AUTO,
         "메타데이터 탭 '결측 정보'(프로파일에서 채우기)"),
    Item("C17", "품질 및 이력", RECOMMENDED, "데이터의 원천·수집경로·가공·전처리(정제·비식별) 이력을 기록·관리하고 공개 가능한 범위에서 정보를 제공하는가?", AUTO,
         "데이터 소스 수집 또는 상류 리니지"),
    Item("C18", "품질 및 이력", RECOMMENDED, "데이터 정제 및 변환에 사용된 소스 코드는 라이선스를 명시하여, 공개 가능한 범위 내 오픈소스 형태로 제공하고 있는가?", MANUAL,
         "공개 저장소 링크를 근거로"),
    Item("C19", "품질 및 이력", RECOMMENDED, "데이터, 메타데이터 및 핵심 문서(스키마/데이터사전·코드북/접근 안내)는 이용자가 동일한 배포 경로에서 함께 확인할 수 있도록 제공하고 있는가?", EXTERNAL,
         "배포는 제공 플랫폼(포털)의 일"),
    Item("C20", "품질 및 이력", RECOMMENDED, "데이터 또는 문서가 변경되는 경우, 관련 메타데이터 및 핵심 문서를 동시에 개정·현행화하고 있는가?", AUTO,
         "최근 스키마 변경에서 추가·변경된 컬럼의 설명"),
    Item("C21", "품질 및 이력", RECOMMENDED, "데이터 특성(용도·품질·리스크 등)을 요약한 데이터 카드(또는 이에 준하는 설명 문서)를 함께 제공하고 있는가?", AUTO,
         "자동 생성 데이터 카드 — 설명과 편향·한계가 있어야 내용이 찹니다"),
    Item("C22", "관리 및 보안", REQUIRED, "개인정보(또는 민감정보)가 포함된 경우, 관련 법령·지침에 따라 비식별/가명처리 등 보호조치를 적용하고 있는가?", AUTO,
         "메타데이터 탭 '개인정보 포함'·컬럼 개인정보 분류·'개인정보 보호조치'"),
    Item("C23", "관리 및 보안", REQUIRED, "이용허락 범위(재사용, 재배포, 상업적 이용, 2차 저작물 작성 등)를 라이선스로 명확히 표시하고, 해당 라이선스 정보를 메타데이터로 제공하고 있는가?", AUTO,
         "메타데이터 탭 '라이선스'(또는 설정의 기관 기본값)"),
    Item("C24", "관리 및 보안", RECOMMENDED, "재식별 위험에 대한 평가·검토를 수행하고, 그 결과 및 조치 사항을 문서화하여 관리하고 있는가?", MANUAL,
         "개인정보가 없으면 해당없음. 평가 문서를 근거로"),
    Item("C25", "관리 및 보안", RECOMMENDED, "데이터에 영구 식별자(PID 또는 이에 준하는 지속 식별자)를 부여하여 관리하고 있는가?", AUTO,
         "메타데이터 탭 '영구 식별자'"),
    Item("C26", "데이터 제공", RECOMMENDED, "데이터는 이용자의 자동 수집 및 연계를 지원할 수 있도록 웹 기반 표준 API(예: RESTful API) 형태로 제공하고 있는가?", EXTERNAL,
         "API 카탈로그에서 이 데이터셋을 리니지 대상으로 연결하면 '적정'"),
    Item("C27", "데이터 제공", RECOMMENDED, "데이터가 API로 제공되는 경우, 이용 편의 및 장애 대응을 위해 파일 다운로드 등 최소 1가지 이상의 추가 제공 경로를 제공하고 있는가?", EXTERNAL,
         "AI-Ready 탭 '배포본'에 내려받기 URL 이 있으면 '적정'. 제공은 포털의 일"),
    Item("C28", "데이터 제공", RECOMMENDED, "API 제공 시 데이터 응답에는 필요한 메타데이터를 함께 포함하거나, 메타데이터를 확인할 수 있는 표준화된 참조 링크를 함께 제공하도록 설계되어 있는가?", EXTERNAL,
         "데이터 API 쪽 구현"),
    Item("C29", "데이터 제공", RECOMMENDED, "대용량 데이터 처리 및 자동 수집을 위해 필터링, 조건 검색, 분할 조회(페이지네이션) 등 효율적인 조회 기능을 제공하고 있는가?", EXTERNAL,
         "데이터 API 쪽 구현"),
    Item("C30", "관리 유지", REQUIRED, "품질·보안·윤리·업데이트에 대한 책임 주체(역할 기준)를 명확히 하고 이용자 문의/오류 신고 연락 창구를 명시하고 운영하는가?", AUTO,
         "소유자 지정 + 문의 연락처(또는 설정의 기관 기본값)"),
    Item("C31", "관리 유지", REQUIRED, "이용자 오류 신고 및 개선 요청을 접수·처리할 수 있는 절차를 마련하고, 피드백 체계를 운영하고 있는가?", AUTO,
         "댓글 탭의 오류 신고·개선 요청 — 처리 상태와 기한(설정 › AI-Ready). 기한 넘긴 미처리 신고가 있으면 '미흡'"),
    Item("C32", "관리 유지", RECOMMENDED, "데이터 변경 사항은 버전별로 추적 가능하도록 변경 이력 정보를 제공하는가?", AUTO,
         "메타데이터 탭 '버전'·'버전 노트' — 바꿀 때마다 버전 이력에 남음"),
    Item("C33", "관리 유지", RECOMMENDED, "데이터의 변조 방지 및 신뢰성 확인을 위한 전자서명이나 해시(Hash)값 제공 등 기술적 검증 방식 운영하는가?", EXTERNAL,
         "AI-Ready 탭 '배포본'에 체크섬을 적으면 '적정'. 전자서명은 배포본을 만드는 쪽의 일"),
)

# 부록 4 [유형별] — 데이터 유형에 맞지 않으면 해당없음.
TYPE_ITEMS: tuple[Item, ...] = (
    Item("T1", "유형별 · 수치", RECOMMENDED, "산식/집계 단위/기준시점 등 산출 규칙을 제공하는가?", MANUAL,
         "숫자 컬럼이 있는 정형·반정형 데이터"),
    Item("T2", "유형별 · 이미지/영상", REQUIRED, "수집 환경(장비/센서·조건·조건)메타정보를 제공하는가?", MANUAL,
         "데이터 형식이 이미지·영상이거나 비정형일 때"),
    Item("T3", "유형별 · 이미지/영상", REQUIRED, "라벨링 가이드라인·검증 절차 정보를 제공하는가?", MANUAL,
         "데이터 형식이 이미지·영상이거나 비정형일 때"),
    Item("T4", "유형별 · 개인정보 보호", REQUIRED, "얼굴, 차량번호, 목소리 등 민감 정보의 마스킹/익명화 처리를 완료하고, 재식별 위험성 평가를 거쳤는가?", MANUAL,
         "비정형 데이터에 개인정보가 있을 때"),
    Item("T5", "유형별 · 개인정보 보호", RECOMMENDED, "비식별·가공으로 인해 제공 정보의 공백/왜곡 가능성이 발생하는 항목에 대해, 가공 범위·원칙·주의사항을 이용자에게 명확히 안내하는가?", AUTO,
         "개인정보가 있을 때 '개인정보 보호조치'"),
)

ITEMS: tuple[Item, ...] = COMMON_ITEMS + TYPE_ITEMS
ITEM_BY_KEY: dict[str, Item] = {i.key: i for i in ITEMS}

# 목록 · 배지용 짧은 이름 — 가이드라인 문장은 길어 표에 넣을 수 없어 번호 옆에 붙인다(문장은 ``text`` 그대로).
ITEM_SHORT: dict[str, str] = {
    "C01": "개방형 포맷", "C02": "구조화", "C03": "스키마 정의", "C04": "표준 메타데이터", "C05": "필수 메타데이터",
    "C06": "검증 기준", "C07": "대용량 포맷", "C08": "데이터 사전", "C09": "표기 형식", "C10": "용어 일관성",
    "C11": "표준 적용 문서", "C12": "온톨로지 정합", "C13": "결측값 표기", "C14": "품질평가 명시", "C15": "합성 · 자동주석 표기",
    "C16": "결측값 처리 안내", "C17": "원천 · 가공 이력", "C18": "정제 코드 공개", "C19": "문서 한곳 제공", "C20": "문서 현행화",
    "C21": "데이터 카드", "C22": "개인정보 보호", "C23": "라이선스", "C24": "재식별 위험 평가", "C25": "영구 식별자",
    "C26": "표준 API", "C27": "추가 제공 경로", "C28": "API 메타데이터", "C29": "효율적 조회", "C30": "책임 주체 · 연락처",
    "C31": "오류 신고 절차", "C32": "변경 이력", "C33": "무결성 검증",
    "T1": "산출 규칙", "T2": "수집 환경", "T3": "라벨링 기준", "T4": "민감정보 마스킹", "T5": "가공 안내",
}

# 개방형 표준 포맷 — 특정 소프트웨어에 종속되지 않는 형식(가이드라인 §3.1 표 3 예시: CSV·JSON·XML·Parquet 등).
OPEN_FORMATS = {"CSV", "TSV", "JSON", "JSONL", "NDJSON", "XML", "PARQUET", "ORC", "AVRO", "TXT", "TEXT",
                "GEOJSON", "RDF", "JSON-LD", "ICEBERG", "DELTA", "PNG", "JPEG", "JPG", "TIFF", "WAV", "FLAC",
                "MP4", "WEBM", "PDF"}
# 대용량 처리에 맞는 열 지향·직렬화 포맷
BULK_FORMATS = {"PARQUET", "ORC", "AVRO", "ICEBERG", "DELTA"}
MEDIA_FORMATS = {"IMAGE", "VIDEO", "AUDIO", "PNG", "JPEG", "JPG", "TIFF", "MP4", "WEBM", "WAV", "FLAC"}
LARGE_BYTES = 1024 ** 3                 # C07 '대용량' 기준 1GB
DICTIONARY_FILL_PCT = 80.0              # C08 컬럼 설명 채움률 기준
NUMERIC_HINTS = ("int", "decimal", "numeric", "number", "float", "double", "real", "long", "short", "money")

# 갱신 주기(시간) — 최소 요건 '갱신 주기 N회 이내'. 실시간·수동·cron 은 판정하지 않는다.
FREQUENCY_HOURS = {"HOURLY": 1, "DAILY": 24, "WEEKLY": 24 * 7, "MONTHLY": 24 * 31}

# 필수 메타데이터(가이드라인 §3.2 표 9~12 의 '필수'). 고유식별자는 URN 이, 데이터명은 이름이, 등록·수정일은 생성·수정 시각이
# 늘 있어 뺀다.
REQUIRED_METADATA: tuple[tuple[str, str], ...] = (
    ("description", "설명"),
    ("legal_basis", "관련법령"),
    ("creator", "소관기관(데이터 소스의 시스템 조직)"),
    ("publisher", "제공기관"),
    ("landing_page", "제공시스템 URL"),
    ("ingestion_frequency", "갱신주기"),
    ("theme", "주제분류"),
    ("keyword", "키워드(태그)"),
    ("contact_email", "문의 연락처"),
    ("data_language", "언어"),
    ("distribution", "배포본(접속 URL)"),
    ("byte_size", "용량"),
    ("data_format", "데이터 형식"),
    ("data_version", "버전"),
    ("license", "라이선스"),
)


# ---------------------------------------------------------------------------
# 판정 재료 — 데이터셋 묶음 단위로 모은다
# ---------------------------------------------------------------------------

@dataclass
class _Facts:
    """데이터셋 하나의 판정 재료."""

    ds: Dataset
    datasource_name: str | None = None
    org_name: str | None = None
    columns: int = 0
    described: int = 0
    undescribed: set[str] = field(default_factory=set)              # 설명이 없는 컬럼 경로
    pk_columns: int = 0
    pii_columns: int = 0
    numeric_columns: int = 0
    owners: int = 0
    tags: int = 0
    categories: int = 0
    rule_types: dict[str, int] = field(default_factory=dict)        # 활성 규칙 유형별 수
    format_results: tuple[int, int] = (0, 0)                        # DATE_FORMAT·ACCEPTED_VALUES 최근 결과 (통과, 전체)
    has_score: bool = False
    term_mapped: int = 0
    term_violations: int = 0
    upstream: int = 0
    api_links: int = 0
    distributions: int = 0
    downloads: int = 0                                              # 내려받기 URL 이 있는 배포본 수
    checksums: int = 0                                              # 체크섬이 있는 배포본 수
    last_change: dict | None = None                                 # 최근 스키마 변경 {synced_at, fields: [추가·변경 컬럼]}
    missing_pct: float | None = None                                # 최신 프로파일의 전체 결측률
    profiled_at: datetime | None = None
    reports: dict[str, int] = field(default_factory=dict)           # 오류 신고·개선 요청 {total, open, overdue, closed}
    versions: int = 0                                               # 데이터 버전 이력 수
    judgments: dict[str, AiReadyJudgment] = field(default_factory=dict)


async def _load_context(session: AsyncSession, datasets: list[Dataset], *, with_judgments: bool = True) -> dict[int, _Facts]:
    from app.apis.models import CatalogApiLineage
    from app.quality.models import DataProfile, QualityResult, QualityRule, QualityScore
    from app.standard.models import TermColumnMapping

    facts = {d.id: _Facts(ds=d) for d in datasets}
    ids = list(facts)
    if not ids:
        return facts

    # 데이터 소스 이름과 소관기관(데이터 소스 → 시스템 → 조직)
    for ds_id, src_name, org_name in (await session.execute(
        select(Dataset.id, Datasource.name, Organization.name)
        .join(Datasource, Datasource.id == Dataset.datasource_id)
        .outerjoin(System, System.id == Datasource.system_id)
        .outerjoin(Organization, Organization.id == System.org_id)
        .where(Dataset.id.in_(ids))
    )).all():
        facts[ds_id].datasource_name = src_name
        facts[ds_id].org_name = org_name

    # 스키마 — 컬럼 수, 설명 채움, 기본키, 개인정보, 숫자 컬럼
    for ds_id, path, desc, pk, pii, ftype in (await session.execute(
        select(DatasetSchema.dataset_id, DatasetSchema.field_path, DatasetSchema.description,
               DatasetSchema.is_primary_key, DatasetSchema.pii_type, DatasetSchema.field_type)
        .where(DatasetSchema.dataset_id.in_(ids))
    )).all():
        f = facts[ds_id]
        f.columns += 1
        if desc and str(desc).strip():
            f.described += 1
        else:
            f.undescribed.add(path)
        if str(pk).lower() == "true":
            f.pk_columns += 1
        if pii and str(pii).strip():
            f.pii_columns += 1
        if any(h in str(ftype or "").lower() for h in NUMERIC_HINTS):
            f.numeric_columns += 1

    for model, attr in ((Owner, "owners"), (DatasetTag, "tags"), (DatasetCategory, "categories")):
        for ds_id, n in (await session.execute(
            select(model.dataset_id, func.count()).where(model.dataset_id.in_(ids)).group_by(model.dataset_id)
        )).all():
            setattr(facts[ds_id], attr, n)

    # 활성 품질 규칙 유형별 수
    for ds_id, ctype, n in (await session.execute(
        select(QualityRule.dataset_id, QualityRule.check_type, func.count())
        .where(QualityRule.dataset_id.in_(ids), QualityRule.is_active == "true")
        .group_by(QualityRule.dataset_id, QualityRule.check_type)
    )).all():
        facts[ds_id].rule_types[ctype] = n

    # 표기 형식 규칙(DATE_FORMAT·ACCEPTED_VALUES)의 규칙별 최근 결과
    latest = (
        select(func.max(QualityResult.id).label("rid"))
        .join(QualityRule, QualityRule.id == QualityResult.rule_id)
        .where(QualityResult.dataset_id.in_(ids), QualityRule.is_active == "true",
               QualityRule.check_type.in_(("DATE_FORMAT", "ACCEPTED_VALUES")))
        .group_by(QualityResult.rule_id)
    ).subquery()
    counts: dict[int, list[int]] = {}
    for ds_id, passed in (await session.execute(
        select(QualityResult.dataset_id, QualityResult.passed).where(QualityResult.id.in_(select(latest.c.rid)))
    )).all():
        c = counts.setdefault(ds_id, [0, 0])
        c[1] += 1
        if str(passed).lower() == "true":
            c[0] += 1
    for ds_id, (ok, total) in counts.items():
        facts[ds_id].format_results = (ok, total)

    for (ds_id,) in (await session.execute(
        select(QualityScore.dataset_id).where(QualityScore.dataset_id.in_(ids)).distinct()
    )).all():
        facts[ds_id].has_score = True

    # 표준 용어 매핑과 위반
    for ds_id, mtype, n in (await session.execute(
        select(TermColumnMapping.dataset_id, TermColumnMapping.mapping_type, func.count())
        .where(TermColumnMapping.dataset_id.in_(ids))
        .group_by(TermColumnMapping.dataset_id, TermColumnMapping.mapping_type)
    )).all():
        facts[ds_id].term_mapped += n
        if mtype == "VIOLATION":
            facts[ds_id].term_violations += n

    # 상류 리니지(이 데이터셋으로 들어오는 엣지)
    for ds_id, n in (await session.execute(
        select(DatasetLineage.target_dataset_id, func.count())
        .where(DatasetLineage.target_dataset_id.in_(ids)).group_by(DatasetLineage.target_dataset_id)
    )).all():
        facts[ds_id].upstream = n

    # API 카탈로그 연결 — 리니지 대상이 이 데이터셋(URN)
    by_urn = {f.ds.urn: f for f in facts.values()}
    for ref, n in (await session.execute(
        select(CatalogApiLineage.target_ref, func.count())
        .where(CatalogApiLineage.target_type == "dataset", CatalogApiLineage.target_ref.in_(list(by_urn)))
        .group_by(CatalogApiLineage.target_ref)
    )).all():
        by_urn[ref].api_links = n

    # 배포본 — 수, 내려받기 URL·체크섬이 있는 수
    from app.catalog.models import DatasetDistribution
    for ds_id, n, dl, ck in (await session.execute(
        select(DatasetDistribution.dataset_id, func.count(), func.count(DatasetDistribution.download_url),
               func.count(DatasetDistribution.checksum))
        .where(DatasetDistribution.dataset_id.in_(ids)).group_by(DatasetDistribution.dataset_id)
    )).all():
        facts[ds_id].distributions, facts[ds_id].downloads, facts[ds_id].checksums = n, dl, ck

    # 오류 신고·개선 요청(댓글) 집계와 데이터 버전 이력 수 (4단계)
    from app.catalog.models import DatasetVersion
    from app.comments.service import report_stats
    for eid, st in (await report_stats(session, "dataset", [str(i) for i in ids])).items():
        if eid.isdigit() and int(eid) in facts:
            facts[int(eid)].reports = st
    for ds_id, n in (await session.execute(
        select(DatasetVersion.dataset_id, func.count()).where(DatasetVersion.dataset_id.in_(ids))
        .group_by(DatasetVersion.dataset_id)
    )).all():
        facts[ds_id].versions = n

    # 최근 스키마 변경 — 첫 수집(기준점) 스냅샷은 변경이 아니므로 changes_json 이 빈 것은 건너뛴다
    snap_latest = (
        select(SchemaSnapshot.dataset_id, func.max(SchemaSnapshot.id).label("sid"))
        .where(SchemaSnapshot.dataset_id.in_(ids),
               SchemaSnapshot.changes_json.isnot(None), SchemaSnapshot.changes_json != "[]")
        .group_by(SchemaSnapshot.dataset_id)
    ).subquery()
    for ds_id, synced_at, changes_json in (await session.execute(
        select(SchemaSnapshot.dataset_id, SchemaSnapshot.synced_at, SchemaSnapshot.changes_json)
        .where(SchemaSnapshot.id.in_(select(snap_latest.c.sid)))
    )).all():
        try:
            changes = json.loads(changes_json or "[]")
        except (TypeError, ValueError):
            changes = []
        fields = [str(c.get("field")) for c in changes
                  if isinstance(c, dict) and c.get("type") in ("ADD", "MODIFY") and c.get("field")]
        facts[ds_id].last_change = {"synced_at": synced_at, "fields": fields}

    # 최신 프로파일의 전체 결측률
    prof_latest = (
        select(func.max(DataProfile.id).label("pid")).where(DataProfile.dataset_id.in_(ids))
        .group_by(DataProfile.dataset_id)
    ).subquery()
    for ds_id, row_count, profile_json, profiled_at in (await session.execute(
        select(DataProfile.dataset_id, DataProfile.row_count, DataProfile.profile_json, DataProfile.profiled_at)
        .where(DataProfile.id.in_(select(prof_latest.c.pid)))
    )).all():
        try:
            cols = json.loads(profile_json or "[]")
        except (TypeError, ValueError):
            cols = []
        cols = [c for c in cols if isinstance(c, dict)] if isinstance(cols, list) else []
        if cols:
            facts[ds_id].missing_pct = ai_ready._missing_from_columns(cols, row_count or 0, profiled_at)["missing_ratio"]
        facts[ds_id].profiled_at = profiled_at

    if with_judgments:
        for j in (await session.execute(
            select(AiReadyJudgment).where(AiReadyJudgment.dataset_id.in_(ids))
        )).scalars().all():
            facts[j.dataset_id].judgments[j.item_key] = j
    return facts


# ---------------------------------------------------------------------------
# 자동 판정
# ---------------------------------------------------------------------------

def _filled(v) -> bool:
    return v is not None and str(v).strip() != ""


def _effective(ds: Dataset, defaults: dict, name: str):
    """데이터셋 값, 없으면 설정의 기관 기본값."""
    v = getattr(ds, name, None)
    return v if _filled(v) else defaults.get(name)


def _fmt(ds: Dataset) -> str:
    return str(ds.data_format or "").strip().upper()


def _has_pii(f: _Facts) -> bool | None:
    """개인정보 포함 여부 — 컬럼 분류가 있으면 포함, 아니면 데이터셋 표시값. 정하지 않았으면 None."""
    if f.pii_columns > 0 or str(f.ds.contains_pii or "").lower() == "true":
        return True
    if str(f.ds.contains_pii or "").lower() == "false":
        return False
    return None


def _is_media(f: _Facts) -> bool:
    return _fmt(f.ds) in MEDIA_FORMATS or str(f.ds.data_category or "").upper() == "UNSTRUCTURED"


def missing_required_metadata(f: _Facts, defaults: dict) -> list[str]:
    """C05 — 비어 있는 필수 메타데이터 이름."""
    ds = f.ds
    present = {
        "description": _filled(ds.description) or _filled(ds.summary),
        "legal_basis": _filled(ds.legal_basis),
        "creator": _filled(f.org_name),
        "publisher": _filled(_effective(ds, defaults, "publisher")),
        "landing_page": _filled(ds.landing_page),
        "ingestion_frequency": _filled(ds.ingestion_frequency) or _filled(ds.ingestion_cron),
        "theme": f.categories > 0,
        "keyword": f.tags > 0,
        "contact_email": _filled(_effective(ds, defaults, "contact_email")),
        "data_language": _filled(_effective(ds, defaults, "data_language")),
        "distribution": f.distributions > 0,
        "byte_size": ds.byte_size is not None,
        "data_format": _filled(ds.data_format),
        "data_version": _filled(ds.data_version),
        "license": _filled(_effective(ds, defaults, "license")),
    }
    return [label for key, label in REQUIRED_METADATA if not present[key]]


def _auto(item: Item, f: _Facts, defaults: dict) -> tuple[str | None, str]:
    """항목 하나의 자동 판정 — (PASS·FAIL·NA·None, 근거)."""
    ds = f.ds
    k = item.key
    fmt = _fmt(ds)
    pii = _has_pii(f)

    if k == "C01":
        if not fmt:
            return FAIL, "데이터 형식이 비어 있습니다."
        return (PASS, f"데이터 형식 {fmt} — 개방형 표준 포맷.") if fmt in OPEN_FORMATS \
            else (FAIL, f"데이터 형식 {fmt} 는 개방형 표준 포맷 목록에 없습니다(CSV·JSON·XML·Parquet 등).")
    if k == "C02":
        cat = str(ds.data_category or "").upper()
        if cat in ("STRUCTURED", "SEMI_STRUCTURED"):
            return PASS, f"데이터 유형 {'정형' if cat == 'STRUCTURED' else '반정형'}."
        if cat == "UNSTRUCTURED":
            return FAIL, "비정형 데이터 — 기계가 처리할 구조(주석·메타정보)를 따로 제공하는지 확인이 필요합니다."
        if f.columns:
            return PASS, f"데이터 유형은 비어 있지만 스키마(컬럼 {f.columns}개)가 있습니다."
        return FAIL, "데이터 유형과 스키마가 모두 없습니다."
    if k == "C03":
        if not f.columns:
            return FAIL, "수집된 스키마(컬럼)가 없습니다."
        pk = f"기본키 {f.pk_columns}개" if f.pk_columns else "기본키 없음"
        return PASS, f"컬럼 {f.columns}개(자료형 포함), {pk}."
    if k == "C04":
        return PASS, (f"외부 API 로 DCAT JSON-LD(/api/v1/external/datasets/{ds.id}/dcat)와 "
                      f"Croissant JSON-LD(/api/v1/external/datasets/{ds.id}/croissant)를 제공합니다.")
    if k == "C05":
        missing = missing_required_metadata(f, defaults)
        if not missing:
            return PASS, f"필수 메타데이터 {len(REQUIRED_METADATA)}개가 모두 있습니다."
        return FAIL, "비어 있는 필수 항목: " + ", ".join(missing) + "."
    if k == "C06":
        n = sum(f.rule_types.values())
        return (PASS, f"활성 품질 규칙 {n}개가 검증 기준입니다.") if n \
            else (FAIL, "활성 품질 규칙(허용값·필수값·형식·관계 검사)이 없습니다.")
    if k == "C07":
        if ds.byte_size is None:
            return None, "용량을 몰라 대용량인지 판정할 수 없습니다."
        if ds.byte_size < LARGE_BYTES:
            return NA, f"용량 {ds.byte_size:,}바이트 — 대용량(1GB) 미만."
        comp = str(ds.compression or "").strip().upper()
        if fmt in BULK_FORMATS or (comp and comp != "NONE"):
            return PASS, f"형식 {fmt or '-'}, 압축 {comp or '-'}."
        return FAIL, f"용량 {ds.byte_size:,}바이트인데 압축이 없고 형식이 {fmt or '비어 있음'} 입니다."
    if k == "C08":
        if not f.columns:
            return FAIL, "스키마(컬럼)가 없어 데이터 사전을 만들 수 없습니다."
        pct = f.described / f.columns * 100
        text = f"컬럼 설명 {f.described}/{f.columns}개({pct:.0f}%)."
        return (PASS, text) if pct >= DICTIONARY_FILL_PCT else (FAIL, text + f" 기준 {DICTIONARY_FILL_PCT:.0f}% 미만.")
    if k == "C09":
        ok, total = f.format_results
        if not total:
            n = f.rule_types.get("DATE_FORMAT", 0) + f.rule_types.get("ACCEPTED_VALUES", 0)
            return None, ("날짜 형식·허용값 규칙은 있지만 아직 진단 결과가 없습니다." if n
                          else "날짜 형식(DATE_FORMAT)·허용값(ACCEPTED_VALUES) 규칙이 없어 판정할 수 없습니다.")
        return (PASS, f"표기 형식 규칙 {total}개 모두 통과.") if ok == total \
            else (FAIL, f"표기 형식 규칙 {total}개 중 {total - ok}개가 최근 진단에서 실패했습니다.")
    if k == "C10":
        if not f.term_mapped:
            return None, "표준 용어에 매핑된 컬럼이 없어 판정할 수 없습니다."
        if f.term_violations:
            return FAIL, f"표준 용어 매핑 {f.term_mapped}개 중 위반(이름은 같고 타입이 다름) {f.term_violations}개."
        return PASS, f"표준 용어 매핑 {f.term_mapped}개, 위반 없음."
    if k == "C11":
        std = f.term_mapped or f.rule_types.get("DATE_FORMAT", 0) or f.rule_types.get("ACCEPTED_VALUES", 0)
        notation = _filled(ds.missing_value_notation)
        if notation and std:
            return PASS, "결측·특수값 표기 규칙과 적용 표준(용어 매핑·형식 규칙)이 있습니다."
        lacks = [x for x, ok in (("결측·특수값 표기 규칙", notation), ("표준 용어 매핑 또는 형식·코드 규칙", std)) if not ok]
        return FAIL, "없는 것: " + ", ".join(lacks) + "."
    if k == "C13":
        return (PASS, f"결측·특수값 표기: {ds.missing_value_notation}") if _filled(ds.missing_value_notation) \
            else (FAIL, "결측·특수값 표기 규칙이 비어 있습니다.")
    if k == "C14":
        if f.has_score or ds.quality_score is not None:
            return PASS, f"품질 점수 {ds.quality_score if ds.quality_score is not None else '-'}점(품질 진단 결과)."
        return FAIL, "품질 진단을 한 적이 없습니다."
    if k == "C15":
        syn, ann = ds.contains_synthetic, ds.contains_machine_annotation
        if _filled(syn) and _filled(ann):
            yes = lambda v: "포함" if str(v).lower() == "true" else "없음"
            return PASS, f"합성 데이터 {yes(syn)}, 기계 자동 주석 {yes(ann)}."
        return FAIL, "합성 데이터·기계 자동 주석 포함 여부를 표시하지 않았습니다."
    if k == "C16":
        return (PASS, "결측 정보가 있습니다.") if _filled(ds.missing_data_info) \
            else (FAIL, "결측 비율·사유·처리 원칙(결측 정보)이 비어 있습니다.")
    if k == "C17":
        parts = []
        if str(ds.is_synced).lower() == "true":
            parts.append(f"데이터 소스 {f.datasource_name or '-'} 에서 수집")
        if f.upstream:
            parts.append(f"상류 리니지 {f.upstream}개")
        return (PASS, ", ".join(parts) + ".") if parts \
            else (FAIL, "수집 경로(데이터 소스 수집)나 상류 리니지가 없습니다.")
    if k == "C20":
        lc = f.last_change
        if not lc:
            return PASS, "스키마 변경 이력이 없습니다."
        if not lc["fields"]:
            return PASS, "최근 스키마 변경은 컬럼 삭제뿐입니다."
        # 추가·변경된 컬럼 중 지금 설명이 없는 것
        return None, ""   # _evaluate 에서 지금 컬럼 설명과 대조해 정한다
    if k == "C21":
        lacks = [x for x, ok in (("설명", _filled(ds.description) or _filled(ds.summary)),
                                 ("알려진 편향 또는 한계", _filled(ds.data_biases) or _filled(ds.known_limitations))) if not ok]
        if lacks:
            return FAIL, "데이터 카드는 자동으로 만들지만 " + ", ".join(lacks) + "이(가) 비어 있어 용도·리스크 요약이 빠집니다."
        return PASS, f"데이터 카드를 자동으로 만듭니다(/api/v1/external/datasets/{ds.id}/data-card)."
    if k == "C22":
        if pii is False:
            return NA, "개인정보가 없다고 표시했습니다."
        if pii is None:
            return FAIL, "개인정보 포함 여부를 정하지 않았습니다."
        return (PASS, "개인정보 보호조치를 적었습니다.") if _filled(ds.privacy_measures) \
            else (FAIL, "개인정보가 있는데 보호조치(비식별·가명처리 방식)를 적지 않았습니다.")
    if k == "C23":
        lic = _effective(ds, defaults, "license")
        if not _filled(lic):
            return FAIL, "라이선스가 없습니다(데이터셋·기관 기본값 모두)."
        src = "" if _filled(ds.license) else " (기관 기본값)"
        if lic == "CUSTOM" and not _filled(_effective(ds, defaults, "license_url")):
            return FAIL, "기관 이용약관(CUSTOM)인데 이용약관 URL 이 없습니다."
        return PASS, f"라이선스 {ai_ready.LICENSES.get(lic, lic)}{src}."
    if k == "C24":
        return (NA, "개인정보가 없다고 표시했습니다.") if pii is False else (None, "재식별 위험 평가 문서를 근거로 판정하세요.")
    if k == "C25":
        return (PASS, f"영구 식별자 {ds.persistent_id}.") if _filled(ds.persistent_id) \
            else (FAIL, "영구 식별자가 없습니다(URN 은 이름이 바뀌면 함께 바뀝니다).")
    if k == "C27":
        return (PASS, f"내려받기 URL 이 있는 배포본 {f.downloads}개.") if f.downloads \
            else (None, "내려받기 URL 이 있는 배포본이 없습니다. 제공 플랫폼에서 제공하면 수동으로 판정하세요.")
    if k == "C33":
        return (PASS, f"체크섬이 있는 배포본 {f.checksums}개.") if f.checksums \
            else (None, "배포본에 체크섬이 없습니다. 전자서명 등 다른 방식이면 수동으로 판정하세요.")
    if k == "C26":
        return (PASS, f"API 카탈로그의 API {f.api_links}개와 연결되어 있습니다.") if f.api_links \
            else (None, "API 카탈로그에 연결된 API 가 없습니다. 제공 플랫폼에서 제공하면 수동으로 판정하세요.")
    if k == "C30":
        contact = _effective(ds, defaults, "contact_email")
        lacks = [x for x, ok in (("소유자", f.owners > 0), ("문의 연락처", _filled(contact))) if not ok]
        if lacks:
            return FAIL, "없는 것: " + ", ".join(lacks) + "."
        return PASS, f"소유자 {f.owners}명, 문의 연락처 {contact}."
    if k == "C31":
        r = f.reports or {}
        tally = (f"접수 {r.get('total', 0)}건 — 처리 중 {r.get('open', 0)}, 끝남 {r.get('closed', 0)}"
                 if r.get("total") else "아직 접수된 신고 없음")
        if r.get("overdue"):
            return FAIL, f"처리 기한을 넘긴 오류 신고·개선 요청이 {r['overdue']}건 있습니다({tally})."
        return PASS, f"댓글 탭에서 오류 신고·개선 요청을 접수하고 처리 상태·기한을 관리합니다({tally})."
    if k == "C32":
        if _filled(ds.data_version) and _filled(ds.version_notes):
            hist = f", 버전 이력 {f.versions}개" if f.versions else ""
            return PASS, f"버전 {ds.data_version}, 버전 노트 있음{hist}."
        return FAIL, "데이터 버전과 버전 노트를 모두 적어야 합니다."
    # 유형별
    if k == "T1":
        if _is_media(f) or not f.numeric_columns:
            return NA, "숫자 컬럼이 없거나 비정형 데이터입니다."
        return None, f"숫자 컬럼 {f.numeric_columns}개 — 산출 규칙(산식·집계 단위·기준시점)을 근거로 판정하세요."
    if k in ("T2", "T3"):
        return (None, "이미지·영상(비정형) 데이터입니다.") if _is_media(f) else (NA, "이미지·영상 데이터가 아닙니다.")
    if k == "T4":
        if not _is_media(f):
            return NA, "이미지·영상·음성 데이터가 아닙니다."
        return (NA, "개인정보가 없다고 표시했습니다.") if pii is False else (None, "마스킹·재식별 평가 결과를 근거로 판정하세요.")
    if k == "T5":
        if pii is False:
            return NA, "개인정보가 없다고 표시했습니다."
        if pii is None:
            return None, "개인정보 포함 여부를 정하지 않았습니다."
        return (PASS, "개인정보 보호조치(가공 범위·원칙)를 적었습니다.") if _filled(ds.privacy_measures) \
            else (FAIL, "비식별·가공 범위와 주의사항을 '개인정보 보호조치'에 적지 않았습니다.")
    # 수동·밖 항목
    return None, ""


# ---------------------------------------------------------------------------
# 판정 조립
# ---------------------------------------------------------------------------

def _rate(items: list[dict], level: str) -> dict:
    scoped = [i for i in items if i["level"] == level and i["status"] != NA]
    passed = sum(1 for i in scoped if i["status"] == PASS)
    total = len(scoped)
    return {"passed": passed, "total": total, "rate": round(passed / total * 100, 1) if total else None}


def _min_requirements(f: _Facts, items_by_key: dict[str, dict], thresholds: dict, now: datetime) -> dict:
    """가이드라인 §3.1.1.2 공유 최소 요건 4개."""
    ds = f.ds
    max_missing = thresholds["max_missing_pct"]
    max_cycles = thresholds["max_missed_cycles"]
    checks = []

    if f.missing_pct is None:
        checks.append({"key": "missing", "label": f"결측률 {max_missing:g}% 미만", "met": None,
                       "detail": "프로파일이 없어 결측률을 모릅니다."})
    else:
        checks.append({"key": "missing", "label": f"결측률 {max_missing:g}% 미만", "met": f.missing_pct < max_missing,
                       "detail": f"전체 결측률 {f.missing_pct:g}%."})

    c05 = items_by_key["C05"]
    checks.append({"key": "metadata", "label": "필수 메타데이터", "met": c05["status"] == PASS, "detail": c05["evidence"]})

    freq = str(ds.ingestion_frequency or "").upper()
    hours = FREQUENCY_HOURS.get(freq)
    label = f"갱신 주기 {max_cycles}회 이내"
    if hours is None:
        checks.append({"key": "freshness", "label": label, "met": None,
                       "detail": "갱신주기가 시간·일·주·월이 아니어서(실시간·수동·cron·미지정) 판정하지 않습니다."})
    elif ds.last_ingested_at is None:
        checks.append({"key": "freshness", "label": label, "met": False, "detail": "최종 적재 시각이 없습니다."})
    else:
        last = ds.last_ingested_at if ds.last_ingested_at.tzinfo else ds.last_ingested_at.replace(tzinfo=timezone.utc)
        elapsed = (now - last).total_seconds() / 3600
        cycles = elapsed / hours
        checks.append({"key": "freshness", "label": label, "met": cycles <= max_cycles,
                       "detail": f"마지막 적재 뒤 {cycles:.1f}주기 지남({freq})."})

    c01 = items_by_key["C01"]
    checks.append({"key": "format", "label": "표준 포맷", "met": c01["status"] == PASS, "detail": c01["evidence"]})

    met = all(c["met"] is True for c in checks if c["met"] is not None) and any(c["met"] is not None for c in checks)
    return {"met": met, "checks": checks}


async def _thresholds(session: AsyncSession) -> dict:
    from app.settings.service import get_config_by_category

    cfg = await get_config_by_category(session, "ai_ready")

    def num(key: str, default: float) -> float:
        try:
            v = cfg.get(key)
            return float(v) if v not in (None, "") else default
        except (TypeError, ValueError):
            return default

    return {"max_missing_pct": num("ai_ready_max_missing_pct", 20.0),
            "max_missed_cycles": int(num("ai_ready_max_missed_cycles", 2))}


def _judgment_dict(j: AiReadyJudgment | None) -> dict | None:
    if j is None:
        return None
    return {"status": j.status, "note": j.note, "judged_by": j.judged_by, "judged_at": j.judged_at}


def _evaluate(f: _Facts, defaults: dict, thresholds: dict, now: datetime,
                    *, detail: bool) -> dict:
    items: list[dict] = []
    for item in ITEMS:
        auto_status, evidence = _auto(item, f, defaults)
        if item.key == "C20" and f.last_change and f.last_change["fields"]:
            # 추가·변경된 컬럼 중 지금 설명이 없는 것(이미 삭제된 컬럼은 undescribed 에 없다)
            missing = [p for p in f.last_change["fields"] if p in f.undescribed]
            when = f.last_change["synced_at"].date().isoformat() if f.last_change["synced_at"] else "날짜 미상"
            if missing:
                shown = ", ".join(missing[:5]) + (f" 외 {len(missing) - 5}개" if len(missing) > 5 else "")
                auto_status, evidence = FAIL, f"{when} 스키마 변경에서 추가·변경된 컬럼 중 설명이 없는 것: {shown}."
            else:
                auto_status, evidence = PASS, f"{when} 스키마 변경에서 추가·변경된 컬럼 {len(f.last_change['fields'])}개 모두 설명이 있습니다."
        j = f.judgments.get(item.key)
        status = j.status if j is not None else auto_status
        row = {"key": item.key, "level": item.level, "status": status,
               "source": "MANUAL" if j is not None else ("AUTO" if auto_status is not None else None),
               "auto_status": auto_status, "evidence": evidence}
        if detail:
            row.update({"group": item.group, "text": item.text, "mode": item.mode, "hint": item.hint,
                        "judgment": _judgment_dict(j)})
        items.append(row)

    by_key = {i["key"]: i for i in items}
    counts = {s: sum(1 for i in items if i["status"] == s) for s in STATUSES}
    counts["UNKNOWN"] = sum(1 for i in items if i["status"] is None)
    return {
        "dataset_id": f.ds.id,
        "required": _rate(items, REQUIRED),
        "recommended": _rate(items, RECOMMENDED),
        "counts": counts,
        "min_requirements": _min_requirements(f, by_key, thresholds, now),
        "items": items,
    }


async def min_requirements_for(session: AsyncSession, datasets: list[Dataset],
                               now: datetime | None = None) -> dict[int, dict]:
    """데이터셋마다 공유 최소 요건 ``{"met", "checks"}`` — 판정 재료는 묶음 조회(알림 주기 점검용)."""
    if not datasets:
        return {}
    facts = await _load_context(session, datasets)
    defaults = await ai_ready.load_defaults(session)
    thresholds = await _thresholds(session)
    now = now or datetime.now(timezone.utc)
    return {d.id: _evaluate(facts[d.id], defaults, thresholds, now, detail=False)["min_requirements"]
            for d in datasets}


async def evaluate_dataset(session: AsyncSession, dataset_id: int) -> dict | None:
    """데이터셋 하나의 전체 점검 결과(항목 문장·근거·수동 판정 포함). 데이터셋이 없으면 None."""
    ds = await session.get(Dataset, dataset_id)
    if ds is None:
        return None
    facts = await _load_context(session, [ds])
    defaults = await ai_ready.load_defaults(session)
    result = _evaluate(facts[ds.id], defaults, await _thresholds(session),
                             datetime.now(timezone.utc), detail=True)
    result["profiled_at"] = facts[ds.id].profiled_at
    return result


# ---------------------------------------------------------------------------
# 수동 판정
# ---------------------------------------------------------------------------

async def set_judgment(session: AsyncSession, dataset_id: int, item_key: str, status: str, note: str | None,
                       actor: str | None) -> AiReadyJudgment:
    """수동 판정 저장(있으면 덮어씀). 항목 키·상태가 틀리면 ValueError."""
    if item_key not in ITEM_BY_KEY:
        raise ValueError(f"체크리스트 항목 '{item_key}' 이 없습니다.")
    if status not in STATUSES:
        raise ValueError("판정은 PASS(적정)·FAIL(미흡)·NA(해당없음) 중 하나여야 합니다.")
    note = ai_ready.norm_text(note)
    row = (await session.execute(
        select(AiReadyJudgment).where(AiReadyJudgment.dataset_id == dataset_id, AiReadyJudgment.item_key == item_key)
    )).scalars().first()
    now = datetime.now(timezone.utc)
    if row is None:
        row = AiReadyJudgment(dataset_id=dataset_id, item_key=item_key)
        session.add(row)
    row.status, row.note, row.judged_by, row.judged_at = status, note, actor, now
    await session.flush()
    logger.info("AI-Ready 수동 판정: dataset_id=%d item=%s status=%s by=%s", dataset_id, item_key, status, actor)
    return row


async def clear_judgment(session: AsyncSession, dataset_id: int, item_key: str) -> bool:
    """수동 판정 삭제 — 자동 판정으로 돌아간다. 지운 행이 있으면 True."""
    res = await session.execute(
        delete(AiReadyJudgment).where(AiReadyJudgment.dataset_id == dataset_id, AiReadyJudgment.item_key == item_key)
    )
    if res.rowcount:
        logger.info("AI-Ready 수동 판정 삭제: dataset_id=%d item=%s", dataset_id, item_key)
    return bool(res.rowcount)


# ---------------------------------------------------------------------------
# 관리 목록 — 데이터셋별 충족률
# ---------------------------------------------------------------------------

SORTS = ("required", "recommended", "name")


def split_path(urn: str, datasource_key: str | None) -> tuple[str, str]:
    """URN ``{데이터 소스 키}.{db…}.{테이블}.dataset`` 에서 (db, 테이블)을 나눈다.

    데이터셋 이름은 어댑터마다 ``db.table`` 이기도 하고 ``table`` 만이기도 해서 URN 을 기준으로 삼는다.
    db 가 여러 단계면(PostgreSQL ``db.schema``) 점으로 이어 하나로 본다. 단계가 없으면 db 는 빈 문자열.
    """
    path = urn[:-len(".dataset")] if urn.endswith(".dataset") else urn
    if datasource_key and path.startswith(datasource_key + "."):
        path = path[len(datasource_key) + 1:]
    elif "." in path:
        path = path.split(".", 1)[1]
    db, _, table = path.rpartition(".")
    return db, table


async def _scoped_datasets(session: AsyncSession) -> list[tuple[Dataset, str | None, str | None, str | None]]:
    """활성 데이터셋과 (카탈로그 키, 카탈로그 이름, 데이터 소스 키). 카탈로그가 없으면 데이터 소스 키를 카탈로그 자리에 쓴다."""
    from app.catalog.models import Catalog

    rows = (await session.execute(
        select(Dataset, Catalog.catalog_key, Catalog.name, Datasource.datasource_id)
        .join(Datasource, Datasource.id == Dataset.datasource_id)
        .outerjoin(Catalog, Catalog.id == Dataset.catalog_id)
        .where(Dataset.status == "active")
    )).all()
    return [(d, ck or dk, cn, dk) for d, ck, cn, dk in rows]


async def scopes(session: AsyncSession) -> list[dict]:
    """카탈로그 → DB 선택지(활성 데이터셋 수 포함). 카탈로그가 없는 데이터셋은 ``catalog_id`` null 로 묶는다."""
    by_cat: dict[int | None, dict] = {}
    for d, ckey, cname, dkey in await _scoped_datasets(session):
        c = by_cat.setdefault(d.catalog_id, {"catalog_id": d.catalog_id, "catalog_key": ckey if d.catalog_id else None,
                                             "name": cname or "(카탈로그 없음)", "datasets": 0, "_dbs": {}})
        c["datasets"] += 1
        db, _ = split_path(d.urn, dkey)
        c["_dbs"][db] = c["_dbs"].get(db, 0) + 1
    out = []
    for c in by_cat.values():
        dbs = [{"name": k, "datasets": v} for k, v in sorted(c.pop("_dbs").items())]
        out.append(c | {"dbs": dbs})
    out.sort(key=lambda c: (c["catalog_id"] is None, c["name"].lower()))
    return out


async def overview(session: AsyncSession, *, q: str | None = None, catalog_id: int | None = None,
                   db: str | None = None, only_unmet: bool = False, sort: str = "required", desc: bool = False,
                   page: int = 1, size: int = 20) -> dict:
    """활성 데이터셋을 판정해 충족률 순으로 정렬한 목록과 요약(카탈로그별 평균 포함).

    범위는 카탈로그 → DB → 이름 검색 순으로 좁힌다. ``catalog_id=0`` 은 카탈로그가 없는 데이터셋, ``db=""`` 는 DB 단계가 없는 데이터셋.
    검색어는 ``db.dataset`` 경로와 표시 이름에서 찾는다(대소문자 무시).
    충족률로 정렬해야 해서 조건에 맞는 데이터셋을 모두 판정한 뒤 자른다. 판정 재료는 묶음 조회라 데이터셋 수천 개까지는 한 번에 계산한다.
    """
    kw = (q or "").strip().lower()
    picked: list[tuple[Dataset, dict]] = []
    for d, ckey, cname, dkey in await _scoped_datasets(session):
        if catalog_id is not None and (d.catalog_id or 0) != catalog_id:
            continue
        dbname, table = split_path(d.urn, dkey)
        if db is not None and dbname != db:
            continue
        path = f"{dbname}.{table}" if dbname else table   # 카탈로그 키는 난수가 섞여 경로에 넣지 않는다(카탈로그는 이름으로 따로)
        if kw and kw not in path.lower() and kw not in (d.display_name or "").lower():
            continue
        picked.append((d, {"catalog_id": d.catalog_id, "catalog_key": ckey, "catalog_name": cname,
                           "db": dbname, "table": table, "path": path}))

    facts = await _load_context(session, [d for d, _ in picked])
    defaults = await ai_ready.load_defaults(session)
    thresholds = await _thresholds(session)
    now = datetime.now(timezone.utc)

    rows = []
    for d, scope in picked:
        r = _evaluate(facts[d.id], defaults, thresholds, now, detail=False)
        failed_required = [i["key"] for i in r["items"] if i["level"] == REQUIRED and i["status"] not in (PASS, NA)]
        rows.append({
            "dataset_id": d.id, "name": d.name, "display_name": d.display_name, **scope,
            "required": r["required"], "recommended": r["recommended"], "counts": r["counts"],
            "min_requirements_met": r["min_requirements"]["met"],
            "unmet_required": failed_required,
        })

    def avg(vals: list[float | None]) -> float | None:
        vs = [v for v in vals if v is not None]
        return round(sum(vs) / len(vs), 1) if vs else None

    # 카탈로그별(카탈로그를 골랐으면 DB 별) 평균
    group_key = "db" if catalog_id is not None else "catalog_id"
    groups: dict = {}
    for r in rows:
        g = groups.setdefault(r[group_key], {
            "catalog_id": r["catalog_id"], "catalog_name": r["catalog_name"], "catalog_key": r["catalog_key"],
            "db": r["db"] if group_key == "db" else None, "datasets": 0, "min_met": 0, "_req": [], "_rec": [],
        })
        g["datasets"] += 1
        g["min_met"] += 1 if r["min_requirements_met"] else 0
        g["_req"].append(r["required"]["rate"])
        g["_rec"].append(r["recommended"]["rate"])
    breakdown = sorted(
        ({k: v for k, v in g.items() if not k.startswith("_")}
         | {"required_rate": avg(g["_req"]), "recommended_rate": avg(g["_rec"])} for g in groups.values()),
        key=lambda g: (g["required_rate"] if g["required_rate"] is not None else -1,
                       (g["db"] if group_key == "db" else g["catalog_name"]) or ""),
    )
    summary = {
        "datasets": len(rows),
        "required_rate": avg([r["required"]["rate"] for r in rows]),
        "recommended_rate": avg([r["recommended"]["rate"] for r in rows]),
        "min_met": sum(1 for r in rows if r["min_requirements_met"]),
        "fully_required": sum(1 for r in rows if not r["unmet_required"]),
    }

    if only_unmet:
        rows = [r for r in rows if r["unmet_required"]]
    # 이름 순으로 먼저 줄 세운 뒤 충족률로 다시 정렬 — 정렬은 안정적이라 같은 충족률 안에서는 이름 순이 남는다(내림차순이어도)
    rows.sort(key=lambda r: (r["path"].lower(), (r["catalog_name"] or "").lower(), r["dataset_id"]))
    if sort == "name":
        if desc:
            rows.reverse()
    else:
        key = "required" if sort not in SORTS else sort
        rows.sort(key=lambda r: r[key]["rate"] if r[key]["rate"] is not None else -1, reverse=desc)
    total = len(rows)
    start = (page - 1) * size
    return {"summary": summary, "breakdown": {"by": "db" if group_key == "db" else "catalog", "groups": breakdown},
            "items": rows[start:start + size], "total": total, "page": page, "size": size}


def item_catalog() -> list[dict]:
    """체크리스트 항목 정의(화면의 항목 이름·관리 목록의 미충족 항목 표시용)."""
    return [{"key": i.key, "short": ITEM_SHORT.get(i.key, i.key), "group": i.group, "level": i.level, "text": i.text,
             "mode": i.mode, "hint": i.hint}
            for i in ITEMS]


__all__ = [
    "ITEMS", "ITEM_BY_KEY", "STATUSES", "evaluate_dataset", "set_judgment", "clear_judgment", "overview", "scopes",
    "item_catalog", "split_path", "ITEM_SHORT",
    "missing_required_metadata",
]
