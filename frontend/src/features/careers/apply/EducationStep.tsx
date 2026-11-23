import { useQuery } from "@tanstack/react-query";
import { useId, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { Icon } from "../../../shared/components/Icon";
import { Spinner } from "../../../shared/components/Spinner";
import type { DraftAuth, DraftView, EducationCheckStatus } from "../../../types/applyFlow";
import {
  getQualificationOptions,
  recheckAcademicDocument,
  requestEducationManualReview,
  saveDraftEducation,
  uploadAcademicDocument,
} from "../api";
import { toDetails, type EducationValues } from "./educationValues";
import { fieldAria } from "./fieldAria";
import { FormField } from "./formFields";
import {
  completionYearError,
  DOCUMENT_ACCEPT,
  documentFileError,
  formatFileSize,
  MAX_MARKS_CARD_MB,
  tenthMarksError,
} from "./validation";

const STATUS_TONE: Record<EducationCheckStatus, "success" | "warning" | "error" | "info"> = {
  NOT_STARTED: "info",
  VERIFIED: "success",
  MANUAL_REVIEW: "info",
  UNCLEAR: "warning",
  MISMATCH: "error",
  FAILED: "error",
};

const STATUS_TITLE: Record<EducationCheckStatus, string> = {
  NOT_STARTED: "Document not checked yet",
  VERIFIED: "Document checked",
  MANUAL_REVIEW: "Document sent for manual review",
  UNCLEAR: "We couldn't confirm your document",
  MISMATCH: "Document doesn't match",
  FAILED: "Check not completed",
};

type Phase = { kind: "idle" } | { kind: "uploading"; progress: number } | { kind: "checking" };

/**
 * Step 4: the qualification — offered only from what this job's JD
 * accepts — its details, and the mandatory academic document, which the
 * server checks with AI for consistency with the selected qualification.
 * The outcome comes only from the server: the candidate continues when the
 * check verified the document, or when the server allowed manual review.
 */
export function EducationStep({
  base,
  auth,
  draft,
  onDraft,
  values,
  onChange,
  marksCard,
  onMarksCard,
  onBack,
  onContinue,
  onRequestError,
}: {
  base: string;
  auth: DraftAuth;
  draft: DraftView;
  onDraft: (draft: DraftView) => void;
  values: EducationValues;
  onChange: (values: EducationValues) => void;
  marksCard: File | null;
  onMarksCard: (file: File | null) => void;
  onBack: () => void;
  onContinue: () => void;
  /** Lets the wizard handle an expired verification; true = handled. */
  onRequestError: (err: unknown) => boolean;
}) {
  const ids = {
    qualification: useId(),
    degree: useId(),
    branch: useId(),
    institution: useId(),
    year: useId(),
    marks: useId(),
    marksCard: useId(),
    document: useId(),
  };
  const documentInput = useRef<HTMLInputElement>(null);
  const marksCardInput = useRef<HTMLInputElement>(null);
  const [errors, setErrors] = useState<Partial<Record<keyof EducationValues, string>>>({});
  const [documentProblem, setDocumentProblem] = useState<string | null>(null);
  const [marksCardProblem, setMarksCardProblem] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  const optionsQuery = useQuery({
    queryKey: ["qualification-options", base],
    queryFn: () => getQualificationOptions(base),
    staleTime: 5 * 60_000,
    retry: 1,
  });
  const options = optionsQuery.data;
  const selected = options?.options.find((option) => option.key === values.qualification_key);
  const check = draft.education_check;
  const busy = phase.kind !== "idle";
  const savedKey = draft.education?.qualification_key ?? null;
  const savedBranch = (draft.education?.branch ?? "").trim().toLowerCase();
  const changedSinceCheck =
    draft.education !== null &&
    (savedKey !== values.qualification_key || savedBranch !== values.branch.trim().toLowerCase());
  const canContinue = check.can_continue && !changedSinceCheck;

  function set<K extends keyof EducationValues>(key: K, value: EducationValues[K]) {
    setErrors((current) => ({ ...current, [key]: undefined }));
    onChange({ ...values, [key]: value });
  }

  function validate(): boolean {
    const next: Partial<Record<keyof EducationValues, string>> = {};
    if (!selected) next.qualification_key = "Select your qualification.";
    if (selected?.requires_degree_name && !values.degree_name.trim()) {
      next.degree_name = "Enter the name of your degree.";
    }
    if (selected?.branch_required && !values.branch.trim()) {
      next.branch = `Enter your ${selected.branch_label.toLowerCase()}.`;
    }
    if (!values.institution.trim()) next.institution = "Enter your institution or university.";
    const yearProblem = completionYearError(values.completion_year);
    if (yearProblem) next.completion_year = yearProblem;
    const marksProblem = tenthMarksError(values.tenth_marks_percentage);
    if (marksProblem) next.tenth_marks_percentage = marksProblem;
    setErrors(next);
    return Object.keys(next).length === 0 && !marksCardProblem;
  }

  function fail(err: unknown, fallback: string) {
    setPhase({ kind: "idle" });
    if (onRequestError(err)) return;
    setFormError(err instanceof ApiError ? err.message : fallback);
  }

  async function saveDetails(): Promise<DraftView | null> {
    try {
      const saved = await saveDraftEducation(base, auth, toDetails(values));
      onDraft(saved);
      return saved;
    } catch (err) {
      fail(err, "We couldn't save your education details. Please try again.");
      return null;
    }
  }

  async function uploadDocument(file: File) {
    setFormError(null);
    const problem = documentFileError(file, options?.max_document_size_mb ?? 5, "document");
    setDocumentProblem(problem);
    if (documentInput.current) documentInput.current.value = "";
    if (problem || !validate()) return;
    setPhase({ kind: "uploading", progress: 0 });
    if (!(await saveDetails())) return;
    try {
      const result = await uploadAcademicDocument(base, auth, file, (fraction) =>
        setPhase(fraction >= 1 ? { kind: "checking" } : { kind: "uploading", progress: fraction }),
      );
      onDraft(result);
      setPhase({ kind: "idle" });
    } catch (err) {
      fail(err, "We couldn't upload your document. Please try again.");
    }
  }

  async function recheck() {
    setFormError(null);
    setPhase({ kind: "checking" });
    try {
      onDraft(await recheckAcademicDocument(base, auth));
      setPhase({ kind: "idle" });
    } catch (err) {
      fail(err, "We couldn't check your document. Please try again.");
    }
  }

  async function manualReview() {
    setFormError(null);
    try {
      onDraft(await requestEducationManualReview(base, auth));
    } catch (err) {
      fail(err, "Please try again.");
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    if (!validate()) return;
    if (!draft.document) {
      setDocumentProblem("Upload your academic document to continue.");
      return;
    }
    setPhase({ kind: "checking" });
    const saved = await saveDetails();
    if (!saved) return;
    if (saved.education_check.can_continue) {
      setPhase({ kind: "idle" });
      onContinue();
      return;
    }
    if (saved.education_check.status === "NOT_STARTED" && saved.document) {
      // The qualification or branch changed: check the same document again.
      try {
        const rechecked = await recheckAcademicDocument(base, auth);
        onDraft(rechecked);
        setPhase({ kind: "idle" });
        if (rechecked.education_check.can_continue) onContinue();
      } catch (err) {
        fail(err, "We couldn't check your document. Please try again.");
      }
      return;
    }
    setPhase({ kind: "idle" });
    setFormError("Your academic document needs to be verified before you continue.");
  }

  function selectMarksCard(file: File | null) {
    const problem = file ? documentFileError(file, MAX_MARKS_CARD_MB, "10th marks card") : null;
    setMarksCardProblem(problem);
    onMarksCard(file && !problem ? file : null);
    if (problem && marksCardInput.current) marksCardInput.current.value = "";
  }

  if (optionsQuery.isPending) {
    return (
      <p className="apply-loading" role="status">
        <Spinner /> Loading the qualifications for this role…
      </p>
    );
  }
  if (optionsQuery.isError || !options) {
    return (
      <div className="apply-step-body">
        <Alert>We couldn't load the qualifications for this role.</Alert>
        <div className="apply-actions">
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            <Icon name="arrow-left" size={16} /> Back
          </button>
          <button type="button" className="btn btn-primary" onClick={() => optionsQuery.refetch()}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (options.options.length === 0) {
    // The role accepts only qualifications this form doesn't offer; the
    // server would refuse any selection, so there's nothing to choose.
    return (
      <div className="apply-step-body">
        <Alert>{options.summary}</Alert>
        <div className="apply-actions">
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            <Icon name="arrow-left" size={16} /> Back
          </button>
        </div>
      </div>
    );
  }

  const branchHint = options.field_mandatory
    ? "This role requires one of the listed fields."
    : undefined;
  const tone = changedSinceCheck ? "info" : STATUS_TONE[check.status];

  return (
    <form className="apply-step-body" onSubmit={handleSubmit} noValidate>
      <p className="apply-eligibility" id={`${ids.qualification}-eligibility`}>
        <Icon name="institution" size={16} />
        <span>{options.summary}</span>
      </p>

      <fieldset className="apply-fieldset">
        <legend>Qualification</legend>
        <FormField
          id={ids.qualification}
          label="Highest qualification"
          message={errors.qualification_key}
        >
          <select
            {...fieldAria(ids.qualification, errors.qualification_key)}
            required
            value={values.qualification_key}
            onChange={(e) => set("qualification_key", e.target.value)}
            disabled={busy}
          >
            <option value="">Select…</option>
            {options.options.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
        </FormField>

        {selected?.requires_degree_name && (
          <FormField id={ids.degree} label="Degree name" message={errors.degree_name}>
            <input
              {...fieldAria(ids.degree, errors.degree_name)}
              required
              maxLength={150}
              value={values.degree_name}
              onChange={(e) => set("degree_name", e.target.value)}
              placeholder="e.g. Bachelor of Design"
              disabled={busy}
            />
          </FormField>
        )}

        {selected && (
          <div className="field-row">
            <FormField
              id={ids.branch}
              label={selected.branch_label}
              optional={!selected.branch_required}
              hint={branchHint}
              message={errors.branch}
            >
              {options.field_mandatory && options.fields_of_study.length > 0 ? (
                <select
                  {...fieldAria(ids.branch, errors.branch, branchHint)}
                  required
                  value={values.branch}
                  onChange={(e) => set("branch", e.target.value)}
                  disabled={busy}
                >
                  <option value="">Select…</option>
                  {options.fields_of_study.map((field) => (
                    <option key={field} value={field}>
                      {field}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  {...fieldAria(ids.branch, errors.branch)}
                  required={selected.branch_required}
                  maxLength={150}
                  value={values.branch}
                  onChange={(e) => set("branch", e.target.value)}
                  placeholder="e.g. Mechanical Engineering"
                  disabled={busy}
                />
              )}
            </FormField>
            <FormField id={ids.year} label="Year of completion" message={errors.completion_year}>
              <input
                {...fieldAria(ids.year, errors.completion_year)}
                type="number"
                inputMode="numeric"
                required
                min={1960}
                max={new Date().getFullYear() + 6}
                value={values.completion_year}
                onChange={(e) => set("completion_year", e.target.value)}
                placeholder={String(new Date().getFullYear())}
                disabled={busy}
              />
            </FormField>
          </div>
        )}
        {selected && (
          <FormField
            id={ids.institution}
            label="Institution / university"
            message={errors.institution}
          >
            <input
              {...fieldAria(ids.institution, errors.institution)}
              required
              maxLength={255}
              value={values.institution}
              onChange={(e) => set("institution", e.target.value)}
              autoComplete="organization"
              disabled={busy}
            />
          </FormField>
        )}
      </fieldset>

      {selected && (
        <fieldset className="apply-fieldset">
          <legend>Supporting document</legend>
          <p className="apply-doc-hint" id={`${ids.document}-hint`}>
            Upload {selected.document_hint} for <strong>{selected.label}</strong>.{" "}
            <span className="muted">
              {options.accepted_document_types.join(", ")}, up to {options.max_document_size_mb} MB.
            </span>
          </p>

          {draft.document && (
            <div className="file-selected">
              <Icon name="notes" size={14} />
              <span className="file-selected-name" title={draft.document.filename}>
                {draft.document.filename}
              </span>
              <span className="file-selected-size">{formatFileSize(draft.document.size_bytes)}</span>
            </div>
          )}

          <div className="apply-upload">
            <input
              ref={documentInput}
              id={ids.document}
              className="sr-only"
              type="file"
              accept={DOCUMENT_ACCEPT}
              aria-label={draft.document ? "Replace academic document" : "Academic document"}
              aria-describedby={`${ids.document}-hint`}
              disabled={busy}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void uploadDocument(file);
              }}
            />
            <label htmlFor={ids.document} className="btn btn-ghost apply-upload-btn">
              <Icon name="plus" size={14} />
              {draft.document ? "Replace document" : "Upload document"}
            </label>
          </div>
          {documentProblem && (
            <p className="field-message" role="alert">
              {documentProblem}
            </p>
          )}

          {phase.kind === "uploading" && (
            <div className="apply-upload-progress" role="status">
              <span>Uploading… {Math.round(phase.progress * 100)}%</span>
              <progress max={1} value={phase.progress} aria-label="Upload progress" />
            </div>
          )}
          {phase.kind === "checking" && (
            <p className="apply-processing" role="status" aria-live="polite">
              <span className="spinner" aria-hidden="true" />
              Checking your document against the qualification you selected. This can take up to
              a minute.
            </p>
          )}

          {phase.kind === "idle" && draft.document && (
            <div className={`apply-check apply-check-${tone}`} role="status" aria-live="polite">
              <p className="apply-check-title">
                <Icon
                  name={tone === "success" ? "check" : tone === "info" ? "clock" : "x"}
                  size={15}
                />
                {changedSinceCheck ? "Details changed" : STATUS_TITLE[check.status]}
              </p>
              <p>
                {changedSinceCheck
                  ? "You changed your qualification. Your document will be checked again when you continue."
                  : (check.message ?? "Your document will be checked when you continue.")}
              </p>
              {!changedSinceCheck && (check.can_retry || check.can_request_manual_review) && (
                <div className="apply-check-actions">
                  {check.can_retry && check.status === "FAILED" && (
                    <button type="button" className="btn btn-ghost btn-sm" onClick={recheck}>
                      Try again
                    </button>
                  )}
                  {check.can_request_manual_review && (
                    <button type="button" className="btn btn-ghost btn-sm" onClick={manualReview}>
                      Continue with manual review
                    </button>
                  )}
                </div>
              )}
              {check.status === "VERIFIED" && !changedSinceCheck && (
                <p className="apply-check-note muted">
                  This is an automated consistency check, not a verification of authenticity.
                </p>
              )}
            </div>
          )}
        </fieldset>
      )}

      <fieldset className="apply-fieldset">
        <legend>10th standard</legend>
        <div className="field-row">
          <FormField
            id={ids.marks}
            label="10th marks"
            optional
            hint="Percentage, 0–100."
            message={errors.tenth_marks_percentage}
          >
            <input
              {...fieldAria(ids.marks, errors.tenth_marks_percentage, "Percentage, 0–100.")}
              inputMode="decimal"
              value={values.tenth_marks_percentage}
              onChange={(e) => set("tenth_marks_percentage", e.target.value)}
              placeholder="e.g. 87.5"
              disabled={busy}
            />
          </FormField>
          <FormField
            id={ids.marksCard}
            label="10th marks card"
            optional
            hint={`PDF, PNG or JPG, up to ${MAX_MARKS_CARD_MB} MB. Uploaded when you submit.`}
            message={marksCardProblem}
          >
            {marksCard ? (
              <div className="file-selected">
                <span className="file-selected-name" title={marksCard.name}>
                  {marksCard.name}
                </span>
                <span className="file-selected-size">{formatFileSize(marksCard.size)}</span>
                <button
                  type="button"
                  className="chip-remove"
                  onClick={() => selectMarksCard(null)}
                  aria-label={`Remove ${marksCard.name}`}
                >
                  <Icon name="x" size={12} />
                </button>
              </div>
            ) : (
              <input
                ref={marksCardInput}
                {...fieldAria(ids.marksCard, marksCardProblem)}
                type="file"
                accept={DOCUMENT_ACCEPT}
                onChange={(e) => selectMarksCard(e.target.files?.[0] ?? null)}
              />
            )}
          </FormField>
        </div>
      </fieldset>

      {formError && <Alert>{formError}</Alert>}
      <div className="apply-actions">
        <button type="button" className="btn btn-ghost" onClick={onBack} disabled={busy}>
          <Icon name="arrow-left" size={16} /> Back
        </button>
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? <Spinner label="Please wait…" /> : canContinue ? "Continue" : "Save and continue"}
          {!busy && <Icon name="arrow-right" size={16} />}
        </button>
      </div>
    </form>
  );
}
