# SPDX-License-Identifier: Apache-2.0
"""Argus Catalog REST 클라이언트 — 표준 라이브러리만 사용 (quality 배치와 동일 패턴).

에이전트가 필요로 하는 최소 표면만 노출한다:
  - 데이터셋/스키마/샘플/용어집 컨텍스트 읽기
  - 제안 반입(suggestions/import) 또는 직접 적용(PUT)
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


class CatalogClient:
    """Argus Catalog API 의 최소 클라이언트 (로그인 토큰 자동 첨부)."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.token: str | None = None
        self.token = self._login(username, password)

    # ------------------------------------------------------------------ 공통
    def _request(self, method: str, path: str, payload: dict | None = None):
        """JSON 요청/응답. HTTP 오류는 본문을 붙여 RuntimeError 로 승격."""
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req) as resp:
                body = resp.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise RuntimeError(f"API 오류 {e.code} {method} {path}: {detail}") from e

    def _login(self, username: str, password: str) -> str:
        data = self._request("POST", "/auth/login",
                             {"username": username, "password": password})
        return data["access_token"]

    # ------------------------------------------------------- 컨텍스트 읽기
    def get_dataset_by_urn(self, urn: str) -> dict:
        """URN 으로 데이터셋 상세(스키마 포함)를 조회한다."""
        return self._request(
            "GET", f"/catalog/datasets/urn/{urllib.parse.quote(urn, safe='')}")

    def get_dataset(self, dataset_id: int) -> dict:
        return self._request("GET", f"/catalog/datasets/{dataset_id}")

    def list_datasets_by_datasource(self, datasource_id: str) -> list[dict]:
        """데이터 소스에 등록된 모든 데이터셋 (페이지네이션 순회)."""
        items: list[dict] = []
        page = 1
        while True:
            data = self._request(
                "GET",
                f"/catalog/datasets?datasource={urllib.parse.quote(datasource_id)}"
                f"&page={page}&page_size=100",
            )
            items.extend(data.get("items", []))
            if page * 100 >= int(data.get("total", 0)):
                return items
            page += 1

    def get_sample_rows(self, dataset_id: int, limit: int = 5) -> dict | None:
        """샘플 데이터 (없으면 None) — 프롬프트 컨텍스트 보강용."""
        try:
            return self._request("GET", f"/catalog/datasets/{dataset_id}/sample?limit={limit}")
        except RuntimeError:
            return None  # 샘플 미등록은 정상 — 컨텍스트에서 생략

    def get_glossary_terms(self, limit: int = 50) -> list[dict]:
        """용어집 TERM 목록 — 설명 생성 시 용어 일관성 컨텍스트.

        실제 라우트는 ``/catalog/glossary`` 이며 응답은 GlossaryTermResponse
        목록(필드: name·description·term_type)이다. CATEGORY 노드는 제외하고
        TERM 만 추린다.
        """
        try:
            data = self._request("GET", "/catalog/glossary")
            terms = [n for n in (data if isinstance(data, list) else data.get("items", []))
                     if (n.get("term_type") or "TERM") == "TERM"]
            return terms[:limit]
        except RuntimeError as e:
            # 용어집이 비어 있거나 조회 실패해도 생성은 계속한다 (컨텍스트만 누락)
            logger.warning("용어집 조회 실패 — 용어 컨텍스트 없이 진행: %s", e)
            return []

    # ------------------------------------------------------- 결과 쓰기
    def import_suggestions(self, dataset_id: int, items: list[dict],
                           provider: str, model: str) -> dict:
        """제안 반입 — UI 의 AI 제안 목록에서 사람이 승인/거절 (admin 전용)."""
        return self._request(
            "POST", f"/ai/datasets/{dataset_id}/suggestions/import",
            {"provider": provider, "model": model, "items": items},
        )

    def update_dataset(self, dataset_id: int, fields: dict) -> dict:
        """직접 적용 모드 — 데이터셋 부분 수정 (admin 전용)."""
        return self._request("PUT", f"/catalog/datasets/{dataset_id}", fields)
