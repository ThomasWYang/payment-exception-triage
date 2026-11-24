# Agentic Workflow

## Tool

Claude Code (Anthropic) as the primary AI coding agent.

## How the work was decomposed

1. **Requirements analysis**: read the invitation and brief; extract hard requirements and the submission checklist.
2. **Option comparison**: compare the initial proposal with two GPT-generated lists, deduplicate and rank; keep only projects that meet every requirement and are showcase-friendly.
3. **Plan review**: audit the plan against the brief twice; fix gaps before writing code.
4. **Implementation**: phased build with a commit per phase; subagents for tests, documentation and checklist auditing.
5. **Verification**: tests, golden-case eval, fresh-clone check, manual red-team.

## Representative steering prompts

- "Compare these two GPT recommendations with yours; rank all proposals; only keep top projects that meet all requirements and are friendly to showcase."
- "Re-read all requirements; review the plan; make sure nothing is missing before implementing."
- "Use subagents for tests, checklists and docs; keep progressive commits."
- "Majority backend, keep the UI thin."
- "If you can finish earlier and meet all requirements, you can create a simple UI with React/Angular separate from the backend API."

## Where I intervened

- Rejected Docker for the 3-hour slice; documented the container/cloud path instead.
- Corrected the refund path: a failed payment cannot produce a legitimate refund; added duplicate-payment and customer-dispute exception types.
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

## How generated output is reviewed

- Every phase ends with a diff review and a commit.
- Tests are the oracle: a policy rule is not considered implemented until a failing test exists and passes.
- The golden-case eval gates regressions with exact expected outcomes and zero forbidden executions.
- A mutation check breaks a rule in a temporary copy and confirms tests fail.
- Every code commit was re-run in an isolated worktree; the test count grew 26 → 31 → 41 → 55 → 86 → 95 → 96, then 100 with the UI/CORS tests. All passed.
- The React UI is checked by a TypeScript + Vite build and a server-side smoke test; no browser automation.
- A fresh public clone was verified end-to-end: verify, demo, UI build and UI smoke.
- Mutation check: disabling the refund-cap rule in a temporary worktree made the policy test and golden eval fail as expected; the submitted history was unchanged.
- Subagents (when used) produce drafts only; I review, run and accept or reject their output.

## Notes

Representative excerpts only; no raw transcripts. The build interventions above are updated as the work proceeds.
