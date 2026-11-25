import type { EducationDetails, SpecializationCategoryView } from "../../../types/applyFlow";

export interface EducationValues {
  qualification_key: string;
  degree_name: string;
  /** Keys into the server's specialization catalogue. */
  specialization_category: string;
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

/** The category and specialization labels for stored keys (null when a key
 * isn't in the catalogue). */
export function specializationLabels(
  categories: SpecializationCategoryView[] | undefined,
  categoryKey: string | null | undefined,
  specializationKey: string | null | undefined,
): { category: string | null; specialization: string | null } {
  const category = categories?.find((item) => item.key === categoryKey);
  const specialization = category?.specializations.find((item) => item.key === specializationKey);
  return { category: category?.label ?? null, specialization: specialization?.label ?? null };
}
