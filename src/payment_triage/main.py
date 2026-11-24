"""Application entrypoint."""

from __future__ import annotations

from payment_triage.api import create_app

app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("payment_triage.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
