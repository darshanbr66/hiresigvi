"""Unit tests for the rule-based question-import parser
(app/integrations/documents/question_import.py) — pure functions, no DB.
Covers the "Import Questions" workflow's core promise: never fabricate a
correct answer it can't find explicitly in the document."""

import io

import pytest
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from openpyxl import Workbook

from app.integrations.documents.question_import import QuestionImportError, parse_questions


def test_csv_single_and_multi_answer() -> None:
    csv_bytes = (
        b"question,option_1,option_2,option_3,option_4,correct,type\n"
        b"What is 2+2?,3,4,5,6,2,single\n"
        b'Pick prime numbers,2,4,6,7,"1,4",multiple\n'
    )
    result = parse_questions(content=csv_bytes, filename="questions.csv")

    assert result.warnings == []
    assert len(result.questions) == 2

    q1 = result.questions[0]
    assert q1.prompt == "What is 2+2?"
    assert q1.type == "MCQ_SINGLE"
    assert [o.is_correct for o in q1.options] == [False, True, False, False]

    q2 = result.questions[1]
    assert q2.type == "MCQ_MULTI"
    assert [o.is_correct for o in q2.options] == [True, False, False, True]


def test_csv_letter_answer_key() -> None:
    csv_bytes = b"question,option_1,option_2,option_3,correct\nCapital of France?,Berlin,Paris,Rome,B\n"
    result = parse_questions(content=csv_bytes, filename="q.csv")
    assert result.questions[0].options[1].label == "Paris"
    assert result.questions[0].options[1].is_correct is True


def test_csv_row_without_resolvable_answer_is_skipped_not_guessed() -> None:
    csv_bytes = b"question,option_1,option_2,correct\nUnanswerable?,A,B,\n"
    result = parse_questions(content=csv_bytes, filename="q.csv")
    assert result.questions == []
    assert "skipped" in result.warnings[0].lower()


def test_csv_missing_question_column_is_a_clear_error() -> None:
    csv_bytes = b"a,b,c\n1,2,3\n"
    with pytest.raises(QuestionImportError, match="question"):
        parse_questions(content=csv_bytes, filename="q.csv")


def test_xlsx_roundtrip() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["question", "option_1", "option_2", "option_3", "correct"])
    sheet.append(["2 + 2 = ?", "3", "4", "5", "2"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    result = parse_questions(content=buffer.getvalue(), filename="questions.xlsx")
    assert len(result.questions) == 1
    assert result.questions[0].options[1].is_correct is True


def test_docx_numbered_questions_with_asterisk_and_answer_line() -> None:
    document = Document()
    document.add_paragraph("1. What is the capital of France?")
    document.add_paragraph("A) Berlin")
    document.add_paragraph("B) Paris*")
    document.add_paragraph("C) Madrid")
    document.add_paragraph("2. Which are prime numbers? [2 pts]")
    document.add_paragraph("A) 2")
    document.add_paragraph("B) 4")
    document.add_paragraph("C) 5")
    document.add_paragraph("D) 9")
    document.add_paragraph("Answer: A, C")
    document.add_paragraph("3. No answer marked at all")
    document.add_paragraph("A) x")
    document.add_paragraph("B) y")
    buffer = io.BytesIO()
    document.save(buffer)

    result = parse_questions(content=buffer.getvalue(), filename="questions.docx")

    assert len(result.questions) == 2
    q1 = result.questions[0]
    assert q1.prompt == "What is the capital of France?"
    assert q1.type == "MCQ_SINGLE"
    assert q1.options[1].label == "Paris"
    assert q1.options[1].is_correct is True

    q2 = result.questions[1]
    assert q2.type == "MCQ_MULTI"
    assert q2.points == 2
    assert "[2 pts]" not in q2.prompt
    assert [o.is_correct for o in q2.options] == [True, False, True, False]

    assert len(result.warnings) == 1
    assert "Question 3" in result.warnings[0]


def test_docx_with_no_numbered_questions_is_a_clear_error() -> None:
    document = Document()
    document.add_paragraph("This document has no numbered questions in it.")
    buffer = io.BytesIO()
    document.save(buffer)

    with pytest.raises(QuestionImportError, match="numbered questions"):
        parse_questions(content=buffer.getvalue(), filename="q.docx")


def test_docx_with_word_automatic_numbering() -> None:
    """Word's automatic numbering ("List Number" style) keeps "1." out of the
    paragraph text — the import must still see numbered questions."""
    document = Document()
    document.add_paragraph("What is 2+2?", style="List Number")
    document.add_paragraph("A) 3")
    document.add_paragraph("B) 4*")
    document.add_paragraph("Capital of France?", style="List Number")
    document.add_paragraph("A) Paris")
    document.add_paragraph("B) Rome")
    document.add_paragraph("Answer: A")
    buffer = io.BytesIO()
    document.save(buffer)

    result = parse_questions(content=buffer.getvalue(), filename="q.docx")

    assert result.warnings == []
    assert [q.prompt for q in result.questions] == ["What is 2+2?", "Capital of France?"]
    assert [o.is_correct for o in result.questions[0].options] == [False, True]
    assert [o.is_correct for o in result.questions[1].options] == [True, False]


def test_docx_with_automatic_lettered_options() -> None:
    """Options as a lettered sub-list (level 2 of the question list) restart
    at A under each question."""
    document = Document()
    numbering = document.part.numbering_part.element
    abstract = parse_xml(
        f'<w:abstractNum {nsdecls("w")} w:abstractNumId="90">'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/></w:lvl>'
        '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/></w:lvl>'
        "</w:abstractNum>"
    )
    numbering.insert(0, abstract)
    numbering.append(
        parse_xml(f'<w:num {nsdecls("w")} w:numId="90"><w:abstractNumId w:val="90"/></w:num>')
    )

    def item(text: str, level: int) -> None:
        paragraph = document.add_paragraph(text)
        num_pr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
        num_pr.get_or_add_numId().val = 90
        num_pr.get_or_add_ilvl().val = level

    item("Pick the even number", 0)
    item("3", 1)
    item("4*", 1)
    item("Pick the vowels", 0)
    item("a", 1)
    item("b", 1)
    item("e", 1)
    document.add_paragraph("Answer: A, C")
    buffer = io.BytesIO()
    document.save(buffer)

    result = parse_questions(content=buffer.getvalue(), filename="q.docx")

    assert result.warnings == []
    first, second = result.questions
    assert [(o.label, o.is_correct) for o in first.options] == [("3", False), ("4", True)]
    assert second.type == "MCQ_MULTI"
    assert [o.is_correct for o in second.options] == [True, False, True]


def test_unsupported_file_type_is_rejected() -> None:
    with pytest.raises(QuestionImportError, match="Unsupported file type"):
        parse_questions(content=b"whatever", filename="questions.txt")


def _multiline_pdf(lines: list[str]) -> bytes:
    """A single-page PDF with one text line per entry (pypdf extracts them
    as separate lines)."""
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    body = " ".join(f"({line}) Tj 0 -16 Td" for line in escaped)
    stream = f"BT /F1 12 Tf 72 740 Td {body} ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def test_pdf_numbered_questions_with_asterisk_and_answer_line() -> None:
    pdf = _multiline_pdf([
        "1. What is the capital of France?",
        "A) Berlin",
        "B) Paris*",
        "2. Which are prime numbers?",
        "A) 2",
        "B) 4",
        "C) 5",
        "Answer: A, C",
        "3. Answer names a missing option",
        "A) yes",
        "B) no",
        "Answer: D",
    ])
    result = parse_questions(content=pdf, filename="questions.pdf")

    assert [q.prompt for q in result.questions] == [
        "What is the capital of France?",
        "Which are prime numbers?",
    ]
    assert [o.is_correct for o in result.questions[0].options] == [False, True]
    assert result.questions[1].type == "MCQ_MULTI"
    # Reported, never silently dropped or guessed.
    assert len(result.warnings) == 1
    assert "Question 3" in result.warnings[0] and "D" in result.warnings[0]


def test_csv_optional_metadata_and_non_choice_types() -> None:
    csv_bytes = (
        b"question,option_1,option_2,correct,type,difficulty,subject,topic,marks,tags,explanation\n"
        b"Is Python interpreted?,True,False,A,true_false,easy,Programming,Python,2,basics;lang,Usually.\n"
        b"Name the SQL keyword to sort rows.,,,ORDER BY|order by,short_answer,medium,Databases,SQL,1,,\n"
        b"Explain inverse kinematics.,,,,descriptive,hard,Robotics,,5,,\n"
    )
    result = parse_questions(content=csv_bytes, filename="q.csv")

    assert result.warnings == []
    tf, short, descriptive = result.questions
    assert (tf.type, tf.difficulty, tf.subject, tf.topic, tf.points) == ("TRUE_FALSE", "EASY", "Programming", "Python", 2)
    assert tf.tags == ["basics", "lang"] and tf.explanation == "Usually."
    assert tf.source == "Row 2"
    assert (short.type, short.accepted_answers) == ("SHORT_ANSWER", ["ORDER BY", "order by"])
    assert (descriptive.type, descriptive.options, descriptive.points) == ("DESCRIPTIVE", [], 5)


def test_csv_row_problems_are_reported_per_row() -> None:
    csv_bytes = (
        b"question,option_1,option_2,correct,type,difficulty,marks\n"
        b"Good?,yes,no,1,,,\n"
        b"Bad answer?,yes,no,7,,,\n"
        b"Bad marks?,yes,no,1,,,lots\n"
        b"Bad difficulty?,yes,no,1,,extreme,\n"
        b"Bad type?,yes,no,1,essayish-thing,,\n"
    )
    result = parse_questions(content=csv_bytes, filename="q.csv")

    assert [q.prompt for q in result.questions] == ["Good?"]
    assert [w.split(":")[0] for w in result.warnings] == ["Row 3", "Row 4", "Row 5", "Row 6"]
    assert "doesn't match any option" in result.warnings[0]


def test_content_must_match_the_extension() -> None:
    with pytest.raises(QuestionImportError, match="contents don't match"):
        parse_questions(content=b"question,option_1\n", filename="q.pdf")
    with pytest.raises(QuestionImportError, match="contents don't match"):
        parse_questions(content=b"%PDF-1.4 not really a spreadsheet", filename="q.xlsx")
    with pytest.raises(QuestionImportError, match="contents don't match"):
        parse_questions(content=b"%PDF-1.4\n", filename="q.csv")


def test_oversized_and_empty_files_are_refused() -> None:
    from app.integrations.documents.question_import import MAX_IMPORT_BYTES

    with pytest.raises(QuestionImportError, match="larger than"):
        parse_questions(content=b"a" * (MAX_IMPORT_BYTES + 1), filename="q.csv")
    with pytest.raises(QuestionImportError, match="empty"):
        parse_questions(content=b"", filename="q.csv")


def test_unreadable_files_get_a_safe_message() -> None:
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<not-a-real-workbook")
    with pytest.raises(QuestionImportError) as raised:
        parse_questions(content=buffer.getvalue(), filename="q.xlsx")
    # A plain instruction, never the library's exception text.
    assert str(raised.value) == "Could not read this XLSX file. Check that it opens in Excel."


def test_row_count_is_bounded() -> None:
    from app.integrations.documents.question_import import MAX_IMPORT_QUESTIONS

    rows = "".join(f"Q{i}?,a,b,1\n" for i in range(MAX_IMPORT_QUESTIONS + 5))
    result = parse_questions(content=("question,option_1,option_2,correct\n" + rows).encode(), filename="q.csv")
    assert len(result.questions) == MAX_IMPORT_QUESTIONS
    assert "first" in result.warnings[0]
