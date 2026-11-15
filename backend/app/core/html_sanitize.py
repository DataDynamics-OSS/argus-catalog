# SPDX-License-Identifier: Apache-2.0
"""리치 텍스트(TipTap 편집기 출력) HTML 저장 전 정화 (TODO-027).

댓글 본문·데이터셋 상세 설명·변경 요청 설명은 웹 편집기가 만든 HTML 로 저장된다. 웹 뷰어는 그리기 전에
DOMPurify 로 정화하지만(DEF-021), 메일·내보내기·다른 클라이언트가 저장값을 그대로 쓰면 다시 위험해지므로
서버도 저장 전에 허용 목록으로 정화한다.

허용 범위는 ``frontend/apps/web/components/comments/comment-editor.tsx`` 의 TipTap 확장 출력 기준이다.
- 태그: 문단·제목·목록·인용·코드 블록·표·체크리스트(label/input)·이미지·링크·글자 서식
- 속성: ``class``, ``style``(아래 CSS 속성만), ``data-*``(체크리스트·들여쓰기·코드 언어), 표 colspan/rowspan/colwidth,
  링크 href/target, 이미지 src/alt/width/height, 체크박스 type/checked
- URL: http·https·mailto(링크), http·https·``data:image/*``(이미지). ``javascript:`` 등은 지운다.
- ``<script>``·``<style>`` 등은 내용째 지우고, ``on*`` 이벤트 속성은 허용 목록에 없어 지워진다.

태그가 없는 값(일반 텍스트·마크다운, 예: 수집한 테이블 코멘트 ``a < b``)은 그대로 돌려준다 — 정화기는
``<``·``&`` 를 엔티티로 바꾸므로, 태그가 없는데도 돌리면 일반 텍스트가 바뀐다.
"""

from __future__ import annotations

import html
import re
from typing import Annotated

import nh3
from pydantic import AfterValidator

_TAGS = {
    "p", "br", "hr", "div", "span", "blockquote", "pre", "code",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "s", "strike", "del", "mark", "sub", "sup",
    "ul", "ol", "li", "label", "input",
    "a", "img",
    "table", "colgroup", "col", "thead", "tbody", "tfoot", "tr", "th", "td",
}

_ATTRIBUTES = {
    "*": {"class", "style"},
    "a": {"href", "target", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "ol": {"start", "type"},
    "th": {"colspan", "rowspan", "colwidth"},
    "td": {"colspan", "rowspan", "colwidth"},
    "col": {"span"},
    "input": {"type", "checked", "disabled"},
}

# TipTap 출력이 쓰는 인라인 스타일 — 글자색·정렬·들여쓰기·이미지 크기/테두리·표 열 너비.
_STYLE_PROPERTIES = {
    "color", "background-color", "text-align", "padding-left",
    "width", "min-width", "max-width", "height",
    "border", "border-width", "border-style", "border-color",
    "white-space",
}

_IMG_DATA_URL = re.compile(r"^data:image/(png|jpe?g|gif|webp|bmp|avif);", re.IGNORECASE)
_LINK_SCHEMES = ("http:", "https:", "mailto:")
_TAG_LIKE = re.compile(r"<[A-Za-z!/?]")


def _attribute_filter(tag: str, attr: str, value: str) -> str | None:
    """값 단위 검사 — 링크는 http·https·mailto·상대 경로만, 이미지 data: 는 이미지 형식만, input 은 체크박스만."""
    v = value.strip()
    if tag == "a" and attr == "href":
        low = v.lower()
        if ":" in low.split("/", 1)[0] and not low.startswith(_LINK_SCHEMES):
            return None
        return v
    if tag == "img" and attr == "src":
        if v.lower().startswith("data:") and not _IMG_DATA_URL.match(v):
            return None
        return v
    if tag == "input" and attr == "type":
        return v if v.lower() == "checkbox" else None
    return value


_cleaner = nh3.Cleaner(
    tags=_TAGS,
    clean_content_tags={"script", "style", "iframe", "object", "embed", "noscript", "template"},
    attributes=_ATTRIBUTES,
    generic_attribute_prefixes={"data-"},
    attribute_filter=_attribute_filter,
    url_schemes={"http", "https", "mailto", "data"},
    filter_style_properties=_STYLE_PROPERTIES,
    link_rel="noopener noreferrer",
)


def sanitize_rich_html(value: str | None) -> str | None:
    """리치 텍스트 HTML 정화. None·빈 값·태그 없는 텍스트는 그대로 돌려준다."""
    if not value or not _TAG_LIKE.search(value):
        return value
    return _cleaner.clean(value)


_BLOCK_END = re.compile(r"<br\s*/?>|</(?:p|div|li|h[1-6]|blockquote|pre|tr|table)\s*>", re.IGNORECASE)


def html_to_plain(value: str | None) -> str:
    """리치 텍스트 HTML 의 평문 — 검색 · 목록 · 알림 요약용(``content_plain`` 을 보내지 않은 API 호출, DEF-133).
    문단 · 줄바꿈 · 목록 끝은 줄을 바꾸고, 태그를 지운 뒤 엔티티를 되돌린다. 태그 없는 값은 앞뒤 공백만 정리한다."""
    if not value:
        return ""
    if _TAG_LIKE.search(value):
        value = html.unescape(nh3.clean(_BLOCK_END.sub("\n", value), tags=set()))
    lines = (re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def _sanitize_required(value: str) -> str:
    """정화 결과가 비면(예: ``<script>`` 만 보냄) 거부한다 — 댓글 본문처럼 필수인 칸."""
    cleaned = sanitize_rich_html(value) or ""
    if not re.sub(r"<[^>]*>|&nbsp;|\s", "", cleaned) and "<img" not in cleaned:
        raise ValueError("내용이 비어 있습니다(허용되지 않는 HTML 은 저장 전에 지워집니다).")
    return cleaned


# 요청 스키마용 타입 — 저장 전에 정화한다.
RichHtml = Annotated[str, AfterValidator(sanitize_rich_html)]
RequiredRichHtml = Annotated[str, AfterValidator(_sanitize_required)]
