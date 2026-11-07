# Saluki 27B Integration Guide

## Status — read this first

The platform ships with:

1. a **deterministic test adapter** (`app/gateway/adapters/test_adapter.py`)
   used by the test-suite and local development, clearly labelled in every
   output (`[test-adapter] …`); and
2. an **OpenAI-compatible adapter** (`app/gateway/adapters/openai_compatible.py`)
   which is the *integration boundary* for Saluki 27B.

**No claim is made that the platform has been verified against a real
Saluki runtime.** The OpenAI adapter is contract-tested against a local
mock server speaking the documented wire format
(`tests/unit/test_openai_adapter.py`). Connecting the real model is a
configuration + verification exercise described below.

## Integration path A — Saluki exposes an OpenAI-compatible API

Most self-hosted runtimes (vLLM, TGI, llama.cpp server, …) expose
`POST /chat/completions` with SSE streaming. If Saluki does:

```bash
# 1. configure the boundary (env or .env)
AGP_SALUKI_ENDPOINT=http://saluki-host:8000/v1
AGP_SALUKI_API_KEY=<if the runtime requires one>
AGP_SALUKI_MODEL_ID=saluki-27b
AGP_SALUKI_CONTEXT_WINDOW=32768        # set to the VERIFIED window

# 2. restart; the seeded "saluki-27b" model config picks up the endpoint
curl -s localhost:8000/api/v1/models | jq
# 3. probe it (live check against the real runtime)
curl -s -H "$AUTH" localhost:8000/api/v1/models/<saluki-model-id>/health | jq
# 4. make it the default
curl -s -X PATCH -H "$ADMIN_AUTH" -H 'content-type: application/json' \
  localhost:8000/api/v1/models/<id> -d '{"is_default": true}' | jq
```

### Verification checklist (perform ALL before declaring integration)

1. **Health**: `/models/{id}/health` reports `healthy: true` against the
   real endpoint.
2. **Non-streaming**: `POST /conversations/{id}/messages` returns a real
   completion; the assistant message persists; `meta.model` shows the
   Saluki model id.
3. **Streaming**: `POST /conversations/{id}/messages/stream` emits `delta`
   events and terminates with `done`.
4. **Structured planning**: create a task WITHOUT a client plan; the
   planner's JSON proposal passes `validate_plan` (step ids, DAG,
   known tools). If the model produces malformed JSON twice, the task
   fails with `invalid_plan` — that is correct behaviour.
5. **Context window**: send a conversation near the declared window and
   confirm no `model_bad_request` overflow errors; adjust
   `context_window` to the verified value.
6. **Token usage**: check `messages.meta.usage` — if the runtime reports
   usage it flows through; otherwise estimates are flagged
   `estimated: true`.

### Capability flags — set them honestly

After verification, update the `saluki-27b` model config capabilities to
what the runtime ACTUALLY supports:

```bash
curl -s -X PATCH -H "$ADMIN_AUTH" -H 'content-type: application/json' \
  localhost:8000/api/v1/models/<id> \
  -d '{"capabilities": {"streaming": true, "tool_calls": true, "structured_output": true}}'
```

The gateway refuses requests that need an undeclared capability (e.g.
tools) — that is deliberate: mis-declared capabilities fail loudly at
configuration time instead of silently at runtime.

For OpenAI-compatible streaming usage stats set
`parameters.include_stream_usage=true` if (and only if) the runtime
supports `stream_options.include_usage`.

## Integration path B — Saluki has a custom API

Write a new adapter module, e.g.
`app/gateway/adapters/saluki_native.py`:

```python
from app.core.errors import ModelGatewayError
from app.db.enums import ModelAdapterType
from app.gateway.base import (
    HealthReport, InferenceRequest, InferenceResponse,
    ModelAdapter, StreamEvent, register_adapter,
)

@register_adapter("saluki_native")
class SalukiNativeAdapter(ModelAdapter):
    async def generate(self, request: InferenceRequest) -> InferenceResponse: ...
    def stream(self, request: InferenceRequest): ...
    async def health(self) -> HealthReport: ...
```

Register the adapter type in `app/db/enums.ModelAdapterType`, add a model
config row (via the admin API) with `adapter_type: "saluki_native"`, and
implement:

* request/response mapping for messages, tools and (if supported)
  structured outputs,
* error normalization into the platform's error taxonomy
  (`ModelTimeoutError` / `ModelUnavailableError` / …) — never leak
  upstream bodies,
* a real health probe,
* `capabilities` from the ModelConfig row (validated, not guessed).

**No core code changes are required** — conversation management,
orchestration, memory, tools and task execution program against the
`ModelAdapter` contract only. This is the central architectural guarantee
of the platform.

## What the platform already guarantees (test-verified)

* adapter contract semantics (generate/stream/health/timeout/cancel,
  context validation, capability gating, error normalization),
* tool-call loop and planner plumbing driven by model outputs with
  strict validation,
* streaming persistence, interruption marking, provenance,
* everything in `docs/ENGINEERING_REPORT.md` marked VERIFIED.

What CANNOT be verified without the real runtime: Saluki's actual quality,
latency, context limit, tool-calling reliability and wire-format
quirks. The integration checklist above exists to establish exactly those
facts before the model is declared production-ready.
