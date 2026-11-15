// The Review queue (#/review) and the suspect panel the Viewer shows for a candidate (#/c/<id>).
import "./review.css";

export { mountSuspect, suspectFrames, type SuspectFramesNote, type SuspectHandle, type SuspectOptions } from "./candidate";
export { mountQueue, mountReview, type QueueHandle, type QueueOptions } from "./review";
export { candidateView } from "./data";
