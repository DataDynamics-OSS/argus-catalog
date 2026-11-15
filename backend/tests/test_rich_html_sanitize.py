# SPDX-License-Identifier: Apache-2.0
"""리치 텍스트 HTML 저장 전 정화 (TODO-027).

TipTap 편집기 출력은 그대로 남고(인라인 style 공백 정리 정도), 스크립트·이벤트 속성·위험 URL 은 지워진다.
태그 없는 일반 텍스트는 바뀌지 않는다.

실행:
    cd backend
    PYTHONPATH=$(pwd) python -m pytest tests/test_rich_html_sanitize.py
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.catalog.schemas import DatasetCreate, DatasetUpdate
from app.change_mgmt.schemas import ChangeRequestCreate
from app.comments.schemas import CommentCreate, CommentUpdate
from app.core.html_sanitize import sanitize_rich_html as clean

# TipTap 확장별 직렬화 예 — 정화 뒤에도 같아야 한다
TIPTAP_KEEP = [
    '<ul data-type="taskList"><li data-checked="true" data-type="taskItem"><label>'
    '<input type="checkbox" checked="checked"><span></span></label><div><p>할 일</p></div></li></ul>',
    '<pre class="hljs"><code class="language-sql">select a &lt; b\nfrom t</code></pre>',
    '<table class="tiptap-table" style="min-width:75px"><colgroup><col style="width:120px"></colgroup><tbody>'
    '<tr><th colspan="1" rowspan="1"><p>h</p></th></tr>'
    '<tr><td colspan="1" rowspan="1" colwidth="120"><p>c</p></td></tr></tbody></table>',
    '<img src="data:image/png;base64,iVBOR" class="max-w-full rounded" style="width:30%;border:1px solid #CCC">',
    '<p><a target="_blank" class="text-primary underline" href="https://x.io/a?b=1" rel="noopener noreferrer">x</a></p>',
    '<p style="text-align:center;padding-left:2em" data-indent="1"><span style="color:#ff0000">red</span> '
    '<strong>b</strong><em>i</em><u>u</u><s>s</s><code>c</code></p><h2>H</h2>'
    '<blockquote><p>q</p></blockquote><hr><p>a<br>b</p><ol start="3"><li><p>n</p></li></ol>',
    '<p><a href="/dashboard/datasets/1" rel="noopener noreferrer">상대 경로</a> '
    '<a href="mailto:a@b.c" rel="noopener noreferrer">메일</a></p>',
]


@pytest.mark.parametrize("html", TIPTAP_KEEP)
def test_tiptap_output_is_kept(html):
    assert clean(html) == html


@pytest.mark.parametrize("html", TIPTAP_KEEP)
def test_idempotent(html):
    assert clean(clean(html)) == clean(html)


@pytest.mark.parametrize("dirty, must_not_contain", [
    ('<img src="x" onerror="alert(1)">', "onerror"),
    ('<a href="javascript:alert(1)">l</a>', "javascript:"),
    ('<a href=" JaVaScRiPt:alert(1)">l</a>', "javascript"),
    ('<a href="data:text/html;base64,PHN">l</a>', "data:"),
    ('<img src="data:text/html;base64,PHN">', "data:text"),
    ('<p>x</p><script>alert(1)</script>', "alert"),
    ('<style>body{display:none}</style><p>x</p>', "display"),
    ('<svg onload="alert(1)"><circle/></svg>', "svg"),
    ('<iframe src="https://evil"></iframe>', "iframe"),
    ('<p style="position:fixed;top:0;left:0">x</p>', "position"),
    ('<input type="text" value="phish">', "text"),
    ('<form action="https://evil"><button>x</button></form>', "form"),
])
def test_dangerous_markup_is_removed(dirty, must_not_contain):
    assert must_not_contain.lower() not in clean(dirty).lower()


@pytest.mark.parametrize("plain", [None, "", "a < b & c", "평범한 설명", "**마크다운** `code`", "x<3"])
def test_plain_text_unchanged(plain):
    assert clean(plain) == plain


def test_request_schemas_sanitize_before_save():
    dirty = '<p>설명</p><img src="x" onerror="alert(1)">'
    assert "onerror" not in DatasetUpdate(description=dirty).description
    assert "onerror" not in DatasetCreate(name="t", datasource_id=1, description=dirty).description
    assert "onerror" not in ChangeRequestCreate(
        title="t", dataset_id=1, change_type="COSMETIC", description=dirty,
        rollback_plan="되돌림", business_justification="사유").description
    assert "onerror" not in CommentCreate(entity_type="dataset", entity_id="1", content=dirty).content
    assert "onerror" not in CommentUpdate(content=dirty).content
    assert DatasetUpdate(description=None).description is None


def test_comment_that_is_only_script_is_rejected():
    with pytest.raises(ValidationError):
        CommentCreate(entity_type="dataset", entity_id="1", content="<script>alert(1)</script>")
    # 이미지 하나만 있는 댓글은 허용
    assert CommentUpdate(content='<p><img src="https://x/a.png"></p>').content
