from __future__ import annotations

import unittest
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sultan_engine_v2.runtime.tradingview_replay import build_tradingview_replay, parse_timeframe_file_args


class LocalTestDir:
    def __init__(self, name: str) -> None:
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / f"{name}_{uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        rmtree(self.path, ignore_errors=True)


def write_csv(path: Path) -> None:
    path.write_text(
        "time,open,high,low,close\n"
        "2026-03-20T00:00:00Z,9,10,8,9.5\n"
        "2026-03-20T01:00:00Z,10,11,9,11\n"
        "2026-03-20T02:00:00Z,12,12,10,10\n"
        "2026-03-20T03:00:00Z,10,10,8,9\n"
        "2026-03-20T04:00:00Z,8,12,8,12\n"
        "2026-03-20T05:00:00Z,11,12.5,10.5,12.5\n",
        encoding="utf-8",
    )


class TradingViewReplayTests(unittest.TestCase):
    def test_parse_timeframe_file_args_normalizes_codes(self) -> None:
        mapping = parse_timeframe_file_args(["240=C:\\temp\\h4.csv", "D1=C:\\temp\\d1.csv"])

        self.assertIn("H4", mapping)
        self.assertIn("D1", mapping)

    def test_build_tradingview_replay_creates_mt5_snapshot(self) -> None:
        with LocalTestDir("tv_replay_mt5") as temp_dir:
            d1_path = temp_dir / "d1.csv"
            h4_path = temp_dir / "h4.csv"
            write_csv(d1_path)
            write_csv(h4_path)

            result = build_tradingview_replay(
                ticker="OANDA:XAUUSD",
                timeframe_files={"D1": d1_path, "H4": h4_path},
            )

        self.assertEqual("MT5", result.snapshot["venue"])
        self.assertEqual("XAUUSD", result.snapshot["symbol"]["requested"])
        self.assertIn("D1", result.snapshot["timeframes"])
        self.assertTrue(result.output_path.exists())
        self.assertIn("Manager audit", result.report.summary)

    def test_build_tradingview_replay_creates_binance_snapshot(self) -> None:
        with LocalTestDir("tv_replay_binance") as temp_dir:
            h1_path = temp_dir / "h1.csv"
            m30_path = temp_dir / "m30.csv"
            write_csv(h1_path)
            write_csv(m30_path)

            result = build_tradingview_replay(
                ticker="BINANCE:BTCUSDT",
                timeframe_files={"H1": h1_path, "M30": m30_path},
            )

        self.assertEqual("BINANCE", result.snapshot["venue"])
        self.assertEqual("BTCUSDT", result.snapshot["symbol"]["requested"])
        self.assertEqual("TRADINGVIEW_REPLAY", result.snapshot["analysis_source"])
        self.assertGreater(float(result.snapshot["market"]["price"] or 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
