# tools

저장소 관리용 스크립트 모음입니다.

## `sync-version.py` — 버전 단일 소스 동기화

루트 [`VERSION`](../VERSION) 파일을 단일 소스(single source of truth)로 삼아, 모노레포
전반의 매니페스트(`pyproject.toml` · `__init__.py` · `package.json`)에 동일한 버전을
전파합니다. 버전 관리 규칙은 [VERSIONING.md](../VERSIONING.md) 를 참고하세요.

```bash
# VERSION → 모든 매니페스트로 동기화
python tools/sync-version.py

# 매니페스트가 VERSION 과 일치하는지 검증 (불일치 시 exit 1) — CI 게이트
python tools/sync-version.py --check

# 버전을 올리고 동기화
python tools/sync-version.py --set 0.2.0
```

- 의존성 없음(Python 표준 라이브러리만 사용).
- 존재하지 않는 파일은 건너뜁니다(컴포넌트가 일부만 있는 트리에서도 동작).
- CI 의 `version-sync` 잡이 `--check` 를 실행해 버전 드리프트를 차단합니다.
