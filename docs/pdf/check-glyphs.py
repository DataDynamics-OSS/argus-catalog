"""PDF 글꼴에 없는 글자를 매뉴얼 소스에서 찾는다.

asciidoctor-pdf 는 글꼴에 없는 글자(이모지 등)를 빈 네모로 찍는다. ``make pdf`` 전에 실행해
본문 글꼴(Noto Sans KR)과 코드 글꼴(나눔고딕코딩) 어디에도 없는 글자를 파일·줄 단위로 알려 준다.

사용: python3 pdf/check-glyphs.py <fonts_dir> <adoc 루트...>
없는 글자가 있으면 종료 코드 1.
"""

import pathlib
import sys
import unicodedata

from fontTools.ttLib import TTFont

FONT_FILES = ("NotoSansKR-Regular.ttf", "NanumGothicCoding-Regular.ttf")
# 글자 모양이 없어도 찍히지 않는 보이지 않는 문자(이모지 변형 선택자·ZWJ)
IGNORED = {"️", "‍"}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    fonts_dir = pathlib.Path(sys.argv[1])
    covered: set[int] = set()
    for name in FONT_FILES:
        covered |= set(TTFont(fonts_dir / name).getBestCmap())

    problems = []
    for root in sys.argv[2:]:
        for path in sorted(pathlib.Path(root).rglob("*.adoc")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                bad = sorted({ch for ch in line if ord(ch) > 127 and ord(ch) not in covered and ch not in IGNORED})
                if bad:
                    problems.append((path, lineno, bad))

    for path, lineno, bad in problems:
        names = ", ".join(f"{ch} U+{ord(ch):04X} {unicodedata.name(ch, '?')}" for ch in bad)
        print(f"{path}:{lineno}: PDF 글꼴에 없는 글자 — {names}")
    if problems:
        print(f"\n{len(problems)} 줄에서 PDF 에 빈 네모로 찍힐 글자를 찾았습니다. "
              "✓ · × · ▼ · ▶ 처럼 글꼴에 있는 기호나 말('연필 아이콘')로 바꾸세요.")
        return 1
    print("check-glyphs: PDF 글꼴에 없는 글자 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
