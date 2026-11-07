NAME    := argus-catalog-manual
PDF_OUT := build/pdf/argus-catalog.pdf

# 한글 폰트 — 첫 빌드 전 1회 준비 (pdf/fonts/, gitignore).
# - Noto Sans KR        (본문 + heading) — google/fonts ofl/notosanskr 의 가변(variable) TTF 를 받아
#                        fontTools 로 정적 Regular(400)·Bold(700) TTF 를 만든다(prawn-pdf 는 가변 폰트 미지원).
# - Nanum Gothic Coding (코드 블록)       — google/fonts ofl/nanumgothiccoding (TTF)
# 모두 정적 TTF 라 prawn-pdf 의 OTF/CFF 임베딩 경고가 없다. fontTools 필요: pip install fonttools
FONTS_DIR := pdf/fonts
NOTO_VF_URL := https://raw.githubusercontent.com/google/fonts/main/ofl/notosanskr/NotoSansKR%5Bwght%5D.ttf
CODING_BASE := https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothiccoding

.PHONY: help install fonts build pdf diagrams preview clean

help:
	@echo "Argus Catalog 매뉴얼 빌드"
	@echo "  make install   의존성 설치 (npm + asciidoctor-pdf gem + 한글 폰트)"
	@echo "  make fonts     한글 폰트만 다운로드 ($(FONTS_DIR)/)"
	@echo "  make build     HTML 사이트 → build/site/"
	@echo "  make check-glyphs  PDF 글꼴에 없는 글자(이모지 등) 검사"
	@echo "  make pdf       PDF        → $(PDF_OUT)"
	@echo "  make diagrams  SVG/PNG 도표 재생성"
	@echo "  make preview   로컬 서버 + 브라우저 (포트 8888)"
	@echo "  make clean     빌드 산출물 삭제"

install:
	npm install
	gem install asciidoctor-pdf rouge
	pip install fonttools
	$(MAKE) fonts

fonts:
	@mkdir -p $(FONTS_DIR)
	@cd $(FONTS_DIR) && \
	  if [ ! -f NotoSansKR-Regular.ttf ] || [ ! -f NotoSansKR-Bold.ttf ]; then \
	    python3 -c "import fontTools" 2>/dev/null || { echo "fontTools 가 필요합니다: pip install fonttools"; exit 1; }; \
	    [ -f NotoSansKR-VF.ttf ] || curl -fsSL -o NotoSansKR-VF.ttf "$(NOTO_VF_URL)"; \
	    python3 -m fontTools.varLib.instancer -q --static --update-name-table -o NotoSansKR-Regular.ttf NotoSansKR-VF.ttf wght=400 && \
	    python3 -m fontTools.varLib.instancer -q --static --update-name-table -o NotoSansKR-Bold.ttf NotoSansKR-VF.ttf wght=700 && \
	    rm -f NotoSansKR-VF.ttf; \
	  fi && \
	  for f in NanumGothicCoding-Regular.ttf NanumGothicCoding-Bold.ttf; do \
	    [ -f $$f ] || curl -fsSL -o $$f "$(CODING_BASE)/$$f"; \
	  done && \
	  echo "fonts ready: $(FONTS_DIR)/" && ls -1 *.ttf

build:
	npx antora antora-playbook.yml

# PDF 글꼴(Noto Sans KR·나눔고딕코딩)에 없는 글자는 빈 네모로 찍히므로 빌드 전에 막는다.
check-glyphs:
	python3 pdf/check-glyphs.py $(FONTS_DIR) modules pdf

pdf: check-glyphs
	@mkdir -p build/pdf
	asciidoctor-pdf \
	  -a pdf-themesdir=pdf \
	  -a pdf-theme=argus-catalog \
	  -a pdf-fontsdir="pdf/fonts;GEM_FONTS_DIR" \
	  pdf/argus-catalog-book.adoc \
	  -o $(PDF_OUT)

diagrams:
	npm run diagrams

preview:
	npm run preview

clean:
	rm -rf build .antora-cache
