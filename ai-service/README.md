# ai-service (deferred)

Placeholder only. Nothing here is built in v1.

## Why it's empty

Per the PRD (§6, "Won't have in v1"), the dedicated LangChain + FastAPI AI microservice with
LangSmith tracing comes after the first few paying customers. Until then, the AI assistant
runs inside the main backend:

- Endpoint: `backend/app/api/v1/ai.py` (`/ai/query`, served under `/api/v1`)
- Logic: `backend/app/services/ai_service.py`, a direct LLM call over a pre-fetched,
  read-only, tenant-scoped data context

## What moves here later (TRD §1.3, §3.2)

- The `/ai/query` implementation, rebuilt as a FastAPI + LangChain service with LangSmith
  tracing.
- Tool-calling instead of a single stuffed prompt.

## Contract that does not change

- **Read-only.** The service connects to Postgres as the `ai_readonly` role, which has
  `SELECT` only on tenant-scoped views. No write grants exist.
- **No write tools.** The agent's tool registry never includes write-capable tools. This
  is decided at registration time, not filtered at runtime.
- **Admin only.** The assistant is exposed in `apps/admin` only, never on the storefront.
