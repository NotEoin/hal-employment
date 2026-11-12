"""The sectioned record: each tab of Your record takes its own files, links and folders, and says what they are.

The person adds each source to a section (CVs, letters, a job's documents, coursework, lecture material,
official papers, projects, documents), and the section says what the source is and whose it is, so the AI
only takes facts out of it within that frame (sections plan, 2026-11-09).

- `registry`: the sections, their kinds of document, roles, ranks and readers.
- `readers`: what a reader is given and returns, the generic `document` reader, and stubs for later phases.
- `store`: the `record_sources` rows (migration 0028).
- `pipeline`: keeping an upload or link, reading it into a note and the profile, and removing it.
- `wrong_place`: the cheap hint that a source may belong in another section.
- `actions` and `routes`: the `record.sources` action and the API of the page's "Add" panels.
"""
