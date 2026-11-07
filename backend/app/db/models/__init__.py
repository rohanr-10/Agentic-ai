"""All ORM models — imported so ``Base.metadata`` is complete for Alembic."""

from app.db.models.ai import ModelConfig
from app.db.models.chat import Attachment, Conversation, Message
from app.db.models.files import FileRecord
from app.db.models.identity import AuthSession, PasswordResetToken, User
from app.db.models.memory import MemoryRecord, MemoryVersion
from app.db.models.platform import AuditEvent, BackgroundJob, Notification
from app.db.models.tasks import ApprovalRequest, Task, TaskExecution, TaskPlan
from app.db.models.tools import ToolDefinition, ToolExecution

__all__ = [
    "Attachment",
    "AuditEvent",
    "AuthSession",
    "ApprovalRequest",
    "BackgroundJob",
    "Conversation",
    "FileRecord",
    "MemoryRecord",
    "MemoryVersion",
    "Message",
    "ModelConfig",
    "Notification",
    "PasswordResetToken",
    "Task",
    "TaskExecution",
    "TaskPlan",
    "ToolDefinition",
    "ToolExecution",
    "User",
]
