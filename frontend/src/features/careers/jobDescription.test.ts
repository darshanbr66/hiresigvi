import { describe, expect, it } from "vitest";
import {
  descriptionNoticeKeys,
  parseDetailValue,
  parseJobDescription,
  splitInlineItems,
  splitLabel,
} from "./jobDescription";
import type { DescriptionBlock } from "./jobDescription";

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

  it("shows the instruction as a notice and emphasises section headings and leading labels — nothing else", () => {
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
      { kind: "notice", text: "Apply only if you meet all the requirements and accept all the terms." },
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

  it("keeps a leading 'Job Description' line plain and makes the instruction after it a notice", () => {
    const jd = `Job Description
Apply only if you meet all the requirements and accept all the terms.
We are hiring Patent Engineers for our drafting team.

Requirements
- B.E./B.Tech in Electronics.

Interview Process: Written test and interview.`;
    const blocks = parseJobDescription(jd);
    expect(blocks).toEqual([
      { kind: "paragraph", text: "Job Description" },
      { kind: "notice", text: "Apply only if you meet all the requirements and accept all the terms." },
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
      { kind: "notice", text: "Apply only if you meet all the requirements and accept all the terms." },
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


/** Every rendered string of a block list, in order. */
function texts(blocks: DescriptionBlock[]): string[] {
  return blocks.flatMap((b) => (b.kind === "list" ? b.items : [b.text]));
}

const INSTRUCTION = "Apply only if you meet the requirements and are comfortable with the stated employment terms.";

describe("parseJobDescription — ranges and hyphens are never list separators", () => {
  it("Test 1: keeps a salary range in one bullet, with or without a list marker", () => {
    const jd = [
      "Compensation & Benefits",
      "- Annual salary: ₹6,00,000 - ₹8,00,000 per annum.",
      "- Health insurance.",
      "- Paid time off.",
      "- Provident Fund (PF).",
    ].join("\n");
    expect(parseJobDescription(jd)).toEqual([
      { kind: "heading", text: "Compensation & Benefits" },
      {
        kind: "list",
        ordered: false,
        items: ["Annual salary: ₹6,00,000 - ₹8,00,000 per annum.", "Health insurance.", "Paid time off.", "Provident Fund (PF)."],
      },
    ]);
    for (const line of [
      "- Annual salary: ₹6,00,000 – ₹8,00,000 per annum.",
      "- Annual salary: ₹6,00,000 - ₹8,00,000",
      "- Salary: Rs. 6,00,000 - Rs. 8,00,000 per annum",
      "- CTC: 6 - 8 LPA",
    ]) {
      expect(parseJobDescription(`Compensation\n${line}`)[1]).toEqual({
        kind: "list",
        ordered: false,
        items: [line.slice(2)],
      });
    }
  });

  it("rejoins a salary range broken onto its own dash line (the reported defect)", () => {
    // What split the salary into "Annual salary: ₹6,00,000" and "₹8,00,000.":
    // the range's second half starts its own line with the range's dash,
    // which a line-start list rule read as a new bullet.
    const jd = [
      "Compensation & Benefits",
      "- Annual salary: ₹6,00,000",
      "– ₹8,00,000.",
      "- Health insurance.",
      "- Paid time off.",
      "- Provident Fund (PF).",
    ].join("\n");
    expect(parseJobDescription(jd)).toEqual([
      { kind: "heading", text: "Compensation & Benefits" },
      {
        kind: "list",
        ordered: false,
        items: ["Annual salary: ₹6,00,000 – ₹8,00,000.", "Health insurance.", "Paid time off.", "Provident Fund (PF)."],
      },
    ]);
    // the same in a paragraph, with a hyphen, a currency word and a period
    expect(parseJobDescription("Annual salary: Rs. 6,00,000\n- Rs. 8,00,000 per annum.")).toEqual([
      { kind: "paragraph", text: "Annual salary: Rs. 6,00,000 - Rs. 8,00,000 per annum." },
    ]);
    expect(parseJobDescription("Benefits\n- Shift: 9:30 AM\n- 6:30 PM\n- Paid time off.")[1]).toEqual({
      kind: "list",
      ordered: false,
      items: ["Shift: 9:30 AM - 6:30 PM", "Paid time off."],
    });
  });

  it("keeps real lists of values as lists: the range join needs strong evidence", () => {
    // bare values, no label before them
    expect(parseJobDescription("Eligible batches\n- 2023\n- 2024\n- 2025")[1]).toEqual({
      kind: "list",
      ordered: false,
      items: ["2023", "2024", "2025"],
    });
    // the previous item ended a sentence
    expect(parseJobDescription("Benefits\n- Joining bonus: ₹50,000.\n- 12 paid holidays")[1]).toEqual({
      kind: "list",
      ordered: false,
      items: ["Joining bonus: ₹50,000.", "12 paid holidays"],
    });
    // the dash line is a full item of its own, not a range end
    expect(parseJobDescription("Requirements\n- Minimum aggregate 60\n- Strong written English")[1]).toEqual({
      kind: "list",
      ordered: false,
      items: ["Minimum aggregate 60", "Strong written English"],
    });
    // a bullet marker other than a dash is always a list item
    expect(parseJobDescription("Pay\n- Stipend: ₹20,000\n• ₹25,000 after review")[1]).toEqual({
      kind: "list",
      ordered: false,
      items: ["Stipend: ₹20,000", "₹25,000 after review"],
    });
  });

  it("Tests 2–5: experience, date, schedule and hyphenated values stay whole", () => {
    const items = [
      "Experience: 1–3 years.",
      "Experience: 2 - 5 years.",
      "Employment period: 2023 - 2025.",
      "Working days: Mon - Fri, 9:30 AM - 6:30 PM.",
      "Employment Type: Full-time.",
      "Work Mode: On-site.",
      "Work-from-home available - part-time roles too.",
    ];
    const [heading, list] = parseJobDescription(["Role Details", ...items.map((i) => `- ${i}`)].join("\n"));
    expect(heading).toEqual({ kind: "heading", text: "Role Details" });
    expect(list).toEqual({ kind: "list", ordered: false, items });
  });

  it("keeps an unmarked label/value line as text, never a heading", () => {
    const blocks = parseJobDescription("Annual salary: ₹6,00,000 - ₹8,00,000 per annum\nHealth insurance");
    expect(blocks).toEqual([
      { kind: "paragraph", text: "Annual salary: ₹6,00,000 - ₹8,00,000 per annum\nHealth insurance" },
    ]);
  });

  it("does not treat a mid-line or line-leading range as a list marker", () => {
    expect(parseJobDescription("Working hours\n9:30 AM - 6:30 PM\n2023 - 2025 batch only")).toEqual([
      { kind: "heading", text: "Working hours" },
      { kind: "paragraph", text: "9:30 AM - 6:30 PM\n2023 - 2025 batch only" },
    ]);
  });
});

describe("parseJobDescription — lists", () => {
  it("Test 7: one bullet per marked line", () => {
    expect(parseJobDescription("Benefits\n- Health insurance.\n- Paid time off.\n- Provident Fund (PF).")).toEqual([
      { kind: "heading", text: "Benefits" },
      { kind: "list", ordered: false, items: ["Health insurance.", "Paid time off.", "Provident Fund (PF)."] },
    ]);
  });

  it("nests deeper-indented items under their parent", () => {
    const jd = "Requirements\n- Education\n  - B.Tech / B.E. in CSE\n  - M.Tech\n- 60% throughout";
    expect(parseJobDescription(jd)[1]).toEqual({
      kind: "list",
      ordered: false,
      items: ["Education", "B.Tech / B.E. in CSE", "M.Tech", "60% throughout"],
      depths: [0, 1, 1, 0],
    });
  });

  it("joins an indented continuation line to the item above it", () => {
    const jd = "Hiring Process\n1. Written test covering\n   aptitude and domain topics.\n2. Interview";
    expect(parseJobDescription(jd)[1]).toEqual({
      kind: "list",
      ordered: true,
      items: ["Written test covering\naptitude and domain topics.", "Interview"],
    });
  });
});

describe("parseJobDescription — application instruction", () => {
  it("Test 9: the instruction becomes one notice with its exact wording", () => {
    const blocks = parseJobDescription(`${INSTRUCTION}\n\nResponsibilities\n- Draft patents.`);
    expect(blocks[0]).toEqual({ kind: "notice", text: INSTRUCTION });
    expect(blocks.filter((b) => b.kind === "notice")).toHaveLength(1);
    expect(blocks.some((b) => b.kind === "paragraph" && b.lead)).toBe(false);
  });

  it("lifts the instruction out of a list in place, keeping the rest in order", () => {
    const blocks = parseJobDescription(`Terms\n- Bond: 2 years.\n- ${INSTRUCTION}\n- Notice period: 1 month.`);
    expect(blocks).toEqual([
      { kind: "heading", text: "Terms" },
      { kind: "list", ordered: false, items: ["Bond: 2 years."] },
      { kind: "notice", text: INSTRUCTION },
      { kind: "list", ordered: false, items: ["Notice period: 1 month."] },
    ]);
  });

  it("shows a repeated instruction once", () => {
    const jd = `${INSTRUCTION}\n\nEligibility\n- B.Tech\n\nNote\n${INSTRUCTION}`;
    const notices = parseJobDescription(jd).filter((b) => b.kind === "notice");
    expect(notices).toEqual([{ kind: "notice", text: INSTRUCTION }]);
    expect(descriptionNoticeKeys(jd).size).toBe(1);
  });

  it("still bolds an ordinary opening statement when there is no instruction", () => {
    const blocks = parseJobDescription("We are hiring patent engineers.\n\nBenefits\n- Insurance.");
    expect(blocks[0]).toEqual({ kind: "paragraph", text: "We are hiring patent engineers.", lead: true });
  });
});

describe("parseJobDescription — Test 10: a complex description", () => {
  const JD = [
    "Job Description",
    INSTRUCTION,
    "Zodiac Patent is hiring Patent Analysts for its Bengaluru office.",
    "",
    "Role Details",
    "- Employment Type: Full-time.",
    "- Work Mode: On-site.",
    "- Working days: Mon - Fri, 9:30 AM - 6:30 PM.",
    "",
    "Eligibility",
    "- B.Tech / B.E. (2023 - 2025 batch)",
    "  - CSE, ECE or EEE",
    "  - 60% throughout",
    "- Experience: 0–2 years.",
    "",
    "Compensation & Benefits",
    "- Annual salary: ₹6,00,000 - ₹8,00,000 per annum.",
    "- Health insurance.",
    "- Paid time off.",
    "- Provident Fund (PF).",
    "",
    "Work Location",
    "Bengaluru (on-site), work-from-home not available.",
    "",
    "Hiring Process",
    "1. Written test",
    "2. Technical interview -",
    "   with the drafting team",
    "3. HR discussion",
    "",
    "Why join us",
    "A well-rounded, fast-growing team.",
  ].join("\n");

  it("structures every section", () => {
    expect(parseJobDescription(JD)).toEqual([
      { kind: "paragraph", text: "Job Description" },
      { kind: "notice", text: INSTRUCTION },
      { kind: "paragraph", text: "Zodiac Patent is hiring Patent Analysts for its Bengaluru office." },
      { kind: "heading", text: "Role Details" },
      {
        kind: "list",
        ordered: false,
        items: ["Employment Type: Full-time.", "Work Mode: On-site.", "Working days: Mon - Fri, 9:30 AM - 6:30 PM."],
      },
      { kind: "heading", text: "Eligibility" },
      {
        kind: "list",
        ordered: false,
        items: ["B.Tech / B.E. (2023 - 2025 batch)", "CSE, ECE or EEE", "60% throughout", "Experience: 0–2 years."],
        depths: [0, 1, 1, 0],
      },
      { kind: "heading", text: "Compensation & Benefits" },
      {
        kind: "list",
        ordered: false,
        items: ["Annual salary: ₹6,00,000 - ₹8,00,000 per annum.", "Health insurance.", "Paid time off.", "Provident Fund (PF)."],
      },
      { kind: "heading", text: "Work Location" },
      { kind: "paragraph", text: "Bengaluru (on-site), work-from-home not available." },
      { kind: "heading", text: "Hiring Process" },
      {
        kind: "list",
        ordered: true,
        items: ["Written test", "Technical interview -\nwith the drafting team", "HR discussion"],
      },
      { kind: "heading", text: "Why join us" },
      { kind: "paragraph", text: "A well-rounded, fast-growing team." },
    ]);
  });

  it("keeps every word and every hyphen, in order", () => {
    expect(words(texts(parseJobDescription(JD)).join("\n"))).toEqual(words(JD));
  });
});

describe("splitInlineItems (Role-details list fields only)", () => {
  it("Test 6: splits three or more requirements typed on one line", () => {
    expect(splitInlineItems("B.Tech / B.E. - 2024 batch - No active backlogs")).toEqual([
      "B.Tech / B.E.",
      "2024 batch",
      "No active backlogs",
    ]);
    expect(splitInlineItems("Python • Django • React")).toEqual(["Python", "Django", "React"]);
  });

  it("never splits a range, a hyphenated word, a label/value pair or a two-part line", () => {
    for (const text of [
      "₹6,00,000 - ₹8,00,000 - negotiable",
      "Rs. 6,00,000 - Rs. 8,00,000 - CTC",
      "Graduated 2023 - 2025 - any stream",
      "Mon - Fri - 9:30 AM - 6:30 PM",
      "Jan - Mar - internship",
      "1 - 3 years - relevant",
      "Full-time, on-site role",
      "Bond: 2 years - extendable - by consent",
      "Bond - 2 years",
    ]) {
      expect(splitInlineItems(text)).toEqual([text]);
    }
  });
});

describe("parseDetailValue", () => {
  it("rejoins a range broken onto a dash line in a Role-details value", () => {
    expect(parseDetailValue("Annual salary: ₹6,00,000\n– ₹8,00,000.\nBond: 2 years.", { splitInline: true })).toEqual([
      { kind: "list", ordered: false, items: ["Annual salary: ₹6,00,000 – ₹8,00,000.", "Bond: 2 years."] },
    ]);
  });

  it("keeps a single plain line as a paragraph", () => {
    expect(parseDetailValue("Mon - Fri, 9:30 AM - 6:30 PM")).toEqual([
      { kind: "paragraph", text: "Mon - Fri, 9:30 AM - 6:30 PM" },
    ]);
    expect(parseDetailValue("1–3 years", { splitInline: true })).toEqual([{ kind: "paragraph", text: "1–3 years" }]);
    expect(parseDetailValue("Bond - 2 years", { splitInline: true })).toEqual([
      { kind: "paragraph", text: "Bond - 2 years" },
    ]);
  });

  it("makes each line a separate bullet, with or without markers", () => {
    expect(parseDetailValue("- B.Tech in CSE\n- 60% throughout\nNo active backlogs")).toEqual([
      { kind: "list", ordered: false, items: ["B.Tech in CSE", "60% throughout", "No active backlogs"] },
    ]);
  });

  it("Test 6: hyphen-separated eligibility requirements become bullets, ranges stay whole", () => {
    expect(parseDetailValue("B.Tech - 2024 batch - 60% throughout", { splitInline: true })).toEqual([
      { kind: "list", ordered: false, items: ["B.Tech", "2024 batch", "60% throughout"] },
    ]);
    expect(
      parseDetailValue("- B.Tech / B.E. (2023 - 2025 batch)\n- 60% throughout\n- Age: 21 - 26 years", { splitInline: true }),
    ).toEqual([
      { kind: "list", ordered: false, items: ["B.Tech / B.E. (2023 - 2025 batch)", "60% throughout", "Age: 21 - 26 years"] },
    ]);
    // without splitInline (e.g. Schedule) an unmarked line is left alone
    expect(parseDetailValue("Mon - Fri - rotational Saturdays")).toEqual([
      { kind: "paragraph", text: "Mon - Fri - rotational Saturdays" },
    ]);
  });

  it("turns a lone label line into a sub-heading over its items", () => {
    expect(parseDetailValue("Education:\n- B.Tech\nTerms:\n1. Bond: 2 years\n2. Notice: 1 month")).toEqual([
      { kind: "heading", text: "Education:" },
      { kind: "list", ordered: false, items: ["B.Tech"] },
      { kind: "heading", text: "Terms:" },
      { kind: "list", ordered: true, items: ["Bond: 2 years", "Notice: 1 month"] },
    ]);
  });

  it("shows the instruction as a notice, or not at all when the page already shows it", () => {
    const terms = `Bond: 2 years\n${INSTRUCTION}`;
    expect(parseDetailValue(terms, { splitInline: true })).toEqual([
      { kind: "list", ordered: false, items: ["Bond: 2 years"] },
      { kind: "notice", text: INSTRUCTION },
    ]);
    const shown = descriptionNoticeKeys(`${INSTRUCTION}\n\nBenefits\n- PF`);
    expect(parseDetailValue(terms, { splitInline: true, omitNotices: shown })).toEqual([
      { kind: "paragraph", text: "Bond: 2 years" },
    ]);
  });

  it("keeps every word, in order", () => {
    const source = "Eligibility:\n- B.Tech - 2024 batch\n• 60% throughout\nNo backlogs - no gaps - Indian citizens";
    const out = parseDetailValue(source, { splitInline: true })
      .flatMap((b) => (b.kind === "list" ? b.items : [b.text]))
      .join(" ")
      .split(/\s+/);
    // only the separators of the one split line are dropped
    expect(out).toEqual(words(source.replace("backlogs - no gaps - Indian", "backlogs no gaps Indian")));
  });
});
