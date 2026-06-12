# SPDX-License-Identifier: Apache-2.0
"""카탈로그 페더레이션(federation) — 다수의 Argus Catalog 인스턴스를 하나로 연합.

여러 팀이 각각 운영하는 Argus Catalog 를 peer 로 등록하고, 검색을 각 peer 로
fan-out(scatter-gather)해 한 화면에서 통합 검색한다. Trino 가 여러 카탈로그를
연합하듯, 메타데이터 카탈로그를 연합한다.

설계 모델(하이브리드):
- HARVEST  — peer 메타데이터를 주기적으로 pull → 로컬 미러 + 재임베딩 → 통합 검색
- LIVE     — 검색/조회를 요청 시점에 peer 로 실시간 프록시(scatter-gather)
- HYBRID   — HARVEST(검색) + LIVE(상세 drill-down)

구현 범위:
- Phase 0 — LIVE scatter-gather 검색 (요청 시 peer fan-out)
- Phase 1 — HARVEST 미러(주기 pull) + 허브 모델 재임베딩 + 로컬 미러 검색,
  export 서비스 토큰 인증
- Phase 2 — LIVE drill-down 프록시(상세·샘플) + export visibility 거버넌스
  (PII/민감도/데이터소스 allow-list)
- Phase 3 — 운영 강화: watermark 기반 증분 동기화 + LIVE peer circuit breaker
- Phase 4 — cross-instance 리니지 stitching (URN 매칭으로 로컬+미러 리니지 그래프)
- 관측성 — /federation/stats (미러 카운트·최근 동기화·breaker 상태 집계)
"""
