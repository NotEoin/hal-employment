/* Which of the person's CVs My CV shows. Every My CV call goes through cvUrl(), which adds `cv=<id>` for the
   chosen CV, so each tab (Your CV, Edit, Suggestions, Type and look, Checks, Versions) and the dialogs it opens
   act on that CV, and each CV's answers are cached under their own address. With no CV chosen (the setup
   guide, a first visit) the address is left as it is and the server uses the lead CV.

   The choice is remembered in this browser only (localStorage, wrapped: a private window refuses it), as a
   convenience; the server never depends on it. */

const KEY = "hal.cv.chosen";
let chosen = null;

try { chosen = localStorage.getItem(KEY) || null; } catch (err) { chosen = null; }

/** The chosen CV's id, or null for the lead CV. */
export function chosenCv() { return chosen; }

/** Chooses the CV My CV shows (null for the lead CV); `remember` false keeps it out of this browser's memory,
    for the lead CV shown because nothing was chosen. */
export function chooseCv(id, remember = true) {
  chosen = id || null;
  if (!remember) return;
  try { if (chosen) localStorage.setItem(KEY, chosen); else localStorage.removeItem(KEY); } catch (err) { /* not remembered */ }
}

/** `path` with the chosen CV added as `cv=<id>`; `id` names another CV instead. */
export function cvUrl(path, id = chosen) {
  if (!id) return path;
  return path + (path.includes("?") ? "&" : "?") + "cv=" + encodeURIComponent(id);
}
