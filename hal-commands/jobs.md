---
description: Job search check-in: what needs Firstname today, new matches, and the application pipeline
---

You are helping Firstname with his job search. The pipeline is a CLI in
`~/employment-helper` (run commands from there with `python3 -m jobhunt ...`).

1. Run `python3 -m jobhunt mail -q` to pull in new recruiting email, then
   `python3 -m jobhunt inbox -n 15`, `python3 -m jobhunt todo`, `python3 -m jobhunt queue`
   and `python3 -m jobhunt stats`.
2. Read `~/vaults/Employment/Areas/Job Search/Pipeline.md`.
3. Report, briefly and in this order:
   - **Needs you**: assessments, interviews, offers, emails from real people, follow-ups due. For each, say what to do and by when.
   - **Changes** since yesterday (new rejections, confirmations).
   - **Today's shortlist**: from `python3 -m jobhunt list -m 60 -n 15`, pick up to 10 worth applying to today and say why in one line each. Prefer City/Region and London roles, graduate/junior level, small companies, and fresh postings.
4. Ask which to shortlist. For each one he picks, run `python3 -m jobhunt set <id> shortlisted`;
   Hal Employment drafts their packs and puts them at the front of the queue.
5. Remind him that applications are made in Hal Employment at http://127.0.0.1:8737/
   (queue, documents, form filling and to-dos).

Rules: never submit an application or send an email on his behalf: draft, and he sends.
Status changes go through `python3 -m jobhunt set <id> <status> [note]`.
$ARGUMENTS
