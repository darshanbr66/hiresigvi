"""The Education step's specialization catalogue — a fixed lookup, never an
AI decision (app/services/education/specializations.py)."""

import pytest

from app.services.education import specializations

EXPECTED: dict[str, tuple[str, list[str]]] = {
    "CS_IT": (
        "Computer Science & Information Technology",
        [
            "Computer Science and Engineering",
            "Information Technology",
            "Artificial Intelligence and Machine Learning",
            "Data Science",
            "Software Engineering",
            "Cybersecurity",
            "Computer Networks",
            "Cloud Computing",
            "Computer Engineering",
        ],
    ),
    "ELECTRONICS_ELECTRICAL": (
        "Electronics & Electrical",
        [
            "Electronics and Communication Engineering",
            "Electrical Engineering",
            "VLSI Design",
            "Microelectronics",
            "Semiconductor Technology",
            "Embedded Systems",
            "Instrumentation and Control Systems",
            "Signal Processing",
            "Power Systems",
        ],
    ),
    "MECHANICAL_MANUFACTURING": (
        "Mechanical & Manufacturing",
        [
            "Mechanical Engineering",
            "Mechatronics",
            "Robotics and Automation",
            "Automotive Engineering",
            "Aerospace Engineering",
            "Manufacturing Engineering",
            "Production Engineering",
            "Thermal Engineering",
            "Industrial Engineering",
        ],
    ),
    "CIVIL_INFRASTRUCTURE": (
        "Civil & Infrastructure",
        [
            "Civil Engineering",
            "Structural Engineering",
            "Construction Technology and Management",
            "Geotechnical Engineering",
            "Transportation Engineering",
            "Environmental Engineering",
            "Water Resources Engineering",
        ],
    ),
    "CHEMICAL_MATERIALS": (
        "Chemical & Materials",
        [
            "Chemical Engineering",
            "Materials Science and Engineering",
            "Metallurgical Engineering",
            "Polymer Technology",
            "Nanotechnology",
            "Process Engineering",
        ],
    ),
    "BIOTECH_BIOMEDICAL": (
        "Biotechnology & Biomedical",
        [
            "Biotechnology",
            "Biomedical Engineering",
            "Bioinformatics",
            "Medical Technology",
            "Pharmaceutical Technology",
            "Genetic Engineering",
        ],
    ),
    "OTHER_TECHNOLOGY": (
        "Other Technology Domains",
        [
            "Energy Engineering",
            "Renewable Energy",
            "Agricultural Engineering",
            "Food Technology",
            "Textile Technology",
            "Mining Engineering",
            "Petroleum Engineering",
            "Environmental Technology",
        ],
    ),
}


def test_all_seven_categories_in_order() -> None:
    assert [(c.key, c.label) for c in specializations.CATEGORIES] == [
        (key, label) for key, (label, _) in EXPECTED.items()
    ]


@pytest.mark.parametrize("category_key", list(EXPECTED))
def test_each_category_has_exactly_its_specializations(category_key: str) -> None:
    category = specializations.get_category(category_key)
    assert category is not None
    assert [s.label for s in category.specializations] == EXPECTED[category_key][1]


def test_every_valid_pair_resolves() -> None:
    for category in specializations.CATEGORIES:
        for item in category.specializations:
            assert specializations.resolve(category.key, item.key) == item


def test_a_specialization_from_another_category_is_rejected() -> None:
    for category in specializations.CATEGORIES:
        for other in specializations.CATEGORIES:
            if other.key == category.key:
                continue
            for item in other.specializations:
                assert specializations.resolve(category.key, item.key) is None


@pytest.mark.parametrize(
    ("category_key", "specialization_key"),
    [
        ("NOT_A_CATEGORY", "MECHATRONICS"),
        ("MECHANICAL_MANUFACTURING", "NOT_A_SPECIALIZATION"),
        (None, "MECHATRONICS"),
        ("MECHANICAL_MANUFACTURING", None),
        ("mechanical_manufacturing", "MECHATRONICS"),  # keys are exact
        ("Mechanical & Manufacturing", "Mechatronics"),  # labels aren't keys
    ],
)
def test_unknown_keys_are_rejected(
    category_key: str | None, specialization_key: str | None
) -> None:
    assert specializations.resolve(category_key, specialization_key) is None


def test_labels_for_stored_keys() -> None:
    assert specializations.category_label("CIVIL_INFRASTRUCTURE") == "Civil & Infrastructure"
    assert specializations.specialization_label("VLSI_DESIGN") == "VLSI Design"
    # A retired key still shows something; nothing stored shows nothing.
    assert specializations.specialization_label("RETIRED_KEY") == "RETIRED_KEY"
    assert specializations.category_label(None) is None


@pytest.mark.parametrize(
    ("specialization_key", "fields", "matched_field"),
    [
        # Same name, other wording, or one contained in the other.
        ("COMPUTER_SCIENCE_ENGINEERING", ["Computer Science"], "Computer Science"),
        ("COMPUTER_SCIENCE_ENGINEERING", ["Computer Science & Engineering"], None),
        ("COMPUTER_SCIENCE_ENGINEERING", ["computer science engineering"], None),
        ("MECHANICAL_ENGINEERING", ["Mechanical Engg."], None),
        ("MECHANICAL_ENGINEERING", ["Mechanical"], None),
        ("ELECTRONICS_COMMUNICATION", ["Electronics & Communications"], None),
        ("ELECTRONICS_COMMUNICATION", ["Electronics"], None),
        # Known equivalents and abbreviations.
        ("COMPUTER_SCIENCE_ENGINEERING", ["CSE"], None),
        ("COMPUTER_SCIENCE_ENGINEERING", ["C.S.E."], None),
        ("COMPUTER_SCIENCE_ENGINEERING", ["B.Tech in CSE"], None),
        ("INFORMATION_TECHNOLOGY", ["IT"], None),
        ("ELECTRONICS_COMMUNICATION", ["ECE"], None),
        ("ELECTRONICS_COMMUNICATION", ["Electronics and Telecommunication Engineering"], None),
        ("ELECTRICAL_ENGINEERING", ["EEE"], None),
        ("AI_ML", ["Artificial Intelligence"], None),
        ("VLSI_DESIGN", ["VLSI"], None),
        # Alternatives within one field, and the field that matched.
        ("INFORMATION_TECHNOLOGY", ["Computer Science / IT"], "Computer Science / IT"),
        ("ROBOTICS_AUTOMATION", ["Mechanical", "Robotics"], "Robotics"),
        ("DATA_SCIENCE", ["Computer Science or related field", "Data Science"], "Data Science"),
    ],
)
def test_clear_matches_are_not_flagged(
    specialization_key: str, fields: list[str], matched_field: str | None
) -> None:
    result = specializations.match_field_of_study(specialization_key, fields)
    assert result.matched is True
    assert result.field_of_study == (matched_field or fields[0])


@pytest.mark.parametrize(
    ("specialization_key", "fields"),
    [
        ("ROBOTICS_AUTOMATION", ["Mechanical Engineering"]),  # related, not the same
        ("AEROSPACE_ENGINEERING", ["Aeronautical Engineering"]),
        ("ELECTRICAL_ENGINEERING", ["Electronics and Communication Engineering"]),
        ("INFORMATION_TECHNOLOGY", ["Computer Science"]),
        ("CIVIL_ENGINEERING", ["CE"]),  # ambiguous abbreviation: never guessed
        ("MECHANICAL_ENGINEERING", ["ME"]),  # Master of Engineering, or Mechanical?
        # Too broad to decide automatically.
        ("MECHANICAL_ENGINEERING", ["Engineering"]),
        ("DATA_SCIENCE", ["Science"]),
        ("FOOD_TECHNOLOGY", ["Technology"]),
        ("DATA_SCIENCE", ["any related field"]),
        ("COMPUTER_SCIENCE_ENGINEERING", []),
        (None, ["Computer Science"]),
        ("NOT_A_KEY", ["Computer Science"]),
    ],
)
def test_uncertain_or_unmatched_fields_are_flagged(
    specialization_key: str | None, fields: list[str]
) -> None:
    result = specializations.match_field_of_study(specialization_key, fields)
    assert result.matched is False and result.field_of_study is None


def test_equivalent_names_belong_to_real_specializations() -> None:
    known = {s.key for c in specializations.CATEGORIES for s in c.specializations}
    assert set(specializations.EQUIVALENT_NAMES) <= known
