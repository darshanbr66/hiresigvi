/** Mirrors backend/app/schemas/email.py. */

export type EmailTemplateKey =
  | "APPLICATION_RECEIVED"
  | "INTERVIEW_INVITATION"
  | "ASSESSMENT_INVITATION"
  | "NEXT_STEPS"
  | "REJECTION"
  | "SCHOLARSHIP_UPDATE"
  | "TRAINING_ENROLLMENT"
  | "GENERAL";

export type EmailFieldInputType = "text" | "textarea" | "date" | "time" | "select" | "url";

export interface EmailTemplateField {
  key: string;
  label: string;
  input_type: EmailFieldInputType;
  required: boolean;
  options: string[];
  placeholder: string;
}

export interface EmailTemplate {
  key: EmailTemplateKey;
  name: string;
  description: string;
  /** Inputs when sent for an application. */
  fields: EmailTemplateField[];
  /** Inputs when sent as a general email (recipient name / role must be typed);
   * null = needs an application and can't be sent that way. */
  general_fields: EmailTemplateField[] | null;
}

export interface EmailComposeRequest {
  template_key: EmailTemplateKey;
  variables: Record<string, string>;
}

export interface EmailComposeResponse {
  template_key: EmailTemplateKey;
  subject: string;
  body: string;
  variables: Record<string, string>;
  /** Required template fields still empty; their placeholders are visible in `body`. */
  missing_required: string[];
}

/** The email exactly as it is in the composer — used for preview and send. */
export interface EmailDraft {
  template_key: EmailTemplateKey;
  subject: string;
  body: string;
  variables: Record<string, string>;
}

export interface EmailPreview {
  to: string;
  reply_to: string;
  subject: string;
  html: string;
  text: string;
  has_call_to_action: boolean;
}

export interface EmailSendResult {
  sent: boolean;
  to: string;
  subject: string;
}

/** A general (non-application) email: the draft plus who it goes to. */
export interface GeneralEmailDraft extends EmailDraft {
  to: string[];
  cc: string[];
  bcc: string[];
}

export interface GeneralEmailPreview {
  to: string[];
  cc: string[];
  bcc: string[];
  reply_to: string;
  subject: string;
  html: string;
  text: string;
  has_call_to_action: boolean;
}

export interface GeneralEmailSendResult {
  sent: boolean;
  to: string[];
  cc: string[];
  bcc: string[];
  subject: string;
}

