# InvoiceOps implementation notes

## Current phase

The local core workflow is implemented: FastAPI intake, safe document storage, SHA-256
fingerprinting, current Notion data-source reads and writes, Notion source-file attachment,
Gemini structured extraction, deterministic validation, vendor matching, centralized workflow
transitions, human approval polling, and exactly-once payment-ready packet generation.

The system does not perform real payments. `storage/approved/` is the external-action safety
boundary. Telegram support is an optional thin adapter; public deployment and live Telegram
operation remain separate verification boundaries.

The diagnostic uses `GET /v1/databases/{database_id}` and `GET /v1/data_sources/{data_source_id}` with
Notion API version `2026-03-11`. It does not create or update rows, schemas, relations, or sharing
permissions. Run it before changing the Notion schema:

```bash
.venv/bin/python -m app.services.notion_schema_diagnostic
```

The final line must be `NOTION_SCHEMA_DIAGNOSTIC_OK`.

## Local setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/uvicorn app.main:app --reload
```

Set the exact Notion data-source IDs and provider credentials in `.env`. Never use the IDs copied
from a Notion URL unless they are confirmed to be the data-source IDs by the diagnostic. The
approval worker starts only when `ENABLE_APPROVAL_POLLER=true` and all Notion IDs are configured.

The service listens on `http://127.0.0.1:8000` by default. Check the foundation with:

```bash
curl http://127.0.0.1:8000/health
```

Upload a document through the intake route:

```bash
curl -F 'file=@sample_invoices/invoice-fixture.pdf;type=application/pdf' \\
  http://127.0.0.1:8000/webhooks/invoices
```

The response contains the generated Invoice ID and current processing state. Read the durable
state with:

```bash
curl http://127.0.0.1:8000/api/invoices/INVOPS-.../status
```

Supported uploads are PDF, PNG, JPG, and JPEG. The original file is stored under
`storage/incoming/` with a generated name. When the Notion file-upload capability is available,
the same document is attached to the Invoice row's `Source File` property; a local filesystem
path is never presented as a reviewer-facing URL.

The optional Telegram adapter accepts a Telegram webhook at `/webhooks/telegram`. Set
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`, then configure Telegram's webhook to point to
the deployed endpoint. The adapter downloads the file and calls the same intake service as the
HTTP upload route.

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
.venv/bin/python -m app.services.notion_schema_diagnostic
```

For a local end-to-end provider check, use the tracked fixture only after confirming that the
configured Gemini and Notion credentials are safe to use. This creates real provider records and
an approval packet; it is not a unit test and the resulting Notion rows are not automatically
deleted.

`render.yaml` is a deployment recipe with a persistent disk for local document and packet
storage. It does not mean that the service has been deployed. Verify the deployed URL, provider
permissions, Telegram webhook, and persistent storage separately.

The schema diagnostic requires `NOTION_TOKEN` and all three exact `*_DATA_SOURCE_ID` values. It prints property names, property IDs, types, options, relation targets, and reverse-property names, then exits non-zero on missing fields, wrong types, wrong options, relation mismatches, or API/permission errors. The `.env` file and invoice files under `storage/` are ignored by Git. Never place credentials, tokens, private documents, or real payment data in tracked files.
