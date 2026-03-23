from __future__ import annotations

import json
import unittest
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from sultan_engine_v2.runtime.fleet_monitor import FleetMonitorPaths, build_fleet_payload
from sultan_engine_v2.runtime.web_monitor import MonitorStatePaths


class LocalTestDir:
    def __init__(self, name: str) -> None:
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / f"{name}_{uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        rmtree(self.path, ignore_errors=True)


class FleetMonitorTests(unittest.TestCase):
    def test_build_fleet_payload_reads_mt5_and_binance_views(self) -> None:
        with LocalTestDir("fleet_monitor") as temp_path:
            mt5_snapshot = temp_path / "latest_snapshot.json"
            mt5_service = temp_path / "mt5_xauusd_service_status.json"
            mt5_stack = temp_path / "vps_stack_xauusd_status.json"
            mt5_perf = temp_path / "performance_mt5_xauusd.json"
            binance_snapshot = temp_path / "binance_btcusdt_snapshot.json"
            binance_service = temp_path / "binance_btcusdt_service_status.json"
            binance_stack = temp_path / "binance_stack_btcusdt_status.json"
            binance_perf = temp_path / "performance_binance_btcusdt.json"

            mt5_snapshot.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-03-21T00:00:00+00:00",
                        "venue": "MT5",
                        "symbol": {"actual": "XAUUSD"},
                        "mt5": {"balance": 1000.0, "equity": 1000.0},
                        "market": {"bid": 4500.0, "spread": 0.2},
                        "setup_focus": {"status": "WAIT_CF"},
                        "health": {"connected": True},
                        "logs": ["mt5 ok"],
                    }
                ),
                encoding="utf-8",
            )
            mt5_service.write_text(json.dumps({"status": "RUNNING"}), encoding="utf-8")
            mt5_stack.write_text(json.dumps({"status": "RUNNING"}), encoding="utf-8")
            mt5_perf.write_text(json.dumps({"last_status": "COLLECTING_DATA", "last_score": 50.0, "last_metrics": {}}), encoding="utf-8")

            binance_snapshot.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-03-21T00:00:00+00:00",
                        "venue": "BINANCE",
                        "symbol": {"actual": "BTCUSDT"},
                        "paper_account": {"balance": 10000.0, "equity": 10010.0},
                        "market": {"bid": 70000.0, "spread": 1.0},
                        "setup_focus": {"status": "NO_ACTIVE_VR"},
                        "health": {"connected": True},
                        "logs": ["binance ok"],
                    }
                ),
                encoding="utf-8",
            )
            binance_service.write_text(json.dumps({"status": "RUNNING"}), encoding="utf-8")
            binance_stack.write_text(json.dumps({"status": "RUNNING"}), encoding="utf-8")
            binance_perf.write_text(json.dumps({"last_status": "PAPER_STABLE", "last_score": 80.0, "last_metrics": {}}), encoding="utf-8")

            payload = build_fleet_payload(
                "XAUUSD",
                "BTCUSDT",
                paths=FleetMonitorPaths(
                    mt5=MonitorStatePaths(mt5_snapshot, mt5_service, mt5_stack, mt5_perf),
                    binance=MonitorStatePaths(binance_snapshot, binance_service, binance_stack, binance_perf),
                ),
            )

        self.assertEqual("HEALTHY", payload["summary"]["fleet_status"])
        self.assertEqual("XAUUSD", payload["mt5"]["summary"]["symbol"])
        self.assertEqual("BTCUSDT", payload["binance"]["summary"]["symbol"])
        self.assertEqual("PAPER_STABLE", payload["binance"]["summary"]["burn_in_status"])


if __name__ == "__main__":
    unittest.main()
