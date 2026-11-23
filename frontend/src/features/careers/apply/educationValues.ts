import type { EducationDetails } from "../../../types/applyFlow";

export interface EducationValues {
  qualification_key: string;
  degree_name: string;
  branch: string;
  institution: string;
  completion_year: string;
  tenth_marks_percentage: string;
}

export const EMPTY_EDUCATION: EducationValues = {
  qualification_key: "",
  degree_name: "",
  branch: "",
  institution: "",
  completion_year: "",
  tenth_marks_percentage: "",
};

export function educationFromDraft(details: EducationDetails | null): EducationValues | null {
  if (!details) return null;
  return {
    qualification_key: details.qualification_key,
    degree_name: details.degree_name ?? "",
    branch: details.branch ?? "",
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
    branch: optional(values.branch),
    institution: values.institution.trim(),
    completion_year: Number(values.completion_year),
    tenth_marks_percentage: optional(values.tenth_marks_percentage),
  };
}
