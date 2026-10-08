# ADR-006: LLM Provider Abstraction & Model Selection Invariants
**Status:** Approved & Frozen  
**Date:** 2026-10-08  
**Author:** Lead Quantitative Systems Engineer & AI Architect  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `ADR-004-ai-vs-deterministic-boundaries.md`

---

## 1. CONTEXT & PROBLEM STATEMENT

Phase 2 introduces AI Multi-Agent reasoning to analyze Gold market structure, hypothesize strategy concepts, and evaluate performance trade-offs. 

Directly coupling the application to a specific commercial LLM SDK (such as OpenAI, Anthropic, or Google GenAI) introduces substantial architectural risks:
1. **Vendor Lock-in:** Codebases become tied to proprietary SDK abstractions, response schemas, and lifecycle changes.
2. **Cost Escalation:** Automated research loops can generate thousands of queries during parameter and hypothesis iterations, creating unsustainable API bills if tied to paid cloud models.
3. **Privacy & Data Security:** Sending proprietary alpha hypotheses or research structures over external network APIs can expose IP.
4. **Reliability & Rate Limiting:** External cloud APIs suffer from transient outages, rate limits (HTTP 429), and non-deterministic latency.

---

## 2. DECISION

We formally establish the **LLM Provider Abstraction Architecture**:

### A. Abstract Interface Isolation
All AI reasoning must route through an abstract `LLMProvider` interface located at `src/ai/providers/base.py`:

```python
class LLMProvider(ABC):
    @abstractmethod
    async def generate_text(self, request: LLMTextRequest) -> LLMTextResponse:
        """Raw text generation for unstructured analysis."""
        ...

    @abstractmethod
    async def generate_structured(
        self, request: LLMStructuredRequest, schema: Type[BaseModel]
    ) -> BaseModel:
        """Guaranteed schema-conforming JSON generation."""
        ...

    @abstractmethod
    async def health_check(self) -> ProviderHealthStatus:
        """Runtime connectivity and model availability check."""
        ...
```

The core quant engines, backtester, and web APIs must NEVER import provider-specific SDKs.

### B. Free-First Provider Hierarchy
To guarantee zero-cost, private quantitative research, the platform implements a strict provider selection hierarchy:

1. **Primary Provider: Local Ollama (`OllamaProvider`)**
   - **Endpoint:** `http://localhost:11434`
   - **Recommended Models:** `qwen2.5-coder:7b`, `deepseek-r1:8b`, or `llama3.1:8b`.
   - **Benefits:** 100% offline, zero marginal cost, zero data leakage, reproducible latency.
   - **Default Execution Mode:** Active default across local development and batch research sessions.

2. **Secondary Provider: Google Gemini API Free Tier (`GeminiProvider`)**
   - **Endpoint:** `https://generativelanguage.googleapis.com`
   - **Recommended Models:** `gemini-2.5-flash` or `gemini-1.5-flash` (free tier rate limits respected).
   - **Benefits:** High context window, strong structured JSON enforcement, available when local GPU resources are constrained.
   - **Activation:** Activated only when configured via environment (`LLM_PROVIDER=gemini`) and `GEMINI_API_KEY` is present.

3. **Fallback & Circuit Breaker Policy**
   - If the primary provider experiences 3 consecutive timeouts or connection errors, the engine activates a circuit breaker.
   - Research sessions fail gracefully with structured error codes (`PROVIDER_UNAVAILABLE`) rather than hanging or silently falling back to unvetted paid services.

### C. Determinism & Prompt Audit Invariants
1. **Temperature Pinning:** All hypothesis and strategy proposal requests must use a low, deterministic temperature ($\le 0.2$, default $0.1$) to maximize structural reliability and reduce hallucinations.
2. **Prompt Hashing:** Every outbound prompt is SHA-256 hashed. The hash, token count, model ID, and full raw output are permanently recorded in the `agent_decisions` relational table.
3. **No Secret Ingestion:** Prompts are passed through a mandatory redaction filter before dispatch. Any string matching API keys, database connection strings, or system secrets raises an immediate `SecuritySanitizationException`.

---

## 3. CONSEQUENCES

### Positive:
- **Zero Cost Floor:** Quantitative research can run indefinitely on commodity hardware without external subscription costs.
- **Pluggability:** New open-source models (via Ollama or vLLM) can be evaluated simply by changing an environment variable (`OLLAMA_MODEL=...`).
- **Institutional Auditability:** Every strategy generated has an auditable cryptographic link to the exact prompt, temperature, and model weights version that generated it.

### Negative / Trade-offs:
- Local models require adequate local RAM/VRAM (minimum 8GB system RAM for 7B quantizations).
- Structured output reliability varies across local models; requires rigorous schema retry mechanisms (Pydantic validation + corrective retry prompt).

---

## 4. COMPLIANCE & VERIFICATION
1. Code reviews must verify that zero imports of `httpx` or proprietary LLM SDKs exist in `src/strategy/` or `src/quant/`.
2. Unit tests must mock `LLMProvider` using synthetic fixtures (`MockLLMProvider`), ensuring test suites execute in isolated environments with 0 network dependencies.
3. Automated security scans must verify that no API keys or cloud tokens are hardcoded.

