# Argus Catalog — 사용자 매뉴얼

이 디렉터리는 Argus Catalog 사용자 매뉴얼을 만들어 내는 곳입니다.
[Antora](https://antora.org/) 기반의 정적 매뉴얼 사이트와 PDF 를 한 벌의 소스에서 함께 생성합니다.
원본은 AsciiDoc (`modules/ROOT/pages/**/*.adoc`) 한 가지이고, 이걸 빌드하면
`build/site` 에는 HTML 사이트가, `build/pdf/argus-catalog.pdf` 에는 책자형 PDF 가 떨어집니다.

## 빠른 시작

```bash
cd docs

# 한 번만 — 의존성 설치 (Antora CLI npm + asciidoctor-pdf gem + fonttools + 한글 폰트)
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

## 도표 이미지

본문의 도표 60개는 홈페이지 스타일의 선 아이콘·파랑/보라 카드·화살표로 구성한
정적 이미지입니다. HTML과 PDF 모두 저장소에 포함된 PNG를 사용합니다.
HTML에서는 도표를 클릭하면 새 탭에서 원본 크기로 볼 수 있습니다.
도표를 표시하거나 일반 매뉴얼을 빌드할 때 외부 도표 서버 호출은 없습니다.
Antora UI 번들 다운로드와 최초 패키지·PDF 폰트 설치는 여전히 별도로 필요합니다.

- 편집 데이터: `diagrams/diagrams.json` (노드·관계·조건·시퀀스·그룹)
- 렌더러: `tools/render-diagrams.mjs`
- 결과: `modules/ROOT/assets/images/diagrams/<id>.svg` 및 `<id>.png`
- 공식 이미지와 출처: [diagrams/README.md](diagrams/README.md)

도표의 라벨이나 관계를 수정할 때는 JSON을 편집한 다음 재생성합니다.
일반 HTML/PDF 빌드에서는 이 단계를 실행할 필요가 없습니다.

```bash
# npm install 후, Noto Sans CJK KR 폰트가 설치된 환경에서 실행
# Debian/Ubuntu의 폰트 패키지: fonts-noto-cjk
make diagrams
make build
make pdf
```

PNG는 SVG 기준 2.5배 해상도로 출력하여 한글과 기술 라벨의 가독성을 확보합니다.
SVG에는 글꼴을 내장하지 않으므로 직접 열 때도 한글 글꼴이 필요합니다.
재생성 시 JSON, SVG, PNG를 함께 커밋하세요.

### Git worktree에서 HTML 빌드

Antora의 로컬 소스 URL은 `.git` 디렉터리가 있는 기본 저장소를 가리켜야 합니다.
연결된 worktree에서 빌드할 때는 별도 playbook에 아래처럼 소스를 지정합니다.
`ui.supplemental_files`와 `output.dir`도 해당 worktree의 `docs` 경로에 맞춥니다.

```yaml
content:
  sources:
    - url: /path/to/primary-repository
      branches: docs/manual-visual-diagrams
      worktrees: true
      start_path: docs
      edit_url: false
```

기본 저장소에서 브랜치를 체크아웃한 경우에는 기존 `make build`를 그대로 사용합니다.

## 디렉토리

```
docs/
├── antora.yml                  # 컴포넌트 설명자 (이름·버전·시작 페이지)
├── antora-playbook.yml         # 사이트 빌더 설정 (UI 번들·출력 경로)
├── package.json                # Antora CLI 의존성
├── Makefile                    # build / pdf / preview / clean 타겟
├── diagrams/                  # 도표 편집 데이터와 공식 이미지 출처
├── tools/                     # SVG/PNG 재생성 도구
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
│           └── images/         # 스크린샷·도표·공식 아키텍처 이미지
├── pdf/
│   ├── argus-catalog-book.adoc # PDF 마스터 — pages 를 include 로 묶음
│   ├── argus-catalog-theme.yml # asciidoctor-pdf 테마 (한글 폰트 매핑)
│   └── fonts/                  # `make fonts` 가 Noto Sans KR(본문)·나눔고딕코딩(코드) 준비 (gitignored)
└── ui-supplemental/
    └── css/argus-catalog.css   # Antora 기본 UI 위 추가 스타일
```

## 페이지 추가

매뉴얼에 새 문서를 한 편 보태는 과정은 다음 네 단계면 충분합니다.

1. `modules/ROOT/pages/<category>/<slug>.adoc` 생성 — AsciiDoc 본문 작성.
2. `modules/ROOT/nav.adoc` 에 한 줄 추가 (`** xref:<category>/<slug>.adoc[제목]`).
3. PDF 에 포함하려면 `pdf/argus-catalog-book.adoc` 의 `include::` 에 등록.
4. `make build` / `make pdf` 로 산출물 갱신.

## 스크린샷 추가

이미지는 따로 등록할 필요 없이 경로 규칙만 맞추면 됩니다.
페이지의 `image::<slug>/<file>.png[...]` 라인 경로에 맞춰
`modules/ROOT/assets/images/<slug>/<file>.png` 위치에 PNG 파일을 떨어뜨리면
빌드 시 자동으로 반영됩니다. 기존 스크린샷이 없는 자리에는 `.gitkeep` 또는 TODO 주석이 남아 있을 수 있습니다.

어디에 스크린샷이 필요한지는 각 페이지의 `// TODO: screenshot - ...` 주석이 알려 줍니다.
