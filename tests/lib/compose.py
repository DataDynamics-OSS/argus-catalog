"""Thin wrapper around the ``docker compose`` CLI.

testcontainers-python 을 직접 쓰는 대신 compose 파일을 그대로 사용한다.
멀티 서비스 의존 그래프(Lakekeeper + MinIO + PG 같은)는 yml 한 파일로
선언하는 편이 훨씬 명확하고, CI 와 로컬에서 동일하게 동작한다.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable

import requests


class ComposeStack:
    """``docker compose -f <file> -p <project>`` 한 묶음을 다루는 헬퍼."""

    def __init__(self, compose_file: Path, project_name: str) -> None:
        self.compose_file = Path(compose_file).resolve()
        self.project_name = project_name

    def _cmd(self, *args: str) -> list[str]:
        return [
            "docker", "compose",
            "-f", str(self.compose_file),
            "-p", self.project_name,
            *args,
        ]

    def up(self, wait: bool = True) -> None:
        """``up -d``. ``wait=True`` 면 compose 의 자체 healthcheck 까지 대기."""
        args = ["up", "-d"]
        if wait:
            args.append("--wait")
        subprocess.run(self._cmd(*args), check=True)

    def down(self, volumes: bool = True) -> None:
        args = ["down"]
        if volumes:
            args.append("-v")
        subprocess.run(self._cmd(*args), check=False)

    def logs(self, service: str | None = None, tail: int = 200) -> str:
        args = ["logs", f"--tail={tail}"]
        if service:
            args.append(service)
        result = subprocess.run(
            self._cmd(*args), check=False, capture_output=True, text=True,
        )
        return result.stdout + result.stderr


def wait_for_http(
    url: str,
    timeout: float = 60.0,
    interval: float = 1.0,
    predicate: Callable[[requests.Response], bool] | None = None,
) -> None:
    """HTTP 엔드포인트가 준비될 때까지 폴링.

    ``predicate`` 가 주어지면 응답을 추가로 검사. 기본은 ``status_code < 500``.
    """
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            res = requests.get(url, timeout=3)
            if predicate is None:
                if res.status_code < 500:
                    return
            else:
                if predicate(res):
                    return
        except requests.RequestException as e:
            last_err = e
        time.sleep(interval)
    raise TimeoutError(
        f"Service at {url} did not become ready within {timeout:.0f}s "
        f"(last error: {last_err})"
    )
