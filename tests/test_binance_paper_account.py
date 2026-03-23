from __future__ import annotations

import unittest
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sultan_engine_v2.core.execution_models import ExecutionMode, TradeIntent, Venue
from sultan_engine_v2.core.models import TradeDirection
from sultan_engine_v2.runtime.binance_paper_account import (
    BinancePaperAccount,
    BinancePaperAccountConfig,
    JsonBinancePaperAccountStore,
)


class LocalTestDir:
    def __init__(self, name: str) -> None:
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / f"{name}_{uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        rmtree(self.path, ignore_errors=True)


def build_intent(direction: TradeDirection = TradeDirection.BUY) -> TradeIntent:
    return TradeIntent(
        intent_key=f"BINANCE:BTCUSDT:H1:M15:{direction}:100.0",
        venue=Venue.BINANCE,
        symbol="BTCUSDT",
        timeframe="M15",
        direction=direction,
        entry_price=100.0,
        stop_loss=95.0 if direction == TradeDirection.BUY else 105.0,
        take_profit=110.0 if direction == TradeDirection.BUY else 90.0,
        risk_fraction=0.01,
        order_reason="paper account test",
        execution_mode=ExecutionMode.PAPER,
    )


class BinancePaperAccountTests(unittest.TestCase):
    def test_open_intent_creates_position_and_updates_balance(self) -> None:
        with LocalTestDir("binance_paper_open") as temp_dir:
            account = BinancePaperAccount(
                config=BinancePaperAccountConfig(starting_balance=10000.0, fee_bps=10.0, max_leverage=3.0),
                store=JsonBinancePaperAccountStore(temp_dir / "paper_account.json"),
            )

            result = account.open_intent(build_intent(), fill_price=100.0)
            snapshot = account.snapshot()

        self.assertTrue(result.success)
        self.assertEqual(1, len(snapshot["positions"]))
        self.assertLess(snapshot["balance"], 10000.0)
        self.assertEqual(snapshot["equity"], snapshot["balance"])

    def test_mark_to_market_closes_take_profit_and_realizes_pnl(self) -> None:
        with LocalTestDir("binance_paper_tp") as temp_dir:
            account = BinancePaperAccount(
                config=BinancePaperAccountConfig(starting_balance=10000.0, fee_bps=0.0, max_leverage=3.0),
                store=JsonBinancePaperAccountStore(temp_dir / "paper_account.json"),
            )

            account.open_intent(build_intent(), fill_price=100.0)
            events = account.mark_to_market("BTCUSDT", bid=111.0, ask=111.2)
            snapshot = account.snapshot()

        self.assertEqual(1, len(events))
        self.assertEqual("TAKE_PROFIT", events[0].kind)
        self.assertEqual(0, len(snapshot["positions"]))
        self.assertGreater(snapshot["balance"], 10000.0)

    def test_short_can_be_disabled(self) -> None:
        with LocalTestDir("binance_paper_short_disabled") as temp_dir:
            account = BinancePaperAccount(
                config=BinancePaperAccountConfig(starting_balance=10000.0, allow_short=False),
                store=JsonBinancePaperAccountStore(temp_dir / "paper_account.json"),
            )

            result = account.open_intent(build_intent(direction=TradeDirection.SELL), fill_price=100.0)

        self.assertFalse(result.success)
        self.assertIn("short", result.message.lower())


if __name__ == "__main__":
    unittest.main()
