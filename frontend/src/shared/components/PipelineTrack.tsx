import { useEffect, useRef, type CSSProperties } from "react";
import { PIPELINE_STAGES as STAGES, pipelineStageIndex } from "../lib/pipelineStages";
import { humanizeStatus, pipelineColor } from "../lib/statusTone";
import { Icon } from "./Icon";

/**
 * The hiring pipeline as a track: the stages an application moves through, in
 * order, with where this one is right now. It only *displays* the real
 * `status` — moving an application is still the existing "Move to…" action —
 * so nothing here implies a capability the workflow doesn't have. When the
 * status changes the classes change with it, and the CSS transitions animate
 * the connector filling and the new stage arriving.
 *
 * Several statuses can belong to one stage (the assessment has three), so the
 * current stage also shows the exact status beneath its name.
 */
export function PipelineTrack({ status, compact = false }: { status: string; compact?: boolean }) {
  const current = pipelineStageIndex(status);
  const rejected = status === "REJECTED";
  const scroller = useRef<HTMLDivElement>(null);
  // The current stage wears its pipeline colour (the same as every chart).
  const stageStyle = { "--stage-color": pipelineColor(status) } as CSSProperties;

  // On a narrow screen the track scrolls sideways; bring the current stage to
  // the middle so "where is this application" is answered without scrolling.
  useEffect(() => {
    const box = scroller.current;
    const node = box?.querySelector<HTMLElement>('[aria-current="step"]');
    if (!box || !node || box.scrollWidth <= box.clientWidth) return;
    box.scrollLeft = node.offsetLeft - (box.clientWidth - node.offsetWidth) / 2;
  }, [status, compact]);

  if (compact) {
    const progress = current < 0 ? 0 : ((current + 1) / STAGES.length) * 100;
    return (
      <div
        className={`pipeline-mini${rejected ? " is-rejected" : ""}${status === "HIRED" ? " is-hired" : ""}`}
        style={stageStyle}
        role="img"
        aria-label={
          current < 0
            ? `Pipeline: ${humanizeStatus(status)}`
            : `Pipeline: stage ${current + 1} of ${STAGES.length}, ${humanizeStatus(status)}`
        }
      >
        <span className="pipeline-mini-track" aria-hidden="true">
          <span className="pipeline-mini-fill" style={{ width: `${progress}%` }} />
        </span>
        <span className="pipeline-mini-label" aria-hidden="true">
          {humanizeStatus(status)}
        </span>
      </div>
    );
  }

  return (
    <div ref={scroller} className={`pipeline${rejected ? " is-rejected" : ""}`} style={stageStyle}>
      <ol className="pipeline-track" aria-label="Application pipeline">
        {STAGES.map((stage, index) => {
          const state = current < 0 ? "todo" : index < current ? "done" : index === current ? "current" : "todo";
          const detail = state === "current" && humanizeStatus(status) !== stage.label ? humanizeStatus(status) : null;
          return (
            <li
              key={stage.key}
              className={`pipeline-stage is-${state}${stage.key === "hired" ? " is-final" : ""}`}
              aria-current={state === "current" ? "step" : undefined}
            >
              <span className="pipeline-node" aria-hidden="true">
                {state === "done" ? <Icon name="check" size={13} /> : index + 1}
              </span>
              <span className="pipeline-label">{stage.label}</span>
              {detail && <span className="pipeline-detail">{detail}</span>}
              {state === "done" && <span className="sr-only">(completed)</span>}
            </li>
          );
        })}
      </ol>
      {rejected && (
        <p className="pipeline-terminal" role="status">
          Rejected — this application has left the pipeline.
        </p>
      )}
    </div>
  );
}
