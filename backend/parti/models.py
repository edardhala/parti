"""Database model.

Privacy rules that the schema itself enforces:
- No audio, video or transcript columns exist. A contribution is a one-line
  summary (max 280 characters) plus a tag and a depth score.
- Depth is 0-100 and is evidence, never a grade.
- Grades are set only by people (``set_by_id`` is required), 0-10 in steps of 0.5.
- ``audit_events`` is append-only (a database trigger rejects UPDATE and DELETE;
  see the initial migration).
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parti.db import Base

SUMMARY_MAX = 280


def _enum(cls: type[enum.Enum], name: str) -> SAEnum:
    # Stored as varchar + CHECK so adding a value is a simple migration
    return SAEnum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CourseRole(enum.StrEnum):
    STUDENT = "student"
    PROFESSOR = "professor"
    TEACHING_ASSISTANT = "teaching_assistant"


STAFF_ROLES = frozenset({CourseRole.PROFESSOR, CourseRole.TEACHING_ASSISTANT})


class ContributionType(enum.StrEnum):
    QUESTION = "question"
    INSIGHT = "insight"
    BUILDS_ON_OTHERS = "builds_on_others"


class ContributionSource(enum.StrEnum):
    SPOKEN = "spoken"
    WRITTEN = "written"


class ParticipationBand(enum.StrEnum):
    DEVELOPING = "developing"  # 0-4.5
    SOLID = "solid"  # 5-7
    STRONG = "strong"  # 7.5-10


class FlagKind(enum.StrEnum):
    BORDERLINE = "borderline"


class FeedbackStatus(enum.StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"


# ---------------------------------------------------------------- people and structure


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    # The OIDC issuer whose logins belong to this institution
    sso_issuer: Mapped[str | None] = mapped_column(String(500), unique=True)
    created_at: Mapped[datetime] = _created()

    users: Mapped[list["User"]] = relationship(back_populates="institution")
    terms: Mapped[list["Term"]] = relationship(back_populates="institution")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("sso_issuer", "sso_subject", name="uq_users_sso_identity"),
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("institutions.id", ondelete="RESTRICT"))
    # Identity comes only from the university login (issuer + subject)
    sso_issuer: Mapped[str] = mapped_column(String(500))
    sso_subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(254))
    display_name: Mapped[str] = mapped_column(String(200))
    is_program_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = _created()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    institution: Mapped[Institution] = relationship(back_populates="users")
    enrolments: Mapped[list["Enrolment"]] = relationship(back_populates="user")


class Term(Base):
    __tablename__ = "terms"
    __table_args__ = (CheckConstraint("ends_on >= starts_on", name="ck_terms_dates"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    institution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("institutions.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(100))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    # Course data is deleted after this date (Phase 5 retention job)
    delete_data_after: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = _created()

    institution: Mapped[Institution] = relationship(back_populates="terms")
    courses: Mapped[list["Course"]] = relationship(back_populates="term")


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("term_id", "code", name="uq_courses_term_code"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("terms.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created()

    term: Mapped[Term] = relationship(back_populates="courses")
    enrolments: Mapped[list["Enrolment"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["ClassSession"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class Enrolment(Base):
    """A person's role in one course. Course roles are always scoped to a course."""

    __tablename__ = "enrolments"
    __table_args__ = (UniqueConstraint("course_id", "user_id", name="uq_enrolments_course_user"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[CourseRole] = mapped_column(_enum(CourseRole, "course_role"))
    created_at: Mapped[datetime] = _created()

    course: Mapped[Course] = relationship(back_populates="enrolments")
    user: Mapped[User] = relationship(back_populates="enrolments")


class ClassSession(Base):
    __tablename__ = "class_sessions"
    __table_args__ = (UniqueConstraint("course_id", "number", name="uq_sessions_course_number"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capture_source: Mapped[str] = mapped_column(String(32), default="zoom")
    created_at: Mapped[datetime] = _created()

    course: Mapped[Course] = relationship(back_populates="sessions")
    contributions: Mapped[list["Contribution"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    summary: Mapped["SessionSummary | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------- evidence


class Contribution(Base):
    __tablename__ = "contributions"
    __table_args__ = (
        CheckConstraint("depth BETWEEN 0 AND 100", name="ck_contributions_depth"),
        CheckConstraint(
            f"char_length(summary) BETWEEN 1 AND {SUMMARY_MAX}", name="ck_contributions_summary_len"
        ),
        CheckConstraint("position(chr(10) in summary) = 0", name="ck_contributions_summary_one_line"),
        Index("ix_contributions_session_student", "session_id", "student_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"))
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[ContributionType] = mapped_column(_enum(ContributionType, "contribution_type"))
    source: Mapped[ContributionSource] = mapped_column(
        _enum(ContributionSource, "contribution_source"), default=ContributionSource.SPOKEN
    )
    summary: Mapped[str] = mapped_column(String(SUMMARY_MAX))
    depth: Mapped[int] = mapped_column(SmallInteger)
    # Which model wrote the summary; null when a person wrote or edited it
    generated_by_model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[ClassSession] = relationship(back_populates="contributions")


class SessionSummary(Base):
    """AI-drafted, anonymized class summary. Must never contain student names."""

    __tablename__ = "session_summaries"

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("class_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    key_points: Mapped[list[str]] = mapped_column(JSON, default=list)
    open_questions: Mapped[list[str]] = mapped_column(JSON, default=list)
    suggested_reading: Mapped[str | None] = mapped_column(Text)
    generated_by_model: Mapped[str | None] = mapped_column(String(64))
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()

    session: Mapped[ClassSession] = relationship(back_populates="summary")


class Note(Base):
    """A professor's or TA's private note about a student in a session."""

    __tablename__ = "notes"
    __table_args__ = (CheckConstraint("char_length(body) BETWEEN 1 AND 2000", name="ck_notes_body_len"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"))
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Grade(Base):
    """A participation grade. Only ever set by a person; nothing in Parti suggests one."""

    __tablename__ = "grades"
    __table_args__ = (
        UniqueConstraint("session_id", "student_id", name="uq_grades_session_student"),
        CheckConstraint(
            "value >= 0 AND value <= 10 AND value * 2 = floor(value * 2)", name="ck_grades_value"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"))
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    value: Mapped[Decimal] = mapped_column(Numeric(3, 1))
    set_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    set_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Students never see the raw value before release (Phase 4)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudentFlag(Base):
    __tablename__ = "student_flags"
    __table_args__ = (UniqueConstraint("session_id", "student_id", "kind", name="uq_student_flags"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"))
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[FlagKind] = mapped_column(_enum(FlagKind, "flag_kind"))
    set_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = _created()


class Feedback(Base):
    """Mid-term feedback a professor sends to one student."""

    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    strengths: Mapped[list[str]] = mapped_column(JSON, default=list)
    improvement: Mapped[str] = mapped_column(Text)
    # Only from the professor's saved grade; null shows "not yet set"
    band: Mapped[ParticipationBand | None] = mapped_column(_enum(ParticipationBand, "participation_band"))
    status: Mapped[FeedbackStatus] = mapped_column(
        _enum(FeedbackStatus, "feedback_status"), default=FeedbackStatus.DRAFT
    )
    created_at: Mapped[datetime] = _created()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- consent, audit, pilot


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    __table_args__ = (Index("ix_consent_user_course", "user_id", "course_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    policy_version: Mapped[str] = mapped_column(String(32))
    consented_at: Mapped[datetime] = _created()
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Who viewed or changed what. Append-only; holds ids and actions, never content."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_course_at", "course_id", "at"),
        Index("ix_audit_events_actor_at", "actor_id", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = _created()
    # Plain ids, not foreign keys, so the log outlives the rows it describes
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    course_id: Mapped[uuid.UUID | None] = mapped_column()
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PilotSignup(Base):
    __tablename__ = "pilot_signups"
    __table_args__ = (Index("uq_pilot_signups_email_lower", text("lower(email)"), unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    school: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
