from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.manager.telegram_reporter import TelegramOperatorReporter
from sultan_engine_v2.runtime.process_lock import FileProcessLock
from sultan_engine_v2.runtime.service_state import JsonServiceStateStore, build_service_state


def _parse_float(raw_value: str | None, default: float) -> float:
    try:
        return float(raw_value) if raw_value is not None else default
    except (TypeError, ValueError):
        return default


def _parse_int(raw_value: str | None, default: int) -> int:
    try:
        return int(raw_value) if raw_value is not None else default
    except (TypeError, ValueError):
        return default


def _parse_iso_datetime(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True)
class ProductionSupervisorConfig:
    base_symbol: str = "XAUUSD"
    execution_mode: ExecutionMode = ExecutionMode.PAPER
    auto_execute: bool = True
    check_interval_seconds: float = 5.0
    heartbeat_timeout_seconds: float = 180.0
    restart_backoff_seconds: float = 10.0
    max_restarts: int = 0
    child_max_cycles: int = 0
    child_script: str = "run_vps_stack.py"
    heartbeat_file_template: str = "vps_stack_{symbol}_status.json"
    state_file_template: str = "production_stack_{symbol}_status.json"
    service_name: str = "SULTAN_PRODUCTION_SUPERVISOR"
    symbol_env_key: str = "MT5_RUNTIME_SYMBOL"
    child_max_cycles_env_key: str = "SULTAN_VPS_STACK_MAX_CYCLES"


def load_production_supervisor_config_from_env(
    *,
    base_symbol: str,
    execution_mode: ExecutionMode,
    auto_execute: bool,
    child_script: str = "run_vps_stack.py",
    heartbeat_file_template: str = "vps_stack_{symbol}_status.json",
    state_file_template: str = "production_stack_{symbol}_status.json",
    service_name: str = "SULTAN_PRODUCTION_SUPERVISOR",
    symbol_env_key: str = "MT5_RUNTIME_SYMBOL",
    child_max_cycles_env_key: str = "SULTAN_VPS_STACK_MAX_CYCLES",
) -> ProductionSupervisorConfig:
    return ProductionSupervisorConfig(
        base_symbol=base_symbol,
        execution_mode=execution_mode,
        auto_execute=auto_execute,
        check_interval_seconds=max(1.0, _parse_float(os.getenv("SULTAN_PRODUCTION_CHECK_INTERVAL_SECONDS"), 5.0)),
        heartbeat_timeout_seconds=max(15.0, _parse_float(os.getenv("SULTAN_PRODUCTION_HEARTBEAT_TIMEOUT_SECONDS"), 180.0)),
        restart_backoff_seconds=max(1.0, _parse_float(os.getenv("SULTAN_PRODUCTION_RESTART_BACKOFF_SECONDS"), 10.0)),
        max_restarts=max(0, _parse_int(os.getenv("SULTAN_PRODUCTION_MAX_RESTARTS"), 0)),
        child_max_cycles=max(0, _parse_int(os.getenv("SULTAN_PRODUCTION_CHILD_MAX_CYCLES"), 0)),
        child_script=child_script,
        heartbeat_file_template=heartbeat_file_template,
        state_file_template=state_file_template,
        service_name=service_name,
        symbol_env_key=symbol_env_key,
        child_max_cycles_env_key=child_max_cycles_env_key,
    )


@dataclass(slots=True)
class HeartbeatAssessment:
    stale: bool
    age_seconds: float | None = None
    reason: str | None = None


def assess_heartbeat(
    heartbeat: dict[str, Any] | None,
    *,
    now_utc: datetime,
    timeout_seconds: float,
) -> HeartbeatAssessment:
    if not heartbeat:
        return HeartbeatAssessment(stale=True, reason="Heartbeat missing")

    updated_at = _parse_iso_datetime(str(heartbeat.get("updated_at") or ""))
    if updated_at is None:
        return HeartbeatAssessment(stale=True, reason="Heartbeat timestamp missing")

    age_seconds = max((now_utc - updated_at).total_seconds(), 0.0)
    if age_seconds > timeout_seconds:
        return HeartbeatAssessment(stale=True, age_seconds=age_seconds, reason=f"Heartbeat stale for {age_seconds:.1f}s")
    return HeartbeatAssessment(stale=False, age_seconds=age_seconds)


def build_child_command(
    *,
    project_root: str | Path,
    base_symbol: str,
    execution_mode: ExecutionMode,
    auto_execute: bool,
    child_script: str = "run_vps_stack.py",
) -> list[str]:
    project_root = Path(project_root)
    engine_root = project_root / "sultan_engine_v2" if (project_root / "sultan_engine_v2").is_dir() else project_root
    script_path = engine_root / child_script
    return [sys.executable, str(script_path), base_symbol, str(execution_mode), "1" if auto_execute else "0"]


class ProductionSupervisor:
    def __init__(
        self,
        *,
        config: ProductionSupervisorConfig,
        reporter: TelegramOperatorReporter | None = None,
        heartbeat_store: JsonServiceStateStore | None = None,
        state_store: JsonServiceStateStore | None = None,
        process_lock: FileProcessLock | None = None,
        process_factory: Callable[..., subprocess.Popen[Any]] | None = None,
        sleeper: Callable[[float], None] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        runtime_dir = Path(__file__).resolve().parent
        self.project_root = runtime_dir.parent.parent
        self.reporter = reporter or TelegramOperatorReporter()
        symbol_slug = config.base_symbol.lower()
        self.heartbeat_store = heartbeat_store or JsonServiceStateStore(
            runtime_dir / "state" / config.heartbeat_file_template.format(symbol=symbol_slug)
        )
        self.state_store = state_store or JsonServiceStateStore(
            runtime_dir / "state" / config.state_file_template.format(symbol=symbol_slug)
        )
        self.process_lock = process_lock
        self.process_factory = process_factory or subprocess.Popen
        self.sleeper = sleeper or time.sleep
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.restart_count = 0
        self.last_restart_reason: str | None = None

    def run_forever(self) -> None:
        child, child_started_at = self._start_child(initial=True)
        try:
            while True:
                now_utc = self._utc_now()
                heartbeat = self.heartbeat_store.read()
                heartbeat_pid = int((heartbeat or {}).get("pid") or 0)
                child_pid = int(getattr(child, "pid", 0) or 0)
                child_age_seconds = max((now_utc - child_started_at).total_seconds(), 0.0)

                exit_code = child.poll()
                if exit_code is not None:
                    reason = f"Child exited with code {exit_code}"
                    if not self._restart_child(reason=reason, heartbeat=heartbeat):
                        break
                    child, child_started_at = self._start_child(initial=False)
                    continue

                if child_pid <= 0 or heartbeat_pid != child_pid:
                    if child_age_seconds > self.config.heartbeat_timeout_seconds:
                        reason = "No fresh heartbeat from current child process"
                        self._terminate_child(child)
                        if not self._restart_child(reason=reason, heartbeat=heartbeat):
                            break
                        child, child_started_at = self._start_child(initial=False)
                        continue
                    self._write_state(status="STARTING", heartbeat=heartbeat, last_error=None, heartbeat_age_seconds=None, child_pid=child_pid)
                    self.sleeper(self.config.check_interval_seconds)
                    continue

                assessment = assess_heartbeat(heartbeat, now_utc=now_utc, timeout_seconds=self.config.heartbeat_timeout_seconds)
                if assessment.stale:
                    self._terminate_child(child)
                    if not self._restart_child(reason=assessment.reason or "Heartbeat stale", heartbeat=heartbeat):
                        break
                    child, child_started_at = self._start_child(initial=False)
                    continue

                self._write_state(
                    status="RUNNING",
                    heartbeat=heartbeat,
                    last_error=(heartbeat or {}).get("last_error"),
                    heartbeat_age_seconds=assessment.age_seconds,
                    child_pid=child_pid,
                )
                self.sleeper(self.config.check_interval_seconds)
        except KeyboardInterrupt:
            self._write_state(
                status="STOPPED",
                heartbeat=self.heartbeat_store.read(),
                last_error="Supervisor stopped by operator",
                heartbeat_age_seconds=None,
                child_pid=int(getattr(child, "pid", 0) or 0),
            )
        finally:
            self._terminate_child(child)
            if self.process_lock is not None:
                self.process_lock.release()

    def _start_child(self, *, initial: bool) -> tuple[subprocess.Popen[Any], datetime]:
        command = build_child_command(
            project_root=self.project_root,
            base_symbol=self.config.base_symbol,
            execution_mode=self.config.execution_mode,
            auto_execute=self.config.auto_execute,
            child_script=self.config.child_script,
        )
        env = os.environ.copy()
        env[self.config.symbol_env_key] = self.config.base_symbol
        env["SULTAN_EXECUTION_MODE"] = str(self.config.execution_mode)
        env["SULTAN_AUTO_EXECUTE"] = "1" if self.config.auto_execute else "0"
        env[self.config.child_max_cycles_env_key] = str(self.config.child_max_cycles)
        child = self.process_factory(command, cwd=str(self.project_root), env=env)
        event_name = "CHILD_STARTED" if initial and self.restart_count == 0 else "CHILD_RESTARTED"
        self._notify_event(event_name, child_pid=int(getattr(child, "pid", 0) or 0), reason=self.last_restart_reason)
        self._write_state(
            status="STARTING",
            heartbeat=self.heartbeat_store.read(),
            last_error=None,
            heartbeat_age_seconds=None,
            child_pid=int(getattr(child, "pid", 0) or 0),
        )
        return child, self._utc_now()

    def _restart_child(self, *, reason: str, heartbeat: dict[str, Any] | None) -> bool:
        if self.config.max_restarts > 0 and self.restart_count >= self.config.max_restarts:
            self._notify_event("MAX_RESTARTS_REACHED", reason=reason, child_pid=None)
            self._write_state(status="ERROR", heartbeat=heartbeat, last_error=reason, heartbeat_age_seconds=None, child_pid=None)
            return False

        self.restart_count += 1
        self.last_restart_reason = reason
        self._notify_event("CHILD_RESTART_REQUESTED", reason=reason, child_pid=None)
        self._write_state(status="RESTARTING", heartbeat=heartbeat, last_error=reason, heartbeat_age_seconds=None, child_pid=None)
        self.sleeper(self.config.restart_backoff_seconds)
        return True

    def _write_state(
        self,
        *,
        status: str,
        heartbeat: dict[str, Any] | None,
        last_error: str | None,
        heartbeat_age_seconds: float | None,
        child_pid: int | None,
    ) -> None:
        heartbeat = heartbeat or {}
        state = build_service_state(
            service_name=self.config.service_name,
            status=status,
            base_symbol=self.config.base_symbol,
            execution_mode=str(self.config.execution_mode),
            auto_execute=self.config.auto_execute,
            cycle_count=int(heartbeat.get("cycle_count") or 0),
            error_count=int(heartbeat.get("error_count") or 0),
            connected=bool(heartbeat.get("connected", False)),
            bootstrapped=bool(heartbeat.get("bootstrapped", False)),
            snapshot_timestamp=heartbeat.get("snapshot_timestamp"),
            last_error=last_error,
            details={
                "child_pid": child_pid,
                "child_status": heartbeat.get("status"),
                "restart_count": self.restart_count,
                "last_restart_reason": self.last_restart_reason,
                "heartbeat_age_seconds": heartbeat_age_seconds,
                "heartbeat_timeout_seconds": self.config.heartbeat_timeout_seconds,
                "check_interval_seconds": self.config.check_interval_seconds,
            },
        )
        self.state_store.write(state)

    def _notify_event(self, event_name: str, *, reason: str | None, child_pid: int | None) -> None:
        if not self.reporter.is_enabled:
            return
        lines = [
            "Sultan Production Supervisor",
            f"Event: {event_name}",
            f"Child Script: {self.config.child_script}",
            f"Symbol: {self.config.base_symbol}",
            f"Mode: {self.config.execution_mode}",
            f"Auto Execute: {self.config.auto_execute}",
            f"Restarts: {self.restart_count}",
        ]
        if child_pid:
            lines.append(f"Child PID: {child_pid}")
        if reason:
            lines.append(f"Reason: {reason}")
        self.reporter.send_text("\n".join(lines))

    def _terminate_child(self, child: subprocess.Popen[Any]) -> None:
        try:
            exit_code = child.poll()
        except Exception:
            exit_code = 0
        if exit_code is not None:
            return
        try:
            child.terminate()
        except Exception:
            return
        try:
            child.wait(timeout=10)
        except Exception:
            try:
                child.kill()
            except Exception:
                return
            try:
                child.wait(timeout=5)
            except Exception:
                pass

    def _utc_now(self) -> datetime:
        current = self.now_provider()
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)
