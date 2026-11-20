import { describe, expect, it } from "vitest";
import { displayNameFromSlug } from "./orgName";

describe("displayNameFromSlug", () => {
  it("capitalises a single word", () => {
    expect(displayNameFromSlug("zodiac")).toBe("Zodiac");
  });
  it("splits on hyphens and underscores", () => {
    expect(displayNameFromSlug("acme-corp")).toBe("Acme Corp");
    expect(displayNameFromSlug("north_star-labs")).toBe("North Star Labs");
  });
  it("falls back to a neutral word for an empty slug", () => {
    expect(displayNameFromSlug("")).toBe("Careers");
  });
});
