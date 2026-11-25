"""Unit tests for the shared AgentRun lifecycle contract."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.errors import Conflict, NotFound
from app.models.workflow import AgentRun
from app.services.agent_runtime_service import (
    AgentRunTransitionError,
    fail_agent_run,
    start_agent_run,
    succeed_agent_run,
)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def one_or_none(self):
        return self.value


class _Session:
    def __init__(self, result):
        self.result = result
        self.added = []

    def scalars(self, _statement):
        return _ScalarResult(self.result)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        return None


def _running_run() -> AgentRun:
    run = AgentRun(
        organization_id=uuid4(),
        agent_type="test_agent",
        status="RUNNING",
        input={"source_sha256": "abc123"},
    )
    run.id = uuid4()
    return run


def test_start_agent_run_is_scoped_to_an_active_workflow() -> None:
    organization_id = uuid4()
    workflow_id = uuid4()
    workflow = SimpleNamespace(
        id=workflow_id,
        organization_id=organization_id,
        status="RUNNING",
    )
    session = _Session(workflow)

    run = start_agent_run(
        session,  # type: ignore[arg-type]
        organization_id,
        workflow_id=workflow_id,
        agent_type="  research_planner ",
        input_snapshot={"request_sha256": "abc123"},
    )

    assert run in session.added
    assert run.organization_id == str(organization_id)
    assert run.workflow_id == str(workflow_id)
    assert run.agent_type == "research_planner"
    assert run.status == "RUNNING"
    assert run.input == {"request_sha256": "abc123"}


def test_start_agent_run_rejects_missing_or_terminal_parent_workflow() -> None:
    organization_id = uuid4()
    session = _Session(None)

    with pytest.raises(NotFound):
        start_agent_run(
            session,  # type: ignore[arg-type]
            organization_id,
            workflow_id=uuid4(),
            agent_type="research_planner",
            input_snapshot={},
        )

    session.result = SimpleNamespace(status="COMPLETED")
    with pytest.raises(Conflict):
        start_agent_run(
            session,  # type: ignore[arg-type]
            organization_id,
            workflow_id=uuid4(),
            agent_type="research_planner",
            input_snapshot={},
        )


def test_succeed_agent_run_records_output_and_aware_finish_time() -> None:
    run = _running_run()
    finished_at = datetime(2026, 11, 25, 10, 30, tzinfo=UTC)

    result = succeed_agent_run(
        _Session(run),  # type: ignore[arg-type]
        run,
        output_snapshot={"answer_count": 3},
        finished_at=finished_at,
    )

    assert result is run
    assert run.status == "SUCCEEDED"
    assert run.output == {"answer_count": 3}
    assert run.error is None
    assert run.finished_at == finished_at


def test_fail_agent_run_preserves_bounded_error_and_diagnostic() -> None:
    run = _running_run()

    fail_agent_run(
        _Session(run),  # type: ignore[arg-type]
        run,
        error="x" * 2400,
        output_snapshot={"status": "FAILED"},
    )

    assert run.status == "FAILED"
    assert run.error == "x" * 2000
    assert run.output == {"status": "FAILED"}
    assert run.finished_at is not None
    assert run.finished_at.tzinfo is not None


def test_agent_run_terminal_state_cannot_be_overwritten() -> None:
    run = _running_run()
    session = _Session(run)
    succeed_agent_run(session, run, output_snapshot={"ok": True})

    with pytest.raises(AgentRunTransitionError):
        fail_agent_run(session, run, error="late failure")

    with pytest.raises(AgentRunTransitionError):
        succeed_agent_run(session, run, output_snapshot={"ok": False})


def test_agent_run_rejects_naive_terminal_timestamp() -> None:
    run = _running_run()

    with pytest.raises(ValueError, match="timezone-aware"):
        succeed_agent_run(
            _Session(run),  # type: ignore[arg-type]
            run,
            output_snapshot={},
            finished_at=datetime(2026, 11, 25, 10, 30),
        )
