"""Abstract LLM Provider interface and response structures."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel
from src.core.logger import logger


@dataclass
class LLMResponse:
    """Standardized response container across all LLM providers."""
    content: str
    prompt_hash: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    provider: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)
    structured_data: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class LLMProvider(ABC):
    """Abstract base class for LLM providers with circuit breaker and retry policy."""

    def __init__(
        self,
        name: str,
        model: str,
        timeout: float = 30.0,
        max_retries: int = 2,
        circuit_breaker_threshold: int = 3,
    ):
        self.name = name
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.failure_count = 0
        self.circuit_breaker_open = False

    def compute_prompt_hash(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Calculate deterministic SHA-256 hash of the complete prompt content."""
        combined = f"SYSTEM: {system_prompt or ''}\nUSER: {prompt}".encode("utf-8")
        return hashlib.sha256(combined).hexdigest()

    def record_success(self) -> None:
        """Reset failure counter upon successful execution."""
        self.failure_count = 0
        self.circuit_breaker_open = False

    def record_failure(self) -> None:
        """Increment failure counter and trip circuit breaker if threshold reached."""
        self.failure_count += 1
        if self.failure_count >= self.circuit_breaker_threshold:
            self.circuit_breaker_open = True
            logger.warning(
                f"LLM Provider {self.name} tripped circuit breaker after {self.failure_count} consecutive failures."
            )

    def check_circuit_breaker(self) -> None:
        """Raise RuntimeError if circuit breaker is currently open."""
        if self.circuit_breaker_open:
            raise RuntimeError(
                f"Circuit breaker is OPEN for provider '{self.name}'. Refusing requests until recovery."
            )

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        """Generate response asynchronously with optional structured schema validation."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify provider availability."""
        pass

