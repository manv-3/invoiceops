"""Tests for the workflow state machine."""

import pytest

from app.models.invoice import InvoiceRecord, InvoiceStatus
from app.services.workflow import IllegalTransitionError, transition


def _make_record(
    status: InvoiceStatus = InvoiceStatus.RECEIVED,
) -> InvoiceRecord:
    return InvoiceRecord(
        original_filename="test.pdf",
        stored_path="storage/incoming/test.pdf",
        fingerprint="fp",
        status=status,
    )


def test_valid_transition() -> None:
    record = _make_record(InvoiceStatus.RECEIVED)
    updated = transition(record, InvoiceStatus.EXTRACTING)
    assert updated.status == InvoiceStatus.EXTRACTING


def test_illegal_transition_raises() -> None:
    record = _make_record(InvoiceStatus.RECEIVED)
    with pytest.raises(IllegalTransitionError):
        transition(record, InvoiceStatus.APPROVED)


def test_terminal_states_cannot_transition() -> None:
    for status in (
        InvoiceStatus.REJECTED,
        InvoiceStatus.ACTION_CREATED,
        InvoiceStatus.FAILED,
    ):
        record = _make_record(status)
        with pytest.raises(IllegalTransitionError):
            transition(record, InvoiceStatus.RECEIVED)


def test_transition_updates_timestamp() -> None:
    record = _make_record(InvoiceStatus.RECEIVED)
    updated = transition(record, InvoiceStatus.EXTRACTING)
    assert updated.updated_at >= record.updated_at


def test_full_happy_path() -> None:
    record = _make_record()
    record = transition(record, InvoiceStatus.EXTRACTING)
    record = transition(record, InvoiceStatus.EXTRACTED)
    record = transition(record, InvoiceStatus.VALIDATING)
    record = transition(record, InvoiceStatus.VALIDATED)
    record = transition(record, InvoiceStatus.APPROVED)
    record = transition(record, InvoiceStatus.ACTION_CREATED)
    assert record.status == InvoiceStatus.ACTION_CREATED


def test_review_path() -> None:
    record = _make_record()
    record = transition(record, InvoiceStatus.EXTRACTING)
    record = transition(record, InvoiceStatus.EXTRACTED)
    record = transition(record, InvoiceStatus.VALIDATING)
    record = transition(record, InvoiceStatus.NEEDS_REVIEW)
    record = transition(record, InvoiceStatus.APPROVED)
    record = transition(record, InvoiceStatus.ACTION_CREATED)
    assert record.status == InvoiceStatus.ACTION_CREATED


def test_rejection_path() -> None:
    record = _make_record()
    record = transition(record, InvoiceStatus.EXTRACTING)
    record = transition(record, InvoiceStatus.EXTRACTED)
    record = transition(record, InvoiceStatus.VALIDATING)
    record = transition(record, InvoiceStatus.NEEDS_REVIEW)
    record = transition(record, InvoiceStatus.REJECTED)
    assert record.status == InvoiceStatus.REJECTED
