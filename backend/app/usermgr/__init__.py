# SPDX-License-Identifier: Apache-2.0
"""사용자 관리(User management) 모듈.

사용자 계정과 역할 관리를 위한 CRUD 기능을 제공한다. 이 모듈은 표준
router/schemas/service 패턴을 따른다:

- router.py  : FastAPI API 엔드포인트(HTTP 핸들러).
- schemas.py : Pydantic 요청/응답 모델과 enum.
- service.py : 비즈니스 로직과 데이터베이스 처리.
- models.py  : SQLAlchemy ORM 모델 정의(ArgusUser, ArgusRole).

핵심 기능:
- 페이지네이션 목록 조회를 포함한 사용자 CRUD(생성·조회·수정·삭제).
- 역할 기반 접근 제어(Admin, Superuser, User 역할 — argus-admin / argus-superuser / argus-user).
- 계정 상태 관리(활성화/비활성화).
- 사용자명·이메일 중복 검증.
- PBKDF2-HMAC-SHA256(사용자별 salt) 비밀번호 해싱(``app.core.passwords``, 예전 SHA-256 해시도 검증).
- 사용자 삭제 전 소유 자산 이관(ownership.py).
"""
