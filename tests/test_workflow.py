import pytest

from app.models.workflow import ProcessingStatus, WorkflowTransitionError, transition_status


def test_received_can_transition_to_extracting() -> None:
    assert (
        transition_status(ProcessingStatus.RECEIVED, ProcessingStatus.EXTRACTING)
        is ProcessingStatus.EXTRACTING
    )


def test_completed_cannot_transition_back_to_extracting() -> None:
    with pytest.raises(WorkflowTransitionError):
        transition_status(ProcessingStatus.COMPLETED, ProcessingStatus.EXTRACTING)
