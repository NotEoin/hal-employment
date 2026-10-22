/* STUB: the remote part */
/* What this window may do on the computer Hal runs on. Components ask can(what) rather than checking
   the session's remote flag themselves. A window on Hal's own computer can
   do everything, and a window from another computer only what /session's caps allow. */
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
