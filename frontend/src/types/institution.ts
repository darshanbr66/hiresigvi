/** Mirrors backend/app/schemas/institution.py. */

export type InstitutionType = "UNIVERSITY" | "COLLEGE" | "INSTITUTE" | "OTHER";

export const INSTITUTION_TYPE_LABELS: Record<InstitutionType, string> = {
  UNIVERSITY: "University",
  COLLEGE: "College",
  INSTITUTE: "Institute",
  OTHER: "Other",
};

export interface InstitutionFields {
  institution_type?: InstitutionType | null;
  city?: string | null;
  state?: string | null;
  country?: string | null;
  website?: string | null;
  /** Optional official student email domains, e.g. "college.edu". */
  email_domains?: string[] | null;
  placement_contact_name?: string | null;
  placement_contact_email?: string | null;
  placement_contact_phone?: string | null;
  notes?: string | null;
}

export interface InstitutionCreateRequest extends InstitutionFields {
  name: string;
}

export interface InstitutionUpdateRequest extends InstitutionFields {
  name?: string;
  is_active?: boolean;
}

export interface InstitutionResponse {
  id: string;
  name: string;
  institution_type: InstitutionType | null;
  city: string | null;
  state: string | null;
  country: string | null;
  website: string | null;
  email_domains: string[];
  placement_contact_name: string | null;
  placement_contact_email: string | null;
  placement_contact_phone: string | null;
  notes: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  campus_drive_count: number;
}

/** The public shape (name and location only). */
export interface InstitutionSummary {
  id: string;
  name: string;
  city: string | null;
  state: string | null;
}
