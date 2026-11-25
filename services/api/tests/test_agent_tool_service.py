"""Unit tests for tool allow-list, scope, and explicit approval boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.errors import Forbidden, Unprocessable
from app.services import agent_tool_service


def test_only_tools_with_explicit_handlers_are_executable() -> None:
    names = [item["name"] for item in agent_tool_service.list_executable_tools()]
    assert names == ["trip.get_plan"]


@pytest.mark.parametrize(
    "tool_name",
    [
        "visa.prepare_checklist",
        "place.prepare_geocoding",
        "plan.prepare_adjustment",
        "visit.draft_minutes",
    ],
)
def test_approval_gated_tools_are_not_executable(tool_name: str) -> None:
    with pytest.raises(Forbidden):
        agent_tool_service.execute_tool(
            object(),  # type: ignore[arg-type]
            uuid4(),
            actor_role="OWNER",
            tool_name=tool_name,
            arguments={},
        )


def test_unknown_tool_is_denied_by_default() -> None:
    with pytest.raises(Forbidden):
        agent_tool_service.execute_tool(
            object(),  # type: ignore[arg-type]
            uuid4(),
            actor_role="OWNER",
            tool_name="trip.delete",
            arguments={},
        )


def test_read_scope_is_checked_before_any_database_access() -> None:
    with pytest.raises(Forbidden):
        agent_tool_service.execute_tool(
            object(),  # type: ignore[arg-type]
            uuid4(),
            actor_role="UNKNOWN",
            tool_name="trip.get_plan",
            arguments={"trip_id": str(uuid4())},
        )


def test_tool_arguments_reject_unknown_fields_before_database_access() -> None:
    with pytest.raises(Unprocessable):
        agent_tool_service.execute_tool(
            object(),  # type: ignore[arg-type]
            uuid4(),
            actor_role="VIEWER",
            tool_name="trip.get_plan",
            arguments={"trip_id": str(uuid4()), "send_email": True},
        )


def test_no_active_plan_is_audited_as_an_explicit_empty_result(monkeypatch) -> None:
    trip_id = uuid4()
    run = SimpleNamespace()
    finished = {}

    monkeypatch.setattr(
        agent_tool_service,
        "start_agent_run",
        lambda *args, **kwargs: run,
    )
    monkeypatch.setattr(
        agent_tool_service,
        "succeed_agent_run",
        lambda session, current_run, **kwargs: finished.update(kwargs),
    )
    monkeypatch.setattr(
        agent_tool_service.trip_service,
        "require_trip",
        lambda session, organization_id, requested_id: SimpleNamespace(
            id=trip_id,
            name="Test trip",
            active_plan_version_id=None,
        ),
    )

    result = agent_tool_service.execute_tool(
        object(),  # type: ignore[arg-type]
        uuid4(),
        actor_role="VIEWER",
        tool_name="trip.get_plan",
        arguments={"trip_id": str(trip_id)},
    )

    assert result.status == "NO_ACTIVE_PLAN"
    assert result.trip_id == str(trip_id)
    assert "没有生效计划" in result.detail
    assert finished["output_snapshot"]["status"] == "NO_ACTIVE_PLAN"


def test_no_read_permission_means_no_tool_run_is_started(monkeypatch) -> None:
    def unexpected_start(*args, **kwargs):
        raise AssertionError("permission must be checked before starting AgentRun")

    monkeypatch.setattr(agent_tool_service, "start_agent_run", unexpected_start)
    with pytest.raises(Forbidden):
        agent_tool_service.execute_tool(
            object(),  # type: ignore[arg-type]
            uuid4(),
            actor_role="",
            tool_name="trip.get_plan",
            arguments={"trip_id": str(uuid4())},
        )
