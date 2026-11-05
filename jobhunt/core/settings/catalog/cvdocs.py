"""Settings group "CV and documents", with the record folder rows shown in it.

CV length, tone and entry style live on the CV type and are edited in My CV, not here.
The two folder rows default to an empty path, which means the folder `core.paths` works out for this
computer (`applications_dir()`, `record_default()`), so a default never pins one machine's path.
"""

from __future__ import annotations

from jobhunt.core.settings.catalog._shared import choices, safe
from jobhunt.core.settings.registry import Setting, register

TAILOR_HELP = ("No: the same CV for every job. A little: reorder and adjust the profile line. "
               "More: also rewrite bullets from your evidence.")

register(
    Setting("cvdocs.route", "link", None, "How your CV is made", to="cv", order=10),
    Setting("cvdocs.tailor", "seg", "little", "Adjust your CV for each job", help=TAILOR_HELP, order=20,
            choices=choices(("no", "No"), ("little", "A little"), ("more", "More")),
            default_fn=lambda get: "no" if safe(get, "cvdocs.route") == "as_is" else "little"),
    Setting("cvdocs.pick_first", "bool", False, "Ask me what the job cares about first", order=30),
    Setting("cvdocs.spelling", "seg", "en-GB", "Spelling", order=40,
            choices=choices(("en-GB", "UK English"), ("en-US", "US English"))),
    Setting("cvdocs.file_name", "text", "{name} CV.pdf", "File names employers see", order=50,
            help="{name} is replaced by your name."),
    Setting("cvdocs.letter_file_name", "text", "{name} Cover Letter.pdf", "Cover letter file name", advanced=True,
            order=60),
    Setting("cvdocs.letter_tone", "seg", "plain", "Cover letter tone", order=70,
            choices=choices(("plain", "Plain"), ("formal", "Formal"), ("warm", "Warm"))),
    Setting("cvdocs.letter_words", "seg", 250, "Cover letter length", order=80,
            choices=choices((150, "About 150 words"), (250, "About 250 words"), (400, "About 400 words"))),
    Setting("cvdocs.signoff", "text", "Kind regards", "Sign-off", order=90),
    Setting("cvdocs.never_use", "tags", ["passionate", "team player", "go-getter"], "Never use", order=100,
            help="Words and phrases Hal must avoid."),
    Setting("cvdocs.voice", "bool", True, "Match my own writing", order=110,
            help="Uses your own writing as a guide to your voice: your edits, answers you rewrote, and the "
                 "letters and CVs in your record."),
    Setting("cvdocs.folder", "path", "", "Where documents are saved", machine=True, remote="local_only", order=120,
            help="Leave empty for Documents, Hal Employment, Applications."),
    Setting("record.folder", "path", "", "Your record folder", group="cvdocs", machine=True, remote="local_only",
            order=130, help="Leave empty for Documents, Employment Record."),
    Setting("record.ocr", "bool", True, "Read photos and scans of certificates", group="cvdocs", order=140),
    Setting("record.search", "choice", "words", "How Hal searches your record", group="cvdocs", advanced=True,
            order=150, choices=choices(
                ("words", "By words"), ("meaning", "By meaning (needs the AI on this computer)"))),
    Setting("record.private_folders", "tags", ["Private"], "Folders in your record never sent to the AI",
            group="cvdocs", order=160),
    Setting("record.drop_folder", "path", "", "Drop folder for your record", group="cvdocs", machine=True,
            remote="local_only", order=170,
            help="Anything you put here is read into your record. Leave empty for the +Inbox folder in it."),
    Setting("record.copy_originals", "bool", True, "Keep a copy of each file Hal reads in", group="cvdocs",
            order=180, help="Copies go in Attachments in your record. Very large files are linked, not copied."),
    Setting("record.privacy_check", "bool", True, "Keep private documents out of your record", group="cvdocs",
            order=185, help="Hal leaves out files that look like bank details, passports and ID cards, payslips "
                            "and tax forms, medical letters, or passwords and keys. They are not filed, not sent "
                            "to the AI and give Hal no facts. Hal lists them by name only, each with Add it "
                            "anyway."),
    Setting("record.ingest_vision", "bool", False, "Read certificates and artwork in pictures with the AI",
            group="cvdocs", advanced=True, order=190,
            help="The words in them are read first. Other pictures are listed by name and date only."),
    Setting("record.read_all", "bool", False, "Read every document with the AI", group="cvdocs", advanced=True,
            order=195, help="Hal reads CVs, transcripts, coursework and other rich documents with the AI, and files "
                            "notes and slides by their headings and words. Turn this on to have the AI read and "
                            "summarise those too. Reading takes much longer."),
)
