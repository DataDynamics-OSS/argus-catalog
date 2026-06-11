NAME    := argus-catalog-manual
PDF_OUT := build/pdf/argus-catalog.pdf

# 한글 폰트 — 첫 빌드 전 1회 다운로드 (pdf/fonts/, gitignore).
# - Nanum Gothic        (본문 + heading) — google/fonts ofl/nanumgothic (TTF)
# - Nanum Gothic Coding (코드 블록)       — google/fonts ofl/nanumgothiccoding (TTF)
# 두 패밀리 모두 정적 TTF 라 prawn-pdf 의 OTF/CFF 임베딩 경고가 없다.
FONTS_DIR := pdf/fonts
GOTHIC_BASE := https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic
CODING_BASE := https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothiccoding

.PHONY: help install fonts build pdf preview clean

help:
	@echo "Argus Catalog 매뉴얼 빌드"
	@echo "  make install   의존성 설치 (npm + asciidoctor-pdf gem + 한글 폰트)"
	@echo "  make fonts     한글 폰트만 다운로드 ($(FONTS_DIR)/)"
	@echo "  make build     HTML 사이트 → build/site/"
	@echo "  make pdf       PDF        → $(PDF_OUT)"
	@echo "  make preview   로컬 서버 + 브라우저 (포트 8888)"
	@echo "  make clean     빌드 산출물 삭제"

install: fonts
	npm install
	gem install asciidoctor-pdf rouge

fonts:
	@mkdir -p $(FONTS_DIR)
	@cd $(FONTS_DIR) && \
	  for f in NanumGothic-Regular.ttf NanumGothic-Bold.ttf; do \
	    [ -f $$f ] || curl -fsSL -o $$f "$(GOTHIC_BASE)/$$f"; \
	  done && \
	  for f in NanumGothicCoding-Regular.ttf NanumGothicCoding-Bold.ttf; do \
	    [ -f $$f ] || curl -fsSL -o $$f "$(CODING_BASE)/$$f"; \
	  done && \
	  echo "fonts ready: $(FONTS_DIR)/" && ls -1 *.ttf

build:
	npx antora antora-playbook.yml

pdf:
	@mkdir -p build/pdf
	asciidoctor-pdf \
	  -a pdf-themesdir=pdf \
	  -a pdf-theme=argus-catalog \
	  -a pdf-fontsdir="pdf/fonts;GEM_FONTS_DIR" \
	  pdf/argus-catalog-book.adoc \
	  -o $(PDF_OUT)

preview:
	npm run preview

clean:
	rm -rf build .antora-cache
