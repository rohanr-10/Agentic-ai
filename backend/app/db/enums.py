"""Persisted enumerations.

Stored as VARCHAR + CHECK-free application-level validation
(``native_enum=False``) so the identical schema runs on SQLite and
PostgreSQL without dialect-specific enum types.
"""

from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class OperatingMode(str, enum.Enum):
    CONVERSATIONAL = "conversational"
    AUTONOMOUS = "autonomous"
    HYBRID = "hybrid"


class ConversationStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class FileStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class ScanStatus(str, enum.Enum):
    SKIPPED = "skipped"
    PENDING = "pending"
    CLEAN = "clean"
    INFECTED = "infected"


class ModelAdapterType(str, enum.Enum):
    TEST = "test"
    OPENAI_COMPATIBLE = "openai_compatible"


class TaskStatus(str, enum.Enum):
    CREATED = "created"
    QUEUED = "queued"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    RETRYING = "retrying"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}


class ExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ApprovalKind(str, enum.Enum):
    PLAN = "plan"
    STEP = "step"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MemoryCategory(str, enum.Enum):
    CONVERSATIONAL = "conversational"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class MemoryScope(str, enum.Enum):
    USER = "user"
    CONVERSATION = "conversation"
    TASK = "task"


class MemorySource(str, enum.Enum):
    USER_PROVIDED = "user_provided"
    EXTRACTED = "extracted"
    MODEL_GENERATED = "model_generated"
    VERIFIED = "verified"


class ToolRunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"
    CANCELLED = "cancelled"


class AuditSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NotificationKind(str, enum.Enum):
    APPROVAL_REQUESTED = "approval_requested"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    FILE_READY = "file_ready"
    FILE_FAILED = "file_failed"
    SYSTEM = "system"
