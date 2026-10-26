import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { EmptyState } from "../../../shared/components/EmptyState";
import { Modal } from "../../../shared/components/Modal";
import { Pagination } from "../../../shared/components/Pagination";
import { SkeletonTable } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import { QUESTION_TYPE_LABELS, type QuestionCreate, type QuestionType } from "../../../types/assessment";
import {
  BANK_STATUS_LABELS,
  DIFFICULTY_LABELS,
  type BankItemListEntry,
  type BankItemResponse,
  type BankQuestionContent,
  type QuestionBankStatus,
  type QuestionDifficulty,
} from "../../../types/questionBank";
import { useAuth } from "../../auth/AuthContext";
import { emptyQuestion } from "../assessments/AssessmentForm";
import { QuestionEditor } from "../assessments/QuestionEditor";
import { QuestionImportDialog } from "./QuestionImportDialog";
import {
  changeQuestionStatus,
  changeQuestionsStatus,
  createCategory,
  createQuestion,
  getQuestion,
  listCategories,
  listTags,
  previewQuestion,
  searchQuestions,
  updateCategory,
  updateQuestion,
} from "./api";

const PAGE_SIZE = 25;
const BANK_KEY = ["recruiter", "question-bank"];

const STATUS_BADGE: Record<QuestionBankStatus, string> = {
  DRAFT: "badge-inactive",
  PUBLISHED: "badge-active",
  ARCHIVED: "badge-warn",
};

/** The status moves offered from each state (mirrors the server's table). */
const STATUS_ACTIONS: Record<QuestionBankStatus, { label: string; to: QuestionBankStatus }[]> = {
  DRAFT: [
    { label: "Publish", to: "PUBLISHED" },
    { label: "Archive", to: "ARCHIVED" },
  ],
  PUBLISHED: [
    { label: "Back to draft", to: "DRAFT" },
    { label: "Archive", to: "ARCHIVED" },
  ],
  ARCHIVED: [{ label: "Restore to draft", to: "DRAFT" }],
};

interface EditorState {
  item: BankItemResponse | null;
  question: QuestionCreate;
  categoryId: string;
  difficulty: QuestionDifficulty;
  tags: string;
  publish: boolean;
}

function toContent(question: QuestionCreate): BankQuestionContent {
  return {
    type: question.type,
    prompt: question.prompt,
    points: question.points,
    negative_points: question.negative_points ?? 0,
    options: question.options,
    accepted_answers: question.accepted_answers ?? [],
    case_sensitive: question.case_sensitive ?? false,
    grading_notes: question.grading_notes ?? null,
    explanation: question.explanation ?? null,
  };
}

function fromItem(item: BankItemResponse): QuestionCreate {
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
  };
}

function splitTags(text: string): string[] {
  return text.split(",").map((t) => t.trim()).filter(Boolean);
}

/** Header checkbox for "select all on this page", with the mixed state when
 * only some rows are selected. `indeterminate` is a DOM property. */
function SelectPageCheckbox({
  checked,
  indeterminate,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label="Select all questions on this page"
      checked={checked}
      onChange={onChange}
      style={{ width: "auto" }}
    />
  );
}

/**
 * The organization's reusable question bank: questions written once and
 * reused across campus drives, regular hiring and programs. Editing a
 * question's content creates a new version; tests that already use an
 * older version keep it unchanged.
 */
export function QuestionBankPage() {
  const { accessToken, user } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const canManage = user?.roles.some((r) => ["ORG_ADMIN", "RECRUITER"].includes(r)) ?? false;

  const [search, setSearch] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [type, setType] = useState<QuestionType | "">("");
  const [difficulty, setDifficulty] = useState<QuestionDifficulty | "">("");
  const [status, setStatus] = useState<QuestionBankStatus | "">("");
  const [tagId, setTagId] = useState("");
  const [page, setPage] = useState(1);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [categoriesOpen, setCategoriesOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const categoriesQuery = useQuery({
    queryKey: [...BANK_KEY, "categories"],
    queryFn: () => listCategories(token),
    enabled: accessToken !== null,
  });
  const tagsQuery = useQuery({
    queryKey: [...BANK_KEY, "tags"],
    queryFn: () => listTags(token),
    enabled: accessToken !== null,
  });
  const filters = {
    q: search.trim() || undefined,
    category_id: categoryId || undefined,
    type,
    difficulty,
    status,
    tag_id: tagId || undefined,
    include_archived: status === "ARCHIVED",
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  };
  const listQuery = useQuery({
    queryKey: [...BANK_KEY, "questions", filters],
    queryFn: () => searchQuestions(filters, token),
    enabled: accessToken !== null,
    placeholderData: keepPreviousData,
  });
  // Drafts (e.g. a fresh import) can't be added to tests — say so, so a
  // recruiter isn't left wondering why the test builder's picker is empty.
  const draftCountQuery = useQuery({
    queryKey: [...BANK_KEY, "questions", "draft-count"],
    queryFn: () => searchQuestions({ status: "DRAFT", limit: 1 }, token),
    enabled: accessToken !== null,
  });
  const draftCount = draftCountQuery.data?.total ?? 0;

  // Only rows on screen count as selected: a filter or page change never
  // leaves hidden questions selected.
  const pageItems = listQuery.data?.items ?? [];
  const selectedEntries = pageItems.filter((entry) => selected.has(entry.id));
  const selectedIds = selectedEntries.map((entry) => entry.id);
  const allOnPageSelected = pageItems.length > 0 && selectedIds.length === pageItems.length;

  function toggleSelected(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectPage() {
    setSelected(allOnPageSelected ? new Set() : new Set(pageItems.map((entry) => entry.id)));
  }

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: BANK_KEY });
  }

  function filterChange(apply: () => void) {
    apply();
    setPage(1);
    setSelected(new Set());
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!editor) throw new Error("Nothing to save.");
      const content = toContent(editor.question);
      if (editor.item === null) {
        return createQuestion(
          {
            ...content,
            category_id: editor.categoryId || null,
            difficulty: editor.difficulty,
            tags: splitTags(editor.tags),
            status: editor.publish ? "PUBLISHED" : "DRAFT",
          },
          token,
        );
      }
      return updateQuestion(
        editor.item.id,
        {
          content,
          ...(editor.categoryId ? { category_id: editor.categoryId } : { clear_category: true }),
          difficulty: editor.difficulty,
          tags: splitTags(editor.tags),
        },
        token,
      );
    },
    onSuccess: (saved) => {
      showToast(
        editor?.item && saved.current_version.version_number > editor.item.current_version.version_number
          ? `Saved as version ${saved.current_version.version_number}. Tests using earlier versions are unchanged.`
          : "Question saved.",
        "success",
      );
      setEditor(null);
      invalidate();
    },
    onError: (err) => setEditorError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });

  const bulkStatusMutation = useMutation({
    mutationFn: (to: QuestionBankStatus) => changeQuestionsStatus(selectedIds, to, token),
    onSuccess: (result) => {
      const count = result.item_ids.length;
      showToast(
        `${count} question${count === 1 ? " is" : "s are"} now ${BANK_STATUS_LABELS[result.status].toLowerCase()}.`,
        "success",
      );
      setSelected(new Set());
      invalidate();
    },
    onError: (err) => showToast(err instanceof ApiError ? err.message : "Could not change the status.", "error"),
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, to }: { id: string; to: QuestionBankStatus }) => changeQuestionStatus(id, to, token),
    onSuccess: (saved) => {
      showToast(`Question is now ${BANK_STATUS_LABELS[saved.status].toLowerCase()}.`, "success");
      invalidate();
    },
    onError: (err) => showToast(err instanceof ApiError ? err.message : "Could not change the status.", "error"),
  });

  function openNew() {
    setEditorError(null);
    setEditor({ item: null, question: emptyQuestion(), categoryId: "", difficulty: "MEDIUM", tags: "", publish: false });
  }

  async function openEdit(entry: BankItemListEntry) {
    setEditorError(null);
    try {
      const item = await getQuestion(entry.id, token);
      setEditor({
        item,
        question: fromItem(item),
        categoryId: item.category_id ?? "",
        difficulty: item.difficulty,
        tags: item.tags.join(", "),
        publish: item.status === "PUBLISHED",
      });
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Could not load the question.", "error");
    }
  }

  function submitEditor(event: FormEvent) {
    event.preventDefault();
    saveMutation.mutate();
  }

  const forbidden = listQuery.error instanceof ApiError && listQuery.error.status === 403;
  const total = listQuery.data?.total ?? 0;

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Question Bank</h1>
          <p className="muted">
            Write questions once and reuse them in any test. Answer keys stay here and are never
            shown to candidates.
          </p>
        </div>
        {canManage && !forbidden && (
          <div className="btn-group">
            <button type="button" className="btn btn-ghost" onClick={() => setCategoriesOpen(true)}>
              Subjects &amp; topics
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setImportOpen(true)}>
              Import questions
            </button>
            <button type="button" className="btn btn-primary" onClick={openNew}>
              + New question
            </button>
          </div>
        )}
      </div>

      {!forbidden && (
        <div className="filter-bar" role="search" aria-label="Filter questions">
          <label className="field">
            <span>Search</span>
            <input
              type="search"
              value={search}
              placeholder="Question text, subject, topic or tag"
              onChange={(e) => filterChange(() => setSearch(e.target.value))}
            />
          </label>
          <label className="field">
            <span>Subject / topic</span>
            <select value={categoryId} onChange={(e) => filterChange(() => setCategoryId(e.target.value))}>
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
            <select value={type} onChange={(e) => filterChange(() => setType(e.target.value as QuestionType | ""))}>
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
              onChange={(e) => filterChange(() => setDifficulty(e.target.value as QuestionDifficulty | ""))}
            >
              <option value="">All</option>
              {(Object.keys(DIFFICULTY_LABELS) as QuestionDifficulty[]).map((d) => (
                <option key={d} value={d}>
                  {DIFFICULTY_LABELS[d]}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Status</span>
            <select
              value={status}
              onChange={(e) => filterChange(() => setStatus(e.target.value as QuestionBankStatus | ""))}
            >
              <option value="">Draft &amp; published</option>
              {(Object.keys(BANK_STATUS_LABELS) as QuestionBankStatus[]).map((s) => (
                <option key={s} value={s}>
                  {BANK_STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Tag</span>
            <select value={tagId} onChange={(e) => filterChange(() => setTagId(e.target.value))}>
              <option value="">All</option>
              {tagsQuery.data?.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.question_count})
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {canManage && draftCount > 0 && status !== "DRAFT" && (
        <div className="toolbar" role="note" style={{ alignItems: "center", gap: "0.75rem" }}>
          <span>
            {draftCount === 1 ? "1 question is a draft" : `${draftCount} questions are drafts`} — drafts
            can&apos;t be added to tests until they are published.
          </span>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => filterChange(() => setStatus("DRAFT"))}
          >
            Review drafts
          </button>
        </div>
      )}

      {canManage && selectedIds.length > 0 && (
        <div className="toolbar" role="toolbar" aria-label="Bulk actions" style={{ alignItems: "center", gap: "0.75rem" }}>
          <strong>{selectedIds.length} selected</strong>
          {selectedEntries.some((entry) => entry.status === "DRAFT") &&
            selectedEntries.every((entry) => entry.status !== "ARCHIVED") && (
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={bulkStatusMutation.isPending}
                onClick={() => bulkStatusMutation.mutate("PUBLISHED")}
              >
                Publish selected
              </button>
            )}
          {selectedEntries.every((entry) => entry.status !== "ARCHIVED") && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={bulkStatusMutation.isPending}
              onClick={() => bulkStatusMutation.mutate("ARCHIVED")}
            >
              Archive selected
            </button>
          )}
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set())}>
            Clear
          </button>
        </div>
      )}

      {listQuery.isPending && <SkeletonTable columns={6} />}
      {forbidden && <Alert>You do not have permission to view the question bank.</Alert>}
      {listQuery.isError && !forbidden && <Alert>Could not load the question bank.</Alert>}
      {listQuery.isSuccess && listQuery.data.items.length === 0 && (
        <EmptyState icon="questionBank" title="No questions found">
          {total === 0 && !search && !categoryId && !type && !difficulty && !status && !tagId
            ? "Add your first question to start building reusable tests."
            : "No questions match these filters."}
        </EmptyState>
      )}
      {listQuery.isSuccess && listQuery.data.items.length > 0 && (
        <>
          <div className={listQuery.isPlaceholderData ? "table-scroll is-refreshing" : "table-scroll"}>
            <table className="data-table">
              <thead>
                <tr>
                  {canManage && (
                    <th style={{ width: "2.5rem" }}>
                      <SelectPageCheckbox
                        checked={allOnPageSelected}
                        indeterminate={selectedIds.length > 0 && !allOnPageSelected}
                        onChange={toggleSelectPage}
                      />
                    </th>
                  )}
                  <th>Question</th>
                  <th>Type</th>
                  <th>Subject / topic</th>
                  <th>Difficulty</th>
                  <th>Marks</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {listQuery.data.items.map((entry) => (
                  <tr key={entry.id} className={selected.has(entry.id) ? "is-selected" : undefined}>
                    {canManage && (
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select question: ${entry.prompt}`}
                          checked={selected.has(entry.id)}
                          onChange={() => toggleSelected(entry.id)}
                          style={{ width: "auto" }}
                        />
                      </td>
                    )}
                    <td style={{ maxWidth: "28rem" }}>
                      {entry.prompt}
                      <span className="cell-sub">
                        v{entry.version_number}
                        {entry.tags.length > 0 ? ` · ${entry.tags.join(", ")}` : ""}
                      </span>
                    </td>
                    <td>{QUESTION_TYPE_LABELS[entry.type]}</td>
                    <td>{entry.category_path ?? "—"}</td>
                    <td>{DIFFICULTY_LABELS[entry.difficulty]}</td>
                    <td>{entry.points}</td>
                    <td>
                      <span className={`badge ${STATUS_BADGE[entry.status]}`}>{BANK_STATUS_LABELS[entry.status]}</span>
                    </td>
                    <td>
                      <div className="btn-group">
                        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPreviewId(entry.id)}>
                          Preview
                        </button>
                        {canManage && entry.status !== "ARCHIVED" && (
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void openEdit(entry)}>
                            Edit
                          </button>
                        )}
                        {canManage &&
                          STATUS_ACTIONS[entry.status].map((action) => (
                            <button
                              key={action.to}
                              type="button"
                              className="btn btn-ghost btn-sm"
                              disabled={statusMutation.isPending}
                              onClick={() => statusMutation.mutate({ id: entry.id, to: action.to })}
                            >
                              {action.label}
                            </button>
                          ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            noun={total === 1 ? "question" : "questions"}
            disabled={listQuery.isPlaceholderData}
            onPageChange={(next) => {
              setPage(next);
              setSelected(new Set());
            }}
          />
        </>
      )}

      {editor && (
        <Modal
          title={editor.item ? `Edit question (currently v${editor.item.current_version.version_number})` : "New question"}
          onClose={() => !saveMutation.isPending && setEditor(null)}
          wide
        >
          <form onSubmit={submitEditor} className="stack-sm">
            {editor.item && editor.item.assessment_usage_count > 0 && (
              <Alert>
                Used in {editor.item.assessment_usage_count} test(s). Saving content changes creates a
                new version; those tests keep the version they already have.
              </Alert>
            )}
            <QuestionEditor
              heading="Question"
              value={editor.question}
              onChange={(question) => setEditor({ ...editor, question })}
              disabled={saveMutation.isPending}
            />
            <div className="field-row">
              <label className="field">
                <span>
                  Subject / topic <span className="optional-mark">(Optional)</span>
                </span>
                <select
                  value={editor.categoryId}
                  onChange={(e) => setEditor({ ...editor, categoryId: e.target.value })}
                  disabled={saveMutation.isPending}
                >
                  <option value="">None</option>
                  {categoriesQuery.data
                    ?.filter((c) => c.is_active || c.id === editor.categoryId)
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.path}
                      </option>
                    ))}
                </select>
              </label>
              <label className="field">
                <span>Difficulty</span>
                <select
                  value={editor.difficulty}
                  onChange={(e) => setEditor({ ...editor, difficulty: e.target.value as QuestionDifficulty })}
                  disabled={saveMutation.isPending}
                >
                  {(Object.keys(DIFFICULTY_LABELS) as QuestionDifficulty[]).map((d) => (
                    <option key={d} value={d}>
                      {DIFFICULTY_LABELS[d]}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label className="field">
              <span>
                Tags <span className="optional-mark">(Optional — comma separated)</span>
              </span>
              <input
                value={editor.tags}
                onChange={(e) => setEditor({ ...editor, tags: e.target.value })}
                placeholder="e.g. React, hooks, campus-2026"
                disabled={saveMutation.isPending}
                list="question-bank-tags"
              />
              <datalist id="question-bank-tags">
                {tagsQuery.data?.map((t) => <option key={t.id} value={t.name} />)}
              </datalist>
            </label>
            {editor.item === null && (
              <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <input
                  type="checkbox"
                  checked={editor.publish}
                  onChange={(e) => setEditor({ ...editor, publish: e.target.checked })}
                  disabled={saveMutation.isPending}
                  style={{ width: "auto" }}
                />
                Publish now (published questions can be added to tests)
              </label>
            )}
            {editorError && <Alert>{editorError}</Alert>}
            <div className="btn-group">
              <button type="submit" className="btn btn-primary" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? <Spinner label="Saving…" /> : "Save question"}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setEditor(null)} disabled={saveMutation.isPending}>
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {importOpen && (
        <QuestionImportDialog
          accessToken={token}
          onClose={() => setImportOpen(false)}
          onImported={(imported, categoriesCreated, savedAs) => {
            setImportOpen(false);
            invalidate();
            showToast(
              `Imported ${imported} question${imported === 1 ? "" : "s"}` +
                (categoriesCreated > 0 ? `; ${categoriesCreated} new subject/topic categor${categoriesCreated === 1 ? "y was" : "ies were"} added.` : ".") +
                (savedAs === "DRAFT" ? " They are drafts — publish them to use them in tests." : ""),
              "success",
            );
          }}
        />
      )}
      {previewId && <QuestionPreview itemId={previewId} accessToken={token} onClose={() => setPreviewId(null)} />}
      {categoriesOpen && (
        <CategoryManager accessToken={token} onClose={() => setCategoriesOpen(false)} onChanged={invalidate} />
      )}
    </div>
  );
}

/** The candidate's view of a question — fetched from the preview endpoint,
 * which carries no answer key. */
function QuestionPreview({ itemId, accessToken, onClose }: { itemId: string; accessToken: string; onClose: () => void }) {
  const previewQuery = useQuery({
    queryKey: [...BANK_KEY, "preview", itemId],
    queryFn: () => previewQuestion(itemId, accessToken),
  });
  return (
    <Modal title="Candidate preview" onClose={onClose}>
      {previewQuery.isPending && <p role="status">Loading…</p>}
      {previewQuery.isError && <Alert>Could not load the preview.</Alert>}
      {previewQuery.isSuccess && (
        <div className="stack-sm">
          <p>
            <strong>{previewQuery.data.prompt}</strong>{" "}
            <span className="muted">({previewQuery.data.points} mark(s))</span>
          </p>
          {previewQuery.data.options.length > 0 ? (
            <ul>
              {previewQuery.data.options.map((o) => (
                <li key={o.key}>{o.label}</li>
              ))}
            </ul>
          ) : (
            <textarea rows={previewQuery.data.type === "SHORT_ANSWER" ? 1 : 5} disabled aria-label="Answer box" />
          )}
          <p className="field-hint">This is all a candidate sees — no correct answers or notes.</p>
        </div>
      )}
    </Modal>
  );
}

function CategoryManager({
  accessToken,
  onClose,
  onChanged,
}: {
  accessToken: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const categoriesQuery = useQuery({
    queryKey: [...BANK_KEY, "categories"],
    queryFn: () => listCategories(accessToken),
  });

  const createMutation = useMutation({
    mutationFn: () => createCategory({ name: name.trim(), parent_id: parentId || null }, accessToken),
    onSuccess: (category) => {
      showToast(`Added ${category.path}.`, "success");
      setName("");
      setError(null);
      void queryClient.invalidateQueries({ queryKey: [...BANK_KEY, "categories"] });
      onChanged();
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });
  const toggleMutation = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => updateCategory(id, { is_active: active }, accessToken),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [...BANK_KEY, "categories"] });
      onChanged();
    },
    onError: (err) => showToast(err instanceof ApiError ? err.message : "Could not update.", "error"),
  });

  return (
    <Modal title="Subjects & topics" onClose={onClose} wide>
      <div className="stack-sm">
        <p className="muted">
          Organize questions by subject (e.g. Programming, Mechanical engineering) and topics under
          them (e.g. Python, Robotics). Filtering by a subject includes its topics.
        </p>
        <form
          className="field-row"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) createMutation.mutate();
          }}
        >
          <label className="field">
            <span>Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} required disabled={createMutation.isPending} />
          </label>
          <label className="field">
            <span>
              Under <span className="optional-mark">(Optional)</span>
            </span>
            <select value={parentId} onChange={(e) => setParentId(e.target.value)} disabled={createMutation.isPending}>
              <option value="">— Top-level subject —</option>
              {categoriesQuery.data
                ?.filter((c) => c.is_active)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.path}
                  </option>
                ))}
            </select>
          </label>
          <div className="field" style={{ alignSelf: "end" }}>
            <button type="submit" className="btn btn-primary" disabled={createMutation.isPending || !name.trim()}>
              Add
            </button>
          </div>
        </form>
        {error && <Alert>{error}</Alert>}
        {categoriesQuery.isSuccess && categoriesQuery.data.length === 0 && <p className="muted">No subjects yet.</p>}
        {categoriesQuery.isSuccess && categoriesQuery.data.length > 0 && (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Subject / topic</th>
                  <th>Questions</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {categoriesQuery.data.map((c) => (
                  <tr key={c.id}>
                    <td>{c.path}</td>
                    <td>{c.question_count}</td>
                    <td>{c.is_active ? "Active" : "Inactive"}</td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => toggleMutation.mutate({ id: c.id, active: !c.is_active })}
                        disabled={toggleMutation.isPending}
                      >
                        {c.is_active ? "Deactivate" : "Reactivate"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Modal>
  );
}
