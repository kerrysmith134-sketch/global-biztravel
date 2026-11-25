"""Unit tests for tool allow-list, scope, and explicit approval boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.errors import Forbidden, NotFound, Unprocessable
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



def test_tool_scope_resource_must_match_registered_handler(monkeypatch) -> None:
    original = agent_tool_service.list_tools

    def mismatched_scope():
        entries = original()
        for entry in entries:
            if entry["name"] == "trip.get_plan":
                entry["scope"] = "place:read"
        return entries

    monkeypatch.setattr(agent_tool_service, "list_tools", mismatched_scope)
    with pytest.raises(Forbidden, match="权限范围配置无效"):
        agent_tool_service.execute_tool(
            object(),  # type: ignore[arg-type]
            uuid4(),
            actor_role="VIEWER",
            tool_name="trip.get_plan",
            arguments={"trip_id": str(uuid4())},
        )

def test_registered_handler_without_resource_mapping_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(agent_tool_service, "SCOPE_RESOURCE_BY_TOOL", {})
    with pytest.raises(Forbidden, match="权限范围配置无效"):
        agent_tool_service.execute_tool(
            object(),  # type: ignore[arg-type]
            uuid4(),
            actor_role="VIEWER",
            tool_name="trip.get_plan",
            arguments={"trip_id": str(uuid4())},
        )


def test_active_plan_tool_returns_real_version_summary(monkeypatch) -> None:
    trip_id = uuid4()
    plan_id = uuid4()
    run = SimpleNamespace()
    finished = {}

    monkeypatch.setattr(agent_tool_service, "start_agent_run", lambda *args, **kwargs: run)
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
            name="Berlin work trip",
            active_plan_version_id=plan_id,
        ),
    )
    monkeypatch.setattr(
        agent_tool_service.plan_service,
        "require_plan_version",
        lambda session, organization_id, version_id: SimpleNamespace(
            id=plan_id,
            trip_id=trip_id,
            status="ACTIVE",
            version_number=3,
        ),
    )
    monkeypatch.setattr(
        agent_tool_service.plan_service,
        "plan_version_detail",
        lambda session, organization_id, version: SimpleNamespace(
            stop_count=8,
            day_count=4,
            legs=[object(), object(), object()],
        ),
    )

    result = agent_tool_service.execute_tool(
        object(),  # type: ignore[arg-type]
        uuid4(),
        actor_role="VIEWER",
        tool_name="trip.get_plan",
        arguments={"trip_id": str(trip_id)},
    )

    assert result.status == "PLAN_LOADED"
    assert result.plan_version_id == str(plan_id)
    assert result.version_number == 3
    assert result.stop_count == 8
    assert result.day_count == 4
    assert "第 3 版生效计划" in result.detail
    assert finished["output_snapshot"]["status"] == "PLAN_LOADED"
    assert finished["output_snapshot"]["plan_version_id"] == str(plan_id)


def test_domain_read_error_is_persisted_as_a_failed_agent_run(monkeypatch) -> None:
    trip_id = uuid4()
    run = SimpleNamespace()
    failure = {}

    monkeypatch.setattr(agent_tool_service, "start_agent_run", lambda *args, **kwargs: run)
    monkeypatch.setattr(
        agent_tool_service,
        "fail_agent_run",
        lambda session, current_run, **kwargs: failure.update(kwargs),
    )

    def missing_trip(*args, **kwargs):
        raise NotFound("出行不存在")

    monkeypatch.setattr(agent_tool_service.trip_service, "require_trip", missing_trip)

    result = agent_tool_service.execute_tool(
        object(),  # type: ignore[arg-type]
        uuid4(),
        actor_role="VIEWER",
        tool_name="trip.get_plan",
        arguments={"trip_id": str(trip_id)},
    )

    assert result.status == "FAILED"
    assert result.trip_id == str(trip_id)
    assert "出行不存在" in result.detail
    assert failure["error"] == "出行不存在"
    assert failure["output_snapshot"]["status"] == "FAILED"
    assert failure["output_snapshot"]["error_code"] == "NOT_FOUND"


def test_assistant_tool_requires_conversation_owner(monkeypatch) -> None:
    from app.services import assistant_service

    trip_id = uuid4()
    owner_id = uuid4()
    conversation = SimpleNamespace(id=uuid4(), user_id=owner_id, trip_id=trip_id)
    monkeypatch.setattr(
        assistant_service,
        "get_conversation",
        lambda *args, **kwargs: conversation,
    )

    def unexpected_execute(*args, **kwargs):
        raise AssertionError("conversation ownership must be checked before tool execution")

    monkeypatch.setattr(assistant_service, "execute_tool", unexpected_execute)
    with pytest.raises(Forbidden):
        assistant_service.run_tool(
            object(),  # type: ignore[arg-type]
            uuid4(),
            conversation.id,
            "trip.get_plan",
            {"trip_id": str(trip_id)},
            actor_user_id=uuid4(),
            actor_role="OWNER",
        )
