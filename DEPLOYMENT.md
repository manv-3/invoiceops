# InvoiceOps deployment runbook

This repository contains the application and a `render.yaml` recipe. It does not claim that a
public service has already been deployed. A deployment is verified only after the health endpoint,
Notion diagnostic, Gemini extraction, and one approval packet have been observed from the deployed
runtime.

## Required production configuration

Set these variables in the deployment provider's secret/environment settings:

```text
APP_ENV=production
DEV_MODE=false
NOTION_TOKEN=<secret>
NOTION_API_VERSION=2026-03-11
NOTION_INVOICES_DATA_SOURCE_ID=<exact data_source_id>
NOTION_VENDORS_DATA_SOURCE_ID=<exact data_source_id>
NOTION_RUN_LOG_DATA_SOURCE_ID=<exact data_source_id>
GEMINI_API_KEY=<secret>
GEMINI_MODEL=gemini-3.6-flash
ENABLE_APPROVAL_POLLER=true
APPROVAL_POLL_SECONDS=10
AUTO_PROCESS_CLEAN_INVOICES=true
STORAGE_DIR=/opt/render/project/src/storage
MAX_UPLOAD_MB=15
```

If Telegram is enabled, also set:

```text
TELEGRAM_BOT_TOKEN=<secret>
TELEGRAM_WEBHOOK_SECRET=<random secret>
```

Attach persistent storage at `/opt/render/project/src/storage`. Without a persistent disk,
incoming source documents and approval packets are ephemeral and the service is not safe to use
for operations.

## Pre-deployment checks

Run locally from the repository root:

```bash
./scripts/verify.sh
```

The script runs tests, Ruff, format verification, bytecode compilation, and the live Notion
schema diagnostic. The diagnostic must end with `NOTION_SCHEMA_DIAGNOSTIC_OK`.

## Post-deployment checks

Replace `DEPLOYED_URL` with the actual service URL:

```bash
curl -fsS "$DEPLOYED_URL/health"
```

Upload a harmless invoice fixture and record the returned Invoice ID:

```bash
curl -fsS \
  -F 'file=@sample_invoices/invoice-fixture-review.pdf;type=application/pdf' \
  "$DEPLOYED_URL/webhooks/invoices"
```

Verify the Invoice row and Run Log in Notion, then approve the row in Notion. Confirm that:

1. the state changes to `Completed`;
2. `External Action Status` is `Success`;
3. `External Action ID` is present;
4. `Decision Processed At` is present; and
5. exactly one matching JSON packet exists on the persistent disk.

Do not interpret a successful `/health` response as proof that Notion, Gemini, Telegram, or
persistent storage is configured.

## Telegram webhook

After the deployed HTTPS URL is available, configure Telegram's webhook using the bot token in a
secret-aware shell:

```bash
curl -fsS -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=${DEPLOYED_URL}/webhooks/telegram" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

Send one real PDF/image to the bot and verify that the same Invoice and Run Log workflow is used.

## Release boundary

No real payments are performed. The generated packet is the controlled external-action boundary.
Deployment, GitHub push, Telegram webhook registration, and any public release remain explicit
external operations and must be verified separately from local tests.
