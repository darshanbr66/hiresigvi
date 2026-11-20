/**
 * The ZPHIREX wordmark — text only, by design: no image logo, no placeholder
 * box. The leading "Z" in the accent, "PHIREX" in the ink colour, set in the
 * display face with open uppercase tracking — the same lockup as the public
 * homepage (features/public/HomeGraphics.tsx). `variant="mark"` is the
 * compact form for tight spaces (the collapsed 72px sidebar rail): the same
 * accent "Z" on its own. Either way the accessible name is "ZPHIREX".
 */
export function Wordmark({
  size = "md",
  variant = "full",
  className,
}: {
  size?: "sm" | "md" | "lg";
  variant?: "full" | "mark";
  className?: string;
}) {
  const classes = `wordmark wordmark-${size}${variant === "mark" ? " wordmark-mark" : ""}${className ? ` ${className}` : ""}`;
  return (
    <span className={classes} role="img" aria-label="ZPHIREX">
      <span className="wordmark-z" aria-hidden="true">Z</span>
      {variant === "full" && <span aria-hidden="true">PHIREX</span>}
    </span>
  );
}
