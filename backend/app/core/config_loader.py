# SPDX-License-Identifier: Apache-2.0
"""properties 변수 치환을 지원하는 설정 로더.

config.properties(Java 스타일 key=value)와 config.yml 을 읽어,
YAML 값의 ${variable} 자리표시자를 properties 값으로 치환한다.
"""

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")

DEFAULT_CONFIG_DIR = Path("/etc/argus-catalog-server")


def load_properties(path: Path) -> dict[str, str]:
    """Java 스타일 .properties 파일을 dict 로 읽어들인다.

    ``#``/``!`` 로 시작하는 줄은 주석. 줄에 ``=`` 가 있으면 첫 ``=`` 를, 없으면 첫 ``:`` 를
    구분자로 쓴다. 줄 이어쓰기·이스케이프는 지원하지 않는다. 파일이 없으면 빈 dict.
    """
    props: dict[str, str] = {}

    if not path.is_file():
        logger.debug("properties 파일을 찾을 수 없음: %s", path)
        return props

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue

            for sep in ("=", ":"):
                idx = line.find(sep)
                if idx >= 0:
                    key = line[:idx].strip()
                    value = line[idx + 1 :].strip()
                    props[key] = value
                    break

    logger.debug("properties 로드: path=%s keys=%d", path, len(props))
    return props


def _resolve_value(value: str, props: dict[str, str]) -> str:
    """${variable} 또는 ${variable:default} 자리표시자를 치환한다."""

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default_value = match.group(2)

        if var_name in props:
            return props[var_name]

        if default_value is not None:
            return default_value

        logger.warning("치환되지 않은 변수: ${%s}", var_name)
        return match.group(0)

    return _VAR_PATTERN.sub(replacer, value)


def _resolve_dict(data: dict[str, Any], props: dict[str, str]) -> dict[str, Any]:
    """dict 안의 ${variable} 자리표시자를 재귀적으로 치환한다.

    리스트는 한 단계만(문자열 항목만) 치환하고, 리스트 안의 dict 는 그대로 둔다.
    """
    resolved = {}
    for key, value in data.items():
        if isinstance(value, str):
            resolved[key] = _resolve_value(value, props)
        elif isinstance(value, dict):
            resolved[key] = _resolve_dict(value, props)
        elif isinstance(value, list):
            resolved[key] = [
                _resolve_value(item, props) if isinstance(item, str) else item for item in value
            ]
        else:
            resolved[key] = value
    return resolved


def load_yaml(path: Path) -> dict[str, Any]:
    """YAML 파일을 읽어 dict 로 반환한다."""
    if not path.is_file():
        logger.debug("YAML 설정 파일을 찾을 수 없음: %s", path)
        return {}

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return data if isinstance(data, dict) else {}


def load_config(
    config_dir: Path | None = None,
    yaml_file: str = "config.yml",
    properties_file: str = "config.properties",
    yaml_path: Path | str | None = None,
    properties_path: Path | str | None = None,
) -> dict[str, Any]:
    """properties 변수로 YAML 을 치환하여 설정을 로드한다.

    yaml_path / properties_path 를 주면 config_dir 대신 그 경로를 쓴다. YAML 이 없거나 비면 빈 dict.
    """
    base_dir = config_dir or DEFAULT_CONFIG_DIR

    props_file = Path(properties_path) if properties_path else base_dir / properties_file
    yaml_config_file = Path(yaml_path) if yaml_path else base_dir / yaml_file

    props = load_properties(props_file)
    raw_config = load_yaml(yaml_config_file)

    if not raw_config:
        logger.warning("설정 YAML 이 없거나 비어 있음 — 기본값 사용: %s", yaml_config_file)
        return {}

    logger.debug("설정 로드: yaml=%s properties=%s", yaml_config_file, props_file)
    return _resolve_dict(raw_config, props)
