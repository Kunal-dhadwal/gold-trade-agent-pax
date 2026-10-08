"""Abstract Base Agent for the AI Quantitative Research Collective."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel

from src.ai.providers.base import LLMProvider, LLMResponse
from src.core.logger import logger


class BaseAgent(ABC):
    """Abstract agent with standardized provider execution and audit trail logging."""

    def __init__(self, name: str, role: str, provider: LLMProvider):
        self.name = name
        self.role = role
        self.provider = provider

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Standardized system persona and operational constraints."""
        pass

    async def execute_prompt(
        self,
        prompt: str,
        schema: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        """Execute prompt against configured LLM provider."""
        logger.info(f"Agent [{self.name}] executing prompt with provider [{self.provider.name}]...")
        response = await self.provider.generate(
            prompt=prompt,
            system_prompt=self.system_prompt,
            schema=schema,
        )
        return response

