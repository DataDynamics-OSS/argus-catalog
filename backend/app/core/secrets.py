# SPDX-License-Identifier: Apache-2.0
"""운영 시크릿(환경변수) 점검 — 개발용 기본값으로 운영되는 것을 막는다.

- ``ARGUS_SECRET_KEY``: 외부 연동 자격증명(API 카탈로그 자격증명·페더레이션 peer 토큰) Fernet 암호화 키
- ``ARGUS_JWT_SECRET``: 로컬 로그인 JWT 서명키

두 값 모두 미설정이면 개발 편의를 위해 소스에 박힌 기본값을 쓰는데, 이 값은 공개돼 있으므로
운영에서 쓰면 저장된 자격증명을 누구나 복호화하거나 토큰을 위조할 수 있다.

``ARGUS_REQUIRE_SECRETS=true`` (운영 배포 — ``deploy/docker-compose.prod.yml``) 이면
미설정·기본값·너무 짧은 값일 때 서버/워커가 기동하지 않는다. 그 외(개발·통합 테스트)는
기존처럼 기본값으로 동작하고 경고만 남긴다.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

REQUIRE_ENV = "ARGUS_REQUIRE_SECRETS"
MIN_SECRET_LENGTH = 16

# 개발용 기본값 — 미설정 시에만 쓰인다(운영 금지). crypto·auth 모듈이 이 값을 사용한다.
DEV_SECRET_KEY = "argus-catalog-dev-secret-change-me"
DEV_JWT_SECRET = "argus-catalog-local-jwt-secret-key-change-in-production"

# 환경변수 이름 → 개발용 기본값
_SECRETS: dict[str, str] = {
    "ARGUS_SECRET_KEY": DEV_SECRET_KEY,
    "ARGUS_JWT_SECRET": DEV_JWT_SECRET,
}


class InsecureSecretError(RuntimeError):
    """운영 시크릿 요구(ARGUS_REQUIRE_SECRETS) 상태에서 시크릿이 없거나 안전하지 않음."""


def secrets_required() -> bool:
    """ARGUS_REQUIRE_SECRETS 가 1/true/yes/on 이면 True(운영 모드)."""
    return os.environ.get(REQUIRE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def secret_problem(name: str) -> str | None:
    """환경변수 하나의 문제(없으면 None) — 미설정 / 개발용 기본값 / 최소 길이 미만."""
    value = (os.environ.get(name) or "").strip()
    if not value:
        return f"{name} 가 설정되지 않았습니다"
    if value == _SECRETS.get(name):
        return f"{name} 가 개발용 기본값입니다"
    if len(value) < MIN_SECRET_LENGTH:
        return f"{name} 가 너무 짧습니다(최소 {MIN_SECRET_LENGTH}자)"
    return None


def check_required_secrets() -> None:
    """``ARGUS_REQUIRE_SECRETS`` 가 켜져 있으면 모든 운영 시크릿을 점검해 문제가 있으면 예외.

    서버(lifespan)·변경관리 워커 시작 시 호출한다. 꺼져 있으면 아무것도 하지 않는다
    (개발용 기본값 경고는 각 모듈이 남긴다).
    """
    if not secrets_required():
        return
    problems = [p for name in _SECRETS if (p := secret_problem(name))]
    if problems:
        logger.warning("운영 시크릿 점검 실패: problems=%d", len(problems))
        raise InsecureSecretError(
            f"{REQUIRE_ENV}=true 인데 운영 시크릿이 안전하지 않아 기동하지 않습니다: "
            + "; ".join(problems)
            + " — `openssl rand -base64 36` 등으로 생성한 값을 환경변수로 설정하세요."
        )
    logger.debug("운영 시크릿 점검 통과: names=%s", list(_SECRETS))


def resolve_secret(name: str) -> str:
    """시크릿 값(미설정이면 개발용 기본값). 요구 상태에서 안전하지 않으면 예외 — 기동 점검을 거치지 않는 경로 대비.

    요구 상태에선 이 값 하나가 아니라 모든 운영 시크릿을 점검해 한 번에 알린다
    (auth 모듈 로드 시 먼저 걸리더라도 고칠 항목이 모두 보이도록).
    """
    if secrets_required():
        check_required_secrets()
    # 값은 가공하지 않는다(공백을 지우면 기존에 저장된 암호문의 키가 달라짐)
    return os.environ.get(name) or _SECRETS[name]
