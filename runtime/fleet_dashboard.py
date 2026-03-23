from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from sultan_engine_v2.runtime.fleet_monitor import build_fleet_payload


DISPLAY_TIMEFRAMES = ("D1", "H4", "H1", "M30", "M15", "M5")
DISPLAY_PARENTS = ("D1", "H4", "H1", "M30", "M15")


def _status_style(value: str) -> str:
    raw = str(value or "").upper()
    if any(token in raw for token in ("RUN", "HEALTHY", "READY", "CONNECTED", "BUY", "IN_CONTROL", "CF_OK")):
        return "green"
    if any(token in raw for token in ("WAIT", "COLLECT", "PAPER", "PARTIAL", "ATTACK", "UNDER_ATTACK", "RECOVERED")):
        return "yellow"
    if any(token in raw for token in ("NOT_READY", "ERROR", "DISCONNECTED", "SELL")):
        return "red"
    return "white"


def format_breakout_time(timestamp: int | None) -> str:
    if not timestamp:
        return "-"
    try:
        return datetime.fromtimestamp(int(timestamp)).strftime("%H:%M")
    except (OverflowError, OSError, ValueError, TypeError):
        return "-"


def build_state_rows(snapshot: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    timeframes = snapshot.get("timeframes", {})
    for timeframe in DISPLAY_TIMEFRAMES:
        node = timeframes.get(timeframe)
        if not isinstance(node, dict):
            continue
        rows.append(
            [
                timeframe,
                str(node.get("cmp") or "-"),
                str(node.get("role") or "CMP"),
                str(node.get("domain") or "-"),
                format_breakout_time(node.get("cmp_timestamp")),
                f"{float(node.get('breakout_level') or 0.0):,.2f}" if float(node.get("breakout_level") or 0.0) > 0 else "-",
            ]
        )
    return rows


def build_sequence_rows(snapshot: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    sequences = snapshot.get("sequences", {})
    for parent in DISPLAY_PARENTS:
        sequence = sequences.get(parent)
        if not isinstance(sequence, dict):
            continue
        control_state = str(sequence.get("control_state") or "-")
        if control_state == "IN_CONTROL":
            state_label = "IN_CTRL"
        elif control_state == "UNDER_ATTACK":
            state_label = "ATTACK"
        elif control_state == "RECOVERED_BY_CF":
            state_label = "CF_OK"
        else:
            state_label = control_state
        rows.append(
            [
                parent,
                str(sequence.get("direction") or "-"),
                state_label,
                str(sequence.get("active_controller") or "-"),
                format_breakout_time(sequence.get("parent_breakout_timestamp")),
                format_breakout_time(sequence.get("vr_started_at")),
                format_breakout_time(sequence.get("last_cf_timestamp")),
                str(sequence.get("cf_count") or 0),
            ]
        )
    return rows


class SultanFleetDashboardApp:
    def __init__(self, *, mt5_symbol: str = "XAUUSD", binance_symbol: str = "BTCUSDT") -> None:
        self.console = Console()
        self.mt5_symbol = mt5_symbol
        self.binance_symbol = binance_symbol

    def _build_header(self, payload: dict) -> Panel:
        summary = payload.get("summary", {})
        text = (
            f"SULTAN FLEET DASHBOARD | MT5 {summary.get('mt5_symbol', self.mt5_symbol)} | "
            f"BINANCE {summary.get('binance_symbol', self.binance_symbol)} | "
            f"STATUS {summary.get('fleet_status', 'PARTIAL')} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return Panel(text, border_style="cyan")

    def _build_fleet_panel(self, payload: dict) -> Panel:
        summary = payload.get("summary", {})
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Field", style="bold cyan", width=14)
        table.add_column("Value")
        table.add_row("Fleet", f"[{_status_style(summary.get('fleet_status'))}]{summary.get('fleet_status', '-')}[/]")
        table.add_row("Running", str(summary.get("running_services", 0)))
        table.add_row("Connected", str(summary.get("connected_venues", 0)))
        table.add_row("MT5", str(summary.get("mt5_symbol", self.mt5_symbol)))
        table.add_row("Binance", str(summary.get("binance_symbol", self.binance_symbol)))
        table.add_row("Generated", str(payload.get("generated_at") or "-"))
        return Panel(table, title="Fleet", border_style="blue")

    def _build_venue_panel(self, title: str, payload: dict) -> Panel:
        summary = payload.get("summary", {})
        snapshot = payload.get("snapshot", {})
        focus = snapshot.get("setup_focus", {})
        account = snapshot.get("mt5") or snapshot.get("paper_account") or {}
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("Field", style="bold cyan", width=14)
        table.add_column("Value")
        table.add_row("Service", f"[{_status_style(summary.get('service'))}]{summary.get('service', '-')}[/]")
        table.add_row("Venue", str(summary.get("venue", "-")))
        table.add_row("Focus", f"[{_status_style(summary.get('focus_status'))}]{summary.get('focus_status', '-')}[/]")
        table.add_row("Master", f"{focus.get('master_timeframe') or '-'} {focus.get('master_direction') or ''}".strip())
        table.add_row("VR", f"{focus.get('vr_timeframe') or '-'} {focus.get('vr_direction') or ''}".strip())
        table.add_row("Burn-In", f"[{_status_style(summary.get('burn_in_status'))}]{summary.get('burn_in_status', '-')}[/]")
        table.add_row("Score", f"{float(summary.get('burn_in_score') or 0.0):.1f}")
        table.add_row("Bid/Price", str(summary.get("market_bid", "-")))
        table.add_row("Spread", str(summary.get("market_spread", "-")))
        table.add_row("Balance", f"{float(account.get('balance') or 0.0):,.2f}" if account else "-")
        table.add_row("Equity", f"{float(account.get('equity') or 0.0):,.2f}" if account else "-")
        table.add_row("Open", str(summary.get("open_structure", 0)))
        return Panel(table, title=title, border_style="green")

    def _build_state_panel(self, title: str, snapshot: dict[str, Any]) -> Panel:
        table = Table(expand=True, box=None, pad_edge=False)
        table.add_column("TF", style="bold white", width=4)
        table.add_column("CMP", justify="center")
        table.add_column("Role", justify="center")
        table.add_column("Dom", justify="center")
        table.add_column("Flip@", justify="center", width=5)
        table.add_column("Lvl", justify="right")

        rows = build_state_rows(snapshot)
        if not rows:
            table.add_row("-", "-", "-", "-", "-", "-")
        for timeframe, cmp_value, role_value, domain, flip_at, level in rows:
            table.add_row(
                timeframe,
                f"[{_status_style(cmp_value)}]{cmp_value}[/]",
                f"[{_status_style(role_value)}]{role_value}[/]",
                domain,
                flip_at,
                level,
            )
        return Panel(table, title=f"{title} CMP / VR / CF Matrix", border_style="magenta")

    def _build_sequence_panel(self, title: str, snapshot: dict[str, Any]) -> Panel:
        table = Table(expand=True, box=None, pad_edge=False)
        table.add_column("Parent", style="bold white", width=5)
        table.add_column("Dir", justify="center")
        table.add_column("State", justify="center")
        table.add_column("Ctrl", justify="center")
        table.add_column("P@", justify="center", width=5)
        table.add_column("VR@", justify="center", width=5)
        table.add_column("CF@", justify="center", width=5)
        table.add_column("#", justify="right", width=2)

        rows = build_sequence_rows(snapshot)
        if not rows:
            table.add_row("-", "-", "-", "-", "-", "-", "-", "0")
        for parent, direction, state_label, controller, parent_at, vr_at, cf_at, cf_count in rows:
            table.add_row(
                parent,
                f"[{_status_style(direction)}]{direction}[/]",
                f"[{_status_style(state_label)}]{state_label}[/]",
                controller,
                parent_at,
                vr_at,
                cf_at,
                cf_count,
            )
        return Panel(table, title=f"{title} Parent Control", border_style="green")

    def _build_logs_panel(self, title: str, payload: dict) -> Panel:
        logs = list((payload.get("snapshot") or {}).get("logs", [])[-8:])
        content = Group(*logs) if logs else Group("No logs yet.")
        return Panel(content, title=title, border_style="yellow")

    def render(self) -> Layout:
        payload = build_fleet_payload(self.mt5_symbol, self.binance_symbol)
        mt5 = payload.get("mt5", {})
        binance = payload.get("binance", {})

        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="summary", size=14),
            Layout(name="matrices", size=12),
            Layout(name="sequences", size=12),
            Layout(name="logs", ratio=1),
        )
        layout["summary"].split_row(
            Layout(name="fleet", size=30),
            Layout(name="mt5_summary", ratio=1),
            Layout(name="binance_summary", ratio=1),
        )
        layout["matrices"].split_row(
            Layout(name="mt5_matrix", ratio=1),
            Layout(name="binance_matrix", ratio=1),
        )
        layout["sequences"].split_row(
            Layout(name="mt5_sequence", ratio=1),
            Layout(name="binance_sequence", ratio=1),
        )
        layout["logs"].split_row(
            Layout(name="mt5_logs", ratio=1),
            Layout(name="binance_logs", ratio=1),
        )

        layout["header"].update(self._build_header(payload))
        layout["fleet"].update(self._build_fleet_panel(payload))
        layout["mt5_summary"].update(self._build_venue_panel("MT5 CFD", mt5))
        layout["binance_summary"].update(self._build_venue_panel("Binance Futures", binance))
        layout["mt5_matrix"].update(self._build_state_panel("MT5", mt5.get("snapshot", {})))
        layout["binance_matrix"].update(self._build_state_panel("BINANCE", binance.get("snapshot", {})))
        layout["mt5_sequence"].update(self._build_sequence_panel("MT5", mt5.get("snapshot", {})))
        layout["binance_sequence"].update(self._build_sequence_panel("BINANCE", binance.get("snapshot", {})))
        layout["mt5_logs"].update(self._build_logs_panel("MT5 Logs", mt5))
        layout["binance_logs"].update(self._build_logs_panel("Binance Logs", binance))
        return layout

    def run(self) -> None:
        with Live(self.render(), console=self.console, refresh_per_second=1, screen=True) as live:
            while True:
                live.update(self.render())
                time.sleep(1)


def main(*, mt5_symbol: str = "XAUUSD", binance_symbol: str = "BTCUSDT") -> None:
    SultanFleetDashboardApp(mt5_symbol=mt5_symbol, binance_symbol=binance_symbol).run()
