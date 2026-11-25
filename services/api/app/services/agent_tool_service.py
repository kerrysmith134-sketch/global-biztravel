"""Bounded, permission-checked execution for registered Agent tools.

The catalogue is descriptive metadata, not executable authority. A tool is executable only
when it has an explicit handler in EXECUTORS, passes its scope check, validates its arguments,
and does not require an approval workflow that has not been connected yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.api.deps import ROLE_RANK
from app.api.errors import ApiError, Conflict, Forbidden, Unprocessable
from app.llm.tools import list_tools
from app.services import plan_service, trip_service, visa_service
from app.services.agent_runtime_service import fail_agent_run, start_agent_run, succeed_agent_run


class TripGetPlanArguments(BaseModel):
    """Only the declared trip identifier is accepted; arbitrary tool context is rejected."""

    model_config = ConfigDict(extra="forbid")

    trip_id: UUID


class VisaPrepareChecklistArguments(BaseModel):
    """Create local draft cases only after an explicit Assistant card tap."""

    model_config = ConfigDict(extra="forbid")

    trip_id: UUID
    countries: list[str] | None = Field(default=None, max_length=20)


@dataclass(frozen=True)
class AgentToolResult:
    tool_name: str
    status: str
    trip_id: str
    trip_name: str
    detail: str
    plan_version_id: str | None = None
    version_number: int | None = None
    stop_count: int = 0
    day_count: int = 0
    visa_case_count: int = 0
    checklist_item_count: int = 0
    ready_item_count: int = 0
    missing_item_count: int = 0
    not_applicable_item_count: int = 0


def _execute_visa_get_checklist(
    session: Session,
    organization_id: UUID | str,
    arguments: TripGetPlanArguments,
    _actor_user_id: UUID | str | None = None,
) -> AgentToolResult:
    trip = trip_service.require_trip(session, organization_id, arguments.trip_id)
    cases = visa_service.list_cases(session, organization_id, trip.id)
    if not cases:
        return AgentToolResult(
            tool_name="visa.get_checklist",
            status="NO_VISA_CASES",
            trip_id=str(trip.id),
            trip_name=trip.name,
            detail=(
                f"「{trip.name}」尚未建立签证案件或材料清单。"
                "可以在行程详情中创建案件；本工具不会根据离线目录推断签证要求。"
            ),
        )

    total = 0
    ready = 0
    missing = 0
    not_applicable = 0
    country_summaries: list[str] = []
    for case in cases:
        items = case.checklist or []
        country_ready = 0
        country_missing = 0
        for item in items:
            status = str(item.get("status") or "MISSING").upper()
            total += 1
            if status == "READY":
                ready += 1
                country_ready += 1
            elif status == "NOT_APPLICABLE":
                not_applicable += 1
            else:
                missing += 1
                country_missing += 1
        country_summaries.append(
            f"{case.country_code}：案件 {case.status}，已备齐 {country_ready}/{len(items)}，"
            f"缺失 {country_missing} 项"
        )

    return AgentToolResult(
        tool_name="visa.get_checklist",
        status="CHECKLIST_LOADED",
        trip_id=str(trip.id),
        trip_name=trip.name,
        detail=(
            f"签证材料清单共 {len(cases)} 个国家案件、{total} 项："
            f"已备齐 {ready} 项、缺失 {missing} 项、不适用 {not_applicable} 项。"
            + "；".join(country_summaries)
            + "。这里只汇总已有案件记录，不代表签证资格或官方入境要求已核实。"
        ),
        visa_case_count=len(cases),
        checklist_item_count=total,
        ready_item_count=ready,
        missing_item_count=missing,
        not_applicable_item_count=not_applicable,
    )


def _execute_visa_prepare_checklist(
    session: Session,
    organization_id: UUID | str,
    arguments: VisaPrepareChecklistArguments,
    actor_user_id: UUID | str | None = None,
) -> AgentToolResult:
    trip = trip_service.require_trip(session, organization_id, arguments.trip_id)
    cases = visa_service.ensure_cases(
        session,
        organization_id,
        trip,
        countries=arguments.countries,
        actor_user_id=actor_user_id,
    )
    if not cases:
        return AgentToolResult(
            tool_name="visa.prepare_checklist",
            status="NO_DESTINATIONS",
            trip_id=str(trip.id),
            trip_name=trip.name,
            detail=(
                f"「{trip.name}」没有可建立签证案件的目的国。"
                "请先在行程中添加目的国；此操作不会提交签证申请。"
            ),
        )

    total = sum(len(case.checklist or []) for case in cases)
    ready = sum(
        1
        for case in cases
        for item in (case.checklist or [])
        if str(item.get("status") or "MISSING").upper() == "READY"
    )
    missing = sum(
        1
        for case in cases
        for item in (case.checklist or [])
        if str(item.get("status") or "MISSING").upper() == "MISSING"
    )
    not_applicable = sum(
        1
        for case in cases
        for item in (case.checklist or [])
        if str(item.get("status") or "MISSING").upper() == "NOT_APPLICABLE"
    )
    countries = ", ".join(case.country_code for case in cases)
    return AgentToolResult(
        tool_name="visa.prepare_checklist",
        status="VISA_CASES_PREPARED",
        trip_id=str(trip.id),
        trip_name=trip.name,
        detail=(
            f"已就绪 {len(cases)} 个目的国签证案件（{countries}），"
            f"包含 {total} 项材料清单：已备齐 {ready} 项、缺失 {missing} 项、"
            f"不适用 {not_applicable} 项。重复执行不会重复建单；这只是本地准备记录，"
            "不会提交申请或确认签证资格。"
        ),
        visa_case_count=len(cases),
        checklist_item_count=total,
        ready_item_count=ready,
        missing_item_count=missing,
        not_applicable_item_count=not_applicable,
    )


def _execute_trip_get_plan(
    session: Session,
    organization_id: UUID | str,
    arguments: TripGetPlanArguments,
    _actor_user_id: UUID | str | None = None,
) -> AgentToolResult:
    trip = trip_service.require_trip(session, organization_id, arguments.trip_id)
    if trip.active_plan_version_id is None:
        return AgentToolResult(
            tool_name="trip.get_plan",
            status="NO_ACTIVE_PLAN",
            trip_id=str(trip.id),
            trip_name=trip.name,
            detail=f"「{trip.name}」目前没有生效计划。生成计划并激活后即可查看完整路线。",
        )

    version = plan_service.require_plan_version(
        session, organization_id, trip.active_plan_version_id
    )
    if str(version.trip_id) != str(trip.id) or version.status != "ACTIVE":
        raise Conflict(
            "出行当前生效计划引用不一致，无法安全读取",
            details={"trip_id": str(trip.id), "plan_version_id": str(version.id)},
        )

    detail = plan_service.plan_version_detail(session, organization_id, version)
    stop_count = detail.stop_count
    day_count = detail.day_count
    leg_count = len(detail.legs)
    return AgentToolResult(
        tool_name="trip.get_plan",
        status="PLAN_LOADED",
        trip_id=str(trip.id),
        trip_name=trip.name,
        detail=(
            f"已读取第 {version.version_number} 版生效计划："
            f"{day_count} 天、{stop_count} 个停靠点、{leg_count} 段交通。"
            "打开行程详情可查看逐日时间表与地点。"
        ),
        plan_version_id=str(version.id),
        version_number=version.version_number,
        stop_count=stop_count,
        day_count=day_count,
    )


EXECUTORS = {
    "trip.get_plan": (_execute_trip_get_plan, TripGetPlanArguments),
    "visa.get_checklist": (_execute_visa_get_checklist, TripGetPlanArguments),
    "visa.prepare_checklist": (_execute_visa_prepare_checklist, VisaPrepareChecklistArguments),
}

SCOPE_MINIMUM_ROLES = {
    "read": "VIEWER",
    "write": "TRAVELER",
    "admin": "ADMIN",
}
SCOPE_RESOURCE_BY_TOOL = {
    "trip.get_plan": "trip",
    "visa.get_checklist": "trip",
    "visa.prepare_checklist": "trip",
}


def list_executable_tools() -> list[dict[str, Any]]:
    """Expose only tools that have a concrete handler and need no missing approval workflow."""
    return [
        dict(tool)
        for tool in list_tools()
        if tool["name"] in EXECUTORS and tool.get("requires_approval") is False
    ]


def execute_tool(
    session: Session,
    organization_id: UUID | str,
    *,
    actor_role: str | None,
    tool_name: str,
    arguments: dict[str, Any],
    actor_user_id: UUID | str | None = None,
) -> AgentToolResult:
    """Validate, authorize, execute and audit one bounded tool invocation."""

    catalog_entry = next((item for item in list_tools() if item["name"] == tool_name), None)
    if catalog_entry is None:
        raise Forbidden("该工具未登记，不能执行", details={"tool_name": tool_name})

    handler = EXECUTORS.get(tool_name)
    if handler is None:
        raise Forbidden(
            "该工具尚未接通可执行业务用例",
            details={"tool_name": tool_name},
        )
    if catalog_entry.get("requires_approval") is not False:
        raise Forbidden(
            "该工具必须先接入审批工作流，当前不能执行",
            details={"tool_name": tool_name},
        )

    scope = str(catalog_entry.get("scope") or "")
    try:
        resource, action = scope.split(":", maxsplit=1)
        expected_resource = SCOPE_RESOURCE_BY_TOOL.get(tool_name)
        if (
            not expected_resource
            or not resource.strip()
            or resource != expected_resource
            or action not in SCOPE_MINIMUM_ROLES
        ):
            raise ValueError
        minimum_role = SCOPE_MINIMUM_ROLES[action]
    except ValueError as exc:
        raise Forbidden(
            "工具权限范围配置无效",
            details={"tool_name": tool_name, "scope": scope},
        ) from exc

    role = (actor_role or "").upper()
    if role not in ROLE_RANK or ROLE_RANK[role] < ROLE_RANK[minimum_role]:
        raise Forbidden(
            "当前角色无权执行该工具",
            details={"tool_name": tool_name, "required_role": minimum_role, "actor_role": role},
        )

    executor, arguments_model = handler
    try:
        parsed_arguments = arguments_model.model_validate(arguments)
    except ValidationError as exc:
        details = [
            {"loc": list(item.get("loc", ())), "msg": item["msg"], "type": item["type"]}
            for item in exc.errors()
        ]
        raise Unprocessable("工具参数校验失败", details=details) from exc

    # Never persist free-form conversation text as tool input. This handler currently needs
    # exactly one stable entity reference, which is safe to retain in the run audit.
    audited_arguments: dict[str, Any] = {"trip_id": str(parsed_arguments.trip_id)}
    if (
        isinstance(parsed_arguments, VisaPrepareChecklistArguments)
        and parsed_arguments.countries is not None
    ):
        audited_arguments["countries"] = [
            str(country).strip().upper() for country in parsed_arguments.countries
        ]
    input_snapshot = {
        "tool_name": tool_name,
        "arguments": audited_arguments,
    }
    run = start_agent_run(
        session,
        organization_id,
        workflow_id=None,
        agent_type=f"tool:{tool_name}",
        input_snapshot=input_snapshot,
    )
    try:
        result = executor(session, organization_id, parsed_arguments, actor_user_id)
    except ApiError as exc:
        # Expected domain failures are durable AgentRun outcomes, not unhandled worker crashes.
        # The caller can commit the FAILED run and show the business-safe message in the Assistant.
        fail_agent_run(
            session,
            run,
            error=exc.message,
            output_snapshot={
                "status": "FAILED",
                "error_code": exc.code,
            },
        )
        label = (
            "签证材料清单"
            if tool_name in {"visa.get_checklist", "visa.prepare_checklist"}
            else "行程计划"
        )
        return AgentToolResult(
            tool_name=tool_name,
            status="FAILED",
            trip_id=str(parsed_arguments.trip_id),
            trip_name="",
            detail=f"无法读取{label}：{exc.message}",
        )

    succeed_agent_run(
        session,
        run,
        output_snapshot={
            "status": result.status,
            "trip_id": result.trip_id,
            "plan_version_id": result.plan_version_id,
            "version_number": result.version_number,
            "stop_count": result.stop_count,
            "day_count": result.day_count,
            "visa_case_count": result.visa_case_count,
            "checklist_item_count": result.checklist_item_count,
            "ready_item_count": result.ready_item_count,
            "missing_item_count": result.missing_item_count,
            "not_applicable_item_count": result.not_applicable_item_count,
        },
    )
    return result


__all__ = [
    "AgentToolResult",
    "TripGetPlanArguments",
    "VisaPrepareChecklistArguments",
    "execute_tool",
    "list_executable_tools",
]
