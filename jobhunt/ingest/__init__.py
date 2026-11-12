"""The bulk ingest (legacy): whole folders, zips, a drop folder and web pages read into the record as notes.

Legacy since the sectioned record (sections plan, 2026-11-09): it guessed each file's kind and owner, which
crossed facts between people. New files go through the sections (`jobhunt.sections`) or "Sort a big folder
for me" (`sections.sorter`), which only proposes. The ingest is kept for records an older Hal built: "Read them
with the AI", "Tidy my record", a private file let in, and its discovery, which the sorter reuses.

One `record.ingest` action (`ingest/actions.py`) takes every input and works through it in stages, each kept
in the `ingest_items` table (`ingest/items.py`) so a restart carries on from the next item:

1. sources (`sources.py`): folders walked as they are, zips unpacked safely, the drop folder;
2. discover (`discover.py`): size, SHA-256, the kind by the first bytes, junk folders and binaries left out,
   cloud placeholders (all zero bytes) listed as not downloaded, exact copies linked to the first;
3. group (`group.py`): a code repository is one unit, a folder of one course's notes or slides another;
4. rank (`rank.py`): the person's richest documents (CV, transcript, letters) first, so facts arrive early;
5. convert (`convert.py`): `record.text_of`, so a document reads the same here as in the record;
6. understand (`understand.py`): the `ingest_doc` prompt for rich documents, with every quote checked in
   code, or tags from the path, headings and frequent words for light ones;
7. write (`notes.py`): `Sources/<Category>/<Title>.md` with list frontmatter (`frontmatter.py`), entity
   notes, an Index note per category and links to known titles.

Facts about the person go to `jobhunt.profile.store.add_found`; the ingest keeps no profile of its own.
"""
