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
                   "verified": true, "found_at": "2026-11-05T09:00:00Z", "source_id": null, "place": "",
                   "line": null, "section": "", "doc_kind": "", "strength": "", "strength_words": ""}],
     "conflicts": [CONFLICT, ...], "can_delete": false,
     "created": "2026-11-05T09:00:00Z", "updated": "2026-11-05T09:00:00Z",
     "tier": "core", "tier_by": "hal", "tier_why": "Your main work in your field", "tier_words": "Lead with this",
     "context": "work", "context_by": "hal", "context_words": "At work", "merged_into": null, "merged_why": "",
     "star": false, "star_by": "", "can_star": true}

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
`context` is degree | team | work | personal | "" (not known), for the same kinds (`profile.contexts`), with
`context_by` hal | person | "" and `context_words` the page's words for it ("Part of my degree",
"University team project", "At work", "In my own time"). `merged_into` is the id of the card a hidden card
was folded into by Hal (`store.fold_away`) or the person (`store.same_as`), else null, and `merged_why` the
words on why ("Part of Lantern workspace", "You said it is the same as Tide gauge"). `star` is the person's
star on a project or job (`can_star`), with `star_by` "you" once they set it. A source's `source_id` is the
source of the record it came from (`jobhunt.sections`), with `place` (a file inside it or a URL) and `line`,
or null and "" for facts the bulk read found. A skill's source says how strongly it shows the skill (`strength`
and `strength_words`, `profile.strength`; "" for other kinds), and `star_by` is "coursework" for a project card
starred through a piece of coursework in the showcase.

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
- `GET /api/v2/profile/check`: the best experiences (`check.best`) and the Check your record queue
  (`check.queue`): {"best": [ITEM], "archived": [ITEM], "conflicts": [NAMED_CONFLICT],
  "cards": [CARD], "more": [CARD], "studied": [ITEM], "mentioned": [ITEM],
  "merged": [{"item": ITEM, "merged": [ITEM], "by": "hal" | "person"}],
  "hidden_twins": [{"item": ITEM, "hidden": [ITEM]}], "done": n, "total": n}. A card (`CARD`) is
  {"type": "item" | "skills", "key", "source_rel", "file_id", "items": [ITEM], "best": [evidence id]}: an item
  card holds one item and no file (`source_rel` "", `file_id` null); a skills card holds the skills that rest
  most on one file. `best` gives, for each item, the id of the source whose quote to show.
  `more` is what the page keeps under "Show more"; `studied` the skills only course material names
  (`store.is_studied`), shown apart; `mentioned` the other skills that are not on a CV, checked or a tool of a
  core or supporting project (`store.is_usable`), folded away; `merged` each card Hal (`by` "hal") or the
  person ("person") merged others into (`duplicates.listing`), with the cards hidden into it; `hidden_twins`
  each shown card with the cards the person hid whose files are all its repository or project files
  (`duplicates.hidden_twins`), offered as maybe the same; `done` of `total` is the progress bar.
- `POST /api/v2/profile/items/{id}/unmerge`: takes the cards Hal or the person merged into this one back out,
  as they were, and Hal never merges them on its own again (`duplicates.undo`); ITEM. A card nothing was
  merged into is 400.
- `POST /api/v2/profile/items/{id}/same-as` {"into": 4}: the person says card {id} is the same as card 4,
  which they keep: {id} is merged into it, checked or not (`duplicates.same_as`), and Unmerge undoes it.
  Returns the kept card's ITEM. An unknown card is 404; `into` missing or not a number, the same card, two
  kinds, a kind that is no experience, or a card already merged is 400.
- `PUT /api/v2/profile/items/{id}/tier` {"tier": "archive"}: the person's tier for an experience, which Hal
  never changes; ITEM. A tier that is not core, supporting or archive, or an item with no tier, is 400.
- `PUT /api/v2/profile/items/{id}/star` {"star": true}: the person's star on a project or job
  (`store.set_star`); ITEM. Any other kind, or `star` not true or false, is 400.
- `PUT /api/v2/profile/items/{id}/context` {"context": "personal"}: where the person says an experience was
  done, which Hal never changes; ITEM. A context that is not one of the four, or an item with none, is 400.
- `POST /api/v2/profile/tiers`: starts sorting the experiences again (action `profile.tiers`);
  {"action_id"}, or 409 while one is running.
- `POST /api/v2/profile/confirm-many` and `.../hide-many` {"ids": [3, 4]}: changes them all with one event;
  {"ids": [3, 4]}. An empty list is 400; an unknown id is 404 and changes nothing.
"""
from __future__ import annotations

from jobhunt.core import events, runner
from jobhunt.evidence.routes import file_id
from jobhunt.profile import actions, check, conflicts, contexts, duplicates, store, tiers
from jobhunt.profile import cvs as section_cvs
from jobhunt.profile import writing as profile_writing
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
    # skills only course material names are folded apart and never asked about (`check.queue`'s "studied")
    studied = {k: store.studied_count(req.con) if k == "skill" else 0 for k in counts}
    return {"counts": counts,
            "kinds": [{"kind": k, "label": LABELS[k], "n": c["total"] - c["hidden"] - studied[k],
                       "to_check": c["hal"] - studied[k], "studied": studied[k]} for k, c in counts.items()],
            "basics": [with_files(i) for i in store.items(req.con, "basic", hidden=False)],
            "links": [with_files(i) for i in store.items(req.con, "link", hidden=False)],
            "to_check": sum(c["hal"] for c in counts.values()) - sum(studied.values()),
            "conflicts": named(req.con, conflicts.listing(req.con)),
            "resolved": named(req.con, conflicts.listing(req.con, "resolved")[::-1])}


# Registered before "/{kind}", which would otherwise take "check" as a kind: the router tries routes in the order
# they were added.
@route("GET", PREFIX + "/check")
def check_queue(req: Request) -> dict:
    """The best experiences, then what is left to check, conflicts first, for the Check your record page."""
    found = check.queue(req.con)
    top = check.best(req.con)
    return {"best": [with_files(i) for i in top["best"]], "archived": [with_files(i) for i in top["archived"]],
            "conflicts": named(req.con, found["conflicts"]), "cards": _cards(found["cards"]),
            "more": _cards(found["more"]), "studied": [with_files(i) for i in found["studied"]],
            "mentioned": [with_files(i) for i in found["mentioned"]],
            "merged": [{"item": with_files(m["item"]), "merged": [with_files(i) for i in m["merged"]], "by": m["by"]}
                       for m in duplicates.listing(req.con)],
            "hidden_twins": [{"item": with_files(t["item"]), "hidden": [with_files(i) for i in t["hidden"]]}
                             for t in duplicates.hidden_twins(req.con)],
            "done": found["done"], "total": found["total"]}


def _cards(cards: list[dict]) -> list[dict]:
    return [{**c, "file_id": file_id(c["source_rel"]) if c["source_rel"] else None,
             "items": [with_files(i) for i in c["items"]]} for c in cards]


@route("GET", PREFIX + "/{kind}")
def of_kind(req: Request) -> dict:
    """Every item of one kind with its sources and conflicts, and for each card what only an older CV of the
    person's gives it (`older_cv`, {field: [value, ...]}: `profile.writing.older_cv_only`), what only a plan
    gives it (`planned`, {field: {value: document}}: `profile.writing.planned_only`), whether only CVs give a
    project (`cv_only`) and, on each source, whether it is an older CV of the CVs section (`from_older_cv`)."""
    kind = req.params["kind"]
    if kind not in store.KINDS:
        raise NotFound("Your profile has no such list.")
    found = _visible_first(store.items(req.con, kind))
    ids = {i["id"] for i in found}
    cvs = profile_writing.CvAuthority.of(req.con)
    older = section_cvs.older_sources(req.con)
    shown = [with_files(i) for i in found]
    return {"kind": kind, "label": LABELS[kind],
            "items": [{**i, "older_cv": profile_writing.older_cv_only(req.con, i, cvs),
                       "planned": profile_writing.planned_only(req.con, i), "cv_only": section_cvs.cv_only(i),
                       "evidence": [{**e, "from_older_cv": e["source_id"] in older} for e in i["evidence"]]}
                      for i in shown],
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


@route("POST", PREFIX + "/items/{item_id}/unmerge")
def unmerge(req: Request) -> dict:
    """Takes the cards Hal merged into this one back out; Hal never merges them on its own again."""
    try:
        return _changed(duplicates.undo(req.con, req.params["item_id"]))
    except ValueError as e:
        raise BadRequest(str(e)) from e


@route("POST", PREFIX + "/items/{item_id}/same-as")
def same_as(req: Request) -> dict:
    """The person says this card is the same as another, which they keep: this one is merged into it."""
    into = req.body.get("into") if isinstance(req.body, dict) else None
    if not isinstance(into, int) or isinstance(into, bool):
        raise BadRequest("Pick the card this one is the same as.", field="into")
    try:
        return _changed(duplicates.same_as(req.con, req.params["item_id"], into))
    except ValueError as e:
        raise BadRequest(str(e), field="into") from e


@route("PUT", PREFIX + "/items/{item_id}/tier")
def set_tier(req: Request) -> dict:
    """The person puts an experience in a tier; Hal never changes it after this."""
    tier = str((req.body or {}).get("tier") or "") if isinstance(req.body, dict) else ""
    try:
        return _changed(tiers.set_tier(req.con, req.params["item_id"], tier))
    except ValueError as e:
        raise BadRequest(str(e), field="tier") from e


@route("PUT", PREFIX + "/items/{item_id}/star")
def set_star(req: Request) -> dict:
    """The person stars a project or job, or takes the star off; no read changes it after this."""
    on = (req.body or {}).get("star") if isinstance(req.body, dict) else None
    try:
        return _changed(store.set_star(req.con, req.params["item_id"], on))
    except ValueError as e:
        raise BadRequest(str(e), field="star") from e


@route("PUT", PREFIX + "/items/{item_id}/context")
def set_context(req: Request) -> dict:
    """The person says where an experience was done; Hal never changes it after this."""
    context = str((req.body or {}).get("context") or "") if isinstance(req.body, dict) else ""
    try:
        return _changed(contexts.set_context(req.con, req.params["item_id"], context))
    except ValueError as e:
        raise BadRequest(str(e), field="context") from e


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
