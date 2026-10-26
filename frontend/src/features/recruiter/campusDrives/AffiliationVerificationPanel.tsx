import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { Modal } from "../../../shared/components/Modal";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import {
  VERIFICATION_METHOD_LABELS,
  type AffiliationStatus,
  type AffiliationVerificationResponse,
  type CampusDriveResponse,
} from "../../../types/campusDrive";
import {
  addRosterEntries,
  listAffiliationVerifications,
  listRoster,
  removeRosterEntry,
  reviewAffiliation,
} from "./api";

const STATUS_BADGE: Record<AffiliationStatus, string> = {
  PENDING: "badge-warn",
  VERIFIED: "badge-active",
  REJECTED: "badge-inactive",
};

/** "email, roll number, name" per line — the last two optional. */
export function parseRosterText(text: string) {
  return text
    .split(/\r?\n/)
    .map((line) => line.split(/[,\t;]/).map((part) => part.trim()))
    .filter((parts) => parts[0] && parts[0].includes("@"))
    .map(([email, studentIdentifier, ...name]) => ({
      email,
      student_identifier: studentIdentifier || null,
      full_name: name.join(" ").trim() || null,
    }));
}

/**
 * Campus affiliation verification for one drive (docs/campus-hiring.md
 * § 3a). Opening the drive link never verifies anyone: automatic
 * verification happens only on a matching college email domain or the
 * college's own student list; everything else waits here for a person.
 */
export function AffiliationVerificationPanel({
  drive,
  accessToken,
}: {
  drive: CampusDriveResponse;
  accessToken: string;
}) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const verificationsKey = ["recruiter", "campus-drives", drive.id, "verifications"];
  const rosterKey = ["recruiter", "campus-drives", drive.id, "roster"];
  const usesRoster = drive.affiliation_verification_method === "STUDENT_LIST";

  const verificationsQuery = useQuery({
    queryKey: verificationsKey,
    queryFn: () => listAffiliationVerifications(drive.id, accessToken),
  });
  const rosterQuery = useQuery({
    queryKey: rosterKey,
    queryFn: () => listRoster(drive.id, accessToken),
    enabled: usesRoster,
  });

  const [reviewing, setReviewing] = useState<{
    record: AffiliationVerificationResponse;
    status: AffiliationStatus;
  } | null>(null);
  const [note, setNote] = useState("");
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [rosterText, setRosterText] = useState("");

  const reviewMutation = useMutation({
    mutationFn: () =>
      reviewAffiliation(
        drive.id,
        reviewing!.record.id,
        { status: reviewing!.status, note: note.trim() || null },
        accessToken,
      ),
    onSuccess: (record) => {
      showToast(`${record.candidate_full_name}: ${record.status.toLowerCase()}.`, "success");
      setReviewing(null);
      void queryClient.invalidateQueries({ queryKey: verificationsKey });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "campus-drives", drive.id, "funnel"] });
    },
    onError: (err) => setReviewError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });

  const rosterMutation = useMutation({
    mutationFn: () => addRosterEntries(drive.id, parseRosterText(rosterText), accessToken),
    onSuccess: (result) => {
      showToast(`${result.added} student(s) added (${result.total} on the list).`, "success");
      setRosterText("");
      void queryClient.invalidateQueries({ queryKey: rosterKey });
      void queryClient.invalidateQueries({ queryKey: verificationsKey });
    },
    onError: (err) =>
      showToast(err instanceof ApiError ? err.message : "Could not add the student list.", "error"),
  });

  const removeMutation = useMutation({
    mutationFn: (entryId: string) => removeRosterEntry(drive.id, entryId, accessToken),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: rosterKey }),
    onError: (err) => showToast(err instanceof ApiError ? err.message : "Could not remove the entry.", "error"),
  });

  function openReview(record: AffiliationVerificationResponse, status: AffiliationStatus) {
    setReviewing({ record, status });
    setNote("");
    setReviewError(null);
  }

  function submitReview(event: FormEvent) {
    event.preventDefault();
    if (reviewing?.status === "REJECTED" && !note.trim()) {
      setReviewError("Add a note explaining why the affiliation is rejected.");
      return;
    }
    reviewMutation.mutate();
  }

  const parsedCount = parseRosterText(rosterText).length;
  const records = verificationsQuery.data ?? [];

  return (
    <section className="card stack-sm" aria-labelledby={`verify-${drive.id}`}>
      <h2 id={`verify-${drive.id}`}>Institution verification</h2>
      <p className="muted">
        Method: {VERIFICATION_METHOD_LABELS[drive.affiliation_verification_method]}. Applying
        through this drive's link does not by itself verify that a candidate studies at{" "}
        {drive.institution_name}.
      </p>

      {verificationsQuery.isPending && <p role="status">Loading verifications…</p>}
      {verificationsQuery.isError && <Alert>Could not load verification records.</Alert>}
      {verificationsQuery.isSuccess && records.length === 0 && (
        <p className="muted">No applications to verify yet.</p>
      )}
      {records.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Status</th>
                <th>How</th>
                <th>Reviewed</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr key={record.id}>
                  <td>
                    <Link to={`/recruiter/applications/${record.application_id}`}>
                      {record.candidate_full_name}
                    </Link>
                    <span className="cell-sub">{record.candidate_email}</span>
                  </td>
                  <td>
                    <span className={`badge ${STATUS_BADGE[record.status]}`}>{record.status}</span>
                  </td>
                  <td>{record.method ? VERIFICATION_METHOD_LABELS[record.method] : "—"}</td>
                  <td>
                    {record.reviewed_at ? new Date(record.reviewed_at).toLocaleDateString() : "—"}
                    {record.reviewed_by_name && <span className="cell-sub">{record.reviewed_by_name}</span>}
                    {record.review_note && <span className="cell-sub">{record.review_note}</span>}
                  </td>
                  <td>
                    <div className="btn-group">
                      {record.status !== "VERIFIED" && (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => openReview(record, "VERIFIED")}>
                          Verify
                        </button>
                      )}
                      {record.status !== "REJECTED" && (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => openReview(record, "REJECTED")}>
                          Reject
                        </button>
                      )}
                      {record.status !== "PENDING" && (
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => openReview(record, "PENDING")}>
                          Reset
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {usesRoster && (
        <div className="stack-sm" style={{ marginTop: "1rem" }}>
          <h3>College-provided student list</h3>
          <p className="field-hint">
            Applicants whose verified email is on this list are verified automatically. Only
            emails (plus an optional roll number and name) are stored — no identity documents.
          </p>
          <label className="field">
            <span>Paste students (one per line: email, roll number, name)</span>
            <textarea
              rows={4}
              value={rosterText}
              onChange={(e) => setRosterText(e.target.value)}
              placeholder={"asha@college.edu, CS21B001, Asha R\nravi@college.edu"}
              disabled={rosterMutation.isPending}
            />
          </label>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            style={{ alignSelf: "flex-start" }}
            disabled={parsedCount === 0 || rosterMutation.isPending}
            onClick={() => rosterMutation.mutate()}
          >
            {rosterMutation.isPending ? <Spinner label="Adding…" /> : `Add ${parsedCount} student(s)`}
          </button>
          {rosterQuery.isSuccess && (
            <details>
              <summary>{rosterQuery.data.length} student(s) on the list</summary>
              <ul className="stack-sm" style={{ paddingLeft: "1rem" }}>
                {rosterQuery.data.map((entry) => (
                  <li key={entry.id} style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                    <span>
                      {entry.email}
                      {entry.student_identifier ? ` · ${entry.student_identifier}` : ""}
                      {entry.full_name ? ` · ${entry.full_name}` : ""}
                    </span>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => removeMutation.mutate(entry.id)}
                      disabled={removeMutation.isPending}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {reviewing && (
        <Modal
          title={`${reviewing.status === "PENDING" ? "Reset" : reviewing.status === "VERIFIED" ? "Verify" : "Reject"} affiliation`}
          onClose={() => !reviewMutation.isPending && setReviewing(null)}
        >
          <form onSubmit={submitReview}>
            <p>
              <strong>{reviewing.record.candidate_full_name}</strong> — {drive.institution_name}
            </p>
            <label className="field">
              <span>
                Note{" "}
                {reviewing.status === "REJECTED" ? (
                  "(required)"
                ) : (
                  <span className="optional-mark">(Optional)</span>
                )}
              </span>
              <textarea
                rows={3}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. Confirmed with the placement office"
                disabled={reviewMutation.isPending}
              />
            </label>
            {reviewError && <Alert>{reviewError}</Alert>}
            <div className="btn-group" style={{ marginTop: "1rem" }}>
              <button type="submit" className="btn btn-primary" disabled={reviewMutation.isPending}>
                {reviewMutation.isPending ? <Spinner label="Saving…" /> : "Save"}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setReviewing(null)} disabled={reviewMutation.isPending}>
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}
    </section>
  );
}
