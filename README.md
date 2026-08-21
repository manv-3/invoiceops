# InvoiceOps

InvoiceOps automates invoice intake and exception handling while keeping important decisions under human control. FastAPI runs the workflow engine, AI extracts structured data from documents, Python applies deterministic financial checks, and Notion gives operations teams a place to review and approve work.

## The problem

Invoice processing often moves through a fragile chain of PDFs, email attachments, spreadsheets, and chat messages. Teams repeatedly:

- read invoice PDFs and scans;
- copy vendor, tax, and amount fields into another system;
- recompute totals and taxes;
- check for duplicate invoices;
- compare vendor and payment details;
- chase approvals; and
- reconstruct what happened when something goes wrong.

Extraction alone does not solve the operational problem. InvoiceOps connects intake, validation, review, approval, action generation, and audit logging in one controlled workflow.

## One complete workflow

```text
PDF or image invoice
        |
        v
FastAPI intake and safe document storage
        |
        v
Structured extraction with confidence
        |
        v
Deterministic validation and duplicate checks
        |
        +----------------------+----------------------+
        |                                             |
   Low-risk path                              Risk or uncertainty
        |                                             |
        v                                             v
Approval packet generated                    Needs Review in Notion
                                                      |
                                                      v
                                           Approve, Reject, or Override
                                                      |
                                                      v
                                           Exactly-once action processing
                                                      |
                                                      v
                                                   Run Log
```

Every meaningful stage has a durable status and an audit entry. An uncertain invoice stays visible for review instead of being silently processed.

## What the system handles

InvoiceOps accepts PDF, PNG, JPG, and JPEG documents. For each upload, it:

1. validates the file type and size;
2. stores the original document without trusting the uploaded filename;
3. creates a SHA-256 fingerprint for document-level duplicate detection;
4. extracts invoice fields into typed Pydantic models;
5. matches the invoice to a vendor;
6. runs deterministic validation rules;
7. creates or updates the relevant Notion records; and
8. routes the invoice to approval processing or human review.

Typical extracted fields include:

- vendor name and identifier;
- invoice number;
- invoice and due dates;
- currency;
- subtotal, tax, and total amounts;
- purchase-order reference;
- payment or bank details; and
- line items.

Unknown values remain `null`. The system does not guess missing financial data.

## AI extraction, deterministic decisions

Google GenAI is used to understand invoice documents and return structured values with confidence information. Python owns the decisions that affect workflow state or financial correctness.

Validation covers:

- missing required fields;
- subtotal, tax, and total mismatches;
- duplicate document fingerprints;
- duplicate vendor and invoice-number combinations;
- low extraction confidence;
- vendor mismatches;
- changed bank or payment details; and
- purchase-order mismatches when a purchase order is available.

Monetary values use `Decimal`. The model is never asked to decide whether invoice arithmetic is valid.

## Notion is the human operations interface

Notion is the human-facing workspace for Vendors, Invoices, review decisions, and the Run Log. The integration is isolated in one client module, uses environment variables for tokens and IDs, and follows the current Notion data-source API rather than deprecated database-query examples.

Reviewers can see the extracted values, validation result, risk flags, source-document reference, invoice amount, vendor information, and concise machine reasoning. Machine reasoning explains the triggered rule or missing evidence; it does not expose private chain-of-thought.

Reviewers can:

- approve an invoice;
- reject an invoice; or
- override a result with a required reviewer note.

Workflow transitions are centralized and validated. Original extracted values are preserved when a reviewer overrides them.

## Action and safety boundary

InvoiceOps does not perform real financial payments. After an invoice is automatically cleared or explicitly approved, it generates a payment-ready approval packet in `storage/approved/` and records its unique action ID in Notion.

Action processing is designed to be exactly once: the same approval cannot create a second action packet, and transient failures can be retried under controlled rules. A rejected or failed invoice remains visible with its status and Run Log entries.

## Audit trail

The Run Log records the important facts needed to understand each invoice:

- intake and document fingerprinting;
- extraction and confidence;
- vendor matching;
- validation findings;
- duplicate checks;
- review and approval decisions;
- overrides and reviewer notes;
- action-packet creation;
- failures and controlled retries; and
- completion state.

The log explains what happened, when it happened, and which rule or human decision moved the invoice forward.

## Architecture

```text
Invoice document
      |
      v
FastAPI application
  |       |        |
  |       |        +--> Local document and approval-packet storage
  |       +-----------> Google GenAI structured extraction
  +-------------------> Notion client: Vendors, Invoices, Run Log
      |
      v
Typed models, Decimal arithmetic, validation, and workflow state machine
```

The architecture is intentionally small: one FastAPI service, local storage for original documents and generated approval packets, a focused Google GenAI integration, and a focused Notion client. Notion remains the operations interface; it is not responsible for workflow logic.

## Product principles

- AI extracts; deterministic code validates.
- Humans decide when evidence is incomplete or risk is material.
- Duplicate actions must never execute twice.
- Original documents and extracted values remain traceable.
- Failed inputs remain visible.
- Secrets come from environment configuration and are never committed.
- The system generates approval packets, not payments.
- One complete, auditable workflow matters more than a broad collection of disconnected features.

## Technology

- Python
- FastAPI
- Pydantic
- `Decimal` for monetary values
- Google GenAI SDK for structured document extraction
- Notion API
- HTTPX
- Pytest

The implementation is being built incrementally. Provider access, credentials, live Notion workspaces, and generated approval packets are separate verification boundaries; local code or fixture checks do not by themselves prove provider, deployment, or production status.
