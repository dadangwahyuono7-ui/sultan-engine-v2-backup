from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sultan_engine_v2.core.execution_models import ExecutionResult, TradeIntent, Venue
from sultan_engine_v2.core.models import TradeDirection
from sultan_engine_v2.runtime.file_utils import atomic_write_json


def _parse_bool(raw_value: str | None, default: bool = True) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "on", "yes"}


def _parse_float(raw_value: str | None, default: float) -> float:
    try:
        return float(raw_value) if raw_value is not None else default
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass(slots=True)
class BinancePaperAccountConfig:
    starting_balance: float = 10000.0
    currency: str = "USDT"
    fee_bps: float = 10.0
    max_leverage: float = 3.0
    allow_short: bool = True


def load_binance_paper_account_config_from_env() -> BinancePaperAccountConfig:
    return BinancePaperAccountConfig(
        starting_balance=max(0.0, _parse_float(os.getenv("SULTAN_BINANCE_PAPER_START_BALANCE"), 10000.0)),
        currency=(os.getenv("SULTAN_BINANCE_PAPER_CURRENCY", "USDT") or "USDT").upper(),
        fee_bps=max(0.0, _parse_float(os.getenv("SULTAN_BINANCE_PAPER_FEE_BPS"), 10.0)),
        max_leverage=max(0.1, _parse_float(os.getenv("SULTAN_BINANCE_PAPER_MAX_LEVERAGE"), 3.0)),
        allow_short=_parse_bool(os.getenv("SULTAN_BINANCE_PAPER_ALLOW_SHORT"), default=True),
    )


@dataclass(slots=True)
class PaperPosition:
    intent_key: str
    symbol: str
    direction: str
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float
    opened_at: str
    opened_fee: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0


@dataclass(slots=True)
class PaperAccountEvent:
    kind: str
    symbol: str
    intent_key: str
    direction: str
    price: float
    quantity: float
    realized_pnl: float = 0.0
    message: str = ""
    created_at: str = ""


@dataclass(slots=True)
class BinancePaperAccountState:
    balance: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    currency: str
    updated_at: str = ""
    trade_count: int = 0
    closed_trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    positions: list[PaperPosition] = field(default_factory=list)
    recent_events: list[PaperAccountEvent] = field(default_factory=list)


class JsonBinancePaperAccountStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, config: BinancePaperAccountConfig) -> BinancePaperAccountState:
        if not self.path.exists():
            starting = config.starting_balance
            return BinancePaperAccountState(
                balance=starting,
                equity=starting,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                currency=config.currency,
            )

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            starting = config.starting_balance
            return BinancePaperAccountState(
                balance=starting,
                equity=starting,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                currency=config.currency,
            )

        balance = payload.get("balance")
        equity = payload.get("equity")
        return BinancePaperAccountState(
            balance=_safe_float(balance) if balance is not None else config.starting_balance,
            equity=_safe_float(equity) if equity is not None else config.starting_balance,
            realized_pnl=_safe_float(payload.get("realized_pnl")),
            unrealized_pnl=_safe_float(payload.get("unrealized_pnl")),
            currency=str(payload.get("currency") or config.currency),
            updated_at=str(payload.get("updated_at") or ""),
            trade_count=int(payload.get("trade_count") or 0),
            closed_trade_count=int(payload.get("closed_trade_count") or 0),
            win_count=int(payload.get("win_count") or 0),
            loss_count=int(payload.get("loss_count") or 0),
            positions=[PaperPosition(**item) for item in payload.get("positions", [])],
            recent_events=[PaperAccountEvent(**item) for item in payload.get("recent_events", [])],
        )

    def save(self, state: BinancePaperAccountState) -> BinancePaperAccountState:
        atomic_write_json(self.path, asdict(state))
        return state


class BinancePaperExecutor:
    def __init__(self, account: "BinancePaperAccount", *, price_provider: Callable[[TradeIntent], float]) -> None:
        self.account = account
        self.price_provider = price_provider

    def execute_intent(self, intent: TradeIntent) -> ExecutionResult:
        return self.account.open_intent(intent, fill_price=float(self.price_provider(intent)))


class BinancePaperAccount:
    def __init__(
        self,
        *,
        config: BinancePaperAccountConfig | None = None,
        store: JsonBinancePaperAccountStore | None = None,
    ) -> None:
        self.config = config or load_binance_paper_account_config_from_env()
        runtime_dir = Path(__file__).resolve().parent
        self.store = store or JsonBinancePaperAccountStore(runtime_dir / "state" / "binance_paper_account.json")
        self.state = self.store.load(self.config)

    def open_intent(self, intent: TradeIntent, *, fill_price: float) -> ExecutionResult:
        if intent.direction == TradeDirection.SELL and not self.config.allow_short:
            return ExecutionResult(
                intent_key=intent.intent_key,
                venue=Venue.BINANCE,
                symbol=intent.symbol,
                success=False,
                execution_mode=intent.execution_mode,
                message="Paper short is disabled for Binance simulation",
                metadata=intent.metadata,
            )

        stop_distance = abs(fill_price - float(intent.stop_loss or 0.0))
        if fill_price <= 0 or stop_distance <= 0:
            return ExecutionResult(
                intent_key=intent.intent_key,
                venue=Venue.BINANCE,
                symbol=intent.symbol,
                success=False,
                execution_mode=intent.execution_mode,
                message="Paper quantity sizing failed because fill/stop distance is invalid",
                metadata=intent.metadata,
            )

        self.mark_to_market(intent.symbol, bid=fill_price, ask=fill_price)

        risk_capital = max(self.state.balance, 0.0) * max(float(intent.risk_fraction), 0.0)
        quantity = risk_capital / stop_distance if risk_capital > 0 else 0.0
        available_notional = max((self.state.balance * self.config.max_leverage) - self._open_notional(), 0.0)
        max_quantity = available_notional / fill_price if fill_price > 0 else 0.0
        quantity = min(quantity, max_quantity)

        if quantity <= 0:
            return ExecutionResult(
                intent_key=intent.intent_key,
                venue=Venue.BINANCE,
                symbol=intent.symbol,
                success=False,
                execution_mode=intent.execution_mode,
                message="Paper account has no available margin for new Binance position",
                metadata=intent.metadata,
            )

        opened_fee = fill_price * quantity * (self.config.fee_bps / 10000.0)
        self.state.balance -= opened_fee
        position = PaperPosition(
            intent_key=intent.intent_key,
            symbol=intent.symbol,
            direction=intent.direction.value,
            quantity=quantity,
            entry_price=fill_price,
            stop_loss=float(intent.stop_loss),
            take_profit=float(intent.take_profit),
            opened_at=datetime.now(timezone.utc).isoformat(),
            opened_fee=opened_fee,
            current_price=fill_price,
            unrealized_pnl=0.0,
        )
        self.state.positions.append(position)
        self.state.trade_count += 1
        event = PaperAccountEvent(
            kind="OPENED",
            symbol=intent.symbol,
            intent_key=intent.intent_key,
            direction=intent.direction.value,
            price=fill_price,
            quantity=quantity,
            realized_pnl=-opened_fee,
            message="Binance paper position opened",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._push_event(event)
        self._recalculate_equity()
        self._touch()
        self.store.save(self.state)
        return ExecutionResult(
            intent_key=intent.intent_key,
            venue=Venue.BINANCE,
            symbol=intent.symbol,
            success=True,
            execution_mode=intent.execution_mode,
            message="Binance paper position opened",
            executed_price=fill_price,
            quantity=quantity,
            metadata={
                **intent.metadata,
                "paper_currency": self.state.currency,
                "paper_balance": round(self.state.balance, 8),
                "paper_equity": round(self.state.equity, 8),
                "paper_open_fee": round(opened_fee, 8),
            },
        )

    def mark_to_market(self, symbol: str, *, bid: float, ask: float) -> list[PaperAccountEvent]:
        events: list[PaperAccountEvent] = []
        remaining: list[PaperPosition] = []
        for position in self.state.positions:
            if position.symbol != symbol:
                remaining.append(position)
                continue

            exit_price, exit_kind = self._exit_signal(position, bid=bid, ask=ask)
            if exit_kind:
                event = self._close_position(position, exit_price=exit_price, kind=exit_kind)
                events.append(event)
                continue

            mark_price = bid if position.direction == TradeDirection.BUY.value else ask
            position.current_price = mark_price
            position.unrealized_pnl = self._direction_sign(position.direction) * (mark_price - position.entry_price) * position.quantity
            remaining.append(position)

        self.state.positions = remaining
        self._recalculate_equity()
        if events:
            self._touch()
            self.store.save(self.state)
        return events

    def snapshot(self) -> dict[str, Any]:
        self._recalculate_equity()
        return {
            "currency": self.state.currency,
            "balance": self.state.balance,
            "equity": self.state.equity,
            "realized_pnl": self.state.realized_pnl,
            "unrealized_pnl": self.state.unrealized_pnl,
            "trade_count": self.state.trade_count,
            "closed_trade_count": self.state.closed_trade_count,
            "win_count": self.state.win_count,
            "loss_count": self.state.loss_count,
            "open_notional": self._open_notional(),
            "positions": [asdict(item) for item in self.state.positions],
            "recent_events": [asdict(item) for item in self.state.recent_events],
        }

    def _close_position(self, position: PaperPosition, *, exit_price: float, kind: str) -> PaperAccountEvent:
        close_fee = exit_price * position.quantity * (self.config.fee_bps / 10000.0)
        gross_pnl = self._direction_sign(position.direction) * (exit_price - position.entry_price) * position.quantity
        net_pnl = gross_pnl - close_fee
        self.state.balance += net_pnl
        self.state.realized_pnl += net_pnl - position.opened_fee
        self.state.closed_trade_count += 1
        if net_pnl >= 0:
            self.state.win_count += 1
        else:
            self.state.loss_count += 1
        event = PaperAccountEvent(
            kind=kind,
            symbol=position.symbol,
            intent_key=position.intent_key,
            direction=position.direction,
            price=exit_price,
            quantity=position.quantity,
            realized_pnl=net_pnl,
            message=f"Binance paper position closed by {kind}",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._push_event(event)
        return event

    def _push_event(self, event: PaperAccountEvent) -> None:
        recent_events = list(self.state.recent_events)
        recent_events.append(event)
        self.state.recent_events = recent_events[-20:]

    def _recalculate_equity(self) -> None:
        self.state.unrealized_pnl = sum(item.unrealized_pnl for item in self.state.positions)
        self.state.equity = self.state.balance + self.state.unrealized_pnl

    def _open_notional(self) -> float:
        return sum(item.entry_price * item.quantity for item in self.state.positions)

    def _touch(self) -> None:
        self.state.updated_at = datetime.now(timezone.utc).isoformat()

    def _exit_signal(self, position: PaperPosition, *, bid: float, ask: float) -> tuple[float, str | None]:
        if position.direction == TradeDirection.BUY.value:
            if bid <= position.stop_loss:
                return position.stop_loss, "STOP_LOSS"
            if bid >= position.take_profit:
                return position.take_profit, "TAKE_PROFIT"
            return bid, None

        if ask >= position.stop_loss:
            return position.stop_loss, "STOP_LOSS"
        if ask <= position.take_profit:
            return position.take_profit, "TAKE_PROFIT"
        return ask, None

    @staticmethod
    def _direction_sign(direction: str) -> int:
        return 1 if direction == TradeDirection.BUY.value else -1
