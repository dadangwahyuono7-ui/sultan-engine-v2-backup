from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.manager.telegram_reporter import TelegramOperatorReporter
from sultan_engine_v2.runtime.exchange_live_runtime import BinanceLiveRuntime
from sultan_engine_v2.runtime.process_lock import FileProcessLock
from sultan_engine_v2.runtime.service_state import JsonServiceStateStore, ServiceState, build_service_state


class BinanceAutonomousService:
    def __init__(
        self,
        runtime: BinanceLiveRuntime | None = None,
        *,
        base_symbol: str = "BTCUSDT",
        execution_mode: ExecutionMode = ExecutionMode.PAPER,
        auto_execute: bool = True,
        poll_interval_seconds: float = 5.0,
        reconnect_backoff_seconds: float = 10.0,
        state_store: JsonServiceStateStore | None = None,
        reporter: TelegramOperatorReporter | None = None,
        process_lock: FileProcessLock | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.runtime = runtime or BinanceLiveRuntime(
            base_symbol=base_symbol,
            execution_mode=execution_mode,
            auto_execute=auto_execute,
        )
        runtime_dir = Path(__file__).resolve().parent
        self.state_store = state_store or JsonServiceStateStore(runtime_dir / "state" / f"binance_{base_symbol.lower()}_service_status.json")
        self.reporter = reporter or getattr(self.runtime, "telegram_reporter", TelegramOperatorReporter())
        self.process_lock = process_lock
        self.poll_interval_seconds = poll_interval_seconds
        self.reconnect_backoff_seconds = reconnect_backoff_seconds
        self.sleeper = sleeper or time.sleep
        self.cycle_count = 0
        self.error_count = 0
        self.last_error: str | None = None
        self.last_snapshot: dict[str, Any] | None = None
        self._last_notified_status: str | None = None

    def run_cycle(self) -> ServiceState:
        try:
            snapshot = self._ensure_runtime_progress()
            self.last_snapshot = snapshot
            self.cycle_count += 1
            self.last_error = None
            status = self._status_from_snapshot(snapshot)
            state = self._write_state(status=status, snapshot=snapshot)
            self._notify_status_change(state)
            return state
        except Exception as error:
            self.error_count += 1
            self.last_error = f"{type(error).__name__}: {error}"
            try:
                self.runtime.log(f"Service error | {self.last_error}")
            except Exception:
                pass
            try:
                self.runtime.close()
            except Exception:
                pass
            state = self._write_state(status="ERROR", snapshot=None, last_error=self.last_error)
            self._notify_status_change(state)
            return state

    def run_forever(self, max_cycles: int | None = None) -> None:
        completed = 0
        try:
            while max_cycles is None or completed < max_cycles:
                state = self.run_cycle()
                completed += 1
                wait_seconds = self.reconnect_backoff_seconds if state.status in {"DISCONNECTED", "ERROR"} else self.poll_interval_seconds
                self.sleeper(wait_seconds)
        finally:
            try:
                self.runtime.close()
            finally:
                if self.process_lock is not None:
                    self.process_lock.release()

    def _ensure_runtime_progress(self) -> dict[str, Any]:
        if not self.runtime.connected:
            connected = self.runtime.connect()
            if not connected:
                snapshot = self.runtime.snapshot_dict()
                self.runtime.write_snapshot(snapshot)
                return snapshot

        if not self.runtime.bootstrapped:
            self.runtime.bootstrap()
            snapshot = self.runtime.snapshot_dict()
            self.runtime.write_snapshot(snapshot)
            return snapshot

        return self.runtime.poll()

    def _write_state(self, *, status: str, snapshot: dict[str, Any] | None, last_error: str | None = None) -> ServiceState:
        health = (snapshot or {}).get("health", {})
        state = build_service_state(
            service_name="BINANCE_AUTONOMOUS_SERVICE",
            status=status,
            base_symbol=self.runtime.base_symbol,
            execution_mode=str(self.runtime.execution_mode),
            auto_execute=self.runtime.auto_execute,
            cycle_count=self.cycle_count,
            error_count=self.error_count,
            connected=bool(health.get("connected", self.runtime.connected)),
            bootstrapped=bool(health.get("bootstrapped", self.runtime.bootstrapped)),
            snapshot_timestamp=(snapshot or {}).get("timestamp"),
            last_error=last_error,
            details={
                "telegram_enabled": self.reporter.is_enabled,
                "actual_symbol": (snapshot or {}).get("symbol", {}).get("actual") or self.runtime.actual_symbol,
                "operator_paused": (snapshot or {}).get("operator", {}).get("paused", self.runtime.operator_state.paused),
                "entries_enabled": (snapshot or {}).get("execution", {}).get("entries_enabled", self.runtime.auto_executor.enabled),
            },
        )
        self.state_store.write(state)
        return state

    def _notify_status_change(self, state: ServiceState) -> None:
        if state.status == self._last_notified_status:
            return
        self._last_notified_status = state.status
        if not self.reporter.is_enabled:
            return

        lines = [
            "Sultan Binance Service",
            f"Status: {state.status}",
            f"Symbol: {state.base_symbol}",
            f"Mode: {state.execution_mode}",
            f"Auto Execute: {state.auto_execute}",
            f"Cycle Count: {state.cycle_count}",
        ]
        if state.last_error:
            lines.append(f"Error: {state.last_error}")
        self.reporter.send_text("\n".join(lines))

    @staticmethod
    def _status_from_snapshot(snapshot: dict[str, Any]) -> str:
        if not snapshot.get("health", {}).get("connected"):
            return "DISCONNECTED"
        if snapshot.get("operator", {}).get("paused"):
            return "PAUSED_BY_OPERATOR"
        if snapshot.get("execution", {}).get("entries_enabled") is False:
            return "ENTRIES_BLOCKED"
        return "RUNNING" if snapshot.get("timeframes") else "BOOTSTRAPPING"
