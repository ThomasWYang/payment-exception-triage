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

## ADR-5: Scope — optional React UI and what we intentionally did not build

**Context:** The 3-hour timebox and the brief's "no browser UI required" guidance; a visible workflow still helps the round-2 walkthrough.

**Decision:** Add a small optional React/Vite client in `ui/` that calls the JSON API; the API, demo and verify commands remain the core path. Do not build real payment rails, a real LLM, IdP/OAuth, multi-level approvals, multi-currency, Docker or cloud deployment.

**Consequences:** Better demo and role/separation-of-duties visibility; Node is an optional prerequisite. The API remains the security boundary; the UI uses synthetic demo keys. Each omission is documented with its production path.
