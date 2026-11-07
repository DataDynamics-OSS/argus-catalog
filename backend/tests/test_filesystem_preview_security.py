# SPDX-License-Identifier: Apache-2.0
"""파일시스템 브라우저 업로드·미리보기 보안 회귀 테스트 (자기완결형, 임시 data_dir).

- C19: 업로드 파일 이름의 디렉터리 성분(``../``·절대 경로)을 버리고, 빈 값·``.``/``..`` 는 400.
- C20: DOCX 미리보기 HTML 정리(``javascript:`` 링크·``on*`` 속성·``<script>`` 제거, 일반 서식 유지),
  PPTX 미리보기 텍스트 HTML 이스케이프.
- C21: 구형 ``.xls`` 는 미리보기 미지원 400, 확장자만 맞는 손상 파일도 500 이 아니라 400.

실행:
    cd backend
    pytest tests/test_filesystem_preview_security.py
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.core.auth import TokenUser, get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.filesystemmgr import service
from app.filesystemmgr.html_sanitizer import sanitize_html
from app.filesystemmgr.router import router as fs_router

ADMIN = TokenUser(sub="1", username="admin", roles=[settings.auth_keycloak_admin_role])


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(settings, "data_dir", d)
    return d


@pytest.fixture
async def client(data_dir):
    app = FastAPI()
    app.include_router(fs_router, prefix="/api/v1")

    async def _user():
        return ADMIN

    async def _session():
        yield None  # 관리자는 조회 범위 검사에서 DB 를 쓰지 않는다

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_session] = _session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# C19 — 업로드 파일 이름
# ---------------------------------------------------------------------------


async def test_upload_strips_directory_components(data_dir, tmp_path):
    (data_dir / "in").mkdir()
    saved = await service.save_uploaded_file("/in", "../../escape.txt", b"x")
    assert saved == "/in/escape.txt"
    assert (data_dir / "in" / "escape.txt").read_bytes() == b"x"
    assert not (tmp_path / "escape.txt").exists()

    saved = await service.save_uploaded_file("/in", "/etc/passwd", b"y")
    assert saved == "/in/passwd" and (data_dir / "in" / "passwd").exists()
    saved = await service.save_uploaded_file("/in", "..\\..\\win.txt", b"z")
    assert saved == "/in/win.txt"


@pytest.mark.parametrize("name", ["..", ".", "a/..", "", "   "])
async def test_upload_rejects_unusable_filename(data_dir, name):
    with pytest.raises(service.InvalidFilenameError):
        await service.save_uploaded_file("/", name, b"x")


async def test_upload_refuses_symlink_escape(data_dir, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    (data_dir / "link.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        await service.save_uploaded_file("/", "link.txt", b"overwrite")
    assert outside.read_text() == "keep"


async def test_upload_api_bad_filename_is_400(client):
    r = await client.post("/api/v1/filesystem/upload", params={"path": "/"}, files={"file": ("..", b"x")})
    assert r.status_code == 400, r.text
    r = await client.post("/api/v1/filesystem/upload", params={"path": "/"},
                          files={"file": ("../evil.txt", b"x")})
    assert r.status_code == 200 and r.json()["path"] == "/evil.txt"


# ---------------------------------------------------------------------------
# C20 — DOCX/PPTX 미리보기 HTML
# ---------------------------------------------------------------------------


def test_sanitizer_removes_script_vectors():
    html = sanitize_html(
        '<p onclick="x()">a<script>alert(1)</script><style>p{}</style>'
        '<a href="javascript:alert(1)">j</a><a href=" java&#9;script:alert(1)">t</a>'
        '<a href="data:text/html,<script>">d</a><img src="x" onerror="alert(1)">'
        '<iframe src="https://evil"></iframe><svg onload="alert(1)"><circle/></svg></p>'
    )
    lowered = html.lower()
    for bad in ("script", "onclick", "onerror", "onload", "javascript", "iframe", "svg", "data:text", "<style"):
        assert bad not in lowered, html
    assert "<p>a" in html and "<a>j</a>" in html


def test_sanitizer_keeps_normal_formatting():
    src = (
        '<h1>제목</h1><p><strong>굵게</strong> <em>기울임</em> &lt;태그&gt; &amp;</p>'
        '<ul><li id="footnote-1">항목</li></ul>'
        '<table><tr><td colspan="2">칸</td></tr></table>'
        '<a href="https://example.com/?a=1&amp;b=2">링크</a><a href="#footnote-1">각주</a>'
        '<a href="mailto:a@example.com">메일</a><img src="data:image/png;base64,iVBORw0KGgo=" alt="그림">'
    )
    html = sanitize_html(src)
    assert "<h1>제목</h1>" in html
    assert "<strong>굵게</strong> <em>기울임</em> &lt;태그&gt; &amp;" in html
    assert '<li id="footnote-1">항목</li>' in html
    assert '<td colspan="2">칸</td>' in html
    assert 'href="https://example.com/?a=1&amp;b=2"' in html and 'rel="noopener noreferrer"' in html
    assert '<a href="#footnote-1">각주</a>' in html
    assert 'href="mailto:a@example.com"' in html
    assert '<img src="data:image/png;base64,iVBORw0KGgo=" alt="그림">' in html


async def test_preview_docx_output_is_sanitized(data_dir, monkeypatch):
    import mammoth

    class _Result:
        value = '<p>본문<a href="javascript:alert(1)" onmouseover="x()">링크</a><script>alert(1)</script></p>'
        messages: list = []

    monkeypatch.setattr(mammoth, "convert_to_html", lambda _f: _Result())
    (data_dir / "a.docx").write_bytes(b"PK")
    resp = await service.preview_docx("/a.docx")
    assert resp.html == "<p>본문<a>링크</a></p>"


async def test_preview_pptx_escapes_text(data_dir):
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    box.text_frame.text = '<img src=x onerror="alert(1)">'
    buf = io.BytesIO()
    prs.save(buf)
    (data_dir / "s.pptx").write_bytes(buf.getvalue())

    resp = await service.preview_pptx("/s.pptx")
    assert "<img" not in resp.html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in resp.html


# ---------------------------------------------------------------------------
# C21 — xls 미지원 · 손상 파일 400
# ---------------------------------------------------------------------------


async def test_preview_xls_is_unsupported_400(client, data_dir):
    (data_dir / "old.xls").write_bytes(b"\xd0\xcf\x11\xe0 legacy")
    r = await client.get("/api/v1/filesystem/preview", params={"path": "/old.xls"})
    assert r.status_code == 400 and "xls" in r.json()["detail"]


@pytest.mark.parametrize("name", ["bad.xlsx", "bad.docx", "bad.pptx"])
async def test_preview_corrupt_zip_is_400(client, data_dir, name):
    (data_dir / name).write_bytes(b"not a zip")
    r = await client.get("/api/v1/filesystem/preview", params={"path": f"/{name}"})
    assert r.status_code == 400, r.text
