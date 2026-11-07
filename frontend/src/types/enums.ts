/** String unions mirroring app/db/enums.py (backend source of truth). */

export type UserRole = "admin" | "user";
export type OperatingMode = "conversational" | "autonomous" | "hybrid";
export type ConversationStatus = "active" | "archived";
export type MessageRole = "user" | "assistant" | "system" | "tool";
export type FileStatus = "pending" | "processing" | "ready" | "failed" | "quarantined";
export type ScanStatus = "skipped" | "pending" | "clean" | "infected";
export type ModelAdapterType = "test" | "openai_compatible";
export type TaskStatus =
  | "created"
  | "queued"
  | "planning"
  | "awaiting_approval"
  | "ready"
  | "running"
  | "paused"
  | "retrying"
  | "verifying"
  | "completed"
  | "failed"
  | "cancelled";
export type ExecutionStatus = "pending" | "running" | "succeeded" | "failed" | "skipped" | "cancelled";
export type ApprovalKind = "plan" | "step";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired";
export type MemoryCategory = "conversational" | "episodic" | "semantic" | "procedural";
export type MemoryScope = "user" | "conversation" | "task";
export type MemorySource = "user_provided" | "extracted" | "model_generated" | "verified";
export type NotificationKind =
  | "approval_requested"
  | "task_completed"
  | "task_failed"
  | "task_cancelled"
  | "file_ready"
  | "file_failed"
  | "system";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "dead" | "cancelled";

export const TERMINAL_TASK_STATUSES: readonly TaskStatus[] = ["completed", "failed", "cancelled"];
export const TASK_STATUS_ORDER: readonly TaskStatus[] = [
  "created",
  "queued",
  "planning",
  "awaiting_approval",
  "ready",
  "running",
  "paused",
  "retrying",
  "verifying",
  "completed",
  "failed",
  "cancelled",
];
