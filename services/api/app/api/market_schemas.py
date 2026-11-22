"""Market Intelligence API request/response contracts (H1.2 foundation)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.schemas import ORMModel


class MarketWorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    objective: str | None = Field(default=None, max_length=4000)
    raw_input: str = Field(min_length=1, max_length=40000)
    settings: dict[str, Any] = Field(default_factory=dict)


class MarketWorkspaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    objective: str | None = Field(default=None, max_length=4000)
    settings: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_change(self) -> MarketWorkspaceUpdate:
        if not self.model_fields_set:
            raise ValueError("至少提供一个可修改字段")
        return self


class MarketWorkspaceRead(ORMModel):
    id: UUID
    organization_id: UUID
    name: str
    objective: str | None
    status: str
    owner_user_id: UUID | None
    current_context_version: int | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MarketContextCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_input: str = Field(min_length=1, max_length=40000)
    user_overrides: dict[str, Any] | None = None


class MarketContextRead(ORMModel):
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    version: int
    raw_input: str
    extracted_context: dict[str, Any]
    user_overrides: dict[str, Any]
    unknowns: dict[str, Any]
    provenance: dict[str, Any]
    extraction_status: str
    extraction_prompt_id: str | None
    extraction_prompt_version: str | None
    is_current: bool
    created_by_user_id: UUID | None
    created_at: datetime


class MarketResearchQuestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    rationale: str | None = Field(default=None, max_length=4000)
    priority: int = Field(default=3, ge=1, le=5)
    parent_question_id: UUID | None = None
    acceptance_criteria: dict[str, Any] = Field(default_factory=dict)


class MarketResearchQuestionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(default=None, min_length=1, max_length=4000)
    rationale: str | None = Field(default=None, max_length=4000)
    priority: int | None = Field(default=None, ge=1, le=5)
    acceptance_criteria: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_change(self) -> MarketResearchQuestionUpdate:
        if not self.model_fields_set:
            raise ValueError("至少提供一个可修改字段")
        return self


class MarketResearchQuestionRead(ORMModel):
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    parent_question_id: UUID | None
    question: str
    rationale: str | None
    source: str
    priority: int
    status: str
    acceptance_criteria: dict[str, Any]
    answer_summary: str | None
    blocked_reason: str | None
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class MarketQuestionDependencyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depends_on_question_id: UUID


class MarketQuestionDependencyRead(ORMModel):
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    question_id: UUID
    depends_on_question_id: UUID
    created_by_user_id: UUID | None
    created_at: datetime


class MarketQuestionDependencyDeleted(BaseModel):
    dependency_id: UUID
    deleted: bool


class MarketActivityEventRead(ORMModel):
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    actor_type: str
    actor_user_id: UUID | None
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
