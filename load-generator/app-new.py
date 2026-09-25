import json
import os
import re
import subprocess
import threading
import time
from collections import deque

from flask import Flask, Response, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest

CB_HOST = os.getenv("CB_HOST", "couchbase")
CB_USERNAME = os.getenv("CB_USERNAME", "Administrator")
CB_PASSWORD = os.getenv("CB_PASSWORD", "password123")
CB_BUCKET = os.getenv("CB_BUCKET", "travel-sample")
QUERIES_FILE = os.getenv("QUERIES_FILE", os.path.join(os.path.dirname(__file__), "queries.json"))
MAX_LOG_LINES = 300

# cbc-n1qlback -T prints a live latency histogram every second, e.g.:
#   [360  - 369 ]us |#    - 1
#   [10   - 19  ]ms | - 3
# one line per non-empty bucket: [low - high]<unit> |<bars> - <count>.
# The counts are cumulative for the whole process lifetime (they only ever
# grow across successive dumps), so the latest count seen for a given bucket
# range is always its current total.
_UNIT_SECONDS = {"us": 1e-6, "ms": 1e-3, "s": 1.0}
_HISTOGRAM_LINE = re.compile(
    r"^\[\s*(?P<low>\d+)\s*-\s*(?P<high>\d+)\s*\](?P<unit>us|ms|s)\s*\|.*-\s*(?P<count>\d+)\s*$"
)
_STATS_LINES = {
    "tick": re.compile(r"^\+(?P<seconds>\d+)s$"),
    "qps": re.compile(r"^QUERIES/SEC:\s+(?P<value>\d+)$"),
    "rps": re.compile(r"^ROWS/SEC:\s+(?P<value>\d+)$"),
    "errors": re.compile(r"^ERRORS:\s+(?P<value>\d+)$"),
}

def _load_queries(path):
    """Load the query list from queries.json (or QUERIES_FILE), so the demo's
    queries can be edited without touching this file. Fails fast and loudly
    on a missing/malformed file rather than silently falling back to
    defaults, since a typo here should be obvious immediately, not discovered
    later as "why is this query missing"."""
    with open(path) as f:
        queries = json.load(f)

    if not isinstance(queries, list) or not queries:
        raise ValueError(f"{path} must contain a non-empty JSON array of queries")

    seen_keys = set()
    for entry in queries:
        if not isinstance(entry, dict) or not entry.get("key") or not entry.get("statement"):
            raise ValueError(f"{path}: every entry needs a non-empty 'key' and 'statement', got {entry!r}")
        if entry["key"] in seen_keys:
            raise ValueError(f"{path}: duplicate query key {entry['key']!r}")
        if entry["key"] == "all":
            raise ValueError(f"{path}: 'all' is reserved (means every query) and can't be used as a query key")
        seen_keys.add(entry["key"])

    return queries


QUERIES = _load_queries(QUERIES_FILE)

# Real per-query duration, parsed straight out of cbc-n1qlback's own -T
# histogram lines (see _HISTOGRAM_LINE below) - scraped by Prometheus and
# charted in Grafana.
QUERY_DURATION = Histogram(
    "loadgen_query_duration_seconds",
    "Observed duration of the load generator's sample SQL++ queries",
    ["query"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)

app = Flask(__name__)

_log_lock = threading.Lock()
_logs = {q["key"]: deque(maxlen=MAX_LOG_LINES) for q in QUERIES}

# Latest cumulative bucket counts per query, keyed by (unit, low, high) -> count,
# for the live horizontal latency chart in the web view.
_hist_lock = threading.Lock()
_histograms = {q["key"]: {} for q in QUERIES}

# Latest QUERIES/SEC + ROWS/SEC + ERRORS readout per query.
_stats_lock = threading.Lock()
_stats = {q["key"]: {"tick": 0, "qps": 0, "rps": 0, "errors": 0} for q in QUERIES}

# Per-query run control: whether stream_n1qlback should keep this query's
# cbc-n1qlback running, and a handle to the current live process (if any) so
# a stop request can kill it immediately instead of waiting for it to die on
# its own.
_control_lock = threading.Lock()
_control = {q["key"]: {"want_running": True, "proc": None} for q in QUERIES}
QUERY_KEYS = {q["key"] for q in QUERIES}


def _append_log(key, line):
    with _log_lock:
        _logs[key].append(line)


def _tail(key):
    with _log_lock:
        return list(_logs[key])


def _record_line(key, line):
    """Feed one line of cbc-n1qlback -T output into whichever live state it
    belongs to: a histogram bucket row (-> Prometheus + chart snapshot) or a
    throughput readout row (QUERIES/SEC, ROWS/SEC, ERRORS, +Ns tick)."""
    m = _HISTOGRAM_LINE.match(line)
    if m:
        low, high, unit, count = m.group("low", "high", "unit", "count")
        count = int(count)
        with _hist_lock:
            _histograms[key][(unit, int(low), int(high))] = count

        # cbc-n1qlback's own bucket counts are cumulative for the process's
        # lifetime, but Prometheus histograms need each individual sample
        # observed once - only feed the newly-seen increment since last time.
        prev = _record_line._seen.setdefault(key, {}).get((unit, low, high), 0)
        delta = count - prev
        if delta > 0:
            midpoint_seconds = (int(low) + int(high)) / 2 * _UNIT_SECONDS[unit]
            metric = QUERY_DURATION.labels(query=key)
            for _ in range(delta):
                metric.observe(midpoint_seconds)
        _record_line._seen[key][(unit, low, high)] = count
        return

    for stat, pattern in _STATS_LINES.items():
        m = pattern.match(line)
        if m:
            value = int(m.group("seconds" if stat == "tick" else "value"))
            with _stats_lock:
                _stats[key][stat] = value
            return


_record_line._seen = {}


def _reset_query_state(key):
    """cbc-n1qlback's bucket counts are cumulative per-process; a fresh
    process starts back at zero, so wipe stale state before it does, or the
    chart would mix old totals with a freshly-restarted process's counts."""
    with _hist_lock:
        _histograms[key].clear()
    _record_line._seen[key] = {}
    with _stats_lock:
        _stats[key] = {"tick": 0, "qps": 0, "rps": 0, "errors": 0}


def _histogram_snapshot(key):
    with _hist_lock:
        buckets = list(_histograms[key].items())
    rows = [
        {
            "label": f"{low}-{high}{unit}",
            "low_us": low * _UNIT_SECONDS[unit] * 1e6,
            "count": count,
        }
        for (unit, low, high), count in buckets
    ]
    rows.sort(key=lambda r: r["low_us"])
    return rows


def stream_n1qlback(query):
    """Run cbc-n1qlback against a single query, streaming its live
    QUERIES/SEC + latency-histogram (-T) output into the per-query log, and
    feeding every histogram bucket it reports into Prometheus. Runs for as
    long as this query is enabled (want_running); if it dies on its own
    (e.g. Couchbase briefly unreachable) it's restarted with a short
    backoff; if /api/control stops it, it's killed and left stopped until
    /api/control starts it again."""
    key = query["key"]
    queryfile = f"/tmp/query-{key}.json"
    with open(queryfile, "w") as f:
        f.write(json.dumps({"statement": query["statement"]}) + "\n")

    cmd = [
        "cbc-n1qlback",
        "-U", f"couchbase://{CB_HOST}/{CB_BUCKET}",
        "-u", CB_USERNAME,
        "-P", CB_PASSWORD,
        "-t", "1",
        "-f", queryfile,
        "-T",
    ]

    while True:
        with _control_lock:
            want_running = _control[key]["want_running"]
        if not want_running:
            time.sleep(0.5)
            continue

        _reset_query_state(key)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            with _control_lock:
                _control[key]["proc"] = proc

            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    _append_log(key, line)
                    _record_line(key, line)
            proc.wait()
        except Exception as exc:
            _append_log(key, f"[error running cbc-n1qlback: {exc}]")

        with _control_lock:
            _control[key]["proc"] = None
            want_running = _control[key]["want_running"]

        if want_running:
            _append_log(key, "[cbc-n1qlback exited - restarting in 2s]")
            time.sleep(2)
        else:
            _append_log(key, "[cbc-n1qlback stopped]")


def _set_running(key, running):
    with _control_lock:
        _control[key]["want_running"] = running
        proc = _control[key]["proc"]
    if not running and proc is not None and proc.poll() is None:
        proc.terminate()


RANGE_OPTIONS = [
    (300, "Last 5m"),
    (900, "Last 15m"),
    (1800, "Last 30m"),
    (3600, "Last 1h"),
    (7200, "Last 2h"),
]


@app.route("/")
def index():
    range_options_html = "\n".join(
        f'<option value="{seconds}"{" selected" if seconds == 300 else ""}>{label}</option>'
        for seconds, label in RANGE_OPTIONS
    )
    panels = "\n".join(
        f'''
        <section class="panel">
          <h2>{q["statement"]}</h2>
          <div class="stats" id="stats-{q["key"]}">waiting for data...</div>
          <div class="chart-row">
            <div class="chart-col">
              <div class="chart-wrap"><canvas id="chart-{q["key"]}"></canvas></div>
            </div>
            <div class="chart-col">
              <div class="chart-col-header">
                <select id="range-{q["key"]}" onchange="onRangeChange('{q["key"]}')">
                  {range_options_html}
                </select>
                <button class="toggle-btn" id="btn-{q["key"]}" onclick="toggle('{q["key"]}')">...</button>
              </div>
              <div class="ts-wrap"><canvas id="qps-{q["key"]}"></canvas></div>
            </div>
          </div>
        </section>
        '''
        for q in QUERIES
    )
    keys = json.dumps([q["key"] for q in QUERIES])
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>Couchbase Load Generator (cbc-n1qlback)</title>
  <script src="/static/chart.umd.min.js"></script>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; margin: 0; background: #1e1e1e; color: #ddd; font-family: Arial, sans-serif; }}
    body {{ display: flex; flex-direction: column; }}
    .topbar {{ display: flex; justify-content: flex-end; align-items: center; padding: 8px 14px; background: #2a2a2a; border-bottom: 2px solid #333; }}
    .panel {{ flex: 1; display: flex; flex-direction: column; border-bottom: 2px solid #333; min-height: 0; padding: 10px 14px; }}
    .panel h2 {{ margin: 0 0 6px 0; font-size: 14px; font-weight: bold; font-family: 'Courier New', monospace; color: #4CAF50; }}
    .stats {{ font-family: 'Courier New', monospace; font-size: 12px; color: #999; margin-bottom: 4px; }}
    .stats b {{ color: #ddd; }}
    .chart-row {{ display: flex; flex: 1; min-height: 0; gap: 14px; }}
    .chart-col {{ flex: 1; min-width: 0; display: flex; flex-direction: column; min-height: 0; }}
    .chart-col-header {{ display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-bottom: 4px; }}
    .chart-wrap {{ flex: 1; min-height: 0; overflow-y: auto; }}
    .ts-wrap {{ flex: 1; min-height: 0; }}
    select {{ background: #2a2a2a; color: #ddd; border: 1px solid #444; border-radius: 4px; padding: 5px 8px; font-size: 12px; font-family: Arial, sans-serif; }}
    button.toggle-btn {{ flex-shrink: 0; padding: 6px 14px; font-size: 12px; font-weight: bold; font-family: Arial, sans-serif; border: none; border-radius: 4px; cursor: pointer; color: white; }}
    button.toggle-btn.running {{ background: #f44336; }}
    button.toggle-btn.stopped {{ background: #4CAF50; }}
    button.toggle-btn:disabled {{ opacity: 0.5; cursor: default; }}
  </style>
</head>
<body>
  <div class="topbar">
    <button class="toggle-btn" id="btn-all" onclick="toggle('all')">...</button>
  </div>
  {panels}
  <script>
    const keys = {keys};
    const charts = {{}};
    const qpsCharts = {{}};
    const qpsHistory = {{}};      // key -> [{{t, qps}}], capped at 2h, never sent to the server
    const selectedRange = {{}};   // key -> window size in seconds
    const runningState = {{}};
    const MAX_HISTORY_MS = 2 * 3600 * 1000;

    for (const key of keys) {{
      const ctx = document.getElementById('chart-' + key);
      charts[key] = new Chart(ctx, {{
        type: 'bar',
        data: {{ labels: [], datasets: [{{
          label: 'observations',
          data: [],
          backgroundColor: '#4CAF50',
        }}] }},
        options: {{
          indexAxis: 'y',
          animation: false,
          responsive: true,
          maintainAspectRatio: false,
          scales: {{
            x: {{ beginAtZero: true, ticks: {{ color: '#999' }}, grid: {{ color: '#333' }} }},
            y: {{ ticks: {{ color: '#999', autoSkip: true, maxTicksLimit: 30 }}, grid: {{ color: '#2a2a2a' }} }},
          }},
          plugins: {{ legend: {{ display: false }} }},
        }},
      }});

      qpsHistory[key] = [];
      selectedRange[key] = 300;
      const ctx2 = document.getElementById('qps-' + key);
      qpsCharts[key] = new Chart(ctx2, {{
        type: 'line',
        data: {{ labels: [], datasets: [{{
          label: 'queries/sec',
          data: [],
          borderColor: '#2196F3',
          backgroundColor: 'rgba(33,150,243,0.15)',
          fill: true,
          pointRadius: 0,
          borderWidth: 1.5,
          tension: 0,
        }}] }},
        options: {{
          animation: false,
          responsive: true,
          maintainAspectRatio: false,
          scales: {{
            // Category axis: the tick labels are the sample timestamps
            // themselves, spaced one per sample in arrival order (not by
            // clock time) - so it always fills the chart width with
            // whatever's been collected so far, rather than reserving empty
            // space out to the full selected window before data exists.
            x: {{ ticks: {{ color: '#999', autoSkip: true, maxTicksLimit: 8 }}, grid: {{ color: '#2a2a2a' }} }},
            y: {{ beginAtZero: true, ticks: {{ color: '#999' }}, grid: {{ color: '#333' }} }},
          }},
          plugins: {{ legend: {{ display: false }} }},
        }},
      }});
    }}

    function setButton(btn, running, label) {{
      btn.textContent = (running ? 'Stop' : 'Start') + (label ? ' ' + label : '');
      btn.classList.toggle('running', running);
      btn.classList.toggle('stopped', !running);
      btn.disabled = false;
    }}

    async function toggle(key) {{
      const btn = document.getElementById('btn-' + key);
      btn.disabled = true;
      const running = key === 'all' ? keys.some(k => runningState[k]) : runningState[key];
      const action = running ? 'stop' : 'start';
      await fetch(`/api/control/${{key}}/${{action}}`, {{ method: 'POST' }});
      await refresh();
    }}

    function fmtTime(t) {{
      return new Date(t).toLocaleTimeString([], {{ hour12: false }});
    }}

    function onRangeChange(key) {{
      selectedRange[key] = parseInt(document.getElementById('range-' + key).value, 10);
      renderQpsChart(key);
    }}

    function renderQpsChart(key) {{
      const cutoff = Date.now() - selectedRange[key] * 1000;
      const points = qpsHistory[key].filter(p => p.t >= cutoff);
      const chart = qpsCharts[key];
      chart.data.labels = points.map(p => fmtTime(p.t));
      chart.data.datasets[0].data = points.map(p => p.qps);
      chart.update('none');
    }}

    async function refresh() {{
      const res = await fetch('/api/live');
      const data = await res.json();
      const now = Date.now();
      for (const key of keys) {{
        const q = data[key];
        if (!q) continue;

        const s = q.stats;
        runningState[key] = s.running;
        setButton(document.getElementById('btn-' + key), s.running);

        document.getElementById('stats-' + key).innerHTML = s.running
          ? `+<b>${{s.tick}}s</b> &nbsp; <b>${{s.qps}}</b> queries/s &nbsp; <b>${{s.rps}}</b> rows/s &nbsp; <b>${{s.errors}}</b> errors`
          : `<b style="color:#f44336">STOPPED</b> &nbsp; last: +${{s.tick}}s, ${{s.qps}} queries/s`;

        const chart = charts[key];
        chart.data.labels = q.buckets.map(b => b.label);
        chart.data.datasets[0].data = q.buckets.map(b => b.count);
        // ~16px per bar so the full distribution stays readable however many buckets are active
        chart.canvas.parentNode.style.height = Math.max(240, q.buckets.length * 16) + 'px';
        chart.resize();
        chart.update('none');

        // Query rate over time - kept only in this tab's memory, capped at 2h;
        // 0 while stopped, since that's the true rate during a stopped period.
        const history = qpsHistory[key];
        history.push({{ t: now, qps: s.running ? s.qps : 0 }});
        while (history.length && now - history[0].t > MAX_HISTORY_MS) {{
          history.shift();
        }}
        renderQpsChart(key);
      }}
      setButton(document.getElementById('btn-all'), keys.some(k => runningState[k]), 'All');
    }}
    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>"""


@app.route("/api/output")
def api_output():
    return jsonify({q["key"]: _tail(q["key"]) for q in QUERIES})


@app.route("/api/live")
def api_live():
    with _stats_lock:
        stats_snapshot = {k: dict(v) for k, v in _stats.items()}
    with _control_lock:
        running_snapshot = {k: v["want_running"] for k, v in _control.items()}
    return jsonify({
        q["key"]: {
            "stats": {**stats_snapshot[q["key"]], "running": running_snapshot[q["key"]]},
            "buckets": _histogram_snapshot(q["key"]),
        }
        for q in QUERIES
    })


@app.route("/api/control/<key>/<action>", methods=["POST"])
def api_control(key, action):
    if action not in ("start", "stop"):
        return jsonify({"error": "action must be start or stop"}), 400

    if key == "all":
        for k in QUERY_KEYS:
            _set_running(k, action == "start")
        return jsonify({"status": "ok", "key": "all", "action": action})

    if key not in QUERY_KEYS:
        return jsonify({"error": f"unknown query key {key!r}"}), 404

    _set_running(key, action == "start")
    return jsonify({"status": "ok", "key": key, "action": action})


@app.route("/api/health")
def api_health():
    return jsonify({"status": "healthy"})


@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def main():
    for query in QUERIES:
        threading.Thread(target=stream_n1qlback, args=(query,), daemon=True).start()

    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
