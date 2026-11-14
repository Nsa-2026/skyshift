// Plain words for a filter that couldn't be drawn. The filters read NASA's SPHEREx map live from
// IRSA in the browser, and IRSA returns 502s and stalls under load (14 Nov), so this is a normal
// state, not a crash: say what happened, that the blink still works, and offer Try again.
import { IrsaError, REQUEST_TIMEOUT_MS } from "./cube";

export interface Failure {
  title: string;
  text: string;
}

const STILL = "The normal view doesn't need it and still works.";

export function describeFailure(e: unknown): Failure {
  if (e instanceof IrsaError) {
    if (e.kind === "slow") {
      return {
        title: "NASA's archive is too slow right now",
        text: `NASA's archive (IRSA), where these pictures come from, didn't answer within ${REQUEST_TIMEOUT_MS / 1000} seconds. It is often busy; try again in a minute. ${STILL}`,
      };
    }
    if (e.kind === "down") {
      const code = e.status ? ` (error ${e.status})` : "";
      return {
        title: "Can't reach NASA's archive right now",
        text: `NASA's archive (IRSA), where these pictures come from, isn't working properly at the moment${code}. Try again in a minute. ${STILL}`,
      };
    }
    if (e.kind === "range") {
      return {
        title: "Can't show this filter right now",
        text: `NASA's archive sent its whole 320 MB map file instead of the small piece we asked for, so we stopped rather than download it all. ${STILL}`,
      };
    }
  }
  // fetch throws a TypeError when the network or CORS fails (no answer at all).
  if (e instanceof TypeError) {
    return {
      title: "Can't reach NASA's archive right now",
      text: `Your browser couldn't reach NASA's archive (IRSA), where these pictures come from. It may be down or busy, or your connection may have dropped. ${STILL}`,
    };
  }
  const why = e instanceof Error && e.message ? ` (${e.message})` : "";
  return {
    title: "Can't show this filter right now",
    text: `Part of NASA's map file wasn't what we expected${why}. ${STILL}`,
  };
}
