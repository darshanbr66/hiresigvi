import type { ReactNode } from "react";
import type { DescriptionBlock } from "./jobDescription";
import { parseDetailValue, parseJobDescription, splitLabel } from "./jobDescription";
import type { JobDetailFact } from "./jobFacts";

/** Text in which each line's leading "Label:" (e.g. "Annual salary:",
 * "Bond:") is emphasised; the rest stays regular. Same characters either
 * way — line breaks are kept and shown by `white-space: pre-line`. */
function Labelled({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").map((line, i) => {
        const parts = splitLabel(line);
        return (
          <span key={i}>
            {i > 0 && "\n"}
            {parts ? (
              <>
                <strong>{parts.label}</strong>
                {parts.rest}
              </>
            ) : (
              line
            )}
          </span>
        );
      })}
    </>
  );
}

type ListNode = { text: string; children: ListNode[] };

/** Rebuilds the item tree from each item's depth (a depth never jumps more
 * than one level, see ListBuilder). */
function toTree(items: string[], depths: number[] | undefined): ListNode[] {
  const root: ListNode[] = [];
  const stack: ListNode[][] = [root];
  items.forEach((text, i) => {
    const depth = Math.min(depths?.[i] ?? 0, stack.length - 1);
    stack.length = depth + 1;
    const node: ListNode = { text, children: [] };
    stack[depth].push(node);
    stack.push(node.children);
  });
  return root;
}

function ListItems({ nodes, ordered }: { nodes: ListNode[]; ordered: boolean }): ReactNode {
  const List = ordered ? "ol" : "ul";
  return (
    <List>
      {nodes.map((node, i) => (
        <li key={i}>
          <Labelled text={node.text} />
          {node.children.length > 0 && <ListItems nodes={node.children} ordered={false} />}
        </li>
      ))}
    </List>
  );
}

/** The application instruction, as one calm informational callout; the
 * employer's wording exactly as typed. */
function Notice({ text }: { text: string }) {
  return (
    <p className="job-notice" role="note">
      <Labelled text={text} />
    </p>
  );
}

/** Renders parsed blocks: bold headings (at `headingLevel`), the instruction
 * notice, a bold lead statement, bold leading labels, paragraphs and lists. */
function Blocks({ blocks, headingLevel }: { blocks: DescriptionBlock[]; headingLevel: "h3" | "h4" }) {
  const Heading = headingLevel;
  return (
    <>
      {blocks.map((block, i) => {
        if (block.kind === "heading") return <Heading key={i}>{block.text}</Heading>;
        if (block.kind === "notice") return <Notice key={i} text={block.text} />;
        if (block.kind === "paragraph") {
          return block.lead ? (
            <p key={i} className="job-lead">
              <strong>{block.text}</strong>
            </p>
          ) : (
            <p key={i}>
              <Labelled text={block.text} />
            </p>
          );
        }
        return <ListItems key={i} nodes={toTree(block.items, block.depths)} ordered={block.ordered} />;
      })}
    </>
  );
}

/** The employer's description, structured for reading (jobDescription.ts):
 * same words, same order. Shared by every candidate-facing page that shows
 * a job's or drive's description. */
export function JobDescription({ text }: { text: string }) {
  return (
    <div className="job-description-body">
      <Blocks blocks={parseJobDescription(text)} headingLevel="h3" />
    </div>
  );
}

const WIDE_TEXT = 60;

/** A Role-details entry: a bold label over its value, the value as a list
 * when it holds several lines or items. Long or list values take the full
 * row of the two-column grid so they never wrap into a narrow column.
 * `omitNotices` holds instruction sentences the page already shows (in the
 * description), so the same instruction is never rendered twice. */
export function DetailFact({ fact, omitNotices }: { fact: JobDetailFact; omitNotices?: Set<string> }) {
  const blocks = parseDetailValue(fact.value, { splitInline: fact.list, omitNotices });
  if (blocks.length === 0) return null;
  const wide = blocks.some((block) => block.kind !== "paragraph") || fact.value.length > WIDE_TEXT;
  return (
    <div className={wide ? "job-detail job-detail-wide" : "job-detail"}>
      <dt>{fact.label}</dt>
      <dd>
        <Blocks blocks={blocks} headingLevel="h4" />
      </dd>
    </div>
  );
}
