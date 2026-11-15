# 품질 진단 리포트 PDF 글꼴

`NotoSansKR-Regular.ttf` · `NotoSansKR-Bold.ttf` — Noto Sans KR (© 2014-2021 Adobe, Reserved Font Name 'Source').
[SIL Open Font License 1.1](https://openfontlicense.org) 로 배포된다.

웹 UI 가 쓰는 `frontend/apps/web/app/fonts/NotoSansKR-400.woff2` · `-700.woff2` 를 fontTools 로 압축만 풀어 TTF 로 바꾼 것이다
(글리프는 그대로 — 한글 11,172자 · 라틴 · 기호, 한자는 없음).

```bash
python3 -c "
from fontTools.ttLib import TTFont
for w, n in (('400', 'Regular'), ('700', 'Bold')):
    f = TTFont(f'frontend/apps/web/app/fonts/NotoSansKR-{w}.woff2'); f.flavor = None
    f.save(f'backend/app/quality/fonts/NotoSansKR-{n}.ttf')"
```
