"""单个拜访的操作（不挂在 /trips 路径下的那两个）。

``DELETE`` 返回 **200 + TripRead** 而不是 204：删掉一个拜访会递增 Trip 的版本号，
客户端需要立刻拿到新的版本号去更新本地乐观并发令牌（否则下一次激活计划会误报版本冲突）。
返回 204 的话客户端只能再发一次 GET——在弱网移动端这是白白多一次往返。

``POST /v1/visits/{visit_id}/records`` 是现场记录的直接写入路径。离线时客户端会先写本地
outbox，联网后既可以重试这个接口，也可以走 ``POST /v1/sync:push``；两条路径共用
:mod:`app.services.record_service` 的幂等与 LWW 判定，不会产生两套语义。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.routes.trips import trip_read
from app.api.schemas import (
    AppointmentRecoveryReassessmentCreate,
    AppointmentRecoveryReassessmentRead,
    ConflictRecordRead,
    MinutesDraftRead,
    MinutesDraftRequest,
    MinutesFollowUpRead,
    MinutesInsightRead,
    MinutesJobAcceptedRead,
    TripRead,
    VisitBriefingRead,
    VisitFollowUpDecisionCreate,
    VisitFollowUpFeedRead,
    VisitFollowUpRead,
    VisitFollowUpUpdate,
    VisitRead,
    VisitRecordCreate,
    VisitRecordRead,
    VisitUpdate,
)
from app.services import (
    appointment_recovery_resolution_service,
    job_service,
    minutes_service,
    record_service,
    visit_briefing_service,
    visit_follow_up_service,
    visit_service,
)

router = APIRouter(tags=["visits"])

VISIT_RECORDS_ENDPOINT = "POST /v1/visits/{visit_id}/records"
MINUTES_DRAFT_ENDPOINT = "POST /v1/visits/{visit_id}/minutes:draft"



VISIT_FOLLOW_UP_CREATE_ENDPOINT = "POST /v1/visits/{visit_id}/follow-ups"
VISIT_FOLLOW_UP_UPDATE_ENDPOINT = "PATCH /v1/visit-follow-ups/{follow_up_id}"
VISIT_FOLLOW_UP_REASSESS_ENDPOINT = "POST /v1/visit-follow-ups/{follow_up_id}:reassess"


@router.get(
    "/visits/{visit_id}/follow-ups",
    response_model=VisitFollowUpFeedRead,
    summary="读取拜访跟进项与尚待人工决策的来源草稿",
)
def get_visit_follow_ups(visit_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return visit_follow_up_service.list_follow_ups(
        session, context.organization_id, visit_id
    )


@router.post(
    "/visits/{visit_id}/follow-ups",
    response_model=VisitFollowUpRead,
    status_code=status.HTTP_201_CREATED,
    summary="人工确认、驳回或创建拜访跟进项",
)
def create_visit_follow_up(
    visit_id: UUID,
    payload: VisitFollowUpDecisionCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=VISIT_FOLLOW_UP_CREATE_ENDPOINT,
        key=idempotency_key,
        payload={"visit_id": str(visit_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = visit_follow_up_service.create_follow_up(
        session, context.organization_id, visit_id, payload,
        actor_user_id=context.user_id,
    )
    body = VisitFollowUpRead.model_validate(
        visit_follow_up_service.read_follow_up(session, context.organization_id, row)
    )
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.post(
    "/visit-follow-ups/{follow_up_id}:reassess",
    response_model=AppointmentRecoveryReassessmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="使用新航班证据重新评估已接受的预约恢复跟进项",
)
def reassess_visit_follow_up(
    follow_up_id: UUID,
    payload: AppointmentRecoveryReassessmentCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=VISIT_FOLLOW_UP_REASSESS_ENDPOINT,
        key=idempotency_key,
        payload={"follow_up_id": str(follow_up_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    result = appointment_recovery_resolution_service.reassess_follow_up(
        session,
        context.organization_id,
        follow_up_id,
        payload,
        actor_user_id=context.user_id,
    )
    body = AppointmentRecoveryReassessmentRead.model_validate(result)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.patch(
    "/visit-follow-ups/{follow_up_id}",
    response_model=VisitFollowUpRead,
    summary="更新拜访跟进项状态或内容",
)
def patch_visit_follow_up(
    follow_up_id: UUID,
    payload: VisitFollowUpUpdate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=VISIT_FOLLOW_UP_UPDATE_ENDPOINT,
        key=idempotency_key,
        payload={"follow_up_id": str(follow_up_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = visit_follow_up_service.update_follow_up(
        session, context.organization_id, follow_up_id, payload,
        actor_user_id=context.user_id,
    )
    body = VisitFollowUpRead.model_validate(
        visit_follow_up_service.read_follow_up(session, context.organization_id, row)
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=body)


def record_read(outcome: record_service.RecordOutcome) -> VisitRecordRead:
    record = outcome.record
    conflict = None
    if outcome.conflict is not None:
        conflict = ConflictRecordRead.model_validate(outcome.conflict)
    return VisitRecordRead(
        id=record.id,
        visit_id=record.visit_id,
        client_record_id=record.client_record_id,
        outcome=record.outcome,
        server_version=record.server_version,
        applied=record.applied,
        conflict=conflict,
        created_at=record.created_at,
    )


@router.get(
    "/visits/{visit_id}/briefing",
    response_model=VisitBriefingRead,
    summary="拜访前简报：客户、地点、目标、历史、跟进与记忆",
)
def get_visit_briefing(visit_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return visit_briefing_service.build_briefing(
        session, context.organization_id, visit_id
    )


@router.patch("/visits/{visit_id}", response_model=VisitRead, summary="更新拜访")
def update_visit(visit_id: UUID, payload: VisitUpdate, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_TRAVELER)
    visit = visit_service.update_visit(session, context.organization_id, visit_id, payload)
    return VisitRead.model_validate(visit)


@router.delete("/visits/{visit_id}", response_model=TripRead, summary="删除拜访")
def delete_visit(visit_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_TRAVELER)
    trip = visit_service.delete_visit(session, context.organization_id, visit_id)
    return trip_read(session, trip)


@router.post(
    "/visits/{visit_id}/records",
    response_model=VisitRecordRead,
    status_code=status.HTTP_201_CREATED,
    summary="提交现场拜访记录（LWW，幂等）",
)
def submit_visit_record(
    visit_id: UUID,
    payload: VisitRecordCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    """记录现场结果。``client_record_id`` 本身已是幂等键；``Idempotency-Key`` 再保证整次
    HTTP 重试不产生第二次副作用。两者都带时以 ``client_record_id`` 为准——它跨设备重启仍在。"""
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=VISIT_RECORDS_ENDPOINT,
        key=idempotency_key,
        payload={"visit_id": str(visit_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay

    outcome = record_service.submit_record(
        session,
        context.organization_id,
        visit_id,
        payload,
        actor_user_id=context.user_id,
    )
    return guard.finish(status_code=status.HTTP_201_CREATED, body=record_read(outcome))


@router.post(
    "/visits/{visit_id}/minutes:draft",
    response_model=MinutesDraftRead,
    status_code=status.HTTP_200_OK,
    summary="生成会议纪要与跟进任务草稿（不发送）",
)
def draft_meeting_minutes(
    visit_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    payload: MinutesDraftRequest | None = None,
):
    """基于最近一条生效的现场记录起草纪要。草稿写入 ``llm_invocations`` 并返回其 id，
    任何后续动作（发送、建任务）都必须由人确认后走业务接口。"""
    require_role(context, ROLE_TRAVELER)
    tone = payload.tone if payload is not None else "PROFESSIONAL"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=MINUTES_DRAFT_ENDPOINT,
        key=idempotency_key,
        payload={"visit_id": str(visit_id), "tone": tone},
    )
    if (replay := guard.begin()) is not None:
        return replay

    draft = minutes_service.draft_minutes(
        session, context.organization_id, visit_id, tone=tone
    )
    body = MinutesDraftRead(
        draft_id=draft.invocation_id,
        llm_invocation_id=draft.invocation_id,
        title=draft.title,
        summary=draft.summary,
        decisions=list(draft.decisions),
        follow_up_tasks=[MinutesFollowUpRead(**task) for task in draft.follow_up_tasks],
        insights=[
            MinutesInsightRead(
                kind=insight.kind,
                text=insight.text,
                requires_confirmation=insight.requires_confirmation,
            )
            for insight in draft.insights
        ],
        degraded=draft.degraded,
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=body)


@router.post(
    "/visits/{visit_id}/minutes:jobs",
    response_model=MinutesJobAcceptedRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="异步生成会议纪要草稿（返回 job_id 轮询）",
)
def create_minutes_job(
    visit_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/visits/{visit_id}/minutes:jobs",
        key=idempotency_key,
        payload={"visit_id": str(visit_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    visit = record_service.ensure_visit_exists(session, context.organization_id, visit_id)
    job = job_service.enqueue(
        session,
        context.organization_id,
        job_type=job_service.JOB_TYPE_DRAFT_MINUTES,
        payload={"visit_id": str(visit.id)},
        dedupe_key=f"draft_minutes:{visit.id}",
        created_by_user_id=context.user_id,
    )
    body = MinutesJobAcceptedRead(job_id=job.id, visit_id=visit.id, status=job.status)
    return guard.finish(status_code=status.HTTP_202_ACCEPTED, body=body)
