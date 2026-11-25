import { useCallback, useEffect, useState } from "react";
import {
  ACTORS,
  ApiError,
  approveCase,
  getAudit,
  getStatus,
  listCases,
  newIdempotencyKey,
  triageCase,
  type ActorKey,
  type ActingRole,
  type AuditEvent,
  type CaseView,
  type DemoIdentity,
  type ServiceStatus,
} from "./api";

function roleHelp(actor: ActorKey, role: ActingRole): string {
  if (role === "ANALYST") {
    return "Can view cases and audit trails, and request triage. Cannot approve.";
  }
  if (ACTORS[actor].roles.includes("ANALYST")) {
    return "Can approve other people's cases; cannot approve a case they created or requested triage for.";
  }
  return "Can approve or reject pending actions. Cannot triage.";
}

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
    expected: "ESCALATED",
  },
  {
    id: "CASE-1005",
    scenario: "Limit exceeded",
    amount: "$3,000",
    expected: "ESCALATED",
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
    expected: "ESCALATED",
  },
  {
    id: "CASE-1009",
    scenario: "Temporary error, above auto-retry limit",
    amount: "$5,000",
    expected: "RETRY pending → approve → EXECUTED",
  },
  {
    id: "CASE-1010",
    scenario: "Temporary error, above auto-retry limit",
    amount: "$5,000",
    expected: "RETRY pending → approve → EXECUTED",
  },
];

const CASE_OWNERS: Record<string, string> = {
  "CASE-1001": "Carol",
  "CASE-1002": "Alice",
  "CASE-1003": "Dave",
  "CASE-1004": "Bob",
  "CASE-1005": "Alice",
  "CASE-1006": "Dave",
  "CASE-1007": "Dave",
  "CASE-1008": "Alice",
  "CASE-1009": "Carol",
  "CASE-1010": "Bob",
};

type PendingApproval = {
  caseId: string;
  decision: "APPROVE" | "REJECT";
  idempotencyKey: string;
};
type AuditState = { caseId: string; events: AuditEvent[]; limit: number };
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

function policyLabel(outcome: string): string {
  switch (outcome) {
    case "AUTO_APPROVED":
      return "auto-approved";
    case "PENDING_APPROVAL":
      return "requires approval";
    case "REJECTED":
      return "rejected by policy";
    case "ESCALATED":
      return "escalated";
    default:
      return outcome.toLowerCase();
  }
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
  const [actor, setActor] = useState<ActorKey>("alice");
  const [activeRole, setActiveRole] = useState<ActingRole>("ANALYST");
  const [cases, setCases] = useState<CaseView[]>([]);
  const [caseLimit, setCaseLimit] = useState(50);
  const [status, setStatus] = useState<ServiceStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [rationale, setRationale] = useState("Reviewed the evidence");
  const [audit, setAudit] = useState<AuditState | null>(null);
  const [details, setDetails] = useState<DetailsState | null>(null);

  const identity: DemoIdentity = {
    key: ACTORS[actor].key,
    actor: ACTORS[actor].actor,
    role: activeRole,
  };

  function changeActor(next: ActorKey) {
    setActor(next);
    setActiveRole(ACTORS[next].roles[0] ?? "ANALYST");
  }

  const refresh = useCallback(async () => {
    const current: DemoIdentity = {
      key: ACTORS[actor].key,
      actor: ACTORS[actor].actor,
      role: activeRole,
    };
    try {
      setError(null);
      const [caseList, serviceStatus] = await Promise.all([
        listCases(current),
        getStatus(),
      ]);
      setCases(caseList.items);
      setCaseLimit(caseList.limit);
      setStatus(serviceStatus);
    } catch (err) {
      setError(formatError(err));
    }
  }, [actor, activeRole]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function runTriage(caseId: string) {
    setBusy(caseId);
    try {
      await triageCase(caseId, identity);
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
        identity,
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
      const result = await getAudit(caseId, identity);
      setAudit({ caseId, events: result.items, limit: result.limit });
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
            <span>User</span>
            <select
              value={actor}
              onChange={(event) => changeActor(event.target.value as ActorKey)}
            >
              {Object.entries(ACTORS).map(([key, item]) => (
                <option key={key} value={key}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Active role</span>
            <select
              value={activeRole}
              onChange={(event) =>
                setActiveRole(event.target.value as ActingRole)
              }
            >
              {ACTORS[actor].roles.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </label>
          <button className="secondary" onClick={() => void refresh()}>
            Refresh
          </button>
        </div>
      </header>
      <p className="role-hint">
        <strong>{ACTORS[actor].label}</strong> acting as{" "}
        <strong>{activeRole}</strong>. {roleHelp(actor, activeRole)}
      </p>

      <section className="intro card" aria-label="How it works">
        <div className="intro-step">
          <span>1</span>
          <div>
            <strong>Triage</strong>
            <p>
              An analyst requests triage. The agent reads payment, account and
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
              retries wait for a different human approver. The agent has no
              execute tool.
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
          Choose a <strong>user</strong> and an <strong>active role</strong>.
          Alice and Bob each hold both analyst and approver roles; Carol is
          analyst-only; Dave is approver-only. Use <strong>Details</strong> for
          the rationale, evidence, assignee and triage requester, and{" "}
          <strong>Audit</strong> for the trail.
        </p>
        <p>
          <strong>Carol (analyst)</strong> triage <code>CASE-1009</code> (Carol's
          case); switch to <strong>Dave (approver)</strong> and approve it. Carol
          has no approval actions; Dave has no Triage button.
        </p>
        <p>
          <strong>Bob (analyst)</strong> triage <code>CASE-1010</code> (Bob's
          case); switch to <strong>Alice (approver)</strong> and approve it.
          Alice and Bob can approve each other's cases.
        </p>
        <p>
          <strong>Alice (analyst)</strong> triage <code>CASE-1002</code>; switch
          to <strong>Alice (approver)</strong> and try to approve: the service
          denies it because she owns and requested the case. Switch to{" "}
          <strong>Bob (approver)</strong> and approve it.
        </p>
        <p>
          <strong>Carol (analyst)</strong> triage <code>CASE-1006</code> (Dave's
          duplicate) and <code>CASE-1007</code> (Dave's dispute); switch to{" "}
          <strong>Bob (approver)</strong> and approve both. Dave owns them but
          cannot triage (approver-only) or approve them (assignee).{" "}
          <strong>Bob (analyst)</strong> can triage <code>CASE-1003</code>{" "}
          (Dave's case) to see a hold auto-execute.
        </p>
        <p className="hint">
          The analyst who requests triage cannot approve it. The service returns{" "}
          <code>TRIAGE_REQUESTER_CANNOT_APPROVE</code> (and{" "}
          <code>SELF_APPROVAL_DENIED</code> when they also own the case).
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
              <td>Alice (analyst + approver)</td>
              <td>View; request triage; approve other people's cases</td>
              <td>Approve a case she created or requested triage for</td>
            </tr>
            <tr>
              <td>Bob (analyst + approver)</td>
              <td>View; request triage; approve other people's cases</td>
              <td>Approve a case he created or requested triage for</td>
            </tr>
            <tr>
              <td>Carol (analyst)</td>
              <td>View; request triage</td>
              <td>Approve</td>
            </tr>
            <tr>
              <td>Dave (approver)</td>
              <td>View; approve or reject pending actions</td>
              <td>Triage</td>
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
            case assignee/creator or the analyst who requested triage.
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
              <th>Owner</th>
              <th>Scenario</th>
              <th>Amount</th>
              <th>Expected</th>
            </tr>
          </thead>
          <tbody>
            {SAMPLE_CASES.map((item) => (
              <tr key={item.id}>
                <td className="mono">{item.id}</td>
                <td>{CASE_OWNERS[item.id]}</td>
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
                    {item.status === "OPEN" && activeRole === "ANALYST" && (
                      <button
                        onClick={() => void runTriage(item.id)}
                        disabled={busy === item.id}
                        title="The analyst requests triage; the agent gathers evidence and proposes; it cannot execute."
                      >
                        {busy === item.id ? "Triaging…" : "Triage"}
                      </button>
                    )}
                    {item.status === "PENDING_APPROVAL" &&
                      activeRole === "APPROVER" && (
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
        {cases.length >= caseLimit && (
          <p className="pager-note">
            Showing the first {caseLimit} cases. Use the API with offset to page
            further.
          </p>
        )}
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
          error={error}
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
            <span>Triage requested by</span>
            <strong className="mono">{item.triage_requested_by ?? "—"}</strong>
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
              <strong>{proposal.policy_outcome}</strong> ·{" "}
              {policyLabel(proposal.policy_outcome)}
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
  error,
  onCancel,
  onConfirm,
}: {
  pending: PendingApproval;
  rationale: string;
  setRationale: (value: string) => void;
  busy: string | null;
  error: string | null;
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
        {error && (
          <div className="error" role="alert">
            <strong>{error}</strong>
          </div>
        )}
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
                {event.actor} ({event.actor_role}) · {event.created_at}
              </span>
              {event.reason_codes.length > 0 && (
                <span className="reasons">
                  {event.reason_codes.join(", ")}
                </span>
              )}
            </li>
          ))}
        </ol>
        {audit.events.length >= audit.limit && (
          <p className="muted">
            Showing the first {audit.limit} events. Use the API with offset to
            page further.
          </p>
        )}
        <div className="modal-actions">
          <button className="secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
