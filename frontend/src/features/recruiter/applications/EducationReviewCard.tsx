import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { triggerBlobDownload } from "../../../lib/downloadBlob";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { downloadAcademicDocument, getApplicationEducation } from "./api";

const STATUS_LABEL = {
  VERIFIED: "Document consistent with selected qualification",
  MANUAL_REVIEW: "Manual review required",
} as const;

const OUTCOME_LABEL = {
  MATCH: "Match",
  MISMATCH: "Mismatch",
  UNCLEAR: "Unclear",
  FAILED: "Check failed",
} as const;

/**
 * The academic document a self-service applicant uploaded, and the AI
 * consistency checks of it — what HR needs to review a MANUAL_REVIEW
 * application. Advisory only: never presented as proof of authenticity.
 */
export function EducationReviewCard({
  applicationId,
  token,
  status,
}: {
  applicationId: string;
  token: string;
  status: "VERIFIED" | "MANUAL_REVIEW";
}) {
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const query = useQuery({
    queryKey: ["recruiter", "application-education", applicationId],
    queryFn: () => getApplicationEducation(applicationId, token),
  });

  async function download() {
    setDownloadError(null);
    setDownloading(true);
    try {
      const { blob, filename } = await downloadAcademicDocument(applicationId, token);
      triggerBlobDownload(blob, filename ?? query.data?.document?.filename ?? "academic-document");
    } catch (err) {
      setDownloadError(err instanceof ApiError ? err.message : "Could not download the document.");
    } finally {
      setDownloading(false);
    }
  }

  const latest = query.data?.checks[0];
  return (
    <section className="card" aria-labelledby="education-review-title">
      <h2 id="education-review-title">Education document</h2>
      <p>
        <span className={`chip ${status === "VERIFIED" ? "chip-verified" : "chip-pending"}`}>
          {STATUS_LABEL[status]}
        </span>
      </p>
      {query.data?.eligibility_review_required && (
        <div className="apply-notice" role="note">
          <p>
            <strong>Eligibility needs checking.</strong>{" "}
            {query.data.eligibility_review_reasons.length === 0 &&
              "The candidate's qualification couldn't be matched to this job's requirements automatically."}{" "}
            Please check it against the job description.
          </p>
          {query.data.eligibility_review_reasons.length > 0 && (
            <ul>
              {query.data.eligibility_review_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {query.isError && <Alert>Could not load the education details.</Alert>}
      {query.data && (
        <>
          <dl className="apply-summary">
            {query.data.highest_qualification ? (
              <>
                <dt>Highest qualification</dt>
                <dd>{query.data.highest_qualification}</dd>
                <dt>Specialization category</dt>
                <dd>{query.data.specialization_category ?? "—"}</dd>
                <dt>Specialization</dt>
                <dd>{query.data.specialization ?? "—"}</dd>
                {query.data.job_fields_of_study.length > 0 && (
                  <>
                    <dt>
                      {query.data.job_field_mandatory
                        ? "Job's required field of study"
                        : "Job's field of study"}
                    </dt>
                    <dd>
                      {query.data.job_fields_of_study.join(" or ")}
                      {query.data.matched_field_of_study ? (
                        <span className="chip chip-verified education-field-chip">
                          Matches specialization
                        </span>
                      ) : (
                        query.data.job_field_mandatory && (
                          <span className="chip chip-pending education-field-chip">
                            Not matched automatically
                          </span>
                        )
                      )}
                    </dd>
                  </>
                )}
              </>
            ) : (
              <>
                <dt>Qualification</dt>
                <dd>{query.data.qualification ?? "—"}</dd>
              </>
            )}
            <dt>Institution</dt>
            <dd>
              {query.data.institution ?? "—"}
              {query.data.qualification_year ? ` (${query.data.qualification_year})` : ""}
            </dd>
            {latest && (
              <>
                <dt>Latest AI check</dt>
                <dd>
                  {OUTCOME_LABEL[latest.outcome]}
                  {latest.confidence !== null && ` · confidence ${Math.round(latest.confidence * 100)}%`}
                </dd>
                {latest.selected_qualification && (
                  <>
                    <dt>Checked against</dt>
                    <dd>
                      {[
                        latest.selected_qualification,
                        latest.selected_specialization_category,
                        latest.selected_specialization,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </dd>
                  </>
                )}
                {latest.detected_level && (
                  <>
                    <dt>Document appears to show</dt>
                    <dd>
                      {[latest.degree_text, latest.branch_text, latest.institution_text]
                        .filter(Boolean)
                        .join(" · ") || latest.detected_level}
                    </dd>
                  </>
                )}
                {latest.notes && (
                  <>
                    <dt>Notes</dt>
                    <dd>{latest.notes}</dd>
                  </>
                )}
              </>
            )}
          </dl>
          {downloadError && <Alert>{downloadError}</Alert>}
          {query.data.document && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => void download()}
              disabled={downloading}
            >
              {downloading ? "Downloading…" : `Download ${query.data.document.filename}`}
            </button>
          )}
          <p className="muted" style={{ fontSize: "0.8rem", marginTop: "0.75rem" }}>
            An automated consistency check of the document against the selected qualification —
            not a verification of authenticity. {query.data.checks.length} check
            {query.data.checks.length === 1 ? "" : "s"} recorded.
          </p>
        </>
      )}
    </section>
  );
}
