# Argus Catalog Agent

Argus Catalog 의 AI 메타데이터 생성 기능을 **독립 에이전트**로 실행합니다.
카탈로그 API 에서 컨텍스트(스키마·샘플·용어집)를 읽고, 로컬 LLM(기본
ollama + **qwen2.5:7b**)으로 메타데이터를 생성해 카탈로그로 되돌립니다.

## 아키텍처

하나의 패키지가 **세 가지 실행 모드**를 제공합니다 — 배치(one-shot),
폴링 데몬(worker), 어시스턴트 서버(serve).

```
┌──────────────────────────────────────────────────────────────────┐
│  argus-agent  (Docker 또는 native — 표준 라이브러리만 사용)           │
│                                                                   │
│  [배치]  describe/.../generate-all   [데몬]  worker (주기 폴링)      │
│     │  ① 컨텍스트 읽기 (스키마·샘플·용어집)                            │
│     ├──── REST (admin 계정) ────▶ Argus Catalog API                │
│     │  ③ 결과 반입 (제안 → UI 에서 사람 승인)                          │
│     │  ② 생성                                                      │
│     └──── OpenAI 호환 API ────▶ ollama (qwen2.5:7b) / vLLM / ...   │
│                                                                   │
│  [서버]  serve (:8930) — AI 어시스턴트 tool-use 채팅                  │
│     ▲ SSE (사용자 토큰 위임)                                         │
│     │                        ┌─ 도구 실행 (사용자 권한) ─▶ 카탈로그 API │
│  백엔드 /ai/assistant/chat    └─ tool-use 루프 ─▶ LLM               │
│  (설정 > AI 어시스턴트 탭에서 활성화+URL 시 프록시)                    │
└──────────────────────────────────────────────────────────────────┘
```

모듈 구성:

| 모듈 | 역할 |
|---|---|
| `config.py` | 설정 (CLI 인자 > 환경변수 > 기본값) |
| `catalog.py` | 카탈로그 REST 클라이언트 (배치용 — admin 계정) |
| `llm.py` | OpenAI 호환 LLM — `generate`(단발)·`chat`(tool-calling) |
| `prompts.py` | 메타데이터 생성용 한국어 프롬프트 |
| `tasks.py` | 배치 작업 5종 (describe/summarize/columns/tags/pii) |
| `tools.py` | 어시스턴트 도구 레지스트리 (스키마 + 실행 함수) |
| `assistant.py` | tool-use 루프 (LLM ↔ 도구 반복, SSE 이벤트 생성) |
| `server.py` | serve 모드 HTTP 서버 (`/chat` SSE, `/health`) |
| `telemetry.py` | 셀프-텔레메트리 — 레지스트리 자기등록 + 호출 지표 push |

## 왜 에이전트로 분리하나

| 관점 | 효과 |
|---|---|
| 자원 분리 | LLM(GPU)을 API 서버와 다른 머신에서 — 서버는 가볍게 유지 |
| 데이터 주권 | 로컬 모델 사용 시 스키마·샘플이 외부로 나가지 않음 (PII 감지에 중요) |
| 독립 스케일 | 대량 일괄 생성 시 에이전트만 확장 |
| 운영 일관성 | `quality/` 배치와 동일한 외장 패턴 (API 읽기 → 처리 → 반입) |

## 기능

| 명령 | 설명 | 결과 처리 |
|---|---|---|
| `describe` | 데이터셋 상세 설명 (markdown) | 제안 또는 직접 적용 |
| `summarize` | 한 줄 요약 | 〃 |
| `columns` | 컬럼별 설명 일괄 | 〃 |
| `tags` | 분류 태그 추천 | 항상 제안 |
| `pii` | PII 컬럼 감지 + 근거 | **항상 제안** (민감 — 사람 승인 강제) |
| `generate-all` | 위 전부 | 〃 |
| `worker` | 폴링 데몬 — 설명 없는 데이터셋 자동 생성 | 〃 |

**결과 처리 모드** (`--mode`):
- `suggest`(기본): 카탈로그의 **AI 제안 워크플로에 반입** → UI 에서 사람이
  승인/거절. 거버넌스가 유지되는 권장 모드.
- `apply`: 데이터셋에 직접 적용 — 신뢰 환경 전용. PII 는 이 모드에서도
  항상 제안으로만 남습니다.

## 실행 — Native

에이전트는 **표준 라이브러리만 사용**하므로 별도 의존성 설치가 없습니다.

```bash
cd agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # 표준 라이브러리만 사용 — CLI 등록용

# 단일 데이터셋 설명 생성
.venv/bin/argus-agent describe \
    --urn sakila-mysql.sakila.film.dataset \
    --api-url http://localhost:4600 --username admin --password '...' \
    --llm-url http://localhost:11434/v1 --model qwen2.5:7b

# 데이터 소스 전체 일괄 (모든 작업)
.venv/bin/argus-agent generate-all --datasource-id sakila-mysql \
    --api-url http://localhost:4600 --username admin --password '...'

# worker — 10분 주기로 설명 없는 데이터셋 자동 생성
.venv/bin/argus-agent worker --datasource-id sakila-mysql \
    --poll-interval 600 \
    --api-url http://localhost:4600 --username admin --password '...'
```

ollama 준비 (native):

```bash
brew install ollama          # 또는 https://ollama.com/download
ollama serve &
ollama pull qwen2.5:7b
```

## 실행 — Docker

ollama + 모델 pull + 에이전트가 compose 하나로 묶여 있습니다.

```bash
cd agent
cp .env.example .env         # ARGUS_PASSWORD 등 수정

docker compose up -d ollama          # qwen2.5:7b 자동 pull (최초 1회 수 분)
docker compose run --rm agent describe --urn sakila-mysql.sakila.film.dataset
docker compose run --rm agent generate-all --datasource-id sakila-mysql
docker compose up -d worker          # 폴링 데몬 상시 실행
```

- 카탈로그 API 가 호스트에서 실행 중이면 기본값
  `http://host.docker.internal:4600` 이 그대로 동작합니다.
- 이미 호스트에 ollama 가 있으면 `.env` 에서
  `AGENT_LLM_URL=http://host.docker.internal:11434/v1` 로 바꾸고
  compose 의 ollama 서비스는 띄우지 않아도 됩니다.

## 설정

우선순위: **CLI 인자 > 환경변수 > 기본값** — Docker 는 `.env`,
네이티브는 인자가 자연스럽도록 양쪽을 지원합니다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `ARGUS_API_URL` | `http://localhost:4600` | 카탈로그 API |
| `ARGUS_USERNAME` / `ARGUS_PASSWORD` | `admin` / (없음) | 반입은 admin 권한 필요 |
| `AGENT_LLM_URL` | `http://localhost:11434/v1` | OpenAI 호환 엔드포인트 |
| `AGENT_MODEL` | `qwen2.5:7b` | 모델 이름 |
| `AGENT_LLM_API_KEY` | `ollama` | ollama 는 임의 값이면 됨 |
| `AGENT_MODE` | `suggest` | suggest / apply |
| `AGENT_POLL_INTERVAL` | `300` | worker 폴링 주기(초) |
| `AGENT_NAME` | `argus-catalog-assistant` | serve 셀프-텔레메트리 레지스트리 이름 |
| `AGENT_TELEMETRY` | `on` | serve 셀프-텔레메트리 on/off |

LLM 백엔드는 OpenAI 호환이면 무엇이든 가능합니다:

```bash
# vLLM
--llm-url http://gpu-server:8000/v1 --model Qwen/Qwen2.5-7B-Instruct
# OpenAI
--llm-url https://api.openai.com/v1 --model gpt-4o-mini --llm-api-key sk-...
```

## 동작 흐름

```
로그인(admin) → 대상 해석(--urn 단일 / --datasource-id 일괄)
→ 컨텍스트 수집: 스키마 + 샘플(상위 5행) + 용어집(표준 용어 일관성)
→ LLM 생성 (작업별 한국어 프롬프트, JSON 출력은 환각 컬럼 필터링)
→ POST /ai/datasets/{id}/suggestions/import   (제안 반입)
→ UI: 데이터셋 상세 > AI 제안에서 승인/거절
```

- 일괄 모드는 한 데이터셋·한 작업이 실패해도 나머지를 계속 처리하고,
  실패가 있으면 exit code 1 을 반환합니다 (스케줄러 알림 연동용).
- worker 는 `--once` 로 1회 실행할 수 있어 cron 에도 등록 가능합니다.

## AI 어시스턴트 서버 (serve 모드)

카탈로그 UI 의 AI 어시스턴트(우하단 플로팅 챗)를 **tool-use 채팅**으로
구동하는 서버입니다. LLM 이 카탈로그 도구를 호출해 실데이터 근거로 답합니다.

| 도구 | 호출하는 카탈로그 API | 용도 (예시 질문) |
|---|---|---|
| `search_datasets` | `/catalog/search/unified` | 시맨틱 통합 검색 — "고객 테이블 뭐 있어?" |
| `get_dataset_detail` | `/catalog/datasets/{id}` | 스키마(타입·PK·PII)·태그·행 수 — 테이블 설명, SQL 작성 재료 |
| `get_erd` | `/catalog/datasets/{id}/erd` | FK 조인 경로 — 멀티 테이블 SQL 의 조인 근거 |
| `get_quality` | `/quality/datasets/{id}/score`·`results` | 점수·실패 규칙·위반 샘플 — "품질이 왜 WARN?" |
| `get_quality_rule_recommendations` | `/quality/datasets/{id}/rules/recommendations` | 프로파일 기반 규칙 후보 — "어떤 품질 규칙 걸까?" |
| `get_lineage` | `/catalog/datasets/{id}/lineage` | 원천/영향 범위 + 업스트림 품질 경고 — "이 데이터 어디서 와?" |
| `get_glossary_term` | `/catalog/glossary` | 비즈니스 용어 정의 — "이 용어 무슨 뜻이야?" |
| `get_standard_compliance` | `/standards/mappings/dataset`·`/compliance` | 표준 용어 준수율·비준수 컬럼 — "명명 규칙 잘 지켜?" |
| `validate_sql` | (로컬 검증 — API 호출 없음) | 생성한 SQL 의 SELECT-only 자가 검증 |

동작 흐름 — LLM 이 질문에 따라 도구를 골라 호출하고(최대 6라운드),
결과를 근거로 답합니다. 모든 단계가 SSE 이벤트
(`tool_call`/`tool_result`/`text_delta`)로 UI 에 표시됩니다.

설계 메모(`assistant.py`):
- **도구 판단은 비스트리밍**으로 한다 — ollama/qwen2.5 는 스트리밍 모드에서
  도구 호출 구조화에 실패하거나 본문에 호출 JSON 을 누수시키는 경우가 있어,
  도구 호출은 비스트리밍으로 받고 **최종 본문만 의사 스트리밍**(청크 분할)으로
  점진 표시한다. 구조화에 실패하면 본문 속 JSON 앵커를 찾아 복구한다.
- **한국어 최우선** — 다국어 모델(qwen2.5)이 중국어(한자)·일본어를 섞으면
  같은 내용을 100% 한국어로 1회 자동 재작성한다 (식별자·SQL 키워드는 원문 유지).

```
"점포별 월 대여 건수 SQL 만들어줘"
  → search_datasets("대여")          # 테이블 후보 찾기
  → get_dataset_detail(rental)       # 스키마 확보
  → get_erd(rental)                  # rental↔store FK 조인 경로 확인
  → (SQL 작성) → validate_sql(...)   # SELECT-only 자가 검증
  → 최종 답변: SQL 코드블록 + 사용 테이블·조인 근거
```

```bash
# 기동 (native) — 카탈로그 계정 불필요: 사용자 토큰을 위임받아 동작
argus-agent serve --port 8930 \
    --api-url http://localhost:4600 \
    --llm-url http://localhost:11434/v1 --model qwen2.5:7b

# Docker
docker compose up -d assistant
```

백엔드 연결 — **카탈로그 UI 의 `설정 > AI 어시스턴트` 탭**에서 설정합니다(권장):

1. **에이전트 기반 어시스턴트 활성화** 토글 ON
2. **에이전트 URL** 입력 — 예) `http://localhost:8930`
3. **연결 테스트**(에이전트 `/health` 핑) → **저장**

저장 즉시 다음 채팅부터 이 서버로 프록시되어 도구 카드와 함께 근거 기반 답변을
제공합니다. 설정은 DB(`catalog_configuration`)에 저장되어 재시작 없이 반영됩니다.

게이팅 — 우하단 **플로팅 챗 버튼**은 이 탭에서 활성화(`assistant_enabled=true`)하고
`ai.assistant` 기능 권한이 있을 때만 표시됩니다. 활성화했지만 URL 이 없으면
도구 없는 **내장 단순 대화**로 폴백합니다(이 경우 `LLM / AI` 탭의 제공자가
켜져 있어야 실제 답변 가능).

> 초기 배포 기본값은 `config.properties` 의 `assistant.agent.url` 로도 줄 수 있으나
> (DB 미시드 시 폴백), 운영 중 변경은 위 설정 탭을 사용합니다.

보안 모델:
- 백엔드가 **사용자의 Bearer 토큰을 그대로 전달** — 도구가 보는 데이터는
  곧 그 사용자 권한으로 보이는 데이터 (권한 위임 없음)
- SQL 은 작성·검증만 (실행 기능 없음), SELECT 단일 문장 가드

## 셀프-텔레메트리 (serve 모드)

serve 모드는 부팅 후 첫 채팅에서 자신을 카탈로그의 **AI Agent 레지스트리**
(`/ai-agents`)에 등록하고, 이후 채팅마다 **호출 지표(지연·토큰·성공여부)**를
`/ai-agents/{name}/invocations` 로 push 한다 — 카탈로그가 자기 AI 어시스턴트를
관측·평판 지표로 거버넌스하게 만든다.

- **권한 위임**: 등록·적재 모두 사용자 토큰으로 수행 (별도 관리자 계정 없음).
- **베스트에포트**: 레지스트리 연결/등록/적재가 실패해도 `warning` 로그만 남기고
  채팅 응답에는 전혀 영향이 없다 — 레지스트리가 없는 환경에서도 안전하다.
- **끄기**: `AGENT_TELEMETRY=off` 또는 `serve --telemetry off`.
- 등록은 프로세스당 1회 (이름 기준 idempotent — 이미 있으면 건너뜀).

```bash
argus-agent serve --port 8930 \
    --agent-name argus-catalog-assistant --telemetry on \
    --api-url http://localhost:4600 --llm-url http://localhost:11434/v1
```

지표 확인: `GET /api/v1/ai-agents/argus-catalog-assistant/metering` (호출/토큰/지연/소비자 집계).

## 도구 확장 방법

새 도구는 `tools.py` 한 파일에서 끝납니다 — **함수 + 스키마 등록** 두 단계.
루프(assistant.py)·서버(server.py)·UI 는 수정할 필요가 없습니다.

### 1) 실행 함수 작성

첫 인자는 `ToolContext`(카탈로그 API + 사용자 토큰), 나머지는 LLM 이
채우는 파라미터. **반환 dict 는 그대로 LLM 컨텍스트에 들어가므로**
필요한 필드만 추려 작게 유지합니다.

```python
def get_glossary_term(ctx: ToolContext, term_name: str) -> dict:
    """용어집에서 비즈니스 용어의 정의를 찾는다."""
    data = ctx.get(f"/glossary/nodes?q={urllib.parse.quote(term_name)}")
    terms = [n for n in data if n.get("node_type") == "TERM"][:3]
    return {"terms": [
        {"name": t["name"], "description": (t.get("description") or "")[:200]}
        for t in terms
    ]}
```

### 2) `TOOLS` 레지스트리에 등록

`description` 이 LLM 의 도구 선택 기준이므로 **언제 쓰는지**를 명확히
씁니다 (예: "사용자가 비즈니스 용어의 뜻을 물으면 사용").

```python
TOOLS["get_glossary_term"] = {
    "fn": get_glossary_term,
    "schema": {
        "type": "function",
        "function": {
            "name": "get_glossary_term",
            "description": "용어집에서 비즈니스 용어의 정의를 찾는다. 사용자가 용어의 뜻을 물으면 사용.",
            "parameters": {
                "type": "object",
                "properties": {"term_name": {"type": "string", "description": "용어 이름"}},
                "required": ["term_name"],
            },
        },
    },
}
```

### 3) (선택) UI 한글 라벨

도구 카드에 한글 라벨을 보여주려면 프런트의
`features/assistant/chat-panel.tsx` 의 `TOOL_LABELS` 에 한 줄 추가합니다.
없으면 도구 이름이 그대로 표시됩니다.

### 작성 규칙

- **읽기 전용**: 도구는 조회만 한다 — 쓰기 도구가 필요해지면 사람 확인
  단계(HITL)를 먼저 설계할 것 (배치 모드의 suggest 패턴 참고)
- **사용자 권한**: `ctx.get()` 은 사용자 토큰으로 호출되므로 권한을
  우회하지 않는다 — 별도 계정을 쓰는 도구를 만들지 말 것
- **실패는 결과로**: 예외를 던지지 말고 `{"error": "..."}` 를 반환하면
  `run_tool` 이 처리한다 — LLM 이 오류를 보고 우회하거나 사과한다
- **컨텍스트 절약**: 긴 텍스트는 자르고(`[:200]`), 목록은 상한을 둔다 —
  도구 결과가 크면 라운드가 거듭될수록 프롬프트가 폭발한다
- **프롬프트 안내**: 도구 사용 순서가 중요하면 `assistant.py` 의
  `SYSTEM_PROMPT` 원칙 목록에 한 줄 추가한다

## 로드맵

- SQL 실행 (read-only 계정·LIMIT·사람 확인 단계를 갖춘 뒤)
- ✅ 표준 용어/글로서리 어시스턴트 도구 (`get_standard_compliance`·`get_glossary_term`)
- ✅ 셀프-텔레메트리 — AI Agent 레지스트리 자기등록 + 호출 지표 push (`telemetry.py`)
- ✅ 품질 규칙 제안·설명 (`get_quality_rule_recommendations` — 프로파일 기반 후보)
- ✅ 한국어 최우선 재작성 + 비스트리밍 도구호출 안정화 (ollama/qwen2.5 누수 대응)
- ✅ UI 연결을 `설정 > AI 어시스턴트` 탭으로 일원화 (활성화 토글 + URL + 연결 테스트)
- 대화 이력 외부 저장소(redis) — 멀티 인스턴스 확장
