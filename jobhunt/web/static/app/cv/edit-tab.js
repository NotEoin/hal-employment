/* The Edit your CV tab: every section, then its entries, then their lines, changed in place. Nothing is
   saved until "Save changes", which sends the session's change list to PATCH /cv/document on the version the
   editor opened, as one new version; so Undo is Versions. When that version is no longer in use (a
   suggestion accepted, a version used again) the server answers 409 and the editor says so, keeping the
   person's changes on screen until they choose. While there are changes, leaving the tab, the page or
   Hal asks first (core/router.js `guardLeave`). */
import { html, useState, useEffect } from "../core/html.js";
import { api } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { guardLeave } from "../core/router.js";
import { Note, Chip } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { ask } from "../ui/dialog.js";
import { EntryCard, focusSoon } from "./edit-entry.js";
import { applyChange, addChange, blankEntry, newId, target } from "./doc-changes.js";

// The record card kinds each section kind can take, as `edit.CARD_SECTIONS` the other way round.
const FROM_RECORD = { experience: "job", volunteering: "job", education: "education", projects: "project",
  certifications: "certificate" };
const NOTHING_SAVED = "Your changes are not saved.";

function fresh(entry, kind) {
  return { ...entry, id: newId(kind), bullets: entry.bullets.map(b => ({ ...b, id: newId("line") })) };
}

function FromRecord({ s, act, addId }) {
  const { data: cards, error } = useApi("/cv/record-cards");
  const fits = (cards || []).filter(c => c.sections.includes(s.kind));
  const have = new Set(s.entries.map(e => (e.heading || "").toLowerCase()));
  const add = c => { const e = fresh(c.entry, s.kind); act({ op: "insert", target: target(s), value: e }); toast("Added " + c.title + ". Save changes to keep it."); focusSoon(addId); };
  if (error) return html`<${Note} icon="warn" tone="warn">${error.message}<//>`;
  if (!cards) return html`<p class="small sub">Loading your record.</p>`;
  if (!fits.length) return html`<p class="small sub">Your record has nothing to add here yet. Add it on Your record first.</p>`;
  return html`<div class="cv-from-record">${fits.map(c => html`<div class="li" key=${c.id}><div class="grow"><b>${c.title}</b>
      ${have.has(c.title.toLowerCase()) && html` <${Chip} tone="good">On your CV<//>`}
      ${c.state === "hal" && html` <${Chip} tone="warn">Not checked yet<//>`}
      <div class="small sub">${[c.sub, c.when].filter(Boolean).join(", ")}</div></div>
    <button type="button" class="btn small" aria-label=${"Add " + c.title} onClick=${() => add(c)}><${Icon} n="plus" c="s" />Add</button></div>`)}</div>`;
}

function SectionCard({ s, act }) {
  const [picking, setPicking] = useState(false);
  const addId = "add-" + target(s);
  const style = (s.entries[0] && s.entries[0].style) || (s.kind === "additional" ? "paragraph" : "bullets");
  const addEntry = () => { const e = blankEntry(s.kind, style); act({ op: "insert", target: target(s), value: e }); focusSoon("f-" + target(s, e) + "/heading"); };
  const head = "sec-" + s.id;
  return html`<section class="card" aria-labelledby=${head}><div class="row"><h2 class="h2 grow" id=${head} style="margin:0">${s.title}</h2>
      ${!s.visible && html`<${Chip} tone="warn">Hidden from your CV<//>`}${s.locked && html`<${Chip}><${Icon} n="lock" c="s" />Locked<//>`}</div>
    ${(s.kind === "summary" || s.text) && html`<div class="rec-field" style="margin-top:12px"><label class="lab" for=${"f-" + target(s) + "/text"}>What it says</label>
      <textarea class="input" rows="4" id=${"f-" + target(s) + "/text"} value=${s.text}
        onInput=${e => act({ op: "replace", target: target(s) + "/text", value: e.target.value })}></textarea></div>`}
    <div class="stack s" style="margin-top:12px">${s.entries.map((e, i) => html`<${EntryCard} key=${e.id} s=${s} e=${e} i=${i} n=${s.entries.length} act=${act} addId=${addId} />`)}</div>
    ${s.kind !== "summary" && html`<div class="row" style="margin-top:12px">
      <button type="button" class="btn quiet small" id=${addId} onClick=${addEntry}><${Icon} n="plus" c="s" />Add an entry</button>
      ${FROM_RECORD[s.kind] && html`<button type="button" class="btn quiet small" aria-expanded=${picking ? "true" : "false"}
        onClick=${() => setPicking(!picking)}><${Icon} n="book" c="s" />Add from your record</button>`}</div>
      ${picking && html`<${FromRecord} s=${s} act=${act} addId=${addId} />`}`}</section>`;
}

/** The tab; `setT` moves to another tab of My CV (Versions, for Undo). */
export function EditTab({ setT }) {
  const { data: base, reload } = useApi("/cv/document");
  const [doc, setDoc] = useState(null);
  const [changes, setChanges] = useState([]);
  const [stale, setStale] = useState("");
  const [busy, setBusy] = useState(false);
  const opened = doc && doc.version_id;
  useEffect(() => { if (base && (!doc || (!changes.length && base.version_id !== doc.version_id))) setDoc(base); }, [base]);
  const dirty = !!doc && !!base && changes.length > 0;
  useEffect(() => {
    if (!dirty) { guardLeave(null); return undefined; }
    guardLeave(next => {
      ask({ title: "Leave without saving?", text: "You have changes you have not saved. If you leave, they are lost.",
        yes: "Leave without saving", no: "Stay here", danger: true, icon: "warn" }).then(yes => { if (yes) next(); });
      return true;
    });
    const unload = e => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", unload);
    return () => { window.removeEventListener("beforeunload", unload); guardLeave(null); };
  }, [dirty]);
  if (!doc) return html`<p class="sub">Loading your CV.</p>`;
  const act = c => {
    try { setDoc(d => applyChange(d, c)); setChanges(cs => addChange(cs, c)); }
    catch (err) { toast(err.message); }
  };
  const restart = async () => {
    try { const now = await reload(); setChanges([]); setStale(""); setDoc(now); } catch (err) { toast(err.message); }
  };
  const discard = async () => {
    if (await ask({ title: "Discard your changes?", text: "Your CV stays as it was before you started editing.",
      yes: "Discard changes", no: "Keep editing", danger: true })) restart();
  };
  const save = async () => {
    const empty = doc.sections.flatMap(s => s.entries.flatMap(e => e.bullets.filter(b => !b.text.trim())
      .map(b => ({ op: "delete", target: target(s, e, b) }))));
    setBusy(true);
    try {
      const out = await api("PATCH", "/cv/document", { base: opened, changes: [...changes, ...empty] });
      setChanges([]);
      setDoc(null);
      invalidate("/cv*");
      toast("Saved your CV as version " + out.n + ". To undo, use an older version.", () => setT("versions"), "Versions");
    } catch (err) {
      if (err.code === "conflict") setStale(err.message);
      else toast(err.message + " " + NOTHING_SAVED);
    } finally { setBusy(false); }
  };
  return html`<div class="stack">
    <div class="card cv-edit-bar"><p class="small sub grow" style="margin:0">Change any line. Nothing is saved until you press${" "}
      <b>Save changes</b>, and each save is a new version. To undo a save, use an older version in${" "}
      <button type="button" class="btn quiet small" onClick=${() => setT("versions")}>Versions</button></p>
      <div class="row">${dirty && html`<span class="small" role="status">You have changes you have not saved.</span>`}
        <button type="button" class="btn quiet small" disabled=${!dirty || busy} onClick=${discard}>Discard changes</button>
        <button type="button" class="btn primary" disabled=${!dirty || busy} onClick=${save}>Save changes</button></div></div>
    ${stale && html`<${Note} icon="warn" tone="warn"><b>${stale}</b> Your CV was changed somewhere else, for example by a
      suggestion you accepted. Copy anything you want to keep, then start again from your CV as it is now.
      <div style="margin-top:8px"><button type="button" class="btn small" onClick=${restart}>Start again from your CV now</button></div><//>`}
    ${doc.sections.map(s => html`<${SectionCard} key=${s.id} s=${s} act=${act} />`)}</div>`;
}
