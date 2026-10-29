import { describe, expect, it } from "vitest";
import {
  APPLICATION_STATUSES,
} from "../../types/recruitment";
import {
  PIPELINE_STAGE_LABELS,
  humanizeStatus,
  pipelineColor,
  pipelineStage,
  statusTone,
  toneBadgeClass,
  toneColor,
} from "./statusTone";

describe("statusTone", () => {
  it("maps every real application status to a pipeline stage", () => {
    for (const status of APPLICATION_STATUSES) {
      expect(pipelineStage(status), status).not.toBeNull();
    }
    expect(pipelineStage("APPLIED")).toBe("applied");
    expect(pipelineStage("UNDER_REVIEW")).toBe("screening");
    expect(pipelineStage("SCREENING")).toBe("screening");
    expect(pipelineStage("ASSESSMENT_STARTED")).toBe("assessment");
    expect(pipelineStage("SHORTLISTED")).toBe("interview");
    expect(pipelineStage("INTERVIEW")).toBe("interview");
    expect(pipelineStage("SELECTED")).toBe("selected");
    expect(pipelineStage("HIRED")).toBe("hired");
    expect(pipelineStage("REJECTED")).toBe("rejected");
    // advisory, overridable by HR — "on hold", not rejected
    expect(pipelineStage("AI_SCREENED_OUT")).toBe("hold");
  });

  it("gives each stage its own colour token, with a neutral fallback", () => {
    expect(pipelineColor("SCREENING")).toBe("var(--pipeline-screening)");
    expect(pipelineColor("HIRED")).toBe("var(--pipeline-hired)");
    expect(pipelineColor("SOMETHING_NEW")).toBe("var(--pipeline-applied)");
    expect(PIPELINE_STAGE_LABELS.hold).toBe("On hold");
  });

  it("gives every pipeline stage a stable, meaningful badge tone", () => {
    expect(statusTone("APPLIED")).toBe("neutral");
    expect(statusTone("SCREENING")).toBe("warn");
    expect(statusTone("ASSESSMENT_INVITED")).toBe("info");
    expect(statusTone("INTERVIEW")).toBe("interview");
    expect(statusTone("SELECTED")).toBe("success");
    expect(statusTone("HIRED")).toBe("success");
    expect(statusTone("REJECTED")).toBe("danger");
  });

  it("falls back to neutral for an unknown stage rather than guessing", () => {
    expect(statusTone("SOMETHING_NEW")).toBe("neutral");
    expect(pipelineStage("SOMETHING_NEW")).toBeNull();
  });

  it("maps tones to badge classes and theme-aware colours", () => {
    expect(toneBadgeClass("danger")).toBe("badge badge-danger");
    expect(toneBadgeClass(statusTone("INTERVIEW"))).toBe("badge badge-interview");
    expect(toneBadgeClass(statusTone("HIRED"))).toBe("badge badge-active");
    expect(toneColor("success")).toMatch(/^var\(--/);
  });

  it("humanizes stage names", () => {
    expect(humanizeStatus("UNDER_REVIEW")).toBe("Under review");
    expect(humanizeStatus("HIRED")).toBe("Hired");
  });
});
