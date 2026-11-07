"""Application configuration.

All configuration is environment driven (prefix ``AGP_``) with an optional
``.env`` file for local development.  Nothing machine-specific is hardcoded:
paths, endpoints, secrets and limits are all overridable.

The same codebase runs in three environments:

* ``development`` -- local SQLite, local storage, embedded worker allowed.
* ``test``        -- used by the automated test-suite (isolated tmp dirs).
* ``production``  -- PostgreSQL required, unsafe defaults rejected.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET_KEY = "dev-insecure-secret-key-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- application ------------------------------------------------------
    app_name: str = "Agentic Platform"
    environment: Literal["development", "test", "production"] = "development"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = False

    # -- security ---------------------------------------------------------
    secret_key: SecretStr = SecretStr(DEV_SECRET_KEY)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 1800
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30
    password_min_length: int = 10
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    cors_origins: list[str] = Field(default_factory=list)
    trust_proxy_headers: bool = False

    # rate limiting (sliding window, in-memory or redis)
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 10
    rate_limit_default_per_minute: int = 600
    rate_limit_upload_per_minute: int = 60

    # request bodies
    max_request_body_bytes: int = 64 * 1024 * 1024  # must cover upload limit

    # -- database ---------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_s: int = 30

    # -- redis (optional; coordination & cross-process events) ------------
    redis_url: str | None = None

    # -- storage ----------------------------------------------------------
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: str = "./data/storage"
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_bucket: str = "agentic-platform"
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None

    # -- files ------------------------------------------------------------
    file_max_size_bytes: int = 50 * 1024 * 1024
    file_allowed_extensions: list[str] = Field(
        default_factory=lambda: [
            ".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx", ".json",
            ".py", ".js", ".ts", ".html", ".css", ".yaml", ".yml", ".toml",
            ".xml", ".log", ".jpeg", ".jpg", ".png",
        ]
    )
    file_extract_text_max_chars: int = 200_000
    file_max_extract_bytes: int = 20 * 1024 * 1024  # bytes read from storage
    file_malware_scan_enabled: bool = False         # integration point (see docs)

    # -- sandbox ----------------------------------------------------------
    sandbox_backend: Literal["local", "docker"] = "local"
    sandbox_root: str = "./data/sandboxes"
    sandbox_default_timeout_s: int = 60
    sandbox_max_output_bytes: int = 1024 * 1024
    sandbox_memory_limit_mb: int = 512
    sandbox_workspace_quota_bytes: int = 256 * 1024 * 1024
    sandbox_docker_image: str = "python:3.12-slim"
    sandbox_docker_disable_network: bool = True
    sandbox_docker_mem_limit: str = "512m"
    sandbox_docker_cpus: str = "1.0"
    # binaries the terminal tool may execute (no shell involved)
    terminal_allowed_binaries: list[str] = Field(
        default_factory=lambda: [
            "ls", "cat", "head", "tail", "wc", "grep", "find", "diff",
            "echo", "sort", "uniq", "python3", "du", "file", "stat", "mkdir",
        ]
    )
    # tools allowed to run without explicit approval inside autonomous tasks
    tool_auto_approved: list[str] = Field(
        default_factory=lambda: [
            "fs.read", "fs.write", "fs.list", "python.execute",
            "data.analyze_csv", "document.extract", "code.search",
        ]
    )

    # -- model gateway ----------------------------------------------------
    default_model_name: str = "test-echo"
    model_request_timeout_s: int = 120
    model_stream_idle_timeout_s: int = 60
    model_health_timeout_s: int = 10
    model_health_cache_s: int = 15
    # Saluki 27B integration boundary (OpenAI-compatible runtime expected;
    # see docs/SALUKI_INTEGRATION.md before enabling).
    saluki_endpoint: str | None = None
    saluki_api_key: SecretStr | None = None
    saluki_model_id: str = "saluki-27b"
    saluki_context_window: int = 32768

    # -- conversation context assembly -------------------------------------
    context_recent_messages: int = 20
    context_max_chars: int = 48_000
    context_memory_top_k: int = 5
    chat_max_tool_rounds: int = 3

    # -- tasks / orchestration ---------------------------------------------
    task_max_steps: int = 50
    task_default_deadline_s: int = 1800
    task_max_parallel_steps: int = 3
    task_step_max_retries: int = 2
    task_retry_backoff_base_s: float = 1.0
    task_max_plan_revisions: int = 1
    task_stale_after_s: int = 300
    auto_approve_plans: bool = False

    # -- background jobs / worker ------------------------------------------
    worker_embedded: bool = False
    worker_poll_interval_s: float = 1.0
    worker_lease_s: int = 120
    worker_heartbeat_s: float = 30.0
    worker_max_attempts: int = 5
    job_retry_backoff_base_s: float = 5.0
    job_batch_size: int = 10

    # -- retention / maintenance -------------------------------------------
    audit_retention_days: int = 365
    session_retention_days: int = 90
    memory_default_ttl_days: int | None = None

    # -- observability ------------------------------------------------------
    metrics_enabled: bool = True
    otel_enabled: bool = False
    otel_endpoint: str | None = None
    otel_service_name: str = "agentic-platform"

    # ----------------------------------------------------------------validators
    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        if self.environment == "production":
            if self.secret_key.get_secret_value() == DEV_SECRET_KEY:
                raise ValueError(
                    "AGP_SECRET_KEY must be set to a strong random value in "
                    "production (generate one with: python -c \"import secrets;"
                    "print(secrets.token_urlsafe(48))\")"
                )
            if not self.database_url.startswith("postgresql"):
                raise ValueError(
                    "production requires AGP_DATABASE_URL pointing at PostgreSQL"
                )
            if not self.cors_origins:
                # allow explicit opt-out, but not an accidental open policy
                if "*" in self.cors_origins:
                    raise ValueError("wildcard CORS origin is not allowed in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def resolved_path(self, value: str) -> Path:
        """Resolve a configured relative path against the process CWD."""
        return Path(value).resolve()


@lru_cache
def get_settings() -> Settings:
    """Cached application settings (module-level singleton)."""
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: force ``get_settings`` to re-read the environment."""
    get_settings.cache_clear()
