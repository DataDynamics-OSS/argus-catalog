"""변경 관리(Change Management) 모듈.

스키마/메타데이터 변경 요청(CR)을 결재 워크플로우로 처리하고
다운스트림 소비자에게 사전 통지·ACK 추적을 수행하는 모듈.

구성:
- models      : ORM (ChangeRequest, ApprovalStep, Consumer, NotificationLog)
- schemas     : Pydantic DTO
- router      : FastAPI 엔드포인트 (/api/v1/changes/*)
- service     : DB CRUD 및 비즈니스 로직
- workflow    : Temporal 워크플로우 정의 (장기 실행 결재 흐름)
- activities  : Temporal 액티비티 (DB 호출, 통지 전송 등)
- temporal_client : Temporal 클라이언트 싱글톤
- worker      : Temporal 워커 진입점 (별도 프로세스로 기동)
"""
