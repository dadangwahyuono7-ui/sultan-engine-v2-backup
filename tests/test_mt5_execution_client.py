from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sultan_engine_v2.adapters.mt5.execution.trader import Mt5ExecutionClient
from sultan_engine_v2.core.execution_models import ExecutionMode, TradeIntent, Venue
from sultan_engine_v2.core.models import TradeDirection


class FakeMt5:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self, connected: bool) -> None:
        self.connected = connected

    def account_info(self):
        return SimpleNamespace(equity=1000.0, balance=1000.0) if self.connected else None

    def terminal_info(self):
        return SimpleNamespace(path="C:/MT5/terminal64.exe") if self.connected else None

    def symbol_info(self, symbol: str):
        return SimpleNamespace(visible=True)

    def symbol_select(self, symbol: str, visible: bool):
        return True

    def symbol_info_tick(self, symbol: str):
        return SimpleNamespace(ask=2000.0, bid=1999.5)

    def order_send(self, request: dict):
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=12345, deal=67890, comment="done")


class DeterministicExecutionClient(Mt5ExecutionClient):
    def _estimate_volume(self, intent: TradeIntent, entry_price: float) -> float:
        return 0.1


def build_live_intent() -> TradeIntent:
    return TradeIntent(
        intent_key="MT5:XAUUSD:H1:M15:BUY:1998.0",
        venue=Venue.MT5,
        symbol="XAUUSD",
        timeframe="M15",
        direction=TradeDirection.BUY,
        entry_price=2000.0,
        stop_loss=1994.0,
        take_profit=2026.0,
        risk_fraction=0.005,
        order_reason="H1 recovered by CF on M15",
        execution_mode=ExecutionMode.LIVE,
        metadata={},
    )


class Mt5ExecutionClientTests(unittest.TestCase):
    def test_live_execution_reuses_existing_mt5_session_without_shutdown(self) -> None:
        fake_mt5 = FakeMt5(connected=True)

        with (
            patch("sultan_engine_v2.adapters.mt5.execution.trader.mt5", fake_mt5),
            patch("sultan_engine_v2.adapters.mt5.execution.trader.connect_active_terminal") as connect_mock,
            patch("sultan_engine_v2.adapters.mt5.execution.trader.shutdown_terminal") as shutdown_mock,
        ):
            client = DeterministicExecutionClient()
            result = client.execute_intent(build_live_intent())

        self.assertTrue(result.success)
        connect_mock.assert_not_called()
        shutdown_mock.assert_not_called()

    def test_live_execution_closes_connection_only_when_client_opened_it(self) -> None:
        fake_mt5 = FakeMt5(connected=False)

        def fake_connect():
            fake_mt5.connected = True
            return SimpleNamespace(connected=True, error="")

        def fake_shutdown():
            fake_mt5.connected = False

        with (
            patch("sultan_engine_v2.adapters.mt5.execution.trader.mt5", fake_mt5),
            patch("sultan_engine_v2.adapters.mt5.execution.trader.connect_active_terminal", side_effect=fake_connect) as connect_mock,
            patch("sultan_engine_v2.adapters.mt5.execution.trader.shutdown_terminal", side_effect=fake_shutdown) as shutdown_mock,
        ):
            client = DeterministicExecutionClient()
            result = client.execute_intent(build_live_intent())

        self.assertTrue(result.success)
        connect_mock.assert_called_once()
        shutdown_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
