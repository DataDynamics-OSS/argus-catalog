# SPDX-License-Identifier: Apache-2.0
"""AI-Ready 내보내기 (TODO-096 3단계) — 자기완결형, in-memory SQLite. 픽스처는 test_ai_ready_check 와 같다.

- 배포본 CRUD(/catalog/datasets/{id}/distributions) — 접속 URL 필수, URL·체크섬 검증 422, 소유자·관리자만.
- GET /external/datasets/{id}/dcat · /croissant · /data-card, URN 판(/external/dcat?urn=), /external/dcat/catalog(쪽).
- 체크리스트: 배포본이 C05 필수 메타데이터에 들고, 내려받기 URL·체크섬이 C27·C33 을 적정으로 만든다.

실행:
    cd backend
    pytest tests/test_ai_ready_export.py
"""

from __future__ import annotations

from app.catalog import ai_ready_export
from tests.test_ai_ready_check import V1, _fill_everything, _items, _tu, env  # noqa: F401 — env 는 픽스처

SHA = "0123456789abcdef" * 4


def test_distribution_crud_and_validation(env):
    c, who, ids, _run = env
    url = f"{V1}/catalog/datasets/{ids['bare']}/distributions"
    assert c.get(url).json() == []

    r = c.post(url, json={"title": " 2025 전체 ", "format": "csv", "access_url": "https://data.go.kr/x",
                          "download_url": "https://data.go.kr/x.csv", "byte_size": 2048, "checksum": SHA.upper()})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == "2025 전체" and d["format"] == "CSV" and d["effective_media_type"] == "text/csv"
    assert d["checksum_algorithm"] == "SHA-256" and d["checksum"] == SHA  # 소문자, 기본 알고리즘

    for bad in ({"access_url": ""}, {"access_url": "ftp://x"}, {"access_url": "https://x", "checksum": "abc"},
                {"access_url": "https://x", "checksum": SHA, "checksum_algorithm": "CRC32"},
                {"access_url": "https://x", "byte_size": -1}):
        assert c.post(url, json=bad).status_code == 422, bad

    r = c.put(f"{url}/{d['id']}", json={"access_url": "https://data.go.kr/api", "format": "API"})
    assert r.status_code == 200 and r.json()["checksum"] is None and r.json()["effective_media_type"] is None
    assert c.put(f"{url}/99999", json={"access_url": "https://x"}).status_code == 404

    who["user"] = _tu("mallory")
    assert c.post(url, json={"access_url": "https://x"}).status_code == 403
    assert c.delete(f"{url}/{d['id']}").status_code == 403
    who["user"] = _tu("alice")
    assert c.delete(f"{url}/{d['id']}").status_code == 200
    assert c.get(url).json() == []
    assert c.get(f"{V1}/catalog/datasets/99999/distributions").status_code == 404


def test_distribution_reorder(env):
    """순서 바꾸기 — id 전부를 새 순서로, 목록이 다르면 409, 소유자·관리자만. DCAT·Croissant 가 순서를 따른다."""
    c, who, ids, _run = env
    url = f"{V1}/catalog/datasets/{ids['bare']}/distributions"
    a = c.post(url, json={"title": "A", "access_url": "https://x.go.kr/a"}).json()
    b = c.post(url, json={"title": "B", "access_url": "https://x.go.kr/b"}).json()
    d = c.post(url, json={"title": "C", "access_url": "https://x.go.kr/c"}).json()
    assert [x["title"] for x in c.get(url).json()] == ["A", "B", "C"]

    r = c.post(f"{url}/reorder", json={"ids": [d["id"], a["id"], b["id"]]})
    assert r.status_code == 200, r.text
    assert [x["title"] for x in r.json()] == ["C", "A", "B"] and [x["ordinal"] for x in r.json()] == [0, 1, 2]
    assert [x["title"] for x in c.get(url).json()] == ["C", "A", "B"]
    dcat = c.get(f"{V1}/external/datasets/{ids['bare']}/dcat").json()
    assert [x["dct:title"] for x in dcat["dcat:distribution"]] == ["C", "A", "B"]
    cro = c.get(f"{V1}/external/datasets/{ids['bare']}/croissant").json()
    assert [x["name"] for x in cro["distribution"]] == ["C", "A", "B"]

    # 빠진 id · 겹친 id · 다른 데이터셋 id → 409, 순서는 그대로
    for bad in ([a["id"], b["id"]], [a["id"], a["id"], b["id"]], [a["id"], b["id"], d["id"], 99999]):
        assert c.post(f"{url}/reorder", json={"ids": bad}).status_code == 409, bad
    assert [x["title"] for x in c.get(url).json()] == ["C", "A", "B"]

    who["user"] = _tu("mallory")
    assert c.post(f"{url}/reorder", json={"ids": [a["id"], b["id"], d["id"]]}).status_code == 403
    who["user"] = _tu("alice")
    assert c.post(f"{V1}/catalog/datasets/99999/distributions/reorder", json={"ids": []}).status_code == 404


def test_dcat_export(env):
    c, _who, ids, run = env
    run(_fill_everything(ids["bare"], ids["src"]))
    r = c.get(f"{V1}/external/datasets/{ids['bare']}/dcat")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/ld+json")
    d = r.json()
    assert d["@type"] == "dcat:Dataset" and d["@context"]["dcat"] == "http://www.w3.org/ns/dcat#"
    assert d["dct:identifier"] == "seoul-pop" and d["dct:references"] == "통계법 제3조"
    assert d["dct:creator"]["foaf:name"] == "서울특별시" and d["dct:publisher"]["foaf:name"] == "서울특별시"
    assert d["dct:accrualPeriodicity"] == {"@id": "http://purl.org/cld/freq/daily"}
    assert d["dct:language"] == [{"@id": "http://id.loc.gov/vocabulary/iso639-1/ko"}]
    assert d["dct:license"] == {"@id": "https://www.kogl.or.kr/info/licenseType1.do"}
    assert d["dcat:keyword"] == ["인구"] and d["dcat:theme"] == ["일반공공행정"]
    assert d["dcat:contactPoint"]["vcard:hasEmail"] == {"@id": "mailto:d@seoul.go.kr"}
    assert d["owl:versionInfo"] == "v1" and d["dcat:mediaType"] == "text/csv"
    dist = d["dcat:distribution"][0]
    assert dist["dcat:downloadURL"] == {"@id": "https://data.seoul.go.kr/x.csv"}
    assert dist["spdx:checksum"]["spdx:checksumValue"] == "a" * 64
    assert d["dqv:hasQualityAnnotation"]["argus:requiredRate"] == 100.0

    # URN 으로도
    assert c.get(f"{V1}/external/dcat", params={"urn": "ds-a.db.bare"}).json()["@id"] == d["@id"]
    assert c.get(f"{V1}/external/datasets/99999/dcat").status_code == 404


def test_dcat_catalog_pages(env):
    c, *_ = env
    body = c.get(f"{V1}/external/dcat/catalog", params={"size": 1}).json()
    assert body["@type"] == "dcat:Catalog" and body["argus:total"] == 2  # removed 제외
    assert len(body["dcat:dataset"]) == 1 and body["argus:next"].endswith("page=2&size=1")
    assert "dqv:hasQualityAnnotation" not in body["dcat:dataset"][0]  # 수확은 점검 요약 없이
    last = c.get(f"{V1}/external/dcat/catalog", params={"size": 1, "page": 2}).json()
    assert "argus:next" not in last


def test_croissant_export(env):
    c, _who, ids, run = env
    run(_fill_everything(ids["bare"], ids["src"]))
    d = c.get(f"{V1}/external/datasets/{ids['bare']}/croissant").json()
    assert d["conformsTo"] == "http://mlcommons.org/croissant/1.0" and d["@type"] == "sc:Dataset"
    assert d["@context"]["cr"] == "http://mlcommons.org/croissant/"
    assert d["license"] == "https://www.kogl.or.kr/info/licenseType1.do" and d["inLanguage"] == ["ko"]
    fo = d["distribution"][0]
    assert fo["@type"] == "cr:FileObject" and fo["encodingFormat"] == "text/csv" and fo["sha256"] == "a" * 64
    rs = d["recordSet"][0]
    fields = {f["name"]: f for f in rs["field"]}
    assert fields["gu"]["dataType"] == "sc:Text" and fields["day"]["dataType"] == "sc:Date"
    assert fields["gu"]["source"] == {"fileObject": {"@id": fo["@id"]}, "extract": {"column": "gu"}}
    assert rs["key"] == {"@id": "bare/gu"}
    assert d["rai:hasSyntheticData"] is False


def test_data_card(env):
    c, _who, ids, run = env
    bare = c.get(f"{V1}/external/datasets/{ids['bare']}/data-card")
    assert bare.status_code == 200 and bare.headers["content-type"].startswith("text/markdown")
    assert "_작성 필요 — 메타데이터 탭 '라이선스'_" in bare.text

    run(_fill_everything(ids["bare"], ids["src"]))
    text = c.get(f"{V1}/external/datasets/{ids['bare']}/data-card").text
    for heading in ("## 데이터셋 개요", "## 데이터셋 구조", "## 데이터셋 생성", "## 사용 시 고려사항",
                    "## 추가 정보 및 라이선스", "## 기술적 사양", "## 필드 정의", "## AI-Ready 점검"):
        assert heading in text, heading
    assert "공공누리 제1유형" in text and "| gu | " in text and "필수 항목 충족률: 100.0%" in text
    assert "샘플 레코드는 카드에 넣지 않습니다" in text


def test_unit_and_calculation_in_croissant_and_data_card(env):
    """데이터 사전 — 단위는 Croissant Field 의 sc:unitText, 산정기준은 설명 뒤. 데이터 카드 '필드 정의'에 단위 · 산정기준 칸."""
    c, _who, ids, run = env
    run(_fill_everything(ids["bare"], ids["src"]))

    async def _go(db):
        import app.catalog.models as cm
        db.add(cm.DatasetSchema(dataset_id=ids["bare"], field_path="pop", field_type="INTEGER", description="인구 수",
                                unit="명", calculation="주민등록 인구 합계", ordinal=2))
        db.add(cm.DatasetSchema(dataset_id=ids["bare"], field_path="rate", field_type="DOUBLE", unit="%",
                                calculation="전입 / 인구 × 100", ordinal=3))
    run(_go)
    d = c.get(f"{V1}/external/datasets/{ids['bare']}/croissant").json()
    fields = {f["name"]: f for f in d["recordSet"][0]["field"]}
    assert fields["pop"]["sc:unitText"] == "명"
    assert fields["pop"]["description"] == "인구 수\n산정기준: 주민등록 인구 합계"
    assert fields["rate"]["description"] == "산정기준: 전입 / 인구 × 100"
    assert "sc:unitText" not in fields["gu"]
    text = c.get(f"{V1}/external/datasets/{ids['bare']}/data-card").text
    assert "| 컬럼 | 논리명 | 타입 | 단위 | NULL | 키 | 설명 | 산정기준 |" in text
    assert "| pop |  | INTEGER | 명 | Y |  | 인구 수 | 주민등록 인구 합계 |" in text


def test_data_card_social_impact_and_risk_fields(env):
    """TODO-150 — 사회적 영향·리스크 완화 권고는 메타데이터 항목, 기술적 사양은 '작성 필요' 칸 없이 안내 한 줄."""
    c, _who, ids, _run = env
    url = f"{V1}/catalog/datasets/{ids['bare']}"
    text = c.get(f"{V1}/external/datasets/{ids['bare']}/data-card").text
    assert "| 사회적 영향 | _작성 필요 — 메타데이터 탭 '사회적 영향'_ |" in text
    assert "| 리스크 완화 권고 | _작성 필요 — 메타데이터 탭 '리스크 완화 권고'_ |" in text
    tech = text.split("## 기술적 사양", 1)[1].split("\n## ", 1)[0]
    assert "작성 필요" not in tech and "해당하는 경우에만" in tech
    for k in ("재현성", "환경 설정", "매개변수/규칙"):
        assert f"| {k} |" not in text

    r = c.put(url, json={"social_impact": " 교통 정책 수립에 쓰임 ", "risk_mitigation": "개인 단위 재식별 시도 금지"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["social_impact"] == "교통 정책 수립에 쓰임" and body["risk_mitigation"] == "개인 단위 재식별 시도 금지"
    text = c.get(f"{V1}/external/datasets/{ids['bare']}/data-card").text
    assert "| 사회적 영향 | 교통 정책 수립에 쓰임 |" in text and "| 리스크 완화 권고 | 개인 단위 재식별 시도 금지 |" in text
    cr = c.get(f"{V1}/external/datasets/{ids['bare']}/croissant").json()
    assert cr["rai:dataSocialImpact"] == "교통 정책 수립에 쓰임"
    dcat = c.get(f"{V1}/external/datasets/{ids['bare']}/dcat").json()
    assert dcat["rai:dataSocialImpact"] == "교통 정책 수립에 쓰임"
    assert "개인 단위 재식별 시도 금지" not in str(dcat)  # 리스크 완화 권고는 대응 속성이 없어 카드에만

    assert c.put(url, json={"social_impact": None}).status_code == 200  # 명시한 null 은 지움
    assert c.get(url).json()["social_impact"] is None


def test_checklist_items_follow_distributions(env):
    c, _who, ids, _run = env
    url = f"{V1}/catalog/datasets/{ids['bare']}"
    c.post(f"{url}/distributions", json={"access_url": "https://x.go.kr/api", "format": "API"})
    items = _items(c.get(f"{url}/ai-ready/check").json())
    assert "배포본" not in items["C05"]["evidence"]
    assert items["C27"]["status"] is None and items["C33"]["status"] is None
    c.post(f"{url}/distributions", json={"access_url": "https://x.go.kr/f", "download_url": "https://x.go.kr/f.csv",
                                         "checksum": SHA, "checksum_algorithm": "sha-256"})
    items = _items(c.get(f"{url}/ai-ready/check").json())
    assert items["C27"]["status"] == "PASS" and items["C33"]["status"] == "PASS"


def test_media_and_type_maps():
    assert ai_ready_export.media_type("parquet") == "application/vnd.apache.parquet"
    assert ai_ready_export.media_type("CSV", "text/plain") == "text/plain"
    assert ai_ready_export.croissant_type("timestamp with time zone") == "sc:DateTime"
    assert ai_ready_export.croissant_type("DECIMAL(10,2)") == "sc:Float"
    assert ai_ready_export.croissant_type("BIGINT") == "sc:Integer"


def test_external_metadata_has_ai_ready_summary(env):
    c, _who, ids, run = env
    run(_fill_everything(ids["bare"], ids["src"]))
    body = c.get(f"{V1}/external/datasets/{ids['bare']}/metadata", params={"no_cache": True}).json()
    s = body["ai_ready"]
    assert s["required_rate"] == 100.0 and s["unmet_required"] == []
