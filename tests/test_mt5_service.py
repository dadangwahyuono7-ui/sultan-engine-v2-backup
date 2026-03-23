from __future__ import annotations

import unittest
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sultan_engine_v2.runtime.mt5_service import Mt5AutonomousService
from sultan_engine_v2.runtime.service_state import JsonServiceStateStore


class DummyReporter:
    def __init__(self) -> None:
        self.is_enabled = False

    def send_text(self, message: str) -> bool:
        return True


class FakeSession:
    def __init__(self) -> None:
        self.connected = False


class FakeOperatorState:
    paused = False


class FakeAutoExecutor:
    enabled = True


class FakeRuntime:
    def __init__(self, *, fail_on_poll: bool = False) -> None:
        self.base_symbol = "XAUUSD"
        self.execution_mode = "PAPER"
        self.auto_execute = True
        self.session = FakeSession()
        self.bootstrapped = False
        self.actual_symbol = "XAUUSD"
        self.operator_state = FakeOperatorState()
        self.auto_executor = FakeAutoExecutor()
        self.telegram_reporter = DummyReporter()
        self.fail_on_poll = fail_on_poll
        self.logged: list[str] = []
        self.closed = False
        self.snapshot = {
            "timestamp": "2026-03-20T00:00:00+00:00",
            "mt5": {"connected": True},
            "symbol": {"actual": "XAUUSD"},
            "operator": {"paused": False},
            "execution": {"entries_enabled": True},
            "timeframes": {"D1": {"cmp": "SELL"}},
        }

    def connect(self) -> bool:
        self.session.connected = True
        return True

    def bootstrap(self) -> bool:
        self.bootstrapped = True
        return True

    def poll(self) -> dict:
        if self.fail_on_poll:
            raise RuntimeError("poll failed")
        return self.snapshot

    def snapshot_dict(self) -> dict:
        return self.snapshot

    def write_snapshot(self, snapshot: dict) -> None:
        self.snapshot = snapshot

    def close(self) -> None:
        self.closed = True
        self.session.connected = False

    def log(self, message: str) -> None:
        self.logged.append(message)


class LocalTestDir:
    def __init__(self, name: str) -> None:
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / f"{name}_{uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        rmtree(self.path, ignore_errors=True)


class Mt5AutonomousServiceTests(unittest.TestCase):
    def test_run_cycle_writes_running_state(self) -> None:
        with LocalTestDir("mt5_service_running") as temp_dir:
            runtime = FakeRuntime()
            state_store = JsonServiceStateStore(temp_dir / "service_status.json")
            service = Mt5AutonomousService(runtime=runtime, state_store=state_store)

            state = service.run_cycle()
            payload = state_store.read()

            self.assertEqual("RUNNING", state.status)
            self.assertTrue(payload is not None)
            self.assertEqual("RUNNING", payload["status"])
            self.assertEqual("XAUUSD", payload["base_symbol"])

    def test_run_cycle_handles_runtime_error(self) -> None:
        with LocalTestDir("mt5_service_error") as temp_dir:
            runtime = FakeRuntime(fail_on_poll=True)
            runtime.session.connected = True
            runtime.bootstrapped = True
            state_store = JsonServiceStateStore(temp_dir / "service_status.json")
            service = Mt5AutonomousService(runtime=runtime, state_store=state_store)

            state = service.run_cycle()
            payload = state_store.read()

            self.assertEqual("ERROR", state.status)
            self.assertTrue(payload is not None)
            self.assertEqual("ERROR", payload["status"])
            self.assertIn("poll failed", payload["last_error"])
            self.assertTrue(runtime.closed)


if __name__ == "__main__":
    unittest.main()
