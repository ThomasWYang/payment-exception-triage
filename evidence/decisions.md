# Decisions

## ADR-1: Deterministic planner behind a protocol; the agent has no execute tool

**Context:** The exercise allows a non-AI product; the AI-agent requirement is about the development process. Prompt-level restrictions are not a security boundary. A real model would add keys, latency and non-determinism.

**Decision:** Implement a deterministic `Planner` protocol for the slice. The planner can only read evidence through the tool gateway and propose an action. Execution is a service-side workflow after policy and, where required, human approval.

**Consequences:** Reproducible tests and demos; no keys. The agent cannot move money; the service enforces the boundary and the API/UI show the denials. The gateway keeps the repository private; an untrusted/LLM planner should run out of process. A real LLM can replace the planner without changing the tool gateway, policy, approval or audit.

## ADR-2: Tiered approval, multi-role identities and four-eye

**Context:** Not every action needs a human, but financial actions do. Real staff can hold multiple roles; the slice models one key per identity with a role set and an explicit active role. Alice and Bob hold analyst + approver, Carol is analyst-only and Dave is approver-only. A user with both roles could otherwise create or triage a case and then approve it.

**Decision:** Low-risk retries auto-approve; refunds and higher-risk retries require an approver. Model roles as a set; require the active role to match the endpoint and record it. Use a separate service identity for the agent/scheduler with read-and-propose only. Record `triage_requested_by` and deny an approver who is the case assignee or the triage requester. No self-approval; if no independent approver is available, leave the case pending/escalated; a break-glass path needs a second approver or supervisor and a specific audit event. The slice covers the assignee and triage requester; production adds proposal editors and beneficiaries.

**Consequences:** Alice and Bob can approve each other's cases but not their own or a case they requested triage for; Carol cannot approve; Dave cannot triage. Triage is role-gated, not assignment-gated: any analyst or the agent service can request triage on any OPEN case; the assignee remains the owner and cannot approve, and the requester is recorded and excluded. The ten seeded cases are spread across all four humans (Alice 1002/1005/1008, Bob 1004/1010, Carol 1001/1009, Dave 1003/1006/1007) to make the combinations visible. Holding both roles is not itself a violation; the boundary is actor identity and case participation. The audit records the active role and actor. One approval level only in this slice; execution is at-least-once and the executor dedupes on the proposal ID. Production needs participant history, access reviews, separate service accounts, an approval queue and an outbox for exactly-once semantics.

## ADR-3: SQLite, append-only audit, scalability and CAP

**Context:** Zero-setup local run is a hard constraint. Triage and approval are write-heavy; the executor is a mock; the planner is deterministic; the slice is single-node.

**Decision:** Use embedded SQLite with constraints, WAL, foreign keys and audit triggers. Add only the audit pagination cap (default 500, max 1000) and the `audit_events(event_type)` index. Do not add Redis, a queue or a managed DB to the slice. In production, use a managed relational DB and an outbox + queue for execution, with a separate planner service, Redis/gateway rate limits, aggregated metrics and immutable audit export. Keep case/approval/execution CP (fail closed under a partition) and case lists/metrics/audit export AP.

**Consequences:** No external service; real enforcement. `recursive_triggers` blocks INSERT OR REPLACE from bypassing the append-only audit. The slice is single-writer and synchronous; the executor/planner boundaries are explicit. The API/DB contracts (idempotency, unique constraints, proposal ID, audit) carry over. Production needs load tests, connection pooling, migrations, retention and queue operations; approvals may be unavailable during a partition/failover, but no double execution.

## ADR-4: React UI is the primary demo surface

**Context:** The brief does not require a browser UI, but the workflow — roles, policy gates, separation of duties, approval and audit — is much easier to test and understand through one. After the first review, the candidate asked to make the UI primary rather than optional and to make it self-explanatory without the README.

**Decision:** Make the React/Vite client in `ui/` the primary demo surface. Commit the built `dist/` so `uvicorn` serves `/ui/` with no Node install; Node 22+ is only needed to rebuild. The UI includes a user/active-role selector (Alice/Bob dual-role, Carol analyst, Dave approver), a guide for the flow, roles, rules and sample cases, plus case details/evidence/triage requester/approval/audit. It reads the live auto-retry limit, refund cap and mode from `/status`. It is a demo client; the API remains the security boundary and production would use SSO + a BFF.

**Consequences:** The demo is understandable and testable without reading the full README; the API remains the core and the security boundary. The UI uses synthetic demo keys, is not a security boundary, and has no browser automation. Node is only a rebuild-time prerequisite.

## ADR-5: LLM expansion and what we intentionally did not build

**Context:** The slice uses a deterministic planner for reproducibility, offline operation and no keys. An LLM could improve evidence synthesis, exception classification and policy retrieval, but it must not gain execute or approval authority.

**Decision:** Keep the `Planner` protocol as the seam. An LLM planner runs out of process, uses the read-only tool gateway, returns a schema-validated proposal with evidence refs, and is wrapped by confidence/abstain and deterministic fallback. The policy engine, approval, separation of duties, idempotency, executor and audit remain service-side and model-agnostic. Roll out shadow → LLM-assisted with human approval → multi-step read-only agent, gated by evals, canary and a kill switch. Do not build real payment rails, a real LLM runtime, IdP/OAuth, multi-level approvals, multi-currency, Docker, cloud deployment, a managed DB, Redis or a queue.

**Consequences:** The slice stays deterministic and key-free; the controls do not change when the model changes. LLM-specific work: prompt-injection defenses, PII redaction, evidence grounding, model/prompt versioning, cost/latency budgets, adversarial/grounding evals, vendor/DPIA. The LLM never executes, approves or becomes the audit source. Each omission has a production path in `design.md`; the scalability/CAP sections cover the external components.
