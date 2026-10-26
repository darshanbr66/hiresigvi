import { describe, expect, it } from "vitest";
import { friendlyMessage, humanizeField, humanizeIssue, NETWORK_MESSAGE, SERVER_MESSAGE } from "./apiErrors";

describe("friendlyMessage", () => {
  it("keeps the backend's own business messages", () => {
    expect(
      friendlyMessage({ status: 409, code: "program_test_pass_required", message: "A passing result is required." }),
    ).toBe("A passing result is required.");
  });

  it("uses the product's wording for specific codes", () => {
    expect(
      friendlyMessage({ status: 409, code: "user_active", message: "Only a deactivated account can be deleted." }),
    ).toBe("Deactivate this account before deleting it.");
  });

  it("never shows framework defaults or server failures", () => {
    expect(friendlyMessage({ status: 404, code: "http_error", message: "Not Found" })).toMatch(/couldn't find/);
    expect(friendlyMessage({ status: 500, code: "internal_error", message: "An unexpected error occurred." })).toBe(
      SERVER_MESSAGE,
    );
    expect(friendlyMessage({ status: 502, code: "unknown_error", message: "" })).toBe(SERVER_MESSAGE);
    expect(friendlyMessage({ status: 0, code: "network_error" })).toBe(NETWORK_MESSAGE);
  });

  it("hides anything that looks like internal detail", () => {
    const leaked = 'sqlalchemy.exc.IntegrityError: duplicate key value violates unique constraint "x"';
    expect(friendlyMessage({ status: 409, code: "conflict", message: leaked })).not.toMatch(/sqlalchemy|constraint/);
    expect(friendlyMessage({ status: 400, code: "bad", message: "Traceback (most recent call last)" })).not.toMatch(
      /Traceback/,
    );
  });

  it("distinguishes a failed sign-in from a lost session", () => {
    expect(friendlyMessage({ status: 401, code: "unauthorized", message: "Invalid email or password." })).toBe(
      "Invalid email or password.",
    );
    expect(friendlyMessage({ status: 401, code: "unauthorized", message: "Invalid or expired token." })).toMatch(
      /session has expired/,
    );
  });

  it("gives permission, rate-limit and file-size failures their own wording", () => {
    expect(friendlyMessage({ status: 403, code: "http_error", message: "Forbidden" })).toMatch(/permission/);
    expect(friendlyMessage({ status: 403, code: "forbidden", message: "Only an Org Admin can do that." })).toBe(
      "Only an Org Admin can do that.",
    );
    expect(friendlyMessage({ status: 429, code: "rate_limited", message: "" })).toMatch(/Too many attempts/);
    expect(friendlyMessage({ status: 413, code: "http_error", message: "Payload Too Large" })).toMatch(/too large/);
  });

  it("turns validation details into plain, field-specific text", () => {
    const message = friendlyMessage({
      status: 422,
      code: "validation_error",
      message: "questions[2].options[1].label: String should have at least 1 character",
      details: [{ field: "questions[2].options[1].label", message: "String should have at least 1 character" }],
    });
    expect(message).toBe("Question 3, option 2, label: This field is required.");

    const several = friendlyMessage({
      status: 422,
      code: "validation_error",
      message: "x",
      details: [
        { field: "email", message: "value is not a valid email address: An email address must have an @-sign." },
        { field: "full_name", message: "Field required" },
      ],
    });
    expect(several).toBe("Please check the form. Email: Enter a valid email address. (and 1 more)");
  });

  it("keeps an authored validation message that has no field details", () => {
    expect(friendlyMessage({ status: 422, code: "validation_error", message: "Bad range." })).toBe("Bad range.");
  });
});

describe("humanizers", () => {
  it("names fields the way a person would", () => {
    expect(humanizeField("date_of_birth")).toBe("Date of birth");
    expect(humanizeField("items[0].content.points")).toBe("Item 1, content, points");
    expect(humanizeField("(request)")).toBe("");
  });

  it("strips Pydantic prefixes", () => {
    expect(humanizeIssue("Value error, A topic needs a subject.")).toBe("A topic needs a subject.");
    expect(humanizeIssue("String should have at most 150 characters")).toBe("Use at most 150 characters.");
  });

  it("explains numeric bounds instead of the generic allowed-values text", () => {
    expect(humanizeIssue("Input should be greater than or equal to 1")).toBe("Enter 1 or more.");
    expect(humanizeIssue("Input should be less than or equal to 1000")).toBe("Enter 1000 or less.");
    expect(humanizeIssue("Input should be greater than 0")).toBe("Enter a number greater than 0.");
    expect(humanizeIssue("Input should be less than 5")).toBe("Enter a number less than 5.");
    expect(humanizeIssue("Input should be 'MCQ_SINGLE' or 'MCQ_MULTI'")).toBe("Choose one of the allowed values.");
  });
});
