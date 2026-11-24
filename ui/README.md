# Payment Exception Triage UI

A small React/Vite client for the local API. It is a demo client only: the API enforces every role, policy and state check.

## Prerequisites

- Node.js 22+ and npm
- The API running locally

## Run the API

From the repository root:

```bash
python scripts/seed.py --reset
python -m uvicorn payment_triage.main:app --reload
```

## Run the UI in development

```bash
cd ui
npm install
npm run dev
```

Open `http://localhost:5173/ui/`.

The API base URL defaults to `http://127.0.0.1:8000`. Override it with `VITE_API_BASE_URL` if needed.

## Build and serve from FastAPI

```bash
cd ui
npm install
npm run build
```

The FastAPI app serves `ui/dist` at `/ui/` when that folder exists. Start the API and open `http://127.0.0.1:8000/ui/`.

## Security note

The UI uses the synthetic demo API keys from the main README. It is not part of the security boundary. Production would use SSO and a backend-for-frontend, never service keys in the browser.
