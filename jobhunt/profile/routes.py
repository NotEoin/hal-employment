"""The profile's API for the Your record page: the overview, the items of each kind, and the person's changes.

Every write is the person's own choice from the page and publishes a `profile` event (with `item_id` or
`conflict_id`), so open pages read the profile again. A value that does not fit is 400 with plain words; an
item or conflict that is not there is 404; deleting an item Hal found is 403 (hide it instead).

Shapes, exactly as sent:

An item (`ITEM`):

    {"id": 3, "kind": "job", "state": "hal", "state_words": "Found by Hal", "origin": "found",
     "fields": {"title": "Collections volunteer", "employer": "Fenwick Lantern Museum Trust",
                "start": "2023-09", "current": true, "duties": ["Catalogued donated letters"]},
     "title": "Collections volunteer", "sub": "Fenwick Lantern Museum Trust", "when": "September 2023 to now",
     "evidence": [{"id": 7, "source_rel": "Sources/CVs and letters/CV.md", "file_id": "U291cmNlcy9...",
                   "quote": "Collections volunteer, Sept 2023 - present", "field": "start", "doc_type": "cv",
                   "verified": true, "found_at": "2026-11-05T09:00:00Z"}],
     "conflicts": [CONFLICT, ...], "can_delete": false,
     "created": "2026-11-05T09:00:00Z", "updated": "2026-11-05T09:00:00Z",
     "tier": "core", "tier_by": "hal", "tier_why": "Your main work in your field", "tier_words": "Lead with this"}

`state` is hal | confirmed | edited | hidden (`state_words`: Found by Hal, Checked by you, Edited by you,
Hidden); `origin` is found | you; `fields` hold only the fields that have a value, with dates as `YYYY-MM` or
`YYYY` (or the words as written when they are not a date); `file_id` opens the source through
`GET /api/v2/record/file/{file_id}`; `conflicts` are the open ones naming this item. The fields of each kind
are `store.KINDS`: job (title, employer, location, start, end, current, hours: full_time | part_time,
duties[], achievements[]), education (qualification, subject, institution, start, end, current, grade,
modules[{name, mark}]), skill (name, level, category), project (name, summary, role, tools[], link, period),
achievement (title, date, detail, issuer), certificate (name, issuer, date, expires, grade), basic (key,
value) and link (label, url). `tier` is core | supporting | archive | "" (not sorted yet), only for jobs,
education, projects, achievements and certificates (`profile.tiers`); `tier_by` is hal | person | "".

A conflict (`CONFLICT`):

    {"id": 1, "kind": "field", "item_ids": [3], "field": "start",
     "values": [{"value": "2024-05", "words": "May 2024",
                 "sources": [{"source_rel": "Sources/CVs and letters/CV.md", "doc_type": "cv", "quote": "..."}]},
                {"value": "2024-06", "words": "June 2024", "sources": [...]}],
     "why": "Your files give ... different start dates: May 2024 in your CV ...; June 2024 in ...",
     "state": "open", "resolution": null, "choices": ["value", "keep_all"]}

`kind` is field | dates | overlap | gap; `values` are best-ranked first and empty for overlap and gap;
`choices` say which bodies the resolve route takes. The overview's conflicts (`NAMED_CONFLICT`) also carry
`"items": [{"id", "kind", "title", "when", "state"}]`, the items they name, so the Inconsistencies tab can
offer "keep this one" without reading every list.

Routes:

- `GET /api/v2/profile`: {"counts": {kind: {"total", "hal", "confirmed", "edited", "hidden"}},
  "kinds": [{"kind", "label", "n", "to_check"}], "basics": [ITEM], "links": [ITEM], "to_check": n,
  "conflicts": [NAMED_CONFLICT], "resolved": [NAMED_CONFLICT]}. `n` leaves out hidden items; basics and links
  are the ones not hidden; `resolved` holds the settled conflicts, newest first.
- `GET /api/v2/profile/{kind}`: {"kind", "label", "items": [ITEM], "conflicts": [CONFLICT]}, every item of
  that kind (hidden ones too, last of all), jobs and education newest first, skills by name.
- `PUT /api/v2/profile/items/{id}` {"fields": {...}}: changes those fields ("" or [] clears one); the item
  becomes edited. Returns ITEM.
- `POST /api/v2/profile/items/{id}/confirm` and `.../hide`: return ITEM.
- `POST /api/v2/profile/items` {"kind", "fields"}: adds an item by hand (state edited, origin you); ITEM.
- `DELETE /api/v2/profile/items/{id}`: only for an item added by hand; {"ok": true}.
- `POST /api/v2/profile/conflicts/{id}/resolve` {"value": "..."} | {"keep_item": 3} | {"keep_all": true}:
  returns CONFLICT, now resolved.
- `GET /api/v2/profile/check`: the Check your record queue (`check.queue`): {"conflicts": [NAMED_CONFLICT],
  "cards": [CARD], "more": [CARD], "done": n, "total": n}. A card (`CARD`) is
  {"type": "item" | "skills", "key", "source_rel", "file_id", "items": [ITEM], "best": [evidence id]}: an item
  card holds one item and no file (`source_rel` "", `file_id` null); a skills card holds the skills that rest
  most on one file. `best` gives, for each item, the id of the source whose quote to show.
  `more` is what the page keeps under "Show more"; `done` of `total` is the progress bar.
- `PUT /api/v2/profile/items/{id}/tier` {"tier": "archive"}: the person's tier for an experience, which Hal
  never changes; ITEM. A tier that is not core, supporting or archive, or an item with no tier, is 400.
- `POST /api/v2/profile/tiers`: starts sorting the experiences again (action `profile.tiers`);
  {"action_id"}, or 409 while one is running.
- `POST /api/v2/profile/confirm-many` and `.../hide-many` {"ids": [3, 4]}: changes them all with one event;
  {"ids": [3, 4]}. An empty list is 400; an unknown id is 404 and changes nothing.
"""
from __future__ import annotations

from jobhunt.core import events, runner
from jobhunt.evidence.routes import file_id
from jobhunt.profile import actions, check, conflicts, store, tiers
from jobhunt.web.router import BadRequest, Conflict, NotFound, Request, route

PREFIX = "/api/v2/profile"
LABELS = {"job": "Work history", "education": "Education", "skill": "Skills", "project": "Projects",
          "achievement": "Achievements", "certificate": "Certificates", "basic": "About you", "link": "Links"}


def with_files(item: dict) -> dict:
    """An item with each source's `file_id`, so the page can open the file it came from."""
    return {**item, "evidence": [{**e, "file_id": file_id(e["source_rel"])} for e in item["evidence"]]}


def named(con, found: list[dict]) -> list[dict]:
    """Conflicts with the items they name (id, kind, title, when, state); an item since deleted is left out."""
    rows = {}
    for item_id in sorted({i for c in found for i in c["item_ids"]}):
        try:
            rows[item_id] = store.item(con, item_id)
        except LookupError:
            continue
    return [{**c, "items": [{k: rows[i][k] for k in ("id", "kind", "title", "when", "state")}
                            for i in c["item_ids"] if i in rows]} for c in found]


def _visible_first(items: list[dict]) -> list[dict]:
    return [i for i in items if i["state"] != "hidden"] + [i for i in items if i["state"] == "hidden"]


@route("GET", PREFIX)
def overview(req: Request) -> dict:
    """Counts of each kind, the basics and links, and every open conflict."""
    counts = store.counts(req.con)
    return {"counts": counts,
            "kinds": [{"kind": k, "label": LABELS[k], "n": c["total"] - c["hidden"], "to_check": c["hal"]}
                      for k, c in counts.items()],
            "basics": [with_files(i) for i in store.items(req.con, "basic", hidden=False)],
            "links": [with_files(i) for i in store.items(req.con, "link", hidden=False)],
            "to_check": sum(c["hal"] for c in counts.values()),
            "conflicts": named(req.con, conflicts.listing(req.con)),
            "resolved": named(req.con, conflicts.listing(req.con, "resolved")[::-1])}


# Registered before "/{kind}", which would otherwise take "check" as a kind: the router tries routes in the order
# they were added.
@route("GET", PREFIX + "/check")
def check_queue(req: Request) -> dict:
    """What is left to check, conflicts first, for the Check your record page."""
    found = check.queue(req.con)
    return {"conflicts": named(req.con, found["conflicts"]), "cards": _cards(found["cards"]),
            "more": _cards(found["more"]), "done": found["done"], "total": found["total"]}


def _cards(cards: list[dict]) -> list[dict]:
    return [{**c, "file_id": file_id(c["source_rel"]) if c["source_rel"] else None,
             "items": [with_files(i) for i in c["items"]]} for c in cards]


@route("GET", PREFIX + "/{kind}")
def of_kind(req: Request) -> dict:
    """Every item of one kind with its sources and conflicts."""
    kind = req.params["kind"]
    if kind not in store.KINDS:
        raise NotFound("Your profile has no such list.")
    found = _visible_first(store.items(req.con, kind))
    ids = {i["id"] for i in found}
    return {"kind": kind, "label": LABELS[kind], "items": [with_files(i) for i in found],
            "conflicts": [c for c in conflicts.listing(req.con) if ids & set(c["item_ids"])]}


def _changed(item: dict) -> dict:
    events.publish("profile", item_id=item["id"])
    return with_files(item)


def _fields(body) -> dict:
    fields = (body or {}).get("fields") if isinstance(body, dict) else None
    if not isinstance(fields, dict):
        raise BadRequest("Send the fields to change.", field="fields")
    return fields


@route("PUT", PREFIX + "/items/{item_id}")
def edit(req: Request) -> dict:
    """Changes an item's fields; it becomes the person's own."""
    try:
        return _changed(store.edit(req.con, req.params["item_id"], _fields(req.body)))
    except ValueError as e:
        raise BadRequest(str(e), field="fields") from e


@route("POST", PREFIX + "/items/{item_id}/confirm")
def confirm(req: Request) -> dict:
    """The person checked this item and it is right."""
    return _changed(store.confirm(req.con, req.params["item_id"]))


@route("PUT", PREFIX + "/items/{item_id}/tier")
def set_tier(req: Request) -> dict:
    """The person puts an experience in a tier; Hal never changes it after this."""
    tier = str((req.body or {}).get("tier") or "") if isinstance(req.body, dict) else ""
    try:
        return _changed(tiers.set_tier(req.con, req.params["item_id"], tier))
    except ValueError as e:
        raise BadRequest(str(e), field="tier") from e


@route("POST", PREFIX + "/tiers")
def sort_tiers(req: Request) -> dict:
    """Starts sorting the experiences again; {"action_id"}. 409 while a sort is already running."""
    try:
        return {"action_id": runner.start(actions.KIND, session_id=getattr(req.session, "id", None),
                                          remote=req.remote)}
    except RuntimeError as e:
        raise Conflict("Hal is sorting your experiences already.") from e


@route("POST", PREFIX + "/items/{item_id}/hide")
def hide(req: Request) -> dict:
    """Keeps the item out of documents; it stays hidden when its file is read again."""
    return _changed(store.hide(req.con, req.params["item_id"]))


def _many(req: Request, change) -> dict:
    ids = req.body.get("ids") if isinstance(req.body, dict) else None
    try:
        done = change(req.con, ids)
    except ValueError as e:
        raise BadRequest(str(e), field="ids") from e
    events.publish("profile", item_ids=done)
    return {"ids": done}


@route("POST", PREFIX + "/confirm-many")
def confirm_many(req: Request) -> dict:
    """The person kept several items at once (a group of skills)."""
    return _many(req, store.confirm_many)


@route("POST", PREFIX + "/hide-many")
def hide_many(req: Request) -> dict:
    """The person said none of these items are theirs."""
    return _many(req, store.hide_many)


@route("POST", PREFIX + "/items")
def add(req: Request) -> dict:
    """Adds an item the person typed in."""
    kind = str((req.body or {}).get("kind") or "") if isinstance(req.body, dict) else ""
    try:
        return _changed(store.add_by_hand(req.con, kind, _fields(req.body)))
    except ValueError as e:
        raise BadRequest(str(e), field="kind" if kind not in store.KINDS else "fields") from e


@route("DELETE", PREFIX + "/items/{item_id}")
def delete(req: Request) -> None:
    """Deletes an item the person added by hand."""
    store.delete(req.con, req.params["item_id"])
    events.publish("profile", item_id=req.params["item_id"])


@route("POST", PREFIX + "/conflicts/{conflict_id}/resolve")
def resolve(req: Request) -> dict:
    """Settles a conflict with the right value, the item to keep, or "keep them all"."""
    body = req.body if isinstance(req.body, dict) else {}
    try:
        done = conflicts.resolve(req.con, req.params["conflict_id"], value=body.get("value"),
                                 keep_item=body.get("keep_item"), keep_all=bool(body.get("keep_all")))
    except ValueError as e:
        raise BadRequest(str(e)) from e
    events.publish("profile", conflict_id=done["id"])
    return done
