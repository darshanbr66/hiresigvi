import { describe, expect, it } from "vitest";
import { parseJobDescription, splitLabel } from "./jobDescription";

const SAMPLE = `About the role
We are looking for a Full Stack Developer with hands-on MERN experience.
You will own features end to end.

Key responsibilities
- Design, build and maintain SPAs.
- Write unit tests (Jest).

Nice to have

- TypeScript, Next.js.
• Docker

Steps:
1. Apply online
2) Take the assessment`;

/** Every word of the source, in order, ignoring list markers and spacing. */
function words(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.replace(/^\s*(?:[-*•▪◦–]|\d+[.)])\s+/, ""))
    .join(" ")
    .split(/\s+/)
    .filter(Boolean);
}

describe("parseJobDescription", () => {
  it("finds headings, paragraphs and lists", () => {
    expect(parseJobDescription(SAMPLE)).toEqual([
      { kind: "heading", text: "About the role" },
      {
        kind: "paragraph",
        text: "We are looking for a Full Stack Developer with hands-on MERN experience.\nYou will own features end to end.",
      },
      { kind: "heading", text: "Key responsibilities" },
      { kind: "list", ordered: false, items: ["Design, build and maintain SPAs.", "Write unit tests (Jest)."] },
      { kind: "heading", text: "Nice to have" },
      { kind: "list", ordered: false, items: ["TypeScript, Next.js.", "Docker"] },
      { kind: "heading", text: "Steps:" },
      { kind: "list", ordered: true, items: ["Apply online", "Take the assessment"] },
    ]);
  });

  it("keeps every word of the employer's text, in order", () => {
    const blocks = parseJobDescription(SAMPLE);
    const rendered = blocks.flatMap((b) => (b.kind === "list" ? b.items : [b.text])).join("\n");
    expect(words(rendered)).toEqual(words(SAMPLE));
  });

  it("leaves a plain one-paragraph description as a single paragraph", () => {
    expect(parseJobDescription("Own the recruitment platform's backend.")).toEqual([
      { kind: "paragraph", text: "Own the recruitment platform's backend." },
    ]);
  });

  it("emphasises the opening statement, section headings and leading labels — and nothing else", () => {
    const jd = `Apply only if you meet all the requirements and accept all the terms.

Eligibility
Graduates in Electronics or Computer Science.
Benefits
Health insurance for the employee.

Interview Process: Two technical rounds and an HR discussion.
- Bond: 2 years from the joining date.
- Laptop provided`;
    const blocks = parseJobDescription(jd);
    expect(blocks).toEqual([
      { kind: "paragraph", text: "Apply only if you meet all the requirements and accept all the terms.", lead: true },
      { kind: "heading", text: "Eligibility" },
      { kind: "paragraph", text: "Graduates in Electronics or Computer Science." },
      { kind: "heading", text: "Benefits" },
      { kind: "paragraph", text: "Health insurance for the employee." },
      { kind: "paragraph", text: "Interview Process: Two technical rounds and an HR discussion." },
      { kind: "list", ordered: false, items: ["Bond: 2 years from the joining date.", "Laptop provided"] },
    ]);
    const rendered = blocks.flatMap((b) => (b.kind === "list" ? b.items : [b.text])).join("\n");
    expect(words(rendered)).toEqual(words(jd));
  });

  it("splits a leading label from its text without changing a character", () => {
    expect(splitLabel("Interview Process: Two technical rounds.")).toEqual({
      label: "Interview Process:",
      rest: " Two technical rounds.",
    });
    expect(splitLabel("Bond: 2 years")).toEqual({ label: "Bond:", rest: " 2 years" });
    // not a label: too long, a URL, or no text after the colon
    expect(splitLabel("This is a very long sentence that happens to contain a colon: here")).toBeNull();
    expect(splitLabel("https://zodiacpatent.com")).toBeNull();
    expect(splitLabel("Benefits:")).toBeNull();
  });

  it("keeps a leading 'Job Description' line plain and bolds the statement after it", () => {
    const jd = `Job Description
Apply only if you meet all the requirements and accept all the terms.
We are hiring Patent Engineers for our drafting team.

Requirements
- B.E./B.Tech in Electronics.

Interview Process: Written test and interview.`;
    const blocks = parseJobDescription(jd);
    expect(blocks).toEqual([
      { kind: "paragraph", text: "Job Description" },
      { kind: "paragraph", text: "Apply only if you meet all the requirements and accept all the terms.", lead: true },
      { kind: "paragraph", text: "We are hiring Patent Engineers for our drafting team." },
      { kind: "heading", text: "Requirements" },
      { kind: "list", ordered: false, items: ["B.E./B.Tech in Electronics."] },
      { kind: "paragraph", text: "Interview Process: Written test and interview." },
    ]);
    const rendered = blocks.flatMap((b) => (b.kind === "list" ? b.items : [b.text])).join("\n");
    expect(words(rendered)).toEqual(words(jd));
  });

  it("handles the title on its own block too ('Job Description:' then a blank line)", () => {
    const blocks = parseJobDescription(
      "Job Description:\n\nApply only if you meet all the requirements and accept all the terms.\n\nBenefits\n- Insurance.",
    );
    expect(blocks.slice(0, 3)).toEqual([
      { kind: "paragraph", text: "Job Description:" },
      { kind: "paragraph", text: "Apply only if you meet all the requirements and accept all the terms.", lead: true },
      { kind: "heading", text: "Benefits" },
    ]);
    expect(blocks.some((b) => b.kind === "heading" && /job description/i.test(b.text))).toBe(false);
  });

  it("never makes a single-sentence description entirely bold", () => {
    expect(parseJobDescription("Own core services powering the platform.")).toEqual([
      { kind: "paragraph", text: "Own core services powering the platform." },
    ]);
  });

  it("never turns a sentence into a heading", () => {
    const blocks = parseJobDescription("This role is remote.\nYou will report to the CTO.");
    expect(blocks).toEqual([{ kind: "paragraph", text: "This role is remote.\nYou will report to the CTO." }]);
  });
});
