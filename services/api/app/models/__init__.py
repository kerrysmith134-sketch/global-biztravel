"""模型注册入口。

``env.py`` 与测试都靠这里的 import 副作用把全部模型注册进 ``Base.metadata``。
新增模型必须在此处导出，否则 ``alembic check`` 与模型/库一致性测试会漏检。
"""

from __future__ import annotations

from app.models.approval import ApprovalPolicy
from app.models.base import Base
from app.models.crm import CrmSyncJob
from app.models.devices import DeviceToken
from app.models.expense_reports import ExpenseReport, ExpenseReportItem
from app.models.expenses import TripExpense
from app.models.fx import FxRate
from app.models.geo import Customer, CustomerPlace, Place
from app.models.identity import Membership, Organization, User
from app.models.integrations import ExternalSignal
from app.models.llm import LLMInvocation
from app.models.memory import PlanAttemptLog, TravelerProfile, TripMemorySummary
from app.models.metrics import MetricSnapshot
from app.models.notifications import Notification
from app.models.planning import PlanStop, PlanTravelLeg, PlanVersion
from app.models.policies import TravelPolicy
from app.models.proactive import (
    Approval,
    ProactiveSuggestion,
    ProactiveTrigger,
    SuggestionAudit,
)
from app.models.records import VisitRecord
from app.models.reliability import (
    BackgroundJob,
    IdempotencyRecord,
    OutboxDelivery,
    OutboxEvent,
)
from app.models.trip import ImportBatch, ImportRow, Trip, Visit
from app.models.visa import EntryAssessment, VisaCase
from app.models.workflow import (
    AgentRun,
    Evidence,
    ExternalAction,
    IntegrationConnection,
    SyncOperation,
    WorkflowInstance,
)

__all__ = [
    "AgentRun",
    "Approval",
    "ApprovalPolicy",
    "BackgroundJob",
    "Base",
    "Customer",
    "CrmSyncJob",
    "CustomerPlace",
    "DeviceToken",
    "EntryAssessment",
    "Evidence",
    "ExpenseReport",
    "ExpenseReportItem",
    "FxRate",
    "ExternalAction",
    "IntegrationConnection",
    "ExternalSignal",
    "IdempotencyRecord",
    "ImportBatch",
    "ImportRow",
    "LLMInvocation",
    "Membership",
    "MetricSnapshot",
    "Notification",
    "Organization",
    "OutboxDelivery",
    "OutboxEvent",
    "Place",
    "PlanAttemptLog",
    "PlanStop",
    "PlanTravelLeg",
    "PlanVersion",
    "ProactiveSuggestion",
    "ProactiveTrigger",
    "SuggestionAudit",
    "SyncOperation",
    "TravelPolicy",
    "TravelerProfile",
    "Trip",
    "TripExpense",
    "TripMemorySummary",
    "User",
    "Visit",
    "VisaCase",
    "VisitRecord",
    "WorkflowInstance",
]