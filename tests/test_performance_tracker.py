from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sultan_engine_v2.runtime.performance_tracker import (
    JsonPerformanceTrackerStateStore,
    PerformanceTracker,
    PerformanceTrackerConfig,
)
from sultan_engine_v2.runtime.service_state import build_service_state


class LocalTestDir:
    def __init__(self, name: str) -> None:
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / f"{name}_{uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        rmtree(self.path, ignore_errors=True)


class DummyReporter:
    def __init__(self) -> None:
        self.is_enabled = True
        self.messages: list[str] = []

    def send_text(self, message: str) -> bool:
        self.messages.append(message)
        return True


class SequenceClock:
    def __init__(self, values: list[datetime]) -> None:
        self.values = values
        self.index = 0

    def __call__(self) -> datetime:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


def build_state(*, symbol: str = "BTCUSDT", connected: bool = True, error_count: int = 0):
    return build_service_state(
        service_name="SULTAN_BINANCE_STACK",
        status="RUNNING" if connected else "DISCONNECTED",
        base_symbol=symbol,
        execution_mode="PAPER",
        auto_execute=True,
        cycle_count=10,
        error_count=error_count,
        connected=connected,
        bootstrapped=True,
    )


def build_binance_snapshot(
    *,
    balance: float,
    equity: float,
    closed_trade_count: int = 0,
    win_count: int = 0,
    loss_count: int = 0,
    realized_pnl: float = 0.0,
    connected: bool = True,
    bootstrapped: bool = True,
) -> dict:
    return {
        "timestamp": "2026-03-21T00:00:00+00:00",
        "venue": "BINANCE",
        "symbol": {"requested": "BTCUSDT", "actual": "BTCUSDT"},
        "paper_account": {
            "currency": "USDT",
            "balance": balance,
            "equity": equity,
            "realized_pnl": realized_pnl,
            "closed_trade_count": closed_trade_count,
            "win_count": win_count,
            "loss_count": loss_count,
        },
        "execution": {"recent_decisions": [], "recent_results": [], "mode": "PAPER"},
        "setup_focus": {"status": "WAIT_CF", "master_timeframe": "H4", "vr_timeframe": "H1"},
        "health": {"connected": connected, "bootstrapped": bootstrapped},
    }


def build_mt5_snapshot(*, balance: float, equity: float, connected: bool = True, bootstrapped: bool = True) -> dict:
    return {
        "timestamp": "2026-03-21T00:00:00+00:00",
        "venue": "MT5",
        "symbol": {"requested": "XAUUSD", "actual": "XAUUSD"},
        "mt5": {
            "currency": "USD",
            "balance": balance,
            "equity": equity,
        },
        "execution": {"recent_decisions": [], "recent_results": [], "mode": "PAPER"},
        "setup_focus": {"status": "WAIT_CF", "master_timeframe": "H4", "vr_timeframe": "H1"},
        "health": {"connected": connected, "bootstrapped": bootstrapped},
    }


class PerformanceTrackerTests(unittest.TestCase):
    def test_binance_tracker_reaches_small_risk_trial_when_thresholds_met(self) -> None:
        with LocalTestDir("performance_tracker_ready") as temp_dir:
            tracker = PerformanceTracker(
                base_symbol="BTCUSDT",
                venue="BINANCE",
                config=PerformanceTrackerConfig(
                    enabled=True,
                    burn_in_report_enabled=False,
                    min_runtime_hours=0.0,
                    min_cycles=1,
                    min_closed_trades=1,
                    min_connected_ratio=0.5,
                    min_bootstrapped_ratio=0.5,
                    max_drawdown_percent=10.0,
                    max_error_events_per_100_cycles=10.0,
                    min_net_return_percent=0.0,
                    sample_every_cycles=1,
                ),
                state_store=JsonPerformanceTrackerStateStore(temp_dir / "performance.json"),
                history_path=temp_dir / "history.jsonl",
            )

            tracker.handle_cycle(
                build_binance_snapshot(
                    balance=1000.0,
                    equity=1000.0,
                    closed_trade_count=0,
                    win_count=0,
                    realized_pnl=0.0,
                ),
                build_state(),
                ledger_entries=[],
            )
            outcome = tracker.handle_cycle(
                build_binance_snapshot(
                    balance=1000.0,
                    equity=1020.0,
                    closed_trade_count=2,
                    win_count=2,
                    realized_pnl=20.0,
                ),
                build_state(),
                ledger_entries=[{"success": True}],
            )

        self.assertEqual("READY_FOR_SMALL_RISK_TRIAL", outcome.burn_in_status)
        self.assertGreater(outcome.burn_in_score, 0.0)
        self.assertAlmostEqual(2.0, outcome.metrics["net_return_percent"])

    def test_mt5_tracker_caps_at_paper_stable_without_trade_metric(self) -> None:
        with LocalTestDir("performance_tracker_mt5") as temp_dir:
            tracker = PerformanceTracker(
                base_symbol="XAUUSD",
                venue="MT5",
                config=PerformanceTrackerConfig(
                    enabled=True,
                    burn_in_report_enabled=False,
                    min_runtime_hours=0.0,
                    min_cycles=1,
                    min_closed_trades=1,
                    min_connected_ratio=0.5,
                    min_bootstrapped_ratio=0.5,
                    max_drawdown_percent=10.0,
                    max_error_events_per_100_cycles=10.0,
                    min_net_return_percent=0.0,
                    sample_every_cycles=1,
                ),
                state_store=JsonPerformanceTrackerStateStore(temp_dir / "performance.json"),
                history_path=temp_dir / "history.jsonl",
            )

            outcome = tracker.handle_cycle(
                build_mt5_snapshot(balance=1000.0, equity=1010.0),
                build_state(symbol="XAUUSD"),
                ledger_entries=[{"success": True}],
            )

        self.assertEqual("PAPER_STABLE", outcome.burn_in_status)
        self.assertFalse(outcome.metrics["trade_metric_available"])

    def test_tracker_reports_not_ready_on_large_drawdown_and_sends_report(self) -> None:
        with LocalTestDir("performance_tracker_drawdown") as temp_dir:
            reporter = DummyReporter()
            tracker = PerformanceTracker(
                base_symbol="BTCUSDT",
                venue="BINANCE",
                config=PerformanceTrackerConfig(
                    enabled=True,
                    burn_in_report_enabled=True,
                    burn_in_report_interval_minutes=60.0,
                    burn_in_report_on_status_change=True,
                    min_runtime_hours=0.0,
                    min_cycles=1,
                    min_closed_trades=0,
                    min_connected_ratio=0.5,
                    min_bootstrapped_ratio=0.5,
                    max_drawdown_percent=5.0,
                    max_error_events_per_100_cycles=10.0,
                    min_net_return_percent=0.0,
                    sample_every_cycles=1,
                ),
                state_store=JsonPerformanceTrackerStateStore(temp_dir / "performance.json"),
                history_path=temp_dir / "history.jsonl",
                reporter=reporter,
                now_provider=SequenceClock(
                    [
                        datetime(2026, 3, 21, 0, 0, tzinfo=timezone.utc),
                        datetime(2026, 3, 21, 0, 5, tzinfo=timezone.utc),
                    ]
                ),
            )

            tracker.handle_cycle(
                build_binance_snapshot(balance=1000.0, equity=1000.0),
                build_state(),
                ledger_entries=[],
            )
            outcome = tracker.handle_cycle(
                build_binance_snapshot(balance=1000.0, equity=930.0),
                build_state(),
                ledger_entries=[],
            )

        self.assertEqual("NOT_READY", outcome.burn_in_status)
        self.assertTrue(outcome.report_sent)
        self.assertIn("drawdown", reporter.messages[-1].lower())


if __name__ == "__main__":
    unittest.main()
