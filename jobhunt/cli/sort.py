"""Commands for adding to Your record from the command line: `sort` and `sources`.

`sort FOLDER...` asks the running Hal to sort folders (with the local API key), then follows the sort and
prints how many files it proposes for each section and how many it left out; nothing is added. `sort
--accept-all` adds everything ticked in the latest sort (or `--id N`) and follows until it is in. `sources
list|add|read` lists the sources of a section, adds one (a file or folder is uploaded, a link, account,
folder on this computer or project name is added as it is) and reads one again; `sources read waiting` reads
everything still waiting and can run as a gpu-light batch job (`read_waiting`). Everything goes through the
running Hal's API, as `ingest` does, so the page shows the same and only one program writes the record.
"""
from __future__ import annotations

import http.client
import json
import signal
import sys
import time
import uuid
from pathlib import Path

FOLLOW_EVERY_S = 3          # how often a followed sort is asked how far it got
FOLLOW_FOR_S = 3600         # a sort of a very large folder ends well within this
HTTP_TIMEOUT_S = 120        # an upload of a few hundred megabytes on this computer
SORTING = ("new", "sorting")
YIELD_EXIT = 75             # gpu-light's default `--yield-exit`: run the job again later
YIELD_SIGNALS = tuple(getattr(signal, n) for n in ("SIGUSR2", "SIGTERM", "SIGINT") if hasattr(signal, n))
FOLDER_KINDS = ("repo", "local_folder")     # kinds of document held as a folder on this computer


def _hal(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    from . import record

    return record._hal(method, path, body)


def _or_exit(what: str, call) -> dict:
    """The reply of a call to the running Hal; exits with plain words when Hal is not running or says no."""
    try:
        status, out = call()
    except (OSError, ValueError) as e:
        sys.exit(f"{what}: Hal is not running here ({e}); start it, then try again")
    if status != 200:
        sys.exit(f"{what}: {(out.get('error') or {}).get('message') or status}")
    return out


def _wait(sort_id: int, done) -> dict:
    """The sort, asked again every few seconds until `done(sort)` says it has finished."""
    deadline = time.monotonic() + FOLLOW_FOR_S
    while True:
        out = _or_exit("sort", lambda: _hal("GET", f"/api/v2/record/sort/{sort_id}?limit=1"))
        if done(out) or time.monotonic() > deadline:
            return out
        time.sleep(FOLLOW_EVERY_S)


def summary(sort: dict) -> list[str]:
    """A sort in plain lines: what each section is proposed, then what was added and left out."""
    c = sort["counts"]
    lines = [f"Sort {sort['id']} ({sort['label']}): {sort['state']}"]
    for s in c["sections"]:
        wait = f", {s['needs_card']} need their course or job" if s["needs_card"] else ""
        lines.append(f"  {s['label']}: {s['proposed']} proposed, {s['ticked']} ticked{wait}")
    lines.append(f"  Added: {c['added']}  Left out: {c['left_out']}  Copies: {c['copy']}  Could not be added: "
                 f"{c['failed']}")
    return lines


def follow_sort(sort_id: int) -> dict:
    """Waits for a sort to finish proposing and prints what it proposes."""
    out = _wait(sort_id, lambda s: s["state"] not in SORTING)
    print("\n".join(summary(out)))
    if out["state"] == "ready":
        print("Nothing is added yet. Check the list on Your record, or run `jobhunt sort --accept-all`.")
    return out


def sort(folders: list[str], accept_all: bool = False, sort_id: int | None = None) -> None:
    """Sorts `folders`, or with `accept_all` adds everything ticked in sort `sort_id` (the latest when None)."""
    if accept_all == bool(folders):
        sys.exit("sort: give one or more folders to sort, or --accept-all")
    if folders:
        out = _or_exit("sort", lambda: _hal("POST", "/api/v2/record/sort",
                                            {"folders": [str(Path(f).resolve()) for f in folders]}))
        print(f"Sorting {len(folders)} folder(s); it shows on Your record too.")
        follow_sort(out["sort"]["id"])
        return
    if sort_id is None:
        found = _or_exit("sort", lambda: _hal("GET", "/api/v2/record/sort"))["sorts"]
        if not found:
            sys.exit("sort: there is no sort to accept; sort a folder first")
        sort_id = found[0]["id"]
    out = _or_exit("sort", lambda: _hal("POST", f"/api/v2/record/sort/{sort_id}/accept", {}))
    print(f"Adding {out['accepted']} file(s) to your record."
          + (f" {out['need_card']} wait for their course or job." if out["need_card"] else ""))
    done = _wait(sort_id, lambda s: not s["counts"]["accepted"])
    print("\n".join(summary(done)))


def cmd_sort(args):
    """Handles `jobhunt sort`: proposes where each file of a folder goes, or adds what was accepted."""
    sort(args.folders, accept_all=args.accept_all, sort_id=args.id)


def _form(path: str, files: list[tuple[str, bytes, str]], fields: dict) -> tuple[int, dict]:
    """POSTs a multipart form to the running Hal with the local API key, as the page uploads files."""
    from ..core import updates
    from .record import address

    key, port = address()
    boundary = uuid.uuid4().hex
    parts = []
    for i, (name, data, rel) in enumerate(files):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file{i}\"; filename=\"{name}\"\r\n"
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
        if rel:
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"path{i}\"\r\n\r\n{rel}\r\n".encode())
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    con = http.client.HTTPConnection(updates.BIND, port, timeout=HTTP_TIMEOUT_S)
    try:
        con.request("POST", path, body=b"".join(parts) + f"--{boundary}--\r\n".encode(),
                    headers={"Host": f"{updates.BIND}:{port}", "X-Hal": "1", "Authorization": f"Bearer {key}",
                             "Content-Type": f"multipart/form-data; boundary={boundary}"})
        reply = con.getresponse()
        return reply.status, json.loads(reply.read() or b"{}")
    finally:
        con.close()


def _files_of(what: Path) -> list[tuple[str, bytes, str]]:
    """A file as one upload, or a folder's files under its name, hidden ones left out."""
    if what.is_file():
        return [(what.name, what.read_bytes(), "")]
    return [(p.name, p.read_bytes(), f"{what.name}/{p.relative_to(what).as_posix()}") for p in sorted(what.rglob("*"))
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(what).parts)]


def sources(op: str, words: list[str], section: str | None = None, kind: str | None = None,
            card: int | None = None) -> None:
    """`list` the sources (of one section), `add SECTION THING` or `read ID` again."""
    if op == "list":
        out = _or_exit("sources", lambda: _hal("GET", "/api/v2/record/sources" + (f"?section={section}"
                                                                                   if section else "")))
        for s in out:
            print(f"{s['id']:>5}  {s['section_label']}: {s['title']} ({s['doc_kind_label']}, {s['state_words']})")
        return
    if op == "read" and words == ["waiting"]:
        read_waiting()
        return
    if op == "read":
        if len(words) != 1 or not words[0].isdigit():
            sys.exit("sources read: give the number of the source, from `jobhunt sources list`, or `waiting`")
        _or_exit("sources", lambda: _hal("POST", f"/api/v2/record/sources/{words[0]}/reread", {}))
        print(f"Hal is reading source {words[0]} again.")
        return
    if len(words) != 2:
        sys.exit("sources add: give the section and what to add, such as `sources add cv CV.pdf`")
    where, thing = words
    path = Path(thing).expanduser()
    fields = {k: v for k, v in (("doc_kind", kind), ("target_id", card)) if v is not None}
    if path.exists() and not (path.is_dir() and kind in FOLDER_KINDS):
        out = _or_exit("sources", lambda: _form(f"/api/v2/record/sources/{where}", _files_of(path), fields))
    else:
        locator = str(path.resolve()) if path.is_dir() else thing
        out = _or_exit("sources", lambda: _hal("POST", f"/api/v2/record/sources/{where}/link",
                                               {"locator": locator, **fields}))
    for s in out["added"]:
        print(f"Added {s['title']} (source {s['id']}); Hal is reading it.")
    for d in out["duplicates"]:
        print(f"{d['name']} is already in your record, under {d['section_label']}.")


def read_waiting() -> None:
    """Reads every source still waiting and follows the read until it ends.

    Run as a gpu-light batch job, it yields the graphics card: on `YIELD_SIGNALS` it stops the read (what was
    read is kept) and exits `YIELD_EXIT`, so gpu-light runs it again later and it reads only what still waits.
    """
    out = _or_exit("sources", lambda: _hal("POST", "/api/v2/record/read-waiting", {}))
    if not out["action_id"]:
        print("Nothing is waiting to be read.")
        return
    action_id = out["action_id"]
    print(f"Reading {out['waiting']} waiting source(s) (action {action_id}).", flush=True)

    def stop(signum, _frame) -> None:
        _hal("POST", f"/api/v2/actions/{action_id}/stop", {})
        print(f"Stopped the read for {signal.Signals(signum).name}; what was read is kept.", flush=True)
        sys.exit(YIELD_EXIT)

    for sig in YIELD_SIGNALS:
        signal.signal(sig, stop)
    while True:
        action = _or_exit("sources", lambda: _hal("GET", f"/api/v2/actions/{action_id}"))
        if action["state"] not in ("queued", "running"):
            break
        time.sleep(FOLLOW_EVERY_S)
    print(f"The read {action['state']}: {action.get('message') or ''}".rstrip(": "))
    if action["state"] != "done":
        sys.exit(1)


def cmd_sources(args):
    """Handles `jobhunt sources`: lists, adds or reads again the sources of Your record."""
    sources(args.op, args.words, section=args.section, kind=args.kind, card=args.card)


def register(sub) -> None:
    """Adds `sort` and `sources` to the command line's subcommands."""
    s = sub.add_parser("sort", help="propose where each file of a big folder goes in your record")
    s.add_argument("folders", nargs="*", help="folders on this computer to sort")
    s.add_argument("--accept-all", action="store_true", help="add everything ticked in the latest sort")
    s.add_argument("--id", type=int, help="the sort to accept, when not the latest")
    s.set_defaults(fn=cmd_sort)
    s = sub.add_parser("sources", help="list, add or read again what each part of your record holds")
    s.add_argument("op", choices=["list", "add", "read"])
    s.add_argument("words", nargs="*", help="add: the section and a file, folder, link or name; read: its number, "
                   "or `waiting` for everything still to read")
    s.add_argument("--section", help="list only this section (cv, coursework, project, document, ...)")
    s.add_argument("--kind", help="add: what it is within the section (essay, transcript, repo, ...)")
    s.add_argument("--card", type=int, help="add: the course or job card it is for, by its number")
    s.set_defaults(fn=cmd_sources)
