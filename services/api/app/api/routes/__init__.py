"""路由聚合。

每个模块自带前缀语义（资源名），这里只做一件事：把它们的 router 按资源域拼成一个
``api_router``，由 :mod:`app.main` 统一挂到 ``/v1``。新增资源域时只改这一个文件，
``main.py`` 不需要知道具体有哪些路由。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    action_center,
    admin,
    audit,
    crm,
    customers,
    devices,
    expense_reports,
    expenses,
    fx,
    health,
    imports,
    integrations,
    llm,
    local_guide,
    memory,
    metrics,
    notifications,
    places,
    plans,
    policies,
    proactive,
    sync,
    trips,
    visa,
    visits,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(trips.router)
api_router.include_router(visits.router)
api_router.include_router(visa.router)
api_router.include_router(imports.router)
api_router.include_router(local_guide.router)
api_router.include_router(integrations.router)
api_router.include_router(audit.router)
api_router.include_router(admin.router)
api_router.include_router(action_center.router)
api_router.include_router(customers.router)
api_router.include_router(crm.router)
api_router.include_router(devices.router)
api_router.include_router(expenses.router)
api_router.include_router(expense_reports.router)
api_router.include_router(fx.router)
api_router.include_router(places.router)
api_router.include_router(plans.router)
api_router.include_router(policies.router)
api_router.include_router(proactive.router)
api_router.include_router(memory.router)
api_router.include_router(llm.router)
api_router.include_router(metrics.router)
api_router.include_router(notifications.router)
api_router.include_router(sync.router)

__all__ = ["api_router"]