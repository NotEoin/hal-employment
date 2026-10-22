/* The API client: every call to /api/v2 goes through api(), which sends the X-Hal header the server
   requires on changes, turns error replies into ApiError with the server's plain words, and drops the
   session on 401 so the shell shows the "open Hal from its shortcut" screen. */
import { store } from "./store.js";
import { on } from "./events.js";

const BASE = "/api/v2";
const ACTION_POLL_MS = 5000;  // a fallback for missed events; the stream normally reports first
const FINISHED = new Set(["done", "error", "stopped", "interrupted"]);

/** An error reply: `code` from the server, plain-words `message`, and the `field` and `fix` when given. */
export class ApiError extends Error {
  constructor(code, message, field, fix) { super(message); Object.assign(this, { code, field, fix }); }
}

/** Calls the API and returns the parsed JSON reply (null for a reply that is not JSON). */
export async function api(method, path, body) {
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  let res;
  try {
    res = await fetch(BASE + path, {
      method, credentials: "same-origin",
      headers: body === undefined || isForm ? { "X-Hal": "1" } : { "X-Hal": "1", "Content-Type": "application/json" },
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body) });
  } catch (err) {
    throw new ApiError("offline", "Hal is not answering. It may be restarting.");
  }
  if (res.status === 401) {
    store.set({ session: null });
    throw new ApiError("signed_out", "Open Hal Employment from its shortcut to carry on.");
  }
  const data = (res.headers.get("content-type") || "").includes("json") ? await res.json() : null;
  if (!res.ok) {
    const e = (data && data.error) || {};
    throw new ApiError(e.code || "error", e.message || "Something went wrong. Hal kept the details in Activity.",
                       e.field, e.fix);
  }
  return data;
}

export const get = p => api("GET", p);
export const post = (p, b = {}) => api("POST", p, b);
export const put = (p, b) => api("PUT", p, b);
export const del = p => api("DELETE", p);

/** Starts an action and follows it: resolves with its final Action, and calls onStep with each step. */
export async function run(path, body, onStep) {
  const started = await post(path, body);
  const id = started && started.action_id;
  if (!id) return started;
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = async () => {
      if (done) return;
      done = true;
      off();
      clearInterval(timer);
      try { resolve(await get("/actions/" + encodeURIComponent(id))); } catch (err) { reject(err); }
    };
    const off = on(ev => {
      if (ev.type === "step" && ev.action_id === id && onStep) onStep(ev);
      if (ev.type === "action" && ev.id === id && FINISHED.has(ev.state)) finish();
    });
    const timer = setInterval(async () => {
      try { const a = await get("/actions/" + encodeURIComponent(id)); if (FINISHED.has(a.state)) finish(); }
      catch (err) { if (err.code === "signed_out" || err.code === "not_found") { done = true; off(); clearInterval(timer); reject(err); } }
    }, ACTION_POLL_MS);
  });
}
