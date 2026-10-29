import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
  APPROVED_TRAINING_DURATIONS,
  EDUCATION_LEVEL_LABELS,
  FUNDING_TYPE_LABELS,
  PROGRAM_AFFILIATION_METHODS,
  type AffiliationMethod,
  type EducationLevel,
  type FundingType,
  type ProgramResponse,
  type ProgramUpdateRequest,
} from "../../../types/program";
import { splitFee } from "./finance";
import { listAssessments } from "../assessments/api";
import { listInstitutions } from "../institutions/api";

/** Everything in the form is text so inputs stay controlled; converted to
 * the API shape by `toProgramPayload`. Empty = "not set yet" (never a
 * made-up default). */
export interface ProgramFormValue {
  title: string;
  description: string;
  eligibleEducationLevels: EducationLevel[];
  eligibleStudyYears: string;
  eligibleSpecializations: string;
  eligibleInstitutionIds: string[];
  expectedCompletionFrom: string;
  expectedCompletionTo: string;
  eligibilityNotes: string;
  trainingDurations: number[];
  trainingFeeAmount: string;
  trainingFeeCurrency: string;
  feePaymentTerms: string;
  feeDisclosure: string;
  scholarshipMin: string;
  scholarshipMax: string;
  scholarshipCurrency: string;
  scholarshipRules: string;
  scholarshipTiming: string;
  scholarshipDisclosure: string;
  employmentPathway: string;
  bondDurationMonths: string;
  bondTermsSummary: string;
  bondStartRule: string;
  opensAt: string;
  closesAt: string;
  assessmentId: string;
  interviewStages: string;
  requiredDocuments: string;
  candidateDisclosures: string;
  termsVersion: string;
  tracksFeePayments: boolean;
  tracksTraining: boolean;
  tracksEmployment: boolean;
  requiresAgreement: boolean;
  fundingType: FundingType | "";
  fundingValue: string;
  independentApproval: boolean;
  affiliationMethod: AffiliationMethod;
}

function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function programToFormValue(program: ProgramResponse): ProgramFormValue {
  return {
    title: program.title,
    description: program.description,
    eligibleEducationLevels: program.eligible_education_levels,
    eligibleStudyYears: program.eligible_study_years.join(", "),
    eligibleSpecializations: program.eligible_specializations.join("\n"),
    eligibleInstitutionIds: program.eligible_institutions.map((i) => i.id),
    expectedCompletionFrom: program.expected_completion_from ?? "",
    expectedCompletionTo: program.expected_completion_to ?? "",
    eligibilityNotes: program.eligibility_notes ?? "",
    trainingDurations: program.training_duration_options,
    trainingFeeAmount: program.training_fee_amount ?? "",
    trainingFeeCurrency: program.training_fee_currency ?? "",
    feePaymentTerms: program.fee_payment_terms ?? "",
    feeDisclosure: program.fee_disclosure ?? "",
    scholarshipMin: program.scholarship_amount_min ?? "",
    scholarshipMax: program.scholarship_amount_max ?? "",
    scholarshipCurrency: program.scholarship_currency ?? "",
    scholarshipRules: program.scholarship_eligibility_rules ?? "",
    scholarshipTiming: program.scholarship_disbursement_timing ?? "",
    scholarshipDisclosure: program.scholarship_disclosure ?? "",
    employmentPathway: program.employment_pathway ?? "",
    bondDurationMonths: program.bond_duration_months ? String(program.bond_duration_months) : "",
    bondTermsSummary: program.bond_terms_summary ?? "",
    bondStartRule: program.bond_start_rule ?? "",
    opensAt: toLocalInput(program.application_opens_at),
    closesAt: toLocalInput(program.application_closes_at),
    assessmentId: program.assessment_id ?? "",
    interviewStages: program.interview_stages.join("\n"),
    requiredDocuments: program.required_documents.join("\n"),
    candidateDisclosures: program.candidate_disclosures ?? "",
    termsVersion: program.terms_version ?? "",
    tracksFeePayments: program.tracks_fee_payments,
    tracksTraining: program.tracks_training,
    tracksEmployment: program.tracks_employment,
    requiresAgreement: program.requires_agreement,
    fundingType: program.company_funding_type ?? "",
    fundingValue: program.company_funding_value ?? "",
    independentApproval: program.funding_requires_independent_approval,
    affiliationMethod: program.affiliation_verification_method,
  };
}

const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);
const orNull = (text: string) => text.trim() || null;

export function toProgramPayload(value: ProgramFormValue): ProgramUpdateRequest {
  return {
    title: value.title.trim(),
    description: value.description.trim(),
    eligible_education_levels: value.eligibleEducationLevels,
    eligible_study_years: value.eligibleStudyYears
      .split(/[\s,]+/)
      .filter(Boolean)
      .map(Number)
      .filter((n) => Number.isInteger(n)),
    eligible_specializations: lines(value.eligibleSpecializations),
    eligible_institution_ids: value.eligibleInstitutionIds,
    expected_completion_from: orNull(value.expectedCompletionFrom),
    expected_completion_to: orNull(value.expectedCompletionTo),
    eligibility_notes: orNull(value.eligibilityNotes),
    training_duration_options: value.trainingDurations,
    training_fee_amount: orNull(value.trainingFeeAmount),
    training_fee_currency: orNull(value.trainingFeeCurrency),
    fee_payment_terms: orNull(value.feePaymentTerms),
    fee_disclosure: orNull(value.feeDisclosure),
    scholarship_amount_min: orNull(value.scholarshipMin),
    scholarship_amount_max: orNull(value.scholarshipMax),
    scholarship_currency: orNull(value.scholarshipCurrency),
    scholarship_eligibility_rules: orNull(value.scholarshipRules),
    scholarship_disbursement_timing: orNull(value.scholarshipTiming),
    scholarship_disclosure: orNull(value.scholarshipDisclosure),
    employment_pathway: orNull(value.employmentPathway),
    bond_duration_months: value.bondDurationMonths ? Number(value.bondDurationMonths) : null,
    bond_terms_summary: orNull(value.bondTermsSummary),
    bond_start_rule: orNull(value.bondStartRule),
    application_opens_at: value.opensAt ? new Date(value.opensAt).toISOString() : null,
    application_closes_at: value.closesAt ? new Date(value.closesAt).toISOString() : null,
    assessment_id: value.assessmentId || null,
    interview_stages: lines(value.interviewStages),
    required_documents: lines(value.requiredDocuments),
    candidate_disclosures: orNull(value.candidateDisclosures),
    terms_version: orNull(value.termsVersion),
    tracks_fee_payments: value.tracksFeePayments,
    tracks_training: value.tracksTraining,
    tracks_employment: value.tracksEmployment,
    requires_agreement: value.requiresAgreement,
    company_funding_type: value.fundingType || null,
    company_funding_value: value.fundingType ? orNull(value.fundingValue) : null,
    funding_requires_independent_approval: value.independentApproval,
    affiliation_verification_method: value.affiliationMethod,
  };
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <fieldset className="card stack-sm" style={{ background: "var(--color-bg)" }}>
      <legend style={{ fontWeight: 600 }}>{title}</legend>
      {hint && <p className="field-hint">{hint}</p>}
      {children}
    </fieldset>
  );
}

/** Program configuration. Any field may stay empty in a draft — the server
 * lists what still blocks publishing. Money values are left unset until
 * the business has finalized them; approved disclosure wording can be
 * published instead. */
export function ProgramForm({
  value,
  onChange,
  disabled,
  accessToken,
}: {
  value: ProgramFormValue;
  onChange: (value: ProgramFormValue) => void;
  disabled: boolean;
  accessToken: string;
}) {
  const institutionsQuery = useQuery({
    queryKey: ["recruiter", "institutions", "active"],
    queryFn: () => listInstitutions(accessToken, { activeOnly: true }),
  });
  const assessmentsQuery = useQuery({
    queryKey: ["recruiter", "assessments"],
    queryFn: () => listAssessments(accessToken),
  });

  function patch(fields: Partial<ProgramFormValue>) {
    onChange({ ...value, ...fields });
  }

  function text(key: keyof ProgramFormValue, label: string, options: { optional?: boolean; rows?: number; hint?: string; type?: string; placeholder?: string } = {}) {
    const current = value[key] as string;
    return (
      <label className="field">
        <span>
          {label} {options.optional !== false && <span className="optional-mark">(Optional in a draft)</span>}
        </span>
        {options.rows ? (
          <textarea
            rows={options.rows}
            value={current}
            onChange={(e) => patch({ [key]: e.target.value } as Partial<ProgramFormValue>)}
            disabled={disabled}
            placeholder={options.placeholder}
          />
        ) : (
          <input
            type={options.type ?? "text"}
            value={current}
            onChange={(e) => patch({ [key]: e.target.value } as Partial<ProgramFormValue>)}
            disabled={disabled}
            placeholder={options.placeholder}
          />
        )}
        {options.hint && <span className="field-hint">{options.hint}</span>}
      </label>
    );
  }

  function toggleLevel(level: EducationLevel) {
    patch({
      eligibleEducationLevels: value.eligibleEducationLevels.includes(level)
        ? value.eligibleEducationLevels.filter((l) => l !== level)
        : [...value.eligibleEducationLevels, level],
    });
  }

  function toggleDuration(months: number) {
    patch({
      trainingDurations: value.trainingDurations.includes(months)
        ? value.trainingDurations.filter((m) => m !== months)
        : [...value.trainingDurations, months].sort((a, b) => a - b),
    });
  }

  const flag = (
    key: "tracksFeePayments" | "tracksTraining" | "tracksEmployment" | "requiresAgreement" | "independentApproval",
    label: string,
  ) => (
    <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
      <input
        type="checkbox"
        checked={value[key]}
        onChange={(e) => patch({ [key]: e.target.checked } as Partial<ProgramFormValue>)}
        disabled={disabled}
        style={{ width: "auto" }}
      />
      {label}
    </label>
  );

  return (
    <div className="stack-lg">
      <Section title="Program">
        {text("title", "Title", { optional: false })}
        {text("description", "Description", { optional: false, rows: 4 })}
      </Section>

      <Section
        title="Eligibility"
        hint="Leave a criterion empty to accept everyone on that point. Candidates see these criteria before they apply, and the server checks them on every application."
      >
        <fieldset className="field">
          <legend>Eligible education levels</legend>
          <div className="btn-group">
            {(Object.keys(EDUCATION_LEVEL_LABELS) as EducationLevel[]).map((level) => (
              <label key={level} style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
                <input
                  type="checkbox"
                  checked={value.eligibleEducationLevels.includes(level)}
                  onChange={() => toggleLevel(level)}
                  disabled={disabled}
                  style={{ width: "auto" }}
                />
                {EDUCATION_LEVEL_LABELS[level]}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="field-row">
          {text("eligibleStudyYears", "Eligible year(s) of study", { placeholder: "e.g. 2", hint: "Comma-separated, e.g. 2 for second-year M.Tech." })}
          {text("expectedCompletionFrom", "Expected completion from", { type: "date" })}
          {text("expectedCompletionTo", "Expected completion to", { type: "date" })}
        </div>
        {text("eligibleSpecializations", "Eligible specializations / disciplines", { rows: 3, placeholder: "One per line, e.g. Robotics", hint: "Empty = any specialization." })}
        <label className="field">
          <span>
            Eligible institutions <span className="optional-mark">(Optional — empty = any institution)</span>
          </span>
          <select
            multiple
            size={Math.min(6, Math.max(3, institutionsQuery.data?.length ?? 3))}
            value={value.eligibleInstitutionIds}
            onChange={(e) =>
              patch({ eligibleInstitutionIds: Array.from(e.target.selectedOptions).map((o) => o.value) })
            }
            disabled={disabled}
          >
            {institutionsQuery.data?.map((institution) => (
              <option key={institution.id} value={institution.id}>
                {institution.name}
              </option>
            ))}
          </select>
          <span className="field-hint">Hold Ctrl (⌘ on Mac) to select several.</span>
        </label>
        {text("eligibilityNotes", "Other eligibility notes shown to candidates", { rows: 2 })}
        <label className="field">
          <span>Institution membership verification</span>
          <select
            value={value.affiliationMethod}
            onChange={(e) => patch({ affiliationMethod: e.target.value as AffiliationMethod })}
            disabled={disabled}
          >
            {PROGRAM_AFFILIATION_METHODS.map((m) => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </select>
          <span className="field-hint">
            A typed institution name is never treated as verified. Candidates can be marked eligible
            only after their membership is verified (unless set to not required).
          </span>
        </label>
      </Section>

      <Section title="Training and fee" hint="The training is paid by the candidate. Enter the fee only once it is finalized; until then publish only business-approved wording.">
        <fieldset className="field">
          <legend>Training duration</legend>
          <div className="btn-group">
            {APPROVED_TRAINING_DURATIONS.map((months) => (
              <label key={months} style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
                <input
                  type="checkbox"
                  checked={value.trainingDurations.includes(months)}
                  onChange={() => toggleDuration(months)}
                  disabled={disabled}
                  style={{ width: "auto" }}
                />
                {months} months
              </label>
            ))}
          </div>
        </fieldset>
        <div className="field-row">
          {text("trainingFeeAmount", "Training fee (finalized amount)", { type: "number" })}
          {text("trainingFeeCurrency", "Currency", { placeholder: "INR" })}
        </div>
        {text("feePaymentTerms", "Fee payment terms (finalized)", { rows: 2 })}
        {text("feeDisclosure", "Approved fee wording (when the amount isn't final)", { rows: 2, placeholder: "e.g. The training fee will be confirmed in writing before enrollment." })}
      </Section>

      <Section title="Scholarship / company funding" hint="Selected candidates may become eligible for company funding of their training fee. Eligibility, the funding proposal, its approval and the funding applied are recorded separately — passing the test or the interview never approves funding. The maximum below caps any single award.">
        <div className="field-row">
          {text("scholarshipMin", "Amount / minimum (finalized)", { type: "number" })}
          {text("scholarshipMax", "Maximum (finalized)", { type: "number" })}
          {text("scholarshipCurrency", "Currency", { placeholder: "INR" })}
        </div>
        {text("scholarshipRules", "Scholarship eligibility and approval rules", { rows: 2 })}
        {text("scholarshipTiming", "Disbursement timing (once finalized)", { rows: 2 })}
        {text("scholarshipDisclosure", "Approved scholarship wording (when the amount isn't final)", { rows: 2 })}
        <div className="field-row">
          <label className="field">
            <span>
              Standard company funding <span className="optional-mark">(Optional — not decided yet = leave empty)</span>
            </span>
            <select
              value={value.fundingType}
              onChange={(e) => patch({ fundingType: e.target.value as FundingType | "" })}
              disabled={disabled}
            >
              <option value="">Not configured</option>
              {(Object.keys(FUNDING_TYPE_LABELS) as FundingType[]).map((t) => (
                <option key={t} value={t}>{FUNDING_TYPE_LABELS[t]}</option>
              ))}
            </select>
          </label>
          {value.fundingType && text("fundingValue", value.fundingType === "PERCENTAGE" ? "Percentage of the fee" : "Amount (fee currency)", { type: "number" })}
        </div>
        {value.fundingType && value.fundingValue && value.trainingFeeAmount && (() => {
          const split = splitFee(value.trainingFeeAmount, value.fundingType, value.fundingValue);
          return typeof split === "string" ? (
            <p className="field-error" role="alert">{split}</p>
          ) : (
            <p className="field-hint" data-testid="funding-preview">
              Per candidate: company {split.companyContribution}, candidate pays {split.candidatePayable} of{" "}
              {split.fee} {value.trainingFeeCurrency}. Each award still needs an approved proposal.
            </p>
          );
        })()}
        {flag("independentApproval", "Funding must be approved by someone other than the person who proposed it")}
      </Section>

      <Section title="Employment and commitment" hint="Scholarship recipients agree to a commitment under the company's approved agreement. The platform tracks the agreement; it never writes the terms.">
        {text("employmentPathway", "Full-time employment pathway", { rows: 2 })}
        <div className="field-row">
          {text("bondDurationMonths", "Commitment (bond) duration in months", { type: "number", hint: "36 months by default; change only per approved terms." })}
        </div>
        {text("bondTermsSummary", "Approved summary of the commitment terms", { rows: 2 })}
        {text("bondStartRule", "When the commitment starts (once finalized)", { rows: 2 })}
      </Section>

      <Section title="Selection">
        <label className="field">
          <span>
            Selection test <span className="optional-mark">(Optional)</span>
          </span>
          <select value={value.assessmentId} onChange={(e) => patch({ assessmentId: e.target.value })} disabled={disabled}>
            <option value="">No test</option>
            {assessmentsQuery.data?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title}
                {a.status === "DRAFT" ? " (draft — publish it first)" : ""}
              </option>
            ))}
          </select>
          <span className="field-hint">With a test, a candidate must pass it before being shortlisted or interviewed.</span>
        </label>
        {text("interviewStages", "Interview stages", { rows: 2, placeholder: "One per line, e.g. Technical interview" })}
        {text("requiredDocuments", "Required documents", { rows: 2, placeholder: "One per line" })}
        <div className="field-row">
          {text("opensAt", "Applications open", { type: "datetime-local" })}
          {text("closesAt", "Applications close", { type: "datetime-local" })}
        </div>
      </Section>

      <Section title="Candidate terms" hint="Shown to candidates, who must accept them to apply. Once anyone has applied, changing what's disclosed requires a new version label.">
        {text("candidateDisclosures", "Approved terms and disclosures", { rows: 5 })}
        {text("termsVersion", "Terms version label", { placeholder: "e.g. 2026-10-v1" })}
      </Section>

      <Section title="Stages tracked for participants">
        {flag("tracksFeePayments", "Training-fee payments (recorded manually — no payment gateway)")}
        {flag("tracksTraining", "Training enrollment and progress")}
        {flag("tracksEmployment", "Full-time employment offer and joining")}
        {flag("requiresAgreement", "Agreement / bond for scholarship recipients")}
      </Section>
    </div>
  );
}
