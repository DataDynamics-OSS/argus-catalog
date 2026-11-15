# SPDX-License-Identifier: Apache-2.0
"""시스템 상태는 ACTIVE · INACTIVE · DEPRECATED 만 받는다 (TODO-103)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.catalog.schemas import SystemCreate, SystemUpdate


@pytest.mark.parametrize("status", ["ACTIVE", "INACTIVE", "DEPRECATED"])
def test_allowed_status(status: str) -> None:
    assert SystemCreate(name="재무", status=status).status == status
    assert SystemUpdate(status=status).status == status


@pytest.mark.parametrize("status", ["active", "운영 중", "RETIRED", ""])
def test_rejects_unknown_status(status: str) -> None:
    with pytest.raises(ValidationError):
        SystemCreate(name="재무", status=status)
    with pytest.raises(ValidationError):
        SystemUpdate(status=status)


def test_default_and_omitted() -> None:
    assert SystemCreate(name="재무").status == "ACTIVE"
    assert "status" not in SystemUpdate(name="재무").model_dump(exclude_unset=True)
    with pytest.raises(ValidationError):
        SystemUpdate(status=None)  # 명시한 null 은 거부(기존 규칙)
