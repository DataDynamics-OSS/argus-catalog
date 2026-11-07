# SPDX-License-Identifier: Apache-2.0
"""Argus Catalog SDK — 모델 저장소(OCI 모델 허브·MLflow 레지스트리)와 AI 에이전트 정의 파일을 다루는 Python 클라이언트.

- ``ModelClient``: 카탈로그 REST API 클라이언트 (``argus_catalog_sdk.client``)
- ``ApiError``: 서버 오류(``status_code``·``detail``), ``RuntimeError`` 를 상속
- 명령줄 도구 ``argus-model``·``argus-catalog``: ``argus_catalog_sdk.cli``
- 정의 파일 반영·정리·내보내기: ``argus_catalog_sdk.agents``

버전은 ``__version__`` 한 곳에만 두고 ``pyproject.toml`` 이 읽어 간다.
"""

from argus_catalog_sdk.client import ApiError, ModelClient

__all__ = ["ApiError", "ModelClient"]
__version__ = "0.5.0"
