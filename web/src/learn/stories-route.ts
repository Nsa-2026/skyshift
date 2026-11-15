// The Stories page's parts, kept apart from its code so the Learn page can route to it without
// loading it: stories.ts, its data module and the /guxing/ scripts load only when it opens.
export type StoryPart = "guests" | "asterisms" | "planets";
export const STORY_PARTS: readonly { id: StoryPart; label: string }[] = [
  { id: "guests", label: "Guest stars" },
  { id: "asterisms", label: "Around a spot" },
  { id: "planets", label: "Planets 2026–27" },
];
export const isStoryPart = (s: string | undefined): s is StoryPart => STORY_PARTS.some((p) => p.id === s);
