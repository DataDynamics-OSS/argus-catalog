# Argus Catalog — 사용자 매뉴얼

[Antora](https://antora.org/) 기반의 정적 매뉴얼 사이트 + PDF 생성 파이프라인.
원본 AsciiDoc (`modules/ROOT/pages/**/*.adoc`) 을 빌드해 `build/site` 에 HTML
사이트를, `build/pdf/argus-catalog.pdf` 에 책자형 PDF 를 떨어뜨린다.

## 빠른 시작

```bash
cd docs

# 한 번만 — 의존성 설치 (Antora CLI npm + asciidoctor-pdf gem + 나눔고딕 폰트)
make install

# HTML 사이트 빌드
make build              # → build/site/index.html

# PDF 생성
make pdf                # → build/pdf/argus-catalog.pdf

# 로컬 미리보기 서버
make preview            # http://localhost:8888

# 빌드 산출물 정리
make clean
```

## 디렉토리

```
docs/
├── antora.yml                  # 컴포넌트 설명자 (이름·버전·시작 페이지)
├── antora-playbook.yml         # 사이트 빌더 설정 (UI 번들·출력 경로)
├── package.json                # Antora CLI 의존성
├── Makefile                    # build / pdf / preview / clean 타겟
├── modules/
│   └── ROOT/
│       ├── nav.adoc            # 좌측 사이드바 네비게이션
│       ├── pages/              # AsciiDoc 본문
│       │   ├── index.adoc
│       │   ├── getting-started.adoc
│       │   ├── catalog/        # 데이터 카탈로그 메뉴별 페이지
│       │   ├── models/         # ML 모델 메뉴별 페이지
│       │   ├── ops/            # 운영 메뉴별 페이지
│       │   ├── install/        # 설치 / 운영 가이드
│       │   └── appendix/       # SDK / API / Extension 부록
│       └── assets/
│           └── images/         # 스크린샷 (page-slug 별 서브폴더)
├── pdf/
│   ├── argus-catalog-book.adoc # PDF 마스터 — pages 를 include 로 묶음
│   ├── argus-catalog-theme.yml # asciidoctor-pdf 테마 (한글 폰트 매핑)
│   └── fonts/                  # `make fonts` 가 NanumGothic 다운로드 (gitignored)
└── ui-supplemental/
    └── css/argus-catalog.css   # Antora 기본 UI 위 추가 스타일
```

## 페이지 추가

1. `modules/ROOT/pages/<category>/<slug>.adoc` 생성 — AsciiDoc 본문 작성.
2. `modules/ROOT/nav.adoc` 에 한 줄 추가 (`** xref:<category>/<slug>.adoc[제목]`).
3. PDF 에 포함하려면 `pdf/argus-catalog-book.adoc` 의 `include::` 에 등록.
4. `make build` / `make pdf` 로 산출물 갱신.

## 스크린샷 추가

페이지의 `image::<slug>/<file>.png[...]` 라인 경로에 맞춰
`modules/ROOT/assets/images/<slug>/<file>.png` 위치에 PNG 파일을 떨어뜨리면
빌드 시 자동 적용. 디렉토리는 미리 생성되어 있고 `.gitkeep` 만 포함된 상태.

스크린샷 필요 위치는 각 페이지의 `// TODO: screenshot - ...` 주석으로 표시.
```
