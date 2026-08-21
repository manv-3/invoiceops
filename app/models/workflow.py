"""Workflow states and the only valid state-transition boundary."""

from __future__ import annotations

from enum import StrEnum


class ProcessingStatus(StrEnum):
    RECEIVED = "Received"
    EXTRACTING = "Extracting"
    VALIDATING = "Validating"
    NEEDS_REVIEW = "Needs Review"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    ACTIONED = "Actioned"
    COMPLETED = "Completed"
    FAILED = "Failed"
    DUPLICATE = "Duplicate"


class HumanDecision(StrEnum):
    PENDING = "Pending"
    APPROVE = "Approve"
    REJECT = "Reject"
    OVERRIDE = "Override"


class WorkflowTransitionError(ValueError):
    """Raised when a workflow transition is not permitted."""


_TRANSITIONS: dict[ProcessingStatus, frozenset[ProcessingStatus]] = {
    ProcessingStatus.RECEIVED: frozenset({ProcessingStatus.EXTRACTING, ProcessingStatus.FAILED}),
    ProcessingStatus.EXTRACTING: frozenset({ProcessingStatus.VALIDATING, ProcessingStatus.FAILED}),
    ProcessingStatus.VALIDATING: frozenset(
        {
            ProcessingStatus.NEEDS_REVIEW,
            ProcessingStatus.DUPLICATE,
            ProcessingStatus.FAILED,
            ProcessingStatus.ACTIONED,
        }
    ),
    ProcessingStatus.NEEDS_REVIEW: frozenset(
        {
            ProcessingStatus.APPROVED,
            ProcessingStatus.REJECTED,
            ProcessingStatus.ACTIONED,
            ProcessingStatus.FAILED,
        }
    ),
    ProcessingStatus.APPROVED: frozenset({ProcessingStatus.ACTIONED, ProcessingStatus.FAILED}),
    ProcessingStatus.ACTIONED: frozenset({ProcessingStatus.COMPLETED, ProcessingStatus.FAILED}),
    ProcessingStatus.REJECTED: frozenset(),
    ProcessingStatus.COMPLETED: frozenset(),
    ProcessingStatus.FAILED: frozenset(),
    ProcessingStatus.DUPLICATE: frozenset(),
}


def transition_status(current: ProcessingStatus, target: ProcessingStatus) -> ProcessingStatus:
    """Return the target state when the centralized transition is valid."""

    if target not in _TRANSITIONS.get(current, frozenset()):
        raise WorkflowTransitionError(f"invalid workflow transition: {current} -> {target}")
    return target


def allowed_transitions(current: ProcessingStatus) -> frozenset[ProcessingStatus]:
    """Return the immutable set of states reachable from ``current``."""

    return _TRANSITIONS.get(current, frozenset())
