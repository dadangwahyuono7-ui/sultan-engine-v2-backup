from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sultan_engine_v2.manager.operator_control import JsonOperatorControlStore


class OperatorControlStoreTests(unittest.TestCase):
    def test_pause_then_resume_updates_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = JsonOperatorControlStore(Path(temp_dir) / "operator_control.json")

            paused_state = store.record_command(
                "PAUSE",
                source="tester",
                message="pause dulu bro",
                interpreter="RULE",
                response="paused",
                reason="manual pause",
            )
            self.assertTrue(paused_state.paused)
            self.assertFalse(paused_state.allow_new_entries)
            self.assertEqual("manual pause", paused_state.pause_reason)
            self.assertEqual("PAUSE", paused_state.last_command)

            resumed_state = store.record_command(
                "RESUME",
                source="tester",
                message="lanjut lagi",
                interpreter="RULE",
                response="resumed",
                reason="manual resume",
            )
            self.assertFalse(resumed_state.paused)
            self.assertTrue(resumed_state.allow_new_entries)
            self.assertIsNone(resumed_state.pause_reason)
            self.assertEqual("RESUME", resumed_state.last_command)


if __name__ == "__main__":
    unittest.main()
