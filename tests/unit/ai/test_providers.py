"""Unit tests for LLM provider abstraction, mock provider, and circuit breaker."""

import pytest
from pydantic import BaseModel, Field

from src.ai.providers import MockLLMProvider, LLMResponse


class DemoSchema(BaseModel):
    summary: str
    score: float = Field(..., ge=0.0, le=100.0)


@pytest.mark.asyncio
async def test_mock_provider_basic_generation():
    provider = MockLLMProvider()
    resp = await provider.generate(prompt="Analyze gold market", system_prompt="You are an analyst")

    assert isinstance(resp, LLMResponse)
    assert len(resp.prompt_hash) == 64  # SHA-256 hex string
    assert resp.provider == "MockLLM"
    assert "MOCK_RESPONSE" in resp.content


@pytest.mark.asyncio
async def test_mock_provider_structured_schema():
    canned = '{"summary": "Bullish trend confirmed", "score": 85.5}'
    provider = MockLLMProvider(canned_responses=[canned])

    resp = await provider.generate(
        prompt="Generate evaluation",
        schema=DemoSchema,
    )

    assert resp.structured_data is not None
    assert resp.structured_data["summary"] == "Bullish trend confirmed"
    assert resp.structured_data["score"] == 85.5


@pytest.mark.asyncio
async def test_mock_provider_circuit_breaker():
    provider = MockLLMProvider(should_fail=True)

    # First attempt fails
    with pytest.raises(RuntimeError):
        await provider.generate("test 1")

    # Second attempt trips circuit breaker
    with pytest.raises(RuntimeError):
        await provider.generate("test 2")

    assert provider.circuit_breaker_open is True

    # Subsequent call rejected immediately by circuit breaker
    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate("test 3")
    assert "Circuit breaker is OPEN" in str(exc_info.value)

