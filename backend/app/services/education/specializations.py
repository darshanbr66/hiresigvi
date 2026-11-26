"""The specialization catalogue of the candidate Education step.

Candidates type their specialization category and specialization as free
text (any value, trimmed, within the lengths below); the catalogue no
longer restricts what they can enter. It remains the internal reference
for three things:

- suggestions the Education step offers while typing (never a limit);
- resolving records stored before free-text entry, which hold catalogue
  keys (`CS_IT`, `AI_ML` …): `category_label` / `specialization_label`
  turn a key into its label and pass any other text through unchanged;
- recognising a specialization in a JD's field of study
  (`match_field_of_study`), including its established equivalent names.

A fixed application rule, never an AI decision. Keys are stable internal
identifiers; never reuse or repurpose one, so stored records keep
resolving to what the candidate chose.

The specialization (its label for a legacy key) is also what the
academic-document check compares the document's branch against
(`EducationDetails.branch` is derived from it server-side).
"""

import re
from dataclasses import dataclass

#: Longest accepted free-text values (characters, after trimming) — the same
#: limits in the API schema, the database columns and the candidate form.
CATEGORY_MAX_LENGTH = 100
SPECIALIZATION_MAX_LENGTH = 150


@dataclass(frozen=True)
class Specialization:
    key: str
    label: str


@dataclass(frozen=True)
class SpecializationCategory:
    key: str
    label: str
    specializations: tuple[Specialization, ...]

    def get(self, specialization_key: str | None) -> Specialization | None:
        if not specialization_key:
            return None
        return next((s for s in self.specializations if s.key == specialization_key), None)


def _category(key: str, label: str, *items: tuple[str, str]) -> SpecializationCategory:
    return SpecializationCategory(key, label, tuple(Specialization(k, v) for k, v in items))


CATEGORIES: tuple[SpecializationCategory, ...] = (
    _category(
        "CS_IT",
        "Computer Science & Information Technology",
        ("COMPUTER_SCIENCE_ENGINEERING", "Computer Science and Engineering"),
        ("INFORMATION_TECHNOLOGY", "Information Technology"),
        ("AI_ML", "Artificial Intelligence and Machine Learning"),
        ("DATA_SCIENCE", "Data Science"),
        ("SOFTWARE_ENGINEERING", "Software Engineering"),
        ("CYBERSECURITY", "Cybersecurity"),
        ("COMPUTER_NETWORKS", "Computer Networks"),
        ("CLOUD_COMPUTING", "Cloud Computing"),
        ("COMPUTER_ENGINEERING", "Computer Engineering"),
    ),
    _category(
        "ELECTRONICS_ELECTRICAL",
        "Electronics & Electrical",
        ("ELECTRONICS_COMMUNICATION", "Electronics and Communication Engineering"),
        ("ELECTRICAL_ENGINEERING", "Electrical Engineering"),
        ("VLSI_DESIGN", "VLSI Design"),
        ("MICROELECTRONICS", "Microelectronics"),
        ("SEMICONDUCTOR_TECHNOLOGY", "Semiconductor Technology"),
        ("EMBEDDED_SYSTEMS", "Embedded Systems"),
        ("INSTRUMENTATION_CONTROL", "Instrumentation and Control Systems"),
        ("SIGNAL_PROCESSING", "Signal Processing"),
        ("POWER_SYSTEMS", "Power Systems"),
    ),
    _category(
        "MECHANICAL_MANUFACTURING",
        "Mechanical & Manufacturing",
        ("MECHANICAL_ENGINEERING", "Mechanical Engineering"),
        ("MECHATRONICS", "Mechatronics"),
        ("ROBOTICS_AUTOMATION", "Robotics and Automation"),
        ("AUTOMOTIVE_ENGINEERING", "Automotive Engineering"),
        ("AEROSPACE_ENGINEERING", "Aerospace Engineering"),
        ("MANUFACTURING_ENGINEERING", "Manufacturing Engineering"),
        ("PRODUCTION_ENGINEERING", "Production Engineering"),
        ("THERMAL_ENGINEERING", "Thermal Engineering"),
        ("INDUSTRIAL_ENGINEERING", "Industrial Engineering"),
    ),
    _category(
        "CIVIL_INFRASTRUCTURE",
        "Civil & Infrastructure",
        ("CIVIL_ENGINEERING", "Civil Engineering"),
        ("STRUCTURAL_ENGINEERING", "Structural Engineering"),
        ("CONSTRUCTION_TECHNOLOGY_MANAGEMENT", "Construction Technology and Management"),
        ("GEOTECHNICAL_ENGINEERING", "Geotechnical Engineering"),
        ("TRANSPORTATION_ENGINEERING", "Transportation Engineering"),
        ("ENVIRONMENTAL_ENGINEERING", "Environmental Engineering"),
        ("WATER_RESOURCES_ENGINEERING", "Water Resources Engineering"),
    ),
    _category(
        "CHEMICAL_MATERIALS",
        "Chemical & Materials",
        ("CHEMICAL_ENGINEERING", "Chemical Engineering"),
        ("MATERIALS_SCIENCE_ENGINEERING", "Materials Science and Engineering"),
        ("METALLURGICAL_ENGINEERING", "Metallurgical Engineering"),
        ("POLYMER_TECHNOLOGY", "Polymer Technology"),
        ("NANOTECHNOLOGY", "Nanotechnology"),
        ("PROCESS_ENGINEERING", "Process Engineering"),
    ),
    _category(
        "BIOTECH_BIOMEDICAL",
        "Biotechnology & Biomedical",
        ("BIOTECHNOLOGY", "Biotechnology"),
        ("BIOMEDICAL_ENGINEERING", "Biomedical Engineering"),
        ("BIOINFORMATICS", "Bioinformatics"),
        ("MEDICAL_TECHNOLOGY", "Medical Technology"),
        ("PHARMACEUTICAL_TECHNOLOGY", "Pharmaceutical Technology"),
        ("GENETIC_ENGINEERING", "Genetic Engineering"),
    ),
    _category(
        "OTHER_TECHNOLOGY",
        "Other Technology Domains",
        ("ENERGY_ENGINEERING", "Energy Engineering"),
        ("RENEWABLE_ENERGY", "Renewable Energy"),
        ("AGRICULTURAL_ENGINEERING", "Agricultural Engineering"),
        ("FOOD_TECHNOLOGY", "Food Technology"),
        ("TEXTILE_TECHNOLOGY", "Textile Technology"),
        ("MINING_ENGINEERING", "Mining Engineering"),
        ("PETROLEUM_ENGINEERING", "Petroleum Engineering"),
        ("ENVIRONMENTAL_TECHNOLOGY", "Environmental Technology"),
    ),
)

BY_KEY: dict[str, SpecializationCategory] = {category.key: category for category in CATEGORIES}

#: Specialization keys are unique across categories (asserted below), so a
#: stored specialization key alone still identifies its label.
_SPECIALIZATION_LABELS: dict[str, str] = {
    s.key: s.label for category in CATEGORIES for s in category.specializations
}
assert len(_SPECIALIZATION_LABELS) == sum(len(c.specializations) for c in CATEGORIES), (
    "specialization keys must be unique across categories"
)


def get_category(key: str | None) -> SpecializationCategory | None:
    return BY_KEY.get(key) if key else None


def resolve(category_key: str | None, specialization_key: str | None) -> Specialization | None:
    """The specialization only when it belongs to that category; None for an
    unknown category, an unknown specialization, or one from another
    category."""
    category = get_category(category_key)
    return category.get(specialization_key) if category else None


def category_label(key: str | None) -> str | None:
    category = get_category(key)
    return category.label if category else key


def specialization_label(key: str | None) -> str | None:
    return _SPECIALIZATION_LABELS.get(key, key) if key else None


#: Other names a JD commonly uses for a specialization — abbreviations and
#: established equivalent wordings only. Matching against a JD's field of
#: study treats these exactly like the label. Deliberately left out:
#: ambiguous abbreviations ("ME" is also Master of Engineering, "CE" both
#: Civil and Computer Engineering, "DS") and merely *related* fields
#: (Aeronautical vs Aerospace) — those are flagged for HR, never guessed.
EQUIVALENT_NAMES: dict[str, tuple[str, ...]] = {
    "COMPUTER_SCIENCE_ENGINEERING": (
        "CSE",
        "CS",
        "Computer Science",
        "Computer Sciences",
        "Computer Science Engineering",
        "Comp Sci",
    ),
    "INFORMATION_TECHNOLOGY": ("IT",),
    "AI_ML": (
        "AI",
        "ML",
        "AIML",
        "AI ML",
        "AI and ML",
        "Artificial Intelligence",
        "Machine Learning",
    ),
    "CYBERSECURITY": ("Cyber Security", "Information Security"),
    "ELECTRONICS_COMMUNICATION": (
        "ECE",
        "EC",
        "Electronics and Communication",
        "Electronics and Communications Engineering",
        "Electronics and Telecommunication Engineering",
        "EnTC",
        "EXTC",
    ),
    "ELECTRICAL_ENGINEERING": ("EE", "EEE", "Electrical and Electronics Engineering"),
    "VLSI_DESIGN": ("VLSI",),
    "INSTRUMENTATION_CONTROL": ("Instrumentation Engineering", "Control Systems"),
    "MECHANICAL_ENGINEERING": ("Mech",),
    "BIOTECHNOLOGY": ("Biotech",),
}
assert set(EQUIVALENT_NAMES) <= set(_SPECIALIZATION_LABELS), "unknown key in EQUIVALENT_NAMES"

#: Words that say nothing about the discipline on their own. A JD field made
#: only of these ("Engineering", "Science", "Technology", "a related
#: field") is too broad to match automatically — HR decides. (Normalized
#: forms: singular.)
_GENERIC_WORDS = frozenset(
    {
        "engineering",
        "science",
        "technology",
        "system",
        "design",
        "management",
        "any",
        "a",
        "related",
        "relevant",
        "allied",
        "equivalent",
        "field",
        "branch",
        "branche",
        "discipline",
        "degree",
        "other",
    }
)
_STOP_WORDS = frozenset({"and", "of", "the", "in"})
_ALTERNATIVE_SEPARATORS = re.compile(r"\s*(?:/|,|;|\bor\b)\s*", re.IGNORECASE)


def _normalize(text: str) -> str:
    """Case-, punctuation- and wording-insensitive form: "&" = "and",
    "Engg." = "Engineering", stop words dropped, a simple plural "s"
    dropped, and spelled-out initials joined ("C.S.E." = "CSE")."""
    text = text.casefold().replace("&", " and ")
    tokens: list[str] = []
    initials = ""
    for token in re.findall(r"[a-z0-9]+", text):
        if len(token) == 1 and token.isalpha():
            initials += token
            continue
        if initials:
            tokens.append(initials)
            initials = ""
        tokens.append(token)
    if initials:
        tokens.append(initials)
    words: list[str] = []
    for token in tokens:
        if token in ("engg", "engr"):
            token = "engineering"
        if token in _STOP_WORDS:
            continue
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        words.append(token)
    return " ".join(words)


def _alternatives(field_of_study: str) -> list[str]:
    """ "Computer Science / IT or related field" -> its alternatives."""
    return [part for part in _ALTERNATIVE_SEPARATORS.split(field_of_study) if part.strip()]


@dataclass(frozen=True)
class FieldMatch:
    matched: bool
    #: The JD's field of study that matched (as the JD states it).
    field_of_study: str | None = None


def _catalog_key_for(text: str) -> str | None:
    """The catalogue specialization a free-text value names — its label or
    one of its EQUIVALENT_NAMES, compared normalized ("Computer Science &
    Engineering" = "Computer Science and Engineering", "CSE") — or None."""
    wanted = _normalize(text)
    if not wanted:
        return None
    for key, label in _SPECIALIZATION_LABELS.items():
        if _normalize(label) == wanted or any(
            _normalize(name) == wanted for name in EQUIVALENT_NAMES.get(key, ())
        ):
            return key
    return None


def match_field_of_study(specialization: str | None, fields_of_study: list[str]) -> FieldMatch:
    """Deterministic check of a specialization against a JD's fields of
    study — never an AI call. `specialization` is the candidate's text, or
    a catalogue key on records stored before free-text entry. A catalogue
    specialization (by key, or text naming it) is known by its label and
    EQUIVALENT_NAMES; any other text by itself. A JD field (or one of its
    "/", ",", "or" alternatives) matches when, after normalization, it
    equals one of those names, or one contains the other as whole words
    ("Computer Science" matches "Computer Science and Engineering", "B.Tech
    in CSE" matches CSE). Generic words alone ("Engineering", "a related
    field") never match, on either side. No match means the application is
    flagged for HR to check — never refused."""
    if not specialization or not specialization.strip():
        return FieldMatch(False)
    key = (
        specialization
        if specialization in _SPECIALIZATION_LABELS
        else _catalog_key_for(specialization)
    )
    if key is not None:
        names = {_normalize(_SPECIALIZATION_LABELS[key])} | {
            _normalize(n) for n in EQUIVALENT_NAMES.get(key, ())
        }
    else:
        names = {_normalize(specialization)}
    padded_names = [
        f" {name} " for name in names if name and not set(name.split()) <= _GENERIC_WORDS
    ]
    if not padded_names:
        return FieldMatch(False)
    for field_of_study in fields_of_study:
        for alternative in _alternatives(field_of_study):
            required = _normalize(alternative)
            if not required or set(required.split()) <= _GENERIC_WORDS:
                continue
            padded = f" {required} "
            if any(padded in name or name in padded for name in padded_names):
                return FieldMatch(True, field_of_study)
    return FieldMatch(False)
