from __future__ import annotations

import json
import unittest
from pathlib import Path
from shutil import rmtree
from uuid import uuid4
from unittest.mock import patch

from sultan_engine_v2.runtime.web_monitor import MonitorStatePaths, build_monitor_payload


class LocalTestDir:
    def __init__(self, name: str) -> None:
        self.path = Path(__file__).resolve().parents[2] / ".tmp" / f"{name}_{uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        rmtree(self.path, ignore_errors=True)


class WebMonitorTests(unittest.TestCase):
    def test_build_monitor_payload_reads_snapshot_and_status_files(self) -> None:
        with LocalTestDir("web_monitor") as temp_path:
            snapshot_path = temp_path / "latest_snapshot.json"
            service_status_path = temp_path / "mt5_xauusd_service_status.json"
            stack_status_path = temp_path / "vps_stack_xauusd_status.json"
            performance_path = temp_path / "performance_mt5_xauusd.json"

            snapshot_path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-03-20T03:00:00+00:00",
                        "venue": "MT5",
                        "symbol": {"actual": "XAUUSD"},
                        "market": {"bid": 4654.45, "spread": 0.08},
                        "setup_focus": {"status": "WAIT_CF", "summary": "WAIT_CF H4 | VR H1"},
                        "execution": {"recent_results": [{}, {}], "recent_decisions": [{}]},
                        "lifecycle": {"position_structure": []},
                        "logs": ["line 1", "line 2"],
                    }
                ),
                encoding="utf-8",
            )
            service_status_path.write_text(json.dumps({"status": "RUNNING"}), encoding="utf-8")
            stack_status_path.write_text(json.dumps({"status": "RUNNING"}), encoding="utf-8")
            performance_path.write_text(
                json.dumps(
                    {
                        "last_status": "PAPER_STABLE",
                        "last_score": 87.5,
                        "last_metrics": {"net_return_percent": 1.25, "max_drawdown_percent": 2.5},
                    }
                ),
                encoding="utf-8",
            )

            payload = build_monitor_payload(
                "XAUUSD",
                paths=MonitorStatePaths(
                    snapshot_path=snapshot_path,
                    service_status_path=service_status_path,
                    stack_status_path=stack_status_path,
                    performance_path=performance_path,
                ),
            )

        self.assertEqual("XAUUSD", payload["summary"]["symbol"])
        self.assertEqual("RUNNING", payload["summary"]["service"])
        self.assertEqual("WAIT_CF", payload["summary"]["focus_status"])
        self.assertEqual(2, payload["summary"]["recent_results"])
        self.assertEqual(2, payload["summary"]["recent_logs"])
        self.assertEqual("PAPER_STABLE", payload["summary"]["burn_in_status"])
        self.assertEqual(1.25, payload["summary"]["net_return_percent"])

    def test_build_monitor_payload_supports_env_override_paths(self) -> None:
        with LocalTestDir("web_monitor_override") as temp_path:
            snapshot_path = temp_path / "binance_btcusdt_snapshot.json"
            service_status_path = temp_path / "binance_btcusdt_service_status.json"
            stack_status_path = temp_path / "binance_stack_btcusdt_status.json"
            performance_path = temp_path / "performance_binance_btcusdt.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "timestamp": "2026-03-20T03:00:00+00:00",
                        "venue": "BINANCE",
                        "symbol": {"actual": "BTCUSDT"},
                        "market": {"price": 65000.0, "spread": 1.2},
                        "setup_focus": {"status": "NO_ACTIVE_VR", "summary": "-"},
                        "execution": {"recent_results": [], "recent_decisions": []},
                        "logs": [],
                    }
                ),
                encoding="utf-8",
            )
            service_status_path.write_text(json.dumps({"status": "RUNNING"}), encoding="utf-8")
            stack_status_path.write_text(json.dumps({"status": "RUNNING"}), encoding="utf-8")
            performance_path.write_text(
                json.dumps(
                    {
                        "last_status": "COLLECTING_DATA",
                        "last_score": 33.0,
                        "last_metrics": {"net_return_percent": 0.0, "max_drawdown_percent": 0.0},
                    }
                ),
                encoding="utf-8",
            )

            env = {
                "SULTAN_MONITOR_SNAPSHOT_PATH": str(snapshot_path),
                "SULTAN_MONITOR_SERVICE_STATUS_PATH": str(service_status_path),
                "SULTAN_MONITOR_STACK_STATUS_PATH": str(stack_status_path),
                "SULTAN_MONITOR_PERFORMANCE_PATH": str(performance_path),
            }
            with patch.dict("os.environ", env, clear=False):
                payload = build_monitor_payload("BTCUSDT")

        self.assertEqual("BINANCE", payload["summary"]["venue"])
        self.assertEqual("BTCUSDT", payload["summary"]["symbol"])
        self.assertEqual(65000.0, payload["summary"]["market_bid"])
        self.assertEqual("COLLECTING_DATA", payload["summary"]["burn_in_status"])


if __name__ == "__main__":
    unittest.main()
