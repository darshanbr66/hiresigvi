"""The qualification vocabulary the self-service apply flow speaks.

This is a *vocabulary*, not an eligibility rule. Which of these a candidate
may choose for a given job comes from that job's own description and
eligibility text (job_qualification_service.py), never from this module:
a job whose JD says "M.Tech mandatory" offers only M.Tech (and related
higher qualifications); a job that says nothing restrictive offers the
default list (`OFFERED_KEYS`). A fixed vocabulary is what makes that
enforceable server-side: the AI maps free JD text onto these keys, and
every later comparison
(eligibility, the uploaded document's detected degree) is a deterministic
comparison of keys and levels, not of free text.

Levels are ordered (`LEVEL_RANK`) so "below the stated minimum" is a plain
comparison. `family` groups related degrees, so "related higher
qualifications" (B.E./B.Tech -> M.Tech) can be derived rather than guessed.

The vocabulary is wider than what candidates may pick: the AI still needs
every degree to read a JD or recognize an uploaded B.Tech marks card, but
the Education step only ever offers `OFFERED_KEYS` (intersected with the
job's own requirements).
"""

from dataclasses import dataclass
from enum import StrEnum

from app.models.candidate import EducationLevel


class QualificationLevel(StrEnum):
    DIPLOMA = "DIPLOMA"
    BACHELORS = "BACHELORS"
    MASTERS = "MASTERS"
    DOCTORATE = "DOCTORATE"


LEVEL_RANK: dict[QualificationLevel, int] = {
    QualificationLevel.DIPLOMA: 1,
    QualificationLevel.BACHELORS: 2,
    QualificationLevel.MASTERS: 3,
    QualificationLevel.DOCTORATE: 4,
}

LEVEL_LABELS: dict[QualificationLevel, str] = {
    QualificationLevel.DIPLOMA: "diploma",
    QualificationLevel.BACHELORS: "bachelor's degree",
    QualificationLevel.MASTERS: "master's degree",
    QualificationLevel.DOCTORATE: "doctorate",
}


@dataclass(frozen=True)
class QualificationOption:
    key: str
    label: str
    level: QualificationLevel
    #: Related degrees share a family (engineering, science, ...); "ANY"
    #: (the doctorate) relates to every family.
    family: str
    #: What the specialization is called for this degree, in the form.
    branch_label: str
    #: The academic record the candidate is asked to upload for it.
    document_hint: str
    #: A catch-all ("Other bachelor's degree"): the candidate must name the
    #: degree, and it is only ever offered when a job doesn't restrict
    #: qualifications.
    is_other: bool = False

    @property
    def education_level(self) -> EducationLevel:
        """The candidate profile's coarser `education_level`."""
        if self.level == QualificationLevel.DIPLOMA:
            return EducationLevel.DIPLOMA
        if self.level == QualificationLevel.BACHELORS:
            return EducationLevel.BACHELORS
        if self.level == QualificationLevel.DOCTORATE:
            return EducationLevel.PHD
        return EducationLevel.MTECH if self.key == "ME_MTECH" else EducationLevel.MASTERS


_DEGREE_RECORD = (
    "your final-year (or consolidated) marks card, provisional certificate or degree certificate"
)

CATALOG: tuple[QualificationOption, ...] = (
    QualificationOption(
        "DIPLOMA_ENGINEERING", "Diploma in Engineering", QualificationLevel.DIPLOMA,
        "ENGINEERING", "Branch", "your diploma marks card or diploma certificate",
    ),
    QualificationOption(
        "DIPLOMA_OTHER", "Other diploma", QualificationLevel.DIPLOMA, "OTHER", "Subject",
        "your diploma marks card or diploma certificate", is_other=True,
    ),
    QualificationOption(
        "BE_BTECH", "B.E. / B.Tech", QualificationLevel.BACHELORS, "ENGINEERING", "Branch",
        _DEGREE_RECORD,
    ),
    QualificationOption(
        "BSC", "B.Sc.", QualificationLevel.BACHELORS, "SCIENCE", "Subject", _DEGREE_RECORD
    ),
    QualificationOption(
        "BCA", "BCA", QualificationLevel.BACHELORS, "COMPUTER_APPLICATIONS", "Specialization",
        _DEGREE_RECORD,
    ),
    QualificationOption(
        "BPHARM", "B.Pharm", QualificationLevel.BACHELORS, "PHARMACY", "Specialization",
        _DEGREE_RECORD,
    ),
    QualificationOption(
        "BCOM", "B.Com", QualificationLevel.BACHELORS, "COMMERCE", "Specialization",
        _DEGREE_RECORD,
    ),
    QualificationOption(
        "BBA", "BBA / BBM", QualificationLevel.BACHELORS, "MANAGEMENT", "Specialization",
        _DEGREE_RECORD,
    ),
    QualificationOption(
        "BA", "B.A.", QualificationLevel.BACHELORS, "ARTS", "Subject", _DEGREE_RECORD
    ),
    QualificationOption(
        "LLB", "LL.B.", QualificationLevel.BACHELORS, "LAW", "Specialization", _DEGREE_RECORD
    ),
    QualificationOption(
        "BACHELORS_OTHER", "Other bachelor's degree", QualificationLevel.BACHELORS, "OTHER",
        "Specialization", _DEGREE_RECORD, is_other=True,
    ),
    QualificationOption(
        "ME_MTECH", "M.Tech", QualificationLevel.MASTERS, "ENGINEERING",
        "Specialization", _DEGREE_RECORD,
    ),
    QualificationOption(
        "MSC", "M.Sc.", QualificationLevel.MASTERS, "SCIENCE", "Subject", _DEGREE_RECORD
    ),
    QualificationOption(
        "MCA", "MCA", QualificationLevel.MASTERS, "COMPUTER_APPLICATIONS", "Specialization",
        _DEGREE_RECORD,
    ),
    QualificationOption(
        "MPHARM", "M.Pharm", QualificationLevel.MASTERS, "PHARMACY", "Specialization",
        _DEGREE_RECORD,
    ),
    QualificationOption(
        "MCOM", "M.Com", QualificationLevel.MASTERS, "COMMERCE", "Specialization",
        _DEGREE_RECORD,
    ),
    QualificationOption(
        "MBA", "MBA / PGDM", QualificationLevel.MASTERS, "MANAGEMENT", "Specialization",
        _DEGREE_RECORD,
    ),
    QualificationOption(
        "MA", "M.A.", QualificationLevel.MASTERS, "ARTS", "Subject", _DEGREE_RECORD
    ),
    QualificationOption(
        "LLM", "LL.M.", QualificationLevel.MASTERS, "LAW", "Specialization", _DEGREE_RECORD
    ),
    QualificationOption(
        "MASTERS_OTHER", "Other master's degree", QualificationLevel.MASTERS, "OTHER",
        "Specialization", _DEGREE_RECORD, is_other=True,
    ),
    QualificationOption(
        "PHD", "Ph.D.", QualificationLevel.DOCTORATE, "ANY", "Research area",
        "your Ph.D. degree certificate, provisional certificate or thesis-submission/award "
        "letter from the university",
    ),
)

BY_KEY: dict[str, QualificationOption] = {option.key: option for option in CATALOG}

#: The qualifications the candidate Education step can offer at all — the
#: platform default when a job doesn't restrict qualifications, and the
#: upper bound when it does (a job's eligible keys are intersected with
#: it). Widening what candidates may choose (e.g. per-job recruiter
#: configuration later) changes this set, not the vocabulary above.
OFFERED_KEYS: tuple[str, ...] = ("ME_MTECH", "PHD")


def get_option(key: str | None) -> QualificationOption | None:
    return BY_KEY.get(key) if key else None


def rank(level: QualificationLevel) -> int:
    return LEVEL_RANK[level]


def related_higher_keys(keys: set[str]) -> set[str]:
    """Higher-level degrees of the same family as any of `keys` (plus the
    doctorate, which relates to every family) — "a related higher
    qualification". Catch-all "other" degrees are never added this way."""
    base = [BY_KEY[key] for key in keys if key in BY_KEY]
    related: set[str] = set()
    for option in CATALOG:
        if option.is_other:
            continue
        for accepted in base:
            higher = rank(option.level) > rank(accepted.level)
            if higher and (option.family in (accepted.family, "ANY")):
                related.add(option.key)
    return related


def describe_options(keys: list[str]) -> str:
    return ", ".join(BY_KEY[key].label for key in keys if key in BY_KEY)
