/* A yes-or-no question: openDialog("confirm", {title, text, yes, no, danger, resolve}), or ask() from
   ui/dialog.js, which returns a promise of true or false. Closing it any other way counts as no. */
import { html, useEffect, useRef } from "../core/html.js";
import { closeDialog } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";

/** The question, with its two buttons. */
export default function Confirm({ d }) {
  const answered = useRef(false);
  const answer = yes => { answered.current = true; closeDialog(); if (d.resolve) d.resolve(yes); };
  useEffect(() => () => { if (!answered.current && d.resolve) d.resolve(false); }, []);
  return html`<${DlgHead} title=${d.title || "Are you sure?"} icon=${d.icon || "help"} />
    ${d.text && html`<p>${d.text}</p>`}
    <footer><button type="button" class="btn quiet" onClick=${() => answer(false)}>${d.no || "Cancel"}</button>
      <button type="button" class=${d.danger ? "btn danger" : "btn primary"} onClick=${() => answer(true)}>${d.yes || "Yes"}</button></footer>`;
}
