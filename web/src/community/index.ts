// Placeholders: the Community and About pages (DESIGN.md "Pages"), replaced by their owner.
import type { MountPage } from "../app/modules";

export const mountCommunity: MountPage = (el) => {
  el.innerHTML = `<h1>Community</h1>`;
  return { show() {}, destroy() { el.innerHTML = ""; } };
};

export const mountAbout: MountPage = (el) => {
  el.innerHTML = `<h1>About SkyShift</h1>`;
  return { show() {}, destroy() { el.innerHTML = ""; } };
};
