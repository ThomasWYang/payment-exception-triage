# Agentic Workflow

## Tool

Claude Code (Anthropic) as the primary AI coding agent.

## How the work was decomposed

1. **Requirements analysis**: read the invitation and brief; extract hard requirements and the submission checklist.
2. **Option comparison**: compare the initial proposal with two GPT-generated lists, deduplicate and rank; keep only projects that meet every requirement and are showcase-friendly.
3. **Plan review**: audit the plan against the brief twice; fix gaps before writing code.
4. **Implementation**: phased build with a commit per phase; subagents for tests, documentation and checklist auditing.
5. **Verification**: tests, golden-case eval, fresh-clone check, mutation check and a full code review.
6. **Code-review hardening**: triage the review findings, fix the valid ones with regression tests, and update the evidence.
7. **Post-review UI/README restructure**: make the UI the primary, self-documenting demo surface; restructure the README for business and technical readers; update the evidence.

## Representative steering prompts

- "Compare these two GPT recommendations with yours; rank all proposals; only keep top projects that meet all requirements and are friendly to showcase."
- "Re-read all requirements; review the plan; make sure nothing is missing before implementing."
- "Use subagents for tests, checklists and docs; keep progressive commits."
- "Majority backend, keep the UI thin."
- "If you can finish earlier and meet all requirements, you can create a simple UI with React/Angular separate from the backend API."
- "Use the code-review skill to review all code if you haven't done so."
- "Address all valid findings when the code-review agent comes back and update docs if necessary."
- "Restructure the README so both technical and business people understand the app: what each role does, the rules, and what each sample case does. Do not make the UI optional — it is the easiest way to test and understand the app. Improve the UI/UX so it is understandable without reading the full README."
- "How would a real system grant multiple roles and enforce four-eye? How could this app expand to use an LLM? Document the expandability."
- "Implement the `triage_requested_by` check. Do not oversimplify with one key per role; one user/identity should hold multiple roles. Give the API and UI at least three human users — two single-role and one dual-role — or two dual-role users who can approve each other; you decide."
- "Think about the scalability of the app. Make only necessary code changes; if it needs external components like Redis or a queue, document the gap and the improvement path."

## Where I intervened

- Rejected Docker for the 3-hour slice; documented the container/cloud path instead.
- Corrected the refund path: a failed payment cannot produce a legitimate refund; added duplicate-payment and customer-dispute cases.
- Replaced loose 90/95% eval thresholds with exact expected-outcome gates for a deterministic planner.
- Added payment-mismatch and action/exception compatibility guards.
- Added tool-call/time limits and fail-closed degradation.
- Kept the UI as a thin client; rejected a frontend framework until the backend was complete.
- Replaced `.env.example` with `env.example` after the environment blocked writing `.env*` files; the server uses environment variables with safe synthetic defaults.
- Caught and fixed a schema bug where an index referenced a non-existent `executions.case_id` column.
- A domain validator caught an impossible test fixture (`duplicate_of` on a failed payment); fixed the fixture, not the guard.
- A planner-timeout test used a value below the configured minimum; fixed the test, not the configuration.
- Fixed buffered output in `scripts/verify.py` so section headers appear before subprocess output.
- Upgraded the UI toolchain to Vite 8.3 / plugin-react 6.3 after `npm audit` flagged dev-only esbuild issues; rebuild and audit are clean.
- Added CORS and static serving for the separate React client; the API remains the security boundary.
- After the first review, made the UI the primary demo surface rather than optional: committed the built `ui/dist`, added an on-screen guide for the flow, roles, rules and sample cases, exposed the live policy caps on `/status`, gated Triage to the analyst role, and restructured the README business-first.
- Documented the production multi-role/four-eye model and the LLM planner expansion (design-only) without adding a model runtime to the slice.
- Implemented role sets and an explicit active role (`X-Acting-Role`), with one key per identity. The demo has four humans: Alice and Bob (analyst + approver), Carol (analyst-only) and Dave (approver-only), plus the agent service. The slice records `triage_requested_by` and denies the assignee or triage requester; Alice and Bob can approve each other's cases, Carol cannot approve and Dave cannot triage.
- Added the only local scalability guards: a paginated audit response (default 500, max 1000) and an `audit_events(event_type)` index for `/status` counts. Documented the production path instead of adding external components: managed relational DB, outbox/queue for execution, separate planner service, Redis/gateway rate limits, aggregated metrics and audit export.

## Code review and hardening

The code-review skill reviewed the full repository and returned **27 findings: 12 important, 15 nit**. I triaged each one and fixed the valid findings, adding regression tests:

- **Agent boundaries**: the gateway no longer exposes the repository publicly; tool attempts (including invalid/unknown calls) count toward the budget; args are revalidated against the tool schema; the planner runs under a real deadline.
- **Authorization**: the server sets the case assignee to the creator; clients cannot choose it. This closes a separation-of-duties bypass.
- **Policy**: degraded mode gates holds as well as retries/refunds; refunds require an active account; policy documents are validated at startup and mid-flight failures escalate.
- **Workflow reliability**: concurrent same-key approvals replay the stored result; invalid planner drafts escalate instead of returning 500; the approval rationale is redacted and truncated to the field limit; the executor receives the proposal ID as a durable idempotency key and its output is length-bounded.
- **Audit**: `recursive_triggers` blocks INSERT OR REPLACE as well as UPDATE/DELETE.
- **API/ops**: chunked requests are rejected; CORS is outermost and covers 413/500; `/readyz` probes the schema without creating a database; case reads use a read transaction; tool calls and failed request completions are logged.
- **UI**: one idempotency key per pending approval; approval actions are gated to approver roles.
- **Hygiene**: `env.example` documents all settings; type annotations were completed.

Deliberate dispositions (documented, not changed):

- Idempotency keys are globally scoped per deployment; clients should use UUIDs.
- Execution is at-least-once at the workflow boundary; the executor must dedupe on the proposal ID. Production would use an outbox for exactly-once semantics.
- The UI has no browser automation; it is checked by a TypeScript/Vite build, a server-side smoke test and an asset-serving test, and the API endpoints it calls are covered by API tests.

## How generated output is reviewed

- Every phase ends with a diff review and a commit.
- Tests are the oracle: a policy rule is not considered implemented until a failing test exists and passes.
- The golden-case eval gates regressions with exact expected outcomes and zero forbidden executions.
- Every code commit from the original build was re-run in an isolated worktree; the test count grew 26 → 31 → 41 → 55 → 86 → 95 → 96, then 121 after the UI/CORS and code-review hardening, 122 after the UI asset-serving test, 132 after multi-role identities, active roles and the triage-requester check, and 134 after audit pagination. All passed.
- A mutation check breaks a rule in a temporary copy and confirms tests fail.
- A fresh public clone was verified end-to-end: verify, demo, UI build and UI smoke (including the referenced assets).
- GitHub Actions is green, including the code-review fix commit and the UI-primary commit (`45588435963`).
- The UI/README restructure was verified by `npm run build`, `ruff` + `pytest`, a fresh clone and CI.
- The multi-role/triage-requester implementation was verified by `ruff` + `pytest` (132 passed), the 30-case golden eval, the demo (including cross-approval, single-role approval and the triage-requester denial), the UI build and CI.
- The scalability guards were verified by `ruff` + `pytest` (134 passed), the audit pagination API/repository tests, the UI build and CI; the production scaling path is documented in design/ADR-8/operate.
- Subagents (when used) produce drafts only; I review, run and accept or reject their output.

## Notes

Representative excerpts only; no raw transcripts.
