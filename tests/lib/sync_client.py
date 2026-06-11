# SPDX-License-Identifier: Apache-2.0
"""Thin REST client for argus-catalog-metadata-sync."""

from __future__ import annotations

from typing import Any

import requests


class SyncClient:
    """``/sync/{platform}/*`` 엔드포인트 호출 래퍼."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def health(self) -> bool:
        try:
            res = self.session.get(f"{self.base_url}/health", timeout=self.timeout)
            return res.status_code == 200
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------
    # Iceberg REST Catalog
    # ------------------------------------------------------------------

    def update_iceberg_rest_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = self.session.put(
            f"{self.base_url}/sync/iceberg-rest/connection",
            json=payload,
            timeout=self.timeout,
        )
        res.raise_for_status()
        return res.json()

    def test_iceberg_rest(self) -> dict[str, Any]:
        res = self.session.post(
            f"{self.base_url}/sync/iceberg-rest/test", timeout=self.timeout,
        )
        res.raise_for_status()
        return res.json()

    # ------------------------------------------------------------------
    # Generic platform operations
    # ------------------------------------------------------------------

    def run_now(self, platform: str) -> dict[str, Any]:
        """플랫폼 단위 즉시 동기화. 응답에 created/updated/failed 포함."""
        res = self.session.post(
            f"{self.base_url}/sync/{platform}/run", timeout=self.timeout,
        )
        res.raise_for_status()
        return res.json()

    def get_status(self, platform: str) -> dict[str, Any]:
        res = self.session.get(
            f"{self.base_url}/sync/{platform}/status", timeout=self.timeout,
        )
        res.raise_for_status()
        return res.json()
