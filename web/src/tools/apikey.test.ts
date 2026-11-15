import { describe, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/api/keys_create.json";
import { ApiFailure } from "../api/client";
import { apiKeyFormHtml, createApiKey, keyErrorText, keyUseExamples } from "./apikey";

const BASE = "https://skyshift.example/";

function respond(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

describe("createApiKey", () => {
  it("posts the trimmed label to /api/keys and returns the contract's shape", async () => {
    const f = respond(201, fixture);
    const k = await createApiKey(BASE, "  my script ", f as unknown as typeof fetch);
    expect(f).toHaveBeenCalledWith("https://skyshift.example/api/keys", expect.objectContaining({ method: "POST" }));
    const init = (f.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(init.body as string)).toEqual({ label: "my script" });
    expect(k.key).toMatch(/^sks_[A-Za-z0-9_-]{32}$/);
    expect(k.header).toBe("X-API-Key");
  });

  it("sends an empty object without a label", async () => {
    const f = respond(201, fixture);
    await createApiKey(BASE, "   ", f as unknown as typeof fetch);
    const init = (f.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(JSON.parse(init.body as string)).toEqual({});
  });

  it("throws the contract's error code", async () => {
    const f = respond(429, { error: { code: "rate_limited", message: "At most 10" } });
    const err = await createApiKey(BASE, "", f as unknown as typeof fetch).catch((e) => e);
    expect(err).toBeInstanceOf(ApiFailure);
    expect(err.code).toBe("rate_limited");
    expect(keyErrorText(err)).toMatch(/Too many new keys/);
  });
});

describe("helpers", () => {
  it("builds examples that send the key in the header", () => {
    const ex = keyUseExamples(fixture, BASE);
    expect(ex.curl).toContain(`-H "X-API-Key: ${fixture.key}"`);
    expect(ex.curl).toContain("https://skyshift.example/api/resolve");
    expect(ex.python).toContain(`{"X-API-Key": "${fixture.key}"}`);
  });

  it("explains failures in words", () => {
    expect(keyErrorText(new ApiFailure("label must be at most 60 characters", 400, "bad_request"))).toBe(
      "label must be at most 60 characters",
    );
    expect(keyErrorText(new TypeError("fetch failed"))).toMatch(/Couldn't reach/);
  });

  it("has a labelled input with the contract's length", () => {
    const html = apiKeyFormHtml();
    expect(html).toContain('for="tl-key-label"');
    expect(html).toContain('maxlength="60"');
  });
});
