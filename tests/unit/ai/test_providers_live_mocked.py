"""Unit tests for OllamaProvider and GeminiProvider with mocked httpx responses."""

import pytest
import httpx
from unittest.mock import patch, AsyncMock
from pydantic import BaseModel

from src.ai.providers.ollama import OllamaProvider
from src.ai.providers.gemini import GeminiProvider


class MockSchema(BaseModel):
    decision: str
    confidence: float


@pytest.mark.asyncio
async def test_ollama_provider_mocked_http_success():
    provider = OllamaProvider(base_url="http://localhost:11434", model="deepseek-r1:8b")

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {
        "response": '{"decision": "LONG", "confidence": 0.88}',
        "prompt_eval_count": 25,
        "eval_count": 15,
    }

    with patch("httpx.AsyncClient.post", return_value=mock_resp):
        res = await provider.generate(
            prompt="Analyze breakout",
            system_prompt="Quant agent",
            schema=MockSchema,
        )

        assert res.provider == "Ollama"
        assert res.structured_data is not None
        assert res.structured_data["decision"] == "LONG"
        assert res.structured_data["confidence"] == 0.88
        assert res.prompt_tokens == 25


@pytest.mark.asyncio
async def test_gemini_provider_mocked_http_success():
    provider = GeminiProvider(api_key="test-api-key", model="gemini-2.5-flash")

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": '{"decision": "SHORT", "confidence": 0.92}'}]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 30,
            "candidatesTokenCount": 20,
        },
    }

    with patch("httpx.AsyncClient.post", return_value=mock_resp):
        res = await provider.generate(
            prompt="Analyze breakdown",
            schema=MockSchema,
        )

        assert res.provider == "Gemini"
        assert res.structured_data is not None
        assert res.structured_data["decision"] == "SHORT"
        assert res.structured_data["confidence"] == 0.92


@pytest.mark.asyncio
async def test_gemini_missing_api_key_raises():
    provider = GeminiProvider(api_key="", model="gemini-2.5-flash")
    with pytest.raises(RuntimeError) as exc:
        await provider.generate("test prompt")
    assert "Gemini API key is not configured" in str(exc.value)
