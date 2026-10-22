"""Commands for the employment record vault: record status, sync, search and ask.

The bodies moved unchanged from `jobhunt/__main__.py`; the parser, its arguments and help stay there,
because the voice bridge and the /jobs command parse what these commands print.
"""
import sys
import textwrap


def cmd_record(args):
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
