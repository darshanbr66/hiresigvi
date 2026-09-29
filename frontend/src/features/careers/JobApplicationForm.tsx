import { useMutation } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ApiError } from "../../lib/apiClient";
import { Alert } from "../../shared/components/Alert";
import { Icon } from "../../shared/components/Icon";
import { Spinner } from "../../shared/components/Spinner";
import type { JobApplicationFormValues } from "../../types/careers";
import type { CandidateType } from "../../types/recruitment";
import {
  applyToCampusDrive,
  applyToJob,
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
};

type Values = typeof EMPTY_VALUES;

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

/** One labelled input with the mandatory marker (every field is required). */
function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="field">
      <span>
        {label} <span className="required-mark" aria-hidden="true">*</span>
      </span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
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
 * is mandatory. The backend is authoritative for all of it (validation, the
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
  const [formError, setFormError] = useState<string | null>(null);

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

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
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
        : requestEmailCode(props.slug, values.email.trim()),
    onSuccess: (data) => {
      setCodeSentTo(values.email.trim().toLowerCase());
      setCode("");
      setResendIn(data.resend_available_in_seconds);
      setFormError(null);
    },
    onError: (err) => setFormError(errorMessage(err, "We couldn't send the code. Please try again.")),
  });

  const verifyMutation = useMutation({
    mutationFn: () =>
      props.campusToken !== undefined
        ? verifyCampusEmailCode(props.campusToken, values.email.trim(), code.trim())
        : verifyEmailCode(props.slug, values.email.trim(), code.trim()),
    // A valid code moves straight on to the resume — there is no separate
    // "verified" screen to click through. An invalid/expired code stays on
    // this step with the backend's message.
    onSuccess: (data) => {
      setVerification({ email: values.email.trim().toLowerCase(), token: data.verification_token });
      goTo("resume");
    },
    onError: (err) => {
      if (!handleConflict(err)) setFormError(errorMessage(err, "We couldn't verify the code."));
    },
  });

  const submitMutation = useMutation({
    mutationFn: () => {
      if (!resume || !verification || values.candidate_type === "") {
        throw new Error("The application is incomplete.");
      }
      const complete = { ...values, candidate_type: values.candidate_type };
      return props.campusToken !== undefined
        ? applyToCampusDrive(props.campusToken, complete, resume, verification.token)
        : applyToJob(props.slug, props.jobId, complete, resume, verification.token);
    },
    onSuccess: (data: ApplicationOutcomeResult) => setResult(data),
    onError: (err) => {
      if (handleConflict(err)) return;
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
    if (values.candidate_type === "") {
      setFormError("Please tell us whether you are a fresher or an experienced professional.");
      return;
    }
    if (values.languages.length === 0) {
      setFormError("Please add at least one language you know.");
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

  const busy = sendCodeMutation.isPending || verifyMutation.isPending || submitMutation.isPending;

  return (
    <section className="card apply-card" aria-labelledby="apply-title">
      <h2 id="apply-title">{title}</h2>
      <p className="muted apply-intro">
        All fields marked <span className="required-mark">*</span> are required. You can apply once —
        our recruitment team will also consider your profile for other suitable roles.
      </p>
      <Stepper current={step} />

      {step === "details" && (
        <form onSubmit={handleDetailsSubmit}>
          <fieldset className="apply-fieldset">
            <legend>Personal information</legend>
            <Field label="Full name">
              <input required maxLength={255} value={values.full_name} onChange={(e) => set("full_name", e.target.value)} autoComplete="name" />
            </Field>
            <div className="field-row">
              <Field label="Email">
                <input type="email" required maxLength={320} value={values.email} onChange={(e) => set("email", e.target.value)} autoComplete="email" />
              </Field>
              <Field label="Mobile number" hint="Include the country code, e.g. +91 98765 43210.">
                <input type="tel" required maxLength={32} value={values.phone} onChange={(e) => set("phone", e.target.value)} autoComplete="tel" placeholder="+91 98765 43210" />
              </Field>
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
              <Field label="LinkedIn profile">
                <input type="url" required maxLength={500} value={values.linkedin_url} onChange={(e) => set("linkedin_url", e.target.value)} placeholder="https://www.linkedin.com/in/…" />
              </Field>
              <Field label="GitHub profile">
                <input type="url" required maxLength={500} value={values.github_url} onChange={(e) => set("github_url", e.target.value)} placeholder="https://github.com/…" />
              </Field>
            </div>
          </fieldset>

          {formError && <Alert>{formError}</Alert>}
          <div className="apply-actions">
            <button type="submit" className="btn btn-primary">
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
