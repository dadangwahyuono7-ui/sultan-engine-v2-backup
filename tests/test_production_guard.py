from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sultan_engine_v2.manager.operator_control import OperatorControlState
from sultan_engine_v2.runtime.production_guard import JsonProductionGuardStateStore, ProductionGuard, ProductionGuardConfig
from sultan_engine_v2.runtime.service_state import build_service_state


class LocalTestDir:
    def __init__(self, name: str) -> None:
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / f"{name}_{uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        rmtree(self.path, ignore_errors=True)


class SequenceClock:
    def __init__(self, values: list[datetime]) -> None:
        self.values = values
        self.index = 0

    def __call__(self) -> datetime:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


class DummyReporter:
    def __init__(self) -> None:
        self.is_enabled = True
        self.messages: list[str] = []

    def send_text(self, message: str) -> bool:
        self.messages.append(message)
        return True


class DummyOperatorControls:
    def __init__(self) -> None:
        self.state = OperatorControlState()
        self.commands: list[dict[str, str | None]] = []

    def record_command(self, action: str, *, source: str, message: str, interpreter: str = "RULE", response: str | None = None, reason: str | None = None):
        normalized = action.upper()
        if normalized == "PAUSE":
            self.state.paused = True
            self.state.allow_new_entries = False
            self.state.pause_reason = reason
        elif normalized == "RESUME":
            self.state.paused = False
            self.state.allow_new_entries = True
            self.state.pause_reason = None
        self.state.last_command = normalized
        self.state.last_source = source
        self.state.last_message = message
        self.state.last_interpreter = interpreter
        self.state.last_response = response
        self.commands.append({"action": normalized, "source": source, "message": message, "reason": reason})
        return self.state


def build_snapshot(
    *,
    balance: float,
    equity: float,
    paused: bool = False,
    connected: bool = True,
    execution_mode: str = "LIVE",
    operator_source: str | None = None,
) -> dict:
    return {
        "timestamp": "2026-03-20T00:00:00+00:00",
        "symbol": {"requested": "XAUUSD", "actual": "XAUUSD"},
        "mt5": {"balance": balance, "equity": equity, "currency": "USD"},
        "setup_focus": {"status": "CF_CONFIRMED", "master_timeframe": "H4", "vr_timeframe": "M30"},
        "execution": {"mode": execution_mode, "entries_enabled": not paused, "recent_decisions": [], "recent_results": []},
        "lifecycle": {"position_structure": []},
        "operator": {"paused": paused, "allow_new_entries": not paused, "last_source": operator_source},
        "health": {"connected": connected, "bootstrapped": True},
    }


def build_runtime_state(*, connected: bool = True, error_count: int = 0):
    return build_service_state(
        service_name="SULTAN_VPS_STACK",
        status="RUNNING" if connected else "DISCONNECTED",
        base_symbol="XAUUSD",
        execution_mode="LIVE",
        auto_execute=True,
        cycle_count=10,
        error_count=error_count,
        connected=connected,
        bootstrapped=True,
    )


class ProductionGuardTests(unittest.TestCase):
    def test_daily_report_sent_once_per_day(self) -> None:
        with LocalTestDir("production_guard_report") as temp_dir:
            reporter = DummyReporter()
            guard = ProductionGuard(
                base_symbol="XAUUSD",
                config=ProductionGuardConfig(
                    enabled=True,
                    daily_report_enabled=True,
                    daily_report_hour=21,
                    daily_report_minute=0,
                    report_utc_offset="+07:00",
                    kill_switch_enabled=False,
                ),
                state_store=JsonProductionGuardStateStore(temp_dir / "guard.json"),
                operator_controls=DummyOperatorControls(),
                reporter=reporter,
                now_provider=SequenceClock(
                    [
                        datetime(2026, 3, 20, 14, 5, tzinfo=timezone.utc),
                        datetime(2026, 3, 20, 14, 6, tzinfo=timezone.utc),
                    ]
                ),
            )

            first = guard.handle_cycle(build_snapshot(balance=1000.0, equity=1005.0), build_runtime_state())
            second = guard.handle_cycle(build_snapshot(balance=1000.0, equity=1006.0), build_runtime_state())

        self.assertTrue(first.daily_report_sent)
        self.assertFalse(second.daily_report_sent)
        self.assertEqual(1, len(reporter.messages))
        self.assertIn("Daily Report", reporter.messages[0])

    def test_kill_switch_pauses_on_equity_drawdown(self) -> None:
        with LocalTestDir("production_guard_drawdown") as temp_dir:
            reporter = DummyReporter()
            controls = DummyOperatorControls()
            guard = ProductionGuard(
                base_symbol="XAUUSD",
                config=ProductionGuardConfig(
                    enabled=True,
                    daily_report_enabled=False,
                    kill_switch_enabled=True,
                    max_equity_drawdown_percent=5.0,
                    max_daily_loss_percent=0.0,
                    max_service_errors=0,
                    max_disconnect_minutes=0.0,
                ),
                state_store=JsonProductionGuardStateStore(temp_dir / "guard.json"),
                operator_controls=controls,
                reporter=reporter,
                now_provider=SequenceClock(
                    [
                        datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc),
                        datetime(2026, 3, 20, 0, 10, tzinfo=timezone.utc),
                    ]
                ),
            )

            guard.handle_cycle(build_snapshot(balance=1000.0, equity=1000.0), build_runtime_state())
            outcome = guard.handle_cycle(build_snapshot(balance=990.0, equity=930.0), build_runtime_state())

        self.assertTrue(outcome.kill_switch_triggered)
        self.assertEqual("PAUSE", controls.commands[0]["action"])
        self.assertTrue(outcome.state.kill_switch_active)
        self.assertIn("Kill Switch", reporter.messages[-1])

    def test_disconnect_timeout_triggers_kill_switch(self) -> None:
        with LocalTestDir("production_guard_disconnect") as temp_dir:
            controls = DummyOperatorControls()
            guard = ProductionGuard(
                base_symbol="XAUUSD",
                config=ProductionGuardConfig(
                    enabled=True,
                    daily_report_enabled=False,
                    kill_switch_enabled=True,
                    max_equity_drawdown_percent=0.0,
                    max_daily_loss_percent=0.0,
                    max_service_errors=0,
                    max_disconnect_minutes=15.0,
                ),
                state_store=JsonProductionGuardStateStore(temp_dir / "guard.json"),
                operator_controls=controls,
                reporter=DummyReporter(),
                now_provider=SequenceClock(
                    [
                        datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc),
                        datetime(2026, 3, 20, 0, 20, tzinfo=timezone.utc),
                    ]
                ),
            )

            first = guard.handle_cycle(build_snapshot(balance=1000.0, equity=1000.0, connected=False), build_runtime_state(connected=False))
            second = guard.handle_cycle(build_snapshot(balance=1000.0, equity=1000.0, connected=False), build_runtime_state(connected=False))

        self.assertFalse(first.kill_switch_triggered)
        self.assertTrue(second.kill_switch_triggered)
        self.assertEqual("PAUSE", controls.commands[0]["action"])

    def test_paper_mode_auto_resumes_stale_guard_pause_when_safe(self) -> None:
        with LocalTestDir("production_guard_auto_resume") as temp_dir:
            controls = DummyOperatorControls()
            controls.record_command(
                "PAUSE",
                source="PRODUCTION_GUARD",
                message="stale pause",
                reason="DAILY_LOSS limit hit (100.00%/5.00%)",
            )
            guard = ProductionGuard(
                base_symbol="XAUUSD",
                config=ProductionGuardConfig(
                    enabled=True,
                    daily_report_enabled=False,
                    kill_switch_enabled=True,
                    max_equity_drawdown_percent=5.0,
                    max_daily_loss_percent=5.0,
                    max_service_errors=0,
                    max_disconnect_minutes=0.0,
                ),
                state_store=JsonProductionGuardStateStore(temp_dir / "guard.json"),
                operator_controls=controls,
                reporter=DummyReporter(),
                now_provider=SequenceClock([datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc)]),
            )

            outcome = guard.handle_cycle(
                build_snapshot(
                    balance=1000.0,
                    equity=1000.0,
                    paused=True,
                    execution_mode="PAPER",
                    operator_source="PRODUCTION_GUARD",
                ),
                build_runtime_state(),
            )

        self.assertFalse(outcome.kill_switch_triggered)
        self.assertEqual("RESUME", controls.commands[-1]["action"])
        self.assertFalse(controls.state.paused)
        self.assertTrue(controls.state.allow_new_entries)

    def test_live_mode_keeps_manual_resume_requirement_after_guard_pause(self) -> None:
        with LocalTestDir("production_guard_live_pause") as temp_dir:
            controls = DummyOperatorControls()
            controls.record_command(
                "PAUSE",
                source="PRODUCTION_GUARD",
                message="stale pause",
                reason="DAILY_LOSS limit hit (100.00%/5.00%)",
            )
            guard = ProductionGuard(
                base_symbol="XAUUSD",
                config=ProductionGuardConfig(
                    enabled=True,
                    daily_report_enabled=False,
                    kill_switch_enabled=True,
                    max_equity_drawdown_percent=5.0,
                    max_daily_loss_percent=5.0,
                    max_service_errors=0,
                    max_disconnect_minutes=0.0,
                ),
                state_store=JsonProductionGuardStateStore(temp_dir / "guard.json"),
                operator_controls=controls,
                reporter=DummyReporter(),
                now_provider=SequenceClock([datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc)]),
            )

            outcome = guard.handle_cycle(
                build_snapshot(
                    balance=1000.0,
                    equity=1000.0,
                    paused=True,
                    execution_mode="LIVE",
                    operator_source="PRODUCTION_GUARD",
                ),
                build_runtime_state(),
            )

        self.assertFalse(outcome.kill_switch_triggered)
        self.assertEqual("PAUSE", controls.commands[-1]["action"])
        self.assertTrue(controls.state.paused)
        self.assertFalse(controls.state.allow_new_entries)


if __name__ == "__main__":
    unittest.main()
