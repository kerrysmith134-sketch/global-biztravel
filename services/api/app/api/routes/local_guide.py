"""当地生活与文化指南（PRD 方向五）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.schemas import LocalGuideRead
from app.services import local_guide_service

router = APIRouter(tags=["local-guide"])


@router.get(
    "/trips/{trip_id}/local-guide",
    response_model=LocalGuideRead,
    summary="按出行国家与拜访城市组装的当地指南（离线目录）",
)
def get_local_guide(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return local_guide_service.build_guide(session, context.organization_id, trip_id)
