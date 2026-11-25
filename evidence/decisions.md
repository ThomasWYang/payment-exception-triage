# Decisions

## ADR-1: Deterministic planner behind a protocol, no LLM runtime

**Context:** The exercise allows a non-AI product; the AI-agent requirement is about the development process. A real model would add keys, latency and non-determinism.

**Decision:** Implement a deterministic `Planner` protocol for the slice. The planner can only read evidence and propose.

**Consequences:** Reproducible tests and demos; no keys. A real LLM/agent can replace the planner later without changing the tool gateway, policy, approval or audit.

## ADR-2: The agent has no execute tool

**Context:** Prompt-level restrictions are not a security boundary.

**Decision:** Expose only read-only tools to the planner. Execution is a service-side workflow after policy and, where required, human approval.

**Consequences:** The agent cannot move money; the service enforces the boundary. The API and UI show the denials. The gateway keeps the repository private; the deterministic planner is trusted code, and an untrusted/LLM planner should run out of process.

## ADR-3: Tiered approval with separation of duties

**Context:** Not every action needs a human, but financial actions do.

**Decision:** Low-risk retries auto-approve; refunds and higher-risk retries require an approver who is not the case assignee.

**Consequences:** Faster low-risk handling; stronger control on money movement. One approval level only in this slice. Execution is at-least-once at the workflow boundary; the executor receives the proposal ID as a durable idempotency key and must dedupe. The mock is pure; production would use an outbox for exactly-once semantics.

## ADR-4: SQLite and append-only audit for the local slice

**Context:** Zero-setup local run is a hard constraint.

**Decision:** Use embedded SQLite with constraints, WAL, foreign keys and audit triggers.

**Consequences:** No external service; real enforcement. `recursive_triggers` is enabled so INSERT OR REPLACE cannot bypass the append-only triggers. Production would use Aurora PostgreSQL and an immutable audit store; migrations would be managed separately.

## ADR-5: React UI is the primary demo surface; what we intentionally did not build

**Context:** The brief does not require a browser UI, but the workflow — roles, policy gates, separation of duties, approval and audit — is much easier to test and understand through one. After the first review, the candidate asked to make the UI primary rather than optional and to make it self-explanatory without the README.

**Decision:** Make the React/Vite client in `ui/` the primary demo surface. Commit the built `dist/` so `uvicorn` serves `/ui/` with no Node install; Node 22+ is only needed to rebuild. The UI includes an on-screen guide for the three-step flow, roles, rules, sample cases and expected outcomes, plus case details/evidence/approval/audit. It reads the live auto-retry limit, refund cap and mode from `/status`. Do not build real payment rails, a real LLM, IdP/OAuth, multi-level approvals, multi-currency, Docker or cloud deployment.

**Consequences:** The demo is understandable and testable without reading the full README; the API remains the core and the security boundary. The UI uses synthetic demo keys, is not a security boundary, and has no browser automation. Node is only a rebuild-time prerequisite. Each omission is documented with its production path.
