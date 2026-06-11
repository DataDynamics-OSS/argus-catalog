"""LLM 제공자의 추상 베이스 클래스.

모든 LLM 제공자(OpenAI, Ollama, Anthropic)가 이 인터페이스를 구현한다.
레지스트리 모듈이 활성 제공자를 싱글턴으로 관리한다.
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """플러그형 LLM 제공자 인터페이스."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> dict:
        """프롬프트를 전송하고 메타데이터와 함께 응답을 반환한다.

        Returns:
            다음 키를 가진 dict:
                - "text": 생성된 텍스트 (str)
                - "prompt_tokens": 입력 토큰 수 (int 또는 None)
                - "completion_tokens": 출력 토큰 수 (int 또는 None)
        """
        ...

    @abstractmethod
    def model_name(self) -> str:
        """모델 식별자 문자열을 반환."""
        ...

    @abstractmethod
    def provider_name(self) -> str:
        """제공자 타입을 반환: 'openai', 'ollama', 'anthropic'."""
        ...

    async def close(self) -> None:
        """리소스를 해제. 정리가 필요하면 오버라이드."""
        pass
