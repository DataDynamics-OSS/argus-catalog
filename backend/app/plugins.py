# SPDX-License-Identifier: Apache-2.0
"""플러그인 로더 — 고객사별 커스텀 화면/API 확장 지점(seam).

코어는 이 파일 하나로 "확장 지점"만 제공한다. 고객사 플러그인은 코어를 수정하지
않고 별도의 pip 패키지로 배포되며, 패키지의 ``pyproject.toml`` 에 다음과 같이
entry point 를 선언하면 자동으로 발견·등록된다::

    [project.entry-points."argus.plugins"]
    acme = "argus_plugin_acme:plugin"

여기서 ``argus_plugin_acme.plugin`` 은 ``router`` 속성(FastAPI ``APIRouter``)을
가진 객체/모듈이면 된다. 선택적으로 ``prefix`` 속성으로 라우터 prefix 를 덮어쓸 수
있다(기본 ``/api/v1``).

활성화는 **명시 허용(allowlist)** 방식이다. 설정 ``plugins.enabled`` 에 이름이 적힌
플러그인만 로드하며, 비워 두면(기본) 설치돼 있어도 아무 것도 로드하지 않는다. 의존성
설치 중 ``argus.plugins`` entry point 를 가진 패키지가 딸려 들어와도 설정 없이 라우터가
붙지 않게 하기 위함이다. 개발 편의를 위해 ``*`` 를 주면 발견된 플러그인을 모두 로드한다.

플러그인을 켜지 않으면 아무 것도 등록되지 않으므로 **OSS 기본 동작과 완전히 동일**
하다. 코어 라우터에는 어떤 영향도 주지 않는다.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: entry point 그룹 이름 — 플러그인 패키지가 라우터를 등록하는 채널.
PLUGIN_GROUP = "argus.plugins"

#: 코어 라우터와 동일한 기본 API prefix.
DEFAULT_PREFIX = "/api/v1"

#: ``plugins.enabled`` 에 이 값이 있으면 발견된 플러그인을 모두 로드한다(개발용).
ENABLE_ALL = "*"


def load_plugin_routers(app: "FastAPI", enabled: list[str] | None = None) -> list[str]:
    """발견된 플러그인 라우터를 ``app`` 에 등록한다.

    Args:
        app: FastAPI 애플리케이션.
        enabled: 로드를 허용할 플러그인 이름 목록. ``None`` 이거나 빈 목록이면
            아무 것도 로드하지 않는다. ``"*"`` 를 포함하면 발견된 플러그인을 모두 로드한다.

    Returns:
        실제로 등록에 성공한 플러그인 이름 목록.
    """
    allow = set(enabled or [])
    allow_all = ENABLE_ALL in allow
    loaded: list[str] = []
    discovered: list[str] = []
    logger.debug("플러그인 탐색 시작: group=%s enabled=%s", PLUGIN_GROUP, sorted(allow))

    for ep in entry_points(group=PLUGIN_GROUP):
        discovered.append(ep.name)
        if not allow_all and ep.name not in allow:
            logger.info("플러그인 '%s' 비활성(plugins.enabled 미포함) — 건너뜀", ep.name)
            continue
        try:
            plugin = ep.load()
        except Exception:  # noqa: BLE001 - 한 플러그인 실패가 서버 기동을 막지 않도록 격리
            logger.exception("플러그인 '%s' 로드 실패 — 건너뜀", ep.name)
            continue

        router = getattr(plugin, "router", None)
        if router is None:
            logger.warning("플러그인 '%s' 에 router 속성이 없음 — 건너뜀", ep.name)
            continue

        # prefix 를 지정하지 않았거나 None/빈 문자열이면 기본 prefix 를 쓴다.
        prefix = getattr(plugin, "prefix", None) or DEFAULT_PREFIX
        try:
            app.include_router(router, prefix=prefix)
        except Exception:  # noqa: BLE001
            logger.exception("플러그인 '%s' 라우터 등록 실패 — 건너뜀", ep.name)
            continue

        loaded.append(ep.name)
        logger.info("플러그인 '%s' 등록 완료 (prefix=%s)", ep.name, prefix)

    if loaded:
        logger.info("플러그인 %d개 로드됨: %s", len(loaded), ", ".join(loaded))

    # 켜 달라고 했는데 설치돼 있지 않은 이름 — 오타·설치 누락을 찾기 쉽도록 알린다.
    missing = sorted(allow - set(discovered) - {ENABLE_ALL})
    if missing:
        logger.warning("plugins.enabled 에 있으나 설치되지 않은 플러그인: %s", ", ".join(missing))
    return loaded
