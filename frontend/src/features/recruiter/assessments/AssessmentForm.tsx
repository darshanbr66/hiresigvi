import { useMutation } from "@tanstack/react-query";
import { useRef, useState, type ChangeEvent } from "react";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import {
  QUESTION_TYPE_LABELS,
  isChoiceType,
  type AssessmentCreateRequest,
  type ParsedQuestionsResponse,
  type QuestionCreate,
} from "../../../types/assessment";
import { parseImportQuestions } from "./api";
import { BankQuestionPicker } from "./BankQuestionPicker";
import { isBlankQuestion, QuestionEditor } from "./QuestionEditor";

const TEMPLATE_CSV =
  "question,option_1,option_2,option_3,option_4,correct,type,points\n" +
  '"What is 2 + 2?",3,4,5,6,2,single,1\n' +
  '"Which of these are prime numbers?",2,4,6,7,"1,4",multiple,2\n';

export function emptyQuestion(): QuestionCreate {
  return {
    prompt: "",
    type: "MCQ_SINGLE",
    points: 1,
    negative_points: 0,
    options: [
      { label: "", is_correct: true },
      { label: "", is_correct: false },
    ],
    accepted_answers: [],
  };
}

export function emptyAssessmentFormValue(): AssessmentCreateRequest {
  return { title: "", instructions: "", duration_minutes: 30, pass_score: 60, questions: [emptyQuestion()] };
}

function downloadTemplate() {
  const blob = new Blob([TEMPLATE_CSV], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "assessment-questions-template.csv";
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * The full assessment-authoring surface — title/instructions/duration/pass
 * score, file import + preview, and the per-question editor. Fully
 * controlled (`value`/`onChange`, no <form> or submit button of its own) so
 * it can be embedded both by AssessmentsPage's "Create assessment" modal
 * and by the retest modal's "New assessment" option (QA § 6: reuse the
 * existing creation/import workflow rather than duplicating it).
 */
export function AssessmentForm({
  value,
  onChange,
  disabled = false,
  questionsLocked = false,
  questionsLockedReason,
  accessToken,
}: {
  value: AssessmentCreateRequest;
  onChange: (value: AssessmentCreateRequest) => void;
  disabled?: boolean;
  /** Freezes the import/question-editing controls while leaving
   * title/instructions/duration/pass score editable — used once an
   * assessment already has invitations (see AssessmentDetailPage). */
  questionsLocked?: boolean;
  questionsLockedReason?: string;
  accessToken: string;
}) {
  const { showToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<ParsedQuestionsResponse | null>(null);
  const [selected, setSelected] = useState<boolean[]>([]);
  const [importError, setImportError] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  const { title, instructions, duration_minutes: durationMinutes, pass_score: passScore, questions } = value;
  const questionsDisabled = disabled || questionsLocked;

  function patch(fields: Partial<AssessmentCreateRequest>) {
    onChange({ ...value, ...fields });
  }

  const importMutation = useMutation({
    mutationFn: (file: File) => parseImportQuestions(file, accessToken),
    onSuccess: (result) => {
      setImportError(null);
      setPreview(result);
      setSelected(result.questions.map(() => true));
      if (result.questions.length === 0) {
        showToast("No usable questions were found in that file.", "error");
      } else {
        showToast(`Found ${result.questions.length} question(s) — review and add them below.`, "success");
      }
    },
    onError: (err) => {
      setImportError(err instanceof ApiError ? err.message : "Could not read that file.");
      setPreview(null);
    },
  });

  function handleFileChosen(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setImportError(null);
    importMutation.mutate(file);
  }

  function toggleSelected(index: number) {
    setSelected((current) => current.map((v, i) => (i === index ? !v : v)));
  }

  function addSelectedQuestions() {
    if (!preview) return;
    const chosen = preview.questions.filter((_, i) => selected[i]);
    if (chosen.length === 0) return;
    const base = questions.length === 1 && isBlankQuestion(questions[0]) ? [] : questions;
    patch({ questions: [...base, ...chosen] });
    setPreview(null);
    showToast(`Added ${chosen.length} question(s) to the assessment.`, "success");
  }

  function replaceQuestion(index: number, question: QuestionCreate) {
    patch({ questions: questions.map((q, i) => (i === index ? question : q)) });
  }

  function addQuestion() {
    patch({ questions: [...questions, emptyQuestion()] });
  }

  function addBankQuestions(chosen: QuestionCreate[]) {
    const base = questions.length === 1 && isBlankQuestion(questions[0]) ? [] : questions;
    patch({ questions: [...base, ...chosen] });
    setPickerOpen(false);
    showToast(`Added ${chosen.length} question(s) from the question bank.`, "success");
  }

  function removeQuestion(index: number) {
    patch({ questions: questions.filter((_, i) => i !== index) });
  }

  function moveQuestion(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= questions.length) return;
    const next = [...questions];
    [next[index], next[target]] = [next[target], next[index]];
    patch({ questions: next });
  }

  const totalMarks = questions.reduce((sum, q) => sum + (Number(q.points) || 0), 0);
  const manualCount = questions.filter(
    (q) => q.type === "DESCRIPTIVE" || q.type === "CODING" ||
      (q.type === "SHORT_ANSWER" && (q.accepted_answers?.length ?? 0) === 0),
  ).length;
  const bankVersionIds = questions.flatMap((q) => (q.bank_version_id ? [q.bank_version_id] : []));

  return (
    <div className="stack-lg" style={{ gap: "1.25rem" }}>
      <label className="field">
        <span>Title</span>
        <input
          required
          value={title}
          onChange={(e) => patch({ title: e.target.value })}
          disabled={disabled}
        />
      </label>
      <label className="field">
        <span>Instructions for the candidate</span>
        <textarea
          required
          rows={2}
          value={instructions}
          onChange={(e) => patch({ instructions: e.target.value })}
          disabled={disabled}
        />
      </label>
      <div className="field-row">
        <label className="field">
          <span>Duration (minutes)</span>
          <input
            required
            type="number"
            min={1}
            max={480}
            value={durationMinutes}
            onChange={(e) => patch({ duration_minutes: Number(e.target.value) })}
            disabled={disabled}
          />
        </label>
        <label className="field">
          <span>Pass score (%)</span>
          <input
            required
            type="number"
            min={0}
            max={100}
            value={passScore}
            onChange={(e) => patch({ pass_score: Number(e.target.value) })}
            disabled={disabled}
          />
        </label>
      </div>

      {questionsLocked && (
        <Alert>
          {questionsLockedReason ??
            "Questions can't be changed once this assessment has been sent to a candidate. Create a new assessment instead."}
        </Alert>
      )}

      {!questionsLocked && (
        <fieldset className="field">
          <legend>Import questions</legend>
          <p className="field-hint" style={{ marginBottom: "0.6rem" }}>
            Upload a PDF, DOCX, XLSX, or CSV file. Tabular files (CSV/XLSX) need a{" "}
            <code>question</code> column, <code>option_1</code>/<code>option_2</code>/… columns,
            and a <code>correct</code> column (by number or letter). PDF/DOCX files need numbered
            questions with lettered options — mark the right one with a trailing <code>*</code> or
            add an <code>Answer: B</code> line.
          </p>
          <div className="btn-group">
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx,.xlsx,.csv"
              onChange={handleFileChosen}
              style={{ display: "none" }}
            />
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={disabled || importMutation.isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              {importMutation.isPending ? <Spinner label="Reading file…" /> : "Choose file to import"}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={downloadTemplate} disabled={disabled}>
              Download CSV template
            </button>
          </div>
          {importError && (
            <div style={{ marginTop: "0.6rem" }}>
              <Alert>{importError}</Alert>
            </div>
          )}
        </fieldset>
      )}

      {preview && (
        <section className="card" style={{ background: "var(--color-bg)" }}>
          <h3>Import preview — nothing is saved yet</h3>
          {preview.warnings.length > 0 && (
            <div className="stack-sm" style={{ marginBottom: "0.75rem" }}>
              {preview.warnings.map((w, i) => (
                <p key={i} className="field-hint" style={{ color: "var(--color-warn-text)" }}>
                  {w}
                </p>
              ))}
            </div>
          )}
          {preview.questions.length === 0 ? (
            <p className="muted">No usable questions were found — see the warnings above.</p>
          ) : (
            <>
              <div className="stack-lg" style={{ gap: "0.6rem" }}>
                {preview.questions.map((q, i) => (
                  <label
                    key={i}
                    style={{
                      display: "flex",
                      flexDirection: "row",
                      alignItems: "flex-start",
                      gap: "0.6rem",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selected[i] ?? true}
                      onChange={() => toggleSelected(i)}
                      style={{ marginTop: "0.2rem", width: "auto", flexShrink: 0 }}
                    />
                    <span>
                      <strong>{q.prompt}</strong> <span className="muted">({q.type}, {q.points} pt)</span>
                      <br />
                      <span className="muted">
                        {q.options
                          .map((o) => (o.is_correct ? `${o.label} ✓` : o.label))
                          .join(" · ")}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
              <div className="btn-group" style={{ marginTop: "1rem" }}>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={addSelectedQuestions}
                  disabled={selected.every((v) => !v)}
                >
                  Add {selected.filter(Boolean).length} selected question(s)
                </button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPreview(null)}>
                  Discard
                </button>
              </div>
            </>
          )}
        </section>
      )}

      <div>
        <h3>Questions</h3>
        <p className="muted" style={{ marginBottom: "0.75rem" }}>
          {questions.length} question(s) · {totalMarks} mark(s) in total
          {manualCount > 0 ? ` · ${manualCount} graded manually` : ""}
          {bankVersionIds.length > 0 ? ` · ${bankVersionIds.length} from the question bank` : ""}
        </p>
        <div className="stack-lg" style={{ gap: "1rem" }}>
          {questions.map((question, qIndex) => (
            <div key={qIndex} className="card" style={{ background: "var(--color-bg)" }}>
              {question.bank_version_id ? (
                <div className="stack-sm">
                  <p>
                    <strong>
                      Question {qIndex + 1}. {question.prompt}
                    </strong>{" "}
                    <span className="badge badge-active">From question bank</span>
                  </p>
                  <p className="muted">
                    {QUESTION_TYPE_LABELS[question.type]}
                    {isChoiceType(question.type)
                      ? ` · ${question.options.map((o) => (o.is_correct ? `${o.label} ✓` : o.label)).join(" · ")}`
                      : ""}
                  </p>
                  <label className="field" style={{ maxWidth: "10rem" }}>
                    <span>Marks in this test</span>
                    <input
                      required
                      type="number"
                      min={1}
                      max={1000}
                      value={question.points}
                      onChange={(e) => replaceQuestion(qIndex, { ...question, points: Number(e.target.value) })}
                      disabled={questionsDisabled}
                    />
                  </label>
                  <span className="field-hint">
                    The exact bank version is copied into this test — later edits in the question
                    bank never change it.
                  </span>
                </div>
              ) : (
                <QuestionEditor
                  heading={`Question ${qIndex + 1}`}
                  value={question}
                  onChange={(next) => replaceQuestion(qIndex, next)}
                  disabled={questionsDisabled}
                />
              )}

              <div className="btn-group" style={{ marginTop: "0.75rem" }}>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={questionsDisabled || qIndex === 0}
                  onClick={() => moveQuestion(qIndex, -1)}
                >
                  Move up
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={questionsDisabled || qIndex === questions.length - 1}
                  onClick={() => moveQuestion(qIndex, 1)}
                >
                  Move down
                </button>
                {questions.length > 1 && (
                  <button
                    type="button"
                    className="btn btn-danger btn-sm"
                    onClick={() => removeQuestion(qIndex)}
                    disabled={questionsDisabled}
                  >
                    Remove question
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>

        {!questionsLocked && (
          <div className="btn-group" style={{ marginTop: "1rem" }}>
            <button type="button" className="btn btn-ghost" onClick={addQuestion} disabled={disabled}>
              + Add question
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setPickerOpen(true)}
              disabled={disabled}
            >
              + Add from question bank
            </button>
          </div>
        )}
      </div>

      {pickerOpen && (
        <BankQuestionPicker
          accessToken={accessToken}
          alreadySelected={bankVersionIds}
          onAdd={addBankQuestions}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
