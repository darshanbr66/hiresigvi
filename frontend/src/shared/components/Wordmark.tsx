/**
 * The HireZ wordmark — text only, by design: no image logo, no placeholder
 * box. "Hire" in the ink colour, the closing "Z" in the accent, set in the
 * display face with tight tracking so it reads as one deliberate word at
 * every size. `variant="mark"` is the compact form for tight spaces (the
 * collapsed 72px sidebar rail): the same indigo "Z" on its own. Either way
 * the accessible name is "HireZ".
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
    <span className={classes} role="img" aria-label="HireZ">
      {variant === "full" && <span aria-hidden="true">Hire</span>}
      <span className="wordmark-z" aria-hidden="true">Z</span>
    </span>
  );
}
