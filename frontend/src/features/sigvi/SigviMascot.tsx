import { useEffect, useId, useRef } from "react";
import { useReducedMotion } from "../../shared/hooks/useReducedMotion";

export type MascotState = "idle" | "thinking" | "delight";

interface SigviMascotProps {
  /** Rendered width in px (height follows the variant's aspect ratio). */
  size?: number;
  /** `head` for tight spaces (launcher, avatars); `full` adds the little body. */
  variant?: "head" | "full";
  /** `thinking`: eyes scan, particles orbit, glow quickens. `delight`: a
   * one-shot glow burst and happy eyes when a reply arrives. */
  state?: MascotState;
  /** Off for the small avatars beside every message — a still pose is enough
   * there and keeps a long conversation cheap to render. */
  animated?: boolean;
  /** Accessible name. Omit for a decorative mascot (hidden from assistive
   * tech); give one where the mascot stands alone. */
  label?: string;
}

const GAZES = ["left", "right", "center"] as const;

/**
 * Gives an idle mascot a pulse of life: every few seconds a quick blink, and
 * now and then a glance to one side. Done as two attributes flipped from a
 * timer (CSS transitions do the movement) rather than as infinite animations
 * on the SVG's insides — those can't be composited, so they would keep the
 * whole page rendering every frame for the sake of a blink. Between events,
 * nothing runs at all.
 */
function useIdleLife(ref: React.RefObject<SVGSVGElement | null>, active: boolean) {
  useEffect(() => {
    const svg = ref.current;
    if (!svg || !active) return;

    let next = 0;
    let unblink = 0;
    let beats = 0;

    const beat = () => {
      if (!document.hidden) {
        beats += 1;
        svg.setAttribute("data-blink", "true");
        unblink = window.setTimeout(() => svg.removeAttribute("data-blink"), 130);
        if (beats % 2 === 0) {
          svg.setAttribute("data-gaze", GAZES[Math.floor(Math.random() * GAZES.length)]);
        }
      }
      next = window.setTimeout(beat, 3500 + Math.random() * 4000);
    };
    next = window.setTimeout(beat, 1800 + Math.random() * 2500);

    return () => {
      window.clearTimeout(next);
      window.clearTimeout(unblink);
      svg.removeAttribute("data-blink");
      svg.removeAttribute("data-gaze");
    };
  }, [ref, active]);
}

/**
 * Zodi — the Zodiac Patent careers assistant (internally the `sigvi` feature): a small rounded white-and-blue robot with a
 * dark visor, glowing eyes and a tell-tale antenna light.
 *
 * Pure inline SVG. The 3D feel comes from gradients, a rim highlight and a
 * soft aura — no filters, images or libraries. Movement is kept cheap on
 * purpose (styles/sigvi.css): the ambient bob moves the whole SVG (compositable),
 * blinks and glances are occasional transitions (`useIdleLife`), and the
 * animations inside the SVG only run while it is thinking or celebrating.
 * All of it switches off under `prefers-reduced-motion`.
 */
export function SigviMascot({
  size = 48,
  variant = "head",
  state = "idle",
  animated = true,
  label,
}: SigviMascotProps) {
  const uid = useId().replace(/:/g, "");
  const svgRef = useRef<SVGSVGElement>(null);
  const reducedMotion = useReducedMotion();
  useIdleLife(svgRef, animated && state === "idle" && !reducedMotion);
  const ref = (name: string) => `url(#sv-${name}-${uid})`;
  const id = (name: string) => `sv-${name}-${uid}`;
  const full = variant === "full";

  return (
    <svg
      ref={svgRef}
      className="sigvi-mascot sigvi-avatar"
      data-state={state}
      data-animated={animated}
      data-variant={variant}
      width={size}
      height={full ? Math.round((size * 134) / 120) : Math.round((size * 90) / 112)}
      viewBox={full ? "0 0 120 134" : "4 0 112 90"}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <defs>
        <radialGradient id={id("shell")} cx="0.32" cy="0.2" r="0.95">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.5" stopColor="#e8efff" />
          <stop offset="0.85" stopColor="#b7cafc" />
          <stop offset="1" stopColor="#8ea9ec" />
        </radialGradient>
        <linearGradient id={id("visor")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#0c1440" />
          <stop offset="1" stopColor="#1a2660" />
        </linearGradient>
        <linearGradient id={id("eye")} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#c2f9ff" />
          <stop offset="0.5" stopColor="#55d3ff" />
          <stop offset="1" stopColor="#3d86ff" />
        </linearGradient>
        <linearGradient id={id("pod")} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#86a8ff" />
          <stop offset="1" stopColor="#3f5cf0" />
        </linearGradient>
        <linearGradient id={id("rim")} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.95" />
          <stop offset="1" stopColor="#7f9cf0" stopOpacity="0.25" />
        </linearGradient>
        <radialGradient id={id("eyeglow")}>
          <stop offset="0" stopColor="#5ee0ff" stopOpacity="0.5" />
          <stop offset="1" stopColor="#5ee0ff" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={id("aura")}>
          <stop offset="0" stopColor="#8b7bff" stopOpacity="0.5" />
          <stop offset="0.55" stopColor="#3d6bf5" stopOpacity="0.2" />
          <stop offset="1" stopColor="#3d6bf5" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={id("tip")}>
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.45" stopColor="#8ff2ff" />
          <stop offset="1" stopColor="#3ea6ff" />
        </radialGradient>
      </defs>

      <circle className="sv-aura" cx="60" cy="48" r="58" fill={ref("aura")} />
      {full && <ellipse className="sv-floor" cx="60" cy="128" rx="27" ry="4" fill="#7c6cff" />}

      <g className="sv-float">
        <g className="sv-orbit">
          <circle cx="60" cy="-2" r="2.6" fill="#5ee0ff" />
          <circle cx="113" cy="62" r="2.1" fill="#a99bff" />
          <circle cx="12" cy="76" r="2.3" fill="#ffffff" />
        </g>
        <g className="sv-burst">
          <circle cx="60" cy="48" r="34" fill="none" stroke="#5ee0ff" strokeWidth="1.6" />
        </g>

        {full && (
          <g>
            <rect x="52" y="81" width="16" height="10" rx="4" fill="#1b2765" />
            <circle cx="30" cy="104" r="7.5" fill={ref("shell")} />
            <circle cx="30" cy="110" r="3.2" fill={ref("pod")} />
            <circle cx="90" cy="104" r="7.5" fill={ref("shell")} />
            <circle cx="90" cy="110" r="3.2" fill={ref("pod")} />
            <rect x="37" y="88" width="46" height="35" rx="19" fill={ref("shell")} />
            <rect x="49" y="99" width="22" height="13" rx="6.5" fill={ref("visor")} />
            <circle className="sv-chest" cx="60" cy="105.5" r="3.6" fill={ref("tip")} />
          </g>
        )}

        {/* antenna */}
        <path d="M60 20 V11" stroke="#c3d2fb" strokeWidth="3" strokeLinecap="round" />
        <circle className="sv-tip-glow" cx="60" cy="7.5" r="9" fill="#5ee0ff" opacity="0.32" />
        <circle cx="60" cy="7.5" r="4.2" fill={ref("tip")} />

        {/* ear pods */}
        <circle cx="16" cy="52" r="8" fill={ref("pod")} />
        <circle cx="16" cy="52" r="3" fill="#8ff0ff" opacity="0.9" />
        <circle cx="104" cy="52" r="8" fill={ref("pod")} />
        <circle cx="104" cy="52" r="3" fill="#8ff0ff" opacity="0.9" />

        {/* head shell */}
        <rect x="19" y="18" width="82" height="66" rx="31" fill={ref("shell")} />
        <rect
          x="19"
          y="18"
          width="82"
          height="66"
          rx="31"
          fill="none"
          stroke={ref("rim")}
          strokeWidth="1.3"
        />
        <path
          d="M30 35 C34 25 46 21 58 21"
          stroke="#ffffff"
          strokeOpacity="0.9"
          strokeWidth="3.2"
          strokeLinecap="round"
          fill="none"
        />

        {/* visor + face */}
        <rect x="27" y="31" width="66" height="42" rx="20" fill={ref("visor")} />
        <rect
          x="27"
          y="31"
          width="66"
          height="42"
          rx="20"
          fill="none"
          stroke="#7da2ff"
          strokeOpacity="0.4"
          strokeWidth="1"
        />
        <path
          d="M34 41 C38 36 46 34 54 34"
          stroke="#7da2ff"
          strokeOpacity="0.28"
          strokeWidth="2"
          strokeLinecap="round"
          fill="none"
        />
        <g className="sv-eyes">
          <g className="sv-eye">
            <circle cx="45" cy="50" r="15" fill={ref("eyeglow")} />
            <rect x="39.5" y="41.5" width="11" height="17" rx="5.5" fill={ref("eye")} />
            <ellipse cx="43" cy="46" rx="2" ry="3" fill="#ffffff" opacity="0.85" />
          </g>
          <g className="sv-eye">
            <circle cx="75" cy="50" r="15" fill={ref("eyeglow")} />
            <rect x="69.5" y="41.5" width="11" height="17" rx="5.5" fill={ref("eye")} />
            <ellipse cx="73" cy="46" rx="2" ry="3" fill="#ffffff" opacity="0.85" />
          </g>
          <path
            d="M53 63.5 Q60 69 67 63.5"
            stroke="#7df3ff"
            strokeWidth="2.4"
            strokeLinecap="round"
            fill="none"
          />
        </g>
      </g>
    </svg>
  );
}
