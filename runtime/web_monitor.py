from __future__ import annotations

import hmac
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


@dataclass(slots=True)
class MonitorStatePaths:
    snapshot_path: Path
    service_status_path: Path
    stack_status_path: Path
    performance_path: Path | None = None


def resolve_state_paths(base_symbol: str = "XAUUSD") -> MonitorStatePaths:
    state_dir = Path(__file__).resolve().parent / "state"
    symbol_slug = base_symbol.lower()
    snapshot_override = os.getenv("SULTAN_MONITOR_SNAPSHOT_PATH", "").strip()
    service_override = os.getenv("SULTAN_MONITOR_SERVICE_STATUS_PATH", "").strip()
    stack_override = os.getenv("SULTAN_MONITOR_STACK_STATUS_PATH", "").strip()
    performance_override = os.getenv("SULTAN_MONITOR_PERFORMANCE_PATH", "").strip()
    return MonitorStatePaths(
        snapshot_path=Path(snapshot_override) if snapshot_override else state_dir / "latest_snapshot.json",
        service_status_path=Path(service_override) if service_override else state_dir / f"mt5_{symbol_slug}_service_status.json",
        stack_status_path=Path(stack_override) if stack_override else state_dir / f"vps_stack_{symbol_slug}_status.json",
        performance_path=Path(performance_override) if performance_override else None,
    )


def load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _snapshot_age_seconds(snapshot: dict) -> int | None:
    timestamp = snapshot.get("timestamp")
    if not timestamp:
        return None

    try:
        snapshot_time = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return None

    if snapshot_time.tzinfo is None:
        snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)

    return max(0, int((datetime.now(timezone.utc) - snapshot_time).total_seconds()))


def _default_performance_path(base_symbol: str, snapshot: dict) -> Path:
    state_dir = Path(__file__).resolve().parent / "state"
    venue_slug = str(snapshot.get("venue") or os.getenv("SULTAN_MONITOR_VENUE") or "MT5").lower()
    return state_dir / f"performance_{venue_slug}_{base_symbol.lower()}.json"


def build_monitor_payload(base_symbol: str = "XAUUSD", paths: MonitorStatePaths | None = None) -> dict:
    paths = paths or resolve_state_paths(base_symbol)
    snapshot = load_json_file(paths.snapshot_path)
    service_status = load_json_file(paths.service_status_path)
    stack_status = load_json_file(paths.stack_status_path)
    performance_path = paths.performance_path or _default_performance_path(base_symbol, snapshot)
    performance = load_json_file(performance_path)
    execution = snapshot.get("execution", {})
    lifecycle = snapshot.get("lifecycle", {})
    performance_metrics = performance.get("last_metrics", {})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_symbol": base_symbol,
        "snapshot_age_seconds": _snapshot_age_seconds(snapshot),
        "paths": {
            "snapshot": str(paths.snapshot_path),
            "service_status": str(paths.service_status_path),
            "stack_status": str(paths.stack_status_path),
            "performance": str(performance_path),
        },
        "service_status": service_status,
        "stack_status": stack_status,
        "performance": performance,
        "snapshot": snapshot,
        "summary": {
            "symbol": snapshot.get("symbol", {}).get("actual") or base_symbol,
            "venue": snapshot.get("venue") or "MT5",
            "service": stack_status.get("status") or service_status.get("status") or "UNKNOWN",
            "focus_status": snapshot.get("setup_focus", {}).get("status") or "NO_ACTIVE_VR",
            "focus_summary": snapshot.get("setup_focus", {}).get("summary") or "-",
            "market_bid": snapshot.get("market", {}).get("bid") or snapshot.get("market", {}).get("price") or 0.0,
            "market_spread": snapshot.get("market", {}).get("spread") or 0.0,
            "recent_results": len(execution.get("recent_results", [])),
            "recent_decisions": len(execution.get("recent_decisions", [])),
            "open_structure": len(lifecycle.get("position_structure", [])),
            "recent_logs": len(snapshot.get("logs", [])),
            "burn_in_status": performance.get("last_status") or "-",
            "burn_in_score": performance.get("last_score"),
            "net_return_percent": performance_metrics.get("net_return_percent"),
            "max_drawdown_percent": performance_metrics.get("max_drawdown_percent"),
        },
    }


def render_monitor_html(base_symbol: str = "XAUUSD") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sultan Web Monitor</title>
  <style>
    :root {{
      --bg: #0d1117;
      --panel: rgba(11, 18, 32, 0.88);
      --panel-2: rgba(17, 24, 39, 0.92);
      --line: rgba(78, 197, 255, 0.35);
      --text: #e8f4ff;
      --muted: #90a9bf;
      --accent: #32d4ff;
      --gold: #ffd166;
      --good: #4ade80;
      --warn: #f59e0b;
      --bad: #fb7185;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(50, 212, 255, 0.16), transparent 30%),
        radial-gradient(circle at top right, rgba(255, 209, 102, 0.12), transparent 22%),
        linear-gradient(180deg, #081019 0%, var(--bg) 100%);
      color: var(--text);
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
    }}
    .shell {{ max-width: 1400px; margin: 0 auto; padding: 18px; }}
    .hero {{
      border: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(8, 20, 36, 0.96), rgba(14, 26, 46, 0.92));
      border-radius: 18px;
      padding: 18px 20px;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
      margin-bottom: 16px;
    }}
    .hero h1 {{ margin: 0; font-size: 28px; letter-spacing: 0.08em; }}
    .hero p {{ margin: 8px 0 0; color: var(--muted); font-size: 16px; }}
    .grid {{ display: grid; gap: 14px; grid-template-columns: repeat(12, minmax(0, 1fr)); }}
    .card {{
      background: linear-gradient(180deg, var(--panel), var(--panel-2));
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 16px;
      padding: 14px 16px;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
    }}
    .span-3 {{ grid-column: span 3; }}
    .span-4 {{ grid-column: span 4; }}
    .span-5 {{ grid-column: span 5; }}
    .span-6 {{ grid-column: span 6; }}
    .span-7 {{ grid-column: span 7; }}
    .span-8 {{ grid-column: span 8; }}
    .span-12 {{ grid-column: span 12; }}
    .card h2 {{ margin: 0 0 10px; font-size: 15px; letter-spacing: 0.08em; color: var(--accent); text-transform: uppercase; }}
    .stat {{ display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; border-top: 1px solid rgba(255,255,255,0.05); font-size: 17px; }}
    .stat:first-of-type {{ border-top: 0; padding-top: 0; }}
    .stat .label {{ color: var(--muted); }}
    .stat .value {{ font-weight: 700; text-align: right; }}
    .status-good {{ color: var(--good); }}
    .status-warn {{ color: var(--warn); }}
    .status-bad {{ color: var(--bad); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 15px; }}
    th, td {{ padding: 8px 6px; border-bottom: 1px solid rgba(255,255,255,0.07); text-align: left; vertical-align: top; }}
    th {{ color: var(--gold); font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; }}
    .mono {{ font-family: "Consolas", "Courier New", monospace; }}
    .log-list {{ display: grid; gap: 8px; }}
    .log-item {{ padding: 9px 10px; border-radius: 10px; background: rgba(255,255,255,0.04); font-size: 15px; }}
    .muted {{ color: var(--muted); }}
    .empty {{ color: var(--muted); padding: 8px 0; }}
    @media (max-width: 1100px) {{
      .span-3, .span-4, .span-5, .span-6, .span-7, .span-8 {{ grid-column: span 12; }}
      .hero h1 {{ font-size: 23px; }}
      .stat {{ font-size: 16px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <h1>SULTAN WEB MONITOR</h1>
      <p>Read-only live view for {base_symbol}. Designed for VPS, HP, and ngrok tunnel access.</p>
    </section>

    <section class="grid">
      <div class="card span-3"><h2>Service</h2><div id="service-stats"></div></div>
      <div class="card span-4"><h2>Market</h2><div id="market-stats"></div></div>
      <div class="card span-5"><h2>Setup Focus</h2><div id="focus-stats"></div></div>

      <div class="card span-7">
        <h2>Timeframes</h2>
        <div id="timeframe-table"></div>
      </div>
      <div class="card span-5">
        <h2>Parent Control</h2>
        <div id="sequence-table"></div>
      </div>

      <div class="card span-6">
        <h2>Open Position Structure</h2>
        <div id="position-structure"></div>
      </div>
      <div class="card span-6">
        <h2>Recent Logs</h2>
        <div id="log-list" class="log-list"></div>
      </div>
    </section>
  </div>

  <script>
    const esc = (value) => String(value ?? '-').replace(/[&<>\"]/g, (char) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[char]));
    const setStats = (targetId, rows) => {{
      document.getElementById(targetId).innerHTML = rows.map(([label, value, statusClass]) => `
        <div class="stat">
          <span class="label">${{esc(label)}}</span>
          <span class="value ${{statusClass || ''}}">${{esc(value)}}</span>
        </div>`).join('');
    }};

    const renderTable = (targetId, headers, rows) => {{
      if (!rows.length) {{
        document.getElementById(targetId).innerHTML = '<div class="empty">No data yet.</div>';
        return;
      }}
      const thead = `<thead><tr>${{headers.map((header) => `<th>${{esc(header)}}</th>`).join('')}}</tr></thead>`;
      const tbody = `<tbody>${{rows.map((row) => `<tr>${{row.map((cell) => `<td>${{esc(cell)}}</td>`).join('')}}</tr>`).join('')}}</tbody>`;
      document.getElementById(targetId).innerHTML = `<table>${{thead}}${{tbody}}</table>`;
    }};

    const renderLogs = (logs) => {{
      const target = document.getElementById('log-list');
      if (!logs.length) {{
        target.innerHTML = '<div class="empty">No logs yet.</div>';
        return;
      }}
      target.innerHTML = logs.map((line) => `<div class="log-item mono">${{esc(line)}}</div>`).join('');
    }};

    const statusClass = (value) => {{
      const raw = String(value || '').toUpperCase();
      if (raw.includes('RUN') || raw.includes('BUY') || raw.includes('CONNECTED') || raw.includes('HEALTHY')) return 'status-good';
      if (raw.includes('WAIT') || raw.includes('GRACE') || raw.includes('ATTACK') || raw.includes('PAUSE')) return 'status-warn';
      if (raw.includes('SELL') || raw.includes('ERROR') || raw.includes('EXIT') || raw.includes('DISCONNECTED')) return 'status-bad';
      return '';
    }};

    async function refresh() {{
      const response = await fetch(`/api/monitor${{window.location.search || ''}}`, {{ cache: 'no-store' }});
      const payload = await response.json();
      const snapshot = payload.snapshot || {{}};
      const summary = payload.summary || {{}};
      const focus = snapshot.setup_focus || {{}};
      const market = snapshot.market || {{}};
      const stackStatus = payload.stack_status || {{}};
      const serviceStatus = payload.service_status || {{}};
      const lifecycle = snapshot.lifecycle || {{}};

      setStats('service-stats', [
        ['Stack', stackStatus.status || summary.service || 'UNKNOWN', statusClass(stackStatus.status || summary.service)],
        ['Service', serviceStatus.status || '-', statusClass(serviceStatus.status)],
        ['Symbol', summary.symbol || payload.base_symbol || '-'],
        ['Burn-In', summary.burn_in_status || '-', statusClass(summary.burn_in_status)],
        ['Score', summary.burn_in_score != null ? Number(summary.burn_in_score).toFixed(1) : '-'],
        ['Snapshot Age', `${{payload.snapshot_age_seconds ?? '-'}}s`],
        ['Updated', payload.generated_at || '-'],
      ]);

      setStats('market-stats', [
        ['Bid', market.bid || '-'],
        ['Ask', market.ask || '-'],
        ['Spread', market.spread || '-'],
        ['Return', summary.net_return_percent != null ? `${{Number(summary.net_return_percent).toFixed(2)}}%` : '-'],
        ['Drawdown', summary.max_drawdown_percent != null ? `${{Number(summary.max_drawdown_percent).toFixed(2)}}%` : '-'],
        ['Pulse', (snapshot.footprint || {{}}).recent_ticks?.length || 0],
        ['Results', summary.recent_results || 0],
      ]);

      setStats('focus-stats', [
        ['Status', focus.status || 'NO_ACTIVE_VR', statusClass(focus.status)],
        ['Master', `${{focus.master_timeframe || '-'}} ${{focus.master_direction || ''}}`.trim()],
        ['VR', `${{focus.vr_timeframe || '-'}} ${{focus.vr_direction || ''}}`.trim()],
        ['Target', focus.target_timeframe || '-'],
        ['Summary', focus.summary || summary.focus_summary || '-'],
      ]);

      renderTable('timeframe-table', ['TF', 'CMP', 'Role', 'Domain', 'Flip@', 'Level'], Object.entries(snapshot.timeframes || {{}}).map(([tf, node]) => [
        tf,
        node.cmp || '-',
        node.role || '-',
        node.domain || '-',
        node.cmp_timestamp || '-',
        node.breakout_level || '-',
      ]));

      renderTable('sequence-table', ['Parent', 'Dir', 'State', 'Ctrl', 'VR@', 'CF#'], Object.entries(snapshot.sequences || {{}}).map(([parent, seq]) => [
        parent,
        seq.direction || '-',
        seq.control_state || '-',
        seq.active_controller || '-',
        seq.vr_started_at || '-',
        seq.cf_count || 0,
      ]));

      const positions = lifecycle.position_structure || [];
      renderTable('position-structure', ['Ticket', 'Dir', 'Kind', 'Parent', 'CF', 'State'], positions.map((item) => [
        item.ticket || '-',
        item.direction || '-',
        item.entry_kind || '-',
        item.parent_timeframe || '-',
        item.cf_timeframe || '-',
        item.status || '-',
      ]));

      renderLogs((snapshot.logs || []).slice(-10).reverse());
    }}

    refresh().catch(console.error);
    setInterval(() => refresh().catch(console.error), 2500);
  </script>
</body>
</html>
"""


class SultanMonitorHandler(BaseHTTPRequestHandler):
    base_symbol = "XAUUSD"
    access_token = ""

    def do_GET(self) -> None:  # noqa: N802
        request = urlsplit(self.path)
        if self.access_token and not self._is_authorized(request):
            self._send_json(
                {"ok": False, "error": "unauthorized", "generated_at": datetime.now(timezone.utc).isoformat()},
                status_code=401,
            )
            return

        if request.path in {"/", "/index.html"}:
            self._send_html(render_monitor_html(self.base_symbol))
            return

        if request.path == "/api/monitor":
            self._send_json(build_monitor_payload(self.base_symbol))
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


def run_server(host: str = "127.0.0.1", port: int = 5000, base_symbol: str = "XAUUSD", access_token: str = "") -> None:
    handler = type(
        "BoundSultanMonitorHandler",
        (SultanMonitorHandler,),
        {"base_symbol": base_symbol, "access_token": access_token},
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Sultan web monitor running on http://{host}:{port} for {base_symbol}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
