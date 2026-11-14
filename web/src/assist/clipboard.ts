// Copy and download, with fallbacks. Nothing leaves the browser.

/** Copy text: the async clipboard first, then a hidden textarea with execCommand. True on success. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* blocked (http, iframe, permissions): try the fallback */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    const active = document.activeElement as HTMLElement | null;
    ta.focus();
    ta.select();
    let ok = false;
    try {
      ok = typeof document.execCommand === "function" && document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    active?.focus?.();
    return ok;
  } catch {
    return false;
  }
}

/** Save text as a file through a temporary link. False when the browser can't. */
export function downloadText(filename: string, text: string, type = "text/plain;charset=utf-8"): boolean {
  try {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.rel = "noopener";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    return true;
  } catch {
    return false;
  }
}

/** A file name from a candidate id: letters, digits, dot, dash, underscore only. */
export function safeFileName(s: string): string {
  return s.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 80) || "skyshift";
}
