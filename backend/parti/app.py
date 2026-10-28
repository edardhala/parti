import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from parti import audit, notify
from parti.config import Settings, get_settings
from parti.db import make_session_factory, session_scope
from parti.models import (
    STAFF_ROLES,
    ClassSession,
    Contribution,
    ContributionType,
    CourseRole,
    Enrolment,
    FlagKind,
    Grade,
    Note,
    PilotSignup,
    StudentFlag,
    User,
)
from parti.schemas import (
    DashboardOut,
    GradeIn,
    GradeOut,
    NoteIn,
    NoteOut,
    OkOut,
    ReviewIn,
    SessionInfoOut,
    SignupIn,
    StudentRowOut,
    SummaryOut,
)

STATIC = Path(__file__).parent / "static"
TYPE_CODES = {
    ContributionType.INSIGHT: "I",
    ContributionType.QUESTION: "Q",
    ContributionType.BUILDS_ON_OTHERS: "B",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def get_db(request: Request) -> Iterator[Session]:
    yield from session_scope(request.app.state.session_factory)


DB = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: DB) -> User:
    email = request.app.state.settings.demo_user_email
    if not email:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Sign-in isn't set up yet. For a local demo, set PARTI_DEMO_USER_EMAIL.",
        )
    user = db.scalar(select(User).where(func.lower(User.email) == email.lower(), User.is_active))
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "The demo user doesn't exist. Run: python -m parti.cli seed"
        )
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def _role(db: Session, course_id: uuid.UUID, user_id: uuid.UUID) -> CourseRole | None:
    return db.scalar(
        select(Enrolment.role).where(Enrolment.course_id == course_id, Enrolment.user_id == user_id)
    )


def _staff_session(db: Session, user: User, session_id: uuid.UUID) -> ClassSession:
    cs = db.get(ClassSession, session_id)
    if cs is None or _role(db, cs.course_id, user.id) not in STAFF_ROLES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.")
    return cs


def _require_student(db: Session, cs: ClassSession, student_id: uuid.UUID) -> None:
    if _role(db, cs.course_id, student_id) != CourseRole.STUDENT:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found in this course.")


def _date_label(value: datetime, tz: ZoneInfo) -> str:
    local = value.astimezone(tz)
    return f"{local.day} {local:%b %Y}"


def _dashboard(db: Session, cs: ClassSession, tz: ZoneInfo) -> DashboardOut:
    students = db.scalars(
        select(User)
        .join(Enrolment, Enrolment.user_id == User.id)
        .where(Enrolment.course_id == cs.course_id, Enrolment.role == CourseRole.STUDENT)
        .order_by(User.display_name)
    ).all()

    contributions: dict[uuid.UUID, list[tuple[str, str, int, str]]] = {}
    for c in db.scalars(
        select(Contribution).where(Contribution.session_id == cs.id).order_by(Contribution.occurred_at)
    ):
        row = (f"{c.occurred_at.astimezone(tz):%H:%M}", TYPE_CODES[c.type], c.depth, c.summary)
        contributions.setdefault(c.student_id, []).append(row)

    grades: dict[uuid.UUID, Decimal] = {
        sid: value
        for sid, value in db.execute(select(Grade.student_id, Grade.value).where(Grade.session_id == cs.id))
    }
    borderline = set(
        db.scalars(
            select(StudentFlag.student_id).where(
                StudentFlag.session_id == cs.id, StudentFlag.kind == FlagKind.BORDERLINE
            )
        )
    )
    last: dict[uuid.UUID, datetime] = {
        sid: at
        for sid, at in db.execute(
            select(Contribution.student_id, func.max(Contribution.occurred_at))
            .join(ClassSession, ClassSession.id == Contribution.session_id)
            .where(ClassSession.course_id == cs.course_id, ClassSession.starts_at <= cs.starts_at)
            .group_by(Contribution.student_id)
        )
    }

    notes: dict[uuid.UUID, list[NoteOut]] = {}
    for n in db.scalars(select(Note).where(Note.session_id == cs.id).order_by(Note.created_at)):
        notes.setdefault(n.student_id, []).append(NoteOut(id=n.id, text=n.body, created_at=n.created_at))

    summary = cs.summary
    return DashboardOut(
        session=SessionInfoOut(
            id=cs.id,
            course=cs.course.title,
            number=cs.number,
            date=_date_label(cs.starts_at, tz),
            source=cs.capture_source,
        ),
        students=[
            StudentRowOut(
                id=s.id,
                name=s.display_name,
                grade=float(grades[s.id]) if s.id in grades else None,
                borderline=s.id in borderline,
                last_contribution=_date_label(last[s.id], tz) if s.id in last else None,
                contributions=contributions.get(s.id, []),
            )
            for s in students
        ],
        notes=notes,
        summary=SummaryOut(
            points=summary.key_points,
            questions=summary.open_questions,
            reading=summary.suggested_reading,
            reviewed=summary.reviewed_at is not None,
        )
        if summary
        else None,
    )


router = APIRouter(prefix="/api")


@router.get("/dashboard", response_model=DashboardOut)
def latest_dashboard(request: Request, db: DB, user: CurrentUser) -> DashboardOut:
    cs = db.scalar(
        select(ClassSession)
        .join(Enrolment, Enrolment.course_id == ClassSession.course_id)
        .where(Enrolment.user_id == user.id, Enrolment.role.in_(STAFF_ROLES))
        .order_by(ClassSession.starts_at.desc())
        .limit(1)
    )
    if cs is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No sessions yet.")
    return _view_dashboard(request, db, user, cs)


@router.get("/sessions/{session_id}/dashboard", response_model=DashboardOut)
def session_dashboard(session_id: uuid.UUID, request: Request, db: DB, user: CurrentUser) -> DashboardOut:
    return _view_dashboard(request, db, user, _staff_session(db, user, session_id))


def _view_dashboard(request: Request, db: Session, user: User, cs: ClassSession) -> DashboardOut:
    out = _dashboard(db, cs, ZoneInfo(request.app.state.settings.display_timezone))
    audit.record(db, "dashboard.view", "class_session", cs.id, actor=user, course_id=cs.course_id)
    db.commit()
    return out


@router.put("/sessions/{session_id}/students/{student_id}/grade", response_model=GradeOut)
def set_grade(
    session_id: uuid.UUID, student_id: uuid.UUID, body: GradeIn, db: DB, user: CurrentUser
) -> GradeOut:
    cs = _staff_session(db, user, session_id)
    _require_student(db, cs, student_id)
    grade = db.scalar(select(Grade).where(Grade.session_id == cs.id, Grade.student_id == student_id))
    if grade is None:
        grade = Grade(session_id=cs.id, student_id=student_id, value=body.value, set_by_id=user.id)
        db.add(grade)
    else:
        grade.value = body.value
        grade.set_by_id = user.id
        grade.updated_at = datetime.now(UTC)
    db.flush()
    audit.record(db, "grade.set", "grade", grade.id, actor=user, course_id=cs.course_id)
    db.commit()
    return GradeOut(value=float(body.value))


@router.delete("/sessions/{session_id}/students/{student_id}/grade", status_code=status.HTTP_204_NO_CONTENT)
def clear_grade(session_id: uuid.UUID, student_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    cs = _staff_session(db, user, session_id)
    _require_student(db, cs, student_id)
    grade = db.scalar(select(Grade).where(Grade.session_id == cs.id, Grade.student_id == student_id))
    if grade is not None:
        audit.record(db, "grade.clear", "grade", grade.id, actor=user, course_id=cs.course_id)
        db.delete(grade)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/{session_id}/students/{student_id}/notes",
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
)
def add_note(
    session_id: uuid.UUID, student_id: uuid.UUID, body: NoteIn, db: DB, user: CurrentUser
) -> NoteOut:
    cs = _staff_session(db, user, session_id)
    _require_student(db, cs, student_id)
    note = Note(session_id=cs.id, student_id=student_id, author_id=user.id, body=body.body)
    db.add(note)
    db.flush()
    audit.record(db, "note.create", "note", note.id, actor=user, course_id=cs.course_id)
    db.commit()
    db.refresh(note)
    return NoteOut(id=note.id, text=note.body, created_at=note.created_at)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: uuid.UUID, db: DB, user: CurrentUser) -> Response:
    note = db.get(Note, note_id)
    if note is None or note.author_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found.")
    cs = _staff_session(db, user, note.session_id)
    audit.record(db, "note.delete", "note", note.id, actor=user, course_id=cs.course_id)
    db.delete(note)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/sessions/{session_id}/summary/review", response_model=OkOut)
def review_summary(session_id: uuid.UUID, body: ReviewIn, db: DB, user: CurrentUser) -> OkOut:
    cs = _staff_session(db, user, session_id)
    summary = cs.summary
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This session has no summary yet.")
    summary.reviewed_by_id = user.id if body.reviewed else None
    summary.reviewed_at = datetime.now(UTC) if body.reviewed else None
    audit.record(
        db,
        "summary.review",
        "session_summary",
        cs.id,
        actor=user,
        course_id=cs.course_id,
        reviewed=body.reviewed,
    )
    db.commit()
    return OkOut()


@router.post("/signups", response_model=OkOut, status_code=status.HTTP_201_CREATED)
def signup(body: SignupIn, request: Request, background: BackgroundTasks, db: DB) -> OkOut:
    email = str(body.email)
    row = db.scalar(select(PilotSignup).where(func.lower(PilotSignup.email) == email.lower()))
    updated = row is not None
    if row is None:
        row = PilotSignup(name=body.name, school=body.school, email=email)
        db.add(row)
    else:
        row.name, row.school, row.email = body.name, body.school, email
        row.updated_at = datetime.now(UTC)
    db.flush()
    audit.record(db, "signup.save", "pilot_signup", row.id)
    db.commit()

    message = notify.signup_message(request.app.state.settings, row, updated=updated)
    if message is not None:
        background.add_task(notify.send_safely, request.app.state.mailer, message)
    return OkOut()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    dev = settings.env != "production"
    app = FastAPI(
        title="Parti",
        docs_url="/api/docs" if dev else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if dev else None,
    )
    app.state.settings = settings
    app.state.session_factory = make_session_factory(settings.database_url)
    app.state.mailer = notify.make_mailer(settings)
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    return app
