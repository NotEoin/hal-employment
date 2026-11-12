"""Commands for the employment record vault: record status, sync, search and ask, and ingest.

`ingest FOLDER...` asks the running Hal to sort the folders (with the local API key), as `jobhunt sort` does:
Hal proposes where each file goes and adds nothing until the person accepts (`cli/sort.py`). It no longer
reads folders straight in, which guessed each file's kind and owner. `--mine DIR` and `--course DIR` are kept
for older scripts and sorted the same way. `--again` and `--tidy` still work on a record an older Hal built,
following the read until it ends; Ctrl+C stops following, not the read.

The bodies moved unchanged from `jobhunt/__main__.py`; the parser, its arguments and help stay there,
because the voice bridge and the /jobs command parse what these commands print.
"""
import http.client
import json
import os
import sys
import textwrap
import time
from pathlib import Path

FOLLOW_EVERY_S = 10       # how often `record ingest` prints how far the ingest has got
HTTP_TIMEOUT_S = 30
FINAL = ("done", "error", "stopped", "interrupted")


def address() -> tuple[str, int]:
    """The local API key and the port of the running Hal on this computer."""
    from ..core import paths, settings, updates

    key = (paths.config_dir() / "local-api-key").read_text(encoding="utf-8").strip()
    raw = os.environ.get("HAL_EMPLOYMENT_PORT", "").strip()
    return key, int(raw) if raw.isdigit() else int(settings.get("advanced.port") or updates.DEFAULT_PORT)


def _hal(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Calls the running Hal on this computer with the local API key."""
    from ..core import updates

    key, port = address()
    con = http.client.HTTPConnection(updates.BIND, port, timeout=HTTP_TIMEOUT_S)
    try:
        con.request(method, path, body=json.dumps(body or {}).encode() if method == "POST" else None,
                    headers={"Host": f"{updates.BIND}:{port}", "X-Hal": "1", "Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"})
        reply = con.getresponse()
        return reply.status, json.loads(reply.read() or b"{}")
    finally:
        con.close()


def ingest(folders: list[str], again: bool = False, tidy: bool = False, mine: list[str] = (),
           course: list[str] = ()) -> None:
    """Sorts `folders` (with `mine` and `course`) in the running Hal, or reads again an older Hal's record.

    Folders are sorted as `jobhunt sort` does: proposals to accept, nothing read in. `again` reads with the AI
    the documents an earlier ingest filed by rules alone; `tidy` holds everything already in the record to
    today's rules, after a backup; both print their progress until they end.
    """
    folders = [*folders, *mine, *course]
    if sum(map(bool, (folders, again, tidy))) != 1:
        sys.exit("ingest: give one or more folders to sort, or --again, or --tidy")
    try:
        if again or tidy:
            status, out = _hal("POST", f"/api/v2/record/ingest/{'again' if again else 'tidy'}", {})
        else:
            status, out = _hal("POST", "/api/v2/record/ingest", {"folders": [str(Path(f).resolve()) for f in folders]})
    except (OSError, ValueError) as e:
        sys.exit(f"ingest: Hal is not running here ({e}); start it, then try again")
    if status != 200:
        sys.exit(f"ingest: {(out.get('error') or {}).get('message') or status}")
    if not (again or tidy):
        from . import sort

        print(f"Sorting {len(folders)} folder(s); Hal proposes where each file goes and adds nothing yet.")
        sort.follow_sort(out["sort_id"])
        return
    print("Reading the rest of your documents with the AI" if again else "Tidying your record",
          "; it shows on Your record too.", sep="")
    try:
        while True:
            time.sleep(FOLLOW_EVERY_S)
            _, now = _hal("GET", "/api/v2/record/ingest")
            eta = f", about {round(now['eta_s'] / 60)} min left" if now.get("eta_s") else ""
            print(f"{now.get('done', 0)}/{now.get('total', 0)}{eta}  {now.get('current') or ''}", flush=True)
            if now.get("state") in FINAL:
                print(f"Finished: {now['state']}")
                return
    except KeyboardInterrupt:
        print("\nStopped following; the ingest carries on in Hal.")


def cmd_ingest(args):
    """Handles `jobhunt ingest`: sorts whole folders, or reads again an older Hal's record, in the running Hal."""
    ingest(args.folders, again=args.again, tidy=args.tidy, mine=args.mine, course=args.course)


def cmd_record(args):
    """Handles `jobhunt record`: searches or asks questions about your record."""
    from .. import record
    query = " ".join(args.text)
    if args.op in ("search", "ask") and not query:
        sys.exit(f"record {args.op}: give a question")
    try:
        if args.op == "sync":
            r = record.sync()
            print(f"{r['notes']} notes, {r['passages']} passages; {r['changed']} re-read, {r['removed']} removed")
            for path in r["converted"]:
                print(f"  converted -> {path}")
        elif args.op == "search":
            for h in record.search(query, k=6, min_score=0.45, include_private=True):
                print(f"{h['score']:.2f}  {h['path']} > {h['heading']}")
                print(textwrap.indent(textwrap.shorten(h["text"], 300), "      "))
        elif args.op == "ask":
            r = record.ask(query)
            print(r["answer"] or f"(no model: {r.get('note')})")
            if r["sources"]:
                print("\nFrom: " + ", ".join(r["sources"]))
        else:
            st = record.status()
            print(f"{st['vault']}{'' if st['exists'] else ' (missing)'}: {st['notes']} notes, {st['passages']} passages, "
                  f"indexed {st['indexed'] or 'never'}")
    except record.RecordUnavailable as e:
        sys.exit(f"record: {e}")
