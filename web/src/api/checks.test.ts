import { describe, expect, it } from "vitest";
import { LEVEL_TEXT, STATUS_TEXT, anyChecked, checkLine, checksOf, hasStrong, knownNameOf, lessLikely, statusOf } from "./checks";

const check = (level: string, code = level, text = `${code} measured`, look = "") => ({ code, level, text, look, values: null });

describe("status (contracts/api.md v3.23)", () => {
  it("reads open, known and flaw; anything else, or no field (older files), is open", () => {
    expect(statusOf({ status: "known" })).toBe("known");
    expect(statusOf({ status: "flaw" })).toBe("flaw");
    expect(statusOf({ status: "open" })).toBe("open");
    expect(statusOf({})).toBe("open");
    expect(statusOf({ status: "KNOWN" })).toBe("open");
    expect(statusOf(null)).toBe("open");
  });

  it("names the known body only when the status is known", () => {
    expect(knownNameOf({ status: "known", known_name: " 306P/LINEAR " })).toBe("306P/LINEAR");
    expect(knownNameOf({ status: "known", known_name: "" })).toBeNull();
    expect(knownNameOf({ status: "open", known_name: "Iapetus" })).toBeNull();
    expect(knownNameOf({})).toBeNull();
  });
});

describe("checks (contracts/api.md v3.23)", () => {
  it("orders strong, then warn, then ok, keeping the file's order within a level", () => {
    const c = { checks: [check("ok", "partner_seen"), check("warn", "fast"), check("strong", "one_detector"), check("warn", "edge")] };
    expect(checksOf(c).map((k) => k.code)).toEqual(["one_detector", "fast", "edge", "partner_seen"]);
  });

  it("reads older files and odd rows safely: no field is [], an unknown level is something to look at", () => {
    expect(checksOf({})).toEqual([]);
    expect(checksOf({ checks: "x" })).toEqual([]);
    const got = checksOf({ checks: [null, 3, { code: "new_code", level: "maybe", text: "Measured 4.2″", look: 7 }, { level: "ok" }] });
    expect(got).toEqual([{ code: "new_code", level: "warn", text: "Measured 4.2″", look: "", values: null }]);
    // A check with a code but no text still shows (its code), so nothing is silently dropped.
    expect(checksOf({ checks: [{ code: "edge", level: "warn" }] })[0].text).toBe("edge");
  });

  it("is less likely only when open with a strong check", () => {
    const strong = { checks: [check("strong")] };
    expect(hasStrong(strong)).toBe(true);
    expect(lessLikely(strong)).toBe(true);
    expect(lessLikely({ ...strong, status: "known" })).toBe(false);
    expect(lessLikely({ checks: [check("warn"), check("ok")] })).toBe(false);
    expect(lessLikely({})).toBe(false);
  });

  it("tells a v3.23 list from an older one", () => {
    expect(anyChecked([{}, { checks: [] }])).toBe(true);
    expect(anyChecked([{}, { status: "open" }])).toBe(true);
    expect(anyChecked([{}, {}])).toBe(false);
  });

  it("marks each level plainly and says a check in one line", () => {
    expect([LEVEL_TEXT.strong.label, LEVEL_TEXT.warn.label, LEVEL_TEXT.ok.label]).toEqual(["Sign against", "Can't rule out", "Sign for"]);
    expect(checkLine(checksOf({ checks: [check("strong", "one_detector", "D1 measured 76 µJy", "Hours, D1: the spot stays empty.")] })[0])).toBe(
      "Sign against: D1 measured 76 µJy. Look: Hours, D1: the spot stays empty.",
    );
    expect([STATUS_TEXT.known.tab, STATUS_TEXT.flaw.tab]).toEqual(["Known bodies", "Instrument flaws"]);
  });
});
