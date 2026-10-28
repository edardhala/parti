"""Request and response models for the API."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic.alias_generators import to_camel

from parti.models import CourseRole


class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    school: str = Field(min_length=1, max_length=200)
    email: EmailStr = Field(max_length=254)

    @field_validator("name", "school", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class OkOut(BaseModel):
    ok: bool = True


class SignupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    school: str
    email: str
    created_at: datetime
    updated_at: datetime


class EnrolmentOut(BaseModel):
    course_id: uuid.UUID
    course_code: str
    course_title: str
    term_name: str
    role: CourseRole


class MeOut(BaseModel):
    id: uuid.UUID
    display_name: str
    email: str
    institution_name: str
    is_program_admin: bool
    enrolments: list[EnrolmentOut]


class CourseOut(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    term_name: str
    term_starts_on: date
    term_ends_on: date
    my_role: CourseRole
    student_count: int
    session_count: int


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    at: datetime
    actor_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    course_id: uuid.UUID | None
    details: dict[str, Any]


class AuditPageOut(BaseModel):
    events: list[AuditEventOut]
    next_before_id: int | None


class ErrorOut(BaseModel):
    detail: str


class _PageOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SessionInfoOut(_PageOut):
    id: uuid.UUID
    course: str
    number: int
    date: str
    source: str


ContributionRow = tuple[str, str, int, str]


class StudentRowOut(_PageOut):
    id: uuid.UUID
    name: str
    grade: float | None
    borderline: bool
    last_contribution: str | None
    contributions: list[ContributionRow]


class NoteOut(_PageOut):
    id: uuid.UUID
    text: str
    created_at: datetime


class SummaryOut(_PageOut):
    points: list[str]
    questions: list[str]
    reading: str | None
    reviewed: bool


class DashboardOut(_PageOut):
    session: SessionInfoOut
    students: list[StudentRowOut]
    notes: dict[uuid.UUID, list[NoteOut]]
    summary: SummaryOut | None


class GradeIn(BaseModel):
    value: Decimal = Field(ge=0, le=10, multiple_of=0.5)


class GradeOut(BaseModel):
    value: float


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ReviewIn(BaseModel):
    reviewed: bool
