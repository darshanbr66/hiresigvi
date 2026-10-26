import { useQuery } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/apiClient";
import { Alert } from "../../shared/components/Alert";
import { PublicHeader } from "../public/PublicHeader";
import {
  EDUCATION_LEVEL_LABELS,
  type EducationLevel,
  type ProgramDeclaration,
  type PublicProgramView,
} from "../../types/program";
import { getPublicProgram } from "./api";
import { JobApplicationForm } from "./JobApplicationForm";

const EMPTY_DECLARATION: ProgramDeclaration = {
  education_level: "",
  study_year: "",
  specialization: "",
  institution_id: "",
  institution_name: "",
  expected_completion_date: "",
  terms_version: "",
  terms_accepted: false,
};

function money(amount: string | null, currency: string | null): string | null {
  if (!amount || !currency) return null;
  return `${currency} ${Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card stack-sm" style={{ marginBottom: "1rem" }}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

/** What the candidate's declaration still lacks for this program's
 * configured criteria — a convenience only; the server checks it all. */
function declarationProblems(program: PublicProgramView, d: ProgramDeclaration): string[] {
  const problems: string[] = [];
  if (!d.education_level) problems.push("Choose your current education level.");
  else if (
    program.eligible_education_levels.length > 0 &&
    !program.eligible_education_levels.includes(d.education_level)
  ) {
    problems.push("This program isn't open to that education level.");
  }
  if (program.eligible_study_years.length > 0) {
    if (!d.study_year) problems.push("Enter your current year of study.");
    else if (!program.eligible_study_years.includes(Number(d.study_year))) {
      problems.push(`This program is open to year ${program.eligible_study_years.join(" / ")} students.`);
    }
  }
  if (!d.specialization.trim()) problems.push("Enter your specialization.");
  if (program.eligible_institutions.length > 0 ? !d.institution_id : !d.institution_name.trim()) {
    problems.push("Tell us your institution.");
  }
  if ((program.expected_completion_from || program.expected_completion_to) && !d.expected_completion_date) {
    problems.push("Enter your expected completion date.");
  }
  if (!d.terms_accepted) problems.push("Read and accept the program terms.");
  return problems;
}

/**
 * The candidate-facing page of a recruitment program: everything the
 * business has approved for disclosure — eligibility, training duration
 * and fee, scholarship, commitment and employment pathway — shown before
 * the candidate agrees to anything. Amounts appear only once finalized;
 * otherwise only HR's approved wording. Applying uses the careers site's
 * form and identity rules, plus the education declaration and terms below.
 */
export function ProgramApplyPage() {
  const { programId = "" } = useParams<{ programId: string }>();
  const programQuery = useQuery({
    queryKey: ["public", "program", programId],
    queryFn: () => getPublicProgram(programId),
  });
  const [declaration, setDeclaration] = useState<ProgramDeclaration>(EMPTY_DECLARATION);
  const ids = { level: useId(), year: useId(), spec: useId(), inst: useId(), done: useId(), terms: useId() };

  const data = programQuery.data;
  const program = data?.kind === "program" ? data : null;

  function patch(fields: Partial<ProgramDeclaration>) {
    setDeclaration((current) => ({ ...current, ...fields }));
  }

  const effective: ProgramDeclaration = program
    ? { ...declaration, terms_version: program.terms_version }
    : declaration;
  const problems = program ? declarationProblems(program, effective) : [];

  return (
    <div>
      <PublicHeader title={program?.organization_name ?? "Careers"} />
      <div className="public-shell">
        {programQuery.isPending && <p role="status">Loading program details…</p>}
        {programQuery.isError && (
          <Alert>
            {programQuery.error instanceof ApiError ? programQuery.error.message : "This program could not be found."}
          </Alert>
        )}
        {data?.kind === "unavailable" && (
          <section className="card stack-sm" style={{ textAlign: "center" }}>
            <h1>This Program Is No Longer Accepting Applications</h1>
            <p className="muted">{data.message}</p>
            <Link to="/" className="btn btn-primary" style={{ alignSelf: "center" }}>
              Back to Careers
            </Link>
          </section>
        )}

        {program && (
          <>
            <h1>{program.title}</h1>
            <p className="muted">{program.organization_name}</p>
            <p className="job-description" style={{ marginBottom: "1.5rem" }}>
              {program.description}
            </p>

            <Section title="Who can apply">
              <ul>
                {program.eligible_education_levels.length > 0 && (
                  <li>
                    Education: {program.eligible_education_levels.map((l) => EDUCATION_LEVEL_LABELS[l]).join(", ")}
                  </li>
                )}
                {program.eligible_study_years.length > 0 && (
                  <li>Current year of study: {program.eligible_study_years.join(" or ")}</li>
                )}
                {program.eligible_specializations.length > 0 && (
                  <li>Specializations: {program.eligible_specializations.join(", ")}</li>
                )}
                {program.eligible_institutions.length > 0 && (
                  <li>Institutions: {program.eligible_institutions.map((i) => i.name).join(", ")}</li>
                )}
                {(program.expected_completion_from || program.expected_completion_to) && (
                  <li>
                    Expected completion:{" "}
                    {program.expected_completion_from ? `from ${program.expected_completion_from} ` : ""}
                    {program.expected_completion_to ? `until ${program.expected_completion_to}` : ""}
                  </li>
                )}
              </ul>
              {program.eligibility_notes && <p>{program.eligibility_notes}</p>}
            </Section>

            <Section title="Paid training">
              <p>
                Duration: {program.training_duration_options.map((m) => `${m} months`).join(" or ")}. The
                training fee is paid by the participant.
              </p>
              {money(program.training_fee_amount, program.training_fee_currency) && (
                <p>Training fee: {money(program.training_fee_amount, program.training_fee_currency)}</p>
              )}
              {program.fee_payment_terms && <p>Payment terms: {program.fee_payment_terms}</p>}
              {program.fee_disclosure && <p>{program.fee_disclosure}</p>}
            </Section>

            <Section title="Scholarship">
              {money(program.scholarship_amount_min, program.scholarship_currency) && (
                <p>
                  Scholarship: {money(program.scholarship_amount_min, program.scholarship_currency)}
                  {program.scholarship_amount_max && program.scholarship_amount_max !== program.scholarship_amount_min
                    ? ` to ${money(program.scholarship_amount_max, program.scholarship_currency)}`
                    : ""}
                </p>
              )}
              {program.scholarship_disclosure && <p>{program.scholarship_disclosure}</p>}
              {program.scholarship_eligibility_rules && <p>{program.scholarship_eligibility_rules}</p>}
              {program.scholarship_disbursement_timing && <p>Disbursement: {program.scholarship_disbursement_timing}</p>}
              <p className="field-hint">
                Being eligible for scholarship consideration is not the same as receiving a scholarship.
              </p>
            </Section>

            <Section title="Commitment and employment">
              {program.bond_duration_months && (
                <p>
                  Scholarship recipients agree to a {program.bond_duration_months}-month commitment under
                  the company's approved agreement.
                </p>
              )}
              {program.bond_terms_summary && <p>{program.bond_terms_summary}</p>}
              {program.employment_pathway && <p>{program.employment_pathway}</p>}
              <p className="field-hint">
                Passing the test, receiving a scholarship or completing training does not by itself
                guarantee employment.
              </p>
            </Section>

            {(program.has_assessment || program.interview_stages.length > 0 || program.required_documents.length > 0) && (
              <Section title="Selection process">
                <ul>
                  {program.has_assessment && <li>An online test, after your eligibility is reviewed.</li>}
                  {program.interview_stages.map((stage) => (
                    <li key={stage}>{stage}</li>
                  ))}
                </ul>
                {program.required_documents.length > 0 && (
                  <p>Documents you may be asked for: {program.required_documents.join(", ")}.</p>
                )}
              </Section>
            )}

            {!program.accepting_applications ? (
              <Alert>{program.not_accepting_reason ?? "This program is not currently accepting applications."}</Alert>
            ) : (
              <>
                <Section title="Your current education">
                  <div className="field-row">
                    <label className="field" htmlFor={ids.level}>
                      <span>Education level</span>
                      <select
                        id={ids.level}
                        required
                        value={declaration.education_level}
                        onChange={(e) => patch({ education_level: e.target.value as EducationLevel | "" })}
                      >
                        <option value="">Choose…</option>
                        {(Object.keys(EDUCATION_LEVEL_LABELS) as EducationLevel[]).map((level) => (
                          <option key={level} value={level}>
                            {EDUCATION_LEVEL_LABELS[level]}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field" htmlFor={ids.year}>
                      <span>
                        Current year of study{" "}
                        {program.eligible_study_years.length === 0 && <span className="optional-mark">(Optional)</span>}
                      </span>
                      <input
                        id={ids.year}
                        type="number"
                        min={1}
                        max={10}
                        value={declaration.study_year}
                        onChange={(e) => patch({ study_year: e.target.value })}
                        required={program.eligible_study_years.length > 0}
                      />
                    </label>
                  </div>
                  <label className="field" htmlFor={ids.spec}>
                    <span>Specialization / discipline</span>
                    {program.eligible_specializations.length > 0 ? (
                      <select
                        id={ids.spec}
                        required
                        value={declaration.specialization}
                        onChange={(e) => patch({ specialization: e.target.value })}
                      >
                        <option value="">Choose…</option>
                        {program.eligible_specializations.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        id={ids.spec}
                        required
                        value={declaration.specialization}
                        onChange={(e) => patch({ specialization: e.target.value })}
                        placeholder="e.g. Robotics"
                      />
                    )}
                  </label>
                  <label className="field" htmlFor={ids.inst}>
                    <span>Institution</span>
                    {program.eligible_institutions.length > 0 ? (
                      <select
                        id={ids.inst}
                        required
                        value={declaration.institution_id}
                        onChange={(e) => patch({ institution_id: e.target.value })}
                      >
                        <option value="">Choose…</option>
                        {program.eligible_institutions.map((i) => (
                          <option key={i.id} value={i.id}>
                            {i.name}
                            {i.city ? ` — ${i.city}` : ""}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        id={ids.inst}
                        required
                        value={declaration.institution_name}
                        onChange={(e) => patch({ institution_name: e.target.value })}
                      />
                    )}
                  </label>
                  <label className="field" htmlFor={ids.done}>
                    <span>
                      Expected completion date{" "}
                      {!(program.expected_completion_from || program.expected_completion_to) && (
                        <span className="optional-mark">(Optional)</span>
                      )}
                    </span>
                    <input
                      id={ids.done}
                      type="date"
                      value={declaration.expected_completion_date}
                      onChange={(e) => patch({ expected_completion_date: e.target.value })}
                    />
                  </label>
                  <p className="field-hint">
                    Your eligibility is reviewed by our team; what you declare here may be verified.
                  </p>
                </Section>

                <Section title={`Program terms (version ${program.terms_version})`}>
                  <pre
                    tabIndex={0}
                    aria-label="Program terms"
                    style={{ whiteSpace: "pre-wrap", maxHeight: "18rem", overflowY: "auto", margin: 0 }}
                  >
                    {program.disclosure_text}
                  </pre>
                  <label htmlFor={ids.terms} style={{ display: "flex", gap: "0.5rem", alignItems: "flex-start" }}>
                    <input
                      id={ids.terms}
                      type="checkbox"
                      checked={declaration.terms_accepted}
                      onChange={(e) => patch({ terms_accepted: e.target.checked })}
                      style={{ width: "auto", marginTop: "0.2rem" }}
                    />
                    I have read and accept these program terms.
                  </label>
                </Section>

                {problems.length > 0 && (
                  <ul className="field-hint" aria-live="polite">
                    {problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                )}

                <JobApplicationForm
                  program={{ id: program.id, declaration: effective, ready: problems.length === 0 }}
                  title="Apply for this program"
                  contactEmail={program.careers_contact_email}
                />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
