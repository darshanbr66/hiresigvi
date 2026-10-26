import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { Modal } from "../../../shared/components/Modal";
import { SkeletonLines } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { QUESTION_TYPE_LABELS, type QuestionCreate, type QuestionType } from "../../../types/assessment";
import { DIFFICULTY_LABELS, type QuestionDifficulty } from "../../../types/questionBank";
import { getQuestion, listCategories, searchQuestions } from "../questionBank/api";

const PAGE_SIZE = 20;

/** Questions are usually published in the Question Bank in another tab
 * (the picker links there so the test being built is kept), and that tab's
 * cache invalidation never reaches this one. So the picker never trusts a
 * cached result: it refetches whenever it opens and whenever the window
 * regains focus, overriding the app-wide 30s `staleTime`. */
const ALWAYS_FRESH = { staleTime: 0, refetchOnMount: "always", refetchOnWindowFocus: "always" } as const;

/**
 * Selects PUBLISHED question-bank questions for a test. The chosen
 * questions come back as `QuestionCreate`s carrying `bank_version_id` —
 * the server copies that exact version (content and answer key), so what
 * the recruiter sees here is exactly what candidates get.
 */
export function BankQuestionPicker({
  accessToken,
  alreadySelected,
  onAdd,
  onClose,
}: {
  accessToken: string;
  /** `bank_version_id`s already in the test. */
  alreadySelected: string[];
  onAdd: (questions: QuestionCreate[]) => void;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [type, setType] = useState<QuestionType | "">("");
  const [difficulty, setDifficulty] = useState<QuestionDifficulty | "">("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const categoriesQuery = useQuery({
    queryKey: ["recruiter", "question-bank", "categories"],
    queryFn: () => listCategories(accessToken),
    ...ALWAYS_FRESH,
  });
  const filters = {
    q: search.trim() || undefined,
    category_id: categoryId || undefined,
    type,
    difficulty,
    status: "PUBLISHED" as const,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const resultsQuery = useQuery({
    queryKey: ["recruiter", "question-bank", "picker", filters],
    queryFn: () => searchQuestions(filters, accessToken),
    ...ALWAYS_FRESH,
  });
  // When nothing published matches, check whether drafts do — the usual
  // reason after an import (imports are saved as drafts by default), and
  // otherwise indistinguishable from an empty bank.
  const noPublishedMatches = resultsQuery.isSuccess && resultsQuery.data.items.length === 0;
  const draftFilters = { ...filters, status: "DRAFT" as const, limit: 1, offset: 0 };
  const draftMatchesQuery = useQuery({
    queryKey: ["recruiter", "question-bank", "picker", draftFilters],
    queryFn: () => searchQuestions(draftFilters, accessToken),
    enabled: noPublishedMatches,
    ...ALWAYS_FRESH,
  });
  const draftMatches = noPublishedMatches ? (draftMatchesQuery.data?.total ?? 0) : 0;

  function toggle(id: string) {
    setSelected((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current, id]));
  }

  async function addSelected() {
    setAdding(true);
    setError(null);
    try {
      const items = await Promise.all(selected.map((id) => getQuestion(id, accessToken)));
      onAdd(
        items.map((item) => {
          const version = item.current_version;
          return {
            prompt: version.prompt,
            type: version.type,
            points: version.points,
            negative_points: version.negative_points,
            options: version.options.map((o) => ({ label: o.label, is_correct: o.is_correct })),
            accepted_answers: version.accepted_answers,
            case_sensitive: version.case_sensitive,
            grading_notes: version.grading_notes,
            explanation: version.explanation,
            bank_version_id: version.id,
          };
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the selected questions.");
    } finally {
      setAdding(false);
    }
  }

  const total = resultsQuery.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <Modal title="Add questions from the question bank" onClose={onClose} wide>
      <div className="stack-sm">
        <div className="field-row">
          <label className="field">
            <span>Search</span>
            <input
              type="search"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
              placeholder="Question text, subject, topic or tag"
            />
          </label>
          <label className="field">
            <span>Subject / topic</span>
            <select value={categoryId} onChange={(e) => { setCategoryId(e.target.value); setPage(0); }}>
              <option value="">All</option>
              {categoriesQuery.data?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.path}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Type</span>
            <select value={type} onChange={(e) => { setType(e.target.value as QuestionType | ""); setPage(0); }}>
              <option value="">All</option>
              {(Object.keys(QUESTION_TYPE_LABELS) as QuestionType[]).map((t) => (
                <option key={t} value={t}>
                  {QUESTION_TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Difficulty</span>
            <select
              value={difficulty}
              onChange={(e) => { setDifficulty(e.target.value as QuestionDifficulty | ""); setPage(0); }}
            >
              <option value="">All</option>
              {(Object.keys(DIFFICULTY_LABELS) as QuestionDifficulty[]).map((d) => (
                <option key={d} value={d}>
                  {DIFFICULTY_LABELS[d]}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="btn-group">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => void resultsQuery.refetch()}
            disabled={resultsQuery.isFetching}
          >
            {resultsQuery.isFetching && !resultsQuery.isPending ? <Spinner label="Refreshing…" /> : "Refresh"}
          </button>
        </div>

        {resultsQuery.isPending && <SkeletonLines count={4} />}
        {resultsQuery.isError && <Alert>Could not load the question bank.</Alert>}
        {noPublishedMatches && (
          <div className="stack-sm">
            <p className="muted">No published questions match these filters.</p>
            {draftMatches > 0 && (
              <p className="field-hint" role="status">
                {draftMatches === 1
                  ? "1 matching question is still a draft."
                  : `${draftMatches} matching questions are still drafts.`}{" "}
                Only published questions can be added to a test —{" "}
                <Link to="/recruiter/question-bank" target="_blank" rel="noreferrer">
                  publish them in the Question Bank
                </Link>{" "}
                (opens in a new tab, so this test is kept), then come back — the list refreshes.
              </p>
            )}
          </div>
        )}
        {resultsQuery.isSuccess && resultsQuery.data.items.length > 0 && (
          <ul className="stack-sm" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {resultsQuery.data.items.map((item) => {
              const inTest = alreadySelected.includes(item.current_version_id);
              return (
                <li key={item.id} className="card" style={{ background: "var(--color-bg)", padding: "0.75rem" }}>
                  <label style={{ display: "flex", gap: "0.6rem", alignItems: "flex-start" }}>
                    <input
                      type="checkbox"
                      checked={selected.includes(item.id)}
                      onChange={() => toggle(item.id)}
                      disabled={inTest}
                      style={{ width: "auto", marginTop: "0.25rem" }}
                    />
                    <span>
                      <strong>{item.prompt}</strong>
                      <br />
                      <span className="muted">
                        {QUESTION_TYPE_LABELS[item.type]} · {item.points} mark(s) ·{" "}
                        {DIFFICULTY_LABELS[item.difficulty]}
                        {item.category_path ? ` · ${item.category_path}` : ""}
                        {item.tags.length > 0 ? ` · ${item.tags.join(", ")}` : ""}
                        {inTest ? " · already in this test" : ""}
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}

        {total > PAGE_SIZE && (
          <div className="btn-group" aria-label="Pagination">
            <button type="button" className="btn btn-ghost btn-sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <span className="muted">
              Page {page + 1} of {pageCount}
            </span>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={page + 1 >= pageCount}
              onClick={() => setPage(page + 1)}
            >
              Next
            </button>
          </div>
        )}

        {error && <Alert>{error}</Alert>}

        <div className="btn-group" style={{ marginTop: "0.5rem" }}>
          <button
            type="button"
            className="btn btn-primary"
            disabled={selected.length === 0 || adding}
            onClick={() => void addSelected()}
          >
            {adding ? <Spinner label="Adding…" /> : `Add ${selected.length} question(s)`}
          </button>
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={adding}>
            Cancel
          </button>
        </div>
      </div>
    </Modal>
  );
}
