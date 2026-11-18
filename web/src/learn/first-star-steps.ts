export const FIRST_STAR_TITLE = "Guidance to Find a New Star";
export const FIRST_STAR_ROUTE = "#/learn/first-star";

export const FIRST_STAR_STEPS = [
  { id: "look", prompt: "Take a look at this patch of sky. What catches your eye?", action: "I’ve looked around" },
  { id: "landmarks", prompt: "Can you mark three stars you’d recognize again?", action: "Keep these landmarks" },
  { id: "colours", prompt: "Try Star colours. Can you still find your three stars?", action: "I recognize them" },
  { id: "next-visit", prompt: "Open the next visit. Can you find your group again?", action: "I found my group" },
  { id: "first-visit", prompt: "Return to Visit 1 and look at the sky around your group.", action: "I’m back at the first visit" },
  { id: "compare", prompt: "Switch back and forth between the two visits. What stays the same?", action: "I’ve compared both visits" },
  { id: "mark", prompt: "Is there a spot you’d like to look at again? Leave a mark there.", action: "Keep this mark" },
  { id: "close-look", prompt: "Zoom in on your mark. What do you notice when you switch visits now?", action: "I’ve taken a closer look" },
  { id: "difference", prompt: "Open Difference. What do you see around your mark?", action: "I’ve checked the difference" },
  { id: "originals", prompt: "Go back to the original images. Can you see the same thing there?", action: "I’ve checked the observations" },
  { id: "verify", prompt: "There’s one more visit. Can you find your star there and mark it again?", action: "Confirm my star" },
] as const;
