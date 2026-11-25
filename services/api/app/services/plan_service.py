"""计划版本的只读查询服务。

写路径（生成候选版本、激活、作废）分别住在 :mod:`app.services.planning_service`（求解结果的落库）
和后续的激活用例里；这里只提供"把已经落库的计划读出来"的读模型，供 API、Agent 工具与
测试复用，避免这些地方各写一份 SQL。

与 :mod:`app.services.planning_service` 的分工刻意划清：那个模块负责**把问题变成计划**，
本模块只负责**把计划读给人看**。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFound
from app.api.schemas import PlanStopRead, PlanTravelLegRead, PlanVersionDetailRead
from app.models.geo import Place
from app.models.trip import Visit
from app.models.planning import PlanStop, PlanTravelLeg, PlanVersion


def list_plan_versions(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> list[PlanVersion]:
    """列出一次出行的全部计划版本，**最新在前**（客户端默认展示最新一版）。"""
    return list(
        session.scalars(
            select(PlanVersion)
            .where(
                PlanVersion.organization_id == str(organization_id),
                PlanVersion.trip_id == str(trip_id),
            )
            .order_by(PlanVersion.version_number.desc())
        )
    )


def require_plan_version(
    session: Session, organization_id: UUID | str, plan_version_id: UUID | str
) -> PlanVersion:
    version = session.scalars(
        select(PlanVersion).where(
            PlanVersion.organization_id == str(organization_id),
            PlanVersion.id == str(plan_version_id),
        )
    ).one_or_none()
    if version is None:
        raise NotFound("计划版本不存在")
    return version


def list_plan_stops(
    session: Session, organization_id: UUID | str, plan_version_id: UUID | str
) -> list[PlanStop]:
    """按时间表顺序读出某个计划版本里的停靠。

    排序用 ``(day_index, sequence)`` 而不是 ``arrive_at``：序列号是计划自身的结构，
    时刻是它的物化结果，按结构排序能在时刻出现异常时仍然给出可解释的顺序。
    """
    return list(
        session.scalars(
            select(PlanStop)
            .where(
                PlanStop.organization_id == str(organization_id),
                PlanStop.plan_version_id == str(plan_version_id),
            )
            .order_by(PlanStop.day_index, PlanStop.sequence)
        )
    )


def list_plan_legs(
    session: Session, organization_id: UUID | str, plan_version_id: UUID | str
) -> list[PlanTravelLeg]:
    return list(
        session.scalars(
            select(PlanTravelLeg)
            .where(
                PlanTravelLeg.organization_id == str(organization_id),
                PlanTravelLeg.plan_version_id == str(plan_version_id),
            )
            .order_by(PlanTravelLeg.day_index, PlanTravelLeg.created_at)
        )
    )



def plan_version_detail(
    session: Session, organization_id, version
) -> PlanVersionDetailRead:
    detail = PlanVersionDetailRead.model_validate(version)
    # 逐条显式转换，而不是直接把 ORM 对象塞进 stops：Pydantic 校验已构造的同类实例时不会
    # 重新校验嵌套字段，ORM 对象会一路带到序列化阶段再炸。
    detail.stops = [
        PlanStopRead.model_validate(stop)
        for stop in plan_service.list_plan_stops(session, organization_id, version.id)
    ]
    visit_ids = [stop.visit_id for stop in detail.stops]
    if visit_ids:
        place_rows = session.execute(
            select(
                Visit.id,
                Visit.place_id,
                Place.name,
                Place.city,
                Place.country_code,
                Place.lat,
                Place.lon,
            )
            .join(Place, Place.id == Visit.place_id)
            .where(Visit.organization_id == str(organization_id), Visit.id.in_(visit_ids))
        ).all()
        place_by_visit = {row[0]: row for row in place_rows}
        for stop in detail.stops:
            info = place_by_visit.get(stop.visit_id)
            if info is not None:
                stop.place_id = info[1]
                stop.place_name = info[2]
                stop.city = info[3]
                stop.country_code = info[4]
                stop.latitude = info[5]
                stop.longitude = info[6]
    detail.legs = [
        PlanTravelLegRead.model_validate(leg)
        for leg in plan_service.list_plan_legs(session, organization_id, version.id)
    ]
    detail.stop_count = len(detail.stops)
    detail.day_count = len({stop.day_index for stop in detail.stops})
    return detail



def stop_stats(
    session: Session, organization_id: UUID | str, plan_version_ids: list[UUID]
) -> dict[UUID, tuple[int, int]]:
    """批量返回 ``plan_version_id -> (停靠数, 天数)``，供列表页避免 N+1。"""
    if not plan_version_ids:
        return {}
    rows = session.execute(
        select(
            PlanStop.plan_version_id,
            func.count(),
            func.count(func.distinct(PlanStop.day_index)),
        )
        .where(
            PlanStop.organization_id == str(organization_id),
            PlanStop.plan_version_id.in_([str(item) for item in plan_version_ids]),
        )
        .group_by(PlanStop.plan_version_id)
    ).all()
    return {row[0]: (int(row[1]), int(row[2])) for row in rows}


__all__ = [
    "list_plan_legs",
    "list_plan_stops",
    "list_plan_versions",
    "plan_version_detail",
    "require_plan_version",
    "stop_stats",
]