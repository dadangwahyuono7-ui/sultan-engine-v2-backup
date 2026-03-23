from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.runtime.production_supervisor import assess_heartbeat, build_child_command


def _normalized_path(value: str) -> str:
    return value.replace("\\", "/")


class ProductionSupervisorTests(unittest.TestCase):
    def test_assess_heartbeat_marks_stale_when_old(self) -> None:
        heartbeat = {"updated_at": "2026-03-20T00:00:00+00:00"}
        outcome = assess_heartbeat(
            heartbeat,
            now_utc=datetime(2026, 3, 20, 0, 5, tzinfo=timezone.utc),
            timeout_seconds=60.0,
        )

        self.assertTrue(outcome.stale)
        self.assertIsNotNone(outcome.reason)
        self.assertIn("stale", outcome.reason or "")

    def test_assess_heartbeat_handles_missing_payload(self) -> None:
        outcome = assess_heartbeat(
            None,
            now_utc=datetime(2026, 3, 20, 0, 5, tzinfo=timezone.utc),
            timeout_seconds=60.0,
        )

        self.assertTrue(outcome.stale)
        self.assertEqual("Heartbeat missing", outcome.reason)

    def test_build_child_command_points_to_run_vps_stack(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        command = build_child_command(
            project_root=project_root,
            base_symbol="XAUUSD",
            execution_mode=ExecutionMode.LIVE,
            auto_execute=False,
        )

        self.assertEqual("XAUUSD", command[2])
        self.assertEqual("LIVE", command[3])
        self.assertEqual("0", command[4])
        self.assertTrue(_normalized_path(command[1]).endswith("sultan_engine_v2/run_vps_stack.py"))

    def test_build_child_command_supports_custom_script(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        command = build_child_command(
            project_root=project_root,
            base_symbol="BTCUSDT",
            execution_mode=ExecutionMode.PAPER,
            auto_execute=True,
            child_script="run_binance_stack.py",
        )

        self.assertEqual("BTCUSDT", command[2])
        self.assertEqual("PAPER", command[3])
        self.assertEqual("1", command[4])
        self.assertTrue(_normalized_path(command[1]).endswith("sultan_engine_v2/run_binance_stack.py"))

    def test_build_child_command_accepts_engine_root_without_duplication(self) -> None:
        engine_root = Path(__file__).resolve().parents[2] / "sultan_engine_v2"
        command = build_child_command(
            project_root=engine_root,
            base_symbol="BTCUSDT",
            execution_mode=ExecutionMode.PAPER,
            auto_execute=True,
            child_script="run_binance_stack.py",
        )

        self.assertEqual("BTCUSDT", command[2])
        normalized = _normalized_path(command[1])
        self.assertTrue(normalized.endswith("sultan_engine_v2/run_binance_stack.py"))
        self.assertNotIn("sultan_engine_v2/sultan_engine_v2/run_binance_stack.py", normalized)


if __name__ == "__main__":
    unittest.main()
