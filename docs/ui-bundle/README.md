# Antora UI 번들

`ui-bundle.zip` 은 [Antora 기본 UI](https://gitlab.com/antora/antora-ui-default)의 `bundle-stable` 빌드입니다.
저장소에 고정해 두어 인터넷이 없는 환경(에어갭)에서도 매뉴얼을 빌드할 수 있고, 빌드할 때마다 모양이 바뀌지 않습니다.
화면 모양 조정은 이 파일을 고치지 말고 `../ui-supplemental/` 에서 덮어씁니다.

갱신할 때

```bash
curl -fsSL -o ui-bundle.zip \
  'https://gitlab.com/antora/antora-ui-default/-/jobs/artifacts/HEAD/raw/build/ui-bundle.zip?job=bundle-stable'
```

갱신한 뒤 `make build` 로 `ui-supplemental` 의 덮어쓴 partial(`header-content.hbs`, `footer-scripts.hbs` 등)이
새 번들과 맞는지 확인합니다.
