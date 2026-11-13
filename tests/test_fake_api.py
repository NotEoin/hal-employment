"""The fake server's made-up replies have the real server's shapes, so the shell's tests draw what Hal sends.

`support/fake_api.py` answers the UI shell's tests with made-up replies. Each is checked here against the real
route, served from the made-up world of `support/world.py`: the same keys and value types at the top level and
one level down, a list standing for its first item. Deeper levels and the values themselves are the page tests'
business. `null` on either side matches any type, since most fields may be empty.
"""
import unittest
from urllib.parse import quote

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support import fake_api
from support.api import HalServer
from support.world import World, job_id

from jobhunt import db

APP_KEY = "sorrel-ready"   # the world's one application, with a letter waiting
LEVELS = 2                 # the top level and one level down
SKIP = {
    "/record/sort/1": "needs a finished sort: a dump folder read through the fake AI (test_sections_sort.py)",
}
# Objects keyed by data (setting names, contact fields), not by a fixed set of field names.
DATA_KEYS = {"/settings.values", "/settings.display", "/settings.secrets", "/goal.values",
             "/cv/document.contact_snapshot", "/cv/document.extras"}
# Routes the made-up world leaves empty, so only the reply's own type is checked.
EMPTY_HERE = {
    "/memory/waiting": "nothing learned yet",
    "/memory/learned": "nothing learned yet",
    "/goal/versions": "the goal was never saved",
    "/record/sources": "no record sources added",
    "/prompts": "the made-up reply is empty too",
}


def kind(value) -> str | None:
    """The JSON type of `value`; `None` for null, and one name for ints and floats."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    return {int: "number", float: "number", str: "string", list: "list", dict: "object"}[type(value)]


def drift(made_up, real, where: str, level: int = 0) -> list[str]:
    """Where `made_up` differs from `real` in keys or value types, down to `LEVELS` levels."""
    if kind(made_up) is None or kind(real) is None:
        return []
    if kind(made_up) != kind(real):
        return [f"{where}: made up {kind(made_up)}, real {kind(real)}"]
    if isinstance(made_up, list):
        return drift(made_up[0], real[0], f"{where}[0]", level) if made_up and real else []
    if not isinstance(made_up, dict) or level >= LEVELS:
        return []
    out = []
    if where not in DATA_KEYS and sorted(made_up) != sorted(real):
        out.append(f"{where}: only made up {sorted(set(made_up) - set(real))}, "
                   f"only real {sorted(set(real) - set(made_up))}")
    for key in sorted(set(made_up) & set(real)):
        out += drift(made_up[key], real[key], f"{where}.{key}", level + 1)
    return out


class MadeUpReplies(unittest.TestCase):
    def test_every_made_up_reply_has_the_real_routes_keys_and_types(self):
        world = self.enterContext(World())
        db.set_status(world.con, job_id(APP_KEY), "applying", "chosen from the queue")
        world.con.commit()
        c = self.enterContext(HalServer()).signed_in()
        app = quote(job_id(APP_KEY), safe="")
        # The fake's ids are made up; the real server is asked for the world's own.
        real_path = {"/applications/1": f"/applications/{app}", "/documents/1": f"/documents/{app}",
                     "/jobs/demo:1": f"/jobs/{app}", "/search": "/search?q=a"}
        made_up = {"/session": fake_api.made_up_session(), "/status": fake_api.made_up_status(),
                   "/home": fake_api.MADE_UP_HOME, "/search": fake_api.MADE_UP_SEARCH, **fake_api.MADE_UP_PAGES}
        self.assertEqual(set(SKIP) - set(made_up), set(), "a skipped route the fake no longer serves")
        for path, reply in made_up.items():
            if path in SKIP:
                continue
            with self.subTest(path):
                r = c.get("/api/v2" + real_path.get(path, path))
                self.assertEqual(r.status, 200, r.text[:200])
                real = r.json()
                self.assertEqual(drift(reply, real, path), [])
                if isinstance(real, list) and not (real and reply):
                    self.assertIn(path, EMPTY_HERE, "an empty list on one side leaves its items unchecked")


if __name__ == "__main__":
    unittest.main()
