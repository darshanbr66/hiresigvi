import { useId } from "react";
import {
  QUESTION_TYPE_LABELS,
  isChoiceType,
  type QuestionCreate,
  type QuestionType,
} from "../../../types/assessment";

const TRUE_FALSE_OPTIONS = [
  { label: "True", is_correct: true },
  { label: "False", is_correct: false },
];

/** Re-shapes a question for a new type, keeping whatever still applies. */
export function retypeQuestion(question: QuestionCreate, type: QuestionType): QuestionCreate {
  if (type === "TRUE_FALSE") {
    return { ...question, type, options: TRUE_FALSE_OPTIONS.map((o) => ({ ...o })), accepted_answers: [] };
  }
  if (!isChoiceType(type)) {
    return {
      ...question,
      type,
      options: [],
      negative_points: type === "SHORT_ANSWER" ? question.negative_points ?? 0 : 0,
      accepted_answers: type === "SHORT_ANSWER" ? question.accepted_answers ?? [] : [],
    };
  }
  const options =
    question.options.length >= 2 && question.type !== "TRUE_FALSE"
      ? question.options
      : [
          { label: "", is_correct: true },
          { label: "", is_correct: false },
        ];
  if (type === "MCQ_SINGLE") {
    let picked = false;
    const single = options.map((o) => {
      const isCorrect = !picked && o.is_correct;
      if (isCorrect) picked = true;
      return { ...o, is_correct: isCorrect };
    });
    if (!picked && single.length > 0) single[0] = { ...single[0], is_correct: true };
    return { ...question, type, options: single, accepted_answers: [] };
  }
  return { ...question, type, options, accepted_answers: [] };
}

/** True when the question has nothing typed yet (used to replace an empty
 * starter question instead of appending after it). */
export function isBlankQuestion(question: QuestionCreate): boolean {
  return (
    !question.bank_version_id &&
    question.prompt.trim() === "" &&
    question.options.every((o) => o.label.trim() === "" || question.type === "TRUE_FALSE")
  );
}

/**
 * Editor for one question of any supported type — the answer key
 * (correct options, accepted short answers, grading notes) is edited here
 * and is only ever sent to staff-only endpoints. Fully controlled.
 */
export function QuestionEditor({
  value,
  onChange,
  disabled = false,
  heading,
}: {
  value: QuestionCreate;
  onChange: (value: QuestionCreate) => void;
  disabled?: boolean;
  heading: string;
}) {
  const id = useId();
  const choice = isChoiceType(value.type);
  const autoGraded =
    choice || (value.type === "SHORT_ANSWER" && (value.accepted_answers?.length ?? 0) > 0);

  function patch(fields: Partial<QuestionCreate>) {
    onChange({ ...value, ...fields });
  }

  function updateOption(index: number, fields: { label?: string; is_correct?: boolean }) {
    const options = value.options.map((o, i) => (i === index ? { ...o, ...fields } : o));
    if (fields.is_correct && value.type !== "MCQ_MULTI") {
      patch({ options: options.map((o, i) => ({ ...o, is_correct: i === index })) });
      return;
    }
    patch({ options });
  }

  return (
    <div className="stack-sm">
      <label className="field">
        <span>{heading}</span>
        <textarea
          required
          rows={2}
          value={value.prompt}
          onChange={(e) => patch({ prompt: e.target.value })}
          disabled={disabled}
        />
      </label>

      <div className="field-row">
        <label className="field">
          <span>Type</span>
          <select
            value={value.type}
            onChange={(e) => onChange(retypeQuestion(value, e.target.value as QuestionType))}
            disabled={disabled}
          >
            {(Object.keys(QUESTION_TYPE_LABELS) as QuestionType[]).map((type) => (
              <option key={type} value={type}>
                {QUESTION_TYPE_LABELS[type]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Marks</span>
          <input
            required
            type="number"
            min={1}
            max={1000}
            value={value.points}
            onChange={(e) => patch({ points: Number(e.target.value) })}
            disabled={disabled}
          />
        </label>
        {autoGraded && (
          <label className="field">
            <span>
              Negative marks <span className="optional-mark">(optional)</span>
            </span>
            <input
              type="number"
              min={0}
              max={value.points}
              value={value.negative_points ?? 0}
              onChange={(e) => patch({ negative_points: Number(e.target.value) })}
              disabled={disabled}
            />
          </label>
        )}
      </div>

      {choice && (
        <fieldset className="field">
          <legend>
            Options — mark the correct {value.type === "MCQ_MULTI" ? "answers" : "answer"}
          </legend>
          <div className="stack-sm">
            {value.options.map((option, index) => (
              <div key={index} style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <input
                  type={value.type === "MCQ_MULTI" ? "checkbox" : "radio"}
                  name={`${id}-correct`}
                  checked={option.is_correct}
                  onChange={(e) => updateOption(index, { is_correct: e.target.checked })}
                  aria-label={`Option ${index + 1} is correct`}
                  disabled={disabled}
                  style={{ width: "auto" }}
                />
                <input
                  required
                  aria-label={`Option ${index + 1}`}
                  placeholder={`Option ${index + 1}`}
                  value={option.label}
                  onChange={(e) => updateOption(index, { label: e.target.value })}
                  style={{ flex: 1 }}
                  disabled={disabled || value.type === "TRUE_FALSE"}
                />
                {value.type !== "TRUE_FALSE" && value.options.length > 2 && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => patch({ options: value.options.filter((_, i) => i !== index) })}
                    disabled={disabled}
                  >
                    Remove
                  </button>
                )}
              </div>
            ))}
            {value.type !== "TRUE_FALSE" && value.options.length < 10 && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                style={{ alignSelf: "flex-start" }}
                onClick={() => patch({ options: [...value.options, { label: "", is_correct: false }] })}
                disabled={disabled}
              >
                + Add option
              </button>
            )}
          </div>
        </fieldset>
      )}

      {value.type === "SHORT_ANSWER" && (
        <>
          <label className="field">
            <span>
              Accepted answers <span className="optional-mark">(optional — one per line)</span>
            </span>
            <textarea
              rows={2}
              value={(value.accepted_answers ?? []).join("\n")}
              onChange={(e) =>
                patch({
                  accepted_answers: e.target.value.split("\n").map((a) => a.trimStart()),
                })
              }
              onBlur={(e) =>
                patch({
                  accepted_answers: e.target.value
                    .split("\n")
                    .map((a) => a.trim())
                    .filter(Boolean),
                })
              }
              disabled={disabled}
            />
            <span className="field-hint">
              With accepted answers the question is graded automatically (spacing is ignored);
              without them a grader marks it by hand.
            </span>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <input
              type="checkbox"
              checked={value.case_sensitive ?? false}
              onChange={(e) => patch({ case_sensitive: e.target.checked })}
              disabled={disabled}
              style={{ width: "auto" }}
            />
            Match case exactly
          </label>
        </>
      )}

      {value.type === "CODING" && (
        <p className="field-hint">
          Candidates type their code as text and a grader reviews it — code is never executed by
          the platform.
        </p>
      )}

      {!choice && (
        <label className="field">
          <span>
            Grading notes <span className="optional-mark">(optional, staff only)</span>
          </span>
          <textarea
            rows={2}
            value={value.grading_notes ?? ""}
            onChange={(e) => patch({ grading_notes: e.target.value || null })}
            disabled={disabled}
            placeholder="What a full-marks answer covers…"
          />
        </label>
      )}
      <label className="field">
        <span>
          Explanation <span className="optional-mark">(optional, staff only)</span>
        </span>
        <textarea
          rows={2}
          value={value.explanation ?? ""}
          onChange={(e) => patch({ explanation: e.target.value || null })}
          disabled={disabled}
        />
      </label>
    </div>
  );
}
