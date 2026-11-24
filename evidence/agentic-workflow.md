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
- "Add a lightweight FE/UI; majority backend; keep it thin."
- "Use subagents for tests, checklists and docs; keep progressive commits."

## Where I intervened

- Rejected Docker for the 3-hour slice; documented the container/cloud path instead.
- Corrected the refund path: a failed payment cannot produce a legitimate refund; added duplicate-payment and customer-dispute exception types.
- Replaced loose 90/95% eval thresholds with exact expected-outcome gates for a deterministic planner.
- Added payment-mismatch and action/exception compatibility guards.
- Added tool-call/time limits and fail-closed degradation.
- Kept the UI as a thin client; rejected a frontend framework.
- Replaced `.env.example` with `env.example` after the environment blocked writing `.env*` files; the server uses environment variables with safe synthetic defaults.

## How generated output is reviewed

- Every phase ends with a diff review and a commit.
- Tests are the oracle: a policy rule is not considered implemented until a failing test exists and passes.
- The golden-case eval gates regressions.
- A mutation check breaks a rule in a temporary copy and confirms tests fail.
- A fresh-clone verification runs before submission.

## Notes

Representative excerpts only; no raw transcripts. The build interventions above are updated as the work proceeds.
