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
    """Argus Catalog API 의 최소 클라이언트 — API 키(기본) 또는 로그인 토큰(이전 방식)을 첨부한다."""

    def __init__(self, base_url: str, username: str = "", password: str = "", timeout: float = 30,
                 *, api_key: str = "") -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.timeout = timeout  # 응답 없는 API 에 무한정 매달리지 않도록 (DEF-038)
        # API 키는 만료·갱신이 없어 그대로 Bearer 로 보낸다(로그인 없음, TODO-056)
        self._api_key = api_key
        # 토큰 만료(로컬 8시간) 뒤 다시 로그인하려고 자격 증명을 들고 있는다 (DEF-036)
        self._username = username
        self._password = password
        self.token: str | None = None
        self.token = api_key or self._login(username, password)

    # ------------------------------------------------------------------ 공통
    def _request(self, method: str, path: str, payload: dict | None = None, *, _retry: bool = True):
        """JSON 요청/응답. HTTP 오류는 본문을 붙여 RuntimeError 로 승격.

        로그인 뒤 요청이 401 이면 토큰이 만료된 것으로 보고 한 번 다시 로그인해 재시도한다
        (worker 처럼 오래 도는 프로세스, DEF-036).
        """
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
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            if e.code == 401 and _retry and self.token and not self._api_key and path != "/auth/login":
                logger.info("카탈로그 토큰 만료로 보임(401) — 다시 로그인 후 재시도: %s %s", method, path)
                self.token = None
                self.token = self._login(self._username, self._password)
                return self._request(method, path, payload, _retry=False)
            detail = e.read().decode(errors="replace")[:400]
            raise RuntimeError(f"API 오류 {e.code} {method} {path}: {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            # 연결 실패·타임아웃도 호출부가 다루는 RuntimeError 로 맞춘다
            raise RuntimeError(f"API 연결 실패 {method} {path}: {e}") from e

    def _login(self, username: str, password: str) -> str:
        """``/auth/login`` 으로 로그인해 access token 을 받는다.

        실패 시 :meth:`_request` 가 RuntimeError 를 던진다 (비밀번호·토큰은 로그에 남기지 않음).
        """
        logger.debug("카탈로그 로그인 시도: base=%s username=%s", self.base, username)
        data = self._request("POST", "/auth/login",
                             {"username": username, "password": password})
        logger.info("카탈로그 로그인 성공: username=%s", username)
        return data["access_token"]

    # ------------------------------------------------------- 컨텍스트 읽기
    def get_dataset_by_urn(self, urn: str) -> dict:
        """URN 으로 데이터셋 상세(스키마 포함)를 조회한다."""
        return self._request(
            "GET", f"/catalog/datasets/urn/{urllib.parse.quote(urn, safe='')}")

    def get_dataset(self, dataset_id: int) -> dict:
        """데이터셋 id 로 상세(스키마 포함)를 조회한다."""
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
                logger.debug("데이터 소스 데이터셋 목록 조회 완료: datasource=%s count=%d pages=%d",
                             datasource_id, len(items), page)
                return items
            page += 1

    def get_sample_rows(self, dataset_id: int, limit: int = 5) -> dict | None:
        """샘플 데이터 (없으면 None) — 프롬프트 컨텍스트 보강용."""
        try:
            return self._request("GET", f"/catalog/datasets/{dataset_id}/sample?limit={limit}")
        except RuntimeError as e:
            # 샘플 미등록은 정상 — 컨텍스트에서 생략
            logger.debug("샘플 데이터 없음 — 컨텍스트에서 생략: dataset_id=%s reason=%s", dataset_id, e)
            return None

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

    def list_pending_suggestions(self, dataset_id: int) -> list[dict]:
        """데이터셋의 미적용 AI 제안 목록 — worker 가 중복 반입을 피하는 데 쓴다 (DEF-037)."""
        data = self._request("GET", f"/ai/datasets/{dataset_id}/suggestions")
        return data if isinstance(data, list) else []

    # ------------------------------------------------------- 결과 쓰기
    def import_suggestions(self, dataset_id: int, items: list[dict],
                           provider: str, model: str) -> dict:
        """제안 반입 — UI 의 AI 제안 목록에서 사람이 승인/거절 (admin 전용)."""
        logger.info("AI 제안 반입: dataset_id=%s count=%d model=%s", dataset_id, len(items), model)
        return self._request(
            "POST", f"/ai/datasets/{dataset_id}/suggestions/import",
            {"provider": provider, "model": model, "items": items},
        )

    def update_dataset(self, dataset_id: int, fields: dict) -> dict:
        """직접 적용 모드 — 데이터셋 부분 수정 (admin 전용)."""
        logger.info("데이터셋 직접 수정: dataset_id=%s fields=%s", dataset_id, sorted(fields))
        return self._request("PUT", f"/catalog/datasets/{dataset_id}", fields)
