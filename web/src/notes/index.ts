// Notes on an object to check (P3). Written on the object's own page, read back there and in the
// Community page's roundup. Kept in this browser until a notes collection exists in the contract.
import "./notes.css";

export { mountNotesPanel, type NotesPanelHandle, type NotesPanelOptions } from "./panel";
export { feedFor, objectsIn, roundup, type Shown } from "./feed";
export { sampleNotes, type SampleNote } from "./sample";
export {
  BODY_MAX,
  KEY,
  NICKNAME_KEY,
  addPost,
  authorName,
  readPosts,
  timeAgo,
  type Post,
} from "./posts";
