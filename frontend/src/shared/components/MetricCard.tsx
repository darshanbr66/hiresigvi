import { AnimatedNumber } from "./AnimatedNumber";
import { Icon, type IconName } from "./Icon";

export type MetricTone = "neutral" | "brand" | "success" | "danger" | "info" | "interview" | "warn";

/**
 * One key figure: the value first, its label second, optional context third.
 * The icon sits in a small tinted tile in the top-right corner, full strength
 * in the tone colour so it is recognizable at a glance in both themes, but
 * small enough never to compete with the number. Tone only tints the icon,
 * its tile and a hairline —
 * the figure itself stays in the ink colour so a row of cards reads as one
 * set. Every value passed in is real data; the card never derives or
 * invents a number.
 */
export function MetricCard({
  label,
  value,
  context,
  icon,
  tone = "neutral",
  animate = true,
}: {
  label: string;
  value: number;
  context?: string;
  icon: IconName;
  tone?: MetricTone;
  /** Count up when first seen (the true value renders at once for reduced motion). */
  animate?: boolean;
}) {
  return (
    <div className={`stat-card metric-card metric-tone-${tone}`}>
      <span className="metric-icon-tile" aria-hidden="true">
        <Icon name={icon} size={20} />
      </span>
      <span className="metric-value">{animate ? <AnimatedNumber value={value} /> : value}</span>
      <span className="metric-label">{label}</span>
      {context && <span className="metric-context">{context}</span>}
    </div>
  );
}
