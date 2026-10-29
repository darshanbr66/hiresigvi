/**
 * The HireZ wordmark — text only, by design: no image logo, no mark, no
 * placeholder box. "Hire" in the ink colour, the closing "Z" in the accent,
 * set in the display face with tight tracking so it reads as one deliberate
 * word at every size. (The collapsed sidebar rail shows only its
 * expand toggle, not a shortened mark.) The accessible name is "HireZ".
 */
export function Wordmark({
  size = "md",
  className,
}: {
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  return (
    <span className={`wordmark wordmark-${size}${className ? ` ${className}` : ""}`} role="img" aria-label="HireZ">
      <span aria-hidden="true">Hire</span>
      <span className="wordmark-z" aria-hidden="true">Z</span>
    </span>
  );
}
