from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sultan_engine_v2.manager.telegram_reporter import TelegramOperatorReporter
from sultan_engine_v2.runtime.file_utils import atomic_write_json
from sultan_engine_v2.runtime.service_state import ServiceState


def _parse_bool(raw_value: str | None, default: bool = True) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "on", "yes"}


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


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_iso_datetime(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True)
class PerformanceTrackerConfig:
    enabled: bool = True
    burn_in_report_enabled: bool = False
    burn_in_report_interval_minutes: float = 360.0
    burn_in_report_on_status_change: bool = True
    min_runtime_hours: float = 24.0
    min_cycles: int = 200
    min_closed_trades: int = 5
    min_connected_ratio: float = 0.95
    min_bootstrapped_ratio: float = 0.80
    max_drawdown_percent: float = 8.0
    max_error_events_per_100_cycles: float = 2.5
    min_net_return_percent: float = 0.0
    sample_every_cycles: int = 30


def load_performance_tracker_config_from_env() -> PerformanceTrackerConfig:
    enabled = _parse_bool(os.getenv("SULTAN_PERFORMANCE_TRACKER_ENABLED"), default=True)
    return PerformanceTrackerConfig(
        enabled=enabled,
        burn_in_report_enabled=_parse_bool(os.getenv("SULTAN_BURN_IN_REPORT_ENABLED"), default=enabled),
        burn_in_report_interval_minutes=max(5.0, _parse_float(os.getenv("SULTAN_BURN_IN_REPORT_INTERVAL_MINUTES"), 360.0)),
        burn_in_report_on_status_change=_parse_bool(os.getenv("SULTAN_BURN_IN_REPORT_ON_STATUS_CHANGE"), default=True),
        min_runtime_hours=max(0.0, _parse_float(os.getenv("SULTAN_BURN_IN_MIN_RUNTIME_HOURS"), 24.0)),
        min_cycles=max(1, _parse_int(os.getenv("SULTAN_BURN_IN_MIN_CYCLES"), 200)),
        min_closed_trades=max(0, _parse_int(os.getenv("SULTAN_BURN_IN_MIN_CLOSED_TRADES"), 5)),
        min_connected_ratio=max(0.0, min(1.0, _parse_float(os.getenv("SULTAN_BURN_IN_MIN_CONNECTED_RATIO"), 0.95))),
        min_bootstrapped_ratio=max(0.0, min(1.0, _parse_float(os.getenv("SULTAN_BURN_IN_MIN_BOOTSTRAPPED_RATIO"), 0.80))),
        max_drawdown_percent=max(0.0, _parse_float(os.getenv("SULTAN_BURN_IN_MAX_DRAWDOWN_PERCENT"), 8.0)),
        max_error_events_per_100_cycles=max(0.0, _parse_float(os.getenv("SULTAN_BURN_IN_MAX_ERROR_EVENTS_PER_100_CYCLES"), 2.5)),
        min_net_return_percent=_parse_float(os.getenv("SULTAN_BURN_IN_MIN_NET_RETURN_PERCENT"), 0.0),
        sample_every_cycles=max(1, _parse_int(os.getenv("SULTAN_PERFORMANCE_SAMPLE_EVERY_CYCLES"), 30)),
    )


@dataclass(slots=True)
class PerformanceTrackerState:
    base_symbol: str
    venue: str = ""
    started_at: str = ""
    updated_at: str = ""
    last_snapshot_timestamp: str | None = None
    session_start_balance: float = 0.0
    session_start_equity: float = 0.0
    last_balance: float = 0.0
    last_equity: float = 0.0
    peak_equity: float = 0.0
    trough_equity: float = 0.0
    max_drawdown_percent: float = 0.0
    total_cycles: int = 0
    connected_cycles: int = 0
    bootstrapped_cycles: int = 0
    total_error_events: int = 0
    last_service_error_count: int = 0
    executions_success: int = 0
    executions_failed: int = 0
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    realized_pnl: float = 0.0
    last_status: str = "COLLECTING_DATA"
    last_score: float = 0.0
    last_reasons: list[str] = field(default_factory=list)
    last_report_at: str | None = None
    last_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PerformanceTrackerOutcome:
    enabled: bool
    state: PerformanceTrackerState
    metrics: dict[str, Any]
    burn_in_status: str = "DISABLED"
    burn_in_score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    report_sent: bool = False


class JsonPerformanceTrackerStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, base_symbol: str) -> PerformanceTrackerState:
        if not self.path.exists():
            return PerformanceTrackerState(base_symbol=base_symbol)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return PerformanceTrackerState(base_symbol=base_symbol)

        return PerformanceTrackerState(
            base_symbol=str(payload.get("base_symbol") or base_symbol),
            venue=str(payload.get("venue") or ""),
            started_at=str(payload.get("started_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            last_snapshot_timestamp=payload.get("last_snapshot_timestamp"),
            session_start_balance=_safe_float(payload.get("session_start_balance")),
            session_start_equity=_safe_float(payload.get("session_start_equity")),
            last_balance=_safe_float(payload.get("last_balance")),
            last_equity=_safe_float(payload.get("last_equity")),
            peak_equity=_safe_float(payload.get("peak_equity")),
            trough_equity=_safe_float(payload.get("trough_equity")),
            max_drawdown_percent=_safe_float(payload.get("max_drawdown_percent")),
            total_cycles=max(0, int(payload.get("total_cycles") or 0)),
            connected_cycles=max(0, int(payload.get("connected_cycles") or 0)),
            bootstrapped_cycles=max(0, int(payload.get("bootstrapped_cycles") or 0)),
            total_error_events=max(0, int(payload.get("total_error_events") or 0)),
            last_service_error_count=max(0, int(payload.get("last_service_error_count") or 0)),
            executions_success=max(0, int(payload.get("executions_success") or 0)),
            executions_failed=max(0, int(payload.get("executions_failed") or 0)),
            closed_trades=max(0, int(payload.get("closed_trades") or 0)),
            wins=max(0, int(payload.get("wins") or 0)),
            losses=max(0, int(payload.get("losses") or 0)),
            realized_pnl=_safe_float(payload.get("realized_pnl")),
            last_status=str(payload.get("last_status") or "COLLECTING_DATA"),
            last_score=_safe_float(payload.get("last_score")),
            last_reasons=[str(item) for item in list(payload.get("last_reasons") or [])],
            last_report_at=payload.get("last_report_at"),
            last_metrics=dict(payload.get("last_metrics") or {}),
        )

    def save(self, state: PerformanceTrackerState) -> PerformanceTrackerState:
        atomic_write_json(self.path, asdict(state))
        return state


class PerformanceTracker:
    def __init__(
        self,
        *,
        base_symbol: str,
        venue: str = "",
        config: PerformanceTrackerConfig | None = None,
        state_store: JsonPerformanceTrackerStateStore | None = None,
        history_path: str | Path | None = None,
        reporter: TelegramOperatorReporter | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_symbol = base_symbol
        self.venue = venue.upper()
        self.config = config or load_performance_tracker_config_from_env()
        runtime_dir = Path(__file__).resolve().parent
        venue_slug = (self.venue or "runtime").lower()
        symbol_slug = base_symbol.lower()
        self.state_store = state_store or JsonPerformanceTrackerStateStore(runtime_dir / "state" / f"performance_{venue_slug}_{symbol_slug}.json")
        self.history_path = Path(history_path) if history_path else runtime_dir / "history" / f"performance_{venue_slug}_{symbol_slug}.jsonl"
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.reporter = reporter or TelegramOperatorReporter()
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    @property
    def is_enabled(self) -> bool:
        return self.config.enabled

    def handle_cycle(
        self,
        snapshot: dict[str, Any] | None,
        service_state: ServiceState,
        *,
        ledger_entries: list[dict[str, Any]] | None = None,
    ) -> PerformanceTrackerOutcome:
        state = self.state_store.load(self.base_symbol)
        if not self.is_enabled:
            return PerformanceTrackerOutcome(enabled=False, state=state, metrics={})

        snapshot = snapshot or {}
        now_utc = self._utc_now()
        state.venue = str(snapshot.get("venue") or self.venue or state.venue or "").upper()
        if not state.started_at:
            state.started_at = now_utc.isoformat()

        state.total_cycles += 1
        if bool(snapshot.get("health", {}).get("connected", service_state.connected)):
            state.connected_cycles += 1
        if bool(snapshot.get("health", {}).get("bootstrapped", service_state.bootstrapped)):
            state.bootstrapped_cycles += 1

        current_errors = max(0, int(service_state.error_count or 0))
        if current_errors >= state.last_service_error_count:
            state.total_error_events += current_errors - state.last_service_error_count
        else:
            state.total_error_events += current_errors
        state.last_service_error_count = current_errors

        account = self._resolve_account_snapshot(snapshot)
        balance = _safe_float(account.get("balance"))
        equity = _safe_float(account.get("equity"))
        if state.session_start_balance <= 0 and balance > 0:
            state.session_start_balance = balance
        if state.session_start_equity <= 0 and equity > 0:
            state.session_start_equity = equity
        if equity > 0:
            state.peak_equity = max(state.peak_equity, equity)
            state.trough_equity = equity if state.trough_equity <= 0 else min(state.trough_equity, equity)
            if state.peak_equity > 0:
                drawdown_percent = max(((state.peak_equity - equity) / state.peak_equity) * 100.0, 0.0)
                state.max_drawdown_percent = max(state.max_drawdown_percent, drawdown_percent)
        state.last_balance = balance
        state.last_equity = equity
        state.last_snapshot_timestamp = snapshot.get("timestamp")

        success_count, failure_count = self._count_ledger_results(ledger_entries or [])
        state.executions_success = success_count
        state.executions_failed = failure_count

        trade_snapshot = snapshot.get("paper_account") if isinstance(snapshot.get("paper_account"), dict) else {}
        state.closed_trades = max(0, int(trade_snapshot.get("closed_trade_count") or state.closed_trades))
        state.wins = max(0, int(trade_snapshot.get("win_count") or state.wins))
        state.losses = max(0, int(trade_snapshot.get("loss_count") or state.losses))
        state.realized_pnl = _safe_float(trade_snapshot.get("realized_pnl")) if trade_snapshot else state.realized_pnl

        metrics = self._build_metrics(snapshot=snapshot, state=state, service_state=service_state, now_utc=now_utc)
        status, score, reasons = self._evaluate_burn_in(metrics=metrics)
        previous_status = state.last_status
        state.last_status = status
        state.last_score = score
        state.last_reasons = reasons
        state.last_metrics = metrics
        state.updated_at = now_utc.isoformat()

        report_sent = self._maybe_send_report(snapshot=snapshot, state=state, metrics=metrics, status=status, score=score, reasons=reasons, previous_status=previous_status, now_utc=now_utc)
        self.state_store.save(state)
        self._maybe_append_history(snapshot=snapshot, state=state, metrics=metrics, status=status, score=score, reasons=reasons, previous_status=previous_status)
        return PerformanceTrackerOutcome(
            enabled=True,
            state=state,
            metrics=metrics,
            burn_in_status=status,
            burn_in_score=score,
            reasons=reasons,
            report_sent=report_sent,
        )

    def _utc_now(self) -> datetime:
        current = self.now_provider()
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def _build_metrics(
        self,
        *,
        snapshot: dict[str, Any],
        state: PerformanceTrackerState,
        service_state: ServiceState,
        now_utc: datetime,
    ) -> dict[str, Any]:
        started_at = _parse_iso_datetime(state.started_at) or now_utc
        elapsed_hours = max((now_utc - started_at).total_seconds(), 0.0) / 3600.0
        connected_ratio = (state.connected_cycles / state.total_cycles) if state.total_cycles > 0 else 0.0
        bootstrapped_ratio = (state.bootstrapped_cycles / state.total_cycles) if state.total_cycles > 0 else 0.0
        error_rate = ((state.total_error_events / state.total_cycles) * 100.0) if state.total_cycles > 0 else 0.0
        net_balance_delta = state.last_balance - state.session_start_balance if state.session_start_balance > 0 else 0.0
        net_equity_delta = state.last_equity - state.session_start_equity if state.session_start_equity > 0 else 0.0
        net_return_percent = ((net_equity_delta / state.session_start_equity) * 100.0) if state.session_start_equity > 0 else 0.0
        win_rate_percent = ((state.wins / state.closed_trades) * 100.0) if state.closed_trades > 0 else 0.0
        recent_decisions = snapshot.get("execution", {}).get("recent_decisions", [])
        recent_blocked = sum(1 for item in recent_decisions if isinstance(item, dict) and not bool(item.get("allowed", True)))
        return {
            "symbol": snapshot.get("symbol", {}).get("actual") or snapshot.get("symbol", {}).get("requested") or self.base_symbol,
            "venue": str(snapshot.get("venue") or self.venue or state.venue or ""),
            "currency": str(self._resolve_account_snapshot(snapshot).get("currency") or ""),
            "runtime_hours": elapsed_hours,
            "total_cycles": state.total_cycles,
            "connected_cycles": state.connected_cycles,
            "bootstrapped_cycles": state.bootstrapped_cycles,
            "connected_ratio": connected_ratio,
            "connected_ratio_percent": connected_ratio * 100.0,
            "bootstrapped_ratio": bootstrapped_ratio,
            "bootstrapped_ratio_percent": bootstrapped_ratio * 100.0,
            "balance": state.last_balance,
            "equity": state.last_equity,
            "floating_pnl": state.last_equity - state.last_balance,
            "net_balance_delta": net_balance_delta,
            "net_equity_delta": net_equity_delta,
            "net_return_percent": net_return_percent,
            "max_drawdown_percent": state.max_drawdown_percent,
            "error_events_total": state.total_error_events,
            "error_events_per_100_cycles": error_rate,
            "service_error_count": service_state.error_count,
            "executions_success": state.executions_success,
            "executions_failed": state.executions_failed,
            "recent_blocked": recent_blocked,
            "closed_trades": state.closed_trades,
            "wins": state.wins,
            "losses": state.losses,
            "win_rate_percent": win_rate_percent,
            "realized_pnl": state.realized_pnl,
            "trade_metric_available": bool(snapshot.get("paper_account")),
            "focus_status": snapshot.get("setup_focus", {}).get("status") or "NO_ACTIVE_VR",
        }

    def _evaluate_burn_in(self, *, metrics: dict[str, Any]) -> tuple[str, float, list[str]]:
        collecting_reasons: list[str] = []
        fail_reasons: list[str] = []

        runtime_hours = _safe_float(metrics.get("runtime_hours"))
        total_cycles = max(0, int(metrics.get("total_cycles") or 0))
        connected_ratio = _safe_float(metrics.get("connected_ratio"))
        bootstrapped_ratio = _safe_float(metrics.get("bootstrapped_ratio"))
        closed_trades = max(0, int(metrics.get("closed_trades") or 0))
        trade_metric_available = bool(metrics.get("trade_metric_available"))
        net_return_percent = _safe_float(metrics.get("net_return_percent"))
        max_drawdown_percent = _safe_float(metrics.get("max_drawdown_percent"))
        error_rate = _safe_float(metrics.get("error_events_per_100_cycles"))

        if runtime_hours < self.config.min_runtime_hours:
            collecting_reasons.append(f"runtime {runtime_hours:.1f}h/{self.config.min_runtime_hours:.1f}h")
        if total_cycles < self.config.min_cycles:
            collecting_reasons.append(f"cycles {total_cycles}/{self.config.min_cycles}")
        if trade_metric_available and closed_trades < self.config.min_closed_trades:
            collecting_reasons.append(f"closed trades {closed_trades}/{self.config.min_closed_trades}")

        if self.config.min_connected_ratio > 0 and connected_ratio < self.config.min_connected_ratio:
            fail_reasons.append(f"connected ratio {connected_ratio * 100.0:.1f}%/{self.config.min_connected_ratio * 100.0:.1f}%")
        if self.config.min_bootstrapped_ratio > 0 and bootstrapped_ratio < self.config.min_bootstrapped_ratio:
            fail_reasons.append(f"bootstrapped ratio {bootstrapped_ratio * 100.0:.1f}%/{self.config.min_bootstrapped_ratio * 100.0:.1f}%")
        if self.config.max_drawdown_percent > 0 and max_drawdown_percent > self.config.max_drawdown_percent:
            fail_reasons.append(f"drawdown {max_drawdown_percent:.2f}%/{self.config.max_drawdown_percent:.2f}%")
        if self.config.max_error_events_per_100_cycles > 0 and error_rate > self.config.max_error_events_per_100_cycles:
            fail_reasons.append(f"errors {error_rate:.2f}/{self.config.max_error_events_per_100_cycles:.2f} per 100 cycles")
        if not collecting_reasons and net_return_percent < self.config.min_net_return_percent:
            fail_reasons.append(f"net return {net_return_percent:.2f}%/{self.config.min_net_return_percent:.2f}%")

        score = self._build_score(metrics=metrics)
        if fail_reasons:
            return "NOT_READY", score, fail_reasons
        if collecting_reasons:
            return "COLLECTING_DATA", score, collecting_reasons
        if trade_metric_available:
            return "READY_FOR_SMALL_RISK_TRIAL", score, ["burn-in thresholds met"]
        return "PAPER_STABLE", score, ["stability thresholds met"]

    def _build_score(self, *, metrics: dict[str, Any]) -> float:
        def _ratio(value: float, target: float) -> float:
            if target <= 0:
                return 1.0
            return max(0.0, min(value / target, 1.0))

        runtime_score = _ratio(_safe_float(metrics.get("runtime_hours")), self.config.min_runtime_hours) * 15.0
        cycle_score = _ratio(_safe_float(metrics.get("total_cycles")), float(self.config.min_cycles)) * 15.0
        if bool(metrics.get("trade_metric_available")):
            trade_score = _ratio(_safe_float(metrics.get("closed_trades")), float(max(self.config.min_closed_trades, 1))) * 10.0
        else:
            trade_score = 10.0 if _safe_float(metrics.get("total_cycles")) >= float(self.config.min_cycles) else 0.0
        connected_score = min(_ratio(_safe_float(metrics.get("connected_ratio")), self.config.min_connected_ratio), 1.0) * 15.0
        boot_score = min(_ratio(_safe_float(metrics.get("bootstrapped_ratio")), self.config.min_bootstrapped_ratio), 1.0) * 10.0
        if self.config.max_drawdown_percent > 0:
            drawdown_score = max(0.0, 1.0 - (_safe_float(metrics.get("max_drawdown_percent")) / self.config.max_drawdown_percent)) * 15.0
        else:
            drawdown_score = 15.0
        if self.config.max_error_events_per_100_cycles > 0:
            error_score = max(0.0, 1.0 - (_safe_float(metrics.get("error_events_per_100_cycles")) / self.config.max_error_events_per_100_cycles)) * 10.0
        else:
            error_score = 10.0
        return_target = max(abs(self.config.min_net_return_percent), 5.0)
        return_score = max(0.0, min((_safe_float(metrics.get("net_return_percent")) + return_target) / (return_target * 2.0), 1.0)) * 10.0
        return round(runtime_score + cycle_score + trade_score + connected_score + boot_score + drawdown_score + error_score + return_score, 2)

    def _maybe_append_history(
        self,
        *,
        snapshot: dict[str, Any],
        state: PerformanceTrackerState,
        metrics: dict[str, Any],
        status: str,
        score: float,
        reasons: list[str],
        previous_status: str,
    ) -> None:
        if state.total_cycles % self.config.sample_every_cycles != 0 and status == previous_status:
            return
        payload = {
            "timestamp": state.updated_at,
            "symbol": metrics.get("symbol", self.base_symbol),
            "venue": metrics.get("venue", state.venue),
            "status": status,
            "score": score,
            "runtime_hours": round(_safe_float(metrics.get("runtime_hours")), 4),
            "total_cycles": state.total_cycles,
            "connected_ratio_percent": round(_safe_float(metrics.get("connected_ratio_percent")), 2),
            "bootstrapped_ratio_percent": round(_safe_float(metrics.get("bootstrapped_ratio_percent")), 2),
            "balance": round(_safe_float(metrics.get("balance")), 8),
            "equity": round(_safe_float(metrics.get("equity")), 8),
            "net_return_percent": round(_safe_float(metrics.get("net_return_percent")), 4),
            "max_drawdown_percent": round(_safe_float(metrics.get("max_drawdown_percent")), 4),
            "closed_trades": int(metrics.get("closed_trades") or 0),
            "win_rate_percent": round(_safe_float(metrics.get("win_rate_percent")), 2),
            "focus_status": metrics.get("focus_status", snapshot.get("setup_focus", {}).get("status")),
            "reasons": reasons,
        }
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def _maybe_send_report(
        self,
        *,
        snapshot: dict[str, Any],
        state: PerformanceTrackerState,
        metrics: dict[str, Any],
        status: str,
        score: float,
        reasons: list[str],
        previous_status: str,
        now_utc: datetime,
    ) -> bool:
        if not self.config.burn_in_report_enabled or not self.reporter.is_enabled:
            return False

        should_send = False
        if self.config.burn_in_report_on_status_change and status != previous_status:
            should_send = True
        else:
            last_report_at = _parse_iso_datetime(state.last_report_at)
            if last_report_at is None:
                should_send = True
            else:
                interval = timedelta(minutes=self.config.burn_in_report_interval_minutes)
                should_send = (now_utc - last_report_at) >= interval

        if not should_send:
            return False

        if not self.reporter.send_text(self._build_report_message(snapshot=snapshot, metrics=metrics, status=status, score=score, reasons=reasons)):
            return False

        state.last_report_at = now_utc.isoformat()
        return True

    def _build_report_message(
        self,
        *,
        snapshot: dict[str, Any],
        metrics: dict[str, Any],
        status: str,
        score: float,
        reasons: list[str],
    ) -> str:
        currency = str(metrics.get("currency") or "").strip()
        focus = snapshot.get("setup_focus", {})
        lines = [
            "Sultan Burn-In Report",
            f"Venue: {metrics.get('venue') or '-'} | Symbol: {metrics.get('symbol') or self.base_symbol}",
            f"Status: {status} | Score: {score:.1f}/100",
            f"Runtime: {metrics.get('runtime_hours', 0.0):.1f}h | Cycles: {int(metrics.get('total_cycles') or 0)}",
            f"Connect: {metrics.get('connected_ratio_percent', 0.0):.1f}% | Bootstrap: {metrics.get('bootstrapped_ratio_percent', 0.0):.1f}%",
            f"Balance: {metrics.get('balance', 0.0):,.2f} {currency}".rstrip(),
            f"Equity: {metrics.get('equity', 0.0):,.2f} {currency}".rstrip(),
            f"Net Return: {metrics.get('net_return_percent', 0.0):+,.2f}% | Drawdown: {metrics.get('max_drawdown_percent', 0.0):.2f}%",
            f"Exec: {int(metrics.get('executions_success') or 0)} ok / {int(metrics.get('executions_failed') or 0)} fail / {int(metrics.get('recent_blocked') or 0)} recent block",
            f"Focus: {focus.get('status') or metrics.get('focus_status') or 'NO_ACTIVE_VR'} | Master: {focus.get('master_timeframe') or '-'} | VR: {focus.get('vr_timeframe') or '-'}",
        ]
        if bool(metrics.get("trade_metric_available")):
            lines.append(
                f"Trades: {int(metrics.get('closed_trades') or 0)} closed | Win Rate: {metrics.get('win_rate_percent', 0.0):.1f}% | Realized: {metrics.get('realized_pnl', 0.0):+,.2f} {currency}".rstrip()
            )
        lines.append(f"Notes: {', '.join(reasons[:3]) or '-'}")
        return "\n".join(lines)

    @staticmethod
    def _resolve_account_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        mt5 = snapshot.get("mt5")
        if isinstance(mt5, dict) and ("balance" in mt5 or "equity" in mt5):
            return mt5
        paper_account = snapshot.get("paper_account")
        if isinstance(paper_account, dict):
            return paper_account
        return {}

    @staticmethod
    def _count_ledger_results(entries: list[dict[str, Any]]) -> tuple[int, int]:
        success = 0
        failed = 0
        for item in entries:
            if bool(item.get("success")):
                success += 1
            else:
                failed += 1
        return success, failed
