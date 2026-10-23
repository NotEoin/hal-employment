/* Copying words to the clipboard, with one wording for the whole app. The browser can refuse (a window opened
   from another computer over http, or a permission the person denied), so a copy says "Copied" only once the
   browser has taken the words. */
import { toast } from "../core/store.js";

/** Puts `text` on the clipboard and toasts `what`; true when it worked. */
export async function copyText(text, what = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(what); return true; }
  catch (err) { toast("Hal could not reach the clipboard. Select the words and copy them yourself."); return false; }
}
