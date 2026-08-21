# InvoiceOps implementation notes

## Current phase

Phase 1 establishes the FastAPI application foundation, validated environment configuration, local storage directories, and the `/health` liveness endpoint.

No invoice processing, Gemini requests, Notion requests, approvals, or external actions are implemented yet. Provider credentials are intentionally optional until the corresponding integration phase.

## Local setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload
```

The service listens on `http://127.0.0.1:8000` by default. Check the foundation with:

```bash
curl http://127.0.0.1:8000/health
```

The expected response is:

```json
{
  "status": "ok",
  "service": "invoiceops",
  "version": "0.1.0"
}
```

## Verification

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m compileall -q app tests
.venv/bin/pytest -q
```

The `.env` file and invoice files under `storage/` are ignored by Git. Never place credentials, tokens, private documents, or real payment data in tracked files.
