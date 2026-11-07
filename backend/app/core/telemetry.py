"""Observability: Prometheus metrics + optional OpenTelemetry tracing.

Metrics are recorded through module-level helper functions that swallow their
own failures — telemetry must never break request handling.  OpenTelemetry is
an optional extra (``requirements-cloud.txt``); when the packages are absent
or ``AGP_OTEL_ENABLED`` is false, ``configure_otel`` is a logged no-op.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.core.config import Settings

logger = structlog.stdlib.get_logger("app.telemetry")

try:
    from prometheus_client import (
        REGISTRY,
        Counter,
        Histogram,
        generate_latest,
        make_asgi_app,
    )

    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover
    _PROM_AVAILABLE = False

_HTTP_REQUESTS_TOTAL: Any = None
_HTTP_REQUEST_LATENCY: Any = None
_MODEL_REQUESTS_TOTAL: Any = None
_MODEL_LATENCY: Any = None
_TOOL_EXECUTIONS_TOTAL: Any = None
_TASK_TRANSITIONS_TOTAL: Any = None
_JOBS_TOTAL: Any = None

if _PROM_AVAILABLE:
    _HTTP_REQUESTS_TOTAL = Counter(
        "agp_http_requests_total",
        "HTTP requests handled",
        labelnames=["route", "method", "status"],
    )
    _HTTP_REQUEST_LATENCY = Histogram(
        "agp_http_request_duration_seconds",
        "HTTP request latency",
        labelnames=["route", "method"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    )
    _MODEL_REQUESTS_TOTAL = Counter(
        "agp_model_requests_total",
        "Model gateway requests",
        labelnames=["model", "adapter", "status"],
    )
    _MODEL_LATENCY = Histogram(
        "agp_model_request_duration_seconds",
        "Model gateway latency",
        labelnames=["model", "adapter"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    )
    _TOOL_EXECUTIONS_TOTAL = Counter(
        "agp_tool_executions_total",
        "Tool executions",
        labelnames=["tool", "status"],
    )
    _TASK_TRANSITIONS_TOTAL = Counter(
        "agp_task_transitions_total",
        "Task state transitions",
        labelnames=["to_state"],
    )
    _JOBS_TOTAL = Counter(
        "agp_background_jobs_total",
        "Background job outcomes",
        labelnames=["kind", "status"],
    )


def record_http(*, route: str, method: str, status: int, duration_s: float) -> None:
    if not _PROM_AVAILABLE:
        return
    _HTTP_REQUESTS_TOTAL.labels(route=route, method=method, status=str(status)).inc()
    _HTTP_REQUEST_LATENCY.labels(route=route, method=method).observe(duration_s)


def record_model_request(*, model: str, adapter: str, status: str, duration_s: float) -> None:
    if not _PROM_AVAILABLE:
        return
    _MODEL_REQUESTS_TOTAL.labels(model=model, adapter=adapter, status=status).inc()
    _MODEL_LATENCY.labels(model=model, adapter=adapter).observe(duration_s)


def record_tool_execution(*, tool: str, status: str) -> None:
    if not _PROM_AVAILABLE:
        return
    _TOOL_EXECUTIONS_TOTAL.labels(tool=tool, status=status).inc()


def record_task_transition(*, to_state: str) -> None:
    if not _PROM_AVAILABLE:
        return
    _TASK_TRANSITIONS_TOTAL.labels(to_state=to_state).inc()


def record_job(*, kind: str, status: str) -> None:
    if not _PROM_AVAILABLE:
        return
    _JOBS_TOTAL.labels(kind=kind, status=status).inc()


def metrics_payload() -> bytes:
    if not _PROM_AVAILABLE:  # pragma: no cover
        return b""
    return generate_latest(REGISTRY)


def metrics_asgi_app():  # pragma: no cover - exercised in deployment docs
    if not _PROM_AVAILABLE:
        return None
    return make_asgi_app()


_otel_configured = False


def configure_otel(settings: Settings, app: Any) -> bool:
    """Initialise OpenTelemetry tracing when available and enabled.

    Returns True when tracing was configured.  Missing packages or a missing
    exporter endpoint degrade to a no-op with a log line — never a crash.
    """
    global _otel_configured
    if _otel_configured or not settings.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:
        logger.warning(
            "otel_requested_but_not_available",
            hint="install requirements-cloud.txt to enable tracing",
            error=str(exc),
        )
        return False

    if not settings.otel_endpoint:
        logger.warning("otel_enabled_but_no_endpoint_configured")
        return False

    try:
        resource = Resource.create({"service.name": settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        SQLAlchemyInstrumentor().instrument(tracer_provider=provider)
        _otel_configured = True
        logger.info("otel_tracing_configured", endpoint=settings.otel_endpoint)
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("otel_configuration_failed", error=str(exc))
        return False
