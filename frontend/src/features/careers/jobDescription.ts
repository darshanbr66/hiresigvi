/**
 * Structure for a plain-text job description, so it reads as headings,
 * paragraphs and lists instead of one block, with the important parts
 * emphasised. Presentation only: every word of the employer's text is kept,
 * in order — the only characters dropped are list markers at the start of a
 * line ("- ", "• ", "1. ") which the rendered list replaces. Nothing is
 * reworded, shortened, reordered or invented, and the stored text is never
 * changed.
 *
 * Lists are recognised by line structure only. A hyphen or dash inside a
 * line is ordinary text — "₹6,00,000 - ₹8,00,000", "1–3 years",
 * "2023 - 2025", "Mon - Fri", "9:30 AM - 6:30 PM", "Full-time" all stay
 * exactly as typed. (Role-details list fields are the one, guarded,
 * exception — see parseDetailValue.)
 *
 * Rules (the conventions recruiters already type):
 * - blank lines separate blocks;
 * - a line starting with -, *, •, ▪, ◦, – or "1." / "1)" followed by a space
 *   is a list item; a deeper-indented marked line is a nested item; an
 *   indented unmarked line right after an item continues that item;
 * - except a dash line that finishes the previous line's range
 *   ("Annual salary: ₹6,00,000" ⏎ "– ₹8,00,000.") — rejoined with its dash,
 *   on strong evidence only (see rangeContinuation);
 * - the first line of a block is a heading when more lines follow it and it
 *   is short (≤ 60 chars), doesn't end like a sentence (. ! ? , ;) and is not
 *   a "Label: value" pair — e.g. "Key responsibilities" or "Qualifications:";
 * - anywhere in a block, a line that is a common section name ("Benefits",
 *   "Compensation & Benefits", "Hiring Process:" …) or a short line that is
 *   only a label ending in ":" is a heading too;
 * - a short, unpunctuated one-line block directly before a list block is a
 *   heading ("Nice to have" ⏎⏎ "- TypeScript");
 * - a "Job Description" line at the very start repeats the section's own
 *   title, so it stays plain text (no second bold heading);
 * - the application instruction ("Apply only if you meet the requirements
 *   and are comfortable with the stated employment terms.") is a notice,
 *   shown once as a callout wherever it first stands; a repeat of the same
 *   sentence later in the text is not shown a second time;
 * - otherwise, the first line of real content, when it is a single short
 *   sentence and more follows, is the lead statement — shown in bold;
 * - everything else is a paragraph (its own line breaks kept).
 *
 * Inline: a paragraph or list item that starts with a short label and a
 * colon ("Annual salary: ₹6,00,000 - ₹8,00,000 per annum.") has just that
 * label emphasised (see splitLabel); the value is untouched.
 */

export type DescriptionBlock =
  | { kind: "heading"; text: string }
  | { kind: "paragraph"; text: string; lead?: true }
  | { kind: "notice"; text: string }
  /** `depths[i]` is item i's nesting level (0 = top); omitted when flat. */
  | { kind: "list"; ordered: boolean; items: string[]; depths?: number[] };

const BULLET = /^(\s*)(?:[-*•▪◦–]|(\d+)[.)])\s+/;
const SENTENCE_END = /[.!?,;]$/;
const HEADING_MAX = 60;
const LEAD_MAX = 160;

/** Section names that are headings wherever they stand on their own line. */
const SECTION_NAMES = new Set(
  [
    "about the role", "about the job", "about us", "about the company", "job description", "role overview",
    "role details", "overview", "job summary", "summary", "responsibilities", "key responsibilities",
    "roles and responsibilities", "roles & responsibilities", "duties", "requirements", "job requirements",
    "required skills", "skills", "key skills", "required skills and experience", "qualifications",
    "qualification", "educational qualification", "preferred qualifications", "education", "experience",
    "eligibility", "eligibility criteria", "who can apply", "benefits", "perks", "perks and benefits",
    "what we offer", "compensation", "compensation & benefits", "compensation and benefits",
    "salary and benefits", "salary & benefits", "salary", "stipend", "interview process", "selection process",
    "hiring process", "recruitment process", "terms", "employment terms", "terms and conditions",
    "terms & conditions", "how to apply", "note", "important", "important note", "location", "work location",
    "job location", "working hours", "working days", "timings", "schedule", "duration", "nice to have",
    "good to have", "training", "bond", "documents required",
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

/** The candidate-facing application instruction ("Apply only if you meet
 * the requirements …"), optionally after a "Note:" / "Important:" label. */
const INSTRUCTION = /^(?:(?:please note|note|important|important note)\s*:\s*)?(?:please\s+)?(?:apply only if|only apply if)\b/i;

export function isApplicationInstruction(text: string): boolean {
  return INSTRUCTION.test(text.trim());
}

/** Same sentence, ignoring case, spacing and trailing punctuation — used to
 * recognise a repeat of the instruction so it is rendered only once. */
export function instructionKey(text: string): string {
  return text.trim().replace(/\s+/g, " ").replace(/[.!\s]+$/, "").toLowerCase();
}

/* ---------- guarded inline item splitting (Role-details list fields) ---------- */

const INLINE_SEPARATOR = /\s+[-–—•▪]\s+/g;
const CURRENCY_WORD = /^(?:rs\.?|inr|usd|eur|gbp|aed|sgd)$/i;
const WEEKDAY = /^(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|nesday|sday|rsday|urday)?\.?$/i;
const MONTH =
  /^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)(?:uary|ruary|ch|il|e|y|ust|tember|ober|ember)?\.?$/i;

/** A token that can be one end of a range: a number or amount (6,00,000 /
 * ₹8,00,000 / 60% / 9:30 / 6LPA / 3+), a time of day (AM / PM), a weekday
 * or a month. */
function isRangeEnd(token: string | undefined): boolean {
  if (!token) return false;
  const t = token.replace(/^[(₹$€£]+/, "").replace(/[),.;:]+$/, "");
  if (/^\d[\d,.:]*(?:%|\+|k|l|lpa|lakhs?|cr|yrs?|years?|months?|am|pm)?$/i.test(t)) return true;
  return /^(?:a\.?m|p\.?m)\.?$/i.test(t) || WEEKDAY.test(t) || MONTH.test(t);
}

/**
 * Splits one line of requirements typed together — "B.Tech - 2024 batch -
 * No active backlogs" — into its items. Only when the evidence is strong:
 * at least two " - " separators (three items), no leading "Label:" (a
 * label/value pair stays whole), and no separator that sits between two
 * range ends ("₹6,00,000 - ₹8,00,000", "2023 - 2025", "Mon - Fri",
 * "9:30 AM - 6:30 PM"). Anything ambiguous comes back unchanged as a single
 * item. Only the separators are dropped; every word is kept, in order.
 */
export function splitInlineItems(text: string): string[] {
  if (splitLabel(text)) return [text];
  const separators = [...text.matchAll(INLINE_SEPARATOR)];
  if (separators.length < 2) return [text];
  for (const sep of separators) {
    const before = text.slice(0, sep.index).trim().split(/\s+/);
    const after = text.slice(sep.index + sep[0].length).trim().split(/\s+/);
    const right = CURRENCY_WORD.test(after[0] ?? "") ? after[1] : after[0];
    if (isRangeEnd(before[before.length - 1]) && isRangeEnd(right)) return [text];
  }
  const parts = text.split(INLINE_SEPARATOR).map((part) => part.trim());
  return parts.some((part) => part === "") ? [text] : parts;
}

const RANGE_DASH_LINE = /^\s*([-–—])\s+(\S.*)$/;
const RANGE_TAIL_MAX_WORDS = 4;

/**
 * A dash line that finishes the previous line's range rather than starting a
 * list item — the employer broke "Annual salary: ₹6,00,000 – ₹8,00,000."
 * across two lines, so the second half begins with the range's own dash.
 * Returns the text to append to the previous line (the dash kept: it is
 * part of the range), or null when it is an ordinary list item.
 *
 * Joined only on strong evidence, otherwise the line stays a list item:
 * - the previous line doesn't end like a sentence or label (. ! ? , ; :),
 *   ends in a range end (amount, number, time, weekday, month) and has real
 *   words before it ("Annual salary:", "Shift:") — so a list of bare values
 *   ("- 2023" ⏎ "- 2024", "- 9:30 AM" ⏎ "- 6:30 PM") stays a list;
 * - the dash line starts with a range end (after an optional "Rs."/"INR")
 *   and is short (≤ 4 words: "₹8,00,000 per annum.").
 */
export function rangeContinuation(previous: string | undefined, line: string): string | null {
  if (!previous) return null;
  const match = RANGE_DASH_LINE.exec(line);
  if (!match) return null;
  const prev = previous.trimEnd();
  if (/[.!?,;:]$/.test(prev)) return null;
  const before = prev.split(/\s+/);
  if (!isRangeEnd(before[before.length - 1])) return null;
  const hasWords = before.some((token) => !isRangeEnd(token) && !CURRENCY_WORD.test(token));
  if (!hasWords) return null;
  const after = match[2].trim().split(/\s+/);
  const right = CURRENCY_WORD.test(after[0]) ? after[1] : after[0];
  if (!isRangeEnd(right) || after.length > RANGE_TAIL_MAX_WORDS + (right === after[0] ? 0 : 1)) return null;
  return `${match[1]} ${match[2].trim()}`;
}

/* ---------- shared line helpers ---------- */

function isSectionName(line: string): boolean {
  return SECTION_NAMES.has(line.trim().replace(/:$/, "").trim().toLowerCase());
}

function looksLikeHeading(line: string): boolean {
  const text = line.trim();
  return (
    text.length > 0 &&
    text.length <= HEADING_MAX &&
    !SENTENCE_END.test(text) &&
    !BULLET.test(line) &&
    !splitLabel(text) &&
    !isApplicationInstruction(text)
  );
}

/** A line that is a heading wherever it stands: a section name, or a short
 * line that is nothing but a label ending in ":". */
function isStandaloneHeading(line: string): boolean {
  const text = line.trim();
  if (BULLET.test(line) || text.length === 0 || text.length > HEADING_MAX) return false;
  return isSectionName(text) || (text.endsWith(":") && !text.slice(0, -1).includes(":"));
}

function indentOf(line: string): number {
  const lead = /^[ \t]*/.exec(line)![0];
  return lead.replace(/\t/g, "    ").length;
}

/** Collects list items with their nesting depth from marker indentation. */
class ListBuilder {
  items: string[] = [];
  depths: number[] = [];
  private indents: number[] = [];
  readonly ordered: boolean;
  constructor(ordered: boolean) {
    this.ordered = ordered;
  }

  add(text: string, indent: number) {
    // pop back to the level this indent belongs to; one level deeper at most
    while (this.indents.length && indent < this.indents[this.indents.length - 1]) this.indents.pop();
    if (!this.indents.length || indent > this.indents[this.indents.length - 1]) this.indents.push(indent);
    this.items.push(text);
    this.depths.push(this.indents.length - 1);
  }

  continueLast(text: string) {
    this.items[this.items.length - 1] += `\n${text}`;
  }

  get last(): string | undefined {
    return this.items[this.items.length - 1];
  }

  /** Finishes the last item's range on the same line (see rangeContinuation). */
  extendLast(tail: string) {
    this.items[this.items.length - 1] += ` ${tail}`;
  }

  block(): DescriptionBlock {
    const nested = this.depths.some((d) => d > 0);
    return nested
      ? { kind: "list", ordered: this.ordered, items: this.items, depths: this.depths }
      : { kind: "list", ordered: this.ordered, items: this.items };
  }
}

/* ---------- job description ---------- */

export function parseJobDescription(source: string): DescriptionBlock[] {
  const blocks: DescriptionBlock[] = [];
  const chunks = source.replace(/\r\n?/g, "\n").split(/\n[ \t]*\n/);

  for (const chunk of chunks) {
    const lines = chunk.split("\n").map((line) => line.replace(/\s+$/, "")).filter((line) => line.trim() !== "");
    let paragraph: string[] = [];
    let list: ListBuilder | null = null;
    let lastItemIndent = 0;

    const flushParagraph = () => {
      if (paragraph.length) blocks.push({ kind: "paragraph", text: paragraph.join("\n") });
      paragraph = [];
    };
    const flushList = () => {
      if (list) blocks.push(list.block());
      list = null;
    };

    lines.forEach((line, index) => {
      // the second half of a range broken onto its own dash line
      const listTail = list ? rangeContinuation((list as ListBuilder).last, line) : null;
      if (listTail) {
        (list as ListBuilder).extendLast(listTail);
        return;
      }
      const paragraphTail = paragraph.length ? rangeContinuation(paragraph[paragraph.length - 1], line) : null;
      if (paragraphTail) {
        paragraph[paragraph.length - 1] += ` ${paragraphTail}`;
        return;
      }
      const bullet = BULLET.exec(line);
      if (bullet) {
        flushParagraph();
        const ordered = bullet[2] !== undefined;
        const indent = indentOf(line);
        // a different marker kind at the top level starts a new list; nested
        // items stay in the list they belong to
        if (!list || (list.ordered !== ordered && indent <= lastItemIndent)) {
          flushList();
          list = new ListBuilder(ordered);
        }
        list.add(line.slice(bullet[0].length).trim(), indent);
        lastItemIndent = indent;
        return;
      }
      // an indented line straight after an item is that item's next line
      if (list && indentOf(line) > lastItemIndent) {
        list.continueLast(line.trim());
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
  const withNotices = extractNotices(dropRedundantTitle(shaped));
  return withNotices.some((block) => block.kind === "notice") ? withNotices : markLead(withNotices);
}

/** "Job Description" at the very start of the text repeats the section's own
 * title (the page already shows it as a heading), so it stays as plain,
 * unemphasised text rather than becoming a second bold heading. */
const REDUNDANT_TITLE = /^job description:?$/i;

function dropRedundantTitle(blocks: DescriptionBlock[]): DescriptionBlock[] {
  const first = blocks[0];
  if (first && (first.kind === "heading" || first.kind === "paragraph") && REDUNDANT_TITLE.test(first.text.trim())) {
    return [{ kind: "paragraph", text: first.text }, ...blocks.slice(1)];
  }
  return blocks;
}

/**
 * Lifts the application instruction out of whatever paragraph or list it
 * was typed in into its own notice block, at the same position — content
 * before it and after it stays where it was. The instruction is shown once:
 * a later line with the same sentence is not rendered again.
 */
function extractNotices(blocks: DescriptionBlock[]): DescriptionBlock[] {
  const out: DescriptionBlock[] = [];
  let shown: string | null = null;
  const emitNotice = (text: string) => {
    const key = instructionKey(text);
    if (shown === key) return;
    shown ??= key;
    out.push({ kind: "notice", text });
  };

  for (const block of blocks) {
    if (block.kind === "paragraph") {
      const lines = block.text.split("\n");
      if (!lines.some(isApplicationInstruction)) {
        out.push(block);
        continue;
      }
      let run: string[] = [];
      const flush = () => {
        if (run.length) out.push({ kind: "paragraph", text: run.join("\n") });
        run = [];
      };
      for (const line of lines) {
        if (isApplicationInstruction(line)) {
          flush();
          emitNotice(line);
        } else run.push(line);
      }
      flush();
      continue;
    }
    if (block.kind === "list" && block.items.some((item, i) => isApplicationInstruction(item) && !block.depths?.[i])) {
      let items: string[] = [];
      let depths: number[] = [];
      const flush = () => {
        if (items.length) {
          out.push(
            depths.some((d) => d > 0)
              ? { kind: "list", ordered: block.ordered, items, depths }
              : { kind: "list", ordered: block.ordered, items },
          );
        }
        items = [];
        depths = [];
      };
      block.items.forEach((item, i) => {
        const depth = block.depths?.[i] ?? 0;
        if (depth === 0 && isApplicationInstruction(item)) {
          flush();
          emitNotice(item);
        } else {
          items.push(item);
          depths.push(depth);
        }
      });
      flush();
      continue;
    }
    out.push(block);
  }
  return out;
}

/** The opening statement — the first line of real content (after a
 * redundant title), when it is a single short sentence and more follows —
 * becomes the bold lead. If more lines follow it in the same paragraph, it
 * is split off into its own paragraph; nothing else changes. */
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

/** The instruction sentences a description shows as its notice, so another
 * section on the same page can avoid showing the same sentence twice. */
export function descriptionNoticeKeys(source: string | null | undefined): Set<string> {
  if (!source) return new Set();
  return new Set(
    parseJobDescription(source)
      .filter((block) => block.kind === "notice")
      .map((block) => instructionKey(block.text)),
  );
}

/* ---------- Role-details values ---------- */

/**
 * Structure for one Role-details value (Eligibility, Terms, Schedule …), so a
 * multi-line value reads as a list rather than a paragraph. Same contract as
 * parseJobDescription: every word kept, in order; only list markers (and,
 * for list fields, guarded inline separators) are dropped.
 *
 * - a single line with no list marker stays a plain paragraph — so
 *   "Mon - Fri, 9:30 AM - 6:30 PM" or "1–3 years" is always one value;
 * - otherwise every line is its own list item (numbered when typed "1." /
 *   "1)"), and a line that is only a label ("Education:", "Terms:")
 *   followed by more lines is a sub-heading for the items under it;
 * - with `splitInline` (requirement-style fields: Eligibility, Terms), a
 *   line that runs three or more requirements together with " - " is one
 *   item each — see splitInlineItems for the guards (never a range, never a
 *   label/value pair);
 * - the application instruction is a notice; `omitNotices` lists instruction
 *   sentences already shown elsewhere on the page, which are not repeated.
 */
export function parseDetailValue(
  source: string,
  options: { splitInline?: boolean; omitNotices?: Set<string> } = {},
): DescriptionBlock[] {
  const lines = source
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/\s+$/, ""))
    .filter((line) => line.trim() !== "")
    .filter((line) => !(isApplicationInstruction(line.replace(BULLET, "")) && options.omitNotices?.has(instructionKey(line.replace(BULLET, "")))));
  if (lines.length === 0) return [];

  const itemsOf = (text: string) => (options.splitInline ? splitInlineItems(text) : [text]);
  if (lines.length === 1 && !BULLET.test(lines[0]) && itemsOf(lines[0].trim()).length === 1) {
    const text = lines[0].trim();
    return [isApplicationInstruction(text) ? { kind: "notice", text } : { kind: "paragraph", text }];
  }

  const blocks: DescriptionBlock[] = [];
  let list: ListBuilder | null = null;
  const flushList = () => {
    if (list) blocks.push(list.block());
    list = null;
  };
  const addItems = (ordered: boolean, items: string[], indent: number) => {
    if (!list || list.ordered !== ordered) {
      flushList();
      list = new ListBuilder(ordered);
    }
    for (const item of items) list.add(item, indent);
  };

  lines.forEach((line, index) => {
    const tail = list ? rangeContinuation((list as ListBuilder).last, line) : null;
    if (tail) {
      (list as ListBuilder).extendLast(tail);
      return;
    }
    const bullet = BULLET.exec(line);
    const text = bullet ? line.slice(bullet[0].length).trim() : line.trim();
    if (isApplicationInstruction(text)) {
      flushList();
      blocks.push({ kind: "notice", text });
      return;
    }
    if (bullet) {
      addItems(bullet[2] !== undefined, itemsOf(text), indentOf(line));
      return;
    }
    if (index < lines.length - 1 && isStandaloneHeading(line)) {
      flushList();
      blocks.push({ kind: "heading", text });
      return;
    }
    addItems(false, itemsOf(text), 0);
  });
  flushList();
  return blocks;
}
