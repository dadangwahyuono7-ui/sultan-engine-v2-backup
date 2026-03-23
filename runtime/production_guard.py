from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sultan_engine_v2.manager.operator_control import JsonOperatorControlStore
from sultan_engine_v2.manager.telegram_reporter import TelegramOperatorReporter
from sultan_engine_v2.runtime.file_utils import atomic_write_json
from sultan_engine_v2.runtime.service_state import ServiceState


def _parse_bool(raw_value: str | None, default: bool = False) -> bool:
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
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_utc_offset(raw_value: str | None) -> timezone:
    raw = (raw_value or "+00:00").strip()
    if raw in {"UTC", "utc", "Z", "z"}:
        return timezone.utc
    sign = -1 if raw.startswith("-") else 1
    normalized = raw[1:] if raw[:1] in {"+", "-"} else raw
    hours_text, _, minutes_text = normalized.partition(":")
    try:
        hours = int(hours_text or "0")
        minutes = int(minutes_text or "0")
    except ValueError:
        return timezone.utc
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


@dataclass(slots=True)
class ProductionGuardConfig:
    enabled: bool = False
    daily_report_enabled: bool = False
    daily_report_hour: int = 21
    daily_report_minute: int = 0
    report_utc_offset: str = "+00:00"
    kill_switch_enabled: bool = False
    max_equity_drawdown_percent: float = 8.0
    max_daily_loss_percent: float = 5.0
    max_service_errors: int = 10
    max_disconnect_minutes: float = 15.0


def load_production_guard_config_from_env() -> ProductionGuardConfig:
    enabled = _parse_bool(os.getenv("SULTAN_PRODUCTION_GUARD_ENABLED"), default=False)
    daily_report_enabled = _parse_bool(os.getenv("SULTAN_DAILY_REPORT_ENABLED"), default=enabled)
    kill_switch_enabled = _parse_bool(os.getenv("SULTAN_KILL_SWITCH_ENABLED"), default=enabled)
    return ProductionGuardConfig(
        enabled=enabled,
        daily_report_enabled=daily_report_enabled,
        daily_report_hour=max(0, min(23, _parse_int(os.getenv("SULTAN_DAILY_REPORT_HOUR"), 21))),
        daily_report_minute=max(0, min(59, _parse_int(os.getenv("SULTAN_DAILY_REPORT_MINUTE"), 0))),
        report_utc_offset=os.getenv("SULTAN_REPORT_UTC_OFFSET", "+00:00"),
        kill_switch_enabled=kill_switch_enabled,
        max_equity_drawdown_percent=max(0.0, _parse_float(os.getenv("SULTAN_KILL_SWITCH_MAX_EQUITY_DRAWDOWN_PERCENT"), 8.0)),
        max_daily_loss_percent=max(0.0, _parse_float(os.getenv("SULTAN_KILL_SWITCH_MAX_DAILY_LOSS_PERCENT"), 5.0)),
        max_service_errors=max(0, _parse_int(os.getenv("SULTAN_KILL_SWITCH_MAX_SERVICE_ERRORS"), 10)),
        max_disconnect_minutes=max(0.0, _parse_float(os.getenv("SULTAN_KILL_SWITCH_MAX_DISCONNECT_MINUTES"), 15.0)),
    )


@dataclass(slots=True)
class ProductionGuardState:
    base_symbol: str
    updated_at: str = ""
    current_day: str | None = None
    day_started_at: str | None = None
    day_start_balance: float = 0.0
    day_start_equity: float = 0.0
    day_peak_equity: float = 0.0
    lifetime_peak_equity: float = 0.0
    disconnected_since: str | None = None
    last_daily_report_day: str | None = None
    kill_switch_active: bool = False
    kill_switch_reason: str | None = None
    kill_switch_triggered_at: str | None = None
    kill_switch_trigger_count: int = 0
    last_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProductionGuardOutcome:
    enabled: bool
    state: ProductionGuardState
    metrics: dict[str, Any]
    daily_report_sent: bool = False
    kill_switch_triggered: bool = False
    kill_switch_reason: str | None = None


class JsonProductionGuardStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, base_symbol: str) -> ProductionGuardState:
        if not self.path.exists():
            return ProductionGuardState(base_symbol=base_symbol)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ProductionGuardState(base_symbol=base_symbol)

        return ProductionGuardState(
            base_symbol=str(payload.get("base_symbol") or base_symbol),
            updated_at=str(payload.get("updated_at") or ""),
            current_day=payload.get("current_day"),
            day_started_at=payload.get("day_started_at"),
            day_start_balance=_safe_float(payload.get("day_start_balance")),
            day_start_equity=_safe_float(payload.get("day_start_equity")),
            day_peak_equity=_safe_float(payload.get("day_peak_equity")),
            lifetime_peak_equity=_safe_float(payload.get("lifetime_peak_equity")),
            disconnected_since=payload.get("disconnected_since"),
            last_daily_report_day=payload.get("last_daily_report_day"),
            kill_switch_active=bool(payload.get("kill_switch_active", False)),
            kill_switch_reason=payload.get("kill_switch_reason"),
            kill_switch_triggered_at=payload.get("kill_switch_triggered_at"),
            kill_switch_trigger_count=int(payload.get("kill_switch_trigger_count") or 0),
            last_metrics=dict(payload.get("last_metrics") or {}),
        )

    def save(self, state: ProductionGuardState) -> ProductionGuardState:
        atomic_write_json(self.path, asdict(state))
        return state


class ProductionGuard:
    def __init__(
        self,
        *,
        base_symbol: str,
        config: ProductionGuardConfig | None = None,
        state_store: JsonProductionGuardStateStore | None = None,
        operator_controls: JsonOperatorControlStore | None = None,
        reporter: TelegramOperatorReporter | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_symbol = base_symbol
        self.config = config or load_production_guard_config_from_env()
        runtime_dir = Path(__file__).resolve().parent
        self.state_store = state_store or JsonProductionGuardStateStore(runtime_dir / "state" / f"production_guard_{base_symbol.lower()}_status.json")
        self.operator_controls = operator_controls or JsonOperatorControlStore(runtime_dir / "state" / "operator_control.json")
        self.reporter = reporter or TelegramOperatorReporter()
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.report_timezone = _parse_utc_offset(self.config.report_utc_offset)

    @property
    def is_enabled(self) -> bool:
        return self.config.enabled

    def handle_cycle(
        self,
        snapshot: dict[str, Any] | None,
        service_state: ServiceState,
        *,
        ledger_entries: list[dict[str, Any]] | None = None,
    ) -> ProductionGuardOutcome:
        state = self.state_store.load(self.base_symbol)
        if not self.is_enabled:
            return ProductionGuardOutcome(enabled=False, state=state, metrics={})

        snapshot = snapshot or {}
        now_utc = self._utc_now()
        local_now = now_utc.astimezone(self.report_timezone)
        operator_snapshot = snapshot.get("operator", {})
        if state.kill_switch_active and "operator" in snapshot and not bool(operator_snapshot.get("paused")):
            state.kill_switch_active = False
            state.kill_switch_reason = None
            state.kill_switch_triggered_at = None

        account = self._resolve_account_snapshot(snapshot)
        balance = _safe_float(account.get("balance"))
        equity = _safe_float(account.get("equity"))
        connected = bool(snapshot.get("health", {}).get("connected", service_state.connected))
        state = self._roll_state_forward(state, now_utc=now_utc, local_now=local_now, balance=balance, equity=equity, connected=connected)
        metrics = self._build_metrics(
            snapshot=snapshot,
            service_state=service_state,
            state=state,
            now_utc=now_utc,
            local_now=local_now,
            ledger_entries=ledger_entries or [],
        )
        state.last_metrics = metrics

        daily_report_sent = self._maybe_send_daily_report(snapshot=snapshot, state=state, metrics=metrics, local_now=local_now)
        kill_switch_reason = self._evaluate_kill_switch(service_state=service_state, metrics=metrics)
        self._maybe_resume_stale_guard_pause(snapshot=snapshot, state=state, metrics=metrics, reason=kill_switch_reason)
        kill_switch_triggered = self._maybe_trigger_kill_switch(snapshot=snapshot, state=state, metrics=metrics, reason=kill_switch_reason, now_utc=now_utc)

        state.updated_at = now_utc.isoformat()
        self.state_store.save(state)
        return ProductionGuardOutcome(
            enabled=True,
            state=state,
            metrics=metrics,
            daily_report_sent=daily_report_sent,
            kill_switch_triggered=kill_switch_triggered,
            kill_switch_reason=kill_switch_reason,
        )

    def _utc_now(self) -> datetime:
        current = self.now_provider()
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def _roll_state_forward(
        self,
        state: ProductionGuardState,
        *,
        now_utc: datetime,
        local_now: datetime,
        balance: float,
        equity: float,
        connected: bool,
    ) -> ProductionGuardState:
        day_key = local_now.date().isoformat()
        if state.current_day != day_key:
            state.current_day = day_key
            state.day_started_at = now_utc.isoformat()
            state.day_start_balance = balance if balance > 0 else 0.0
            state.day_start_equity = equity if equity > 0 else 0.0
            state.day_peak_equity = equity if equity > 0 else 0.0

        if state.day_start_balance <= 0 and balance > 0:
            state.day_start_balance = balance
        if state.day_start_equity <= 0 and equity > 0:
            state.day_start_equity = equity
        if equity > 0:
            state.day_peak_equity = max(state.day_peak_equity, equity)
            state.lifetime_peak_equity = max(state.lifetime_peak_equity, equity)

        if connected:
            state.disconnected_since = None
        elif state.disconnected_since is None:
            state.disconnected_since = now_utc.isoformat()
        return state

    def _build_metrics(
        self,
        *,
        snapshot: dict[str, Any],
        service_state: ServiceState,
        state: ProductionGuardState,
        now_utc: datetime,
        local_now: datetime,
        ledger_entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        account = self._resolve_account_snapshot(snapshot)
        balance = _safe_float(account.get("balance"))
        equity = _safe_float(account.get("equity"))
        day_balance_delta = balance - state.day_start_balance if state.day_start_balance > 0 else 0.0
        day_equity_delta = equity - state.day_start_equity if state.day_start_equity > 0 else 0.0

        daily_loss_candidates: list[float] = []
        if state.day_start_balance > 0:
            daily_loss_candidates.append(((state.day_start_balance - balance) / state.day_start_balance) * 100.0)
        if state.day_start_equity > 0:
            daily_loss_candidates.append(((state.day_start_equity - equity) / state.day_start_equity) * 100.0)
        daily_loss_percent = max([value for value in daily_loss_candidates] + [0.0])

        peak_equity = max(state.lifetime_peak_equity, state.day_peak_equity, equity)
        equity_drawdown_percent = (((peak_equity - equity) / peak_equity) * 100.0) if peak_equity > 0 else 0.0
        disconnected_since = _parse_iso_datetime(state.disconnected_since)
        disconnect_minutes = 0.0
        if disconnected_since is not None:
            disconnect_minutes = max((now_utc - disconnected_since).total_seconds(), 0.0) / 60.0

        success_count = 0
        failure_count = 0
        for entry in ledger_entries:
            recorded_at = _parse_iso_datetime(str(entry.get("recorded_at") or ""))
            if recorded_at is None:
                continue
            if recorded_at.astimezone(self.report_timezone).date().isoformat() != state.current_day:
                continue
            if bool(entry.get("success")):
                success_count += 1
            else:
                failure_count += 1

        recent_decisions = snapshot.get("execution", {}).get("recent_decisions", [])
        blocked_recent = 0
        for decision in recent_decisions:
            if isinstance(decision, dict) and not bool(decision.get("allowed", True)):
                blocked_recent += 1

        return {
            "symbol": snapshot.get("symbol", {}).get("actual") or snapshot.get("symbol", {}).get("requested") or self.base_symbol,
            "current_day": state.current_day,
            "local_time": local_now.isoformat(),
            "execution_mode": str(snapshot.get("execution", {}).get("mode") or service_state.execution_mode),
            "focus_status": snapshot.get("setup_focus", {}).get("status") or "NO_ACTIVE_VR",
            "balance": balance,
            "equity": equity,
            "floating_pnl": equity - balance,
            "day_balance_delta": day_balance_delta,
            "day_equity_delta": day_equity_delta,
            "day_loss_percent": max(daily_loss_percent, 0.0),
            "equity_drawdown_percent": max(equity_drawdown_percent, 0.0),
            "disconnect_minutes": disconnect_minutes,
            "service_error_count": service_state.error_count,
            "open_structure_count": len(snapshot.get("lifecycle", {}).get("position_structure", [])),
            "operator_paused": bool(snapshot.get("operator", {}).get("paused", False)),
            "entries_enabled": bool(snapshot.get("execution", {}).get("entries_enabled", False)),
            "executions_today_success": success_count,
            "executions_today_failed": failure_count,
            "blocked_recent": blocked_recent,
            "connected": bool(snapshot.get("health", {}).get("connected", service_state.connected)),
            "currency": str(account.get("currency") or ""),
        }

    @staticmethod
    def _resolve_account_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        mt5 = snapshot.get("mt5")
        if isinstance(mt5, dict) and ("balance" in mt5 or "equity" in mt5):
            return mt5
        paper_account = snapshot.get("paper_account")
        if isinstance(paper_account, dict):
            return paper_account
        return {}

    def _maybe_send_daily_report(
        self,
        *,
        snapshot: dict[str, Any],
        state: ProductionGuardState,
        metrics: dict[str, Any],
        local_now: datetime,
    ) -> bool:
        if not self.config.daily_report_enabled or not self.reporter.is_enabled or not state.current_day:
            return False

        scheduled_at = local_now.replace(hour=self.config.daily_report_hour, minute=self.config.daily_report_minute, second=0, microsecond=0)
        if local_now < scheduled_at or state.last_daily_report_day == state.current_day:
            return False

        message = self._build_daily_report_message(snapshot=snapshot, state=state, metrics=metrics)
        if not self.reporter.send_text(message):
            return False

        state.last_daily_report_day = state.current_day
        return True

    def _evaluate_kill_switch(self, *, service_state: ServiceState, metrics: dict[str, Any]) -> str | None:
        if not self.config.kill_switch_enabled:
            return None
        if self.config.max_service_errors > 0 and service_state.error_count >= self.config.max_service_errors:
            return f"SERVICE_ERRORS limit hit ({service_state.error_count}/{self.config.max_service_errors})"
        if self.config.max_disconnect_minutes > 0 and metrics.get("disconnect_minutes", 0.0) >= self.config.max_disconnect_minutes:
            return f"DISCONNECT timeout hit ({metrics.get('disconnect_minutes', 0.0):.1f}m/{self.config.max_disconnect_minutes:.1f}m)"
        if self.config.max_daily_loss_percent > 0 and metrics.get("day_loss_percent", 0.0) >= self.config.max_daily_loss_percent:
            return f"DAILY_LOSS limit hit ({metrics.get('day_loss_percent', 0.0):.2f}%/{self.config.max_daily_loss_percent:.2f}%)"
        if self.config.max_equity_drawdown_percent > 0 and metrics.get("equity_drawdown_percent", 0.0) >= self.config.max_equity_drawdown_percent:
            return f"EQUITY_DRAWDOWN limit hit ({metrics.get('equity_drawdown_percent', 0.0):.2f}%/{self.config.max_equity_drawdown_percent:.2f}%)"
        return None

    def _maybe_resume_stale_guard_pause(
        self,
        *,
        snapshot: dict[str, Any],
        state: ProductionGuardState,
        metrics: dict[str, Any],
        reason: str | None,
    ) -> bool:
        operator_snapshot = snapshot.get("operator", {})
        if not bool(operator_snapshot.get("paused", False)):
            return False
        if (operator_snapshot.get("last_source") or "") != "PRODUCTION_GUARD":
            return False
        if reason:
            return False

        execution_mode = str(metrics.get("execution_mode") or "").upper()
        if execution_mode == "LIVE":
            return False

        message = "Production guard auto-resume | stale pause cleared after safe paper cycle"
        self.operator_controls.record_command(
            "RESUME",
            source="PRODUCTION_GUARD",
            message=message,
            interpreter="RULE",
            response=message,
        )
        state.kill_switch_active = False
        state.kill_switch_reason = None
        state.kill_switch_triggered_at = None
        return True

    def _maybe_trigger_kill_switch(
        self,
        *,
        snapshot: dict[str, Any],
        state: ProductionGuardState,
        metrics: dict[str, Any],
        reason: str | None,
        now_utc: datetime,
    ) -> bool:
        if not reason:
            return False
        if state.kill_switch_active and state.kill_switch_reason == reason and bool(snapshot.get("operator", {}).get("paused", False)):
            return False

        message = f"Production guard auto-pause | {reason}"
        self.operator_controls.record_command(
            "PAUSE",
            source="PRODUCTION_GUARD",
            message=message,
            interpreter="RULE",
            response=message,
            reason=reason,
        )
        state.kill_switch_active = True
        state.kill_switch_reason = reason
        state.kill_switch_triggered_at = now_utc.isoformat()
        state.kill_switch_trigger_count += 1
        if self.reporter.is_enabled:
            self.reporter.send_text(self._build_kill_switch_message(snapshot=snapshot, state=state, metrics=metrics, reason=reason))
        return True

    def _build_daily_report_message(
        self,
        *,
        snapshot: dict[str, Any],
        state: ProductionGuardState,
        metrics: dict[str, Any],
    ) -> str:
        symbol = metrics.get("symbol", self.base_symbol)
        currency = metrics.get("currency") or ""
        master = snapshot.get("setup_focus", {}).get("master_timeframe") or "-"
        vr = snapshot.get("setup_focus", {}).get("vr_timeframe") or "-"
        return "\n".join(
            [
                "Sultan Production Daily Report",
                f"Symbol: {symbol}",
                f"Mode: {metrics.get('execution_mode')} | Focus: {metrics.get('focus_status')}",
                f"Master: {master} | VR: {vr}",
                f"Balance: {metrics.get('balance', 0.0):,.2f} {currency}".rstrip(),
                f"Equity: {metrics.get('equity', 0.0):,.2f} {currency}".rstrip(),
                f"Floating PnL: {metrics.get('floating_pnl', 0.0):+,.2f} {currency}".rstrip(),
                f"Day Balance Delta: {metrics.get('day_balance_delta', 0.0):+,.2f} {currency}".rstrip(),
                f"Day Equity Delta: {metrics.get('day_equity_delta', 0.0):+,.2f} {currency}".rstrip(),
                f"Day Loss: {metrics.get('day_loss_percent', 0.0):.2f}% | Drawdown: {metrics.get('equity_drawdown_percent', 0.0):.2f}%",
                f"Exec Today: {metrics.get('executions_today_success', 0)} ok / {metrics.get('executions_today_failed', 0)} fail",
                f"Open Structure: {metrics.get('open_structure_count', 0)} | Blocks Recent: {metrics.get('blocked_recent', 0)}",
                f"Service Errors: {metrics.get('service_error_count', 0)} | Disconnect: {metrics.get('disconnect_minutes', 0.0):.1f}m",
                f"Kill Switch: {'ACTIVE' if state.kill_switch_active else 'ARMED'}",
                f"Report Day: {state.current_day}",
            ]
        )

    def _build_kill_switch_message(
        self,
        *,
        snapshot: dict[str, Any],
        state: ProductionGuardState,
        metrics: dict[str, Any],
        reason: str,
    ) -> str:
        symbol = metrics.get("symbol", self.base_symbol)
        return "\n".join(
            [
                "Sultan Production Kill Switch",
                f"Symbol: {symbol}",
                f"Reason: {reason}",
                f"Mode: {metrics.get('execution_mode')}",
                f"Balance: {metrics.get('balance', 0.0):,.2f}",
                f"Equity: {metrics.get('equity', 0.0):,.2f}",
                f"Day Loss: {metrics.get('day_loss_percent', 0.0):.2f}%",
                f"Drawdown: {metrics.get('equity_drawdown_percent', 0.0):.2f}%",
                f"Disconnect: {metrics.get('disconnect_minutes', 0.0):.1f}m",
                f"Service Errors: {metrics.get('service_error_count', 0)}",
                f"Paused: {snapshot.get('operator', {}).get('paused', False)}",
                f"Trigger Count: {state.kill_switch_trigger_count}",
            ]
        )
