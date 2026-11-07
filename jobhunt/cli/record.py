"""Commands for the employment record vault: record status, sync, search and ask, and ingest.

`ingest FOLDER...` asks the running Hal to read the folders (with the local API key), so a long ingest
runs where Your record shows its progress, then follows it here until it ends; Ctrl+C stops following, not
the ingest. `--mine DIR` and `--course DIR` name folders of the person's own work and of course material.

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


def _hal(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Calls the running Hal on this computer with the local API key."""
    from ..core import paths, settings, updates

    key = (paths.config_dir() / "local-api-key").read_text(encoding="utf-8").strip()
    raw = os.environ.get("HAL_EMPLOYMENT_PORT", "").strip()
    port = int(raw) if raw.isdigit() else int(settings.get("advanced.port") or updates.DEFAULT_PORT)
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
    """Starts an ingest of `folders` in the running Hal and prints its progress until it ends.

    `folders` hold a mix of the person's own work and course material, `mine` their own work and `course`
    course material they were given. `again` instead reads with the AI the documents an earlier ingest filed
    by rules alone; `tidy` holds everything already in the record to today's rules, after a backup.
    """
    given = [(f, "mixed") for f in folders] + [(f, "mine") for f in mine] + [(f, "course") for f in course]
    folders = [f for f, _ in given]
    if sum(map(bool, (given, again, tidy))) != 1:
        sys.exit("ingest: give one or more folders to read, or --again, or --tidy")
    try:
        if again or tidy:
            status, out = _hal("POST", f"/api/v2/record/ingest/{'again' if again else 'tidy'}", {})
        else:
            status, out = _hal("POST", "/api/v2/record/ingest",
                               {"folders": [{"path": str(Path(f).resolve()), "origin": origin}
                                            for f, origin in given]})
    except (OSError, ValueError) as e:
        sys.exit(f"ingest: Hal is not running here ({e}); start it, then try again")
    if status != 200:
        sys.exit(f"ingest: {(out.get('error') or {}).get('message') or status}")
    print("Reading the rest of your documents with the AI" if again else "Tidying your record" if tidy else
          f"Reading {len(folders)} folder(s) into your record", "; it shows on Your record too.", sep="")
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
    """Handles `jobhunt ingest`: reads whole folders into the record in the running Hal."""
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
