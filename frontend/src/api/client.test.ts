import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, readCookie, setUnauthorizedHandler } from "./client";

function resp(status: number, body: unknown = null): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as Response;
}

function lastInit(mock: ReturnType<typeof vi.fn>, i = 0): RequestInit {
  return mock.mock.calls[i][1] as RequestInit;
}

describe("apiFetch", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    document.cookie = "csrftoken=tok-123";
    setUnauthorizedHandler(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends credentials and omits the CSRF header on GET", async () => {
    fetchMock.mockResolvedValueOnce(resp(200, [{ id: "r1" }]));

    const data = await apiFetch("/runs");

    expect(data).toEqual([{ id: "r1" }]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/runs$/);
    expect(init.credentials).toBe("include");
    expect((init.headers as Headers).has("X-CSRF-Token")).toBe(false);
  });

  it("attaches the CSRF token from the cookie on writes", async () => {
    fetchMock.mockResolvedValueOnce(resp(202, { id: "r2", status: "pending" }));

    await apiFetch("/runs", { method: "POST" });

    expect((lastInit(fetchMock).headers as Headers).get("X-CSRF-Token")).toBe("tok-123");
  });

  it("serializes a JSON body with Content-Type", async () => {
    fetchMock.mockResolvedValueOnce(resp(201, { id: "s1" }));

    await apiFetch("/schedules", { method: "POST", body: { cron: "0 6 * * *" } });

    const init = lastInit(fetchMock);
    expect((init.headers as Headers).get("Content-Type")).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ cron: "0 6 * * *" }));
  });

  it("calls the unauthorized handler and throws on 401", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    fetchMock.mockResolvedValueOnce(resp(401, { detail: "nope" }));

    await expect(apiFetch("/runs")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("refreshes via /auth/me and retries a write once on 403", async () => {
    fetchMock
      .mockResolvedValueOnce(resp(403, { detail: "csrf" })) // original write rejected
      .mockResolvedValueOnce(resp(200, { username: "admin" })) // /auth/me refresh
      .mockResolvedValueOnce(resp(200, { id: "s1" })); // retried write succeeds

    const data = await apiFetch("/schedules", { method: "POST", body: { cron: "0 6 * * *" } });

    expect(data).toEqual({ id: "s1" });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(String(fetchMock.mock.calls[1][0])).toMatch(/\/auth\/me$/);
  });

  it("gives up after one retry if the write is still 403", async () => {
    fetchMock
      .mockResolvedValueOnce(resp(403, { detail: "csrf" }))
      .mockResolvedValueOnce(resp(200, { username: "admin" }))
      .mockResolvedValueOnce(resp(403, { detail: "csrf" }));

    await expect(
      apiFetch("/schedules", { method: "POST", body: {} }),
    ).rejects.toMatchObject({ status: 403 });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("returns undefined for 204", async () => {
    fetchMock.mockResolvedValueOnce(resp(204));
    expect(await apiFetch("/schedules/s1", { method: "DELETE" })).toBeUndefined();
  });

  it("throws ApiError with the server detail on error responses", async () => {
    fetchMock.mockResolvedValueOnce(resp(400, { detail: "bad cron" }));
    await expect(
      apiFetch("/schedules", { method: "POST", body: {} }),
    ).rejects.toMatchObject({ status: 400, detail: "bad cron" });
  });

  it("readCookie reads a named cookie value", () => {
    document.cookie = "csrftoken=xyz789";
    expect(readCookie("csrftoken")).toBe("xyz789");
  });
});
