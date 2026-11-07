# SPDX-License-Identifier: Apache-2.0
"""데이터 소스 접속 설정의 비밀값 보관 규칙.

``catalog_datasource_configurations.config_json`` 에는 접속 정보 전체가 JSON 으로 들어
있다. 이 중 비밀값(비밀번호·토큰·시크릿 등)은 ``enc:`` 접두사가 붙은 Fernet 암호문으로만
저장하고(``ARGUS_SECRET_KEY`` 파생 키, ``app.apis.crypto``), API 응답에는 절대 내보내지
않는다(빈 문자열로 마스킹 + ``secret_fields`` 로 "저장돼 있음"만 알림).

- 저장(:func:`seal_config`): 비밀 키의 값이 빈 문자열이면 "변경 안 함"으로 보고 기존
  암호문을 유지한다(마스킹된 값을 그대로 다시 PUT 해도 비밀번호가 지워지지 않도록).
  키 자체가 빠졌으면(예: 인증 방식 변경으로 필드가 사라짐) 기존 값도 버린다.
- 사용(:func:`unseal_config`): 동기화·연결 테스트·품질 프로파일링처럼 서버 내부에서
  실제 접속할 때만 복호화한다. 접두사 없는 값은 암호화 도입 전 평문(레거시)으로 보고
  그대로 쓴다 — 해당 설정을 한 번 저장하면 암호문으로 바뀐다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from cryptography.fernet import InvalidToken

from app.apis.crypto import decrypt, encrypt

logger = logging.getLogger(__name__)

_PREFIX = "enc:"

# 키 이름으로 비밀값을 판별한다(프론트 datasource-configs.ts 의 password 필드와 대응).
# password / ldap_password / sasl_password / secret_access_key / oauth_client_secret /
# token / credential / credentials_json / connection_string(자격증명 포함 가능)
_SECRET_KEY_RE = re.compile(r"(password|passwd|secret|token|credential|connection_string)", re.I)


# 접속 "대상"을 정하는 키 — 이 값이 바뀌면 저장된 비밀값을 이어 쓰지 않는다.
# host / port / hosts / uri / api_url / endpoint_url / schema_registry_url / bootstrap_servers /
# master_addresses / metastore_host·port / namenode_host·port / account(Snowflake)
_TARGET_KEY_RE = re.compile(r"(^|_)(host|hosts|port|url|uri|endpoint|address|addresses|servers|account)$", re.I)


class DatasourceSecretError(RuntimeError):
    """저장된 비밀값을 복호화하지 못함(ARGUS_SECRET_KEY 변경 등)."""


class SecretTargetChangedError(ValueError):
    """접속 대상이 바뀌었는데 비밀값을 비워 보내 저장값을 이어 쓰려 함.

    저장된 비밀번호를 다른(임의의) 서버로 보내는 유출 경로를 막는다 — 대상이 바뀌면
    비밀값을 다시 입력해야 한다.
    """


def is_secret_key(key: str) -> bool:
    """키 이름이 비밀값 패턴(부분 일치, 대소문자 무시)에 맞으면 True."""
    return bool(_SECRET_KEY_RE.search(key or ""))


def _is_sealed(value: Any) -> bool:
    """``enc:`` 접두사가 붙은 암호문 문자열인지."""
    return isinstance(value, str) and value.startswith(_PREFIX)


def _load(stored: str | dict | None) -> dict:
    """저장값(JSON 문자열 또는 dict)을 새 dict 로 읽는다. 파싱 실패·dict 아님이면 빈 dict."""
    if stored is None:
        return {}
    if isinstance(stored, dict):
        return dict(stored)
    try:
        data = json.loads(stored) if stored else {}
    except ValueError:
        logger.warning("데이터 소스 접속 설정 JSON 파싱 실패 — 빈 설정으로 처리: length=%d", len(stored))
        return {}
    return data if isinstance(data, dict) else {}


def seal_config(new_config: dict, old_stored: str | dict | None = None) -> dict:
    """요청 config → 저장용 config. 비밀값은 암호화, 빈 비밀값은 기존 값 유지.

    요청에 없는 키는 결과에서도 빠진다(기존 비밀값 포함). 기존 값이 레거시 평문이면
    이어받으면서 암호화한다. ``enc:`` 로 시작하는 값을 보내면 위조로 보고 기존 값을 유지한다.
    """
    old = _load(old_stored)
    out: dict = {}
    for key, value in (new_config or {}).items():
        if not is_secret_key(key):
            out[key] = value
            continue
        if value is None or value == "":
            # 변경 안 함 — 기존 저장값(암호문/레거시 평문)을 그대로 이어받는다.
            prev = old.get(key)
            if prev not in (None, ""):
                out[key] = prev if _is_sealed(prev) else _PREFIX + encrypt(str(prev))
            continue
        if _is_sealed(value):
            # 클라이언트가 암호문을 보낼 일은 없다 — 위조된 값으로 덮어쓰지 않도록 기존 값 유지.
            logger.warning("암호문 형식의 비밀값 입력 무시 — 기존 값 유지: key=%s", key)
            prev = old.get(key)
            if prev not in (None, ""):
                out[key] = prev
            continue
        out[key] = _PREFIX + encrypt(str(value))
    return out


def unseal_config(stored: str | dict | None) -> dict:
    """저장된 config → 평문 config(서버 내부 접속용). 응답에 내보내면 안 된다.

    Raises:
        DatasourceSecretError: 암호문을 현재 키로 복호화하지 못한 경우.
    """
    data = _load(stored)
    for key, value in list(data.items()):
        if is_secret_key(key) and _is_sealed(value):
            try:
                data[key] = decrypt(value[len(_PREFIX):])
            except InvalidToken as e:
                logger.warning("데이터 소스 비밀값 복호화 실패(ARGUS_SECRET_KEY 변경 의심): key=%s", key)
                raise DatasourceSecretError(
                    "데이터 소스 비밀값을 복호화하지 못했습니다"
                    "(ARGUS_SECRET_KEY 확인 후 접속 설정을 다시 저장하세요)."
                ) from e
    return data


def mask_config(stored: str | dict | None) -> tuple[dict, list[str]]:
    """저장된 config → (응답용 config, 저장된 비밀 키 목록). 비밀값은 빈 문자열."""
    data = _load(stored)
    secret_fields: list[str] = []
    for key, value in list(data.items()):
        if is_secret_key(key):
            if value not in (None, ""):
                secret_fields.append(key)
            data[key] = ""
    return data, sorted(secret_fields)


def _norm(value: Any) -> str:
    """비교용 정규화 — None 은 빈 문자열, 그 밖에는 공백 제거·소문자."""
    return "" if value is None else str(value).strip().lower()


def changed_target_keys(config: dict, stored: str | dict | None) -> list[str]:
    """요청 config 와 저장된 config 사이에서 값이 달라진 접속 대상 키."""
    new, old = dict(config or {}), _load(stored)
    keys = {k for k in set(new) | set(old) if _TARGET_KEY_RE.search(k) and not is_secret_key(k)}
    return sorted(k for k in keys if _norm(new.get(k)) != _norm(old.get(k)))


def reused_secret_keys(config: dict, stored: str | dict | None) -> list[str]:
    """비워(또는 암호문으로) 보내 저장된 값을 이어받게 되는 비밀 키."""
    old = _load(stored)
    return sorted(
        k for k, v in (config or {}).items()
        if is_secret_key(k) and (v is None or v == "" or _is_sealed(v)) and old.get(k) not in (None, "")
    )


def guard_secret_reuse(config: dict, stored: str | dict | None, *, type_changed: bool = False) -> None:
    """저장된 비밀값을 이어 쓸 요청인데 접속 대상(또는 유형)이 바뀌었으면 거부한다.

    Raises:
        SecretTargetChangedError: 비밀값 재사용 + 대상 키 변경(또는 ``type_changed``).
    """
    reused = reused_secret_keys(config, stored)
    if not reused:
        return
    changed = changed_target_keys(config, stored)
    if changed or type_changed:
        what = ", ".join(changed) if changed else "유형"
        logger.warning(
            "비밀값 재사용 거부 — 접속 대상 변경: changed=%s type_changed=%s reused_keys=%s",
            changed, type_changed, reused,
        )
        raise SecretTargetChangedError(
            f"접속 대상({what})이 저장된 설정과 다릅니다. 저장된 비밀값은 원래 대상에만 쓰므로 "
            f"비밀값({', '.join(reused)})을 다시 입력하세요."
        )


def fill_stored_secrets(config: dict, stored: str | dict | None) -> dict:
    """연결 테스트용 — 요청 config 의 빈 비밀값을 저장된 값(평문)으로 채운다.

    요청에 키가 있는 경우만 채운다. 복호화 실패 시 :class:`DatasourceSecretError`.
    """
    plain = unseal_config(stored)
    out = dict(config or {})
    for key, value in out.items():
        if is_secret_key(key) and (value is None or value == "") and plain.get(key):
            out[key] = plain[key]
    return out
