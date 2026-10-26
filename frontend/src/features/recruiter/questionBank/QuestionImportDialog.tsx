import { useMutation } from "@tanstack/react-query";
import { useRef, useState, type ChangeEvent } from "react";
import { ApiError } from "../../../lib/apiClient";
import { triggerBlobDownload } from "../../../lib/downloadBlob";
import { Alert } from "../../../shared/components/Alert";
import { Modal } from "../../../shared/components/Modal";
import { Spinner } from "../../../shared/components/Spinner";
import { CHOICE_TYPES, QUESTION_TYPE_LABELS } from "../../../types/assessment";
import {
  DIFFICULTY_LABELS,
  type BankImportCandidate,
  type BankImportPreview,
  type QuestionBankStatus,
} from "../../../types/questionBank";
import { downloadImportTemplate, importQuestions, previewImport } from "./api";

const ACCEPT = ".pdf,.docx,.xlsx,.csv";

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** Importable = parsed cleanly; duplicates start unticked so they're a choice. */
function initialSelection(preview: BankImportPreview): boolean[] {
  return preview.questions.map((q) => q.issue === null && q.content !== null && !q.duplicate_of_item_id && !q.duplicate_in_file);
}

function AnswerSummary({ candidate }: { candidate: BankImportCandidate }) {
  const content = candidate.content;
  if (!content) return null;
  if (CHOICE_TYPES.includes(content.type)) {
    return (
      <span className="cell-sub">
        {content.options.map((o, i) => (
          <span key={i}>
            {i > 0 && " · "}
            {o.is_correct ? <strong>{o.label} (correct)</strong> : o.label}
          </span>
        ))}
      </span>
    );
  }
  if (content.accepted_answers.length > 0) {
    return <span className="cell-sub">Accepted: {content.accepted_answers.join(" · ")}</span>;
  }
  return <span className="cell-sub">Graded manually</span>;
}

/**
 * Bulk import into the question bank: choose a file → review what was read
 * (nothing is saved yet) → import the ticked questions in one go. The server
 * parses and validates; this only shows the result and sends back the
 * questions HR kept.
 */
export function QuestionImportDialog({
  accessToken,
  onClose,
  onImported,
}: {
  accessToken: string;
  onClose: () => void;
  onImported: (imported: number, categoriesCreated: number, status: QuestionBankStatus) => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<BankImportPreview | null>(null);
  const [selected, setSelected] = useState<boolean[]>([]);
  const [publish, setPublish] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const previewMutation = useMutation({
    mutationFn: (file: File) => previewImport(file, accessToken),
    onSuccess: (result) => {
      setPreview(result);
      setSelected(initialSelection(result));
      setError(null);
    },
    onError: (err) => setError(errorText(err, "We couldn't read that file. Please try again.")),
  });

  const importMutation = useMutation({
    mutationFn: () => {
      const items = (preview?.questions ?? [])
        .filter((q, i) => selected[i] && q.content !== null)
        .map((q) => ({
          content: q.content!,
          difficulty: q.difficulty,
          subject: q.subject,
          topic: q.topic,
          tags: q.tags,
        }));
      return importQuestions({ items, status: publish ? "PUBLISHED" : "DRAFT" }, accessToken);
    },
    onSuccess: (result) => onImported(result.imported, result.categories_created, result.status),
    onError: (err) => setError(errorText(err, "The questions weren't imported. Please try again.")),
  });

  const templateMutation = useMutation({
    mutationFn: () => downloadImportTemplate(accessToken),
    onSuccess: ({ blob, filename }) => triggerBlobDownload(blob, filename ?? "question-bank-import-template.csv"),
    onError: (err) => setError(errorText(err, "We couldn't download the template. Please try again.")),
  });

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError(null);
    previewMutation.mutate(file);
  }

  const busy = previewMutation.isPending || importMutation.isPending;
  const questions = preview?.questions ?? [];
  const ready = questions.filter((q) => q.issue === null && q.content !== null).length;
  const withIssues = questions.length - ready;
  const duplicates = questions.filter((q) => q.duplicate_of_item_id || q.duplicate_in_file).length;
  const chosen = selected.filter(Boolean).length;
  const newCategories = new Set(
    questions.filter((q, i) => selected[i] && q.creates_category && q.category_path).map((q) => q.category_path),
  );

  return (
    <Modal title="Import questions" onClose={() => !busy && onClose()} wide={preview ? "extra" : true}>
      <div className="stack-sm">
        <input ref={fileInput} type="file" accept={ACCEPT} onChange={chooseFile} hidden aria-hidden="true" tabIndex={-1} />

        {!preview && (
          <>
            <p>Upload a PDF, DOCX, XLSX or CSV file (up to 5 MB). Nothing is saved until you review it.</p>
            <ul className="import-rules">
              <li>
                <strong>CSV / XLSX:</strong> a <code>question</code> column, <code>option_1</code>/<code>option_2</code>/…
                columns, and a <code>correct</code> column (by number or letter). Optional: <code>type</code>,{" "}
                <code>difficulty</code>, <code>subject</code>, <code>topic</code>, <code>marks</code>, <code>tags</code>,{" "}
                <code>explanation</code>.
              </li>
              <li>
                <strong>PDF / DOCX:</strong> numbered questions with lettered options — mark the right one with a
                trailing <code>*</code> or add an <code>Answer: B</code> line.
              </li>
            </ul>
            <div className="btn-group">
              <button type="button" className="btn btn-primary" disabled={busy} onClick={() => fileInput.current?.click()}>
                {previewMutation.isPending ? <Spinner label="Reading file…" /> : "Choose file to import"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={templateMutation.isPending}
                onClick={() => templateMutation.mutate()}
              >
                Download CSV template
              </button>
            </div>
          </>
        )}

        {error && <Alert>{error}</Alert>}

        {preview && (
          <>
            <p aria-live="polite">
              <strong>{preview.filename}</strong>: {ready} question{ready === 1 ? "" : "s"} ready to import
              {withIssues > 0 && `, ${withIssues} need fixing in the file`}
              {preview.warnings.length > 0 && `, ${preview.warnings.length} couldn't be read`}
              {duplicates > 0 && `, ${duplicates} possible duplicate${duplicates === 1 ? "" : "s"} (unticked)`}.
              {" "}Nothing has been saved yet.
            </p>

            {preview.warnings.length > 0 && (
              <details className="import-warnings" open={preview.questions.length === 0}>
                <summary>Not imported ({preview.warnings.length})</summary>
                <ul>
                  {preview.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </details>
            )}

            {questions.length > 0 && (
              <div className="table-scroll import-preview">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">
                        <span className="sr-only">Import</span>
                      </th>
                      <th scope="col">From</th>
                      <th scope="col">Question</th>
                      <th scope="col">Details</th>
                      <th scope="col">Subject / topic</th>
                    </tr>
                  </thead>
                  <tbody>
                    {questions.map((q, i) => {
                      const importable = q.issue === null && q.content !== null;
                      return (
                        <tr key={`${q.source}-${i}`} className={importable ? undefined : "row-muted"}>
                          <td>
                            <input
                              type="checkbox"
                              aria-label={`Import ${q.source}`}
                              checked={selected[i] ?? false}
                              disabled={!importable || busy}
                              onChange={() => setSelected((s) => s.map((v, j) => (j === i ? !v : v)))}
                            />
                          </td>
                          <td className="nowrap">{q.source}</td>
                          <td className="import-question">
                            {q.prompt}
                            <AnswerSummary candidate={q} />
                            {q.issue && <span className="cell-sub text-danger">Can't import: {q.issue}</span>}
                            {q.duplicate_of_item_id && (
                              <span className="cell-sub text-warn">Already in the bank with the same text.</span>
                            )}
                            {q.duplicate_in_file && (
                              <span className="cell-sub text-warn">Repeats an earlier question in this file.</span>
                            )}
                          </td>
                          <td className="nowrap">
                            {q.content ? QUESTION_TYPE_LABELS[q.content.type] : "—"}
                            <span className="cell-sub">
                              {q.content ? `${q.content.points} mark${q.content.points === 1 ? "" : "s"} · ` : ""}
                              {DIFFICULTY_LABELS[q.difficulty]}
                            </span>
                          </td>
                          <td>
                            {q.category_path ?? "—"}
                            {q.creates_category && <span className="cell-sub">New — will be created</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {ready > 0 && (
              <fieldset className="field">
                <legend>Save imported questions as</legend>
                <label className="choice">
                  <input type="radio" name="import-status" checked={!publish} onChange={() => setPublish(false)} disabled={busy} />
                  Draft — review them in the bank before use
                </label>
                <label className="choice">
                  <input type="radio" name="import-status" checked={publish} onChange={() => setPublish(true)} disabled={busy} />
                  Published — available to tests straight away
                </label>
              </fieldset>
            )}

            {newCategories.size > 0 && (
              <p className="field-hint">New subjects/topics will be added for: {[...newCategories].join(", ")}.</p>
            )}

            <div className="btn-group">
              <button
                type="button"
                className="btn btn-primary"
                disabled={chosen === 0 || busy}
                onClick={() => importMutation.mutate()}
              >
                {importMutation.isPending ? (
                  <Spinner label="Importing…" />
                ) : (
                  `Import ${chosen} question${chosen === 1 ? "" : "s"}`
                )}
              </button>
              <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => fileInput.current?.click()}>
                {previewMutation.isPending ? <Spinner label="Reading file…" /> : "Choose a different file"}
              </button>
              <button type="button" className="btn btn-ghost" disabled={busy} onClick={onClose}>
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
