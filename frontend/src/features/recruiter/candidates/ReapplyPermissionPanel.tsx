import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { Avatar } from "../../../shared/components/Avatar";
import { Spinner } from "../../../shared/components/Spinner";
import type { ReapplySearchResult } from "../../../types/recruitment";
import { useAuth } from "../../auth/AuthContext";
import { searchCandidatesForReapply } from "./api";
import { GrantReapplyModal } from "./GrantReapplyModal";

function formatDay(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/**
 * "Reapply Permission": find a candidate by name, email or mobile number and
 * grant them permission to apply again while the reapply window would
 * otherwise block them. Rendered only for roles holding
 * `candidate.reapply.grant`; the API enforces it regardless.
 */
export function ReapplyPermissionPanel() {
  const { accessToken } = useAuth();
  const [input, setInput] = useState("");
  const [term, setTerm] = useState("");
  const [validation, setValidation] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReapplySearchResult | null>(null);

  const searchQuery = useQuery({
    // Under ["recruiter", "candidates"], so a grant refreshes these results.
    queryKey: ["recruiter", "candidates", "reapply-search", term],
    queryFn: () => searchCandidatesForReapply(term, accessToken as string),
    enabled: accessToken !== null && term.length > 0,
  });

  function handleSearch(event: FormEvent) {
    event.preventDefault();
    const value = input.trim();
    if (value.length < 2) {
      setValidation("Enter at least 2 characters to search.");
      return;
    }
    setValidation(null);
    setTerm(value);
  }

  return (
    <section className="card stack-sm" aria-labelledby="reapply-permission-title">
      <div>
        <h2 id="reapply-permission-title" style={{ margin: 0 }}>
          Reapply Permission
        </h2>
        <p className="muted" style={{ margin: "0.25rem 0 0" }}>
          Let a candidate apply again before their reapply period ends.
        </p>
      </div>

      <form className="toolbar" onSubmit={handleSearch} role="search">
        <input
          className="search-input"
          aria-label="Search candidate by name, email or mobile"
          placeholder="Search candidate by name, email or mobile…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button type="submit" className="btn btn-ghost" disabled={searchQuery.isFetching}>
          {searchQuery.isFetching ? <Spinner label="Searching…" /> : "Search"}
        </button>
      </form>

      {validation && <Alert>{validation}</Alert>}
      {searchQuery.isError && (
        <Alert>
          {searchQuery.error instanceof ApiError
            ? searchQuery.error.message
            : "Unable to reach the server."}
        </Alert>
      )}
      {searchQuery.isSuccess && searchQuery.data.length === 0 && (
        <p className="muted" role="status">
          Candidate not found.
        </p>
      )}

      {searchQuery.isSuccess && searchQuery.data.length > 0 && (
        <ul className="reapply-results" aria-label="Matching candidates">
          {searchQuery.data.map((candidate) => (
            <li key={candidate.id} className="reapply-result">
              <span className="person-cell">
                <Avatar name={candidate.full_name} />
                <span>
                  <strong>{candidate.full_name}</strong>
                  <br />
                  <span className="muted">{candidate.email}</span>
                  {candidate.phone && (
                    <>
                      <br />
                      <span className="muted">{candidate.phone}</span>
                    </>
                  )}
                </span>
              </span>
              {candidate.can_self_apply_now ? (
                <span className="muted reapply-result-note">
                  Can already apply — no permission needed.
                </span>
              ) : (
                <span className="reapply-result-action">
                  {candidate.eligible_from && (
                    <span className="muted reapply-result-note">
                      Otherwise from {formatDay(candidate.eligible_from)}
                    </span>
                  )}
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => setSelected(candidate)}
                  >
                    Grant Reapply Permission
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {selected && <GrantReapplyModal candidate={selected} onClose={() => setSelected(null)} />}
    </section>
  );
}
