from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sultan_engine_v2.runtime.reset_runtime_state import build_reset_plan, execute_reset_plan


class LocalTestDir:
    def __init__(self, name: str) -> None:
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / f"{name}_{uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        rmtree(self.path, ignore_errors=True)


class ResetRuntimeStateTests(unittest.TestCase):
    def test_build_reset_plan_targets_runtime_state_without_touching_operator_state_by_default(self) -> None:
        with LocalTestDir("reset_runtime_plan") as temp_dir:
            runtime_dir = temp_dir / "runtime"
            (runtime_dir / "state" / "locks").mkdir(parents=True, exist_ok=True)
            (runtime_dir / "state" / "locks" / "vps_stack_xauusd.lock").write_text(
                json.dumps(
                    {
                        "service_name": "VPS_STACK_XAUUSD",
                        "pid": 12345,
                        "hostname": "test-host",
                        "acquired_at": "2026-03-21T00:00:00+00:00",
                        "command": "run_vps_stack.py",
                    }
                ),
                encoding="utf-8",
            )

            plan = build_reset_plan(
                runtime_dir=runtime_dir,
                mt5_symbol="XAUUSD",
                binance_symbol="BTCUSDT",
                timestamp=datetime(2026, 3, 21, tzinfo=timezone.utc),
                pid_checker=lambda pid: False,
            )

        relative_paths = {path.relative_to(runtime_dir).as_posix() for path in plan.target_paths}
        self.assertIn("state/binance_btcusdt_paper_account.json", relative_paths)
        self.assertIn("logs/binance_btcusdt_execution_ledger.json", relative_paths)
        self.assertIn("state/locks/vps_stack_xauusd.lock", relative_paths)
        self.assertNotIn("state/operator_control.json", relative_paths)
        self.assertEqual("reset_20260321T000000Z_xauusd_btcusdt", plan.archive_dir.name)

    def test_execute_reset_plan_moves_existing_files_into_archive(self) -> None:
        with LocalTestDir("reset_runtime_execute") as temp_dir:
            runtime_dir = temp_dir / "runtime"
            (runtime_dir / "state" / "locks").mkdir(parents=True, exist_ok=True)
            (runtime_dir / "logs").mkdir(parents=True, exist_ok=True)
            (runtime_dir / "history").mkdir(parents=True, exist_ok=True)

            snapshot_path = runtime_dir / "state" / "latest_snapshot.json"
            ledger_path = runtime_dir / "logs" / "binance_btcusdt_execution_ledger.json"
            paper_account_path = runtime_dir / "state" / "binance_btcusdt_paper_account.json"

            snapshot_path.write_text('{"status":"ok"}', encoding="utf-8")
            ledger_path.write_text("[]", encoding="utf-8")
            paper_account_path.write_text('{"balance":10000}', encoding="utf-8")

            plan = build_reset_plan(
                runtime_dir=runtime_dir,
                mt5_symbol="XAUUSD",
                binance_symbol="BTCUSDT",
                timestamp=datetime(2026, 3, 21, tzinfo=timezone.utc),
                pid_checker=lambda pid: False,
            )
            result = execute_reset_plan(plan)

            archived_snapshot = result.archive_dir / "state" / "latest_snapshot.json"
            archived_ledger = result.archive_dir / "logs" / "binance_btcusdt_execution_ledger.json"
            archived_paper = result.archive_dir / "state" / "binance_btcusdt_paper_account.json"

            self.assertFalse(snapshot_path.exists())
            self.assertFalse(ledger_path.exists())
            self.assertFalse(paper_account_path.exists())
            self.assertTrue(archived_snapshot.exists())
            self.assertTrue(archived_ledger.exists())
            self.assertTrue(archived_paper.exists())
            self.assertTrue(result.manifest_path.exists())
            self.assertTrue((runtime_dir / "state" / "locks").exists())
            self.assertTrue((runtime_dir / "logs").exists())
            self.assertTrue((runtime_dir / "history").exists())

    def test_execute_reset_plan_rejects_active_locks(self) -> None:
        with LocalTestDir("reset_runtime_active_lock") as temp_dir:
            runtime_dir = temp_dir / "runtime"
            (runtime_dir / "state" / "locks").mkdir(parents=True, exist_ok=True)
            (runtime_dir / "state" / "locks" / "production_stack_xauusd.lock").write_text(
                json.dumps(
                    {
                        "service_name": "PRODUCTION_STACK_XAUUSD",
                        "pid": 999,
                        "hostname": "test-host",
                        "acquired_at": "2026-03-21T00:00:00+00:00",
                        "command": "run_production_stack.py",
                    }
                ),
                encoding="utf-8",
            )

            plan = build_reset_plan(
                runtime_dir=runtime_dir,
                mt5_symbol="XAUUSD",
                binance_symbol="BTCUSDT",
                timestamp=datetime(2026, 3, 21, tzinfo=timezone.utc),
                pid_checker=lambda pid: True,
            )

            self.assertEqual(1, len(plan.active_locks))
            with self.assertRaises(RuntimeError):
                execute_reset_plan(plan)


if __name__ == "__main__":
    unittest.main()
