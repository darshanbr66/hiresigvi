import type { EducationDetails, SpecializationCategoryView } from "../../../types/applyFlow";

/** Longest accepted values, in characters after trimming — the same limits
 * the server enforces (app/services/education/specializations.py). */
export const SPECIALIZATION_CATEGORY_MAX_LENGTH = 100;
export const SPECIALIZATION_MAX_LENGTH = 150;

export interface EducationValues {
  qualification_key: string;
  degree_name: string;
  /** What the candidate typed — any value; the catalogue only suggests. */
  specialization_category: string;
  /** Not entered in the form any more (the field was removed): empty for
   * new drafts; an older draft's value is carried through unchanged so it
   * isn't lost and doesn't reset that draft's document check. */
  specialization: string;
  institution: string;
  completion_year: string;
  tenth_marks_percentage: string;
}

export const EMPTY_EDUCATION: EducationValues = {
  qualification_key: "",
  degree_name: "",
  specialization_category: "",
  specialization: "",
  institution: "",
  completion_year: "",
  tenth_marks_percentage: "",
};

export function educationFromDraft(details: EducationDetails | null): EducationValues | null {
  if (!details) return null;
  return {
    qualification_key: details.qualification_key,
    degree_name: details.degree_name ?? "",
    specialization_category: details.specialization_category ?? "",
    specialization: details.specialization ?? "",
    institution: details.institution,
    completion_year: String(details.completion_year),
    tenth_marks_percentage:
      details.tenth_marks_percentage === null ? "" : String(details.tenth_marks_percentage),
  };
}

export function toDetails(values: EducationValues): EducationDetails {
  const optional = (value: string) => (value.trim() ? value.trim() : null);
  return {
    qualification_key: values.qualification_key,
    degree_name: optional(values.degree_name),
    specialization_category: optional(values.specialization_category),
    specialization: optional(values.specialization),
    // Derived by the server from the specialization.
    branch: null,
    institution: values.institution.trim(),
    completion_year: Number(values.completion_year),
    tenth_marks_percentage: optional(values.tenth_marks_percentage),
  };
}

/** Typing suggestions from the server's specialization catalogue — never a
 * restriction. Categories: every category label. Specializations: those of
 * the category the candidate typed, when it names one (any case), otherwise
 * every specialization. */
export function specializationSuggestions(
  categories: SpecializationCategoryView[] | undefined,
  typedCategory: string,
): { categories: string[]; specializations: string[] } {
  const all = categories ?? [];
  const wanted = typedCategory.trim().toLowerCase();
  const match = all.find((item) => item.label.toLowerCase() === wanted);
  const pool = match ? [match] : all;
  return {
    categories: all.map((item) => item.label),
    specializations: [...new Set(pool.flatMap((item) => item.specializations.map((s) => s.label)))],
  };
}
