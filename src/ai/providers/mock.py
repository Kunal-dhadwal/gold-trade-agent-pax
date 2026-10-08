"""Deterministic offline Mock LLM provider for unit and integration testing."""

import json
from typing import Any, Callable, Dict, List, Optional, Type
from pydantic import BaseModel

from src.ai.providers.base import LLMProvider, LLMResponse


class MockLLMProvider(LLMProvider):
    """Deterministic, offline mock provider for testing without external services."""

    def __init__(
        self,
        model: str = "mock-agent-model",
        canned_responses: Optional[List[str]] = None,
        custom_responder: Optional[Callable[[str, Optional[Type[BaseModel]]], Any]] = None,
        should_fail: bool = False,
        timeout: float = 5.0,
    ):
        super().__init__(
            name="MockLLM",
            model=model,
            timeout=timeout,
            max_retries=1,
            circuit_breaker_threshold=2,
        )
        self.canned_responses = list(canned_responses or [])
        self.custom_responder = custom_responder
        self.should_fail = should_fail
        self.call_history: List[Dict[str, Any]] = []

    def set_canned_responses(self, responses: List[str]) -> None:
        self.canned_responses = list(responses)

    def add_canned_response(self, response: str) -> None:
        self.canned_responses.append(response)

    async def health_check(self) -> bool:
        return not self.should_fail

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        self.check_circuit_breaker()
        prompt_hash = self.compute_prompt_hash(prompt, system_prompt)

        self.call_history.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "schema": schema,
                "prompt_hash": prompt_hash,
            }
        )

        if self.should_fail:
            self.record_failure()
            raise RuntimeError("MockLLMProvider simulated failure.")

        # 1. Custom responder if present
        if self.custom_responder:
            result = self.custom_responder(prompt, schema)
            if isinstance(result, BaseModel):
                text_content = result.model_dump_json()
                structured = result.model_dump()
            elif isinstance(result, dict):
                text_content = json.dumps(result)
                structured = result
            else:
                text_content = str(result)
                structured = None

            self.record_success()
            return LLMResponse(
                content=text_content,
                prompt_hash=prompt_hash,
                prompt_tokens=len(prompt.split()),
                completion_tokens=len(text_content.split()),
                model=self.model,
                provider=self.name,
                structured_data=structured,
            )

        # 2. Canned response queue
        if self.canned_responses:
            text_content = self.canned_responses.pop(0)
            structured = None
            if schema:
                try:
                    parsed = json.loads(text_content)
                    validated = schema.model_validate(parsed)
                    structured = validated.model_dump()
                except Exception as err:
                    self.record_failure()
                    raise RuntimeError(f"Mock response failed schema validation: {err}")

            self.record_success()
            return LLMResponse(
                content=text_content,
                prompt_hash=prompt_hash,
                prompt_tokens=len(prompt.split()),
                completion_tokens=len(text_content.split()),
                model=self.model,
                provider=self.name,
                structured_data=structured,
            )

        # 3. Default fallback response
        default_text = "MOCK_RESPONSE: Deterministic research hypothesis generated."
        structured = None

        self.record_success()
        return LLMResponse(
            content=default_text,
            prompt_hash=prompt_hash,
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(default_text.split()),
            model=self.model,
            provider=self.name,
            structured_data=structured,
        )

