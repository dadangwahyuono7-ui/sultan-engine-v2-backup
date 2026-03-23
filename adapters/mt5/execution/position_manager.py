from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - optional runtime dependency
    mt5 = None

from sultan_engine_v2.core.doctrine import ControlState
from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.core.timeframe_relations import CF_HIGH_RISK_BY_PARENT, CF_LOW_RISK_BY_PARENT
from sultan_engine_v2.runtime.env_loader import load_runtime_env
from sultan_engine_v2.runtime.file_utils import atomic_write_json


STRUCTURE_COMMENT_PATTERN = re.compile(
    r"(?P<parent>[A-Z0-9]+)\s+recovered\s+by\s+CF\s+on\s+(?P<cf>[A-Z0-9]+)",
    re.IGNORECASE,
)

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
    "MN1": 2592000,
}


@dataclass(slots=True)
class PositionLifecycleConfig:
    enabled: bool = True
    manage_only_magic: int | None = 26031926
    structure_exit_enabled: bool = True
    structure_exit_grace_bars: int = 1
    break_even_rr: float = 1.0
    break_even_offset_points: float = 10.0
    break_even_use_pip_trigger: bool = False
    break_even_trigger_pips: float = 10.0
    pip_size: float = 0.1
    partial_take_rr: float = 1.5
    partial_close_fraction: float = 0.5
    trail_start_rr: float = 2.0
    trail_buffer_rr: float = 1.0


@dataclass(slots=True)
class PositionManagementAction:
    ticket: int
    symbol: str
    action: str
    success: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PositionStructureStatus:
    ticket: int
    symbol: str
    direction: str
    entry_kind: str
    parent_timeframe: str
    cf_timeframe: str
    low_risk_timeframe: str | None = None
    high_risk_timeframe: str | None = None
    status: str = "UNTRACKED"
    summary: str = ""
    grace_remaining_seconds: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


def load_position_lifecycle_config_from_env() -> PositionLifecycleConfig:
    load_runtime_env(__file__)
    return PositionLifecycleConfig(
        enabled=os.getenv("SULTAN_POSITION_LIFECYCLE_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"},
        manage_only_magic=int(os.getenv("SULTAN_POSITION_MANAGE_ONLY_MAGIC", "26031926")),
        structure_exit_enabled=os.getenv("SULTAN_POSITION_STRUCTURE_EXIT_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"},
        structure_exit_grace_bars=max(int(os.getenv("SULTAN_POSITION_STRUCTURE_EXIT_GRACE_BARS", "1")), 0),
        break_even_rr=float(os.getenv("SULTAN_POSITION_BREAK_EVEN_RR", "1.0")),
        break_even_offset_points=float(os.getenv("SULTAN_POSITION_BREAK_EVEN_OFFSET_POINTS", "10")),
        break_even_use_pip_trigger=os.getenv("SULTAN_POSITION_BREAK_EVEN_USE_PIP_TRIGGER", "0").strip().lower() not in {"0", "false", "off", "no"},
        break_even_trigger_pips=float(os.getenv("SULTAN_POSITION_BREAK_EVEN_TRIGGER_PIPS", "10")),
        pip_size=float(os.getenv("SULTAN_POSITION_PIP_SIZE", "0.1")),
        partial_take_rr=float(os.getenv("SULTAN_POSITION_PARTIAL_TAKE_RR", "1.5")),
        partial_close_fraction=float(os.getenv("SULTAN_POSITION_PARTIAL_CLOSE_FRACTION", "0.5")),
        trail_start_rr=float(os.getenv("SULTAN_POSITION_TRAIL_START_RR", "2.0")),
        trail_buffer_rr=float(os.getenv("SULTAN_POSITION_TRAIL_BUFFER_RR", "1.0")),
    )


class JsonPositionLifecycleState:
    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            self.state = {}
            return
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.state = {}

    def get_ticket_state(self, ticket: int) -> dict[str, Any]:
        return dict(self.state.get(str(ticket), {}))

    def update_ticket_state(self, ticket: int, **updates: Any) -> None:
        ticket_key = str(ticket)
        current = dict(self.state.get(ticket_key, {}))
        current.update(updates)
        self.state[ticket_key] = current
        atomic_write_json(self.state_path, self.state, default=str)


class Mt5PositionLifecycleManager:
    def __init__(
        self,
        config: PositionLifecycleConfig | None = None,
        state_path: str | Path | None = None,
        positions_provider: Callable[[str], list[Any]] | None = None,
        tick_provider: Callable[[str], Any] | None = None,
        symbol_info_provider: Callable[[str], Any] | None = None,
        modify_position: Callable[[Any, float], tuple[bool, str]] | None = None,
        partial_close: Callable[[Any, float], tuple[bool, str]] | None = None,
        close_position: Callable[[Any], tuple[bool, str]] | None = None,
    ) -> None:
        self.config = config or load_position_lifecycle_config_from_env()
        self.state = JsonPositionLifecycleState(state_path or Path(__file__).resolve().parents[3] / "runtime" / "logs" / "position_lifecycle_state.json")
        self.positions_provider = positions_provider or self._default_positions_provider
        self.tick_provider = tick_provider or self._default_tick_provider
        self.symbol_info_provider = symbol_info_provider or self._default_symbol_info_provider
        self.modify_position = modify_position or self._default_modify_position
        self.partial_close = partial_close or self._default_partial_close
        self.close_position = close_position or self._default_close_position

    def manage_symbol(self, symbol: str, execution_mode: ExecutionMode, snapshot: dict | None = None) -> list[PositionManagementAction]:
        if execution_mode != ExecutionMode.LIVE or not self.config.enabled:
            return []

        actions: list[PositionManagementAction] = []
        for position in self.positions_provider(symbol):
            if not self._is_managed_position(position):
                continue
            actions.extend(self._manage_position(position, snapshot=snapshot))
        return actions

    def assess_symbol(self, symbol: str, snapshot: dict | None = None) -> list[PositionStructureStatus]:
        statuses: list[PositionStructureStatus] = []
        for position in self.positions_provider(symbol):
            if not self._is_managed_position(position):
                continue
            statuses.append(self._assess_position_structure(position, snapshot))
        return statuses

    def _manage_position(self, position: Any, snapshot: dict | None = None) -> list[PositionManagementAction]:
        actions: list[PositionManagementAction] = []
        symbol = str(getattr(position, "symbol", ""))
        ticket = int(getattr(position, "ticket", 0) or 0)
        position_type = int(getattr(position, "type", 0) or 0)
        is_buy = position_type == 0

        if self.config.structure_exit_enabled:
            exit_details = self._evaluate_structure_exit(position, snapshot)
            if exit_details is not None:
                success, message = self.close_position(position)
                actions.append(
                    PositionManagementAction(
                        ticket=ticket,
                        symbol=symbol,
                        action="STRUCTURE_EXIT",
                        success=success,
                        message=message,
                        details=exit_details,
                    )
                )
                if success:
                    self.state.update_ticket_state(ticket, last_structure_exit=exit_details, closed_by_structure=True)
                return actions

        tick = self.tick_provider(symbol)
        symbol_info = self.symbol_info_provider(symbol)
        current_price = float(getattr(tick, "bid", 0.0) if is_buy else getattr(tick, "ask", 0.0))
        entry_price = float(getattr(position, "price_open", 0.0) or 0.0)
        stop_loss = float(getattr(position, "sl", 0.0) or 0.0)
        take_profit = float(getattr(position, "tp", 0.0) or 0.0)
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        point = float(getattr(symbol_info, "point", 0.0) or 0.0) or self._infer_point(entry_price)
        initial_risk = abs(entry_price - stop_loss)
        if current_price <= 0 or entry_price <= 0 or stop_loss <= 0 or initial_risk <= 0:
            return actions

        rr_now = (current_price - entry_price) / initial_risk if is_buy else (entry_price - current_price) / initial_risk
        favorable_move = (current_price - entry_price) if is_buy else (entry_price - current_price)
        pip_trigger_distance = max(float(self.config.break_even_trigger_pips), 0.0) * max(float(self.config.pip_size), 0.00001)
        pip_trigger_hit = bool(self.config.break_even_use_pip_trigger and favorable_move >= pip_trigger_distance)
        rr_trigger_hit = rr_now >= self.config.break_even_rr
        ticket_state = self.state.get_ticket_state(ticket)

        if (pip_trigger_hit or rr_trigger_hit) and not ticket_state.get("break_even_applied"):
            new_stop = entry_price + (self.config.break_even_offset_points * point if is_buy else -self.config.break_even_offset_points * point)
            if self._sl_improves(is_buy, stop_loss, new_stop):
                success, message = self.modify_position(position, new_stop)
                trigger_source = "PIP_TRIGGER" if pip_trigger_hit else "RR_FALLBACK"
                actions.append(
                    PositionManagementAction(
                        ticket=ticket,
                        symbol=symbol,
                        action="BREAK_EVEN",
                        success=success,
                        message=message,
                        details={
                            "rr_now": round(rr_now, 4),
                            "favorable_move": round(favorable_move, 8),
                            "pip_trigger_distance": round(pip_trigger_distance, 8),
                            "trigger_source": trigger_source,
                            "new_stop_loss": round(new_stop, 8),
                        },
                    )
                )
                if success:
                    self.state.update_ticket_state(
                        ticket,
                        break_even_applied=True,
                        break_even_trigger_source=trigger_source,
                        last_stop_loss=new_stop,
                    )
                    stop_loss = new_stop

        if rr_now >= self.config.partial_take_rr and not ticket_state.get("partial_taken"):
            close_volume = self._round_volume(volume * self.config.partial_close_fraction, symbol_info)
            if 0 < close_volume < volume:
                success, message = self.partial_close(position, close_volume)
                actions.append(
                    PositionManagementAction(
                        ticket=ticket,
                        symbol=symbol,
                        action="PARTIAL_CLOSE",
                        success=success,
                        message=message,
                        details={"rr_now": round(rr_now, 4), "closed_volume": close_volume},
                    )
                )
                if success:
                    self.state.update_ticket_state(ticket, partial_taken=True)

        if rr_now >= self.config.trail_start_rr:
            trail_distance = initial_risk * self.config.trail_buffer_rr
            new_stop = current_price - trail_distance if is_buy else current_price + trail_distance
            if self._sl_improves(is_buy, stop_loss, new_stop):
                success, message = self.modify_position(position, new_stop)
                actions.append(
                    PositionManagementAction(
                        ticket=ticket,
                        symbol=symbol,
                        action="TRAIL_SL",
                        success=success,
                        message=message,
                        details={"rr_now": round(rr_now, 4), "new_stop_loss": round(new_stop, 8)},
                    )
                )
                if success:
                    self.state.update_ticket_state(ticket, last_stop_loss=new_stop)

        return actions

    def _assess_position_structure(self, position: Any, snapshot: dict | None) -> PositionStructureStatus:
        ticket = int(getattr(position, "ticket", 0) or 0)
        symbol = str(getattr(position, "symbol", "") or "")
        position_type = int(getattr(position, "type", 0) or 0)
        direction = "BUY" if position_type == 0 else "SELL"

        reference = self._parse_structure_reference(position)
        if reference is None:
            return PositionStructureStatus(
                ticket=ticket,
                symbol=symbol,
                direction=direction,
                entry_kind="UNTRACKED",
                parent_timeframe="-",
                cf_timeframe="-",
                status="UNTRACKED",
                summary="Posisi tidak punya tag struktur Sultan.",
            )

        status = PositionStructureStatus(
            ticket=ticket,
            symbol=symbol,
            direction=direction,
            entry_kind=reference["entry_kind"],
            parent_timeframe=reference["parent_timeframe"],
            cf_timeframe=reference["cf_timeframe"],
            low_risk_timeframe=reference["low_risk_timeframe"] or None,
            high_risk_timeframe=reference["high_risk_timeframe"] or None,
            details={},
        )

        if not snapshot:
            status.status = "NO_SNAPSHOT"
            status.summary = "Menunggu snapshot runtime untuk baca struktur posisi."
            return status

        timeframes = snapshot.get("timeframes", {})
        sequences = snapshot.get("sequences", {})
        parent = reference["parent_timeframe"]
        cf_timeframe = reference["cf_timeframe"]
        low_risk_timeframe = reference["low_risk_timeframe"]

        parent_node = timeframes.get(parent) or {}
        parent_sequence = sequences.get(parent) or {}
        parent_cmp = str(parent_node.get("cmp") or "")
        control_state = str(parent_sequence.get("control_state") or "")
        active_controller = str(parent_sequence.get("active_controller") or "")
        low_risk_cmp = str((timeframes.get(low_risk_timeframe) or {}).get("cmp") or "") if low_risk_timeframe else ""
        status.details.update(
            {
                "parent_cmp": parent_cmp or "-",
                "control_state": control_state or "-",
                "active_controller": active_controller or "-",
                "low_risk_cmp": low_risk_cmp or "-",
            }
        )

        if parent_cmp and parent_cmp != direction:
            status.status = "EXIT_NOW"
            status.summary = f"{parent} CMP {parent_cmp} sudah melawan {direction}."
            status.details["reason"] = "PARENT_CMP_REVERSED"
            return status

        if reference["entry_kind"] == "LOW_RISK_CF":
            if control_state == ControlState.UNDER_ATTACK.value and active_controller == cf_timeframe:
                status.status = "EXIT_NOW"
                status.summary = f"{cf_timeframe} menyerang balik {parent}, CF gagal hold."
                status.details["reason"] = "LOW_RISK_CF_FAILED"
                return status

            status.status = "HEALTHY"
            status.summary = f"{parent} masih searah {direction}, CF {cf_timeframe} masih valid."
            status.details["reason"] = "LOW_RISK_CF_HEALTHY"
            return status

        if low_risk_timeframe and control_state == ControlState.UNDER_ATTACK.value and active_controller == low_risk_timeframe:
            status.status = "EXIT_NOW"
            status.summary = f"{low_risk_timeframe} masih VR kuat terhadap {parent}."
            status.details["reason"] = "LOW_RISK_VR_STILL_ACTIVE"
            return status

        if low_risk_timeframe and low_risk_cmp and low_risk_cmp != direction:
            grace_remaining = self._structure_grace_remaining_seconds(position, snapshot, cf_timeframe)
            status.grace_remaining_seconds = grace_remaining
            if self._structure_grace_expired(position, snapshot, cf_timeframe):
                status.status = "EXIT_NOW"
                status.summary = f"{low_risk_timeframe} belum align {direction} setelah grace habis."
                status.details["reason"] = "LOW_RISK_CONFIRMATION_FAILED"
                return status

            status.status = "GRACE"
            status.summary = f"Menunggu {low_risk_timeframe} align {direction} sebelum grace habis."
            status.details["reason"] = "WAIT_LOW_RISK_CONFIRMATION"
            return status

        status.status = "HEALTHY"
        if low_risk_timeframe:
            status.summary = f"{low_risk_timeframe} sudah align {direction}, entry high-risk sehat."
            status.details["reason"] = "HIGH_RISK_CF_CONFIRMED"
        else:
            status.summary = f"Struktur {parent}/{cf_timeframe} masih sehat."
            status.details["reason"] = "STRUCTURE_HEALTHY"
        return status

    def _evaluate_structure_exit(self, position: Any, snapshot: dict | None) -> dict[str, Any] | None:
        if not snapshot:
            return None

        reference = self._parse_structure_reference(position)
        if reference is None:
            return None

        timeframes = snapshot.get("timeframes", {})
        sequences = snapshot.get("sequences", {})
        parent = reference["parent_timeframe"]
        cf_timeframe = reference["cf_timeframe"]
        low_risk_timeframe = reference["low_risk_timeframe"]
        direction = reference["direction"]

        parent_node = timeframes.get(parent) or {}
        parent_sequence = sequences.get(parent) or {}
        parent_cmp = str(parent_node.get("cmp") or "")
        control_state = str(parent_sequence.get("control_state") or "")
        active_controller = str(parent_sequence.get("active_controller") or "")
        low_risk_cmp = str((timeframes.get(low_risk_timeframe) or {}).get("cmp") or "") if low_risk_timeframe else ""

        if parent_cmp and parent_cmp != direction:
            return {
                "reason": "PARENT_CMP_REVERSED",
                "parent_timeframe": parent,
                "cf_timeframe": cf_timeframe,
                "direction": direction,
                "current_parent_cmp": parent_cmp,
            }

        if reference["entry_kind"] == "LOW_RISK_CF":
            if control_state == ControlState.UNDER_ATTACK.value and active_controller == cf_timeframe:
                return {
                    "reason": "LOW_RISK_CF_FAILED",
                    "parent_timeframe": parent,
                    "cf_timeframe": cf_timeframe,
                    "direction": direction,
                    "control_state": control_state,
                    "active_controller": active_controller,
                }
            return None

        if low_risk_timeframe and control_state == ControlState.UNDER_ATTACK.value and active_controller == low_risk_timeframe:
            return {
                "reason": "LOW_RISK_VR_STILL_ACTIVE",
                "parent_timeframe": parent,
                "cf_timeframe": cf_timeframe,
                "direction": direction,
                "low_risk_timeframe": low_risk_timeframe,
                "current_low_risk_cmp": low_risk_cmp or "-",
            }

        if low_risk_timeframe and low_risk_cmp and low_risk_cmp != direction and self._structure_grace_expired(position, snapshot, cf_timeframe):
            return {
                "reason": "LOW_RISK_CONFIRMATION_FAILED",
                "parent_timeframe": parent,
                "cf_timeframe": cf_timeframe,
                "direction": direction,
                "low_risk_timeframe": low_risk_timeframe,
                "current_low_risk_cmp": low_risk_cmp,
                "grace_bars": self.config.structure_exit_grace_bars,
            }

        return None

    def _parse_structure_reference(self, position: Any) -> dict[str, str] | None:
        comment = str(getattr(position, "comment", "") or "")
        match = STRUCTURE_COMMENT_PATTERN.search(comment)
        if match is None:
            return None

        parent = match.group("parent").upper()
        cf_timeframe = match.group("cf").upper()
        low_risk_timeframe = CF_LOW_RISK_BY_PARENT.get(parent)
        high_risk_timeframe = CF_HIGH_RISK_BY_PARENT.get(parent)
        if cf_timeframe == low_risk_timeframe:
            entry_kind = "LOW_RISK_CF"
        elif cf_timeframe == high_risk_timeframe:
            entry_kind = "HIGH_RISK_CF"
        else:
            entry_kind = "CF"

        position_type = int(getattr(position, "type", 0) or 0)
        direction = "BUY" if position_type == 0 else "SELL"
        return {
            "parent_timeframe": parent,
            "cf_timeframe": cf_timeframe,
            "low_risk_timeframe": low_risk_timeframe or "",
            "high_risk_timeframe": high_risk_timeframe or "",
            "entry_kind": entry_kind,
            "direction": direction,
        }

    def _structure_grace_expired(self, position: Any, snapshot: dict, reference_timeframe: str) -> bool:
        grace_seconds = TIMEFRAME_SECONDS.get(reference_timeframe, 0) * self.config.structure_exit_grace_bars
        if grace_seconds <= 0:
            return True

        entry_time = int(getattr(position, "time", 0) or 0)
        market_time = int(snapshot.get("market", {}).get("time") or 0)
        if entry_time <= 0 or market_time <= 0:
            return False
        return market_time >= entry_time + grace_seconds

    def _structure_grace_remaining_seconds(self, position: Any, snapshot: dict, reference_timeframe: str) -> int | None:
        grace_seconds = TIMEFRAME_SECONDS.get(reference_timeframe, 0) * self.config.structure_exit_grace_bars
        if grace_seconds <= 0:
            return 0

        entry_time = int(getattr(position, "time", 0) or 0)
        market_time = int(snapshot.get("market", {}).get("time") or 0)
        if entry_time <= 0 or market_time <= 0:
            return None
        return max((entry_time + grace_seconds) - market_time, 0)

    def _is_managed_position(self, position: Any) -> bool:
        if self.config.manage_only_magic is None:
            return True
        return int(getattr(position, "magic", 0) or 0) == self.config.manage_only_magic

    @staticmethod
    def _sl_improves(is_buy: bool, current_stop: float, new_stop: float) -> bool:
        if is_buy:
            return new_stop > current_stop
        return new_stop < current_stop or current_stop <= 0

    @staticmethod
    def _round_volume(volume: float, symbol_info: Any) -> float:
        volume_min = float(getattr(symbol_info, "volume_min", 0.0) or 0.0)
        volume_step = float(getattr(symbol_info, "volume_step", volume_min) or volume_min)
        if volume_min <= 0 or volume_step <= 0:
            return 0.0
        rounded_steps = int(volume / volume_step)
        rounded_volume = rounded_steps * volume_step
        if rounded_volume < volume_min:
            return 0.0
        return round(rounded_volume, Mt5PositionLifecycleManager._volume_precision(volume_step))

    @staticmethod
    def _volume_precision(step: float) -> int:
        text = f"{step:.10f}".rstrip("0")
        if "." not in text:
            return 0
        return len(text.split(".", 1)[1])

    @staticmethod
    def _infer_point(price: float) -> float:
        if price <= 0:
            return 0.01
        text = f"{price:.10f}".rstrip("0")
        if "." not in text:
            return 1.0
        return 10 ** (-len(text.split(".", 1)[1]))

    @staticmethod
    def _default_positions_provider(symbol: str) -> list[Any]:
        if mt5 is None:
            return []
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return []
        return list(positions)

    @staticmethod
    def _default_tick_provider(symbol: str) -> Any:
        if mt5 is None:
            return None
        return mt5.symbol_info_tick(symbol)

    @staticmethod
    def _default_symbol_info_provider(symbol: str) -> Any:
        if mt5 is None:
            return None
        return mt5.symbol_info(symbol)

    @staticmethod
    def _default_modify_position(position: Any, new_stop_loss: float) -> tuple[bool, str]:
        if mt5 is None:
            return False, "MetaTrader5 package is not installed"
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": int(getattr(position, "ticket", 0)),
            "symbol": str(getattr(position, "symbol", "")),
            "sl": new_stop_loss,
            "tp": float(getattr(position, "tp", 0.0) or 0.0),
        }
        result = mt5.order_send(request)
        if result is None:
            return False, "order_send returned None"
        success = bool(getattr(result, "retcode", 0) == getattr(mt5, "TRADE_RETCODE_DONE", 10009))
        return success, str(getattr(result, "comment", "Position modify processed"))

    @staticmethod
    def _default_partial_close(position: Any, close_volume: float) -> tuple[bool, str]:
        if mt5 is None:
            return False, "MetaTrader5 package is not installed"

        symbol = str(getattr(position, "symbol", ""))
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return False, "symbol_info_tick returned None"

        position_type = int(getattr(position, "type", 0) or 0)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": int(getattr(position, "ticket", 0)),
            "symbol": symbol,
            "volume": close_volume,
            "type": mt5.ORDER_TYPE_SELL if position_type == 0 else mt5.ORDER_TYPE_BUY,
            "price": float(getattr(tick, "bid", 0.0) if position_type == 0 else getattr(tick, "ask", 0.0)),
            "deviation": 30,
            "comment": "Lifecycle partial close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": getattr(mt5, "ORDER_FILLING_IOC", 1),
        }
        result = mt5.order_send(request)
        if result is None:
            return False, "order_send returned None"
        success = bool(getattr(result, "retcode", 0) == getattr(mt5, "TRADE_RETCODE_DONE", 10009))
        return success, str(getattr(result, "comment", "Partial close processed"))

    @staticmethod
    def _default_close_position(position: Any) -> tuple[bool, str]:
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        if volume <= 0:
            return False, "Position volume is zero"
        return Mt5PositionLifecycleManager._default_partial_close(position, volume)
