/**
 * Candidate-facing submission confirmation — deliberately shows no
 * score/percentage (ZPHIREX platform overhaul § 4). Recruiters/admins see
 * the score elsewhere, per their permissions.
 */
export function SubmissionSuccess() {
  return (
    <section className="card submission-success" role="status">
      <div className="submission-success-check" aria-hidden="true">
        <svg viewBox="0 0 52 52" width="52" height="52">
          <circle className="submission-success-check-circle" cx="26" cy="26" r="24" fill="none" />
          <path className="submission-success-check-mark" fill="none" d="M14 27l7 7 17-17" />
        </svg>
      </div>
      <h2>Assessment Submitted</h2>
      <p className="muted">Your assessment has been successfully submitted.</p>
      <p className="muted">
        Thank you for completing the assessment. Our recruitment team will review your application
        and contact you regarding the next step.
      </p>
    </section>
  );
}
