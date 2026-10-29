import { useEffect, useRef } from "react";
import { Icon } from "../../shared/components/Icon";
import { pipelineColor } from "../../shared/lib/statusTone";

/**
 * The home-page hero visual: a layered preview of HireZ's own interface —
 * an application panel (stage badge, the real pipeline stages, the next
 * step), with an assessment card and an interview-feedback card resting on
 * it. Built from the product's own pieces (status badges, stage colours,
 * the Icon set) in plain HTML/CSS, so it reads as the actual software.
 *
 * Nothing in it is data: names are placeholder lines, and there are no
 * scores, counts or rates. It is one image to assistive tech (role="img"
 * with a description); the parts inside are hidden from it.
 *
 * Motion (styles/home.css): the parts arrive just after the headline, the
 * two resting cards drift a few pixels on their own slow cycles, and the
 * layers shift slightly with the pointer (parallax). Parallax only runs for
 * a fine pointer on wide screens and never with reduced motion; it writes
 * two CSS variables per animation frame — nothing else re-renders.
 */
function usePointerParallax(ref: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof window.matchMedia !== "function") return;
    const allowed = window.matchMedia("(pointer: fine) and (min-width: 1000px)");
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!allowed.matches || reduced.matches) return;

    const host = (node.closest(".home-hero") as HTMLElement | null) ?? node;
    let frame = 0;
    const set = (x: number, y: number) => {
      host.style.setProperty("--px", x.toFixed(3));
      host.style.setProperty("--py", y.toFixed(3));
    };
    const onMove = (event: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const box = node.getBoundingClientRect();
        const clamp = (v: number) => Math.max(-1, Math.min(1, v));
        set(
          clamp((event.clientX - (box.left + box.width / 2)) / box.width),
          clamp((event.clientY - (box.top + box.height / 2)) / box.height),
        );
      });
    };
    const onLeave = () => {
      cancelAnimationFrame(frame);
      set(0, 0);
    };
    host.addEventListener("pointermove", onMove);
    host.addEventListener("pointerleave", onLeave);
    return () => {
      cancelAnimationFrame(frame);
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("pointerleave", onLeave);
      host.style.removeProperty("--px");
      host.style.removeProperty("--py");
    };
  }, [ref]);
}
const STAGES = [
  { status: "APPLIED", label: "Applied" },
  { status: "SCREENING", label: "Screening" },
  { status: "ASSESSMENT_COMPLETED", label: "Assessment" },
  { status: "INTERVIEW", label: "Interview" },
  { status: "SELECTED", label: "Selected" },
  { status: "HIRED", label: "Hired" },
];
const CURRENT = 3;

export function HeroProductPreview() {
  const ref = useRef<HTMLDivElement>(null);
  usePointerParallax(ref);
  return (
    <div
      ref={ref}
      className="hp"
      role="img"
      aria-label="Preview of a HireZ application: the candidate is at the interview stage, with a passed assessment and recorded interview feedback."
    >
      <div className="hp-window" aria-hidden="true">
        <div className="hp-window-head">
          <span className="hp-avatar">
            <Icon name="user" size={18} />
          </span>
          <span className="hp-identity">
            <span className="hp-line hp-line-strong" style={{ width: "7.5rem" }} />
            <span className="hp-line" style={{ width: "5rem" }} />
          </span>
          <span className="badge badge-interview hp-badge">Interview</span>
        </div>

        <ol className="hp-track">
          {STAGES.map((stage, index) => {
            const state = index < CURRENT ? "done" : index === CURRENT ? "current" : "ahead";
            return (
              <li key={stage.status} className={`hp-stage is-${state}`}>
                <span
                  className="hp-node"
                  style={state === "current" ? { background: pipelineColor(stage.status) } : undefined}
                >
                  {state === "done" && <Icon name="check" size={10} />}
                </span>
                <span className="hp-stage-label">{stage.label}</span>
              </li>
            );
          })}
        </ol>

        <div className="hp-next">
          <span className="hp-next-icon">
            <Icon name="calendar" size={16} />
          </span>
          <span className="hp-next-text">
            <span className="hp-next-title">Technical interview</span>
            <span className="hp-next-meta">Interviewer assigned · invitation sent</span>
          </span>
          <span className="hp-next-action">Scheduled</span>
        </div>

        <div className="hp-rows">
          <span className="hp-line" style={{ width: "92%" }} />
          <span className="hp-line" style={{ width: "78%" }} />
          <span className="hp-line" style={{ width: "64%" }} />
        </div>
      </div>

      <div className="hp-float hp-float-assessment" aria-hidden="true">
        <span className="hp-float-icon">
          <Icon name="assessments" size={16} />
        </span>
        <span className="hp-float-text">
          <span className="hp-float-title">Assessment</span>
          <span className="hp-float-meta">Graded · results ready</span>
        </span>
        <span className="badge badge-active">Passed</span>
      </div>

      <div className="hp-float hp-float-feedback" aria-hidden="true">
        <span className="hp-float-icon">
          <Icon name="notes" size={16} />
        </span>
        <span className="hp-float-text">
          <span className="hp-float-title">Interview feedback</span>
          <span className="hp-line" style={{ width: "8.5rem" }} />
          <span className="hp-line" style={{ width: "6rem" }} />
        </span>
        <span className="badge badge-active">Pass</span>
      </div>
    </div>
  );
}
