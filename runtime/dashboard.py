from __future__ import annotations

import os
import time
from datetime import datetime

from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from sultan_engine_v2.adapters.mt5.symbols.resolver import ResolvedSymbol, resolve_many
from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.core.doctrine import ControlState, SequenceRole
from sultan_engine_v2.runtime.mt5_live_runtime import Mt5LiveRuntime
from sultan_engine_v2.runtime.env_loader import load_runtime_env


DEFAULT_CFD_WATCHLIST = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]


def load_watchlist() -> list[str]:
    load_runtime_env(__file__)
    raw_value = os.getenv("SULTAN_MT5_WATCHLIST")
    if not raw_value:
        return DEFAULT_CFD_WATCHLIST
    return [item.strip().upper() for item in raw_value.split(",") if item.strip()]


def format_breakout_time(timestamp: int | None) -> str:
    if not timestamp:
        return "-"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "-"


def format_focus_direction(value: str | None) -> str:
    if not value:
        return "-"
    return value


def format_grace(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds <= 0:
        return "0s"
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes <= 0:
        return f"{remaining_seconds}s"
    return f"{minutes}m {remaining_seconds:02d}s"


def build_range_bar(price: float, low: float, high: float, width: int = 14) -> str:
    if width < 3 or high <= low or price <= 0:
        return "[no-range]"

    ratio = (price - low) / (high - low)
    ratio = max(0.0, min(1.0, ratio))
    marker_index = min(width - 1, max(0, int(round(ratio * (width - 1)))))
    chars = ["-"] * width
    chars[marker_index] = "|"
    return "[" + "".join(chars) + "]"


def build_tape(recent_ticks: list[dict], width: int = 12) -> str:
    if len(recent_ticks) < 2:
        return "waiting"

    tape: list[str] = []
    for previous, current in zip(recent_ticks, recent_ticks[1:]):
        previous_mid = float(previous.get("mid") or 0.0)
        current_mid = float(current.get("mid") or 0.0)
        if current_mid > previous_mid:
            tape.append("+")
        elif current_mid < previous_mid:
            tape.append("-")
        else:
            tape.append("=")
    return " ".join(tape[-width:]) or "waiting"


def build_delta_pressure_summary(recent_ticks: list[dict]) -> dict[str, float | int | str]:
    if len(recent_ticks) < 2:
        return {
            "delta_plus": 0.0,
            "delta_minus": 0.0,
            "net_delta": 0.0,
            "buy_moves": 0,
            "sell_moves": 0,
            "flat_moves": 0,
            "pressure": "IDLE",
            "detail": "tick feed warming up",
        }

    delta_plus = 0.0
    delta_minus = 0.0
    buy_moves = 0
    sell_moves = 0
    flat_moves = 0

    for previous, current in zip(recent_ticks, recent_ticks[1:]):
        previous_mid = float(previous.get("mid") or 0.0)
        current_mid = float(current.get("mid") or 0.0)
        change = current_mid - previous_mid
        if change > 0:
            delta_plus += change
            buy_moves += 1
        elif change < 0:
            delta_minus += abs(change)
            sell_moves += 1
        else:
            flat_moves += 1

    net_delta = delta_plus - delta_minus
    if net_delta > 0 and buy_moves >= sell_moves:
        pressure = "BUY_PRESSURE"
    elif net_delta < 0 and sell_moves >= buy_moves:
        pressure = "SELL_PRESSURE"
    elif net_delta > 0:
        pressure = "BUY_BIAS"
    elif net_delta < 0:
        pressure = "SELL_BIAS"
    else:
        pressure = "BALANCED"

    detail = f"net {net_delta:+.2f} | buy {buy_moves} sell {sell_moves} flat {flat_moves}"
    return {
        "delta_plus": delta_plus,
        "delta_minus": delta_minus,
        "net_delta": net_delta,
        "buy_moves": buy_moves,
        "sell_moves": sell_moves,
        "flat_moves": flat_moves,
        "pressure": pressure,
        "detail": detail,
    }


def build_pulse_summary(recent_ticks: list[dict]) -> tuple[str, str]:
    if len(recent_ticks) < 2:
        return "IDLE", "tick feed warming up"

    first_mid = float(recent_ticks[0].get("mid") or 0.0)
    last_mid = float(recent_ticks[-1].get("mid") or 0.0)
    delta = last_mid - first_mid
    pressure = build_delta_pressure_summary(recent_ticks)
    up_moves = int(pressure["buy_moves"])
    down_moves = int(pressure["sell_moves"])

    if delta > 0:
        state = "UP"
    elif delta < 0:
        state = "DOWN"
    else:
        state = "FLAT"

    detail = f"dMid {delta:+.2f} | up {up_moves} down {down_moves}"
    return state, detail


class SultanDashboardApp:
    def __init__(
        self,
        base_symbol: str = "XAUUSD",
        execution_mode: ExecutionMode = ExecutionMode.PAPER,
        auto_execute: bool = True,
    ) -> None:
        self.console = Console()
        self.runtime = Mt5LiveRuntime(
            base_symbol=base_symbol,
            execution_mode=execution_mode,
            auto_execute=auto_execute,
        )
        self.watchlist = load_watchlist()
        self.runtime.connect()
        self.runtime.bootstrap()
        self.symbols: list[ResolvedSymbol] = resolve_many(self.watchlist) if self.runtime.session.connected else []

    def refresh(self) -> None:
        self.runtime.poll()
        if self.runtime.session.connected:
            self.symbols = resolve_many(self.watchlist)

    def _build_header(self) -> Panel:
        market = self.runtime.tick.bid or self.runtime.tick.last or 0.0
        actual_symbol = self.runtime.actual_symbol or self.runtime.base_symbol
        text = f"SULTAN ENGINE V2 | {actual_symbol} | MT5 ACTIVE SESSION | BID {market:,.5f}"
        return Panel(text, border_style="cyan")

    def _build_mt5_panel(self) -> Panel:
        snapshot = self.runtime.snapshot_dict()
        mt5_snapshot = self.runtime.session.snapshot
        guard_config = snapshot.get("execution", {}).get("guard_config", {})
        recent_decisions = snapshot.get("execution", {}).get("recent_decisions", [])
        lifecycle_actions = snapshot.get("lifecycle", {}).get("recent_actions", [])
        operator = snapshot.get("operator", {})
        blocked_decisions = [decision for decision in recent_decisions if not decision.get("allowed", True)]
        last_block_reason = blocked_decisions[-1]["reason"] if blocked_decisions else "-"
        last_lifecycle = lifecycle_actions[-1]["action"] if lifecycle_actions else "-"
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Field", style="bold cyan", width=16)
        table.add_column("Value", style="white")

        status = "CONNECTED" if mt5_snapshot.connected else "DISCONNECTED"
        table.add_row("Status", status)
        table.add_row("Login", str(mt5_snapshot.login or "-"))
        table.add_row("Server", mt5_snapshot.server or "-")
        table.add_row("Broker", mt5_snapshot.broker_name or "-")
        table.add_row("Currency", mt5_snapshot.currency or "-")
        table.add_row("Balance", f"{mt5_snapshot.balance:,.2f}" if mt5_snapshot.connected else "-")
        table.add_row("Equity", f"{mt5_snapshot.equity:,.2f}" if mt5_snapshot.connected else "-")
        table.add_row("Leverage", str(mt5_snapshot.leverage or "-"))
        table.add_row("Symbol", self.runtime.actual_symbol or self.runtime.base_symbol)
        table.add_row("Exec Mode", str(self.runtime.execution_mode))
        table.add_row("Auto Exec", "ON" if self.runtime.auto_execute else "OFF")
        table.add_row("New Entry", "ON" if operator.get("allow_new_entries", True) else "OFF")
        table.add_row("Paused", "YES" if operator.get("paused") else "NO")
        table.add_row("Exec Count", str(len(self.runtime.execution_results)))
        table.add_row("Max Spread", str(guard_config.get("max_spread_points", "-")))
        table.add_row("Max Pos/Sym", str(guard_config.get("max_open_positions_per_symbol", "-")))
        table.add_row("Cooldown", f"{guard_config.get('cooldown_minutes', '-')}m")
        table.add_row("Last Block", last_block_reason)
        table.add_row("Max Chase", str(guard_config.get("max_chase_points", "-")))
        table.add_row("Lifecycle", "ON" if snapshot.get("lifecycle", {}).get("config", {}).get("enabled") else "OFF")
        table.add_row("Last Life", last_lifecycle)
        table.add_row("Telegram", "ON" if snapshot.get("operator", {}).get("telegram_enabled") else "OFF")
        table.add_row("Last Cmd", str(operator.get("last_command") or "-"))
        table.add_row("Interpreter", str(operator.get("last_interpreter") or "-"))
        table.add_row("Path", mt5_snapshot.terminal_path or mt5_snapshot.path_source or "-")

        if mt5_snapshot.error:
            table.add_row("Error", mt5_snapshot.error)

        return Panel(table, title="MT5 Session", border_style="green" if mt5_snapshot.connected else "red")

    def _build_symbols_panel(self) -> Panel:
        table = Table(expand=True)
        table.add_column("Requested", style="bold white")
        table.add_column("Resolved", style="cyan")
        table.add_column("Source", style="yellow")
        table.add_column("Score", justify="right")

        if not self.symbols:
            table.add_row("-", "-", "waiting", "0")
        else:
            for symbol in self.symbols:
                table.add_row(
                    symbol.requested,
                    symbol.actual or "missing",
                    symbol.source,
                    str(symbol.score),
                )

        return Panel(table, title="Symbol Resolution", border_style="blue")

    def _build_footprint_panel(self) -> Panel:
        snapshot = self.runtime.snapshot_dict()
        market = snapshot.get("market", {})
        footprint = snapshot.get("footprint", {})
        recent_ticks = footprint.get("recent_ticks", [])[-12:]
        pulse_state, pulse_detail = build_pulse_summary(recent_ticks)
        spread = float(market.get("spread") or 0.0)
        current_price = float(market.get("bid") or market.get("mid") or market.get("last") or 0.0)

        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Field", style="bold cyan", width=10)
        table.add_column("Value", style="white")
        pressure = build_delta_pressure_summary(recent_ticks)
        table.add_row("Pulse", f"{pulse_state} | {pulse_detail}")
        table.add_row("Pressure", f"{pressure['pressure']} | {pressure['detail']}")
        table.add_row("Delta+", f"{float(pressure['delta_plus']):+.2f}")
        table.add_row("Delta-", f"-{float(pressure['delta_minus']):.2f}")
        table.add_row("Net", f"{float(pressure['net_delta']):+.2f}")
        table.add_row("Spread", f"{spread:.2f}")
        table.add_row("Tape", build_tape(recent_ticks))

        timeframe_rows: list[str] = []
        for timeframe in ("H1", "M30", "M15", "M5"):
            node = snapshot.get("timeframes", {}).get(timeframe) or {}
            support = float(node.get("support") or 0.0)
            resistance = float(node.get("resistance") or 0.0)
            cmp_value = str(node.get("cmp") or "-")
            timeframe_rows.append(
                f"{timeframe} {cmp_value:<4} {build_range_bar(current_price, support, resistance)}"
            )
        table.add_row("Range", "\n".join(timeframe_rows))

        return Panel(table, title="Market Footprint", border_style="bright_blue")

    def _build_brand_panel(self) -> Panel:
        snapshot = self.runtime.snapshot_dict()
        mt5_snapshot = snapshot.get("mt5", {})
        market = snapshot.get("market", {})
        focus = snapshot.get("setup_focus", {})

        brand_title = Text("DEV DADANG WAHYUONO", style="bold bright_cyan", justify="center")
        brand_line = Text(
            f"Build Signature | {snapshot.get('symbol', {}).get('actual') or self.runtime.base_symbol} | {focus.get('status') or 'NO_ACTIVE_VR'}",
            style="yellow",
            justify="center",
        )
        brand_detail = Text(
            f"Login {mt5_snapshot.get('login') or '-'} | Server {mt5_snapshot.get('server') or '-'} | Bid {float(market.get('bid') or 0.0):,.2f}",
            style="green",
            justify="center",
        )
        brand_stamp = Text(
            f"Protected Build Mark | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            style="bright_black",
            justify="center",
        )

        content = Align.center(Group(brand_title, brand_line, brand_detail, brand_stamp), vertical="middle")
        return Panel(content, title="Build Signature", border_style="cyan")

    def _build_focus_panel(self) -> Panel:
        snapshot = self.runtime.snapshot_dict()
        focus = snapshot.get("setup_focus", {})
        status = str(focus.get("status") or "NO_ACTIVE_VR")
        border_style = "yellow" if status == "WAIT_CF" else "cyan" if status == "CF_CONFIRMED" else "green"

        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Field", style="bold cyan", width=12)
        table.add_column("Value", style="white")
        table.add_row("Status", status)
        table.add_row(
            "Master",
            f"{focus.get('master_timeframe') or '-'} {format_focus_direction(focus.get('master_direction'))}",
        )

        if status == "WAIT_CF":
            table.add_row(
                "Active VR",
                f"{focus.get('vr_timeframe') or '-'} {format_focus_direction(focus.get('vr_direction'))}",
            )
            table.add_row(
                "Wait CF",
                f"low {focus.get('cf_low_risk_timeframe') or '-'} | high {focus.get('cf_high_risk_timeframe') or '-'}",
            )
            table.add_row("VR@", format_breakout_time(focus.get("vr_started_at")))
        elif status == "CF_CONFIRMED":
            table.add_row(
                "Confirmed CF",
                f"{focus.get('confirmed_cf_timeframe') or '-'} {format_focus_direction(focus.get('confirmed_cf_direction'))}",
            )
            table.add_row("CF@", format_breakout_time(focus.get("confirmed_cf_at")))

        table.add_row("Master@", format_breakout_time(focus.get("master_breakout_timestamp")))
        table.add_row("Target", str(focus.get("target_timeframe") or "-"))
        higher_context = focus.get("higher_context") or []
        table.add_row("Context", " | ".join(higher_context) if higher_context else "-")
        table.add_row("Summary", str(focus.get("summary") or "-"))

        return Panel(table, title="Setup Focus", border_style=border_style)

    def _build_state_panel(self) -> Panel:
        table = Table(expand=True, box=None, pad_edge=False)
        table.add_column("TF", style="bold white", width=4)
        table.add_column("CMP", justify="center")
        table.add_column("Role", justify="center")
        table.add_column("Dom", justify="center")
        table.add_column("Flip@", justify="center", width=5)
        table.add_column("Lvl", justify="right")

        snapshot = self.runtime.engine.snapshot()
        display_tfs = [tf for tf in ("D1", "H4", "H1", "M30", "M15", "M5") if tf in snapshot.nodes]
        for timeframe in display_tfs:
            node = snapshot.nodes[timeframe]
            cmp_value = node.cmp_direction.value if node.cmp_direction else "-"
            cmp_style = "green" if cmp_value == "BUY" else "red" if cmp_value == "SELL" else "white"

            role_value = node.role_to_parent.value if node.role_to_parent else "CMP"
            if node.role_to_parent == SequenceRole.VR:
                role_style = "yellow"
            elif node.role_to_parent == SequenceRole.CF:
                role_style = "cyan"
            else:
                role_style = "white"

            table.add_row(
                timeframe,
                f"[{cmp_style}]{cmp_value}[/]",
                f"[{role_style}]{role_value}[/]",
                node.setup_domain or "-",
                format_breakout_time(node.cmp_timestamp),
                f"{node.breakout_level:,.2f}" if node.breakout_level else "-",
            )

        return Panel(table, title="CMP / VR / CF Matrix", border_style="magenta")

    def _build_sequence_panel(self) -> Panel:
        table = Table(expand=True, box=None, pad_edge=False)
        table.add_column("Parent", style="bold white", width=5)
        table.add_column("Dir", justify="center")
        table.add_column("State", justify="center")
        table.add_column("Ctrl", justify="center")
        table.add_column("P@", justify="center", width=5)
        table.add_column("VR@", justify="center", width=5)
        table.add_column("CF@", justify="center", width=5)
        table.add_column("#", justify="right", width=2)

        snapshot = self.runtime.engine.snapshot()
        for parent in ("D1", "H4", "H1", "M30", "M15"):
            sequence = snapshot.sequences.get(parent)
            if sequence is None:
                continue

            direction = sequence.parent_direction.value if sequence.parent_direction else "-"
            direction_style = "green" if direction == "BUY" else "red" if direction == "SELL" else "white"

            if sequence.control_state == ControlState.IN_CONTROL:
                state_style = "green"
                state_label = "IN_CTRL"
            elif sequence.control_state == ControlState.UNDER_ATTACK:
                state_style = "yellow"
                state_label = "ATTACK"
            elif sequence.control_state == ControlState.RECOVERED_BY_CF:
                state_style = "cyan"
                state_label = "CF_OK"
            else:
                state_style = "red"
                state_label = sequence.control_state.value

            table.add_row(
                parent,
                f"[{direction_style}]{direction}[/]",
                f"[{state_style}]{state_label}[/]",
                sequence.active_controller or "-",
                format_breakout_time(sequence.parent_breakout_timestamp),
                format_breakout_time(sequence.vr_started_at),
                format_breakout_time(sequence.last_cf_timestamp),
                str(sequence.cf_count),
            )

        return Panel(table, title="Parent Control", border_style="green")

    def _build_logs_panel(self) -> Panel:
        lines = list(self.runtime.logs) or ["No log entries yet."]
        return Panel(Group(*lines), title="Operator Log", border_style="yellow")

    def _build_position_structure_panel(self) -> Panel:
        snapshot = self.runtime.snapshot_dict()
        statuses = snapshot.get("lifecycle", {}).get("position_structure", [])

        table = Table(expand=True)
        table.add_column("Tk", style="bold white", width=6)
        table.add_column("Dir", justify="center", width=5)
        table.add_column("Kind", justify="center", width=10)
        table.add_column("Parent", justify="center", width=6)
        table.add_column("CF", justify="center", width=5)
        table.add_column("State", justify="center", width=9)
        table.add_column("Grace", justify="center", width=9)
        table.add_column("Note", overflow="fold")

        if not statuses:
            table.add_row("-", "-", "-", "-", "-", "OPEN", "-", "Tidak ada posisi managed yang sedang terbuka.")
            return Panel(table, title="Open Position Structure", border_style="green")

        risk_detected = False
        for status in statuses:
            direction = str(status.get("direction") or "-")
            direction_style = "green" if direction == "BUY" else "red" if direction == "SELL" else "white"
            state_value = str(status.get("status") or "UNTRACKED")
            if state_value == "HEALTHY":
                state_style = "green"
            elif state_value == "GRACE":
                state_style = "yellow"
            elif state_value == "EXIT_NOW":
                state_style = "red"
                risk_detected = True
            else:
                state_style = "white"

            table.add_row(
                str(status.get("ticket") or "-"),
                f"[{direction_style}]{direction}[/]",
                str(status.get("entry_kind") or "-"),
                str(status.get("parent_timeframe") or "-"),
                str(status.get("cf_timeframe") or "-"),
                f"[{state_style}]{state_value}[/]",
                format_grace(status.get("grace_remaining_seconds")),
                str(status.get("summary") or "-"),
            )

        border_style = "red" if risk_detected else "green"
        return Panel(table, title="Open Position Structure", border_style=border_style)

    def render(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=12),
        )
        layout["body"].split_row(
            Layout(name="left", size=42),
            Layout(name="main", ratio=1),
        )
        layout["left"].split_column(
            Layout(name="mt5_panel", size=23),
            Layout(name="focus_panel", size=7),
            Layout(name="symbol_panel", ratio=1),
        )
        layout["main"].split_column(
            Layout(name="main_top", ratio=1),
            Layout(name="brand_panel", size=6),
        )
        layout["main_top"].split_row(
            Layout(name="footprint_panel", ratio=6),
            Layout(name="right", ratio=5),
        )
        layout["right"].split_column(
            Layout(name="state_panel", size=9),
            Layout(name="sequence_panel", size=8),
        )
        layout["footer"].split_row(
            Layout(name="footer_left", size=56),
            Layout(name="footer_right", ratio=1),
        )
        layout["header"].update(self._build_header())
        layout["mt5_panel"].update(self._build_mt5_panel())
        layout["focus_panel"].update(self._build_focus_panel())
        layout["symbol_panel"].update(self._build_symbols_panel())
        layout["footprint_panel"].update(self._build_footprint_panel())
        layout["state_panel"].update(self._build_state_panel())
        layout["sequence_panel"].update(self._build_sequence_panel())
        layout["brand_panel"].update(self._build_brand_panel())
        layout["footer_left"].update(self._build_position_structure_panel())
        layout["footer_right"].update(self._build_logs_panel())
        return layout

    def run(self) -> None:
        with Live(self.render(), console=self.console, refresh_per_second=2, screen=True) as live:
            try:
                while True:
                    self.refresh()
                    live.update(self.render())
                    time.sleep(1)
            finally:
                self.runtime.close()


def main(
    base_symbol: str = "XAUUSD",
    execution_mode: ExecutionMode = ExecutionMode.PAPER,
    auto_execute: bool = True,
) -> None:
    SultanDashboardApp(
        base_symbol=base_symbol,
        execution_mode=execution_mode,
        auto_execute=auto_execute,
    ).run()


if __name__ == "__main__":
    main()
