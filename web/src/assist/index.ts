/**
 * The check assistant (web/DESIGN.md, "The check assistant"), used on the Candidate page (#/c/<id>)
 * and by the Tools page. Owner: src/assist. Other pages import from here only.
 *
 * ```ts
 * import { mountChecklist, mountWhatNext, toCSV, toVOTable, toJSON } from "../assist";
 *
 * const check = mountChecklist(el, candidate, { details, onVote: () => votePanel.focus(), myVote });
 * check.update(candidate, { details: true });      // e.g. when the header's Details switch flips
 * const next = mountWhatNext(el2, candidate, counts); // counts: Counts from getVoteStore().countsFor, or null
 * next.update(candidate, newCounts);                // after a vote (window "skyshift:voted" event)
 * check.destroy(); next.destroy();
 * ```
 *
 * Nothing here calls an external service or submits anything: drafts are copied or downloaded by
 * the person. Replies to the checklist's "Your check" steps live in this browser
 * (localStorage "skyshift.assist.replies", via src/vote's safe storage).
 * @module
 */

/**
 * Mount the six-step checklist for one candidate: status glyph and word, a one-sentence finding
 * with numbers and units, field-level details in professional mode, yes / no / can't tell replies
 * for the steps only a person can settle, and "How to check this yourself" disclosures.
 * Returns `{ update(c, opts?), destroy() }`.
 */
export { mountChecklist, type ChecklistHandle, type ChecklistOptions } from "./checklist";

/**
 * Mount "What next": a draft MPC report (ADES PSV) for movers, a Backyard Worlds post draft for
 * moving stars, a citation (plain or BibTeX) and a permalink, each with Copy and Download.
 * `votes` are the candidate's counts (src/vote/model Counts) or null when not loaded; drafts open by
 * themselves once the community's consensus is "mover". Returns `{ update(c, votes?), destroy() }`.
 */
export { mountWhatNext, leadSentence, type WhatNextHandle, type WhatNextOptions } from "./whatnext";

/** The checklist as data: `checklist(c)` → CheckStep[]; `withReplies`, `summarize`, thresholds. */
export {
  checklist,
  withReplies,
  summarize,
  canReply,
  fitTrack,
  blinkHref,
  detectionsOf,
  MATCH_RADIUS_ARCSEC,
  KNOWN_SAME_ARCSEC,
  KNOWN_NEAR_ARCSEC,
  PM_GOOD_SIGMA,
} from "./checks";

/** Plain words for kinds, reason codes, FLAGS bits and labels: one table each. */
export {
  KIND_TEXT,
  REASON_TEXT,
  FLAG_TEXT,
  LABEL_TEXT,
  STATUS_TEXT,
  REPLY_TEXT,
  reasonWords,
  flagWords,
  labelWords,
  type Tone,
  type FlagPolicy,
} from "./words";

/** Text drafts (pure): `mpcDraft`, `forumDraft`, `citation`, `permalink`. Never sent anywhere. */
export {
  mpcDraft,
  forumDraft,
  citation,
  permalink,
  votesSentence,
  trackletId,
  siteBase,
  SPHEREX_MPC_CODE,
  MPC_ADES_URL,
  BYW_FORUM_URL,
  DATA_DOIS,
  type MpcDraft,
  type Citation,
  type ReportOptions,
} from "./reports";

/** Downloads for any list of candidates (the Tools page): CSV, VOTable 1.4 and JSON. */
export { toCSV, toVOTable, toJSON, COLUMNS, type ExportOptions } from "./exports";

/** Copy with a textarea fallback, and download as a file. */
export { copyText, downloadText } from "./clipboard";

/** Coordinates and times: sexagesimal (no 60-second carry bugs) and ISO from MJD. */
export { formatRa, formatDec, mjdToIso } from "./format";

/** The person's replies, per candidate, in this browser. */
export { loadReplies, saveReply, REPLY_EVENT } from "./replies";

export type { AssistCandidate, CandidateExtras, Detection, CheckStep, AnsweredStep, StepId, StepStatus, Reply } from "./types";
