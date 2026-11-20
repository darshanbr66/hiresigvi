/**
 * Structure for a plain-text job description, so it reads as headings,
 * paragraphs and lists instead of one block, with the important parts
 * emphasised. Presentation only: every word of the employer's text is kept,
 * in order — the only characters dropped are list markers ("- ", "• ",
 * "1. "), which the rendered list replaces. Nothing is reworded, shortened
 * or invented.
 *
 * Rules (the conventions recruiters already type):
 * - blank lines separate blocks;
 * - a line starting with -, *, •, ▪, ◦, – or "1." / "1)" is a list item;
 * - the first line of a block is a heading when more lines follow it and it
 *   is short (≤ 60 chars) and doesn't end like a sentence (. ! ? , ;) —
 *   e.g. "Key responsibilities" or "Qualifications:";
 * - anywhere in a block, a line that is a common section name ("Benefits",
 *   "Eligibility", "Interview Process:" …) or a short line ending in ":" is a
 *   heading too;
 * - a short, unpunctuated one-line block directly before a list block is a
 *   heading ("Nice to have" ⏎⏎ "- TypeScript");
 * - a "Job Description" line at the very start repeats the section's own
 *   title, so it stays plain text (no second bold heading);
 * - the first line of real content after it, when it is a single short
 *   sentence and more follows, is the lead statement ("Apply only if you
 *   meet all the requirements and accept all the terms.") — shown in bold;
 * - everything else is a paragraph (its own line breaks kept).
 *
 * Inline: a paragraph or list item that starts with a short label and a
 * colon ("Interview Process: two rounds …") has just that label emphasised
 * (see splitLabel).
 */

export type DescriptionBlock =
  | { kind: "heading"; text: string }
  | { kind: "paragraph"; text: string; lead?: true }
  | { kind: "list"; ordered: boolean; items: string[] };

const BULLET = /^\s*(?:[-*•▪◦–]|(\d+)[.)])\s+/;
const SENTENCE_END = /[.!?,;]$/;
const HEADING_MAX = 60;
const LEAD_MAX = 160;

/** Section names that are headings wherever they stand on their own line. */
const SECTION_NAMES = new Set(
  [
    "about the role", "about the job", "about us", "about the company", "job description", "role overview",
    "overview", "job summary", "summary", "responsibilities", "key responsibilities", "roles and responsibilities",
    "roles & responsibilities", "duties", "requirements", "job requirements", "required skills", "skills",
    "key skills", "required skills and experience", "qualifications", "preferred qualifications", "education",
    "experience", "eligibility", "eligibility criteria", "who can apply", "benefits", "perks", "perks and benefits",
    "what we offer", "compensation", "salary", "stipend", "interview process", "selection process",
    "hiring process", "recruitment process", "terms", "terms and conditions", "terms & conditions", "how to apply",
    "note", "important", "important note", "location", "work location", "working hours", "timings", "duration",
    "nice to have", "good to have", "training", "bond", "documents required",
  ],
);

/** "Label: rest" — a short leading label (≤ 5 words, ≤ 40 chars, no URL). */
const LABEL = /^([A-Za-z][A-Za-z0-9 &/()'’+-]{0,39}):(\s+\S[\s\S]*)$/;

/** Splits "Interview Process: two rounds" into the label (with its colon) and
 * the rest, or returns null when the text has no such label. The two parts
 * concatenate back to the original text exactly. */
export function splitLabel(text: string): { label: string; rest: string } | null {
  const match = LABEL.exec(text);
  if (!match) return null;
  const label = match[1];
  if (label.trim().split(/\s+/).length > 5 || /https?$/i.test(label)) return null;
  return { label: `${label}:`, rest: match[2] };
}

function isSectionName(line: string): boolean {
  return SECTION_NAMES.has(line.trim().replace(/:$/, "").trim().toLowerCase());
}

function looksLikeHeading(line: string): boolean {
  const text = line.trim();
  return text.length > 0 && text.length <= HEADING_MAX && !SENTENCE_END.test(text) && !BULLET.test(line);
}

/** A line that is a heading wherever it stands: a section name, or a short
 * line that is nothing but a label ending in ":". */
function isStandaloneHeading(line: string): boolean {
  const text = line.trim();
  if (BULLET.test(line) || text.length === 0 || text.length > HEADING_MAX) return false;
  return isSectionName(text) || (text.endsWith(":") && !text.slice(0, -1).includes(":"));
}

export function parseJobDescription(source: string): DescriptionBlock[] {
  const blocks: DescriptionBlock[] = [];
  const chunks = source.replace(/\r\n?/g, "\n").split(/\n[ \t]*\n/);

  for (const chunk of chunks) {
    const lines = chunk.split("\n").map((line) => line.replace(/\s+$/, "")).filter((line) => line.trim() !== "");
    let paragraph: string[] = [];
    let list: { ordered: boolean; items: string[] } | null = null;

    const flushParagraph = () => {
      if (paragraph.length) blocks.push({ kind: "paragraph", text: paragraph.join("\n") });
      paragraph = [];
    };
    const flushList = () => {
      if (list) blocks.push({ kind: "list", ...list });
      list = null;
    };

    lines.forEach((line, index) => {
      const bullet = BULLET.exec(line);
      if (bullet) {
        flushParagraph();
        const ordered = bullet[1] !== undefined;
        if (!list || list.ordered !== ordered) {
          flushList();
          list = { ordered, items: [] };
        }
        list.items.push(line.slice(bullet[0].length).trim());
        return;
      }
      flushList();
      const leadsBlock = index === 0 && lines.length > 1 && looksLikeHeading(line);
      if (leadsBlock || (lines.length > 1 && isStandaloneHeading(line))) {
        flushParagraph();
        blocks.push({ kind: "heading", text: line.trim() });
        return;
      }
      paragraph.push(line.trim());
    });
    flushParagraph();
    flushList();
  }

  const shaped = blocks.map((block, i): DescriptionBlock => {
    if (block.kind !== "paragraph" || block.text.includes("\n")) return block;
    const next = blocks[i + 1];
    // a lone short line right before a list introduces that list
    if (next?.kind === "list" && looksLikeHeading(block.text)) return { kind: "heading", text: block.text };
    // a standalone section name ("Benefits") on its own line
    if (next && isStandaloneHeading(block.text)) return { kind: "heading", text: block.text };
    return block;
  });
  return markLead(dropRedundantTitle(shaped));
}

/** "Job Description" at the very start of the text repeats the section's own
 * title (the page already shows it as a heading), so it stays as plain,
 * unemphasised text rather than becoming a second bold heading. */
const REDUNDANT_TITLE = /^job description:?$/i;

function dropRedundantTitle(blocks: DescriptionBlock[]): DescriptionBlock[] {
  const first = blocks[0];
  if (first && first.kind !== "list" && REDUNDANT_TITLE.test(first.text.trim())) {
    return [{ kind: "paragraph", text: first.text }, ...blocks.slice(1)];
  }
  return blocks;
}

/** The opening statement — the first line of real content (after a
 * redundant title), when it is a single short sentence and more follows —
 * becomes the bold lead ("Apply only if you meet all the requirements and
 * accept all the terms."). If more lines follow it in the same paragraph,
 * it is split off into its own paragraph; nothing else changes. */
function markLead(blocks: DescriptionBlock[]): DescriptionBlock[] {
  const start = blocks[0]?.kind === "paragraph" && REDUNDANT_TITLE.test(blocks[0].text.trim()) ? 1 : 0;
  const block = blocks[start];
  if (!block || block.kind !== "paragraph") return blocks;
  const [firstLine, ...restLines] = block.text.split("\n");
  // Split a statement off a longer paragraph only when it directly follows
  // the redundant title; otherwise it must stand alone, so the first sentence
  // of an ordinary opening paragraph is never singled out.
  if (restLines.length > 0 && start === 0) return blocks;
  const moreFollows = restLines.length > 0 || start + 1 < blocks.length;
  const isStatement = firstLine.length <= LEAD_MAX && /[.!]$/.test(firstLine) && !splitLabel(firstLine);
  if (!moreFollows || !isStatement) return blocks;
  const lead: DescriptionBlock = { kind: "paragraph", text: firstLine, lead: true };
  const rest: DescriptionBlock[] = restLines.length ? [{ kind: "paragraph", text: restLines.join("\n") }] : [];
  return [...blocks.slice(0, start), lead, ...rest, ...blocks.slice(start + 1)];
}
