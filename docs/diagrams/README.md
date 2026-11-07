# 매뉴얼 도표 제작과 출처

매뉴얼의 도표 60개를 Data Dynamics 홈페이지의 파랑·보라 패널, 선 아이콘,
흰색 카드와 흐름 화살표를 참고하여 새로 그렸습니다. 기존 도표의 노드, 관계,
조건, 방향, 응답, 상태 전이, 그룹 경계는 `diagrams.json`에 보존합니다.

## 편집과 재생성

`docs/`에서 의존성을 설치한 뒤 `npm run diagrams` 또는 `make diagrams`를 실행합니다.
Node.js 20 이상과 `Noto Sans CJK KR` 폰트가 필요합니다. Debian/Ubuntu에서는
`fonts-noto-cjk` 패키지가 해당 폰트를 제공합니다.

- `diagrams.json`: 각 도표의 ID, 매뉴얼 경로, 제목과 편집 가능한 의미 데이터.
- `../tools/render-diagrams.mjs`: Graphviz WASM을 이용한 배치와 자체 SVG 카드·아이콘·시퀀스 렌더링.
- `../modules/ROOT/assets/images/diagrams/`: 편집 가능한 SVG와 2.5배 해상도 PNG.
- HTML/PDF 본문은 PNG를 사용합니다. 일반 빌드에서는 도표를 재생성하지 않습니다.
- 순차 단계가 5개 이상인 단일 경로는 3열로 접어 배치합니다. 시퀀스는 위에서 아래로 읽습니다.
- ER 관계의 `1 → 0..N` 및 `1 → 0..1` 표기는 원본의 관계 수량을 뜻합니다.
- 도표 ID를 바꾸면 해당 AsciiDoc의 `image::diagrams/<id>.png[...]` 참조도 함께 수정합니다.
- 생성 결과는 글꼴과 도구 버전의 영향을 받습니다. 재생성 후 한글, 긴 기술 라벨,
  화살표 방향과 조건 분기를 확인하고 JSON·SVG·PNG를 함께 커밋합니다.

## 홈페이지 원본 이미지

플랫폼 개요 이미지는 2026-11-07에 아래 공개 URL에서 다운로드하여 원본 그대로 저장했습니다.
제공자: **Data Dynamics**. 매뉴얼 소개 페이지에 출처를 표시합니다.
이 출처 기록은 제3자 이미지에 새로운 라이선스를 부여하지 않습니다.

| 저장 파일 | 원본 | 용도 |
| --- | --- | --- |
| `architecture/platform-overview.png` | [플랫폼 개요](https://www.data-dynamics.io/solutions/argus-catalog-platform-ko.png) | 자산 등록·통합·검색·활용의 전체 흐름 |

기준 페이지: [Argus Catalog 솔루션 소개](https://www.data-dynamics.io/ko/solutions/argus-catalog).
홈페이지 이미지는 제품 전체 및 확장 구성을 포함하므로, 매뉴얼 본문에서 필수·선택 구성과
기본 동기화·Enterprise 수집 확장을 구분하여 안내합니다.

## 다시 그린 상세 아키텍처

`architecture/system-architecture.png`는 홈페이지의 구형 상세 아키텍처를 대체하는 이미지입니다.
현재 매뉴얼과 같은 파랑·보라 카드 및 선 아이콘 스타일로 built-in `image_gen`을 사용해 제작했습니다.
AI Agent·MCP·API 카탈로그, 선택 AI 서비스, Temporal·변경 관리 워커, Query Service를 반영합니다.
Metadata Sync·LDAP User Sync의 REST API 연결과 Query Service의 DB 연결을 구분하며,
Source Analyzer는 분석 결과 파일을 출력하는 것으로 표시합니다.

내용 기준: `pages/index.adoc`, `pages/install/production.adoc`, `pages/ai/ai-service.adoc`,
`pages/extensions/index.adoc`, `pages/extensions/source-analyzer.adoc`
(모두 `modules/ROOT/` 기준). 제작 프롬프트는 [system-architecture.prompt.txt](system-architecture.prompt.txt)에 보관합니다.
이 PNG는 AI로 별도 제작했으므로 `npm run diagrams`의 재생성 대상이 아닙니다.
