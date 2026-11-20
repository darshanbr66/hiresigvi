import { describe, expect, it } from "vitest";
import { greetingName } from "./greeting";

describe("greetingName", () => {
  it("greets an organization admin as Admin, whatever their account name is", () => {
    expect(greetingName({ full_name: "ZODIAC Admin", roles: ["ORG_ADMIN"] })).toBe("Admin");
  });

  it("greets other roles by their own first name", () => {
    expect(greetingName({ full_name: "Riya Recruiter", roles: ["RECRUITER"] })).toBe("Riya");
    expect(greetingName({ full_name: "  Hari   Manager ", roles: ["HIRING_MANAGER"] })).toBe("Hari");
  });

  it("falls back to a neutral greeting when there is no usable name", () => {
    expect(greetingName(null)).toBe("there");
    expect(greetingName({ full_name: "   ", roles: ["RECRUITER"] })).toBe("there");
  });
});
