/* What Hal remembers: suggestions waiting for a yes, what Hal learned, notes about job
   sites and saved answers, each in its own tab. The tab is part of the route ("memory-learned"), so a link
   from Home or a notification opens the right one.

   The sites and answers tabs belong to their own parts of Hal. Each may export `useCount()` for the number on
   its tab; a tab that does not shows no number, so this page never fetches another part's data. */
import { html, useState } from "../core/html.js";
import { useApi } from "../core/store.js";
import { Head, Tabs } from "../ui/controls.js";
import { WaitingTab, MEMORY_EVENTS } from "../memory/waiting-tab.js";
import { LearnedTab } from "../memory/learned-tab.js";
import * as Sites from "../memory/sites-tab.js";
import * as Answers from "../memory/answers-tab.js";

const TABS = ["waiting", "learned", "sites", "answers"];
const noCount = () => "";
const useSitesCount = Sites.useCount || noCount;
const useAnswersCount = Answers.useCount || noCount;

function writeTab(t) {
  try { history.replaceState(null, "", "#" + (t === "waiting" ? "memory" : "memory-" + t)); } catch (err) { /* a sandboxed frame may refuse */ }
}

/** The page: header and the four tabs. */
export function Memory({ arg }) {
  const [t, setTab] = useState(TABS.includes(arg) ? arg : "waiting");
  const setT = next => { setTab(next); writeTab(next); };
  const waiting = useApi("/memory/waiting", { on: MEMORY_EVENTS }).data;
  const learned = useApi("/memory/learned", { on: MEMORY_EVENTS }).data;
  const sites = useSitesCount();
  const answers = useAnswersCount();
  return html`
    <${Head} title="What Hal remembers" sub="Everything Hal has learned or been told, where it came from, and a switch for each. Hal only suggests changes; you decide." />
    <${Tabs} label="What Hal remembers" value=${t} onChange=${setT} tabs=${[["waiting", "Waiting for you", (waiting || []).length || ""],
      ["learned", "Learned", learned ? learned.length : ""], ["sites", "Notes about job sites", sites],
      ["answers", "Saved answers", answers]]} />
    ${t === "waiting" && html`<${WaitingTab} />`}
    ${t === "learned" && html`<${LearnedTab} />`}
    ${t === "sites" && html`<${Sites.SitesTab} />`}
    ${t === "answers" && html`<${Answers.AnswersTab} />`}`;
}
