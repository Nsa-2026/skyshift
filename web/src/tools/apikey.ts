// Optional API keys (contracts/api.md v3.14): a small form on Tools › API that asks the server for a
// key and shows it once. The key is never stored by the page; the server keeps only its hash.
import { ApiFailure } from "../api/client";

export interface NewApiKey {
  key: string;
  id: string;
  label: string | null;
  created: string;
  header: string;
}

export const LABEL_MAX = 60;

/** POST /api/keys. Throws ApiFailure with the contract's error code and message. */
export async function createApiKey(base: string, label: string, fetchFn: typeof fetch = fetch): Promise<NewApiKey> {
  const url = new URL("api/keys", base).href;
  const trimmed = label.trim();
  const res = await fetchFn(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(trimmed ? { label: trimmed } : {}),
  });
  if (!res.ok) {
    let code = "internal";
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.error) ({ code, message } = body.error);
    } catch {
      /* not JSON */
    }
    throw new ApiFailure(message, res.status, code);
  }
  return (await res.json()) as NewApiKey;
}

/** The text shown when a key can't be made. */
export function keyErrorText(e: unknown): string {
  if (e instanceof ApiFailure) {
    if (e.code === "rate_limited") return "Too many new keys from this address in the last hour. Try again later.";
    if (e.code === "bad_request") return e.message;
    return `The API didn't make a key (${e.message}). Try again in a minute.`;
  }
  return "Couldn't reach the API. Check your connection and try again.";
}

export function keyUseExamples(key: NewApiKey, base: string): { curl: string; python: string } {
  const url = new URL("api/resolve?q=Barnard%27s%20Star", base).href;
  const h = key.header || "X-API-Key";
  return {
    curl: `curl -H "${h}: ${key.key}" "${url}"`,
    python: `import requests\n\nheaders = {"${h}": "${key.key}"}\nr = requests.get("${url}", headers=headers)\nprint(r.json())`,
  };
}

export function apiKeyFormHtml(): string {
  return `<form class="tl-key-form" novalidate>
      <label class="tl-label" for="tl-key-label">Name (optional)</label>
      <div class="tl-key-row">
        <input id="tl-key-label" class="tl-input" type="text" maxlength="${LABEL_MAX}" autocomplete="off"
          spellcheck="false" placeholder="e.g. my survey script" />
        <button type="submit" class="btn btn-primary">Make a key</button>
      </div>
    </form>
    <p class="tl-status tl-key-status" role="status" aria-live="polite"></p>
    <div class="tl-key-out" hidden></div>`;
}
