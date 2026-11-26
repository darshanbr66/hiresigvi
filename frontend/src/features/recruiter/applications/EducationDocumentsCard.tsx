import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "../../../lib/apiClient";
import { triggerBlobDownload } from "../../../lib/downloadBlob";
import { Alert } from "../../../shared/components/Alert";
import { ResumePreviewModal } from "../../../shared/components/ResumePreviewModal";
import type { DocumentVerification, EducationDocument } from "../../../types/recruitment";
import { formatFileSize } from "../../careers/apply/validation";
import { downloadEducationDocument, listEducationDocuments } from "./api";

const VERIFICATION: Record<DocumentVerification, { label: string; chip: string }> = {
  AI_VERIFIED: { label: "AI verified", chip: "chip-verified" },
  MANUAL_VERIFICATION_REQUIRED: { label: "Manual verification required", chip: "chip-pending" },
  NOT_UPLOADED: { label: "Not uploaded", chip: "chip-pending" },
};

function availability(document: EducationDocument): { label: string; chip: string } {
  if (!document.uploaded) {
    return { label: document.required ? "Not uploaded" : "Not uploaded (optional)", chip: "chip-pending" };
  }
  return document.available
    ? { label: "Available", chip: "chip-verified" }
    : { label: "File unavailable", chip: "chip-pending" };
}

/**
 * The candidate's education documents for this application — the academic
 * document from the Education step and the optional 10th marks card — with
 * View and Download. The academic document also shows its verification:
 * AI verified, manual verification required (with the reason) or not
 * uploaded. Files are fetched through the authorized recruiter
 * endpoint (same access as the resume); no storage URL is ever shown.
 */
export function EducationDocumentsCard({
  applicationId,
  token,
}: {
  applicationId: string;
  token: string;
}) {
  const [previewing, setPreviewing] = useState<EducationDocument | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["recruiter", "application-documents", applicationId],
    queryFn: () => listEducationDocuments(applicationId, token),
  });

  async function download(document: EducationDocument) {
    setError(null);
    setDownloading(document.kind);
    try {
      const { blob, filename } = await downloadEducationDocument(applicationId, document.kind, token);
      triggerBlobDownload(blob, filename ?? document.filename ?? document.kind);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not download the ${document.label.toLowerCase()}.`);
    } finally {
      setDownloading(null);
    }
  }

  return (
    <section className="card" aria-labelledby="education-documents-title">
      <h2 id="education-documents-title">Education documents</h2>
      {query.isPending && <p className="muted">Loading documents…</p>}
      {query.isError && <Alert>Could not load the education documents.</Alert>}
      {error && <Alert>{error}</Alert>}
      {query.data && (
        <ul className="education-documents">
          {query.data.documents.map((document) => {
            const state = availability(document);
            return (
              <li key={document.kind} className="education-document">
                <div className="education-document-info">
                  <p className="education-document-title">
                    <strong>{document.label}</strong>
                    <span className={`chip ${state.chip}`}>{state.label}</span>
                    {document.verification && document.verification !== "NOT_UPLOADED" && (
                      <span className={`chip ${VERIFICATION[document.verification].chip}`}>
                        {VERIFICATION[document.verification].label}
                      </span>
                    )}
                  </p>
                  <p className="muted">{document.description}</p>
                  {document.verification_reason && (
                    <p className="education-document-reason">
                      <strong>Reason for manual review:</strong> {document.verification_reason}
                    </p>
                  )}
                  {document.filename && (
                    <p className="education-document-file">
                      {document.filename}
                      {document.size_bytes !== null && ` · ${formatFileSize(document.size_bytes)}`}
                      {document.uploaded_at && ` · uploaded ${new Date(document.uploaded_at).toLocaleString()}`}
                    </p>
                  )}
                </div>
                {document.available && (
                  <div className="btn-group">
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={() => setPreviewing(document)}
                      aria-label={`View ${document.label.toLowerCase()}`}
                    >
                      View
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => void download(document)}
                      disabled={downloading === document.kind}
                      aria-label={`Download ${document.label.toLowerCase()}`}
                    >
                      {downloading === document.kind ? "Downloading…" : "Download"}
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {previewing && (
        <ResumePreviewModal
          title={previewing.label}
          subject={previewing.label.toLowerCase()}
          filename={previewing.filename ?? previewing.kind}
          fetchResume={() => downloadEducationDocument(applicationId, previewing.kind, token)}
          onClose={() => setPreviewing(null)}
        />
      )}
    </section>
  );
}
