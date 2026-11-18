export const FIRST_STAR_TITLE = "Guidance to Find a New Star";
export const FIRST_STAR_ROUTE = "#/learn/first-star";

export const FIRST_STAR_STEPS = [
  { id: "look", prompt: "Take a look at this patch of sky. What catches your eye?", action: "I’ve looked around" },
  { id: "landmarks", prompt: "Can you mark three stars you’d recognize again?", action: "Keep these landmarks" },
  { id: "colours", prompt: "Try Star colours. Can you still find your three stars?", action: "I recognize them" },
  { id: "next-visit", prompt: "Open Visit 2. Can you find your group inside the blue box?", action: "I found my group" },
  { id: "compare", prompt: "Both images show the same patch of sky. What looks the same?", action: "I’m ready to look for a star" },
  { id: "mark", prompt: "Which star would you look at again? Mark it on the image.", action: "Keep this mark" },
  { id: "close-look", prompt: "Compare the two close-ups. Would you keep your mark here?", action: "I’ve checked my mark" },
  { id: "difference", prompt: "Try Difference. What stands out inside your box?", action: "I’ve checked the difference" },
  { id: "originals", prompt: "Go back to Original. Can you see that change in the two close-ups?", action: "I’ve checked the observations" },
  { id: "verify", prompt: "Open Visit 3. Can you find and mark the same star?", action: "Confirm my star" },
] as const;
