# SPDX-License-Identifier: Apache-2.0
"""문서 미리보기 HTML 용 허용 목록(allow-list) 기반 sanitizer.

DOCX 미리보기는 mammoth 가 만든 HTML 을 프런트가 ``dangerouslySetInnerHTML`` 로 그대로
렌더링한다. 업로드된 문서에 ``javascript:`` 링크 등이 들어 있으면 저장형 XSS 가 되므로,
서버에서 표준 라이브러리 ``html.parser`` 로 다시 파싱해 허용된 태그·속성만 남긴다
(새 의존성 없이 동작하도록 nh3/bleach 를 쓰지 않는다).

규칙:
- 허용 태그 외의 태그는 버리고 안쪽 텍스트만 남긴다.
- ``script``/``style``/``iframe`` 등 위험 태그는 안쪽 내용까지 통째로 버린다.
- 속성은 태그별 허용 목록만 남긴다(``on*`` 이벤트 핸들러·``style`` 등은 모두 제거).
- ``a[href]`` 는 ``http``/``https``/``mailto`` 와 ``#`` 앵커만, ``img[src]`` 는 래스터
  이미지 ``data:`` URI(mammoth 내장 이미지)와 ``http``/``https`` 만 허용한다.
- 텍스트·속성 값은 모두 다시 이스케이프하고, 열린 태그는 끝에서 닫아 구조를 맞춘다.
"""

import re
from html import escape
from html.parser import HTMLParser

#: 허용 태그 → 허용 속성
_ALLOWED_TAGS: dict[str, frozenset[str]] = {
    **{t: frozenset() for t in (
        "p", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i", "u", "s",
        "sub", "sup", "ul", "ol", "table", "thead", "tbody", "tfoot", "tr", "blockquote",
        "pre", "code", "br", "hr", "span", "div", "caption",
    )},
    "a": frozenset({"href", "id", "title"}),
    "li": frozenset({"id"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
    "img": frozenset({"src", "alt", "title", "width", "height"}),
}

#: 내용까지 통째로 버리는 태그
_DROP_CONTENT_TAGS = frozenset({
    "script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math",
    "frame", "frameset", "applet", "textarea", "select", "title", "head",
})

#: 끝 태그가 없는 void 요소
_VOID_TAGS = frozenset({"br", "hr", "img"})

_SAFE_HREF_SCHEMES = ("http:", "https:", "mailto:")
_SAFE_IMG_DATA_RE = re.compile(r"^data:image/(png|jpe?g|gif|bmp|webp);base64,[a-z0-9+/=\s]*$", re.I)
_NUMERIC_ATTRS = frozenset({"colspan", "rowspan", "width", "height"})
# 브라우저는 URL 스킴 안의 공백·제어 문자를 무시하므로(``java\tscript:``) 검사 전에 제거한다
_URL_STRIP_RE = re.compile(r"[\x00-\x20\x7f]+")


def _safe_url(tag: str, attr: str, value: str) -> str | None:
    """허용되는 URL 이면 원래 값(앞뒤 공백 제거)을, 아니면 None 을 돌려준다."""
    compact = _URL_STRIP_RE.sub("", value).lower()
    if tag == "a" and attr == "href":
        if compact.startswith("#") or compact.startswith(_SAFE_HREF_SCHEMES):
            return value.strip()
        return None
    if tag == "img" and attr == "src":
        if compact.startswith(("http:", "https:")) or _SAFE_IMG_DATA_RE.match(value.strip()):
            return value.strip()
        return None
    return None


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []      # 출력한(열린) 허용 태그
        self.drop_depth = 0             # 위험 태그 내부 깊이(0 이면 정상 출력)

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = _ALLOWED_TAGS[tag]
        parts: list[str] = []
        for name, value in attrs:
            name = name.lower()
            if name not in allowed or value is None:
                continue
            if name in ("href", "src"):
                value = _safe_url(tag, name, value)
                if value is None:
                    continue
            elif name in _NUMERIC_ATTRS and not value.strip().isdigit():
                continue
            parts.append(f' {name}="{escape(value, quote=True)}"')
        if tag == "a" and any(p.startswith(" href=") and not p.startswith(' href="#') for p in parts):
            # 외부 링크(문서 내 # 앵커 제외)가 미리보기 창(opener)에 접근하지 못하도록
            parts.append(' rel="noopener noreferrer" target="_blank"')
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            self.drop_depth += 1
            return
        if self.drop_depth or tag not in _ALLOWED_TAGS:
            return
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS or self.drop_depth or tag not in _ALLOWED_TAGS:
            return
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        if tag not in _VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if self.drop_depth or tag not in self.stack:
            return
        while self.stack:
            open_tag = self.stack.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag == tag:
                break

    def handle_data(self, data):
        if not self.drop_depth:
            self.out.append(escape(data, quote=False))

    # 주석·선언·처리 지시문은 모두 버린다(조건부 주석 등 우회 차단)
    def handle_comment(self, data):
        return

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return

    def result(self) -> str:
        self.close()
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(html: str) -> str:
    """신뢰할 수 없는 HTML 을 허용 목록 기준으로 정리해 돌려준다."""
    parser = _Sanitizer()
    parser.feed(html or "")
    return parser.result()
