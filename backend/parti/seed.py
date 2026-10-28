from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from parti.models import (
    ClassSession,
    Contribution,
    ContributionType,
    Course,
    CourseRole,
    Enrolment,
    FlagKind,
    Grade,
    Institution,
    SessionSummary,
    StudentFlag,
    Term,
    User,
)

DEMO_SLUG = "demo"
DEMO_ISSUER = "demo"
PROFESSOR_EMAIL = "professor@demo.example.edu"
TZ = ZoneInfo("Europe/Madrid")
SOURCE_MODEL = "sample-data"

TYPES = {
    "I": ContributionType.INSIGHT,
    "Q": ContributionType.QUESTION,
    "B": ContributionType.BUILDS_ON_OTHERS,
}

SESSIONS = {5: date(2026, 9, 30), 6: date(2026, 10, 7), 7: date(2026, 10, 14)}
CURRENT = 7


@dataclass(frozen=True)
class Student:
    name: str
    grade: float | None = None
    borderline: bool = False
    contributions: tuple[tuple[str, str, int, str], ...] = ()
    earlier: tuple[tuple[int, str, str, int, str], ...] = ()


STUDENTS = (
    Student(
        "Amara Okafor",
        9,
        contributions=(
            ("10:04", "I", 82, "Framed Lumora's delay as a capacity problem, not a demand problem"),
            ("10:18", "B", 70, "Built on a classmate's point by adding supplier lock-in as a switching cost"),
            ("10:31", "Q", 64, "Asked how regulators would treat Lumora's new recycling claims"),
            ("10:42", "I", 88, "Linked the case to last week's pricing model"),
            ("10:55", "I", 79, "Argued the cost advantage disappears once competitors reach scale"),
            ("11:05", "Q", 66, "Asked whether the CEO's incentives explained the delay"),
            ("11:16", "B", 72, "Extended the stakeholder map to include municipal grid operators"),
            ("11:27", "I", 85, "Proposed staging the launch by country to protect margins"),
        ),
    ),
    Student(
        "Lucas Ferreira",
        8,
        contributions=(
            ("10:06", "Q", 58, "Asked what share of revenue depends on the Nordic market"),
            (
                "10:15",
                "I",
                76,
                "Identified long-term supply contracts as Lumora's main competitive advantage",
            ),
            (
                "10:27",
                "B",
                66,
                "Agreed with a classmate and added that limited capacity weakens pricing power",
            ),
            ("10:39", "I", 74, "Compared Lumora's premium pricing to a luxury positioning strategy"),
            ("10:50", "Q", 55, "Asked whether customers would pay more for verified low-carbon batteries"),
            ("11:02", "B", 68, "Connected a classmate's ESG point to investor pressure"),
            ("11:14", "I", 80, "Argued that delaying protects the brand more than rushing a weak launch"),
            ("11:24", "Q", 52, "Asked how the delay affects the sales team's targets"),
        ),
    ),
    Student(
        "Mei Lin Zhang",
        8.5,
        contributions=(
            ("10:09", "I", 84, "Argued ESG reporting is becoming a licence to operate, not a differentiator"),
            ("10:21", "Q", 62, "Asked how Lumora measures the carbon footprint of its supply chain"),
            ("10:35", "B", 71, "Added that unions are a stakeholder the case overlooks"),
            (
                "10:47",
                "I",
                86,
                "Showed the low-carbon premium only holds if competitors can't copy the certification",
            ),
            ("11:00", "B", 69, "Built on the pricing discussion with a tiered pricing option"),
            (
                "11:12",
                "I",
                78,
                "Argued that transparent communication about the delay protects investor trust",
            ),
            ("11:22", "Q", 54, "Asked what a credible 2030 ESG target would look like"),
        ),
    ),
    Student(
        "Rohan Mehta",
        7.5,
        contributions=(
            ("10:12", "Q", 50, "Asked how large the price gap is versus Asian competitors"),
            ("10:44", "I", 72, "Pointed out that vertical integration reduces exposure to lithium prices"),
            ("11:10", "B", 61, "Added that the delay gives competitors a window to lock in customers"),
        ),
    ),
    Student(
        "Sofia Rossi",
        7,
        contributions=(
            ("10:24", "I", 68, "Suggested framing the delay as a quality commitment to customers"),
            ("11:18", "Q", 49, "Asked how employees were told about the delay"),
        ),
    ),
    Student(
        "Tomás Herrera",
        6.5,
        contributions=(
            ("10:29", "B", 58, "Built on the supplier discussion with a dual-sourcing idea"),
            ("10:58", "Q", 46, "Asked whether the case figures include government subsidies"),
        ),
    ),
    Student(
        "Leila Haddad",
        7,
        contributions=(
            ("10:37", "I", 74, "Argued that community opposition to the new plant is the real risk"),
            ("11:20", "B", 60, "Linked local opposition to the company's ESG reporting"),
        ),
    ),
    Student(
        "Jonas Becker",
        6,
        contributions=(
            ("10:33", "Q", 44, "Asked what competitors' margins look like"),
            ("11:06", "I", 63, "Noted that the premium price only works with strong after-sales service"),
        ),
    ),
    Student(
        "Aiko Tanaka",
        contributions=(
            ("10:52", "I", 70, "Proposed partnering with a carmaker to share the expansion risk"),
            ("11:25", "B", 57, "Added that a partner would also bring distribution channels"),
        ),
    ),
    Student(
        "Kwame Mensah",
        borderline=True,
        contributions=(
            ("10:46", "Q", 30, "Asked what cost leadership means in this case"),
            ("11:13", "I", 66, "Pointed out that the delay frees cash for R&D"),
        ),
    ),
    Student(
        "Ingrid Larsen",
        borderline=True,
        contributions=(("11:03", "B", 38, "Agreed with the previous point about investor pressure"),),
    ),
    Student(
        "Diego Álvarez",
        borderline=True,
        contributions=(
            ("10:20", "I", 62, "Suggested Lumora sell to energy storage before cars"),
            ("11:28", "Q", 28, "Asked how the case relates to the midterm"),
        ),
    ),
    Student(
        "Nadia Petrova", earlier=((5, "10:40", "Q", 52, "Asked how Lumora's suppliers set their prices"),)
    ),
    Student("Yusuf Demir"),
    Student(
        "Chloé Martin",
        earlier=(
            (6, "11:02", "I", 61, "Argued that first-mover advantage matters less in commodity markets"),
        ),
    ),
)

SUMMARY = {
    "key_points": [
        "Lumora's delay was framed as a capacity problem rather than weak demand. A student pointed out that "
        "limited capacity also limits pricing power.",
        "Long-term supply contracts and vertical integration were named as the main sources of competitive "
        "advantage, though one student argued the cost advantage fades once competitors reach scale.",
        "On pricing, the class linked the case to last week's pricing model and compared premium "
        "positioning with a tiered option. Several students noted the premium only works with strong "
        "after-sales service.",
        "ESG was discussed as a licence to operate rather than a differentiator: the low-carbon premium "
        "lasts only if competitors can't copy the certification.",
        "The stakeholder map grew beyond investors to include unions, municipal grid operators and local "
        "communities opposing the new plant.",
        "Options raised: staging the launch by country, partnering with a carmaker to share risk, or selling "
        "to energy storage before cars.",
    ],
    "open_questions": [
        "Did the CEO's incentives contribute to the delay?",
        "Would customers pay more for verified low-carbon batteries?",
    ],
    "suggested_reading": "Michael E. Porter, “What Is Strategy?”, Harvard Business Review (1996): useful for "
    "separating operational effectiveness from Lumora's strategic positioning.",
}


class DemoDataExists(Exception):
    pass


def _at(session_number: int, hhmm: str) -> datetime:
    return datetime.combine(SESSIONS[session_number], time.fromisoformat(hhmm), TZ)


def _slug(name: str) -> str:
    return "".join(c for c in name.lower().replace(" ", ".") if c.isascii() and (c.isalnum() or c == "."))


def remove_demo(db: Session) -> None:
    inst = db.scalar(select(Institution).where(Institution.slug == DEMO_SLUG))
    if inst is None:
        return
    db.execute(delete(Term).where(Term.institution_id == inst.id))
    db.execute(delete(User).where(User.institution_id == inst.id))
    db.execute(delete(Institution).where(Institution.id == inst.id))


def seed(db: Session, *, reset: bool = False) -> str:
    if db.scalar(select(Institution.id).where(Institution.slug == DEMO_SLUG)):
        if not reset:
            raise DemoDataExists("Demo data is already loaded. Use --reset to replace it.")
        remove_demo(db)
        db.flush()

    inst = Institution(name="Demo University", slug=DEMO_SLUG)
    term = Term(
        institution=inst,
        name="Fall 2026",
        starts_on=date(2026, 9, 7),
        ends_on=date(2026, 12, 18),
        delete_data_after=date(2027, 1, 31),
    )
    course = Course(term=term, code="STRAT-201", title="Strategy & Society")
    professor = User(
        institution=inst,
        sso_issuer=DEMO_ISSUER,
        sso_subject="professor",
        email=PROFESSOR_EMAIL,
        display_name="Dana Whitfield",
    )
    db.add_all([inst, term, course, professor])
    db.add(Enrolment(course=course, user=professor, role=CourseRole.PROFESSOR))

    sessions = {
        n: ClassSession(course=course, number=n, starts_at=_at(n, "10:00"), ends_at=_at(n, "11:30"))
        for n in SESSIONS
    }
    db.add_all(sessions.values())
    db.flush()

    current = sessions[CURRENT]
    for s in STUDENTS:
        user = User(
            institution=inst,
            sso_issuer=DEMO_ISSUER,
            sso_subject=_slug(s.name),
            email=f"{_slug(s.name)}@demo.example.edu",
            display_name=s.name,
        )
        db.add(user)
        db.add(Enrolment(course=course, user=user, role=CourseRole.STUDENT))
        db.flush()
        rows = [(CURRENT, *c) for c in s.contributions] + list(s.earlier)
        for number, hhmm, kind, depth, summary in rows:
            db.add(
                Contribution(
                    session_id=sessions[number].id,
                    student_id=user.id,
                    occurred_at=_at(number, hhmm),
                    type=TYPES[kind],
                    summary=summary,
                    depth=depth,
                    generated_by_model=SOURCE_MODEL,
                )
            )
        if s.grade is not None:
            db.add(
                Grade(
                    session_id=current.id,
                    student_id=user.id,
                    value=Decimal(str(s.grade)),
                    set_by_id=professor.id,
                )
            )
        if s.borderline:
            db.add(
                StudentFlag(
                    session_id=current.id,
                    student_id=user.id,
                    kind=FlagKind.BORDERLINE,
                    set_by_id=professor.id,
                )
            )

    db.add(SessionSummary(session_id=current.id, generated_by_model=SOURCE_MODEL, **SUMMARY))
    db.commit()
    return PROFESSOR_EMAIL
