"""设备 token 注册（APNs 推送前置）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import ROLE_TRAVELER, ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.schemas import DeviceTokenRead, DeviceTokenRegister
from app.models.devices import DeviceToken

router = APIRouter(tags=["devices"])


@router.post(
    "/devices",
    response_model=DeviceTokenRead,
    status_code=status.HTTP_201_CREATED,
    summary="注册/刷新当前用户的设备 token（同 token 幂等）",
)
def register_device(
    payload: DeviceTokenRegister, context: ContextDep, session: SessionDep
):
    require_role(context, ROLE_TRAVELER)
    now = session.scalar(select(func.clock_timestamp()))
    statement = (
        pg_insert(DeviceToken.__table__)
        .values(
            organization_id=str(context.organization_id),
            user_id=str(context.user_id),
            platform=payload.platform,
            token=payload.token,
            enabled=True,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_device_tokens_org_token",
            set_={
                "user_id": str(context.user_id),
                "platform": payload.platform,
                "enabled": True,
                "last_seen_at": now,
                "updated_at": func.now(),
            },
        )
        .returning(DeviceToken.__table__.c.id)
    )
    device_id = session.execute(statement).scalar_one()
    row = session.get(DeviceToken, device_id)
    return DeviceTokenRead.model_validate(row)


@router.get(
    "/devices",
    response_model=list[DeviceTokenRead],
    summary="当前用户的设备 token 列表",
)
def list_devices(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    rows = session.scalars(
        select(DeviceToken)
        .where(
            DeviceToken.organization_id == str(context.organization_id),
            DeviceToken.user_id == str(context.user_id),
        )
        .order_by(DeviceToken.created_at.desc())
    )
    return [DeviceTokenRead.model_validate(row) for row in rows]


@router.delete(
    "/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="注销设备 token",
)
def delete_device(device_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_TRAVELER)
    session.execute(
        delete(DeviceToken).where(
            DeviceToken.organization_id == str(context.organization_id),
            DeviceToken.user_id == str(context.user_id),
            DeviceToken.id == device_id,
        )
    )
