"""Thin REST client for argus-catalog-server."""

from __future__ import annotations

import os
from typing import Any

import requests


class ArgusClient:
    """``/api/v1/catalog/*`` 엔드포인트 호출 래퍼."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        # catalog server 가 admin 인증을 요구하는 환경에서는 사전에 발급한 access token
        # 을 ``ARGUS_CATALOG_TOKEN`` 환경변수로 받아 모든 요청에 자동 부착.
        token = os.environ.get("ARGUS_CATALOG_TOKEN", "")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> bool:
        try:
            res = self.session.get(f"{self.base_url}/health", timeout=self.timeout)
            return res.status_code == 200
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------
    # Platforms
    # ------------------------------------------------------------------

    def list_datasources(self) -> list[dict[str, Any]]:
        res = self.session.get(
            f"{self.base_url}/api/v1/catalog/datasources", timeout=self.timeout,
        )
        res.raise_for_status()
        data = res.json()
        return data.get("items", data) if isinstance(data, dict) else data

    def find_datasource(self, datasource_id: str) -> dict[str, Any] | None:
        for d in self.list_datasources():
            if d.get("datasource_id") == datasource_id:
                return d
        return None

    def find_datasource_by_type(self, datasource_type: str) -> dict[str, Any] | None:
        for d in self.list_datasources():
            if d.get("type") == datasource_type:
                return d
        return None

    def create_datasource(
        self, name: str, datasource_type: str, origin: str = "DEV",
    ) -> dict[str, Any]:
        """카탈로그에 데이터소스를 등록한다(datasource_id 는 서버가 생성). 멱등 헬퍼."""
        res = self.session.post(
            f"{self.base_url}/api/v1/catalog/datasources",
            json={"name": name, "type": datasource_type, "origin": origin},
            timeout=self.timeout,
        )
        res.raise_for_status()
        return res.json()

    def ensure_datasource(
        self, datasource_type: str, name: str, origin: str = "DEV",
    ) -> str:
        """type 의 데이터소스가 있으면 그 datasource_id 를, 없으면 등록 후 반환(find-or-create)."""
        ds = self.find_datasource_by_type(datasource_type)
        if ds:
            return ds["datasource_id"]
        return self.create_datasource(name, datasource_type, origin)["datasource_id"]

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    def list_datasets(
        self,
        datasource: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if datasource:
            params["datasource"] = datasource
        res = self.session.get(
            f"{self.base_url}/api/v1/catalog/datasets",
            params=params,
            timeout=self.timeout,
        )
        res.raise_for_status()
        return res.json()

    def get_dataset_by_urn(self, urn: str) -> dict[str, Any] | None:
        res = self.session.get(
            f"{self.base_url}/api/v1/catalog/datasets/urn/{urn}",
            timeout=self.timeout,
        )
        if res.status_code == 404:
            return None
        res.raise_for_status()
        return res.json()

    def delete_dataset(self, dataset_id: int) -> None:
        res = self.session.delete(
            f"{self.base_url}/api/v1/catalog/datasets/{dataset_id}",
            timeout=self.timeout,
        )
        # 200 / 204 모두 허용. 멱등 정리 용도라 404 도 무시.
        if res.status_code not in (200, 204, 404):
            res.raise_for_status()

    # ------------------------------------------------------------------
    # Sample data
    # ------------------------------------------------------------------

    def upload_sample(self, dataset_id: int, csv_bytes: bytes, filename: str = "sample.csv") -> dict[str, Any]:
        """CSV bytes 를 dataset 의 sample 로 업로드. 서버는 100 KB 까지 허용."""
        res = self.session.post(
            f"{self.base_url}/api/v1/catalog/datasets/{dataset_id}/sample",
            files={"file": (filename, csv_bytes, "text/csv")},
            timeout=self.timeout,
        )
        res.raise_for_status()
        return res.json()

    def convert_sample_to_parquet(self, dataset_id: int) -> dict[str, Any]:
        """업로드된 CSV sample 을 parquet 으로 변환."""
        res = self.session.post(
            f"{self.base_url}/api/v1/catalog/datasets/{dataset_id}/sample/convert-to-parquet",
            timeout=self.timeout,
        )
        res.raise_for_status()
        return res.json()

    # ------------------------------------------------------------------
    # Cleanup helper
    # ------------------------------------------------------------------

    def delete_all_datasets_for(self, datasource_id: str) -> int:
        """주어진 ``datasource_id`` 의 모든 dataset 을 제거. 테스트 멱등성을 위해 사용."""
        if not self.find_datasource(datasource_id):
            return 0
        count = 0
        while True:
            page = self.list_datasets(datasource=datasource_id, page=1, page_size=100)
            items = page.get("items", [])
            if not items:
                break
            for ds in items:
                self.delete_dataset(ds["id"])
                count += 1
        return count
