from __future__ import annotations

import os
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.manager.ai_manager import ManagerReport, SultanAIManager
from sultan_engine_v2.runtime.binance_service import BinanceAutonomousService
from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.performance_tracker import PerformanceTracker, load_performance_tracker_config_from_env
from sultan_engine_v2.runtime.process_lock import FileProcessLock, ProcessAlreadyRunningError
from sultan_engine_v2.runtime.production_guard import ProductionGuard, load_production_guard_config_from_env
from sultan_engine_v2.runtime.service_state import JsonServiceStateStore, build_service_state

load_runtime_env(__file__)


def _parse_bool(raw_value: str | None, default: bool = True) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in {"0", "false", "off", "no"}


def build_periodic_summary_message(snapshot: dict, report: ManagerReport, performance: dict | None = None) -> str:
    symbol = snapshot.get("symbol", {}).get("actual") or snapshot.get("symbol", {}).get("requested") or "UNKNOWN"
    focus = snapshot.get("setup_focus", {})
    market = snapshot.get("market", {})
    execution = snapshot.get("execution", {})
    lifecycle = snapshot.get("lifecycle", {})
    paper_account = snapshot.get("paper_account", {})
    finding_codes = ", ".join(item.code for item in report.findings[:5]) or "NONE"
    master = focus.get("master_timeframe") or "-"
    vr = focus.get("vr_timeframe") or "-"
    status = focus.get("status") or "NO_ACTIVE_VR"

    lines = [
        "Sultan Binance Periodic Summary",
        f"Symbol: {symbol}",
        f"Venue: BINANCE | Focus: {status}",
        f"Master: {master} | VR: {vr}",
        f"Bid: {float(market.get('bid') or 0.0):,.4f} | Ask: {float(market.get('ask') or 0.0):,.4f}",
        f"Paper Balance: {float(paper_account.get('balance') or 0.0):,.2f} {paper_account.get('currency') or 'USDT'}",
        f"Paper Equity: {float(paper_account.get('equity') or 0.0):,.2f}",
        f"Recent Results: {len(execution.get('recent_results', []))} | Recent Decisions: {len(execution.get('recent_decisions', []))}",
        f"Open Structure: {len(lifecycle.get('position_structure', []))}",
    ]
    if performance:
        lines.append(
            f"Burn-In: {performance.get('burn_in_status', '-')} | Score: {float(performance.get('burn_in_score') or 0.0):.1f}"
        )
        lines.append(
            f"Return: {float(performance.get('net_return_percent') or 0.0):+,.2f}% | Drawdown: {float(performance.get('max_drawdown_percent') or 0.0):.2f}%"
        )
    lines.extend(
        [
            f"Findings: {finding_codes}",
            f"Summary: {report.summary}",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    base_symbol = sys.argv[1] if len(sys.argv) > 1 else os.getenv("BINANCE_RUNTIME_SYMBOL", "BTCUSDT")
    execution_mode = ExecutionMode((sys.argv[2] if len(sys.argv) > 2 else os.getenv("SULTAN_EXECUTION_MODE", "PAPER")).upper())
    auto_execute = _parse_bool(sys.argv[3] if len(sys.argv) > 3 else os.getenv("SULTAN_AUTO_EXECUTE", "1"))
    runtime_interval = float(os.getenv("SULTAN_BINANCE_RUNTIME_INTERVAL_SECONDS", "5"))
    idle_sleep = float(os.getenv("SULTAN_BINANCE_IDLE_SLEEP_SECONDS", "0.5"))
    max_cycles = int(os.getenv("SULTAN_BINANCE_STACK_MAX_CYCLES", "0") or "0")
    telegram_summary_minutes = float(os.getenv("SULTAN_TELEGRAM_SUMMARY_MINUTES", "60") or "0")
    telegram_summary_on_start = _parse_bool(os.getenv("SULTAN_TELEGRAM_SUMMARY_ON_START", "1"))

    runtime_dir = Path(__file__).resolve().parent / "runtime"
    lock = FileProcessLock(
        runtime_dir / "state" / "locks" / f"binance_stack_{base_symbol.lower()}.lock",
        service_name=f"BINANCE_STACK_{base_symbol.upper()}",
        command="run_binance_stack.py",
    )
    try:
        lock.acquire()
    except ProcessAlreadyRunningError as error:
        print(f"Sultan Binance stack already running for {base_symbol} with pid={error.metadata.pid}.")
        return

    runtime_service = BinanceAutonomousService(
        base_symbol=base_symbol,
        execution_mode=execution_mode,
        auto_execute=auto_execute,
        poll_interval_seconds=runtime_interval,
        reconnect_backoff_seconds=max(runtime_interval, 10.0),
    )
    manager = SultanAIManager()
    reporter = runtime_service.reporter
    production_guard = ProductionGuard(
        base_symbol=base_symbol,
        config=load_production_guard_config_from_env(),
        reporter=reporter,
    )
    performance_tracker = PerformanceTracker(
        base_symbol=base_symbol,
        venue="BINANCE",
        config=load_performance_tracker_config_from_env(),
        reporter=reporter,
    )
    stack_state = JsonServiceStateStore(runtime_dir / "state" / f"binance_stack_{base_symbol.lower()}_status.json")

    print(f"Sultan Binance stack running for {base_symbol}...")
    next_runtime_at = 0.0
    next_summary_at = 0.0 if telegram_summary_on_start else time.monotonic() + (telegram_summary_minutes * 60.0)
    completed_cycles = 0
    latest_performance: dict | None = None
    try:
        while max_cycles <= 0 or completed_cycles < max_cycles:
            now = time.monotonic()
            if now >= next_runtime_at:
                service_state = runtime_service.run_cycle()
                snapshot = runtime_service.last_snapshot
                guard_outcome = None
                performance_outcome = None
                if production_guard.is_enabled:
                    guard_outcome = production_guard.handle_cycle(
                        snapshot,
                        service_state,
                        ledger_entries=list(runtime_service.runtime.auto_executor.ledger.entries),
                    )
                if performance_tracker.is_enabled:
                    performance_outcome = performance_tracker.handle_cycle(
                        snapshot,
                        service_state,
                        ledger_entries=list(runtime_service.runtime.auto_executor.ledger.entries),
                    )
                    latest_performance = {
                        "burn_in_status": performance_outcome.burn_in_status,
                        "burn_in_score": performance_outcome.burn_in_score,
                        **performance_outcome.metrics,
                    }
                completed_cycles += 1
                details = {
                    "telegram_enabled": reporter.is_enabled,
                    "binance_service_status": service_state.status,
                    "production_guard_enabled": production_guard.is_enabled,
                    "performance_tracker_enabled": performance_tracker.is_enabled,
                }
                if guard_outcome is not None:
                    details.update(
                        {
                            "kill_switch_active": guard_outcome.state.kill_switch_active,
                            "kill_switch_reason": guard_outcome.state.kill_switch_reason,
                            "daily_report_sent": guard_outcome.daily_report_sent,
                            "day_loss_percent": guard_outcome.metrics.get("day_loss_percent"),
                            "equity_drawdown_percent": guard_outcome.metrics.get("equity_drawdown_percent"),
                            "disconnect_minutes": guard_outcome.metrics.get("disconnect_minutes"),
                        }
                    )
                if performance_outcome is not None:
                    details.update(
                        {
                            "burn_in_status": performance_outcome.burn_in_status,
                            "burn_in_score": performance_outcome.burn_in_score,
                            "runtime_hours": performance_outcome.metrics.get("runtime_hours"),
                            "net_return_percent": performance_outcome.metrics.get("net_return_percent"),
                            "max_drawdown_percent": performance_outcome.metrics.get("max_drawdown_percent"),
                            "connected_ratio_percent": performance_outcome.metrics.get("connected_ratio_percent"),
                            "closed_trades": performance_outcome.metrics.get("closed_trades"),
                            "burn_in_report_sent": performance_outcome.report_sent,
                        }
                    )
                stack_state.write(
                    build_service_state(
                        service_name="SULTAN_BINANCE_STACK",
                        status=service_state.status,
                        base_symbol=base_symbol,
                        execution_mode=str(execution_mode),
                        auto_execute=auto_execute,
                        cycle_count=service_state.cycle_count,
                        error_count=service_state.error_count,
                        connected=service_state.connected,
                        bootstrapped=service_state.bootstrapped,
                        snapshot_timestamp=service_state.snapshot_timestamp,
                        last_error=service_state.last_error,
                        details=details,
                    )
                )
                next_runtime_at = now + runtime_interval

            if telegram_summary_minutes > 0 and reporter.is_enabled and now >= next_summary_at:
                summary_snapshot = runtime_service.last_snapshot or runtime_service.runtime.snapshot_dict()
                report = manager.audit_snapshot(summary_snapshot)
                reporter.send_text(build_periodic_summary_message(summary_snapshot, report, latest_performance))
                next_summary_at = now + (telegram_summary_minutes * 60.0)

            time.sleep(idle_sleep)
    finally:
        runtime_service.runtime.close()
        lock.release()


if __name__ == "__main__":
    main()
