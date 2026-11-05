"""The settings reading a person's material adds, shown with the record rows in "CV and documents".

The GitHub token is optional: GitHub's public API answers without one, but only 60 requests an hour, which a
person with many repositories uses up in one read.
"""
from __future__ import annotations

from ..core.settings.registry import Setting, register

WEB_PAGES = 30          # enough for a portfolio or a blog's recent posts, few enough to read in a minute
WEB_PAGES_MAX = 200     # a bigger site is not one person's own work
ORDER = 170             # after "Folders in your record never sent to the AI"

register(
    Setting("record.web_pages", "int", WEB_PAGES, "Pages Hal reads from each web link", group="cvdocs",
            min=1, max=WEB_PAGES_MAX, unit="pages", advanced=True, order=ORDER,
            help="For your own site or blog, Hal reads the page you give and the pages under it, up to this many."),
    Setting("record.github_token", "secret", None, "GitHub token", secret_name="record.github_token",
            group="cvdocs", advanced=True, order=ORDER + 10,
            help="Optional. Lets Hal read more of your GitHub at once. A token with no permissions is enough, "
                 "because Hal reads only what is public."),
)
