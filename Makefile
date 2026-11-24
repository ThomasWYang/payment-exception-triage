.PHONY: verify demo run

verify:
	python scripts/verify.py

demo:
	python scripts/demo.py

run:
	python -m uvicorn payment_triage.main:app --reload
