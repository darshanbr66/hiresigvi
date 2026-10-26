"""The one definition of what a valid question looks like, per type —
shared by questions written directly into an assessment
(app/schemas/assessment.py) and by question-bank versions
(app/schemas/question_bank.py), so a question is never valid in one place
and invalid in the other."""

from app.models.assessment import (
    CHOICE_QUESTION_TYPES,
    MANUAL_QUESTION_TYPES,
    QuestionType,
)

MAX_OPTIONS = 10
MAX_ACCEPTED_ANSWERS = 20


def clean_accepted_answers(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        answer = " ".join(value.split())
        if answer and answer not in cleaned:
            cleaned.append(answer)
    return cleaned


def validate_question_content(
    *,
    question_type: QuestionType,
    prompt: str,
    option_labels: list[str],
    correct_flags: list[bool],
    accepted_answers: list[str],
    points: int,
    negative_points: int,
) -> None:
    """Raises ValueError (surfaced as a 422) describing the first problem."""
    if not prompt.strip():
        raise ValueError("Question text is required.")
    if negative_points < 0:
        raise ValueError("Negative marks cannot be below zero.")
    if negative_points > points:
        raise ValueError("Negative marks cannot exceed the question's marks.")

    if question_type in CHOICE_QUESTION_TYPES:
        if any(not label.strip() for label in option_labels):
            raise ValueError("Every option needs text.")
        if len(option_labels) > MAX_OPTIONS:
            raise ValueError(f"A question can have at most {MAX_OPTIONS} options.")
        correct = sum(1 for flag in correct_flags if flag)
        if question_type == QuestionType.TRUE_FALSE:
            if len(option_labels) != 2:
                raise ValueError("A true/false question has exactly two options.")
            if correct != 1:
                raise ValueError("Mark exactly one option of a true/false question as correct.")
        elif len(option_labels) < 2:
            raise ValueError("A multiple-choice question needs at least two options.")
        elif question_type == QuestionType.MCQ_SINGLE and correct != 1:
            raise ValueError("Mark exactly one correct option for a single-choice question.")
        elif question_type == QuestionType.MCQ_MULTI and correct < 1:
            raise ValueError("Mark at least one correct option for a multiple-choice question.")
        if accepted_answers:
            raise ValueError("Accepted answers apply only to short-answer questions.")
        return

    if option_labels:
        raise ValueError("Options apply only to choice questions.")
    if len(accepted_answers) > MAX_ACCEPTED_ANSWERS:
        raise ValueError(f"At most {MAX_ACCEPTED_ANSWERS} accepted answers.")
    if question_type in MANUAL_QUESTION_TYPES:
        if accepted_answers:
            raise ValueError("Descriptive and coding questions are graded manually; remove the accepted answers.")
        if negative_points:
            raise ValueError("Negative marking applies only to automatically graded questions.")
    if question_type == QuestionType.SHORT_ANSWER and not accepted_answers and negative_points:
        raise ValueError(
            "Negative marking needs accepted answers (without them the answer is graded manually)."
        )
