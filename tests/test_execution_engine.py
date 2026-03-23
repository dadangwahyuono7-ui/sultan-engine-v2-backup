from __future__ import annotations

import unittest
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sultan_engine_v2.core.execution_models import ExecutionMode, Venue
from sultan_engine_v2.core.models import TradeDirection
from sultan_engine_v2.runtime.execution_engine import (
    ExecutionOrchestrator,
    JsonExecutionLedger,
    PaperExecutor,
    RuntimeAutoExecutor,
    SnapshotExecutionPlanner,
)


class LocalTestDir:
    def __init__(self, name: str) -> None:
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / f"{name}_{uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        rmtree(self.path, ignore_errors=True)


def build_recovered_snapshot() -> dict:
    return {
        "venue": "MT5",
        "symbol": {"requested": "XAUUSD", "actual": "XAUUSD", "resolution_source": "legacy-suffix"},
        "market": {"bid": 1999.5, "ask": 2000.0, "last": 1999.75, "time": 1710000000},
        "setup_focus": {"status": "CF_CONFIRMED", "master_timeframe": "H1", "confirmed_cf_timeframe": "M5"},
        "timeframes": {
            "H1": {
                "cmp": "BUY",
                "role": "CMP",
                "domain": "H4",
                "target_tf": None,
                "support": 1986.0,
                "resistance": 2030.0,
                "breakout_level": 1992.0,
                "cmp_timestamp": 1710000000,
            },
            "M15": {
                "cmp": "BUY",
                "role": "CMP",
                "domain": "H1",
                "target_tf": "M30",
                "support": 1990.0,
                "resistance": 2026.0,
                "breakout_level": 1995.0,
                "cmp_timestamp": 1710000300,
            },
            "M5": {
                "cmp": "BUY",
                "role": "CF",
                "domain": "M15",
                "target_tf": "M15",
                "support": 1994.0,
                "resistance": 2008.0,
                "breakout_level": 1998.0,
                "cmp_timestamp": 1710000600,
            },
        },
        "sequences": {
            "H1": {
                "direction": "BUY",
                "vr_child": "M15",
                "vr_active": False,
                "cf_count": 1,
                "control_state": "RECOVERED_BY_CF",
                "active_controller": "H1",
                "last_cf_timeframe": "M5",
            }
        },
    }


def build_cheat_code_snapshot() -> dict:
    return {
        "venue": "MT5",
        "symbol": {"requested": "XAUUSD", "actual": "XAUUSD", "resolution_source": "legacy-suffix"},
        "market": {"bid": 1999.5, "ask": 2000.0, "last": 1999.75, "time": 1710000000},
        "setup_focus": {"status": "WAIT_CF", "master_timeframe": "H4"},
        "timeframes": {
            "H4": {
                "cmp": "BUY",
                "role": "CMP",
                "domain": "D1",
                "target_tf": None,
                "support": 1980.0,
                "resistance": 2050.0,
                "breakout_level": 2000.0,
                "cmp_timestamp": 1710000000,
            },
            "M30": {
                "cmp": "SELL",
                "role": "CMP",
                "domain": "H1",
                "target_tf": "H1",
                "support": 1970.0,
                "resistance": 2010.0,
                "breakout_level": 1990.0,
                "cmp_timestamp": 1710001800,
            },
            "M5": {
                "cmp": "SELL",
                "role": "CMP",
                "domain": "M15",
                "target_tf": "M15",
                "support": 1985.0,
                "resistance": 2005.0,
                "breakout_level": 1992.0,
                "cmp_timestamp": 1710002400,
            },
        },
        "sequences": {},
        "cheat_code": {
            "status": "CF_CONFIRMED",
            "reason": "h4_m30_m5_cheat_code_valid",
            "direction": "SELL",
            "parent_timeframe": "H4",
            "trigger_timeframe": "M30",
            "execution_timeframe": "M5",
            "target_timeframe": "M30",
            "h4_confirmed_time": 1710000000,
            "m30_breakout_time": 1710001800,
            "m5_vr_time": 1710002100,
            "m5_cf_time": 1710002400,
        },
    }


class ExecutionEngineTests(unittest.TestCase):
    def test_planner_builds_intent_from_cf_recovery(self) -> None:
        planner = SnapshotExecutionPlanner(default_venue=Venue.MT5, execution_mode=ExecutionMode.PAPER)
        intents = planner.plan(build_recovered_snapshot())

        self.assertEqual(1, len(intents))
        intent = intents[0]
        self.assertEqual(Venue.MT5, intent.venue)
        self.assertEqual(TradeDirection.BUY, intent.direction)
        self.assertEqual("M5", intent.timeframe)
        self.assertAlmostEqual(2000.0, intent.entry_price)
        self.assertAlmostEqual(1994.0, intent.stop_loss)
        self.assertAlmostEqual(2026.0, intent.take_profit)
        self.assertEqual("HIGH_RISK_CF", intent.metadata["entry_kind"])
        self.assertEqual("M15", intent.metadata["confirmation_timeframe"])
        self.assertEqual("M15", intent.metadata["invalidation_timeframe"])

    def test_planner_builds_intent_from_cheat_code(self) -> None:
        planner = SnapshotExecutionPlanner(default_venue=Venue.MT5, execution_mode=ExecutionMode.PAPER)
        intents = planner.plan(build_cheat_code_snapshot())

        self.assertEqual(1, len(intents))
        intent = intents[0]
        self.assertEqual(TradeDirection.SELL, intent.direction)
        self.assertEqual("M5", intent.timeframe)
        self.assertEqual("CHEAT_CODE", intent.metadata["entry_kind"])
        self.assertEqual("M30", intent.metadata["confirmation_timeframe"])
        self.assertEqual("M30", intent.metadata["invalidation_timeframe"])
        self.assertEqual("M30", intent.metadata["target_timeframe"])

    def test_orchestrator_deduplicates_intent_keys(self) -> None:
        planner = SnapshotExecutionPlanner(default_venue=Venue.MT5, execution_mode=ExecutionMode.PAPER)
        with LocalTestDir("execution_engine") as temp_dir:
            ledger = JsonExecutionLedger(temp_dir / "ledger.json")
            orchestrator = ExecutionOrchestrator(planner=planner, executors={Venue.MT5: PaperExecutor()}, ledger=ledger)

            first_results = orchestrator.execute_snapshot(build_recovered_snapshot())
            second_results = orchestrator.execute_snapshot(build_recovered_snapshot())

        self.assertEqual(1, len(first_results))
        self.assertEqual(0, len(second_results))

    def test_runtime_auto_executor_keeps_recent_results(self) -> None:
        with LocalTestDir("runtime_executor") as temp_dir:
            runtime_executor = RuntimeAutoExecutor(
                venue=Venue.MT5,
                execution_mode=ExecutionMode.PAPER,
                executors={Venue.MT5: PaperExecutor()},
                ledger_path=temp_dir / "runtime_ledger.json",
                enabled=True,
            )

            results = runtime_executor.execute(build_recovered_snapshot())

        self.assertEqual(1, len(results))
        self.assertEqual(1, len(runtime_executor.recent_results))
        self.assertTrue(results[0].success)
        self.assertEqual(1, len(runtime_executor.last_cycle_decisions))
        self.assertTrue(runtime_executor.last_cycle_decisions[0].allowed)

    def test_failed_result_does_not_permanently_dedupe_intent(self) -> None:
        class DeterministicFailExecutor:
            def execute_intent(self, intent):
                from sultan_engine_v2.core.execution_models import ExecutionResult

                return ExecutionResult(
                    intent_key=intent.intent_key,
                    venue=intent.venue,
                    symbol=intent.symbol,
                    success=False,
                    execution_mode=intent.execution_mode,
                    message="temporary broker failure",
                    metadata=intent.metadata,
                )

        planner = SnapshotExecutionPlanner(default_venue=Venue.MT5, execution_mode=ExecutionMode.PAPER)
        with LocalTestDir("execution_retry") as temp_dir:
            ledger = JsonExecutionLedger(temp_dir / "ledger.json")
            orchestrator = ExecutionOrchestrator(
                planner=planner,
                executors={Venue.MT5: DeterministicFailExecutor()},
                ledger=ledger,
            )

            first_results = orchestrator.execute_snapshot(build_recovered_snapshot())
            second_results = orchestrator.execute_snapshot(build_recovered_snapshot())

        self.assertEqual(1, len(first_results))
        self.assertEqual(1, len(second_results))
        self.assertFalse(first_results[0].success)
        self.assertFalse(second_results[0].success)


if __name__ == "__main__":
    unittest.main()
