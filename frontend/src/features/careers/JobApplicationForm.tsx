import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ApiError } from "../../lib/apiClient";
import { Alert } from "../../shared/components/Alert";
import { Icon } from "../../shared/components/Icon";
import { Spinner } from "../../shared/components/Spinner";
import type { ApplicantBackground, DraftAuth, DraftView } from "../../types/applyFlow";
import type { ApplicantProfilePrefill, JobApplicationFormValues } from "../../types/careers";
import type { ProgramDeclaration } from "../../types/program";
import type { CandidateType } from "../../types/recruitment";
import {
  applyFlowBase,
  applyToProgram,
  checkApplicantStatus,
  checkCampusApplicantStatus,
  checkProgramApplicantStatus,
  getQualificationOptions,
  requestCampusEmailCode,
  requestEmailCode,
  requestProgramEmailCode,
  saveDraftBackground,
  startApplicationDraft,
  submitApplicationDraft,
  verifyCampusEmailCode,
  verifyEmailCode,
  verifyProgramEmailCode,
} from "./api";
import {
  AlreadyRegistered,
  ApplicationOutcome,
  type ApplicationOutcomeResult,
  ReapplyRestricted,
  UpdateInfoNote,
} from "./ApplicationOutcome";
import { ApplyStepper, type StepDefinition } from "./apply/ApplyStepper";
import { EducationStep } from "./apply/EducationStep";
import { educationFromDraft, EMPTY_EDUCATION, type EducationValues } from "./apply/educationValues";
import { fieldAria } from "./apply/fieldAria";
import { FormField } from "./apply/formFields";
import { PhoneInput } from "./apply/PhoneInput";
import {
  emailError,
  formatFileSize,
  fullNameError,
  latestAllowedBirthDate,
  looksLikeEmail,
  MAX_MARKS_CARD_MB,
  MAX_RESUME_MB,
  phoneError,
  profileUrlError,
  resumeFileError,
  splitPhone,
  tenthMarksError,
  documentFileError,
  DOCUMENT_ACCEPT,
} from "./apply/validation";
import { LanguagesInput } from "./LanguagesInput";

const CANDIDATE_TYPE_LABELS: Record<CandidateType, string> = {
  FRESHER: "Fresher (no professional experience yet)",
  EXPERIENCED: "Experienced professional",
};

type StepKey = "details" | "verify" | "background" | "education" | "resume" | "review";

const ALL_STEPS: StepDefinition<StepKey>[] = [
  { key: "details", label: "Your details" },
  { key: "verify", label: "Verify" },
  { key: "background", label: "Professional background" },
  { key: "education", label: "Education" },
  { key: "resume", label: "Resume" },
  { key: "review", label: "Review & submit" },
];

const STEP_HEADINGS: Record<StepKey, string> = {
  details: "Your details",
  verify: "Verify your email",
  background: "Professional background",
  education: "Education",
  resume: "Resume",
  review: "Review & submit",
};

type Values = Omit<JobApplicationFormValues, "candidate_type"> & {
  candidate_type: CandidateType | "";
};

const EMPTY_VALUES: Values = {
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

type FieldErrors = Partial<Record<keyof Values, string>>;

/** Shown — and Continue disabled — when the early check (or the server, at
 * any later step) says this email or mobile number has already applied. */
const ALREADY_APPLIED_MESSAGE = "You have already applied.";
const VERIFICATION_EXPIRED_MESSAGE =
  "Your email verification has expired. Please verify your email again — your progress is saved.";
/** The early check waits for typing to pause rather than calling the API on
 * every keystroke. */
const STATUS_CHECK_DEBOUNCE_MS = 400;

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

/** "+91 098765 43210" -> "+919876543210": what the server is sent. */
function normalizedPhone(value: string): string {
  const { code, number } = splitPhone(value);
  return `${code}${number.replace(/\D/g, "").replace(/^0+/, "")}`;
}

/** A returning candidate's stored profile (received only after their code
 * was verified) fills in what they haven't typed — never overwrites it. */
function prefillEmptyFields(current: Values, stored: ApplicantProfilePrefill): Values {
  const text = (value: string, fallback: string | null) => (value.trim() ? value : (fallback ?? ""));
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

/** The server's saved background (a resumed draft) fills empty fields. */
function prefillFromBackground(current: Values, saved: ApplicantBackground): Values {
  return prefillEmptyFields(current, {
    full_name: null,
    phone: null,
    date_of_birth: saved.date_of_birth,
    place_of_birth: saved.place_of_birth,
    languages: saved.languages,
    candidate_type: saved.candidate_type,
    years_experience: saved.years_experience,
    notice_period_days: saved.notice_period_days,
    immediate_joiner: saved.immediate_joiner,
    current_title: saved.current_title,
    current_company: saved.current_company,
    location: saved.location,
    preferred_location: saved.preferred_location,
    qualification: null,
    linkedin_url: saved.linkedin_url,
    github_url: saved.github_url,
    tenth_marks_percentage: null,
  });
}

function toBackground(values: Values): ApplicantBackground {
  const optional = (value: string) => (value.trim() ? value.trim() : null);
  const experienced = values.candidate_type === "EXPERIENCED";
  return {
    date_of_birth: values.date_of_birth,
    place_of_birth: values.place_of_birth.trim(),
    languages: values.languages,
    candidate_type: values.candidate_type as CandidateType,
    location: values.current_location.trim(),
    preferred_location: values.preferred_location.trim(),
    linkedin_url: optional(values.linkedin_url),
    github_url: optional(values.github_url),
    years_experience: experienced ? Number(values.years_experience) : null,
    current_title: experienced ? optional(values.current_title) : null,
    current_company: experienced ? optional(values.current_company) : null,
    notice_period_days:
      experienced && !values.immediate_joiner && values.notice_period_days.trim()
        ? Number(values.notice_period_days)
        : null,
    immediate_joiner: experienced ? values.immediate_joiner : null,
  };
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const issue = err.fieldIssues[0];
    return err.code === "validation_error" && issue ? `${issue.field}: ${issue.message}` : err.message;
  }
  return fallback;
}

/**
 * The self-service application as a progressive, step-by-step flow:
 *
 *   Your details (name, email, mobile) -> Verify (email one-time code) ->
 *   Professional background -> Education (JD-specific qualification + AI
 *   check of the academic document) -> Resume -> Review & submit
 *
 * For a careers-site job (`slug` + `jobId`) and a campus drive link
 * (`campusToken`), every step after verification is saved on the server
 * (a draft the verified email unlocks), and the submission is built from
 * that server-side draft. A recruitment program (`program`) has no
 * Education step — its page collects the education declaration — and
 * submits the complete form in one request. The backend is authoritative
 * for everything; the checks here only spare the candidate a round trip.
 */
export function JobApplicationForm(
  props: { contactEmail?: string | null; title?: string } & (
    | { slug: string; jobId: string; campusToken?: undefined; program?: undefined }
    | { campusToken: string; slug?: undefined; jobId?: undefined; program?: undefined }
    | {
        program: { id: string; declaration: ProgramDeclaration; ready: boolean };
        slug?: undefined;
        jobId?: undefined;
        campusToken?: undefined;
      }
  ),
) {
  const { contactEmail = null, title = "Apply for this role" } = props;
  const base = props.program
    ? null
    : applyFlowBase(
        props.campusToken !== undefined
          ? { campusToken: props.campusToken }
          : { slug: props.slug, jobId: props.jobId },
      );
  const steps = base ? ALL_STEPS : ALL_STEPS.filter((step) => step.key !== "education");

  const [step, setStep] = useState<StepKey>("details");
  const [values, setValues] = useState<Values>(EMPTY_VALUES);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [education, setEducation] = useState<EducationValues>(EMPTY_EDUCATION);
  const [resume, setResume] = useState<File | null>(null);
  const [resumeProblem, setResumeProblem] = useState<string | null>(null);
  const [marksCard, setMarksCard] = useState<File | null>(null);
  const [marksCardProblem, setMarksCardProblem] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [phoneConflict, setPhoneConflict] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftView | null>(null);
  const [submitProgress, setSubmitProgress] = useState<number | null>(null);

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

  const ids = {
    name: useId(),
    email: useId(),
    phone: useId(),
    code: useId(),
    dob: useId(),
    pob: useId(),
    type: useId(),
    years: useId(),
    notice: useId(),
    jobTitle: useId(),
    company: useId(),
    current: useId(),
    preferred: useId(),
    qualification: useId(),
    linkedin: useId(),
    github: useId(),
    marks: useId(),
    marksCard: useId(),
    resume: useId(),
  };
  const headingRef = useRef<HTMLHeadingElement>(null);
  const firstRender = useRef(true);

  const normalizedEmail = values.email.trim().toLowerCase();
  const verifiedEmail = verification?.email ?? null;
  const emailIsVerified = verifiedEmail !== null && verifiedEmail === normalizedEmail;
  const auth: DraftAuth | null = verification
    ? { email: verification.email, email_verification_token: verification.token }
    : null;
  const isExperienced = values.candidate_type === "EXPERIENCED";

  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = window.setTimeout(() => setResendIn((s) => s - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [resendIn]);

  // Move focus to the new step's heading, so keyboard and screen-reader
  // users land at the start of the step (not on the initial render).
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [step]);

  // The early "already applied?" check — a plain yes/no once a plausible
  // email or mobile number has been typed. The server enforces the same rule
  // at every later step on its own; this only stops the candidate up front.
  const checkEmail = useDebouncedValue(
    looksLikeEmail(normalizedEmail) ? normalizedEmail : "",
    STATUS_CHECK_DEBOUNCE_MS,
  );
  const checkPhone = useDebouncedValue(
    phoneError(values.phone) === null ? normalizedPhone(values.phone) : "",
    STATUS_CHECK_DEBOUNCE_MS,
  );
  const statusScope = props.program
    ? `program/${props.program.id}`
    : (props.campusToken ?? `${props.slug}/${props.jobId}`);
  function checkStatus(query: { email: string } | { phone: string }) {
    if (props.program) return checkProgramApplicantStatus(props.program.id, query);
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
  const [blockedEmail, setBlockedEmail] = useState<string | null>(null);
  const emailBlocked =
    (checkEmail !== "" && emailStatusQuery.data?.already_applied === true) ||
    (blockedEmail !== null && blockedEmail === normalizedEmail);
  const phoneBlocked = checkPhone !== "" && phoneStatusQuery.data?.already_applied === true;
  const alreadyApplied = emailBlocked || phoneBlocked;

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setErrors((current) => ({ ...current, [key]: undefined }));
    if (key === "phone") setPhoneConflict(null);
    setValues((current) => ({ ...current, [key]: value }));
  }

  function goTo(next: StepKey) {
    setFormError(null);
    setNotice(null);
    setStep(next);
  }

  /** "You have already applied." from the server: back to the details
   * step, where the message shows and Continue stays disabled. */
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
    setDraft(null);
    goTo("details");
    if (err.code === "already_applied_to_job") setFormError(err.message);
    return true;
  }

  function handleConflict(err: unknown): boolean {
    if (!(err instanceof ApiError) || err.status !== 409) return false;
    if (err.code === "already_registered") {
      setAlreadyRegistered(err.message);
      return true;
    }
    if (err.code === "reapply_locked") {
      const eligibleFrom = typeof err.data?.eligible_from === "string" ? err.data.eligible_from : null;
      setReapplyLocked({ message: err.message, eligibleFrom });
      return true;
    }
    if (err.code === "phone_already_registered") {
      // The server spent the verification on this attempt.
      setVerification(null);
      setCodeSentTo(null);
      setDraft(null);
      goTo("details");
      setPhoneConflict(err.message);
      return true;
    }
    return false;
  }

  /** Errors any step's server call can return; true = handled here. */
  function handleRequestError(err: unknown): boolean {
    if (handleAlreadyApplied(err) || handleConflict(err)) return true;
    if (err instanceof ApiError && err.code === "email_not_verified") {
      setVerification(null);
      setCodeSentTo(null);
      setCode("");
      goTo("verify");
      setNotice(VERIFICATION_EXPIRED_MESSAGE);
      return true;
    }
    if (err instanceof ApiError && err.code === "draft_not_found") {
      setDraft(null);
      setVerification(null);
      setCodeSentTo(null);
      goTo("details");
      setFormError(err.message);
      return true;
    }
    return false;
  }

  function applyDraft(next: DraftView) {
    setDraft(next);
    if (next.background) {
      const saved = next.background;
      setValues((current) => prefillFromBackground(current, saved));
    }
    const savedEducation = educationFromDraft(next.education);
    if (savedEducation) {
      setEducation((current) => (current.qualification_key ? current : savedEducation));
    }
  }

  const startDraftMutation = useMutation({
    mutationFn: (verified: { email: string; token: string }) => {
      if (!base) throw new Error("No draft for this application.");
      return startApplicationDraft(
        base,
        { email: verified.email, email_verification_token: verified.token },
        values.full_name.trim(),
        normalizedPhone(values.phone),
      );
    },
    onSuccess: (next) => {
      applyDraft(next);
      goTo("background");
    },
    onError: (err) => {
      if (!handleRequestError(err)) {
        setFormError(errorMessage(err, "We couldn't save your details. Please try again."));
      }
    },
  });

  const sendCodeMutation = useMutation({
    mutationFn: () =>
      props.program
        ? requestProgramEmailCode(props.program.id, values.email.trim())
        : props.campusToken !== undefined
          ? requestCampusEmailCode(props.campusToken, values.email.trim())
          : requestEmailCode(props.slug, values.email.trim(), props.jobId),
    onSuccess: (data) => {
      setCodeSentTo(normalizedEmail);
      setCode("");
      setResendIn(data.resend_available_in_seconds);
      setFormError(null);
      if (step !== "verify") goTo("verify");
    },
    onError: (err) => {
      if (handleAlreadyApplied(err)) return;
      // A cooldown still running from an earlier send: the code already sent
      // is still valid, so the candidate can go on and enter it.
      if (err instanceof ApiError && err.code === "otp_resend_throttled" && codeSentTo === normalizedEmail) {
        if (step !== "verify") goTo("verify");
        return;
      }
      setFormError(errorMessage(err, "We couldn't send the code. Please try again."));
    },
  });

  const verifyMutation = useMutation({
    mutationFn: () =>
      props.program
        ? verifyProgramEmailCode(props.program.id, values.email.trim(), code.trim())
        : props.campusToken !== undefined
          ? verifyCampusEmailCode(props.campusToken, values.email.trim(), code.trim())
          : verifyEmailCode(props.slug, values.email.trim(), code.trim(), props.jobId),
    onSuccess: (data) => {
      const verified = { email: normalizedEmail, token: data.verification_token };
      setVerification(verified);
      setCode("");
      const stored = data.existing_profile;
      if (stored) setValues((current) => prefillEmptyFields(current, stored));
      if (base) startDraftMutation.mutate(verified);
      else goTo("background");
    },
    onError: (err) => {
      if (handleAlreadyApplied(err) || handleConflict(err)) return;
      setFormError(errorMessage(err, "We couldn't verify the code."));
    },
  });

  const backgroundMutation = useMutation({
    mutationFn: () => {
      if (!base || !auth) throw new Error("Verify your email first.");
      return saveDraftBackground(base, auth, toBackground(values));
    },
    onSuccess: (next) => {
      setDraft(next);
      goTo("education");
    },
    onError: (err) => {
      if (!handleRequestError(err)) {
        setFormError(errorMessage(err, "We couldn't save your details. Please try again."));
      }
    },
  });

  const submitMutation = useMutation({
    mutationFn: async (): Promise<ApplicationOutcomeResult> => {
      if (!resume || !verification) throw new Error("The application is incomplete.");
      if (base && auth) {
        setSubmitProgress(0);
        return submitApplicationDraft(base, auth, resume, marksCard, setSubmitProgress);
      }
      if (!props.program || values.candidate_type === "") {
        throw new Error("The application is incomplete.");
      }
      if (!props.program.ready) throw new Error("Complete the program details and accept the terms.");
      return applyToProgram(
        props.program.id,
        { ...values, candidate_type: values.candidate_type, phone: normalizedPhone(values.phone) },
        resume,
        verification.token,
        marksCard,
        props.program.declaration,
      );
    },
    onSuccess: (data) => setResult(data),
    onError: (err) => {
      setSubmitProgress(null);
      if (handleRequestError(err)) return;
      if (err instanceof ApiError) {
        if (
          ["education_recheck_required", "education_not_verified", "academic_document_required", "education_required"].includes(
            err.code,
          )
        ) {
          goTo("education");
          setFormError(err.message);
          return;
        }
        if (err.code === "background_required") {
          goTo("background");
          setFormError(err.message);
          return;
        }
      }
      setFormError(errorMessage(err, "Unable to submit your application. Please try again."));
    },
  });

  if (result) {
    return (
      <ApplicationOutcome
        result={result}
        careersHref={props.slug !== undefined ? `/org/${props.slug}` : "/"}
      />
    );
  }
  if (reapplyLocked) {
    return <ReapplyRestricted message={reapplyLocked.message} eligibleFrom={reapplyLocked.eligibleFrom} />;
  }
  if (alreadyRegistered) return <AlreadyRegistered message={alreadyRegistered} />;

  const busy =
    sendCodeMutation.isPending ||
    verifyMutation.isPending ||
    startDraftMutation.isPending ||
    backgroundMutation.isPending ||
    submitMutation.isPending;

  // --- Step handlers ---------------------------------------------------------

  function handleDetailsSubmit(event: FormEvent) {
    event.preventDefault();
    if (alreadyApplied || busy) return;
    const next: FieldErrors = {};
    const nameProblem = fullNameError(values.full_name);
    const emailProblem = emailError(values.email);
    const phoneProblem = phoneError(values.phone);
    if (nameProblem) next.full_name = nameProblem;
    if (emailProblem) next.email = emailProblem;
    if (phoneProblem) next.phone = phoneProblem;
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    if (emailIsVerified && verification) {
      // Back here to fix a typo: the email is still verified — refresh the
      // draft with the corrected name/mobile and carry on.
      if (base) startDraftMutation.mutate(verification);
      else goTo("background");
      return;
    }
    if (codeSentTo === normalizedEmail && resendIn > 0) {
      goTo("verify");
      return;
    }
    sendCodeMutation.mutate();
  }

  function validateBackground(): boolean {
    const next: FieldErrors = {};
    if (!values.date_of_birth) next.date_of_birth = "Enter your date of birth.";
    else if (values.date_of_birth > latestAllowedBirthDate()) {
      next.date_of_birth = "Applicants must be at least 16 years old.";
    }
    if (!values.place_of_birth.trim()) next.place_of_birth = "Enter your place of birth.";
    if (values.languages.length === 0) next.languages = "Add at least one language you know.";
    if (!values.candidate_type) next.candidate_type = "Tell us whether you are a fresher or experienced.";
    if (isExperienced) {
      if (!values.years_experience.trim()) next.years_experience = "Enter your total experience.";
      if (!values.immediate_joiner && !values.notice_period_days.trim()) {
        next.notice_period_days = "Enter your notice period, or tick “I can join immediately”.";
      }
      if (!values.current_title.trim()) next.current_title = "Enter your current job title.";
      if (!values.current_company.trim()) next.current_company = "Enter your current company.";
    }
    if (!values.current_location.trim()) next.current_location = "Enter your current location.";
    if (!values.preferred_location.trim()) next.preferred_location = "Enter your preferred location.";
    const linkedin = profileUrlError(values.linkedin_url, "linkedin.com");
    if (linkedin) next.linkedin_url = linkedin;
    const github = profileUrlError(values.github_url, "github.com");
    if (github) next.github_url = github;
    if (!base) {
      if (!values.qualification.trim()) next.qualification = "Enter your highest qualification.";
      const marks = tenthMarksError(values.tenth_marks_percentage);
      if (marks) next.tenth_marks_percentage = marks;
    }
    setErrors(next);
    return Object.keys(next).length === 0 && !(!base && marksCardProblem);
  }

  function handleBackgroundSubmit(event: FormEvent) {
    event.preventDefault();
    if (busy || !validateBackground()) return;
    if (base) backgroundMutation.mutate();
    else goTo("resume");
  }

  function selectResume(file: File | null) {
    setFormError(null);
    const problem = file ? resumeFileError(file) : null;
    setResumeProblem(problem);
    setResume(file && !problem ? file : null);
  }

  function selectMarksCard(file: File | null) {
    const problem = file ? documentFileError(file, MAX_MARKS_CARD_MB, "10th marks card") : null;
    setMarksCardProblem(problem);
    setMarksCard(file && !problem ? file : null);
  }

  function handleResumeSubmit(event: FormEvent) {
    event.preventDefault();
    if (!resume) {
      setResumeProblem(resumeProblem ?? "Please attach your resume as a PDF.");
      return;
    }
    goTo("review");
  }

  const stepIndex = steps.findIndex((s) => s.key === step);
  const heading = STEP_HEADINGS[step];

  return (
    <section className="card apply-card" aria-labelledby="apply-title">
      <h2 id="apply-title">{title}</h2>
      <p className="muted apply-intro">
        Fields marked <span className="required-mark">*</span> are required. Your progress is saved as
        you go.
      </p>
      <ApplyStepper steps={steps} current={step} />

      <h3 className="apply-step-heading" ref={headingRef} tabIndex={-1}>
        <span className="sr-only">{`Step ${stepIndex + 1} of ${steps.length}:`}</span> {heading}
      </h3>
      {notice && (
        <p className="apply-notice" role="status">
          {notice}
        </p>
      )}

      {step === "details" && (
        <form className="apply-step-body" onSubmit={handleDetailsSubmit} noValidate>
          <p className="muted apply-step-lead">
            Start with your contact details. We'll send a verification code to your email.
          </p>
          <FormField id={ids.name} label="Full name" message={errors.full_name}>
            <input
              {...fieldAria(ids.name, errors.full_name)}
              required
              value={values.full_name}
              onChange={(e) => set("full_name", e.target.value)}
              autoComplete="name"
            />
          </FormField>
          <FormField
            id={ids.email}
            label="Email address"
            message={errors.email ?? (emailBlocked ? ALREADY_APPLIED_MESSAGE : null)}
          >
            <input
              {...fieldAria(ids.email, errors.email ?? (emailBlocked ? ALREADY_APPLIED_MESSAGE : null))}
              type="email"
              required
              maxLength={320}
              value={values.email}
              onChange={(e) => set("email", e.target.value)}
              autoComplete="email"
            />
          </FormField>
          <FormField
            id={ids.phone}
            label="Mobile number"
            hint="Select your country code and enter your mobile number."
            message={
              errors.phone ??
              phoneConflict ??
              (phoneBlocked && !emailBlocked ? ALREADY_APPLIED_MESSAGE : null)
            }
          >
            <PhoneInput
              id={ids.phone}
              value={values.phone}
              onChange={(next) => set("phone", next)}
              invalid={Boolean(errors.phone || phoneConflict || phoneBlocked)}
              describedBy={
                errors.phone || phoneConflict || phoneBlocked
                  ? `${ids.phone}-message ${ids.phone}-hint`
                  : `${ids.phone}-hint`
              }
            />
          </FormField>
          {emailIsVerified && (
            <p className="verify-status verify-status-ok">
              <Icon name="check" size={15} /> {values.email.trim()} is verified.
            </p>
          )}
          {formError && <Alert>{formError}</Alert>}
          <div className="apply-actions apply-actions-end">
            <button type="submit" className="btn btn-primary" disabled={alreadyApplied || busy}>
              {busy ? (
                <Spinner label="Please wait…" />
              ) : emailIsVerified ? (
                "Continue"
              ) : (
                "Continue & verify email"
              )}
              {!busy && <Icon name="arrow-right" size={16} />}
            </button>
          </div>
        </form>
      )}

      {step === "verify" && (
        <div className="apply-step-body apply-verify">
          {codeSentTo === normalizedEmail ? (
            <p>
              We sent a 6-digit code to <strong>{values.email.trim()}</strong>. Enter it below to
              verify your email address. Check your spam folder if it doesn't arrive.
            </p>
          ) : (
            <p>
              We'll send a 6-digit verification code to <strong>{values.email.trim()}</strong>.
            </p>
          )}
          {codeSentTo === normalizedEmail ? (
            <form
              className="verify-code-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (code.length === 6 && !busy) verifyMutation.mutate();
              }}
            >
              <FormField id={ids.code} label="Verification code">
                <input
                  {...fieldAria(ids.code)}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  required
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  className="verify-code-input"
                />
              </FormField>
              <button type="submit" className="btn btn-primary" disabled={busy || code.length !== 6}>
                {verifyMutation.isPending || startDraftMutation.isPending ? (
                  <Spinner label="Verifying…" />
                ) : (
                  "Verify email"
                )}
              </button>
            </form>
          ) : null}
          <div className="apply-verify-links">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => sendCodeMutation.mutate()}
              disabled={busy || resendIn > 0}
            >
              {sendCodeMutation.isPending ? (
                <Spinner label="Sending…" />
              ) : codeSentTo === normalizedEmail ? (
                resendIn > 0 ? (
                  `Resend code in ${resendIn}s`
                ) : (
                  "Resend code"
                )
              ) : (
                "Send verification code"
              )}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => goTo("details")} disabled={busy}>
              Change email
            </button>
          </div>
          {formError && <Alert>{formError}</Alert>}
          <div className="apply-actions">
            <button type="button" className="btn btn-ghost" onClick={() => goTo("details")} disabled={busy}>
              <Icon name="arrow-left" size={16} /> Back
            </button>
          </div>
        </div>
      )}

      {step === "background" && (
        <form className="apply-step-body" onSubmit={handleBackgroundSubmit} noValidate>
          <p className="verify-status verify-status-ok">
            <Icon name="check" size={15} /> Email verified: {verification?.email}
          </p>
          <fieldset className="apply-fieldset">
            <legend>About you</legend>
            <div className="field-row">
              <FormField id={ids.dob} label="Date of birth" message={errors.date_of_birth}>
                <input
                  {...fieldAria(ids.dob, errors.date_of_birth)}
                  type="date"
                  required
                  max={latestAllowedBirthDate()}
                  value={values.date_of_birth}
                  onChange={(e) => set("date_of_birth", e.target.value)}
                  autoComplete="bday"
                />
              </FormField>
              <FormField id={ids.pob} label="Place of birth" message={errors.place_of_birth}>
                <input
                  {...fieldAria(ids.pob, errors.place_of_birth)}
                  required
                  maxLength={255}
                  value={values.place_of_birth}
                  onChange={(e) => set("place_of_birth", e.target.value)}
                  placeholder="City, Country"
                />
              </FormField>
            </div>
            <LanguagesInput value={values.languages} onChange={(next) => set("languages", next)} />
            {errors.languages && (
              <p className="field-message" role="alert">
                {errors.languages}
              </p>
            )}
          </fieldset>

          <fieldset className="apply-fieldset">
            <legend>Experience</legend>
            <FormField id={ids.type} label="I am a…" message={errors.candidate_type}>
              <select
                {...fieldAria(ids.type, errors.candidate_type)}
                required
                value={values.candidate_type}
                onChange={(e) => set("candidate_type", e.target.value as CandidateType | "")}
              >
                <option value="">Select…</option>
                {(Object.keys(CANDIDATE_TYPE_LABELS) as CandidateType[]).map((type) => (
                  <option key={type} value={type}>
                    {CANDIDATE_TYPE_LABELS[type]}
                  </option>
                ))}
              </select>
            </FormField>
            {isExperienced && (
              <>
                <div className="field-row">
                  <FormField id={ids.years} label="Total experience (years)" message={errors.years_experience}>
                    <input
                      {...fieldAria(ids.years, errors.years_experience)}
                      type="number"
                      required
                      min={0}
                      max={80}
                      step={1}
                      value={values.years_experience}
                      onChange={(e) => set("years_experience", e.target.value)}
                    />
                  </FormField>
                  <FormField id={ids.notice} label="Notice period (days)" message={errors.notice_period_days}>
                    <input
                      {...fieldAria(ids.notice, errors.notice_period_days)}
                      type="number"
                      required={!values.immediate_joiner}
                      min={0}
                      max={365}
                      step={1}
                      value={values.immediate_joiner ? "0" : values.notice_period_days}
                      onChange={(e) => set("notice_period_days", e.target.value)}
                      disabled={values.immediate_joiner}
                    />
                  </FormField>
                </div>
                <label className="field field-inline">
                  <input
                    type="checkbox"
                    checked={values.immediate_joiner}
                    onChange={(e) => set("immediate_joiner", e.target.checked)}
                  />
                  <span>I can join immediately</span>
                </label>
                <div className="field-row">
                  <FormField id={ids.jobTitle} label="Current job title" message={errors.current_title}>
                    <input
                      {...fieldAria(ids.jobTitle, errors.current_title)}
                      required
                      maxLength={255}
                      value={values.current_title}
                      onChange={(e) => set("current_title", e.target.value)}
                    />
                  </FormField>
                  <FormField id={ids.company} label="Current company" message={errors.current_company}>
                    <input
                      {...fieldAria(ids.company, errors.current_company)}
                      required
                      maxLength={255}
                      value={values.current_company}
                      onChange={(e) => set("current_company", e.target.value)}
                    />
                  </FormField>
                </div>
              </>
            )}
            <div className="field-row">
              <FormField id={ids.current} label="Current location" message={errors.current_location}>
                <input
                  {...fieldAria(ids.current, errors.current_location)}
                  required
                  maxLength={255}
                  value={values.current_location}
                  onChange={(e) => set("current_location", e.target.value)}
                  placeholder="City"
                />
              </FormField>
              <FormField id={ids.preferred} label="Preferred location" message={errors.preferred_location}>
                <input
                  {...fieldAria(ids.preferred, errors.preferred_location)}
                  required
                  maxLength={255}
                  value={values.preferred_location}
                  onChange={(e) => set("preferred_location", e.target.value)}
                  placeholder="City"
                />
              </FormField>
            </div>
          </fieldset>

          <fieldset className="apply-fieldset">
            <legend>Online profiles</legend>
            <div className="field-row">
              <FormField id={ids.linkedin} label="LinkedIn profile" optional message={errors.linkedin_url}>
                <input
                  {...fieldAria(ids.linkedin, errors.linkedin_url)}
                  type="url"
                  maxLength={500}
                  value={values.linkedin_url}
                  onChange={(e) => set("linkedin_url", e.target.value)}
                  placeholder="https://www.linkedin.com/in/…"
                />
              </FormField>
              <FormField id={ids.github} label="GitHub profile" optional message={errors.github_url}>
                <input
                  {...fieldAria(ids.github, errors.github_url)}
                  type="url"
                  maxLength={500}
                  value={values.github_url}
                  onChange={(e) => set("github_url", e.target.value)}
                  placeholder="https://github.com/…"
                />
              </FormField>
            </div>
          </fieldset>

          {!base && (
            <fieldset className="apply-fieldset">
              <legend>Education</legend>
              <FormField id={ids.qualification} label="Highest qualification" message={errors.qualification}>
                <input
                  {...fieldAria(ids.qualification, errors.qualification)}
                  required
                  maxLength={255}
                  value={values.qualification}
                  onChange={(e) => set("qualification", e.target.value)}
                  placeholder="e.g. B.Tech in Computer Science"
                />
              </FormField>
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
                  />
                </FormField>
                <FormField
                  id={ids.marksCard}
                  label="10th marks card"
                  optional
                  hint={`PDF, PNG or JPG, up to ${MAX_MARKS_CARD_MB} MB.`}
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
                      {...fieldAria(ids.marksCard, marksCardProblem)}
                      type="file"
                      accept={DOCUMENT_ACCEPT}
                      onChange={(e) => selectMarksCard(e.target.files?.[0] ?? null)}
                    />
                  )}
                </FormField>
              </div>
            </fieldset>
          )}

          {formError && <Alert>{formError}</Alert>}
          <div className="apply-actions">
            <button type="button" className="btn btn-ghost" onClick={() => goTo("details")} disabled={busy}>
              <Icon name="arrow-left" size={16} /> Back
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {backgroundMutation.isPending ? <Spinner label="Saving…" /> : "Continue"}
              {!backgroundMutation.isPending && <Icon name="arrow-right" size={16} />}
            </button>
          </div>
        </form>
      )}

      {step === "education" && formError && <Alert>{formError}</Alert>}
      {step === "education" && base && auth && draft && (
        <EducationStep
          base={base}
          auth={auth}
          draft={draft}
          onDraft={setDraft}
          values={education}
          onChange={setEducation}
          marksCard={marksCard}
          onMarksCard={setMarksCard}
          onBack={() => goTo("background")}
          onContinue={() => goTo("resume")}
          onRequestError={handleRequestError}
        />
      )}

      {step === "resume" && (
        <form className="apply-step-body" onSubmit={handleResumeSubmit} noValidate>
          <FormField
            id={ids.resume}
            label="Resume"
            hint={`PDF only, up to ${MAX_RESUME_MB} MB.`}
            message={resumeProblem}
          >
            {resume ? (
              <div className="file-selected">
                <Icon name="notes" size={14} />
                <span className="file-selected-name" title={resume.name}>
                  {resume.name}
                </span>
                <span className="file-selected-size">{formatFileSize(resume.size)}</span>
                <button
                  type="button"
                  className="chip-remove"
                  onClick={() => selectResume(null)}
                  aria-label={`Remove ${resume.name}`}
                >
                  <Icon name="x" size={12} />
                </button>
              </div>
            ) : (
              <input
                {...fieldAria(ids.resume, resumeProblem, `PDF only, up to ${MAX_RESUME_MB} MB.`)}
                type="file"
                required
                accept=".pdf,application/pdf"
                onChange={(e) => selectResume(e.target.files?.[0] ?? null)}
              />
            )}
          </FormField>
          {resume && (
            <p className="muted apply-step-lead">
              Ready — your resume is uploaded when you submit your application.
            </p>
          )}
          {formError && <Alert>{formError}</Alert>}
          <div className="apply-actions">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => goTo(base ? "education" : "background")}
            >
              <Icon name="arrow-left" size={16} /> Back
            </button>
            <button type="submit" className="btn btn-primary">
              Continue <Icon name="arrow-right" size={16} />
            </button>
          </div>
        </form>
      )}

      {step === "review" && (
        <ReviewStep
          base={base}
          values={values}
          isExperienced={isExperienced}
          emailIsVerified={emailIsVerified}
          draft={base ? draft : null}
          educationLabel={draft?.education ? undefined : values.qualification}
          resume={resume}
          marksCard={marksCard}
          contactEmail={contactEmail}
          onEdit={goTo}
          hasEducationStep={Boolean(base)}
        >
          {submitMutation.isPending && (
            <div className="apply-processing" role="status" aria-live="polite">
              <span className="spinner" aria-hidden="true" />
              <span>
                {submitProgress !== null && submitProgress < 1
                  ? `Uploading your resume… ${Math.round(submitProgress * 100)}%`
                  : "Submitting your application and reviewing your resume against the role requirements. This can take up to a minute — please keep this page open."}
              </span>
            </div>
          )}
          {formError && <Alert>{formError}</Alert>}
          {props.program && !props.program.ready && (
            <Alert>Complete the program details above and accept the program terms to submit.</Alert>
          )}
          <div className="apply-actions">
            <button type="button" className="btn btn-ghost" onClick={() => goTo("resume")} disabled={busy}>
              <Icon name="arrow-left" size={16} /> Back
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                if (busy) return;
                setFormError(null);
                submitMutation.mutate();
              }}
              disabled={
                busy ||
                !emailIsVerified ||
                !resume ||
                (base !== null && !draft?.education_check.can_continue) ||
                (props.program !== undefined && !props.program.ready)
              }
            >
              {submitMutation.isPending ? <Spinner label="Submitting…" /> : "Submit application"}
            </button>
          </div>
        </ReviewStep>
      )}
    </section>
  );
}

const EDUCATION_STATUS_LABEL: Record<string, string> = {
  VERIFIED: "Document checked",
  MANUAL_REVIEW: "Pending manual review",
};

function ReviewSection({
  title,
  onEdit,
  children,
}: {
  title: string;
  onEdit: () => void;
  children: ReactNode;
}) {
  return (
    <section className="apply-review-section" aria-label={title}>
      <header className="apply-review-header">
        <h4>{title}</h4>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onEdit}>
          Edit <span className="sr-only">{title.toLowerCase()}</span>
        </button>
      </header>
      <dl className="apply-summary">{children}</dl>
    </section>
  );
}

function ReviewStep({
  base,
  values,
  isExperienced,
  emailIsVerified,
  draft,
  educationLabel,
  resume,
  marksCard,
  contactEmail,
  onEdit,
  hasEducationStep,
  children,
}: {
  values: Values;
  isExperienced: boolean;
  emailIsVerified: boolean;
  draft: DraftView | null;
  educationLabel?: string;
  resume: File | null;
  marksCard: File | null;
  contactEmail: string | null;
  onEdit: (step: StepKey) => void;
  hasEducationStep: boolean;
  base: string | null;
  children: ReactNode;
}) {
  // Same query (and cache) as the Education step's options.
  const options = useQuery({
    queryKey: ["qualification-options", base],
    queryFn: () => getQualificationOptions(base ?? ""),
    enabled: base !== null,
    staleTime: 5 * 60_000,
  });
  const education = draft?.education ?? null;
  const qualificationLabel =
    education &&
    (education.degree_name ||
      options.data?.options.find((option) => option.key === education.qualification_key)?.label ||
      education.qualification_key);
  return (
    <div className="apply-step-body apply-review">
      <ReviewSection title="Basic information" onEdit={() => onEdit("details")}>
        <dt>Name</dt>
        <dd>{values.full_name}</dd>
        <dt>Email</dt>
        <dd>
          {values.email} {emailIsVerified && <span className="chip chip-verified">Verified</span>}
        </dd>
        <dt>Mobile</dt>
        <dd>{values.phone}</dd>
      </ReviewSection>
      <ReviewSection title="Professional background" onEdit={() => onEdit("background")}>
        <dt>Date of birth</dt>
        <dd>{values.date_of_birth}</dd>
        <dt>Place of birth</dt>
        <dd>{values.place_of_birth}</dd>
        <dt>Languages</dt>
        <dd>{values.languages.join(", ")}</dd>
        <dt>Profile</dt>
        <dd>
          {values.candidate_type === "" ? "" : CANDIDATE_TYPE_LABELS[values.candidate_type]}
          {isExperienced &&
            ` · ${values.years_experience} yrs · ${values.current_title} at ${values.current_company}`}
        </dd>
        <dt>Location</dt>
        <dd>
          {values.current_location} (prefers {values.preferred_location})
        </dd>
        {values.linkedin_url.trim() && (
          <>
            <dt>LinkedIn</dt>
            <dd>{values.linkedin_url.trim()}</dd>
          </>
        )}
        {values.github_url.trim() && (
          <>
            <dt>GitHub</dt>
            <dd>{values.github_url.trim()}</dd>
          </>
        )}
        {!hasEducationStep && (
          <>
            <dt>Qualification</dt>
            <dd>{educationLabel}</dd>
            {values.tenth_marks_percentage.trim() && (
              <>
                <dt>10th marks</dt>
                <dd>{values.tenth_marks_percentage.trim()}%</dd>
              </>
            )}
            {marksCard && (
              <>
                <dt>10th marks card</dt>
                <dd>{marksCard.name}</dd>
              </>
            )}
          </>
        )}
      </ReviewSection>
      {hasEducationStep && education && draft && (
        <ReviewSection title="Education" onEdit={() => onEdit("education")}>
          <dt>Qualification</dt>
          <dd>
            {qualificationLabel}
            {education.branch && ` in ${education.branch}`}
          </dd>
          <dt>Institution</dt>
          <dd>
            {education.institution} ({education.completion_year})
          </dd>
          {education.tenth_marks_percentage !== null && String(education.tenth_marks_percentage) && (
            <>
              <dt>10th marks</dt>
              <dd>{String(education.tenth_marks_percentage)}%</dd>
            </>
          )}
          {marksCard && (
            <>
              <dt>10th marks card</dt>
              <dd>{marksCard.name}</dd>
            </>
          )}
          <dt>Document</dt>
          <dd>
            {draft.document?.filename}{" "}
            <span
              className={`chip ${draft.education_check.status === "VERIFIED" ? "chip-verified" : "chip-pending"}`}
            >
              {EDUCATION_STATUS_LABEL[draft.education_check.status] ?? "Not verified"}
            </span>
          </dd>
        </ReviewSection>
      )}
      <ReviewSection title="Resume" onEdit={() => onEdit("resume")}>
        <dt>Resume</dt>
        <dd>{resume?.name}</dd>
      </ReviewSection>
      <p className="muted">
        Please check your details — once submitted, an application can't be edited online.
      </p>
      <UpdateInfoNote contactEmail={contactEmail} />
      {children}
    </div>
  );
}
