# Payment Exception Triage UI

The primary demo surface for the local API. The built UI is committed in `dist/` and served by FastAPI at `/ui/`; no Node install is required to run it.

## Prerequisites

- Python 3.11+ (for the API)
- Node.js 22+ only if you want to develop or rebuild the UI

## Run the demo

From the repository root:

```bash
python scripts/seed.py --reset
python -m uvicorn payment_triage.main:app --reload
```

Open `http://127.0.0.1:8000/ui/`.

The screen includes:

- a three-step "how it works" guide;
- roles and rules panels;
- the sample cases and expected outcomes;
- case details (rationale, evidence, approval, execution);
- the append-only audit trail.

## Develop

```bash
cd ui
npm ci
npm run dev
```

Open `http://localhost:5173/ui/`.

## Rebuild the committed UI

```bash
cd ui
npm ci
npm run build
```

Commit the updated `dist/` so the API serves the new build without Node.

## Security note

The UI uses the synthetic demo API keys from the main README and is a demo client only. The API enforces every role, policy and state check. Production would use SSO and a backend-for-frontend, never service keys in the browser.
