# InvoiceOps implementation notes

## Current phase

The full invoice-processing workflow is implemented: intake, extraction,
deterministic validation, human review, approval-packet generation, and audit
logging.  Dev-mode mocks are available for both Gemini (extraction) and Notion
(operations interface) so the entire workflow can be exercised without
provider credentials.

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

Upload an invoice (dev mode returns mock extraction):

```bash
curl -X POST http://127.0.0.1:8000/invoices/upload \
  -F "file=@invoice.pdf;type=application/pdf"
```

## Verification

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m compileall -q app tests
.venv/bin/pytest -q
```

The `.env` file and invoice files under `storage/` are ignored by Git. Never place credentials, tokens, private documents, or real payment data in tracked files.
