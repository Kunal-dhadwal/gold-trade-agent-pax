"""Ollama local LLM provider implementation via asynchronous REST client."""

import json
from typing import Any, Dict, Optional, Type
import httpx
from pydantic import BaseModel, ValidationError

from src.core.logger import logger
from src.ai.providers.base import LLMProvider, LLMResponse


class OllamaProvider(LLMProvider):
    """Local-first, zero-cost Ollama LLM provider."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "deepseek-r1:8b",
        timeout: float = 45.0,
        max_retries: int = 2,
    ):
        super().__init__(
            name="Ollama",
            model=model,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.base_url = base_url.rstrip("/")

    async def health_check(self) -> bool:
        """Verify Ollama daemon availability."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.base_url}/api/version")
                return res.status_code == 200
        except Exception:
            return False

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        self.check_circuit_breaker()
        prompt_hash = self.compute_prompt_hash(prompt, system_prompt)

        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if schema:
            payload["format"] = "json"

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/generate",
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    raw_text = data.get("response", "")
                    prompt_tokens = data.get("prompt_eval_count", len(prompt.split()))
                    completion_tokens = data.get("eval_count", len(raw_text.split()))

                    structured_data: Optional[Dict[str, Any]] = None
                    if schema:
                        # Attempt to parse structured JSON
                        parsed_json = json.loads(raw_text)
                        # Validate against Pydantic schema
                        validated_obj = schema.model_validate(parsed_json)
                        structured_data = validated_obj.model_dump()

                    self.record_success()
                    return LLMResponse(
                        content=raw_text,
                        prompt_hash=prompt_hash,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        model=self.model,
                        provider=self.name,
                        raw_response=data,
                        structured_data=structured_data,
                    )

            except (httpx.HTTPError, json.JSONDecodeError, ValidationError) as err:
                last_error = err
                logger.warning(
                    f"Ollama attempt {attempt + 1}/{self.max_retries + 1} failed: {err}"
                )
                if attempt < self.max_retries and schema and isinstance(err, ValidationError):
                    # Inject validation correction hint
                    payload["prompt"] = (
                        f"{prompt}\n\n[SYSTEM NOTICE: Previous response failed schema validation: "
                        f"{err.errors()[0]['msg'] if err.errors() else str(err)}. Please output strictly valid JSON conforming to the schema.]"
                    )

        self.record_failure()
        raise RuntimeError(f"Ollama generation failed after {self.max_retries + 1} attempts: {last_error}")

