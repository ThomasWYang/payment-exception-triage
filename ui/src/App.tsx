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

type PendingApproval = {
  caseId: string;
  decision: "APPROVE" | "REJECT";
  idempotencyKey: string;
};
type AuditState = { caseId: string; events: AuditEvent[] };

function statusClass(status: string): string {
  return `badge badge-${status.toLowerCase().replaceAll("_", "-")}`;
}

function formatMoney(cents: number | null): string {
  if (cents === null) {
    return "—";
  }
  return `$${(cents / 100).toFixed(2)}`;
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

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Payment Exception Triage</h1>
          <p className="subtitle">Synthetic demo · the API enforces every control</p>
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
                        {proposal.action}
                        {proposal.reason_codes.length > 0 && (
                          <span className="reasons">
                            {proposal.reason_codes.join(", ")}
                          </span>
                        )}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="actions">
                    {item.status === "OPEN" && (
                      <button
                        onClick={() => void runTriage(item.id)}
                        disabled={busy === item.id}
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

      {pending && (
        <div className="modal-backdrop" onClick={() => setPending(null)}>
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
              <button className="secondary" onClick={() => setPending(null)}>
                Cancel
              </button>
              <button
                onClick={() => void confirmApproval()}
                disabled={!rationale.trim() || busy === pending.caseId}
              >
                {busy === pending.caseId ? "Submitting…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}

      {audit && (
        <div className="modal-backdrop" onClick={() => setAudit(null)}>
          <div
            className="modal wide"
            onClick={(event) => event.stopPropagation()}
          >
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
              <button className="secondary" onClick={() => setAudit(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
