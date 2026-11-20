import type { OpportunityGlyphName } from "./homeContent";

/**
 * Presentation-only graphics for the public homepage: the ZPHIREX wordmark
 * and the four opportunity glyphs. Inline SVG/CSS — no images besides the
 * organization's official logo (see homeContent.ts).
 */

/** ZPHIREX — text only, the Z in the brand colour. */
export function ZphirexWordmark({ size = "md" }: { size?: "sm" | "md" }) {
  return (
    <span className={`zp-wordmark zp-wordmark-${size}`} role="img" aria-label="ZPHIREX">
      <span className="zp-wordmark-z" aria-hidden="true">
        Z
      </span>
      <span aria-hidden="true">PHIREX</span>
    </span>
  );
}

const GLYPHS: Record<OpportunityGlyphName, React.ReactNode> = {
  // briefcase
  jobs: (
    <>
      <rect x="3" y="7.5" width="18" height="12.5" rx="2" />
      <path d="M8.5 7.5V5.5a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v2" />
      <path d="M3 13h18" />
    </>
  ),
  // compass: finding a direction
  internships: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m15.5 8.5-2 5-5 2 2-5 5-2Z" />
    </>
  ),
  // graduation cap
  campus: (
    <>
      <path d="M2.5 9 12 4.5 21.5 9 12 13.5 2.5 9Z" />
      <path d="M6.5 11v4.5c0 1.4 2.5 3 5.5 3s5.5-1.6 5.5-3V11M21.5 9v5" />
    </>
  ),
  // open book
  programs: (
    <>
      <path d="M12 6.5c-1.8-1.4-4.6-2-8-1.8v13c3.4-.2 6.2.4 8 1.8 1.8-1.4 4.6-2 8-1.8v-13c-3.4-.2-6.2.4-8 1.8Z" />
      <path d="M12 6.5v13" />
    </>
  ),
};

export function OpportunityGlyph({ name, size = 24 }: { name: OpportunityGlyphName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {GLYPHS[name]}
    </svg>
  );
}
