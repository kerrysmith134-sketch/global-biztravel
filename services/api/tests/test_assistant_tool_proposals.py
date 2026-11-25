"""Model-assisted Assistant tool proposal filtering."""

from __future__ import annotations

from app.services.assistant_service import _merge_tool_proposals


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
    proposals = _merge_tool_proposals(
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
    proposals = _merge_tool_proposals(
        policy_proposals=[],
        model_candidates=["visa.prepare_checklist"],
        available_tools=AVAILABLE_TOOLS,
        message="请查看一下签证材料清单状态",
    )

    assert proposals == []


def test_model_can_suggest_write_tool_after_explicit_user_request() -> None:
    proposals = _merge_tool_proposals(
        policy_proposals=[],
        model_candidates=["visa.prepare_checklist"],
        available_tools=AVAILABLE_TOOLS,
        message="请创建签证材料清单",
    )

    assert [item["name"] for item in proposals] == ["visa.prepare_checklist"]


def test_policy_and_model_proposals_are_merged_without_duplicates() -> None:
    policy = [
        {
            "name": "visa.get_checklist",
            "description": "读取已建立的签证清单",
            "scope": "trip:read",
            "requires_approval": False,
        }
    ]
    proposals = _merge_tool_proposals(
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
    proposals = _merge_tool_proposals(
        policy_proposals=[],
        model_candidates="trip.get_plan",
        available_tools=AVAILABLE_TOOLS,
        message="查看行程",
    )

    assert proposals == []


def test_model_cannot_propose_any_tool_without_a_bound_trip_catalogue() -> None:
    proposals = _merge_tool_proposals(
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

    assert _validate(valid, ASSISTANT_SCHEMA) is None
    assert _validate(invalid, ASSISTANT_SCHEMA) == "字段 tool_suggestions 应为 array"
