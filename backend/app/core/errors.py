"""Domain error taxonomy.

Every error surfaced to an API client is an ``AppError`` subclass carrying a
stable machine-readable ``code`` and an HTTP status.  Handlers in
``app.main`` translate these into JSON responses; unexpected exceptions never
leak stack traces to clients (they are logged internally with the request's
trace id).
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected application errors."""

    code: str = "app_error"
    http_status: int = 500

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class RequestValidationError(AppError):
    code = "validation_error"
    http_status = 400


class AuthenticationError(AppError):
    code = "authentication_failed"
    http_status = 401


class AuthorizationError(AppError):
    code = "permission_denied"
    http_status = 403


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404


class ConflictError(AppError):
    code = "conflict"
    http_status = 409


class IntegrityConflictError(ConflictError):
    code = "integrity_conflict"


class RateLimitError(AppError):
    code = "rate_limited"
    http_status = 429

    def __init__(self, message: str = "rate limit exceeded", *, retry_after_s: int = 1, details: Any = None) -> None:
        super().__init__(message, details=details)
        self.retry_after_s = retry_after_s


class PayloadTooLargeError(AppError):
    code = "payload_too_large"
    http_status = 413


class BudgetExceededError(AppError):
    code = "budget_exceeded"
    http_status = 400


class ModelGatewayError(AppError):
    """Base for model-gateway failures (never leaks upstream bodies)."""

    code = "model_gateway_error"
    http_status = 502


class ModelTimeoutError(ModelGatewayError):
    code = "model_timeout"
    http_status = 504


class ModelUnavailableError(ModelGatewayError):
    code = "model_unavailable"
    http_status = 502


class ModelBadRequestError(ModelGatewayError):
    code = "model_bad_request"
    http_status = 502


class ToolExecutionError(AppError):
    code = "tool_execution_failed"
    http_status = 500


class ToolRetryableError(ToolExecutionError):
    """Tool failed in a way that may succeed on retry."""

    code = "tool_retryable_error"
    http_status = 500


class SandboxError(AppError):
    code = "sandbox_error"
    http_status = 500


class PathTraversalError(RequestValidationError):
    """An attempted path escape from its allowed root (workspace/storage)."""

    code = "path_traversal_blocked"
    http_status = 400


class SandboxTimeoutError(SandboxError):
    code = "sandbox_timeout"
    http_status = 504


class StorageError(AppError):
    code = "storage_error"
    http_status = 500


class JobError(AppError):
    code = "job_error"
    http_status = 500


class OrchestrationError(AppError):
    code = "orchestration_error"
    http_status = 400


class InvalidTransitionError(OrchestrationError):
    code = "invalid_task_transition"
    http_status = 409
