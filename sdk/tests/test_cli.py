# SPDX-License-Identifier: Apache-2.0
"""명령줄 옵션이 SDK 호출 인자로 그대로 전달되는지 (push · oci-import-hf)."""

from __future__ import annotations

import pytest

from argus_catalog_sdk import cli


class Recorder:
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def push(self, *a, **kw):
        self.calls.append(("push", a, kw))
        return {"version": 3, "file_count": 2}

    def oci_import_huggingface(self, *a, **kw):
        self.calls.append(("oci_import_huggingface", a, kw))
        return {"name": "m", "version": 1, "status": "importing"}


@pytest.fixture
def rec(monkeypatch) -> Recorder:
    r = Recorder()
    monkeypatch.setattr(cli, "_get_client", lambda args: r)
    return r


def test_push_forwards_metadata_and_presigned_threshold(rec):
    cli.main(["push", "/tmp/m", "my-model", "--owner", "ml-team", "--task", "text-generation",
              "--framework", "pytorch", "--presigned-min-mb", "1.5", "--description", "d"])
    name, args, kw = rec.calls[0]
    assert (name, args) == ("push", ("/tmp/m", "my-model"))
    assert kw == {"description": "d", "owner": "ml-team", "task": "text-generation", "framework": "pytorch",
                  "presigned_min_size": int(1.5 * 1024 * 1024)}


def test_push_defaults_to_server_upload(rec):
    cli.main(["push", "/tmp/m", "my-model"])
    assert rec.calls[0][2]["presigned_min_size"] is None


def test_oci_import_hf_forwards_metadata(rec):
    cli.main(["oci-import-hf", "org/m", "--name", "m", "--task", "fill-mask", "--framework", "pytorch",
              "--language", "ko", "--owner", "ml-team", "--no-wait"])
    _, args, kw = rec.calls[0]
    assert args == ("org/m",)
    assert kw == {"name": "m", "revision": "main", "description": None, "owner": "ml-team", "task": "fill-mask",
                  "framework": "pytorch", "language": "ko", "wait": False}


@pytest.mark.parametrize("argv", [["oci-delete", "m"], ["delete", "m"]])
def test_delete_cancel_exits_nonzero(rec, monkeypatch, argv):
    """확인 문구가 틀려 취소하면 스크립트가 알 수 있게 종료 코드 1."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "아니오")
    with pytest.raises(SystemExit) as ei:
        cli.main(argv)
    assert ei.value.code == 1
    assert rec.calls == []


class NullFields:
    """서버가 빈 값(null)을 준 목록·파일."""

    def oci_list_models(self, **kw):
        return {"total": 1, "items": [{"name": "m", "source_type": None, "version_count": None,
                                       "status": None, "updated_at": None}]}

    def list_models(self, **kw):
        return {"total": 1, "items": [{"name": "m", "owner": None, "max_version_number": None,
                                       "latest_version_status": None, "status": None, "updated_at": None}]}

    def list_files(self, name, version):
        return [{"filename": "a", "size": None, "last_modified": None}]


@pytest.mark.parametrize("argv", [["oci-list"], ["list"], ["files", "m", "1"]])
def test_tables_tolerate_null_fields(monkeypatch, argv):
    monkeypatch.setattr(cli, "_get_client", lambda args: NullFields())
    cli.main(argv)  # TypeError 없이 표를 그린다
