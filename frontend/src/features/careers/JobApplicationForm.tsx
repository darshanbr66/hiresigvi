import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ApiError } from "../../lib/apiClient";
import { Alert } from "../../shared/components/Alert";
import { Icon } from "../../shared/components/Icon";
import { Spinner } from "../../shared/components/Spinner";
import type { ApplicantProfilePrefill, JobApplicationFormValues } from "../../types/careers";
import type { CandidateType } from "../../types/recruitment";
import {
  applyToCampusDrive,
  applyToJob,
  checkApplicantStatus,
  checkCampusApplicantStatus,
  requestCampusEmailCode,
  requestEmailCode,
  verifyCampusEmailCode,
  verifyEmailCode,
} from "./api";
import {
  AlreadyRegistered,
  ReapplyRestricted,
  ApplicationOutcome,
  type ApplicationOutcomeResult,
  UpdateInfoNote,
} from "./ApplicationOutcome";
import { LanguagesInput } from "./LanguagesInput";

const CANDIDATE_TYPE_LABELS: Record<CandidateType, string> = {
  FRESHER: "Fresher (no professional experience yet)",
  EXPERIENCED: "Experienced professional",
};

const MAX_RESUME_MB = 10;
const MIN_AGE_YEARS = 16;
/** The optional 10th marks card — the backend enforces the same rules
 * (app/services/candidate_document_service.py). */
const MAX_MARKS_CARD_MB = 5;
const MARKS_CARD_EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg"];
const MARKS_CARD_ACCEPT = ".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg";

type Step = "details" | "verify" | "resume" | "review";

const STEPS: { key: Step; label: string }[] = [
  { key: "details", label: "Your details" },
  { key: "verify", label: "Verify email" },
  { key: "resume", label: "Resume" },
  { key: "review", label: "Review & submit" },
];

const EMPTY_VALUES: Omit<JobApplicationFormValues, "candidate_type"> & {
  candidate_type: CandidateType | "";
} = {
  full_name: "",
  email: "",
  phone: "",
  date_of_birth: "",
  place_of_birth: "",
  languages: [],
  candidate_type: "",
  years_experience: "",
  notice_period_days: "",
  immediate_joiner: false,
  current_title: "",
  current_company: "",
  current_location: "",
  preferred_location: "",
  qualification: "",
  linkedin_url: "",
  github_url: "",
  tenth_marks_percentage: "",
};

type Values = typeof EMPTY_VALUES;

/** Shown — and Continue disabled — when the early check (or the server, at
 * any later step) says this email or mobile number has already applied. */
const ALREADY_APPLIED_MESSAGE = "You have already applied.";
/** The early check waits for typing to pause rather than calling the API on
 * every keystroke. */
const STATUS_CHECK_DEBOUNCE_MS = 400;

function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

function looksLikeMobile(value: string): boolean {
  const digits = value.replace(/\D/g, "").length;
  return digits >= 10 && digits <= 15;
}

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

/** A returning candidate's stored profile (received only after their code
 * was verified) fills in what they haven't typed — never overwrites it: what
 * they entered now is their current information. */
function prefillEmptyFields(current: Values, stored: ApplicantProfilePrefill): Values {
  const text = (value: string, fallback: string | null) => (value.trim() ? value : fallback ?? "");
  const number = (value: string, fallback: number | null) =>
    value.trim() ? value : fallback === null ? "" : String(fallback);
  return {
    ...current,
    full_name: text(current.full_name, stored.full_name),
    phone: text(current.phone, stored.phone),
    date_of_birth: text(current.date_of_birth, stored.date_of_birth),
    place_of_birth: text(current.place_of_birth, stored.place_of_birth),
    languages: current.languages.length > 0 ? current.languages : stored.languages,
    candidate_type: current.candidate_type || stored.candidate_type || "",
    years_experience: number(current.years_experience, stored.years_experience),
    notice_period_days: number(current.notice_period_days, stored.notice_period_days),
    current_title: text(current.current_title, stored.current_title),
    current_company: text(current.current_company, stored.current_company),
    current_location: text(current.current_location, stored.location),
    preferred_location: text(current.preferred_location, stored.preferred_location),
    qualification: text(current.qualification, stored.qualification),
    linkedin_url: text(current.linkedin_url, stored.linkedin_url),
    github_url: text(current.github_url, stored.github_url),
    tenth_marks_percentage: number(current.tenth_marks_percentage, stored.tenth_marks_percentage),
  };
}

/** A percentage between 0 and 100 with at most two decimals, or blank. */
function tenthMarksError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(trimmed) || Number(trimmed) > 100) {
    return "Enter a percentage between 0 and 100 (up to two decimals).";
  }
  return null;
}

function marksCardError(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!MARKS_CARD_EXTENSIONS.some((extension) => name.endsWith(extension))) {
    return "The marks card must be a PDF, PNG or JPG file.";
  }
  if (file.size === 0) return "The selected file is empty.";
  if (file.size > MAX_MARKS_CARD_MB * 1024 * 1024) {
    return `The marks card must be smaller than ${MAX_MARKS_CARD_MB} MB.`;
  }
  return null;
}

function formatFileSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function latestAllowedBirthDate(): string {
  const date = new Date();
  date.setFullYear(date.getFullYear() - MIN_AGE_YEARS);
  return date.toISOString().slice(0, 10);
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const detail = err.details[0];
    return err.code === "validation_error" && detail ? `${detail.field}: ${detail.message}` : err.message;
  }
  return fallback;
}

function RequiredMark({ optional }: { optional?: boolean }) {
  return optional ? (
    <span className="optional-mark">(Optional)</span>
  ) : (
    <span className="required-mark" aria-hidden="true">*</span>
  );
}

/** One labelled input, marked required (*) unless `optional`. */
function Field({
  label,
  children,
  hint,
  optional,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  optional?: boolean;
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label} <RequiredMark optional={optional} />
      </span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

/** A field whose inline message sits directly below its control — outside
 * the <label>, so the message never becomes part of the input's name; it is
 * linked with aria-describedby instead. The control gets `id`. */
function FieldWithMessage({
  id,
  label,
  optional,
  hint,
  message,
  messageId,
  children,
}: {
  id: string;
  label: string;
  optional?: boolean;
  hint?: string;
  message: string | null;
  messageId: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        {label} <RequiredMark optional={optional} />
      </label>
      {children}
      {message && (
        <p id={messageId} className="field-message" role="alert">
          {message}
        </p>
      )}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

function Stepper({ current }: { current: Step }) {
  const currentIndex = STEPS.findIndex((step) => step.key === current);
  return (
    <ol className="apply-stepper" aria-label="Application steps">
      {STEPS.map((step, index) => {
        const state = index < currentIndex ? "done" : index === currentIndex ? "current" : "todo";
        return (
          <li
            key={step.key}
            className={`apply-step apply-step-${state}`}
            aria-current={state === "current" ? "step" : undefined}
          >
            <span className="apply-step-node" aria-hidden="true">
              {state === "done" ? <Icon name="check" size={13} /> : index + 1}
            </span>
            <span className="apply-step-label">{step.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The self-service application, as a guided flow: details -> email
 * verification (one-time code) -> PDF resume -> review & submit. Every field
 * is mandatory except LinkedIn, GitHub and the 10th marks / marks card. The backend is authoritative for all of it (validation, the
 * verified email, duplicates, the AI screening) — the checks here only spare
 * the candidate a round trip. A candidate can apply once.
 *
 * Used for a careers-site job (`slug` + `jobId`) and for a campus drive's
 * public link (`campusToken`): both follow the same candidate identity rules.
 */
export function JobApplicationForm(
  props: { contactEmail?: string | null; title?: string } & (
    | { slug: string; jobId: string; campusToken?: undefined }
    | { campusToken: string; slug?: undefined; jobId?: undefined }
  ),
) {
  const { contactEmail = null, title = "Apply for this role" } = props;
  const [step, setStep] = useState<Step>("details");
  const [values, setValues] = useState<Values>(EMPTY_VALUES);
  const [resume, setResume] = useState<File | null>(null);
  const [marksCard, setMarksCard] = useState<File | null>(null);
  const [marksCardProblem, setMarksCardProblem] = useState<string | null>(null);
  const marksCardInput = useRef<HTMLInputElement>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const fieldIds = {
    email: useId(),
    emailMessage: useId(),
    phone: useId(),
    phoneMessage: useId(),
    marks: useId(),
    marksMessage: useId(),
    marksCard: useId(),
    marksCardMessage: useId(),
  };

  const [codeSentTo, setCodeSentTo] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [resendIn, setResendIn] = useState(0);
  const [verification, setVerification] = useState<{ email: string; token: string } | null>(null);
  const [alreadyRegistered, setAlreadyRegistered] = useState<string | null>(null);
  const [reapplyLocked, setReapplyLocked] = useState<{
    message: string;
    eligibleFrom: string | null;
  } | null>(null);
  const [result, setResult] = useState<ApplicationOutcomeResult | null>(null);

  const isExperienced = values.candidate_type === "EXPERIENCED";
  const verifiedEmail = verification?.email ?? null;
  const emailIsVerified = verifiedEmail !== null && verifiedEmail === values.email.trim().toLowerCase();

  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = window.setTimeout(() => setResendIn((s) => s - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [resendIn]);

  // The early "already applied?" check: once a plausible email or mobile
  // number has been typed (and typing has paused), ask the server — a plain
  // yes/no, nothing about the profile. The server enforces the same rule at
  // every later step on its own; this only stops the candidate up front.
  // The email and the mobile number are checked separately (same endpoint,
  // one identifier each), so the message can sit under the field that
  // actually caused the block.
  const normalizedEmail = values.email.trim().toLowerCase();
  const checkEmail = useDebouncedValue(
    looksLikeEmail(normalizedEmail) ? normalizedEmail : "",
    STATUS_CHECK_DEBOUNCE_MS,
  );
  const checkPhone = useDebouncedValue(
    looksLikeMobile(values.phone) ? values.phone.trim() : "",
    STATUS_CHECK_DEBOUNCE_MS,
  );
  const statusScope = props.campusToken ?? `${props.slug}/${props.jobId}`;
  function checkStatus(query: { email: string } | { phone: string }) {
    return props.campusToken !== undefined
      ? checkCampusApplicantStatus(props.campusToken, query)
      : checkApplicantStatus(props.slug, { ...query, job_id: props.jobId });
  }
  const emailStatusQuery = useQuery({
    queryKey: ["applicant-status", statusScope, "email", checkEmail],
    queryFn: () => checkStatus({ email: checkEmail }),
    enabled: checkEmail !== "",
    retry: false,
    staleTime: 30_000,
  });
  const phoneStatusQuery = useQuery({
    queryKey: ["applicant-status", statusScope, "phone", checkPhone],
    queryFn: () => checkStatus({ phone: checkPhone }),
    enabled: checkPhone !== "",
    retry: false,
    staleTime: 30_000,
  });
  // The server's own "already applied" answer at a later step (a code
  // request, verification or submission), for the email it was given.
  const [blockedEmail, setBlockedEmail] = useState<string | null>(null);
  const emailBlocked =
    (checkEmail !== "" && emailStatusQuery.data?.already_applied === true) ||
    (blockedEmail !== null && blockedEmail === normalizedEmail);
  const phoneBlocked = checkPhone !== "" && phoneStatusQuery.data?.already_applied === true;
  const alreadyApplied = emailBlocked || phoneBlocked;
  // One message, under the field that caused the block — under Email when
  // both did (both inputs then point at it), never twice.
  const emailMessageShown = emailBlocked;
  const phoneMessageShown = phoneBlocked && !emailBlocked;
  const blockMessageId = emailMessageShown ? fieldIds.emailMessage : fieldIds.phoneMessage;

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  /** "You have already applied." from the server: back to the details step,
   * where the message shows and Continue stays disabled. */
  function handleAlreadyApplied(err: unknown): boolean {
    if (
      !(err instanceof ApiError) ||
      err.status !== 409 ||
      (err.code !== "already_applied" && err.code !== "already_applied_to_job")
    ) {
      return false;
    }
    setBlockedEmail(normalizedEmail);
    setVerification(null);
    setCodeSentTo(null);
    setFormError(null);
    setStep("details");
    return true;
  }

  function handleConflict(err: unknown): boolean {
    if (!(err instanceof ApiError) || err.status !== 409) return false;
    if (err.code === "already_registered") {
      setAlreadyRegistered(err.message);
      return true;
    }
    // Applied before and still inside the reapply window — a different
    // situation from "we already have your profile", and shown as such.
    if (err.code === "reapply_locked") {
      const eligibleFrom =
        typeof err.data?.eligible_from === "string" ? err.data.eligible_from : null;
      setReapplyLocked({ message: err.message, eligibleFrom });
      return true;
    }
    return false;
  }

  const sendCodeMutation = useMutation({
    mutationFn: () =>
      props.campusToken !== undefined
        ? requestCampusEmailCode(props.campusToken, values.email.trim())
        : requestEmailCode(props.slug, values.email.trim(), props.jobId),
    onSuccess: (data) => {
      setCodeSentTo(values.email.trim().toLowerCase());
      setCode("");
      setResendIn(data.resend_available_in_seconds);
      setFormError(null);
    },
    onError: (err) => {
      if (!handleAlreadyApplied(err)) {
        setFormError(errorMessage(err, "We couldn't send the code. Please try again."));
      }
    },
  });

  const verifyMutation = useMutation({
    mutationFn: () =>
      props.campusToken !== undefined
        ? verifyCampusEmailCode(props.campusToken, values.email.trim(), code.trim())
        : verifyEmailCode(props.slug, values.email.trim(), code.trim(), props.jobId),
    // A valid code moves straight on to the resume — there is no separate
    // "verified" screen to click through. An invalid/expired code stays on
    // this step with the backend's message.
    onSuccess: (data) => {
      setVerification({ email: values.email.trim().toLowerCase(), token: data.verification_token });
      // Only now — with the email proven theirs — does a returning
      // candidate's stored profile come back, to fill in anything left blank.
      const stored = data.existing_profile;
      if (stored) setValues((current) => prefillEmptyFields(current, stored));
      goTo("resume");
    },
    onError: (err) => {
      if (handleAlreadyApplied(err) || handleConflict(err)) return;
      setFormError(errorMessage(err, "We couldn't verify the code."));
    },
  });

  const submitMutation = useMutation({
    mutationFn: () => {
      if (!resume || !verification || values.candidate_type === "") {
        throw new Error("The application is incomplete.");
      }
      const complete = { ...values, candidate_type: values.candidate_type };
      return props.campusToken !== undefined
        ? applyToCampusDrive(props.campusToken, complete, resume, verification.token, marksCard)
        : applyToJob(props.slug, props.jobId, complete, resume, verification.token, marksCard);
    },
    onSuccess: (data: ApplicationOutcomeResult) => setResult(data),
    onError: (err) => {
      if (handleAlreadyApplied(err) || handleConflict(err)) return;
      if (err instanceof ApiError && err.code === "email_not_verified") {
        setVerification(null);
        setCodeSentTo(null);
        setStep("verify");
      }
      setFormError(errorMessage(err, "Unable to submit your application. Please try again."));
    },
  });

  if (result)
    return (
      <ApplicationOutcome
        result={result}
        careersHref={props.slug !== undefined ? `/org/${props.slug}` : "/"}
      />
    );
  if (reapplyLocked)
    return (
      <ReapplyRestricted
        message={reapplyLocked.message}
        eligibleFrom={reapplyLocked.eligibleFrom}
      />
    );
  if (alreadyRegistered) return <AlreadyRegistered message={alreadyRegistered} />;

  function goTo(next: Step) {
    setFormError(null);
    setStep(next);
  }

  function handleDetailsSubmit(event: FormEvent) {
    event.preventDefault();
    if (alreadyApplied) return;
    if (values.candidate_type === "") {
      setFormError("Please tell us whether you are a fresher or an experienced professional.");
      return;
    }
    if (values.languages.length === 0) {
      setFormError("Please add at least one language you know.");
      return;
    }
    if (tenthMarksError(values.tenth_marks_percentage) || marksCardProblem) {
      setFormError("Please correct the 10th marks details, or leave them blank.");
      return;
    }
    // Already verified this exact address (they came back to edit details):
    // no need to verify it again.
    goTo(emailIsVerified ? "resume" : "verify");
  }

  function handleResumeSubmit(event: FormEvent) {
    event.preventDefault();
    if (!resume) {
      setFormError("Please attach your resume as a PDF.");
      return;
    }
    goTo("review");
  }

  function selectResume(file: File | null) {
    setFormError(null);
    if (file && !file.name.toLowerCase().endsWith(".pdf")) {
      setResume(null);
      setFormError("Please upload your resume as a PDF file.");
      return;
    }
    if (file && file.size > MAX_RESUME_MB * 1024 * 1024) {
      setResume(null);
      setFormError(`Your resume must be smaller than ${MAX_RESUME_MB} MB.`);
      return;
    }
    setResume(file);
  }

  function selectMarksCard(file: File | null) {
    const problem = file ? marksCardError(file) : null;
    setMarksCardProblem(problem);
    setMarksCard(file && !problem ? file : null);
    if (problem && marksCardInput.current) marksCardInput.current.value = "";
  }

  function clearMarksCard() {
    setMarksCard(null);
    setMarksCardProblem(null);
    if (marksCardInput.current) marksCardInput.current.value = "";
  }

  const marksProblem = tenthMarksError(values.tenth_marks_percentage);
  const busy = sendCodeMutation.isPending || verifyMutation.isPending || submitMutation.isPending;

  return (
    <section className="card apply-card" aria-labelledby="apply-title">
      <h2 id="apply-title">{title}</h2>
      <p className="muted apply-intro">
        Fields marked <span className="required-mark">*</span> are required. You can apply once — our
        recruitment team will also consider your profile for other suitable roles.
      </p>
      <Stepper current={step} />

      {step === "details" && (
        <form className="apply-details" onSubmit={handleDetailsSubmit}>
          <fieldset className="apply-fieldset">
            <legend>Personal information</legend>
            <Field label="Full name">
              <input required value={values.full_name} onChange={(e) => set("full_name", e.target.value)} autoComplete="name" />
            </Field>
            <div className="field-row">
              <FieldWithMessage
                id={fieldIds.email}
                label="Email"
                message={emailMessageShown ? ALREADY_APPLIED_MESSAGE : null}
                messageId={fieldIds.emailMessage}
              >
                <input
                  id={fieldIds.email}
                  type="email"
                  required
                  maxLength={320}
                  value={values.email}
                  onChange={(e) => set("email", e.target.value)}
                  autoComplete="email"
                  aria-invalid={emailBlocked || undefined}
                  aria-describedby={emailBlocked ? blockMessageId : undefined}
                />
              </FieldWithMessage>
              <FieldWithMessage
                id={fieldIds.phone}
                label="Mobile number"
                hint="Include the country code, e.g. +91 98765 43210."
                message={phoneMessageShown ? ALREADY_APPLIED_MESSAGE : null}
                messageId={fieldIds.phoneMessage}
              >
                <input
                  id={fieldIds.phone}
                  type="tel"
                  required
                  maxLength={32}
                  value={values.phone}
                  onChange={(e) => set("phone", e.target.value)}
                  autoComplete="tel"
                  placeholder="+91 98765 43210"
                  aria-invalid={phoneBlocked || undefined}
                  aria-describedby={phoneBlocked ? blockMessageId : undefined}
                />
              </FieldWithMessage>
            </div>
            <div className="field-row">
              <Field label="Date of birth">
                <input type="date" required max={latestAllowedBirthDate()} value={values.date_of_birth} onChange={(e) => set("date_of_birth", e.target.value)} autoComplete="bday" />
              </Field>
              <Field label="Place of birth">
                <input required maxLength={255} value={values.place_of_birth} onChange={(e) => set("place_of_birth", e.target.value)} placeholder="City, Country" />
              </Field>
            </div>
            <LanguagesInput value={values.languages} onChange={(next) => set("languages", next)} />
          </fieldset>

          <fieldset className="apply-fieldset">
            <legend>Professional background</legend>
            <Field label="I am a…">
              <select required value={values.candidate_type} onChange={(e) => set("candidate_type", e.target.value as CandidateType | "")}>
                <option value="">Select…</option>
                {(Object.keys(CANDIDATE_TYPE_LABELS) as CandidateType[]).map((type) => (
                  <option key={type} value={type}>
                    {CANDIDATE_TYPE_LABELS[type]}
                  </option>
                ))}
              </select>
            </Field>

            {isExperienced && (
              <>
                <div className="field-row">
                  <Field label="Total experience (years)">
                    <input type="number" required min={0} max={80} step={1} value={values.years_experience} onChange={(e) => set("years_experience", e.target.value)} />
                  </Field>
                  <Field label="Notice period (days)">
                    <input
                      type="number"
                      required={!values.immediate_joiner}
                      min={0}
                      max={365}
                      step={1}
                      value={values.immediate_joiner ? "0" : values.notice_period_days}
                      onChange={(e) => set("notice_period_days", e.target.value)}
                      disabled={values.immediate_joiner}
                    />
                  </Field>
                </div>
                <label className="field field-inline">
                  <input type="checkbox" checked={values.immediate_joiner} onChange={(e) => set("immediate_joiner", e.target.checked)} />
                  <span>I can join immediately</span>
                </label>
                <div className="field-row">
                  <Field label="Current job title">
                    <input required maxLength={255} value={values.current_title} onChange={(e) => set("current_title", e.target.value)} />
                  </Field>
                  <Field label="Current company">
                    <input required maxLength={255} value={values.current_company} onChange={(e) => set("current_company", e.target.value)} />
                  </Field>
                </div>
              </>
            )}

            <div className="field-row">
              <Field label="Current location">
                <input required maxLength={255} value={values.current_location} onChange={(e) => set("current_location", e.target.value)} />
              </Field>
              <Field label="Preferred location">
                <input required maxLength={255} value={values.preferred_location} onChange={(e) => set("preferred_location", e.target.value)} />
              </Field>
            </div>
            <Field label="Highest qualification">
              <input required maxLength={255} value={values.qualification} onChange={(e) => set("qualification", e.target.value)} placeholder="e.g. B.Tech in Computer Science" />
            </Field>
            <div className="field-row">
              <Field label="LinkedIn profile" optional>
                <input type="url" maxLength={500} value={values.linkedin_url} onChange={(e) => set("linkedin_url", e.target.value)} placeholder="https://www.linkedin.com/in/…" />
              </Field>
              <Field label="GitHub profile" optional>
                <input type="url" maxLength={500} value={values.github_url} onChange={(e) => set("github_url", e.target.value)} placeholder="https://github.com/…" />
              </Field>
            </div>
          </fieldset>

          <fieldset className="apply-fieldset">
            <legend>Education</legend>
            <div className="field-row">
              <FieldWithMessage
                id={fieldIds.marks}
                label="10th Marks"
                optional
                hint="Percentage, 0–100."
                message={marksProblem}
                messageId={fieldIds.marksMessage}
              >
                <input
                  id={fieldIds.marks}
                  inputMode="decimal"
                  value={values.tenth_marks_percentage}
                  onChange={(e) => set("tenth_marks_percentage", e.target.value)}
                  placeholder="e.g. 87.5"
                  aria-invalid={marksProblem !== null || undefined}
                  aria-describedby={marksProblem ? fieldIds.marksMessage : undefined}
                />
              </FieldWithMessage>
              <FieldWithMessage
                id={fieldIds.marksCard}
                label="10th Marks Card"
                optional
                hint={`PDF, PNG or JPG, up to ${MAX_MARKS_CARD_MB} MB.`}
                message={marksCardProblem}
                messageId={fieldIds.marksCardMessage}
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
                      onClick={clearMarksCard}
                      aria-label={`Remove ${marksCard.name}`}
                    >
                      <Icon name="x" size={12} />
                    </button>
                  </div>
                ) : (
                  <input
                    ref={marksCardInput}
                    id={fieldIds.marksCard}
                    type="file"
                    accept={MARKS_CARD_ACCEPT}
                    onChange={(e) => selectMarksCard(e.target.files?.[0] ?? null)}
                    aria-invalid={marksCardProblem !== null || undefined}
                    aria-describedby={marksCardProblem ? fieldIds.marksCardMessage : undefined}
                  />
                )}
              </FieldWithMessage>
            </div>
          </fieldset>

          {formError && <Alert>{formError}</Alert>}
          <div className="apply-actions apply-actions-end">
            <button type="submit" className="btn btn-primary" disabled={alreadyApplied}>
              Continue <Icon name="arrow-right" size={16} />
            </button>
          </div>
        </form>
      )}

      {step === "verify" && (
        <div className="apply-verify">
          <p>
            We'll send a 6-digit verification code to <strong>{values.email.trim()}</strong> to confirm
            it's your email address.
          </p>

          <p className="verify-status" role="status">
            <Icon name="email" size={16} />
            {codeSentTo === values.email.trim().toLowerCase()
              ? "Code sent — check your inbox (and spam folder)."
              : "Not verified yet"}
          </p>
          <div className="apply-actions apply-actions-start">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => sendCodeMutation.mutate()}
              disabled={busy || resendIn > 0}
            >
              {sendCodeMutation.isPending ? (
                <Spinner label="Sending…" />
              ) : codeSentTo ? (
                resendIn > 0 ? `Resend code in ${resendIn}s` : "Resend code"
              ) : (
                "Send verification code"
              )}
            </button>
          </div>
          {codeSentTo && (
            <form
              className="verify-code-form"
              onSubmit={(event) => {
                event.preventDefault();
                verifyMutation.mutate();
              }}
            >
              <Field label="Verification code">
                <input
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  required
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  className="verify-code-input"
                />
              </Field>
              <button type="submit" className="btn btn-primary" disabled={busy || code.length !== 6}>
                {verifyMutation.isPending ? <Spinner label="Verifying…" /> : "Verify email"}
              </button>
            </form>
          )}
          {formError && <Alert>{formError}</Alert>}
          <div className="apply-actions">
            <button type="button" className="btn btn-ghost" onClick={() => goTo("details")} disabled={busy}>
              Back
            </button>
          </div>
        </div>
      )}

      {step === "resume" && (
        <form onSubmit={handleResumeSubmit}>
          <Field label="Resume (PDF only)" hint={`PDF, up to ${MAX_RESUME_MB} MB.`}>
            <input type="file" required accept=".pdf,application/pdf" onChange={(e) => selectResume(e.target.files?.[0] ?? null)} />
          </Field>
          {resume && (
            <p className="muted">
              Selected: <strong>{resume.name}</strong>
            </p>
          )}
          {formError && <Alert>{formError}</Alert>}
          <div className="apply-actions">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => goTo(emailIsVerified ? "details" : "verify")}
            >
              Back
            </button>
            <button type="submit" className="btn btn-primary">
              Continue <Icon name="arrow-right" size={16} />
            </button>
          </div>
        </form>
      )}

      {step === "review" && (
        <div className="apply-review">
          <dl className="apply-summary">
            <dt>Name</dt>
            <dd>{values.full_name}</dd>
            <dt>Email</dt>
            <dd>
              {values.email} {emailIsVerified && <span className="chip chip-verified">Verified</span>}
            </dd>
            <dt>Mobile</dt>
            <dd>{values.phone}</dd>
            <dt>Date of birth</dt>
            <dd>{values.date_of_birth}</dd>
            <dt>Place of birth</dt>
            <dd>{values.place_of_birth}</dd>
            <dt>Languages</dt>
            <dd>{values.languages.join(", ")}</dd>
            <dt>Profile</dt>
            <dd>
              {values.candidate_type === "" ? "" : CANDIDATE_TYPE_LABELS[values.candidate_type]}
              {isExperienced && ` · ${values.years_experience} yrs · ${values.current_title} at ${values.current_company}`}
            </dd>
            <dt>Location</dt>
            <dd>
              {values.current_location} (prefers {values.preferred_location})
            </dd>
            <dt>Qualification</dt>
            <dd>{values.qualification}</dd>
            {values.tenth_marks_percentage.trim() && (
              <>
                <dt>10th Marks</dt>
                <dd>{values.tenth_marks_percentage.trim()}%</dd>
              </>
            )}
            {marksCard && (
              <>
                <dt>10th Marks Card</dt>
                <dd>{marksCard.name}</dd>
              </>
            )}
            <dt>Resume</dt>
            <dd>{resume?.name}</dd>
          </dl>
          <p className="muted">
            Please check your details — once submitted, an application can't be edited online.
          </p>
          <UpdateInfoNote contactEmail={contactEmail} />

          {submitMutation.isPending && (
            <p className="apply-processing" role="status" aria-live="polite">
              <span className="spinner" aria-hidden="true" />
              Submitting your application and reviewing your resume against the role requirements.
              This can take up to a minute — please keep this page open.
            </p>
          )}
          {formError && <Alert>{formError}</Alert>}
          <div className="apply-actions">
            <button type="button" className="btn btn-ghost" onClick={() => goTo("resume")} disabled={busy}>
              Back
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                setFormError(null);
                submitMutation.mutate();
              }}
              disabled={busy || !emailIsVerified || !resume}
            >
              {submitMutation.isPending ? <Spinner label="Submitting…" /> : "Submit application"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
