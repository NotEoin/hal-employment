"""The gpu-light claim around a local AI call: class by priority, and no claim without the client."""
import contextlib
import os
import sys
import types
import unittest
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.ai import gpu


def fake_client(seen: list, fail: Exception | None = None) -> types.ModuleType:
    """A stand-in `gpu_light` that records each claim and its release."""
    mod = types.ModuleType("gpu_light")

    @contextlib.contextmanager
    def claim(name, **kw):
        if fail:
            raise fail
        seen.append(("claim", name, kw["cls"]))
        try:
            yield
        finally:
            seen.append(("release", name))

    mod.claim = claim
    return mod


class Claim(unittest.TestCase):
    def run_claim(self, priority: int, client: types.ModuleType | None) -> list:
        env = {"HAL_TEST_REAL_GPU_LIGHT": "1"}
        mods = {"gpu_light": client} if client else {"gpu_light": None}     # None makes the import fail
        with mock.patch.dict(os.environ, env), mock.patch.dict(sys.modules, mods), gpu.claim(priority):
            pass
        return []

    def test_a_call_the_person_waits_for_claims_the_card_as_interactive(self):
        seen: list = []
        self.run_claim(0, fake_client(seen))
        self.assertEqual(seen, [("claim", gpu.CLAIM_NAME, "interactive"), ("release", gpu.CLAIM_NAME)])

    def test_background_work_claims_the_card_as_batch(self):
        seen: list = []
        self.run_claim(gpu.BACKGROUND, fake_client(seen))
        self.assertEqual(seen[0], ("claim", gpu.CLAIM_NAME, "batch"))

    def test_without_the_client_the_call_goes_on_unclaimed(self):
        self.run_claim(0, None)

    def test_an_unreachable_scheduler_does_not_stop_the_call(self):
        ran = []
        with mock.patch.dict(os.environ, {"HAL_TEST_REAL_GPU_LIGHT": "1"}), \
                mock.patch.dict(sys.modules, {"gpu_light": fake_client([], OSError("no daemon"))}), \
                gpu.claim(0):
            ran.append(True)
        self.assertEqual(ran, [True])

    def test_a_test_run_never_reaches_a_live_scheduler(self):
        seen: list = []
        with mock.patch.dict(sys.modules, {"gpu_light": fake_client(seen)}), gpu.claim(0):
            pass
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
