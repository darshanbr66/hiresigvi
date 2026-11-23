import { Icon } from "../../../shared/components/Icon";

export interface StepDefinition<K extends string> {
  key: K;
  label: string;
}

/**
 * Where the candidate is in the application. Deliberately not clickable —
 * moving between steps is done with the Back / Continue buttons and the
 * review screen's Edit links, so a mandatory step can never be skipped.
 * On narrow screens the labels collapse to the numbered nodes plus a
 * "Step 3 of 6 · Professional background" caption.
 */
export function ApplyStepper<K extends string>({
  steps,
  current,
}: {
  steps: StepDefinition<K>[];
  current: K;
}) {
  const currentIndex = Math.max(
    0,
    steps.findIndex((step) => step.key === current),
  );
  return (
    <nav className="apply-progress" aria-label="Application progress">
      <ol className="apply-stepper" style={{ ["--apply-steps" as string]: steps.length }}>
        {steps.map((step, index) => {
          const state = index < currentIndex ? "done" : index === currentIndex ? "current" : "todo";
          const status =
            state === "done" ? "completed" : state === "current" ? "current step" : "not started";
          return (
            <li
              key={step.key}
              className={`apply-step apply-step-${state}`}
              aria-current={state === "current" ? "step" : undefined}
            >
              <span className="apply-step-node" aria-hidden="true">
                {state === "done" ? <Icon name="check" size={13} /> : index + 1}
              </span>
              <span className="apply-step-label" aria-hidden="true">
                {step.label}
              </span>
              <span className="sr-only">
                {`Step ${index + 1} of ${steps.length}: ${step.label}, ${status}`}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="apply-stepper-caption" aria-hidden="true">
        Step {currentIndex + 1} of {steps.length} · <strong>{steps[currentIndex]?.label}</strong>
      </p>
    </nav>
  );
}
