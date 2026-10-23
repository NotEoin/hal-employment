/* What this window may do on the computer Hal runs on. Components ask can(what) rather than
   checking the window's remote flag themselves, so a window opened from another computer through the
   launcher gets the right behaviour in one place.

   A window on Hal's own computer can do everything. A window from another computer may do only what
   /session's caps allow: folders cannot open there, Hal's own window is not offered, and sign-ins happen
   on the computer Hal runs on. Its pop-ups are browser notifications, which a browser allows only after
   the person agrees, from a button they pressed. */
import { store } from "./store.js";

/** True when this window was opened from another computer through the launcher. */
export function isRemote() { return !!(store.s.session && store.s.session.remote); }

/** The name of the computer Hal runs on, for "Do this on <host>". */
export function host() { return (store.s.session && store.s.session.remote_host) || "the computer Hal runs on"; }

/** True when this window may do `what` ("open_folder", "window_mode", "sign_in_here", "start_at_login"). */
export function can(what) {
  const s = store.s.session;
  if (!s) return false;
  if (!s.remote) return true;
  return !!(s.caps && s.caps[what]);
}

/** The words for something only the computer Hal runs on can do. */
export function onlyThere() { return "Change this on " + host() + ", the computer Hal runs on."; }

/** "default", "granted", "denied", or "none" when this browser has no notifications. */
export function popUpState() {
  return typeof Notification === "undefined" ? "none" : Notification.permission;
}

/** Asks once, from a button, whether Hal may show notifications in this browser; the new state. */
export async function askForPopUps() {
  if (popUpState() !== "default") return popUpState();
  try { return await Notification.requestPermission(); } catch (err) { return popUpState(); }
}
