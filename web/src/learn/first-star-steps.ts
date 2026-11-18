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
  { id: "look", prompt: "Look around the star field. What shapes can you see?", action: "I’ve looked around" },
  { id: "landmarks", prompt: "Mark three stars you can use as landmarks.", action: "Keep these landmarks" },
  { id: "contrast", prompt: "Try High contrast. Look for soft shapes among the sharp points.", action: "I can see more detail" },
  { id: "next-visit", prompt: "Open Frame 2. Can you find the same landmarks?", action: "I found my group" },
  { id: "compare", prompt: "Compare both frames. What has moved across the field?", action: "I’m ready to choose a point" },
  { id: "mark", prompt: "Mark the bright head of the object you think moved.", action: "Keep this mark" },
  { id: "close-look", prompt: "Can you find that same object in the other frame? Mark its head again.", action: "I’ve marked both frames" },
  { id: "difference", prompt: "Try Difference. What do you see near your two marks?", action: "I’ve checked the difference" },
  { id: "originals", prompt: "Go back to Original. Can you trace the shift between the two frames?", action: "I’ve checked the images" },
  { id: "verify", prompt: "Open Frame 3. Where did the object go next?", action: "Confirm my track" },
] as const;
