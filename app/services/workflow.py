"""Invoice status state machine.

Centralises and validates all status transitions so that no other module can
move an invoice into an illegal state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.invoice import InvoiceRecord, InvoiceStatus

# Allowed transitions: current_status -> {allowed next statuses}
_TRANSITIONS: dict[InvoiceStatus, set[InvoiceStatus]] = {
    InvoiceStatus.RECEIVED: {InvoiceStatus.EXTRACTING, InvoiceStatus.FAILED},
    InvoiceStatus.EXTRACTING: {InvoiceStatus.EXTRACTED, InvoiceStatus.FAILED},
    InvoiceStatus.EXTRACTED: {InvoiceStatus.VALIDATING, InvoiceStatus.FAILED},
    InvoiceStatus.VALIDATING: {
        InvoiceStatus.VALIDATED,
        InvoiceStatus.NEEDS_REVIEW,
        InvoiceStatus.FAILED,
    },
    InvoiceStatus.VALIDATED: {InvoiceStatus.APPROVED, InvoiceStatus.NEEDS_REVIEW},
    InvoiceStatus.NEEDS_REVIEW: {
        InvoiceStatus.APPROVED,
        InvoiceStatus.REJECTED,
        InvoiceStatus.FAILED,
    },
    InvoiceStatus.APPROVED: {InvoiceStatus.ACTION_CREATED, InvoiceStatus.FAILED},
    InvoiceStatus.REJECTED: set(),
    InvoiceStatus.ACTION_CREATED: set(),
    InvoiceStatus.FAILED: set(),
}


class IllegalTransitionError(Exception):
    """Raised when a status transition is not allowed."""


def transition(record: InvoiceRecord, target: InvoiceStatus) -> InvoiceRecord:
    """Move *record* to *target* status, raising on illegal transitions.

    Returns a **new** record with the updated status and timestamp.
    """
    allowed = _TRANSITIONS.get(record.status, set())
    if target not in allowed:
        raise IllegalTransitionError(
            f"Cannot transition from {record.status.value!r} to {target.value!r}."
        )
    return record.model_copy(update={"status": target, "updated_at": datetime.now(UTC)})
