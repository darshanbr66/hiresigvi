import type { ReactNode } from "react";

/**
 * A small hand-drawn icon set (24×24, 1.75 stroke, round caps) — enough for
 * navigation, empty states and status cues without pulling in an icon
 * library. Icons are decorative by default (`aria-hidden`); pass `label` when
 * an icon stands alone with no adjacent text.
 */
const PATHS = {
  institution: (
    <>
      <path d="M3 9.5 12 4l9 5.5" />
      <path d="M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18" />
    </>
  ),
  program: (
    <>
      <path d="M2.5 9 12 4.5 21.5 9 12 13.5 2.5 9Z" />
      <path d="M6.5 11v4.5c0 1.4 2.5 3 5.5 3s5.5-1.6 5.5-3V11M21.5 9v5" />
    </>
  ),
  questionBank: (
    <>
      <rect x="4" y="3" width="13" height="16" rx="2" />
      <path d="M7 21h12a1 1 0 0 0 1-1V7" />
      <path d="M8.5 8.5a2 2 0 1 1 2.6 1.9c-.5.2-.6.5-.6 1v.3M10.5 14.5v.01" />
    </>
  ),
  overview: (
    <>
      <rect x="3" y="3" width="7" height="8" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="15" width="7" height="6" rx="1.5" />
    </>
  ),
  jobs: (
    <>
      <rect x="3" y="7" width="18" height="13" rx="2" />
      <path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18" />
    </>
  ),
  candidates: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
      <circle cx="17" cy="9" r="2.4" />
      <path d="M17 14.2c2.4 0 4 1.8 4 4.3" />
    </>
  ),
  applications: (
    <>
      <path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" />
      <path d="M14 3v5h5M8.5 13h7M8.5 17h5" />
    </>
  ),
  assessments: (
    <>
      <rect x="5" y="4" width="14" height="17" rx="2" />
      <path d="M9 4V3h6v1M9 13l2 2 4-4" />
    </>
  ),
  campus: (
    <>
      <path d="M2 9l10-5 10 5-10 5z" />
      <path d="M6 11.5V16c0 1.2 2.7 3 6 3s6-1.8 6-3v-4.5M22 9v6" />
    </>
  ),
  reports: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  team: (
    <>
      <circle cx="12" cy="5" r="2.5" />
      <circle cx="5" cy="19" r="2.5" />
      <circle cx="19" cy="19" r="2.5" />
      <path d="M12 7.5V12M12 12H5v4.5M12 12h7v4.5" />
    </>
  ),
  email: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 7l9 6 9-6" />
    </>
  ),
  activities: <path d="M3 12h4l3-8 4 16 3-8h4" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="M20 20l-4.2-4.2" />
    </>
  ),
  graph: (
    <>
      <circle cx="6" cy="6" r="2" />
      <circle cx="18" cy="8" r="2" />
      <circle cx="8" cy="18" r="2" />
      <circle cx="18" cy="17" r="2" />
      <path d="M7.7 6.9l8.4.8M6.6 8l1 8M9.9 17.7l6.2-.5M18 10l.1 5" />
    </>
  ),
  sparkles: (
    <>
      <path d="M11 3l1.8 4.7L17.5 9.5l-4.7 1.8L11 16l-1.8-4.7L4.5 9.5l4.7-1.8z" />
      <path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z" />
      <path d="M9 12l2 2 4-4" />
    </>
  ),
  eye: (
    <>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  pin: (
    <>
      <path d="M12 21s7-5.5 7-11a7 7 0 0 0-14 0c0 5.5 7 11 7 11z" />
      <circle cx="12" cy="10" r="2.5" />
    </>
  ),
  send: (
    <>
      <path d="M21 3L3 10.5l7 3 3 7z" />
      <path d="M10 13.5L21 3" />
    </>
  ),
  trash: <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />,
  plus: <path d="M12 5v14M5 12h14" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  /* Panel outline with the rail filled in and a chevron pointing at the rail
   * — collapse pushes the sidebar closed, expand pulls it back out. */
  "sidebar-collapse": (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
      <path d="M16.5 9.5L14 12l2.5 2.5" />
    </>
  ),
  "sidebar-expand": (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
      <path d="M13 9.5l2.5 2.5-2.5 2.5" />
    </>
  ),
  "arrow-right": <path d="M5 12h14M13 6l6 6-6 6" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z" />,
  inbox: (
    <>
      <path d="M3 13l3-8h12l3 8v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      <path d="M3 13h5l1 3h6l1-3h5" />
    </>
  ),
  notes: (
    <>
      <path d="M6 3h9l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M14.5 3v4.5H19M8 12h8M8 15.5h5" />
    </>
  ),
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
      <path d="M8 14h1M12 14h1M16 14h1M8 17.5h1M12 17.5h1" />
    </>
  ),
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof PATHS;

export function Icon({
  name,
  size = 20,
  className,
  label,
}: {
  name: IconName;
  size?: number;
  className?: string;
  /** Accessible name for an icon that has no adjacent text. */
  label?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      /* Which glyph is drawn — the only thing that distinguishes two
       * otherwise identical decorative SVGs to CSS and to tests. */
      data-icon={name}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}

/**
 * The product mark, redrawn from the app icon that ships as the favicon: a
 * magnifier over a person (finding the right people) with three circuit nodes
 * (the intelligence behind it). One solid brand tile, no gradient, so it stays
 * crisp at 24px and identical in both themes — and the browser tab and the
 * page read as the same identity.
 */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden="true"
      focusable="false"
      className="logo-mark"
    >
      <rect width="32" height="32" rx="9" fill="var(--color-primary-solid)" />
      {/* circuit nodes */}
      <path d="M19.6 9.6l2.6-2.2M20.4 12.6h3.1l1.4-1.4M20.2 15.8h2.7" stroke="#8fe3ff" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      <circle cx="23.4" cy="6.6" r="1.5" fill="none" stroke="#8fe3ff" strokeWidth="1.2" />
      <circle cx="26.2" cy="10.3" r="1.5" fill="none" stroke="#8fe3ff" strokeWidth="1.2" />
      <circle cx="24.6" cy="16.2" r="1.5" fill="none" stroke="#8fe3ff" strokeWidth="1.2" />
      {/* magnifier + person */}
      <circle cx="13.6" cy="15" r="6.6" fill="none" stroke="#fff" strokeWidth="2" />
      <path d="M18.4 19.8l4.6 4.6" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" />
      <circle cx="13.6" cy="12.9" r="2.1" fill="#fff" />
      <path d="M9.7 19c.5-2.3 2-3.4 3.9-3.4s3.4 1.1 3.9 3.4" fill="#fff" />
    </svg>
  );
}
