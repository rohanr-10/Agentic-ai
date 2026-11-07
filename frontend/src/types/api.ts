/**
 * Wire types for the backend API (docs/BACKEND_API_INTEGRATION_MAP.md is the
 * human-readable version). Field-for-field with the backend Pydantic schemas;
 * extra defensive `?` only where the backend genuinely omits/nulls fields.
 */
import type {
  ApprovalKind,
  ApprovalStatus,
  ConversationStatus,
  ExecutionStatus,
  FileStatus,
  JobStatus,
  MemoryCategory,
  MemoryScope,
  MemorySource,
  MessageRole,
  ModelAdapterType,
  NotificationKind,
  OperatingMode,
  ScanStatus,
  TaskStatus,
  UserRole,
} from "./enums";

// Re-export so feature modules can import wire enum types from either module.
export type {
  ApprovalKind,
  ApprovalStatus,
  ConversationStatus,
  ExecutionStatus,
  FileStatus,
  JobStatus,
  MemoryCategory,
  MemoryScope,
  MemorySource,
  MessageRole,
  ModelAdapterType,
  NotificationKind,
  OperatingMode,
  ScanStatus,
  TaskStatus,
  UserRole,
};

export type UUID = string;
/** Backend timestamps are ISO strings; some SSE payloads stringify Date objects. */
export type Iso = string;

// ------------------------------------------------------------------ auth
export interface UserOut {
  id: UUID;
  email: string;
  display_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: Iso;
  last_login_at: Iso | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: UserOut;
}

// ---------------------------------------------------------- conversations
export interface ConversationOut {
  id: UUID;
  title: string;
  status: ConversationStatus;
  mode: OperatingMode;
  system_prompt: string | null;
  model_config_id: UUID | null;
  message_count: number;
  last_message_at: Iso | null;
  created_at: Iso;
  updated_at: Iso;
}

export interface AttachmentOut {
  file_id: UUID;
  filename: string;
  mime_type: string;
  size_bytes: number;
}

export interface ToolCallMeta {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface UsageMeta {
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  estimated?: boolean | null;
}

export interface MessageOut {
  id: UUID;
  conversation_id: UUID;
  seq: number;
  role: MessageRole;
  content: string;
  parent_message_id: UUID | null;
  is_active: boolean;
  token_count: number | null;
  tool_call_id: string | null;
  tool_name: string | null;
  meta: {
    model?: string;
    adapter?: string;
    finish_reason?: string;
    tool_rounds?: number;
    streamed?: boolean;
    interrupted?: boolean;
    regenerated_from?: string;
    superseded_by_regeneration?: boolean;
    edited?: boolean;
    tool_error?: boolean;
    usage?: UsageMeta | null;
    tool_calls?: ToolCallMeta[];
    [key: string]: unknown;
  };
  created_at: Iso;
  edited_at: Iso | null;
  attachments: AttachmentOut[];
}

export interface ChatResponse {
  user_message: MessageOut;
  assistant_message: MessageOut;
  tool_messages: MessageOut[];
}

export interface SearchHit {
  message_id: UUID;
  seq: number;
  role: MessageRole;
  content_snippet: string;
  created_at: Iso;
}

// ----------------------------------------------------------------- models
export interface ModelCapabilities {
  streaming?: boolean;
  tool_calls?: boolean;
  structured_output?: boolean;
  [key: string]: unknown;
}

export interface ModelConfigOut {
  id: UUID;
  name: string;
  display_name: string;
  adapter_type: ModelAdapterType;
  endpoint: string | null;
  api_key: string | null; // masked by the backend
  model_id: string | null;
  context_window: number;
  max_output_tokens: number;
  capabilities: ModelCapabilities;
  parameters: Record<string, unknown>;
  is_default: boolean;
  is_enabled: boolean;
  created_at: Iso;
  updated_at: Iso;
}

export interface ModelHealthOut {
  model_id: UUID;
  name: string;
  healthy: boolean;
  detail: string;
  checked_at: string;
}

export interface ModelConfigCreateIn {
  name: string;
  display_name: string;
  adapter_type: ModelAdapterType;
  endpoint?: string | null;
  api_key?: string | null;
  model_id?: string | null;
  context_window?: number;
  max_output_tokens?: number;
  capabilities?: Record<string, unknown>;
  parameters?: Record<string, unknown>;
  is_default?: boolean;
  is_enabled?: boolean;
}

export interface ModelConfigUpdateIn {
  display_name?: string;
  endpoint?: string | null;
  api_key?: string | null;
  model_id?: string | null;
  context_window?: number;
  max_output_tokens?: number;
  capabilities?: Record<string, unknown>;
  parameters?: Record<string, unknown>;
  is_default?: boolean;
  is_enabled?: boolean;
}

// ------------------------------------------------------------------ tasks
export interface TaskBudgets {
  max_steps?: number;
  max_tool_calls?: number;
  deadline_s?: number;
  max_plan_revisions?: number;
}

export interface PlanStepSpec {
  id: string;
  title: string;
  description?: string;
  tool_key?: string | null;
  input?: Record<string, unknown>;
  depends_on?: string[];
  requires_approval?: boolean;
  max_retries?: number;
  timeout_s?: number;
  success_criteria?: string[];
}

export interface PlanSpec {
  objective_restated?: string;
  steps: PlanStepSpec[];
  notes?: string;
}

export interface TaskUsage {
  steps_executed?: number;
  tool_calls?: number;
  revisions?: number;
  [key: string]: unknown;
}

export interface TaskError {
  code: string;
  message: string;
}

export interface TaskOut {
  id: UUID;
  title: string;
  objective: string;
  status: TaskStatus;
  mode: OperatingMode;
  conversation_id: UUID | null;
  priority: number;
  version: number;
  plan_version: number;
  usage: TaskUsage;
  budgets: TaskBudgets;
  cancel_requested: boolean;
  pause_requested: boolean;
  deadline_at: Iso | null;
  started_at: Iso | null;
  finished_at: Iso | null;
  result_summary: string | null;
  artifacts: Record<string, unknown> | null;
  error: TaskError | null;
  created_at: Iso;
  updated_at: Iso;
}

export interface TaskDetailOut extends TaskOut {
  plan: PlanSpec | null;
  checkpoint: {
    completed?: Record<string, unknown>;
    approved_steps?: string[];
    [key: string]: unknown;
  };
  additional_context: Array<{ content: string; at: string; request_replan: boolean }>;
}

export interface TaskExecutionOut {
  id: UUID;
  step_id: string;
  attempt: number;
  status: ExecutionStatus;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error: TaskError | null;
  duration_ms: number | null;
  started_at: Iso | null;
  finished_at: Iso | null;
  created_at: Iso;
}

// -------------------------------------------------------------- approvals
export interface ApprovalOut {
  id: UUID;
  task_id: UUID;
  kind: ApprovalKind;
  step_id: string | null;
  plan_version: number | null;
  title: string;
  payload: {
    objective?: string;
    plan?: PlanSpec;
    step?: PlanStepSpec;
    depends_on?: string[];
    [key: string]: unknown;
  };
  status: ApprovalStatus;
  reason: string | null;
  requested_at: Iso;
  decided_at: Iso | null;
  expires_at: Iso | null;
}

// ------------------------------------------------------------------ files
export interface FileOut {
  id: UUID;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  status: FileStatus;
  scan_status: ScanStatus;
  extracted_chars: number;
  error: string | null;
  conversation_id: UUID | null;
  created_at: Iso;
  updated_at: Iso;
}

export interface FileDetailOut extends FileOut {
  extracted_text?: string | null;
}

// ----------------------------------------------------------------- memory
export interface MemoryOut {
  id: UUID;
  category: MemoryCategory;
  scope: MemoryScope;
  scope_id: UUID | null;
  title: string | null;
  key: string | null;
  content: string;
  source: MemorySource;
  confidence: number;
  importance: number;
  version: number;
  provenance: Record<string, unknown>;
  expires_at: Iso | null;
  created_at: Iso;
  updated_at: Iso;
}

export interface MemorySearchHit {
  record: MemoryOut;
  score: number;
}

// ------------------------------------------------------------------ tools
export interface ToolInfo {
  key: string;
  name: string;
  description: string;
  category: string;
  permissions: string[];
  default_timeout_s: number;
  max_timeout_s: number;
  requires_approval: boolean;
  allowed_in_chat: boolean;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  is_enabled: boolean;
}

export interface ToolInvokeOut {
  tool: string;
  output: Record<string, unknown>;
  duration_ms: number;
  tool_execution_id: string;
}

// ---------------------------------------------------------- notifications
export interface NotificationOut {
  id: UUID;
  kind: NotificationKind;
  payload: Record<string, unknown>;
  read_at: Iso | null;
  created_at: Iso;
}

// ------------------------------------------------------------------ admin
export interface JobOut {
  id: UUID;
  kind: string;
  status: JobStatus;
  priority: number;
  attempts: number;
  max_attempts: number;
  run_at: Iso;
  locked_by: string | null;
  last_error: string | null;
  idempotency_key: string | null;
  created_at: Iso;
  updated_at: Iso;
  finished_at: Iso | null;
}

export interface AuditEventOut {
  id: UUID;
  user_id: UUID | null;
  event_type: string;
  resource_type: string | null;
  resource_id: string | null;
  severity: "info" | "warning" | "critical";
  ip: string | null;
  details: Record<string, unknown>;
  created_at: Iso;
}

export interface AdminStats {
  users: number;
  conversations: number;
  messages: number;
  tasks_by_status: Record<string, number>;
  jobs_by_status: Record<string, number>;
  files: number;
  memory_records: number;
}

// ----------------------------------------------------------------- health
export interface ReadinessReport {
  status: string;
  checked_at: string;
  components: Record<string, { ok: boolean; detail: string }>;
}

// ------------------------------------------------------- pagination + errors
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
  trace_id?: string | null;
}
