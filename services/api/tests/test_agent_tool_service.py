"""Unit tests for tool allow-list, scope, and explicit approval boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.errors import Forbidden, NotFound, Unprocessable
from app.services import agent_tool_service, assistant_service


def test_only_tools_with_explicit_handlers_are_executable() -> None:
    catalogue = {item["name"]: item for item in agent_tool_service.list_executable_tools()}
    assert list(catalogue) == ["trip.get_plan", "visa.get_checklist", "visa.prepare_checklist"]
    prepare_schema = catalogue["visa.prepare_checklist"]["input_schema"]
    assert prepare_schema["required"] == ["trip_id"]
    assert "countries" not in prepare_schema["required"]
    assert "countries" in prepare_schema["properties"]


@pytest.mark.parametrize(
    "tool_name",
    [
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


def test_visa_tool_without_cases_returns_an_explicit_empty_result(monkeypatch) -> None:
    trip_id = uuid4()
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
        ),
    )
    monkeypatch.setattr(agent_tool_service.visa_service, "list_cases", lambda *args: [])

    result = agent_tool_service.execute_tool(
        object(),  # type: ignore[arg-type]
        uuid4(),
        actor_role="VIEWER",
        tool_name="visa.get_checklist",
        arguments={"trip_id": str(trip_id)},
    )

    assert result.status == "NO_VISA_CASES"
    assert result.visa_case_count == 0
    assert "尚未建立签证案件" in result.detail
    assert "不会根据离线目录推断签证要求" in result.detail
    assert finished["output_snapshot"]["status"] == "NO_VISA_CASES"


def test_visa_prepare_requires_traveler_and_records_created_by_user(monkeypatch) -> None:
    trip_id = uuid4()
    actor_user_id = uuid4()
    run = SimpleNamespace()
    started = {}
    finished = {}
    cases = [
        SimpleNamespace(
            country_code="DE",
            checklist=[
                {"status": "MISSING"},
                {"status": "READY"},
                {"status": "MISSING"},
            ],
        ),
        SimpleNamespace(
            country_code="NL",
            checklist=[
                {"status": "MISSING"},
                {"status": "MISSING"},
            ],
        ),
    ]

    monkeypatch.setattr(
        agent_tool_service,
        "start_agent_run",
        lambda *args, **kwargs: started.update(kwargs) or run,
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
            name="Europe work trip",
            countries=["DE", "NL"],
        ),
    )
    ensure = {}
    monkeypatch.setattr(
        agent_tool_service.visa_service,
        "ensure_cases",
        lambda session, organization_id, trip, **kwargs: ensure.update(kwargs) or cases,
    )

    with pytest.raises(Forbidden):
        agent_tool_service.execute_tool(
            object(),  # type: ignore[arg-type]
            uuid4(),
            actor_role="VIEWER",
            tool_name="visa.prepare_checklist",
            arguments={"trip_id": str(trip_id)},
            actor_user_id=actor_user_id,
        )

    result = agent_tool_service.execute_tool(
        object(),  # type: ignore[arg-type]
        uuid4(),
        actor_role="TRAVELER",
        tool_name="visa.prepare_checklist",
        arguments={"trip_id": str(trip_id), "countries": ["de", "nl"]},
        actor_user_id=actor_user_id,
    )

    assert result.status == "VISA_CASES_PREPARED"
    assert result.visa_case_count == 2
    assert result.checklist_item_count == 5
    assert result.ready_item_count == 1
    assert result.missing_item_count == 4
    assert ensure["countries"] == ["de", "nl"]
    assert ensure["actor_user_id"] == actor_user_id
    assert started["input_snapshot"]["arguments"]["countries"] == ["DE", "NL"]
    assert finished["output_snapshot"]["status"] == "VISA_CASES_PREPARED"


def test_visa_prepare_without_destinations_does_not_create_cases(monkeypatch) -> None:
    trip_id = uuid4()
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
            name="Open trip",
            countries=[],
        ),
    )
    calls = {}
    monkeypatch.setattr(
        agent_tool_service.visa_service,
        "ensure_cases",
        lambda session, organization_id, trip, **kwargs: calls.update(kwargs) or [],
    )

    result = agent_tool_service.execute_tool(
        object(),  # type: ignore[arg-type]
        uuid4(),
        actor_role="TRAVELER",
        tool_name="visa.prepare_checklist",
        arguments={"trip_id": str(trip_id)},
        actor_user_id=uuid4(),
    )

    assert result.status == "NO_DESTINATIONS"
    assert result.visa_case_count == 0
    assert "没有可建立签证案件的目的国" in result.detail
    assert calls["countries"] is None
    assert finished["output_snapshot"]["status"] == "NO_DESTINATIONS"


def test_visa_tool_summarizes_only_persisted_checklist_items(monkeypatch) -> None:
    trip_id = uuid4()
    run = SimpleNamespace()
    finished = {}
    cases = [
        SimpleNamespace(
            country_code="DE",
            status="PREPARING",
            checklist=[
                {"key": "passport", "label": "护照", "status": "READY"},
                {"key": "invitation", "label": "邀请函", "status": "MISSING"},
                {"key": "photo", "label": "照片", "status": "NOT_APPLICABLE"},
            ],
        ),
        SimpleNamespace(
            country_code="NL",
            status="NEW",
            checklist=[
                {"key": "passport", "label": "护照", "status": "MISSING"},
                {"key": "insurance", "label": "保险", "status": "READY"},
            ],
        ),
    ]

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
            name="Europe work trip",
        ),
    )
    monkeypatch.setattr(
        agent_tool_service.visa_service,
        "list_cases",
        lambda session, organization_id, requested_trip_id: cases,
    )

    result = agent_tool_service.execute_tool(
        object(),  # type: ignore[arg-type]
        uuid4(),
        actor_role="VIEWER",
        tool_name="visa.get_checklist",
        arguments={"trip_id": str(trip_id)},
    )

    assert result.status == "CHECKLIST_LOADED"
    assert result.visa_case_count == 2
    assert result.checklist_item_count == 5
    assert result.ready_item_count == 2
    assert result.missing_item_count == 2
    assert result.not_applicable_item_count == 1
    assert "DE" in result.detail and "NL" in result.detail
    snapshot = finished["output_snapshot"]
    assert snapshot["status"] == "CHECKLIST_LOADED"
    assert snapshot["ready_item_count"] == 2
    assert snapshot["missing_item_count"] == 2


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



AVAILABLE_TOOLS = [
    {
        "name": "trip.get_plan",
        "description": "读取生效计划",
        "scope": "trip:read",
        "requires_approval": False,
    },
    {
        "name": "visa.get_checklist",
        "description": "读取已建立的签证清单",
        "scope": "trip:read",
        "requires_approval": False,
    },
    {
        "name": "visa.prepare_checklist",
        "description": "创建本地签证清单草稿",
        "scope": "trip:write",
        "requires_approval": False,
    },
]


def test_model_candidates_are_filtered_to_executable_tool_allowlist() -> None:
    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[],
        model_candidates=[
            "visa.get_checklist",
            "trip.delete",
            "os.system",
            123,
            {"name": "trip.get_plan"},
            "trip.get_plan",
            "trip.get_plan",
        ],
        available_tools=AVAILABLE_TOOLS,
        message="请核对出行安排",
    )

    assert [item["name"] for item in proposals] == [
        "visa.get_checklist",
        "trip.get_plan",
    ]


def test_model_cannot_suggest_write_tool_without_explicit_creation_intent() -> None:
    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[],
        model_candidates=["visa.prepare_checklist"],
        available_tools=AVAILABLE_TOOLS,
        message="请查看一下签证材料清单状态",
    )

    assert proposals == []


def test_model_can_suggest_write_tool_after_explicit_user_request() -> None:
    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[],
        model_candidates=["visa.prepare_checklist"],
        available_tools=AVAILABLE_TOOLS,
        message="请创建签证材料清单",
    )

    assert [item["name"] for item in proposals] == ["visa.prepare_checklist"]


@pytest.mark.parametrize(
    "message",
    [
        "请准备签证材料需要什么？",
        "准备签证材料有哪些要求？",
        "我要了解准备签证材料的流程。",
        "What visa documents should I prepare?",
    ],
)
def test_information_request_does_not_propose_checklist_creation(message: str) -> None:
    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[],
        model_candidates=["visa.prepare_checklist"],
        available_tools=AVAILABLE_TOOLS,
        message=message,
    )

    assert proposals == []

@pytest.mark.parametrize(
    "message",
    [
        "签证清单怎么创建？",
        "如何创建签证案件？",
        "创建签证材料清单有哪些步骤？",
        "How do I create a visa checklist?",
        "What are the requirements for a visa checklist?",
    ],
)
def test_how_to_question_does_not_propose_checklist_creation(message: str) -> None:
    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[],
        model_candidates=["visa.prepare_checklist"],
        available_tools=AVAILABLE_TOOLS,
        message=message,
    )

    assert proposals == []



@pytest.mark.parametrize(
    "message",
    [
        "请帮我准备签证材料清单",
        "准备签证清单",
        "请帮我创建签证案件并准备签证材料",
        "Prepare a visa checklist for Germany.",
    ],
)
def test_explicit_checklist_preparation_can_propose_write_tool(message: str) -> None:
    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[],
        model_candidates=["visa.prepare_checklist"],
        available_tools=AVAILABLE_TOOLS,
        message=message,
    )

    assert [item["name"] for item in proposals] == ["visa.prepare_checklist"]


@pytest.mark.parametrize(
    "message",
    [
        "不要创建签证材料清单，只查看现有清单",
        "Do not create a visa checklist; only show the existing one.",
    ],
)
def test_model_write_suggestion_is_rejected_when_user_negates_creation(message: str) -> None:
    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[],
        model_candidates=["visa.prepare_checklist"],
        available_tools=AVAILABLE_TOOLS,
        message=message,
    )

    assert proposals == []


def test_policy_and_model_proposals_are_merged_without_duplicates() -> None:
    policy = [
        {
            "name": "visa.get_checklist",
            "description": "读取已建立的签证清单",
            "scope": "trip:read",
            "requires_approval": False,
        }
    ]
    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=policy,
        model_candidates=["visa.get_checklist", "trip.get_plan", "unknown.tool"],
        available_tools=AVAILABLE_TOOLS,
        message="请查看行程计划和签证清单",
    )

    assert [item["name"] for item in proposals] == [
        "visa.get_checklist",
        "trip.get_plan",
    ]
    assert proposals[0] == policy[0]


def test_model_candidates_must_be_a_list() -> None:
    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[],
        model_candidates="trip.get_plan",
        available_tools=AVAILABLE_TOOLS,
        message="查看行程",
    )

    assert proposals == []


def test_write_intent_is_specific_to_the_requested_tool() -> None:
    available = AVAILABLE_TOOLS + [
        {
            "name": "expense_report.create",
            "description": "Create an expense report draft",
            "scope": "trip:write",
            "requires_approval": False,
        }
    ]

    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[],
        model_candidates=["expense_report.create"],
        available_tools=available,
        message="请创建签证材料清单",
    )

    assert proposals == []


def test_admin_scope_is_never_offered_as_an_assistant_tool() -> None:
    available = AVAILABLE_TOOLS + [
        {
            "name": "trip.admin_action",
            "description": "Administrative action",
            "scope": "trip:admin",
            "requires_approval": False,
        }
    ]

    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[{"name": "trip.admin_action"}],
        model_candidates=["trip.admin_action"],
        available_tools=available,
        message="请处理这个行程",
    )

    assert proposals == []


def test_policy_and_model_candidates_share_the_same_write_intent_gate() -> None:
    proposal = {"name": "visa.prepare_checklist"}
    denied = assistant_service._merge_tool_proposals(
        policy_proposals=[proposal],
        model_candidates=["visa.prepare_checklist"],
        available_tools=AVAILABLE_TOOLS,
        message="请查看签证材料清单",
    )
    allowed = assistant_service._merge_tool_proposals(
        policy_proposals=[proposal],
        model_candidates=["visa.prepare_checklist"],
        available_tools=AVAILABLE_TOOLS,
        message="请创建签证材料清单",
    )

    assert denied == []
    assert [item["name"] for item in allowed] == ["visa.prepare_checklist"]


def test_model_cannot_propose_any_tool_without_a_bound_trip_catalogue() -> None:
    proposals = assistant_service._merge_tool_proposals(
        policy_proposals=[],
        model_candidates=["trip.get_plan", "visa.get_checklist"],
        available_tools=[],
        message="查看行程和签证清单",
    )

    assert proposals == []


def test_assistant_schema_accepts_only_array_for_model_tool_suggestions() -> None:
    from app.llm.gateway import _validate
    from app.llm.prompts import ASSISTANT_SCHEMA

    valid = {
        "reply": "我可以读取已生效的计划。",
        "tool_suggestions": ["trip.get_plan"],
    }
    invalid = {
        "reply": "我可以读取已生效的计划。",
        "tool_suggestions": "trip.get_plan",
    }
    invalid_item = {
        "reply": "我可以读取已生效的计划。",
        "tool_suggestions": ["trip.get_plan", {"name": "trip.delete"}],
    }

    assert _validate(valid, ASSISTANT_SCHEMA) is None
    assert _validate(invalid, ASSISTANT_SCHEMA) == "字段 tool_suggestions 应为 array"
    assert _validate(invalid_item, ASSISTANT_SCHEMA) == (
        "字段 tool_suggestions[1] 应为 string"
    )
