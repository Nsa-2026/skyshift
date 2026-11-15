// The Community and About pages (DESIGN.md "Pages"). src/app mounts them on first visit.
// Results mounts the list for astronomers alone (mountCommunityWith, variant "astronomers").
import "./community.css";
import type { MountPage } from "../app/modules";
import { mountAboutWith } from "./about";
import { mountCommunityWith } from "./community";

export { mountCommunityWith, type CommunityOptions } from "./community";

export const mountCommunity: MountPage = (el, ctx) => mountCommunityWith(el, ctx);

export const mountAbout: MountPage = (el, ctx) => mountAboutWith(el, ctx);
