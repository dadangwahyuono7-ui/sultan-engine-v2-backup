from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from sultan_engine_v2.adapters.exchange.binance_client import (
    DEFAULT_FUTURES_BASE_URL,
    BinanceRestClient,
    parse_binance_klines,
    resolve_symbol_from_exchange_info,
)


class BinanceClientTests(unittest.TestCase):
    def test_parse_binance_klines(self) -> None:
        rows = [
            [1710000000000, "100.0", "110.0", "95.0", "105.0"],
            [1710000300000, "105.0", "112.0", "101.0", "111.0"],
        ]

        bars = parse_binance_klines(rows)

        self.assertEqual(2, len(bars))
        self.assertEqual(1710000000, bars[0].timestamp)
        self.assertEqual(111.0, bars[1].close)

    def test_resolve_symbol_uses_usdt_alias_for_usd_request(self) -> None:
        exchange_info = {
            "symbols": [
                {"symbol": "BTCUSDT", "status": "TRADING"},
                {"symbol": "ETHUSDT", "status": "TRADING"},
            ]
        }

        resolved = resolve_symbol_from_exchange_info(exchange_info, "BTCUSD")

        self.assertEqual("BTCUSDT", resolved.actual)
        self.assertEqual("usd-alias", resolved.source)

    def test_binance_client_defaults_to_futures_market_type(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            client = BinanceRestClient(api_key="x", api_secret="y")

        self.assertEqual("FUTURES", client.market_type)
        self.assertEqual(DEFAULT_FUTURES_BASE_URL, client.base_url)
        self.assertEqual("BINANCE_FUTURES", client.venue_label)
        self.assertEqual("/fapi/v1/klines", client._klines_path)


if __name__ == "__main__":
    unittest.main()
