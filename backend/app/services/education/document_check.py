"""Turns the AI's observations of an academic document into an outcome.

The AI reports what the document appears to show (integrations/ai/
education.py); *this* module decides MATCH / MISMATCH / UNCLEAR, from those
structured fields only, so a document can't talk its way into a MATCH.
Candidate-facing messages are composed here from vocabulary labels — never
from model free text — and never claim a document is genuine.

Confidence-aware by design: anything the model isn't sure about is
UNCLEAR (re-upload a clearer copy, or continue to manual review), never a
rejection. MISMATCH is reserved for a confident, specific inconsistency.
"""

import re
from dataclasses import dataclass

from app.integrations.ai.education import AcademicDocumentAnalysis
from app.models.application_draft import EducationVerificationOutcome
from app.services.education import catalog
from app.services.education.catalog import LEVEL_LABELS, QualificationLevel, QualificationOption
from app.services.education.job_qualification_service import QualificationRequirements

#: Below this the model's reading of the document is not relied on.
MIN_CONFIDENCE = 0.6

_SCHOOL_LEVELS = {"SECONDARY", "HIGHER_SECONDARY"}


@dataclass(frozen=True)
class CheckResult:
    outcome: EducationVerificationOutcome
    reason_code: str
    message: str


def _article(text: str) -> str:
    return "an" if text[:1].upper() in "AEIOU" else "a"


def _detected_description(analysis: AcademicDocumentAnalysis) -> str:
    detected = catalog.get_option(analysis.detected_qualification_key)
    if detected is not None and not detected.is_other:
        return f"{_article(detected.label)} {detected.label} academic record"
    if analysis.detected_level == "SECONDARY":
        return "a 10th-standard (secondary school) record"
    if analysis.detected_level == "HIGHER_SECONDARY":
        return "a 12th-standard (higher secondary) record"
    try:
        label = LEVEL_LABELS[QualificationLevel(analysis.detected_level)]
    except ValueError:
        return "a different qualification's record"
    return f"{_article(label)} {label} record"


_NAME_TOKEN = re.compile(r"[^\W\d_]{2,}")


def _name_tokens(name: str) -> set[str]:
    return {token.casefold() for token in _NAME_TOKEN.findall(name)}


def names_conflict(document_name: str, candidate_name: str) -> bool:
    """True only when a name was clearly read and shares no word (of 2+
    letters) with the applicant's name. Initials, word order and partial
    names never count as a conflict."""
    on_document = _name_tokens(document_name)
    applicant = _name_tokens(candidate_name)
    return bool(on_document) and bool(applicant) and not (on_document & applicant)


def evaluate(
    analysis: AcademicDocumentAnalysis,
    *,
    selected: QualificationOption,
    selected_branch: str | None,
    requirements: QualificationRequirements,
    candidate_name: str,
) -> CheckResult:
    match, mismatch, unclear = (
        EducationVerificationOutcome.MATCH,
        EducationVerificationOutcome.MISMATCH,
        EducationVerificationOutcome.UNCLEAR,
    )
    if not analysis.legible or analysis.document_type == "UNREADABLE":
        return CheckResult(
            unclear,
            "illegible",
            "We couldn't read the uploaded document clearly. Please upload a clearer, complete "
            "scan or PDF of your academic record.",
        )
    if not analysis.is_academic_document or analysis.document_type == "NOT_ACADEMIC":
        if analysis.confidence >= MIN_CONFIDENCE:
            return CheckResult(
                mismatch,
                "not_academic",
                "The uploaded file doesn't appear to be an academic record. Please upload "
                f"{selected.document_hint} for {selected.label}.",
            )
        return CheckResult(
            unclear,
            "low_confidence",
            "We couldn't confidently identify the uploaded document as an academic record. "
            "Please upload a clearer copy.",
        )
    if analysis.confidence < MIN_CONFIDENCE or analysis.detected_level == "UNKNOWN":
        return CheckResult(
            unclear,
            "low_confidence",
            f"We couldn't confidently confirm that this document is for {selected.label}. "
            "Please upload a clearer or more complete copy that shows the degree name.",
        )

    selected_level = selected.level.value
    if analysis.detected_level in _SCHOOL_LEVELS or analysis.detected_level != selected_level:
        return CheckResult(
            mismatch,
            "level_mismatch",
            "The qualification selected does not appear to match the uploaded document. You "
            f"selected {selected.label}, but the uploaded document appears to be "
            f"{_detected_description(analysis)}. Please upload the correct document.",
        )
    detected = catalog.get_option(analysis.detected_qualification_key)
    if (
        detected is not None
        and detected.key != selected.key
        and not detected.is_other
        and not selected.is_other
    ):
        return CheckResult(
            mismatch,
            "degree_mismatch",
            "The qualification selected does not appear to match the uploaded document. You "
            f"selected {selected.label}, but the uploaded document appears to be "
            f"{_detected_description(analysis)}. Please upload the correct document.",
        )
    if selected_branch and analysis.branch_matches_selected == "NO":
        return CheckResult(
            mismatch,
            "branch_mismatch",
            f"The {selected.branch_label.lower()} on the uploaded document doesn't appear to "
            f'match the one you entered ("{selected_branch}"). Please check your details or '
            "upload the correct document.",
        )
    if names_conflict(analysis.name_on_document, candidate_name):
        return CheckResult(
            unclear,
            "name_mismatch",
            "The name on the uploaded document doesn't appear to match the name on your "
            "application. Please upload a document issued in your name, or continue and our "
            "recruitment team will review it.",
        )
    if not requirements.allows(selected.key):
        return CheckResult(
            mismatch,
            "not_eligible",
            f"{selected.label} isn't one of the eligible qualifications for this role.",
        )
    return CheckResult(
        match,
        "consistent",
        f"Your document appears consistent with {selected.label}. You can continue.",
    )


AI_FAILED_MESSAGE = (
    "We couldn't check your document right now. Your document and details are saved — "
    "please try again in a moment."
)
AI_UNAVAILABLE_MESSAGE = (
    "Automatic document checking isn't available right now. Your document is saved — you "
    "can continue, and our recruitment team will review it."
)
