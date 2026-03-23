from __future__ import annotations

import json
import unittest
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sultan_engine_v2.adapters.tradingview.mapping import (
    load_tradingview_symbol_overrides,
    map_tradingview_interval,
    map_tradingview_symbol,
    parse_tradingview_csv,
)
from sultan_engine_v2.core.execution_models import Venue


class LocalTestDir:
    def __init__(self, name: str) -> None:
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / f"{name}_{uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        rmtree(self.path, ignore_errors=True)


class TradingViewMappingTests(unittest.TestCase):
    def test_map_binance_symbol_keeps_binance_preference(self) -> None:
        mapping = map_tradingview_symbol("BINANCE:BTCUSDT")

        self.assertEqual(Venue.BINANCE, mapping.preferred_venue)
        self.assertEqual("BTCUSDT", mapping.base_symbol)
        self.assertEqual("BTCUSDT", mapping.binance_symbol)

    def test_map_oanda_symbol_routes_to_mt5(self) -> None:
        mapping = map_tradingview_symbol("OANDA:XAUUSD")

        self.assertEqual(Venue.MT5, mapping.preferred_venue)
        self.assertEqual("XAUUSD", mapping.base_symbol)
        self.assertEqual("XAUUSD", mapping.mt5_symbol)

    def test_map_crypto_perp_alias_to_usdt(self) -> None:
        mapping = map_tradingview_symbol("BYBIT:BTCUSD.P")

        self.assertEqual(Venue.BINANCE, mapping.preferred_venue)
        self.assertEqual("BTCUSDT", mapping.base_symbol)
        self.assertEqual("BTCUSDT", mapping.binance_symbol)

    def test_map_tradingview_interval_accepts_tv_and_engine_codes(self) -> None:
        self.assertEqual("H4", map_tradingview_interval("240"))
        self.assertEqual("D1", map_tradingview_interval("D1"))
        self.assertEqual("M30", map_tradingview_interval("30"))

    def test_parse_tradingview_csv_reads_ohlc_rows(self) -> None:
        with LocalTestDir("tv_csv") as temp_dir:
            csv_path = temp_dir / "bars.csv"
            csv_path.write_text(
                "time,open,high,low,close\n"
                "2026-03-20T00:00:00Z,100,110,95,105\n"
                "2026-03-20T01:00:00Z,105,112,101,111\n",
                encoding="utf-8",
            )

            bars = parse_tradingview_csv(csv_path)

        self.assertEqual(2, len(bars))
        self.assertEqual(105.0, bars[0].close)
        self.assertEqual(111.0, bars[1].close)
        self.assertGreater(int(bars[1].timestamp or 0), int(bars[0].timestamp or 0))

    def test_load_symbol_overrides_reads_json_file(self) -> None:
        with LocalTestDir("tv_override") as temp_dir:
            override_path = temp_dir / "tradingview_map.json"
            override_path.write_text(
                json.dumps(
                    {
                        "symbols": {
                            "TVC:GOLD": {
                                "base_symbol": "XAUUSD",
                                "preferred_venue": "MT5",
                                "mt5_symbol": "XAUUSD",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            overrides = load_tradingview_symbol_overrides(override_path)
            mapping = map_tradingview_symbol("TVC:GOLD", overrides=overrides)

        self.assertEqual("XAUUSD", mapping.base_symbol)
        self.assertEqual(Venue.MT5, mapping.preferred_venue)
        self.assertEqual("override", mapping.source)


if __name__ == "__main__":
    unittest.main()
