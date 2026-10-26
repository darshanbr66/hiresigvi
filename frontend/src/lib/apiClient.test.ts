import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiClient } from "./apiClient";

function stubFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("apiClient.getPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns the page and the total from X-Total-Count, and authenticates", async () => {
    const fetchMock = stubFetch(
      new Response(JSON.stringify([{ id: "a" }, { id: "b" }]), {
        status: 200,
        headers: { "X-Total-Count": "137" },
      }),
    );

    const page = await apiClient.getPage<{ id: string }>("/api/v1/x?limit=2", "tok");

    expect(page).toEqual({ items: [{ id: "a" }, { id: "b" }], total: 137 });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(init.credentials).toBe("include");
  });

  it("falls back to the page size when the header is missing or garbled", async () => {
    stubFetch(new Response(JSON.stringify([1, 2, 3]), { status: 200 }));
    expect((await apiClient.getPage("/x", "t")).total).toBe(3);

    stubFetch(new Response(JSON.stringify([1, 2]), { status: 200, headers: { "X-Total-Count": "lots" } }));
    expect((await apiClient.getPage("/x", "t")).total).toBe(2);
  });

  it("reports the server's error like every other call", async () => {
    const body = { error: { code: "validation_error", message: "Bad range.", request_id: null } };
    stubFetch(new Response(JSON.stringify(body), { status: 422 }));

    const failure = apiClient.getPage("/x", "t");
    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({
      status: 422,
      code: "validation_error",
      message: "Bad range.",
    });
  });
});

describe("apiClient error normalization", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reports a failure to connect as a friendly network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const failure = apiClient.get("/x", "t");
    await expect(failure).rejects.toMatchObject({ status: 0, code: "network_error", kind: "network" });
    await expect(failure).rejects.not.toMatchObject({ message: "Failed to fetch" });
  });

  it("never shows a non-JSON error page", async () => {
    stubFetch(new Response("<html>502 Bad Gateway</html>", { status: 502 }));
    await expect(apiClient.post("/x", {}, "t")).rejects.toMatchObject({
      status: 502,
      message: "We couldn't complete this request. Please try again.",
    });
  });

  it("keeps the server's text and request id for diagnostics", async () => {
    const body = { error: { code: "http_error", message: "Not Found", request_id: "req-1" } };
    stubFetch(new Response(JSON.stringify(body), { status: 404 }));
    const error = (await apiClient.get("/x", "t").catch((e: unknown) => e)) as ApiError;
    expect(error.serverMessage).toBe("Not Found");
    expect(error.requestId).toBe("req-1");
    expect(error.message).not.toBe("Not Found");
  });

  it("exposes field issues in plain wording for forms", async () => {
    const body = {
      error: {
        code: "validation_error",
        message: "email: Field required",
        request_id: null,
        details: [{ field: "email", message: "Field required" }],
      },
    };
    stubFetch(new Response(JSON.stringify(body), { status: 422 }));
    const error = (await apiClient.postForm("/x", new FormData()).catch((e: unknown) => e)) as ApiError;
    expect(error.fieldIssues).toEqual([{ field: "email", message: "This field is required." }]);
    expect(error.message).toBe("Email: This field is required.");
  });
});
