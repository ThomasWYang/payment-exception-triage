# Decisions

## ADR-1: Deterministic planner behind a protocol, no LLM runtime

**Context:** The exercise allows a non-AI product; the AI-agent requirement is about the development process. A real model would add keys, latency and non-determinism.

**Decision:** Implement a deterministic `Planner` protocol for the slice. The planner can only read evidence and propose.

**Consequences:** Reproducible tests and demos; no keys. A real LLM/agent can replace the planner later without changing the tool gateway, policy, approval or audit.

## ADR-2: The agent has no execute tool

**Context:** Prompt-level restrictions are not a security boundary.

**Decision:** Expose only read-only tools to the planner. Execution is a service-side workflow after policy and, where required, human approval.

**Consequences:** The agent cannot move money; the service enforces the boundary. The UI and API show the denials.

## ADR-3: Tiered approval with separation of duties

**Context:** Not every action needs a human, but financial actions do.

**Decision:** Low-risk retries auto-approve; refunds and higher-risk retries require an approver who is not the case assignee.

**Consequences:** Faster low-risk handling; stronger control on money movement. One approval level only in this slice.

## ADR-4: SQLite and append-only audit for the local slice

**Context:** Zero-setup local run is a hard constraint.

**Decision:** Use embedded SQLite with constraints, WAL, foreign keys and audit triggers.

**Consequences:** No external service; real enforcement. Production would use Aurora PostgreSQL and an immutable audit store; migrations would be managed separately.

## ADR-5: What we intentionally did not build

**Context:** 3-hour timebox.

**Decision:** No real payment rails, real LLM, IdP/OAuth, multi-level approvals, multi-currency, SPA framework, Docker or cloud deployment.

**Consequences:** A small, verifiable slice; each omission is documented with its production path.
