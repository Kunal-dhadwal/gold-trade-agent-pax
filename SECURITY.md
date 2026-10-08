# SECURITY POLICY & SECRETS MANAGEMENT
*Zero-Leakage Architecture, API Authentication, and Input Sanitization*

---

## 1. SECRETS MANAGEMENT & ENVIRONMENT ISOLATION

1. **No Hardcoded Credentials:**
   - All API keys, database credentials, and exchange secrets are provided strictly via environment variables parsed by `pydantic-settings`.
   - `.env` files are explicitly excluded via `.gitignore`.
   - A sanitized `.env.example` is maintained in source control.
2. **Log Redaction:**
   - Custom log formatters filter and mask sensitive tokens, API keys, and authorization headers (`Authorization`, `api-key`, `api-secret`).
3. **LLM Prompt Sanitization:**
   - Under no circumstances are private API credentials, user tokens, or real account balances injected into LLM context prompts.

---

## 2. API & SYSTEM SECURITY

1. **FastAPI Endpoints:**
   - Input validation handled by Pydantic models with strict typing and bounds.
   - CORS origin configuration restricted to designated frontend ports.
   - Rate limiting on public and ingestion triggers to prevent resource exhaustion.
2. **Database Access:**
   - Connection pooling with parameterized SQL via SQLAlchemy ORM (zero raw string concatenation to prevent SQL injection).
3. **Execution Safety Flags:**
   - `LIVE_TRADING_ENABLED=false` hardcoded by default. Enabling live trading requires explicit multi-step configuration.
