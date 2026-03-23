from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - optional dependency at runtime
    mt5 = None


@dataclass(slots=True)
class Mt5TerminalSnapshot:
    connected: bool
    login: int | None = None
    server: str = ""
    broker_name: str = ""
    currency: str = ""
    leverage: int | None = None
    balance: float = 0.0
    equity: float = 0.0
    margin_mode: int | None = None
    terminal_path: str = ""
    path_source: str = ""
    error: str = ""


def iter_terminal_candidates() -> list[str]:
    candidates: list[str] = []

    env_path = os.environ.get("MT5_TERMINAL_PATH")
    if env_path:
        candidates.append(env_path)

    common_paths = [
        Path("C:/Program Files/MetaTrader 5/terminal64.exe"),
        Path("C:/Program Files (x86)/MetaTrader 5/terminal64.exe"),
    ]

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        common_paths.append(Path(local_app_data) / "Programs/MetaTrader 5/terminal64.exe")

    program_roots = [Path("C:/Program Files"), Path("C:/Program Files (x86)")]
    for root in program_roots:
        if root.exists():
            for pattern in ("MetaTrader*/terminal64.exe", "*MetaTrader*/terminal64.exe"):
                common_paths.extend(root.glob(pattern))

    seen: set[str] = set()
    for path in common_paths:
        resolved = str(path)
        if resolved not in seen and path.exists():
            seen.add(resolved)
            candidates.append(resolved)

    return candidates


def read_active_terminal_snapshot(terminal_path: str = "", path_source: str = "") -> Mt5TerminalSnapshot:
    if mt5 is None:
        return Mt5TerminalSnapshot(connected=False, error="MetaTrader5 package is not installed.")

    account = mt5.account_info()
    terminal = mt5.terminal_info()
    if account is None or terminal is None:
        return Mt5TerminalSnapshot(connected=False, error=f"MT5 session unavailable: {mt5.last_error()}")

    return Mt5TerminalSnapshot(
        connected=True,
        login=getattr(account, "login", None),
        server=str(getattr(account, "server", "")),
        broker_name=str(getattr(account, "company", "")) or str(getattr(account, "server", "")),
        currency=str(getattr(account, "currency", "")),
        leverage=getattr(account, "leverage", None),
        balance=float(getattr(account, "balance", 0.0)),
        equity=float(getattr(account, "equity", 0.0)),
        margin_mode=getattr(account, "margin_mode", None),
        terminal_path=terminal_path or str(getattr(terminal, "path", "")),
        path_source=path_source,
        error="",
    )


def connect_active_terminal() -> Mt5TerminalSnapshot:
    if mt5 is None:
        return Mt5TerminalSnapshot(connected=False, error="MetaTrader5 package is not installed.")

    if mt5.initialize():
        return read_active_terminal_snapshot(path_source="default")

    last_error = mt5.last_error()
    for candidate in iter_terminal_candidates():
        try:
            mt5.shutdown()
        except Exception:
            pass

        if mt5.initialize(path=candidate):
            return read_active_terminal_snapshot(terminal_path=candidate, path_source="candidate")
        last_error = mt5.last_error()

    return Mt5TerminalSnapshot(
        connected=False,
        error=f"Could not attach to active MT5 terminal. Last error: {last_error}",
    )


def shutdown_terminal() -> None:
    if mt5 is not None:
        try:
            mt5.shutdown()
        except Exception:
            pass
