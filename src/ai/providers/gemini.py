"""Google Gemini cloud LLM provider implementation via asynchronous REST client."""

import json
import os
from typing import Any, Dict, Optional, Type
import httpx
from pydantic import BaseModel, ValidationError

from src.core.logger import logger
from src.ai.providers.base import LLMProvider, LLMResponse


class GeminiProvider(LLMProvider):
    """Secondary cloud LLM provider using Google Gemini free tier."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        super().__init__(
            name="Gemini",
            model=model,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.endpoint_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        )

    async def health_check(self) -> bool:
        """Verify Gemini API availability and credential presence."""
        return bool(self._api_key)

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
    ) -> LLMResponse:
        self.check_circuit_breaker()
        if not self._api_key:
            raise RuntimeError("Gemini API key is not configured.")

        prompt_hash = self.compute_prompt_hash(prompt, system_prompt)

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"SYSTEM INSTRUCTIONS:\n{system_prompt}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood. I will strictly follow these instructions."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
            },
        }

        if schema:
            body["generationConfig"]["responseMimeType"] = "application/json"

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                headers = {"x-goog-api-key": self._api_key}
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        self.endpoint_url,
                        headers=headers,
                        json=body,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    candidates = data.get("candidates", [])
                    if not candidates:
                        raise RuntimeError("Empty response received from Gemini.")

                    raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    usage = data.get("usageMetadata", {})
                    prompt_tokens = usage.get("promptTokenCount", len(prompt.split()))
                    completion_tokens = usage.get("candidatesTokenCount", len(raw_text.split()))

                    structured_data: Optional[Dict[str, Any]] = None
                    if schema:
                        parsed_json = json.loads(raw_text)
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
                    f"Gemini attempt {attempt + 1}/{self.max_retries + 1} failed: {err}"
                )

        self.record_failure()
        raise RuntimeError(f"Gemini generation failed after {self.max_retries + 1} attempts: {last_error}")

