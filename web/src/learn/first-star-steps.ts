export const FIRST_STAR_TITLE = "Guidance to Find a New Star";
export const FIRST_STAR_ROUTE = "#/learn/first-star";

export const FIRST_STAR_STEPS = [
  { id: "look", prompt: "Take a look at this patch of sky. What catches your eye?", action: "I’ve looked around" },
  { id: "landmarks", prompt: "Mark three bright stars you could recognize again.", action: "Keep these landmarks" },
  { id: "contrast", prompt: "Try High contrast. What faint points can you see now?", action: "I can see more detail" },
  { id: "next-visit", prompt: "Open Visit 2. Can you find your group inside the blue box?", action: "I found my group" },
  { id: "compare", prompt: "Look at the same patch on both dates. Can you spot a point that moved?", action: "I’m ready to choose a point" },
  { id: "mark", prompt: "Find a point you want to follow and mark it in this visit.", action: "Keep this mark" },
  { id: "close-look", prompt: "Find the same point in the other visit. Where would you put its second mark?", action: "I’ve marked both visits" },
  { id: "difference", prompt: "Try Difference. Where do you see red and blue near your marks?", action: "I’ve checked the difference" },
  { id: "originals", prompt: "Go back to Original. Can you follow the shift between the two close-ups?", action: "I’ve checked the observations" },
  { id: "verify", prompt: "Open Visit 3. Find the same point and leave your final mark.", action: "Confirm my find" },
] as const;

export const COMET_TITLE = "Track a Comet";
export const COMET_ROUTE = "#/learn/comet";

export const COMET_STEPS = [
  { id: "landmarks", prompt: "Watch what moves across the three frames.", action: "Mark what moved" },
  { id: "track", prompt: "What moved between the two frames?" },
  { id: "difference", prompt: "Do the red and blue patches match your marks?", action: "Check Frame 3" },
  { id: "verify", prompt: "Where did the comet go next? Mark its head in Frame 3." },
] as const;
