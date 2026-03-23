from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from sultan_engine_v2.core.execution_models import ExecutionMode
from sultan_engine_v2.run_dashboard import parse_dashboard_options
from sultan_engine_v2.run_fleet_monitor import parse_fleet_monitor_options
from sultan_engine_v2.run_production_stack import parse_production_options
from sultan_engine_v2.run_web_monitor import parse_monitor_options


class LauncherEntrypointTests(unittest.TestCase):
    def test_parse_dashboard_options_uses_environment_defaults(self) -> None:
        env = {
            "MT5_RUNTIME_SYMBOL": "EURUSD",
            "SULTAN_EXECUTION_MODE": "LIVE",
            "SULTAN_AUTO_EXECUTE": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            symbol, execution_mode, auto_execute = parse_dashboard_options()

        self.assertEqual(symbol, "EURUSD")
        self.assertEqual(execution_mode, ExecutionMode.LIVE)
        self.assertFalse(auto_execute)

    def test_parse_monitor_options_uses_environment_defaults(self) -> None:
        env = {
            "SULTAN_WEB_MONITOR_HOST": "0.0.0.0",
            "SULTAN_WEB_MONITOR_PORT": "7010",
            "MT5_RUNTIME_SYMBOL": "GBPUSD",
            "SULTAN_WEB_MONITOR_TOKEN": "sekret",
        }
        with patch.dict(os.environ, env, clear=False):
            host, port, symbol, token = parse_monitor_options()

        self.assertEqual(host, "0.0.0.0")
        self.assertEqual(port, 7010)
        self.assertEqual(symbol, "GBPUSD")
        self.assertEqual(token, "sekret")

    def test_parse_production_options_uses_environment_defaults(self) -> None:
        env = {
            "MT5_RUNTIME_SYMBOL": "USDJPY",
            "SULTAN_EXECUTION_MODE": "LIVE",
            "SULTAN_AUTO_EXECUTE": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            symbol, execution_mode, auto_execute = parse_production_options()

        self.assertEqual(symbol, "USDJPY")
        self.assertEqual(execution_mode, ExecutionMode.LIVE)
        self.assertFalse(auto_execute)

    def test_parse_fleet_monitor_options_defaults_to_port_5000(self) -> None:
        env = {
            "SULTAN_FLEET_MONITOR_PORT": "",
            "SULTAN_WEB_MONITOR_PORT": "",
            "MT5_RUNTIME_SYMBOL": "XAUUSD",
            "BINANCE_RUNTIME_SYMBOL": "BTCUSDT",
            "SULTAN_WEB_MONITOR_TOKEN": "",
        }
        with patch.dict(os.environ, env, clear=False):
            host, port, mt5_symbol, binance_symbol, token = parse_fleet_monitor_options()

        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 5000)
        self.assertEqual(mt5_symbol, "XAUUSD")
        self.assertEqual(binance_symbol, "BTCUSDT")
        self.assertEqual(token, "")
