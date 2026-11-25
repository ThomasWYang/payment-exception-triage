export type RoleKey = "analyst" | "approver" | "approverAlice";

export const API_KEYS: Record<RoleKey, string> = {
  analyst: "analyst-key",
  approver: "approver-key",
  approverAlice: "approver-alice-key",
};

const AGENT_KEY = "agent-key";
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export interface CaseItem {
  id: string;
  payment_id: string;
  exception_type: string;
  assignee: string;
  status: string;
}

export interface EvidenceRef {
  source: string;
  ref: string;
  summary: string;
}

export interface Proposal {
  id: string;
  action: string;
  amount_cents: number | null;
  rationale: string;
  evidence: EvidenceRef[];
  planner_version: string;
  policy_outcome: string;
  reason_codes: string[];
  requires_approval: boolean;
}

export interface Approval {
  id: string;
  decision: string;
  approver: string;
  rationale_redacted: string;
  created_at: string;
}

export interface Execution {
  status: string;
  execution_ref: string | null;
  error_code: string | null;
}

export interface CaseView {
  case: CaseItem;
  proposal: Proposal | null;
  approval: Approval | null;
  execution: Execution | null;
}

export interface CaseList {
  items: CaseView[];
  limit: number;
  offset: number;
}

export interface AuditEvent {
  id: number;
  event_type: string;
  outcome: string;
  actor: string;
  actor_role: string;
  reason_codes: string[];
  created_at: string;
}

export interface AuditList {
  items: AuditEvent[];
}

export interface ServiceStatus {
  version: string;
  mode: string;
  uptime_seconds: number;
  auth_denials: number;
  cases: Record<string, number>;
  policy_denials: number;
  executor_failures: number;
  idempotent_replays: number;
  approval_denials: number;
  auto_retry_limit_cents: number;
  refund_cap_cents: number;
  auto_approve_enabled: boolean;
  degraded_mode: boolean;
}

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
    request_id: string;
  };
}

export class ApiError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(code: string, message: string, details: Record<string, unknown>) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

async function request<T>(
  path: string,
  apiKey: string,
  init: RequestInit & { headers?: Record<string, string> } = {},
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": apiKey,
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    let body: ErrorEnvelope | null = null;
    try {
      body = (await response.json()) as ErrorEnvelope;
    } catch {
      body = null;
    }
    throw new ApiError(
      body?.error?.code ?? "HTTP_ERROR",
      body?.error?.message ?? `HTTP ${response.status}`,
      body?.error?.details ?? {},
    );
  }
  return (await response.json()) as T;
}

export function listCases(apiKey: string): Promise<CaseList> {
  return request<CaseList>("/v1/cases", apiKey);
}

export function triageCase(caseId: string): Promise<unknown> {
  return request(`/v1/cases/${caseId}/triage`, AGENT_KEY, { method: "POST" });
}

export function approveCase(
  caseId: string,
  apiKey: string,
  decision: "APPROVE" | "REJECT",
  rationale: string,
  idempotencyKey: string,
): Promise<unknown> {
  return request(`/v1/cases/${caseId}/approve`, apiKey, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ decision, rationale }),
  });
}

export function newIdempotencyKey(): string {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

export function getAudit(caseId: string, apiKey: string): Promise<AuditList> {
  return request<AuditList>(`/v1/cases/${caseId}/audit`, apiKey);
}

export function getStatus(): Promise<ServiceStatus> {
  return request<ServiceStatus>("/status", "");
}
