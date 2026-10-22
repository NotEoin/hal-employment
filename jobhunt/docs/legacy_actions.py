"""The legacy interface's document actions: interview preparation, contacts and an inbox sync.

Moved unchanged from `jobhunt/web/server.py`, which re-exports every name here for the legacy interface.
"""


from .. import contacts, gpu, prep, tasks
from ..apply.queries import _job


def act_prep(con, job_id: str) -> str:
    reason = gpu.busy()
    if reason:
        raise RuntimeError(reason)
    prep.build(con, _job(con, job_id))
    return "preparation notes ready"


def act_contacts(con, job_id: str) -> str:
    found = contacts.find(con, _job(con, job_id))
    count = len(found["people"])
    return (f"{count} people suggested" if count else "nobody found to suggest") \
        + (f"; {found['note']}" if found["note"] else "")


def act_mail(con, _job_id: str) -> str:
    from .. import gmail
    from ..notify import notify
    attention = gmail.sync(con)
    made = tasks.sync(con)
    for a in attention:
        notify(f"{a['kind'].title()}: {a['company'] or a['sender']}", a["subject"], urgent=a["kind"] != "human")
    return f"mail synced; {len(attention)} need you, {len(made)} new to-dos"
