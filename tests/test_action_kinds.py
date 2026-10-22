"""Gate G9: every action kind, run from its example in the made-up world with fakes, records an Activity row
with a why and its steps, and Activity and actions survive a restart.

Each area registers its kinds with an `example(world) -> params`; this harness finds every kind through
`runner.load_kinds()`, so a new kind is covered the moment it lands.
"""
import unittest

from support.world import World

from jobhunt import db
from jobhunt.core import runner


def problems(kind: runner.ActionKind, world: World) -> list[str]:
    """What is wrong with one kind when it runs from its example; empty when it keeps every promise."""
    if kind.example is None:
        return [f"{kind.kind} has no example for gate G9"]
    params = kind.example(world)
    done = runner.run(kind.kind, params=params, subject=params.pop("_subject", None) if params else None)
    out = []
    if done["state"] != "done":
        out.append(f"{kind.kind} ended {done['state']}: {done['error']}")
    if not done["steps"]:
        out.append(f"{kind.kind} took no steps")
    con = db.connect()
    try:
        row = con.execute("SELECT why, running FROM activity WHERE action_id = ?", (done["id"],)).fetchone()
    finally:
        con.close()
    if row is None or not (row["why"] or "").strip():
        out.append(f"{kind.kind} left no Activity row with a why")
    elif row["running"]:
        out.append(f"{kind.kind} left its Activity row running")
    return out


class EveryKind(unittest.TestCase):
    def test_every_action_kind_records_activity_with_a_why_and_its_steps(self):
        kinds = runner.load_kinds()
        for name, kind in sorted(kinds.items()):
            with self.subTest(kind=name), World() as w:
                self.assertEqual(problems(kind, w), [])
                runner.flush()
                # A restart keeps the row and its steps.
                self.assertEqual(runner.recover(), 0)
                self.assertGreater(w.con.execute("SELECT count(*) FROM action_steps").fetchone()[0], 0)

    def test_the_harness_catches_a_kind_without_an_example_or_steps(self):
        runner.action("test.no_example", area="find", label="Looking", why="Because")(lambda ctx: None)
        self.addCleanup(runner.forget, "test.no_example")
        runner.action("test.no_steps", area="find", label="Looking", why="Because", example=lambda w: {})(
            lambda ctx: None)
        self.addCleanup(runner.forget, "test.no_steps")
        runner.action("test.good", area="find", label="Looking at {title}", why="Because",
                      example=lambda w: {"title": "Museum Assistant"})(lambda ctx, title: ctx.step(f"reading {title}"))
        self.addCleanup(runner.forget, "test.good")
        with World() as w:
            self.assertEqual(problems(runner.KINDS["test.no_example"], w),
                             ["test.no_example has no example for gate G9"])
            self.assertEqual(problems(runner.KINDS["test.no_steps"], w), ["test.no_steps took no steps"])
            self.assertEqual(problems(runner.KINDS["test.good"], w), [])


if __name__ == "__main__":
    unittest.main()
