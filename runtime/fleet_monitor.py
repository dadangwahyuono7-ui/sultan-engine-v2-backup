from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sultan_engine_v2.runtime.web_monitor import MonitorStatePaths, build_monitor_payload


@dataclass(slots=True)
class FleetMonitorPaths:
    mt5: MonitorStatePaths
    binance: MonitorStatePaths


def _state_dir() -> Path:
    return Path(__file__).resolve().parent / "state"


def build_mt5_paths(base_symbol: str) -> MonitorStatePaths:
    symbol_slug = base_symbol.lower()
    state_dir = _state_dir()
    return MonitorStatePaths(
        snapshot_path=state_dir / "latest_snapshot.json",
        service_status_path=state_dir / f"mt5_{symbol_slug}_service_status.json",
        stack_status_path=state_dir / f"vps_stack_{symbol_slug}_status.json",
        performance_path=state_dir / f"performance_mt5_{symbol_slug}.json",
    )


def build_binance_paths(base_symbol: str) -> MonitorStatePaths:
    symbol_slug = base_symbol.lower()
    state_dir = _state_dir()
    return MonitorStatePaths(
        snapshot_path=state_dir / f"binance_{symbol_slug}_snapshot.json",
        service_status_path=state_dir / f"binance_{symbol_slug}_service_status.json",
        stack_status_path=state_dir / f"binance_stack_{symbol_slug}_status.json",
        performance_path=state_dir / f"performance_binance_{symbol_slug}.json",
    )


def build_fleet_payload(
    mt5_symbol: str = "XAUUSD",
    binance_symbol: str = "BTCUSDT",
    *,
    paths: FleetMonitorPaths | None = None,
) -> dict:
    paths = paths or FleetMonitorPaths(
        mt5=build_mt5_paths(mt5_symbol),
        binance=build_binance_paths(binance_symbol),
    )
    mt5_payload = build_monitor_payload(mt5_symbol, paths=paths.mt5)
    binance_payload = build_monitor_payload(binance_symbol, paths=paths.binance)
    services = [mt5_payload.get("summary", {}).get("service"), binance_payload.get("summary", {}).get("service")]
    running_count = sum(1 for item in services if str(item or "").upper() == "RUNNING")
    connected_count = sum(
        1
        for item in (mt5_payload.get("snapshot", {}), binance_payload.get("snapshot", {}))
        if bool(item.get("health", {}).get("connected"))
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "running_services": running_count,
            "connected_venues": connected_count,
            "fleet_status": "HEALTHY" if running_count == 2 and connected_count == 2 else "PARTIAL",
            "mt5_symbol": mt5_payload.get("summary", {}).get("symbol") or mt5_symbol,
            "binance_symbol": binance_payload.get("summary", {}).get("symbol") or binance_symbol,
        },
        "mt5": mt5_payload,
        "binance": binance_payload,
    }


def render_fleet_monitor_html(mt5_symbol: str = "XAUUSD", binance_symbol: str = "BTCUSDT") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sultan Fleet Monitor</title>
  <style>
    :root {{
      --bg: #08111b;
      --panel: rgba(8, 18, 30, 0.92);
      --line: rgba(76, 190, 255, 0.25);
      --text: #e7f3ff;
      --muted: #8fa7bb;
      --accent: #41d5ff;
      --good: #4ade80;
      --warn: #f59e0b;
      --bad: #fb7185;
      --gold: #ffd166;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(65, 213, 255, 0.14), transparent 28%),
        radial-gradient(circle at top right, rgba(255, 209, 102, 0.10), transparent 22%),
        linear-gradient(180deg, #071019 0%, var(--bg) 100%);
      color: var(--text);
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
    }}
    .shell {{ max-width: 1500px; margin: 0 auto; padding: 18px; }}
    .hero {{
      border: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(8, 20, 36, 0.96), rgba(14, 26, 46, 0.92));
      border-radius: 18px;
      padding: 18px 20px;
      margin-bottom: 16px;
    }}
    .hero h1 {{ margin: 0; font-size: 28px; letter-spacing: 0.08em; }}
    .hero p {{ margin: 8px 0 0; color: var(--muted); font-size: 16px; }}
    .grid {{ display: grid; gap: 14px; grid-template-columns: repeat(12, minmax(0, 1fr)); }}
    .card {{
      background: linear-gradient(180deg, var(--panel), rgba(15, 24, 39, 0.95));
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 16px;
      padding: 14px 16px;
    }}
    .span-4 {{ grid-column: span 4; }}
    .span-6 {{ grid-column: span 6; }}
    .span-12 {{ grid-column: span 12; }}
    .card h2 {{ margin: 0 0 10px; font-size: 15px; letter-spacing: 0.08em; color: var(--accent); text-transform: uppercase; }}
    .stat {{ display: flex; justify-content: space-between; gap: 10px; padding: 8px 0; border-top: 1px solid rgba(255,255,255,0.05); font-size: 16px; }}
    .stat:first-of-type {{ border-top: 0; padding-top: 0; }}
    .label {{ color: var(--muted); }}
    .value {{ font-weight: 700; text-align: right; }}
    .status-good {{ color: var(--good); }}
    .status-warn {{ color: var(--warn); }}
    .status-bad {{ color: var(--bad); }}
    .log-list {{ display: grid; gap: 8px; }}
    .log-item {{ padding: 9px 10px; border-radius: 10px; background: rgba(255,255,255,0.04); font-size: 14px; }}
    .mono {{ font-family: "Consolas", "Courier New", monospace; }}
    @media (max-width: 1100px) {{
      .span-4, .span-6 {{ grid-column: span 12; }}
      .hero h1 {{ font-size: 23px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>SULTAN FLEET MONITOR</h1>
      <p>Unified view for MT5 CFD ({mt5_symbol}) and Binance Futures ({binance_symbol}). Designed for one-click start and ngrok access.</p>
    </section>
    <section class="grid">
      <div class="card span-4"><h2>Fleet</h2><div id="fleet-stats"></div></div>
      <div class="card span-4"><h2>MT5</h2><div id="mt5-stats"></div></div>
      <div class="card span-4"><h2>Binance</h2><div id="binance-stats"></div></div>
      <div class="card span-6"><h2>MT5 Logs</h2><div id="mt5-logs" class="log-list"></div></div>
      <div class="card span-6"><h2>Binance Logs</h2><div id="binance-logs" class="log-list"></div></div>
    </section>
  </div>
  <script>
    const esc = (value) => String(value ?? '-').replace(/[&<>\\"]/g, (char) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[char]));
    const setStats = (targetId, rows) => {{
      document.getElementById(targetId).innerHTML = rows.map(([label, value, statusClass]) => `
        <div class="stat">
          <span class="label">${{esc(label)}}</span>
          <span class="value ${{statusClass || ''}}">${{esc(value)}}</span>
        </div>`).join('');
    }};
    const renderLogs = (targetId, logs) => {{
      const target = document.getElementById(targetId);
      if (!logs.length) {{
        target.innerHTML = '<div class="log-item">No logs yet.</div>';
        return;
      }}
      target.innerHTML = logs.map((line) => `<div class="log-item mono">${{esc(line)}}</div>`).join('');
    }};
    const statusClass = (value) => {{
      const raw = String(value || '').toUpperCase();
      if (raw.includes('RUN') || raw.includes('HEALTHY') || raw.includes('READY') || raw.includes('BUY') || raw.includes('CONNECTED')) return 'status-good';
      if (raw.includes('WAIT') || raw.includes('COLLECT') || raw.includes('PAPER') || raw.includes('PARTIAL')) return 'status-warn';
      if (raw.includes('NOT_READY') || raw.includes('ERROR') || raw.includes('DISCONNECTED') || raw.includes('SELL')) return 'status-bad';
      return '';
    }};
    const buildVenueRows = (payload) => {{
      const summary = payload.summary || {{}};
      const snapshot = payload.snapshot || {{}};
      const account = snapshot.mt5 || snapshot.paper_account || {{}};
      return [
        ['Service', summary.service || '-', statusClass(summary.service)],
        ['Focus', summary.focus_status || '-', statusClass(summary.focus_status)],
        ['Burn-In', summary.burn_in_status || '-', statusClass(summary.burn_in_status)],
        ['Score', summary.burn_in_score != null ? Number(summary.burn_in_score).toFixed(1) : '-'],
        ['Bid/Price', summary.market_bid ?? '-'],
        ['Spread', summary.market_spread ?? '-'],
        ['Balance', account.balance != null ? Number(account.balance).toFixed(2) : '-'],
        ['Equity', account.equity != null ? Number(account.equity).toFixed(2) : '-'],
      ];
    }};
    async function refresh() {{
      const response = await fetch(`/api/fleet${{window.location.search || ''}}`, {{ cache: 'no-store' }});
      const payload = await response.json();
      const fleet = payload.summary || {{}};
      const mt5 = payload.mt5 || {{}};
      const binance = payload.binance || {{}};
      setStats('fleet-stats', [
        ['Fleet', fleet.fleet_status || '-', statusClass(fleet.fleet_status)],
        ['Running', fleet.running_services ?? 0],
        ['Connected', fleet.connected_venues ?? 0],
        ['Generated', payload.generated_at || '-'],
      ]);
      setStats('mt5-stats', buildVenueRows(mt5));
      setStats('binance-stats', buildVenueRows(binance));
      renderLogs('mt5-logs', ((mt5.snapshot || {{}}).logs || []).slice(-8).reverse());
      renderLogs('binance-logs', ((binance.snapshot || {{}}).logs || []).slice(-8).reverse());
    }}
    refresh().catch(console.error);
    setInterval(() => refresh().catch(console.error), 2500);
  </script>
</body>
</html>
"""


class SultanFleetMonitorHandler(BaseHTTPRequestHandler):
    mt5_symbol = "XAUUSD"
    binance_symbol = "BTCUSDT"
    access_token = ""

    def do_GET(self) -> None:  # noqa: N802
        request = urlsplit(self.path)
        if self.access_token and not self._is_authorized(request):
            self._send_json({"ok": False, "error": "unauthorized", "generated_at": datetime.now(timezone.utc).isoformat()}, status_code=401)
            return

        if request.path in {"/", "/index.html"}:
            self._send_html(render_fleet_monitor_html(self.mt5_symbol, self.binance_symbol))
            return
        if request.path == "/api/fleet":
            self._send_json(build_fleet_payload(self.mt5_symbol, self.binance_symbol))
            return
        if request.path == "/api/health":
            self._send_json({"ok": True, "generated_at": datetime.now(timezone.utc).isoformat()})
            return
        self.send_error(404, "Not Found")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _send_html(self, html: str) -> None:
        encoded = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload: dict, *, status_code: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=True, indent=2, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _is_authorized(self, request: object) -> bool:
        query = parse_qs(getattr(request, "query", ""))
        query_token = (query.get("token") or [""])[0]
        header_token = self.headers.get("X-Sultan-Token", "")
        supplied_token = header_token or query_token
        return bool(supplied_token) and hmac.compare_digest(supplied_token, self.access_token)


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    mt5_symbol: str = "XAUUSD",
    binance_symbol: str = "BTCUSDT",
    access_token: str = "",
) -> None:
    handler = type(
        "BoundSultanFleetMonitorHandler",
        (SultanFleetMonitorHandler,),
        {
            "mt5_symbol": mt5_symbol,
            "binance_symbol": binance_symbol,
            "access_token": access_token,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Sultan fleet monitor running on http://{host}:{port} | MT5={mt5_symbol} | BINANCE={binance_symbol}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
