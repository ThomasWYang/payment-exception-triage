import { useCallback, useEffect, useState } from "react";
import {
  API_KEYS,
  ApiError,
  approveCase,
  getAudit,
  getStatus,
  listCases,
  newIdempotencyKey,
  triageCase,
  type AuditEvent,
  type CaseView,
  type RoleKey,
  type ServiceStatus,
} from "./api";

const ROLE_LABELS: Record<RoleKey, string> = {
  analyst: "Analyst Alice",
  approver: "Approver Bob",
  approverAlice: "Approver Alice (SoD test)",
};

const ROLE_HELP: Record<RoleKey, string> = {
  analyst:
    "Can view cases and audit trails, and run the agent's triage. Cannot approve.",
  approver: "Can approve or reject pending actions. Cannot triage.",
  approverAlice:
    "Owns the seeded cases, so approving them is denied (separation of duties).",
};

const SAMPLE_CASES = [
  {
    id: "CASE-1001",
    scenario: "Temporary error, active account",
    amount: "$250",
    expected: "Auto-retry → EXECUTED",
  },
  {
    id: "CASE-1002",
    scenario: "Temporary error, above auto-retry limit",
    amount: "$5,000",
    expected: "RETRY pending → approve → EXECUTED",
  },
  {
    id: "CASE-1003",
    scenario: "Insufficient funds",
    amount: "$1,200",
    expected: "HOLD auto → EXECUTED",
  },
  {
    id: "CASE-1004",
    scenario: "Account closed",
    amount: "$900",
    expected: "ESCALATE",
  },
  {
    id: "CASE-1005",
    scenario: "Limit exceeded",
    amount: "$3,000",
    expected: "ESCALATE",
  },
  {
    id: "CASE-1006",
    scenario: "Duplicate of PAY-1000",
    amount: "$1,500",
    expected: "REFUND pending → approve → EXECUTED",
  },
  {
    id: "CASE-1007",
    scenario: "Customer dispute",
    amount: "$2,500",
    expected: "REFUND pending → approve → EXECUTED",
  },
  {
    id: "CASE-1008",
    scenario: "Customer dispute over the cap",
    amount: "$20,000",
    expected: "ESCALATE",
  },
];

type PendingApproval = {
  caseId: string;
  decision: "APPROVE" | "REJECT";
  idempotencyKey: string;
};
type AuditState = { caseId: string; events: AuditEvent[] };
type DetailsState = { view: CaseView };

function statusClass(status: string): string {
  return `badge badge-${status.toLowerCase().replaceAll("_", "-")}`;
}

function formatMoney(cents: number | null): string {
  if (cents === null) {
    return "—";
  }
  return new Intl.NumberFormat("en-AU", {
    style: "currency",
    currency: "AUD",
    maximumFractionDigits: 2,
  }).format(cents / 100);
}

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    const reasons = Array.isArray(err.details.reason_codes)
      ? ` (${(err.details.reason_codes as string[]).join(", ")})`
      : "";
    return `${err.code}: ${err.message}${reasons}`;
  }
  return err instanceof Error ? err.message : "Unexpected error";
}

export default function App() {
  const [role, setRole] = useState<RoleKey>("analyst");
  const [cases, setCases] = useState<CaseView[]>([]);
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [rationale, setRationale] = useState("Reviewed the evidence");
  const [audit, setAudit] = useState<AuditState | null>(null);
  const [details, setDetails] = useState<DetailsState | null>(null);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const [caseList, serviceStatus] = await Promise.all([
        listCases(API_KEYS[role]),
        getStatus(),
      ]);
      setCases(caseList.items);
      setStatus(serviceStatus);
    } catch (err) {
      setError(formatError(err));
    }
  }, [role]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function runTriage(caseId: string) {
    setBusy(caseId);
    try {
      await triageCase(caseId);
      await refresh();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(null);
    }
  }

  function openApproval(caseId: string, decision: "APPROVE" | "REJECT") {
    setPending({ caseId, decision, idempotencyKey: newIdempotencyKey() });
  }

  async function confirmApproval() {
    if (!pending) {
      return;
    }
    setBusy(pending.caseId);
    try {
      await approveCase(
        pending.caseId,
        API_KEYS[role],
        pending.decision,
        rationale,
        pending.idempotencyKey,
      );
      setPending(null);
      await refresh();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(null);
    }
  }

  async function openAudit(caseId: string) {
    try {
      const result = await getAudit(caseId, API_KEYS[role]);
      setAudit({ caseId, events: result.items });
    } catch (err) {
      setError(formatError(err));
    }
  }

  function showAudit(caseId: string) {
    setDetails(null);
    void openAudit(caseId);
  }

  const autoRetryLimit = status
    ? formatMoney(status.auto_retry_limit_cents)
    : "$1,000";
  const refundCap = status ? formatMoney(status.refund_cap_cents) : "$10,000";

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Payment Exception Triage</h1>
          <p className="subtitle">
            An agent proposes; the service decides. Synthetic demo — the API
            enforces every control.
          </p>
        </div>
        <div className="header-right">
          <label className="field">
            <span>Acting as</span>
            <select
              value={role}
              onChange={(event) => setRole(event.target.value as RoleKey)}
            >
              {Object.entries(ROLE_LABELS).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <button className="secondary" onClick={() => void refresh()}>
            Refresh
          </button>
        </div>
      </header>
      <p className="role-hint">{ROLE_HELP[role]}</p>

      <section className="intro card" aria-label="How it works">
        <div className="intro-step">
          <span>1</span>
          <div>
            <strong>Triage</strong>
            <p>
              An OPEN case goes to the agent. It reads payment, account and
              policy evidence and proposes Retry, Hold, Escalate or Refund.
            </p>
          </div>
        </div>
        <div className="intro-step">
          <span>2</span>
          <div>
            <strong>Decide</strong>
            <p>
              Low-risk retries execute automatically. Refunds and higher-risk
              retries wait for an approver. The agent has no execute tool.
            </p>
          </div>
        </div>
        <div className="intro-step">
          <span>3</span>
          <div>
            <strong>Audit</strong>
            <p>
              Every step is recorded. Duplicate approvals replay the same
              result; audit rows cannot be changed.
            </p>
          </div>
        </div>
      </section>

      <details className="card guide" open>
        <summary>How to use this demo</summary>
        <p>
          Start as <strong>Analyst Alice</strong> and click{" "}
          <strong>Triage</strong> on an OPEN case. The Triage button runs the
          agent service; the selected role controls viewing and approval.
          Switch to <strong>Approver Bob</strong> to approve a pending action.
          Use <strong>Details</strong> for the rationale and evidence, and{" "}
          <strong>Audit</strong> for the trail.
        </p>
        <p className="hint">
          Approver Alice is the assignee of the seeded cases. Try approving one
          as her: the service denies it with <code>SELF_APPROVAL_DENIED</code>.
        </p>
      </details>

      <details className="card guide">
        <summary>Roles</summary>
        <table className="guide-table">
          <thead>
            <tr>
              <th>Role</th>
              <th>Can do</th>
              <th>Cannot do</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Agent service</td>
              <td>Read payment/account/policy evidence; propose</td>
              <td>Execute, write or approve</td>
            </tr>
            <tr>
              <td>Analyst</td>
              <td>View cases and audit trails; run triage</td>
              <td>Approve</td>
            </tr>
            <tr>
              <td>Approver</td>
              <td>Approve or reject pending actions</td>
              <td>Triage</td>
            </tr>
            <tr>
              <td>Approver Alice</td>
              <td>Approve other people's cases</td>
              <td>Approve her own seeded cases (SoD)</td>
            </tr>
          </tbody>
        </table>
      </details>

      <details className="card guide">
        <summary>Rules the service enforces</summary>
        <ul className="rules">
          <li>
            <strong>Read-only tools:</strong> the agent can only look up
            payment, account and policy evidence.
          </li>
          <li>
            <strong>No execute tool:</strong> it proposes; the service executes
            after policy and approval.
          </li>
          <li>
            <strong>Auto-retry:</strong> up to {autoRetryLimit} executes
            automatically; above that an approver is required.
          </li>
          <li>
            <strong>Refund cap:</strong> {refundCap}; a refund must also be
            within the original settled amount.
          </li>
          <li>
            <strong>Refund eligibility:</strong> settled payment and an active
            account.
          </li>
          <li>
            <strong>Separation of duties:</strong> the approver must not be the
            case assignee/creator.
          </li>
          <li>
            <strong>One execution per proposal:</strong> duplicate approvals
            replay the stored result.
          </li>
          <li>
            <strong>Audit:</strong> append-only; UPDATE/DELETE/REPLACE are
            blocked.
          </li>
          <li>
            <strong>Degraded mode:</strong> retries, refunds and holds require
            approval.
          </li>
          <li>
            <strong>Failures:</strong> planner/tool failure or timeout
            escalates; executor failure marks the case failed.
          </li>
        </ul>
      </details>

      <details className="card guide" open>
        <summary>Sample cases and expected outcomes</summary>
        <table className="guide-table">
          <thead>
            <tr>
              <th>Case</th>
              <th>Scenario</th>
              <th>Amount</th>
              <th>Expected</th>
            </tr>
          </thead>
          <tbody>
            {SAMPLE_CASES.map((item) => (
              <tr key={item.id}>
                <td className="mono">{item.id}</td>
                <td>{item.scenario}</td>
                <td>{item.amount}</td>
                <td>{item.expected}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      {status && (
        <div className="status-bar">
          <span>
            Mode: <strong>{status.mode}</strong>
          </span>
          <span>
            Open: <strong>{status.cases.OPEN ?? 0}</strong>
          </span>
          <span>
            Pending: <strong>{status.cases.PENDING_APPROVAL ?? 0}</strong>
          </span>
          <span>
            Executed: <strong>{status.cases.EXECUTED ?? 0}</strong>
          </span>
          <span>
            Escalated: <strong>{status.cases.ESCALATED ?? 0}</strong>
          </span>
          <span>
            Auto-retry ≤ <strong>{autoRetryLimit}</strong>
          </span>
          <span>
            Refund cap <strong>{refundCap}</strong>
          </span>
        </div>
      )}

      {error && (
        <div className="error" role="alert">
          <strong>{error}</strong>
          <button className="link" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      <main className="card">
        <table>
          <thead>
            <tr>
              <th>Case</th>
              <th>Payment</th>
              <th>Type</th>
              <th>Amount</th>
              <th>Assignee</th>
              <th>Status</th>
              <th>Proposal</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((view) => {
              const item = view.case;
              const proposal = view.proposal;
              return (
                <tr key={item.id}>
                  <td className="mono">{item.id}</td>
                  <td className="mono">{item.payment_id}</td>
                  <td>{item.exception_type.replaceAll("_", " ")}</td>
                  <td>{formatMoney(proposal?.amount_cents ?? null)}</td>
                  <td className="mono">{item.assignee}</td>
                  <td>
                    <span className={statusClass(item.status)}>
                      {item.status.replaceAll("_", " ")}
                    </span>
                  </td>
                  <td>
                    {proposal ? (
                      <span title={proposal.reason_codes.join(", ")}>
                        <strong>{proposal.action}</strong>
                        <span className="reasons">
                          {proposal.policy_outcome}
                          {proposal.reason_codes.length > 0
                            ? ` · ${proposal.reason_codes.join(", ")}`
                            : ""}
                        </span>
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="actions">
                    {item.status === "OPEN" && role === "analyst" && (
                      <button
                        onClick={() => void runTriage(item.id)}
                        disabled={busy === item.id}
                        title="The agent gathers evidence and proposes; it cannot execute."
                      >
                        {busy === item.id ? "Triaging…" : "Triage"}
                      </button>
                    )}
                    {item.status === "PENDING_APPROVAL" &&
                      (role === "approver" || role === "approverAlice") && (
                        <>
                          <button
                            onClick={() => openApproval(item.id, "APPROVE")}
                            disabled={busy === item.id}
                          >
                            Approve
                          </button>
                          <button
                            className="danger"
                            onClick={() => openApproval(item.id, "REJECT")}
                            disabled={busy === item.id}
                          >
                            Reject
                          </button>
                        </>
                      )}
                    <button
                      className="secondary"
                      onClick={() => setDetails({ view })}
                    >
                      Details
                    </button>
                    <button
                      className="secondary"
                      onClick={() => void openAudit(item.id)}
                    >
                      Audit
                    </button>
                  </td>
                </tr>
              );
            })}
            {cases.length === 0 && (
              <tr>
                <td colSpan={8} className="empty">
                  No cases. Run <code>python scripts/seed.py --reset</code> and
                  refresh.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </main>

      {details && (
        <DetailsModal
          view={details.view}
          onClose={() => setDetails(null)}
          onAudit={showAudit}
        />
      )}

      {pending && (
        <ApprovalModal
          pending={pending}
          rationale={rationale}
          setRationale={setRationale}
          busy={busy}
          onCancel={() => setPending(null)}
          onConfirm={() => void confirmApproval()}
        />
      )}

      {audit && <AuditModal audit={audit} onClose={() => setAudit(null)} />}
    </div>
  );
}

function DetailsModal({
  view,
  onClose,
  onAudit,
}: {
  view: CaseView;
  onClose: () => void;
  onAudit: (caseId: string) => void;
}) {
  const item = view.case;
  const proposal = view.proposal;
  const approval = view.approval;
  const execution = view.execution;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(event) => event.stopPropagation()}>
        <h2>Case details · {item.id}</h2>
        <div className="detail-grid">
          <div>
            <span>Payment</span>
            <strong className="mono">{item.payment_id}</strong>
          </div>
          <div>
            <span>Type</span>
            <strong>{item.exception_type.replaceAll("_", " ")}</strong>
          </div>
          <div>
            <span>Amount</span>
            <strong>{formatMoney(proposal?.amount_cents ?? null)}</strong>
          </div>
          <div>
            <span>Assignee</span>
            <strong className="mono">{item.assignee}</strong>
          </div>
          <div>
            <span>Status</span>
            <strong>{item.status.replaceAll("_", " ")}</strong>
          </div>
          <div>
            <span>Proposal</span>
            <strong>{proposal ? proposal.action : "—"}</strong>
          </div>
        </div>

        {proposal && (
          <div className="detail-section">
            <h3>Why this proposal</h3>
            <p>
              <strong>{proposal.policy_outcome}</strong>
              {proposal.requires_approval
                ? " · requires approval"
                : " · auto-approved"}
            </p>
            <p>{proposal.rationale}</p>
            <p className="muted">
              Reasons: {proposal.reason_codes.join(", ") || "none"} · planner{" "}
              {proposal.planner_version}
            </p>
            <h4>Evidence</h4>
            <ul className="evidence">
              {proposal.evidence.map((evidence) => (
                <li key={`${evidence.source}-${evidence.ref}`}>
                  <strong>{evidence.source}</strong> · {evidence.ref} —{" "}
                  {evidence.summary}
                </li>
              ))}
            </ul>
          </div>
        )}

        {approval && (
          <div className="detail-section">
            <h3>Approval</h3>
            <p>
              <strong>{approval.decision}</strong> by{" "}
              <span className="mono">{approval.approver}</span>
            </p>
            <p className="muted">{approval.rationale_redacted}</p>
          </div>
        )}

        {execution && (
          <div className="detail-section">
            <h3>Execution</h3>
            <p>
              <strong>{execution.status}</strong>
              {execution.execution_ref ? ` · ${execution.execution_ref}` : ""}
              {execution.error_code ? ` · ${execution.error_code}` : ""}
            </p>
          </div>
        )}

        <div className="modal-actions">
          <button className="secondary" onClick={() => onAudit(item.id)}>
            View audit
          </button>
          <button className="secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function ApprovalModal({
  pending,
  rationale,
  setRationale,
  busy,
  onCancel,
  onConfirm,
}: {
  pending: PendingApproval;
  rationale: string;
  setRationale: (value: string) => void;
  busy: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <h2>
          {pending.decision === "APPROVE" ? "Approve" : "Reject"}{" "}
          {pending.caseId}
        </h2>
        <label className="field">
          <span>Rationale</span>
          <textarea
            value={rationale}
            onChange={(event) => setRationale(event.target.value)}
            rows={3}
          />
        </label>
        <div className="modal-actions">
          <button className="secondary" onClick={onCancel}>
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={!rationale.trim() || busy === pending.caseId}
          >
            {busy === pending.caseId ? "Submitting…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}

function AuditModal({
  audit,
  onClose,
}: {
  audit: AuditState;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(event) => event.stopPropagation()}>
        <h2>Audit · {audit.caseId}</h2>
        <ol className="audit">
          {audit.events.map((event) => (
            <li key={event.id}>
              <span className="mono">{event.event_type}</span>
              <span>{event.outcome}</span>
              <span className="muted">
                {event.actor} · {event.created_at}
              </span>
              {event.reason_codes.length > 0 && (
                <span className="reasons">
                  {event.reason_codes.join(", ")}
                </span>
              )}
            </li>
          ))}
        </ol>
        <div className="modal-actions">
          <button className="secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
