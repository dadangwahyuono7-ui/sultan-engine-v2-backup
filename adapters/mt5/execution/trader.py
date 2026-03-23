from __future__ import annotations

from dataclasses import asdict

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - optional runtime dependency
    mt5 = None

from sultan_engine_v2.adapters.mt5.discovery.active_terminal import connect_active_terminal, shutdown_terminal
from sultan_engine_v2.core.execution_models import ExecutionMode, ExecutionResult, TradeIntent
from sultan_engine_v2.core.models import TradeDirection


class Mt5ExecutionClient:
    def __init__(self, deviation_points: int = 30, magic_number: int = 26031926) -> None:
        self.deviation_points = deviation_points
        self.magic_number = magic_number

    def execute_intent(self, intent: TradeIntent) -> ExecutionResult:
        if intent.execution_mode == ExecutionMode.PAPER:
            return ExecutionResult(
                intent_key=intent.intent_key,
                venue=intent.venue,
                symbol=intent.symbol,
                success=True,
                execution_mode=intent.execution_mode,
                message="MT5 paper execution simulated",
                executed_price=intent.entry_price,
                quantity=intent.quantity,
                metadata=intent.metadata,
            )

        if mt5 is None:
            return ExecutionResult(
                intent_key=intent.intent_key,
                venue=intent.venue,
                symbol=intent.symbol,
                success=False,
                execution_mode=intent.execution_mode,
                message="MetaTrader5 package is not installed",
                metadata=intent.metadata,
            )

        owns_connection = False
        if not self._has_active_connection():
            snapshot = connect_active_terminal()
            owns_connection = True
        else:
            snapshot = None

        if owns_connection and (snapshot is None or not snapshot.connected):
            return ExecutionResult(
                intent_key=intent.intent_key,
                venue=intent.venue,
                symbol=intent.symbol,
                success=False,
                execution_mode=intent.execution_mode,
                message=f"MT5 connection failed: {snapshot.error}",
                metadata=intent.metadata,
            )

        try:
            symbol_info = mt5.symbol_info(intent.symbol)
            if symbol_info is None:
                return ExecutionResult(
                    intent_key=intent.intent_key,
                    venue=intent.venue,
                    symbol=intent.symbol,
                    success=False,
                    execution_mode=intent.execution_mode,
                    message="MT5 symbol_info returned None",
                    metadata=intent.metadata,
                )

            if not symbol_info.visible and not mt5.symbol_select(intent.symbol, True):
                return ExecutionResult(
                    intent_key=intent.intent_key,
                    venue=intent.venue,
                    symbol=intent.symbol,
                    success=False,
                    execution_mode=intent.execution_mode,
                    message=f"Failed to select symbol {intent.symbol}",
                    metadata=intent.metadata,
                )

            tick = mt5.symbol_info_tick(intent.symbol)
            if tick is None:
                return ExecutionResult(
                    intent_key=intent.intent_key,
                    venue=intent.venue,
                    symbol=intent.symbol,
                    success=False,
                    execution_mode=intent.execution_mode,
                    message="MT5 symbol_info_tick returned None",
                    metadata=intent.metadata,
                )

            price = float(tick.ask if intent.direction == TradeDirection.BUY else tick.bid)
            volume = self._estimate_volume(intent, price)
            if volume <= 0:
                return ExecutionResult(
                    intent_key=intent.intent_key,
                    venue=intent.venue,
                    symbol=intent.symbol,
                    success=False,
                    execution_mode=intent.execution_mode,
                    message="Risk sizing produced zero volume",
                    metadata=intent.metadata,
                )

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": intent.symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_BUY if intent.direction == TradeDirection.BUY else mt5.ORDER_TYPE_SELL,
                "price": price,
                "sl": intent.stop_loss,
                "tp": intent.take_profit,
                "deviation": self.deviation_points,
                "magic": self.magic_number,
                "comment": intent.order_reason[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": getattr(mt5, "ORDER_FILLING_IOC", 1),
            }
            result = mt5.order_send(request)
            if result is None:
                return ExecutionResult(
                    intent_key=intent.intent_key,
                    venue=intent.venue,
                    symbol=intent.symbol,
                    success=False,
                    execution_mode=intent.execution_mode,
                    message="MT5 order_send returned None",
                    quantity=volume,
                    metadata=intent.metadata,
                )

            raw = result._asdict() if hasattr(result, "_asdict") else {"repr": str(result)}
            success = bool(getattr(result, "retcode", 0) == getattr(mt5, "TRADE_RETCODE_DONE", 10009))
            order_id = getattr(result, "order", None) or getattr(result, "deal", None)
            message = getattr(result, "comment", "Order processed")
            return ExecutionResult(
                intent_key=intent.intent_key,
                venue=intent.venue,
                symbol=intent.symbol,
                success=success,
                execution_mode=intent.execution_mode,
                message=str(message),
                order_id=str(order_id) if order_id else None,
                executed_price=price,
                quantity=volume,
                metadata={**intent.metadata, "mt5_result": raw},
            )
        finally:
            if owns_connection:
                shutdown_terminal()

    @staticmethod
    def _has_active_connection() -> bool:
        if mt5 is None:
            return False
        try:
            return mt5.account_info() is not None and mt5.terminal_info() is not None
        except Exception:
            return False

    def _estimate_volume(self, intent: TradeIntent, entry_price: float) -> float:
        account = mt5.account_info()
        symbol_info = mt5.symbol_info(intent.symbol)
        if account is None or symbol_info is None:
            return 0.0

        equity = float(getattr(account, "equity", 0.0) or getattr(account, "balance", 0.0) or 0.0)
        risk_amount = equity * intent.risk_fraction
        if risk_amount <= 0:
            return 0.0

        volume_min = float(getattr(symbol_info, "volume_min", 0.0) or 0.0)
        volume_max = float(getattr(symbol_info, "volume_max", volume_min) or volume_min)
        volume_step = float(getattr(symbol_info, "volume_step", volume_min) or volume_min)
        if volume_min <= 0 or volume_step <= 0:
            return 0.0

        order_type = mt5.ORDER_TYPE_BUY if intent.direction == TradeDirection.BUY else mt5.ORDER_TYPE_SELL
        best_volume = 0.0
        volume = volume_min
        iterations = 0
        while volume <= volume_max + 1e-12 and iterations < 2000:
            theoretical_loss = mt5.order_calc_profit(order_type, intent.symbol, volume, entry_price, intent.stop_loss)
            if theoretical_loss is None:
                contract_size = float(getattr(symbol_info, "trade_contract_size", 1.0) or 1.0)
                theoretical_loss = -abs(entry_price - intent.stop_loss) * contract_size * volume

            if abs(float(theoretical_loss)) <= risk_amount:
                best_volume = volume
                volume += volume_step
                iterations += 1
                continue
            break

        if best_volume <= 0:
            return 0.0
        return round(best_volume, self._volume_precision(volume_step))

    @staticmethod
    def _volume_precision(step: float) -> int:
        text = f"{step:.10f}".rstrip("0")
        if "." not in text:
            return 0
        return len(text.split(".", 1)[1])
