import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { PublicApplicationResult } from "../../types/careers";

/** A careers-site result, or a campus drive one (which may carry the
 * drive's assessment link). */
export type ApplicationOutcomeResult = PublicApplicationResult & {
  assessment_invitation_link?: string | null;
};

/** "Need to update your information?" — candidates can't edit a submitted
 * application themselves; they reach the organization's configured
 * recruitment contact (never a hardcoded address). */
export function UpdateInfoNote({ contactEmail }: { contactEmail: string | null }) {
  return (
    <p className="muted outcome-contact">
      Need to update your information?{" "}
      {contactEmail ? (
        <>
          Please contact <a href={`mailto:${contactEmail}`}>{contactEmail}</a>.
        </>
      ) : (
        "Please contact our recruitment team."
      )}
    </p>
  );
}

function OutcomeCard({
  tone,
  title,
  children,
}: {
  tone: "success" | "neutral";
  title: string;
  children: ReactNode;
}) {
  return (
    <section className={`card application-outcome application-outcome-${tone}`} role="status">
      {tone === "success" && (
        <div className="submission-success-check" aria-hidden="true">
          <svg viewBox="0 0 52 52" width="52" height="52">
            <circle className="submission-success-check-circle" cx="26" cy="26" r="24" fill="none" />
            <path className="submission-success-check-mark" fill="none" d="M14 27l7 7 17-17" />
          </svg>
        </div>
      )}
      <h2>{title}</h2>
      {children}
    </section>
  );
}

/** Whether the acknowledgement email went out — only claimed when the email
 * provider actually accepted it. */
function EmailStatus({ result }: { result: PublicApplicationResult }) {
  return result.confirmation_email_sent ? (
    <p className="muted">
      A confirmation email has been sent to <strong>{result.candidate_email}</strong>.
    </p>
  ) : (
    <p className="muted">
      We couldn't send a confirmation email right now, but your application has been received
      safely.
    </p>
  );
}

/**
 * What the candidate sees after submitting: a neutral confirmation, the
 * same whatever the AI screening decided. Submission is not selection —
 * the screen never shows a match/not-match, score, skills, recommendation
 * or any internal status (the API doesn't send them either; see
 * backend/app/schemas/public.py). Any decision reaches the candidate by
 * email from the recruitment team.
 */
export function ApplicationOutcome({
  result,
  careersHref,
}: {
  result: ApplicationOutcomeResult;
  /** Where "Back to Careers" leads. */
  careersHref: string;
}) {
  return (
    <OutcomeCard tone="success" title="Application Submitted">
      <p>
        Thank you for applying for <strong>{result.job_title}</strong>.
      </p>
      <p>
        Your application has been successfully submitted and will be reviewed by our recruitment
        team.
      </p>
      <p className="muted">
        If there is an update regarding your application, we will contact you by email.
      </p>
      {result.assessment_invitation_link && (
        <div className="application-outcome-next">
          <p>This drive includes an assessment — you can start it now.</p>
          <Link to={result.assessment_invitation_link} className="btn btn-primary">
            Start assessment
          </Link>
        </div>
      )}
      <EmailStatus result={result} />
      <UpdateInfoNote contactEmail={result.careers_contact_email} />
      <div className="application-outcome-actions">
        <Link
          to={careersHref}
          className={result.assessment_invitation_link ? "btn btn-ghost" : "btn btn-primary"}
        >
          Back to Careers
        </Link>
      </div>
    </OutcomeCard>
  );
}

/** Shown when this person already has a profile (by email or mobile). */
export function AlreadyRegistered({ message }: { message: string }) {
  return (
    <OutcomeCard tone="neutral" title="You've already applied">
      <p>{message}</p>
    </OutcomeCard>
  );
}

/**
 * Shown when this person has applied before and is still inside the
 * reapply restriction period. The wording comes from the backend, which
 * knows the window length and both dates (app/services/reapply_service.py)
 * — and only ever reaches someone who has just proven they own the email
 * address, so naming their own dates here is safe.
 *
 * Deliberately not a rejection: the profile stays on file, and the date
 * they can return is stated plainly rather than left as "you already
 * applied".
 */
export function ReapplyRestricted({
  message,
  eligibleFrom,
}: {
  message: string;
  /** ISO date the window opens, when the backend supplied it. */
  eligibleFrom?: string | null;
}) {
  return (
    <OutcomeCard tone="neutral" title="You've already applied recently">
      <p>{message}</p>
      {eligibleFrom && (
        <p className="muted">
          You can apply again from{" "}
          <strong>
            {new Date(eligibleFrom).toLocaleDateString(undefined, {
              day: "numeric",
              month: "long",
              year: "numeric",
            })}
          </strong>
          .
        </p>
      )}
    </OutcomeCard>
  );
}
