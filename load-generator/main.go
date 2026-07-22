package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/couchbase/gocb/v2"
)

type Config struct {
	KVGetOps        int `json:"kv_get_ops"`
	KVSetOps        int `json:"kv_set_ops"`
	KVUpsertOps     int `json:"kv_upsert_ops"`
	N1QLSelectOps   int `json:"n1ql_select_ops"`
	N1QLUpsertOps   int `json:"n1ql_upsert_ops"`
	N1QLJoinOps     int `json:"n1ql_join_ops"`
	IsRunning       bool `json:"is_running"`
}

type Metrics struct {
	KVGet        int64 `json:"kv_get"`
	KVSet        int64 `json:"kv_set"`
	KVUpsert     int64 `json:"kv_upsert"`
	N1QLSelect   int64 `json:"n1ql_select"`
	N1QLUpsert   int64 `json:"n1ql_upsert"`
	N1QLJoin     int64 `json:"n1ql_join"`
	Total        int64 `json:"total"`
	CurrentRate  int64 `json:"current_rate"`
	IsRunning    bool  `json:"is_running"`
}

var (
	config    = Config{
		KVGetOps:      0,
		KVSetOps:      0,
		KVUpsertOps:   0,
		N1QLSelectOps: 0,
		N1QLUpsertOps: 0,
		N1QLJoinOps:   0,
	}
	metrics   = Metrics{}
	mu        sync.RWMutex
	bucket    *gocb.Bucket
	cluster   *gocb.Cluster
	stopChan  chan struct{}
	lastCount int64
	lastTime  time.Time
	logs      []LogEntry
	logMutex  sync.RWMutex
	maxLogs   = 100
)

type LogEntry struct {
	Time   string `json:"time"`
	Op     string `json:"op"`
	Detail string `json:"detail"`
}

func main() {
	// Connect to Couchbase
	cbHost := os.Getenv("CB_HOST")
	if cbHost == "" {
		cbHost = "couchbase"
	}
	cbUser := os.Getenv("CB_USERNAME")
	if cbUser == "" {
		cbUser = "Administrator"
	}
	cbPass := os.Getenv("CB_PASSWORD")
	if cbPass == "" {
		cbPass = "password123"
	}
	cbBucket := os.Getenv("CB_BUCKET")
	if cbBucket == "" {
		cbBucket = "travel-sample"
	}

	// Go's stock http.Transport (used internally by gocbcore for N1QL/HTTP
	// requests) defaults to keeping only 2 idle connections per host. Under our
	// 128-worker concurrent N1QL load that meant almost every query had to dial
	// a brand new TCP connection instead of reusing one - burning CPU on
	// connection setup/teardown and, as ephemeral ports/fds got scarce, making
	// dials progressively slower (the "throughput decays after a while"
	// symptom). Raise the per-host and total idle pool sizes to comfortably
	// cover our worker count.
	connStr := fmt.Sprintf("couchbase://%s?max_idle_http_connections=200&max_perhost_idle_http_connections=200&idle_http_connection_timeout=30000", cbHost)
	c, err := gocb.Connect(connStr, gocb.ClusterOptions{
		Authenticator: gocb.PasswordAuthenticator{
			Username: cbUser,
			Password: cbPass,
		},
	})
	if err != nil {
		log.Fatalf("Failed to connect: %v", err)
	}
	cluster = c

	b := c.Bucket(cbBucket)
	b.WaitUntilReady(10*time.Second, nil)
	bucket = b

	log.Printf("Connected to %s, bucket: %s", cbHost, cbBucket)

	// HTTP routes
	http.HandleFunc("/", serveUI)
	http.HandleFunc("/api/status", apiStatus)
	http.HandleFunc("/api/config", apiConfig)
	http.HandleFunc("/api/control/start", apiStart)
	http.HandleFunc("/api/control/stop", apiStop)
	http.HandleFunc("/api/health", apiHealth)
	http.HandleFunc("/api/logs", apiLogs)
	http.HandleFunc("/api/logs/clear", apiLogsClear)

	log.Println("🚀 Load generator started on :5000")
	http.ListenAndServe(":5000", nil)
}

func serveUI(w http.ResponseWriter, r *http.Request) {
	html := `<!DOCTYPE html>
<html>
<head>
    <title>Couchbase Load Generator</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
        h1 { color: #333; margin: 0 0 20px 0; }
        .controls { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .status-bar { display: flex; gap: 10px; margin-bottom: 20px; }
        .status { flex: 1; padding: 12px; background: #e8f5e9; border-left: 4px solid #4CAF50; border-radius: 4px; }
        button { padding: 10px 20px; font-size: 14px; cursor: pointer; border-radius: 4px; border: none; margin-right: 10px; }
        .start-btn { background: #4CAF50; color: white; }
        .stop-btn { background: #f44336; color: white; }
        button:disabled { background: #ccc; color: #888; cursor: not-allowed; }
        .slider-row { display: flex; align-items: center; padding: 12px 0 12px 12px; border-bottom: 1px solid #eee; gap: 15px; }
        .slider-row:last-child { border-bottom: none; }
        .slider-label { width: 120px; font-weight: bold; color: #333; }
        .slider-control { position: relative; flex: 1; padding-top: 24px; padding-bottom: 16px; }
        input[type="range"] { width: 100%; cursor: pointer; display: block; margin: 0; }
        .tick-marks { display: flex; justify-content: space-between; margin-top: 2px; }
        .tick { position: relative; width: 1px; }
        .tick::before { content: ''; display: block; width: 1px; height: 5px; background: #ccc; margin: 0 auto; }
        .tick-label { position: absolute; top: 6px; left: 50%; transform: translateX(-50%); font-size: 9px; color: #999; white-space: nowrap; }
        .tick:first-child .tick-label { left: 0; transform: translateX(0); }
        .tick:last-child .tick-label { left: auto; right: 0; transform: translateX(0); }
        .slider-bubble {
            position: absolute;
            top: 0;
            transform: translateX(-50%);
            background: #333;
            color: white;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
            white-space: nowrap;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.15s;
        }
        .slider-bubble.visible { opacity: 1; }
        .slider-value { width: 60px; text-align: right; font-weight: bold; color: #666; }
        .metrics { width: 200px; text-align: right; font-size: 12px; }
        .metric-count { font-size: 18px; font-weight: bold; color: #2196F3; }
        .metric-rate { color: #999; font-size: 11px; }
        .logs-section { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .logs-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        #logs { border: 1px solid #ddd; border-radius: 4px; height: 200px; overflow-y: auto; padding: 10px; background: #fafafa; font-family: monospace; font-size: 12px; line-height: 1.4; }
        .log-entry { padding: 3px 0; display: flex; align-items: center; gap: 8px; }
        .log-time { color: #999; flex-shrink: 0; }
        .log-detail { font-family: 'Courier New', monospace; color: #555; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0; }
        .op-badge {
            display: inline-block;
            min-width: 90px;
            text-align: center;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: bold;
            color: white;
            font-family: Arial, sans-serif;
        }
        .op-badge.kv_get { background: #2196F3; }
        .op-badge.kv_set { background: #4CAF50; }
        .op-badge.kv_upsert { background: #009688; }
        .op-badge.n1ql_select { background: #FF9800; }
        .op-badge.n1ql_upsert { background: #E91E63; }
        .op-badge.n1ql_join { background: #9C27B0; }
    </style>
</head>
<body>
    <h1>Couchbase Load Generator (Go + SDK)</h1>

    <div class="controls">
        <div class="status-bar">
            <div class="status" id="status">Status: Stopped</div>
            <button class="start-btn" id="start-btn" onclick="start()">Start</button>
            <button class="stop-btn" id="stop-btn" onclick="stop()" disabled>Stop</button>
        </div>

        <h3 style="margin: 15px 0 10px 0;">Operation Load (ops/sec per type)</h3>

        <div class="slider-row">
            <div class="slider-label">KV GET</div>
            <div class="slider-control">
                <div class="slider-bubble" id="b_kv_get_ops">0</div>
                <input type="range" id="kv_get_ops" min="0" max="5000" value="0" step="10"
                       oninput="onSliderInput('kv_get_ops')" onchange="updateConfig()">
                <div class="tick-marks">
                    <span class="tick"><span class="tick-label">0</span></span>
                    <span class="tick"><span class="tick-label">500</span></span>
                    <span class="tick"><span class="tick-label">1000</span></span>
                    <span class="tick"><span class="tick-label">1500</span></span>
                    <span class="tick"><span class="tick-label">2000</span></span>
                    <span class="tick"><span class="tick-label">2500</span></span>
                    <span class="tick"><span class="tick-label">3000</span></span>
                    <span class="tick"><span class="tick-label">3500</span></span>
                    <span class="tick"><span class="tick-label">4000</span></span>
                    <span class="tick"><span class="tick-label">4500</span></span>
                    <span class="tick"><span class="tick-label">5000</span></span>
                </div>
            </div>
            <div class="slider-value"><span id="kv_get_val">0</span></div>
            <div class="metrics">
                <div class="metric-count" id="m_kv_get">0</div>
                <div class="metric-rate" id="r_kv_get">0 ops/s</div>
            </div>
        </div>

        <div class="slider-row">
            <div class="slider-label">KV SET</div>
            <div class="slider-control">
                <div class="slider-bubble" id="b_kv_set_ops">0</div>
                <input type="range" id="kv_set_ops" min="0" max="5000" value="0" step="10"
                       oninput="onSliderInput('kv_set_ops')" onchange="updateConfig()">
                <div class="tick-marks">
                    <span class="tick"><span class="tick-label">0</span></span>
                    <span class="tick"><span class="tick-label">500</span></span>
                    <span class="tick"><span class="tick-label">1000</span></span>
                    <span class="tick"><span class="tick-label">1500</span></span>
                    <span class="tick"><span class="tick-label">2000</span></span>
                    <span class="tick"><span class="tick-label">2500</span></span>
                    <span class="tick"><span class="tick-label">3000</span></span>
                    <span class="tick"><span class="tick-label">3500</span></span>
                    <span class="tick"><span class="tick-label">4000</span></span>
                    <span class="tick"><span class="tick-label">4500</span></span>
                    <span class="tick"><span class="tick-label">5000</span></span>
                </div>
            </div>
            <div class="slider-value"><span id="kv_set_val">0</span></div>
            <div class="metrics">
                <div class="metric-count" id="m_kv_set">0</div>
                <div class="metric-rate" id="r_kv_set">0 ops/s</div>
            </div>
        </div>

        <div class="slider-row">
            <div class="slider-label">KV UPSERT</div>
            <div class="slider-control">
                <div class="slider-bubble" id="b_kv_upsert_ops">0</div>
                <input type="range" id="kv_upsert_ops" min="0" max="5000" value="0" step="10"
                       oninput="onSliderInput('kv_upsert_ops')" onchange="updateConfig()">
                <div class="tick-marks">
                    <span class="tick"><span class="tick-label">0</span></span>
                    <span class="tick"><span class="tick-label">500</span></span>
                    <span class="tick"><span class="tick-label">1000</span></span>
                    <span class="tick"><span class="tick-label">1500</span></span>
                    <span class="tick"><span class="tick-label">2000</span></span>
                    <span class="tick"><span class="tick-label">2500</span></span>
                    <span class="tick"><span class="tick-label">3000</span></span>
                    <span class="tick"><span class="tick-label">3500</span></span>
                    <span class="tick"><span class="tick-label">4000</span></span>
                    <span class="tick"><span class="tick-label">4500</span></span>
                    <span class="tick"><span class="tick-label">5000</span></span>
                </div>
            </div>
            <div class="slider-value"><span id="kv_upsert_val">0</span></div>
            <div class="metrics">
                <div class="metric-count" id="m_kv_upsert">0</div>
                <div class="metric-rate" id="r_kv_upsert">0 ops/s</div>
            </div>
        </div>

        <div class="slider-row">
            <div class="slider-label">N1QL SELECT</div>
            <div class="slider-control">
                <div class="slider-bubble" id="b_n1ql_select_ops">0</div>
                <input type="range" id="n1ql_select_ops" min="0" max="5000" value="0" step="10"
                       oninput="onSliderInput('n1ql_select_ops')" onchange="updateConfig()">
                <div class="tick-marks">
                    <span class="tick"><span class="tick-label">0</span></span>
                    <span class="tick"><span class="tick-label">500</span></span>
                    <span class="tick"><span class="tick-label">1000</span></span>
                    <span class="tick"><span class="tick-label">1500</span></span>
                    <span class="tick"><span class="tick-label">2000</span></span>
                    <span class="tick"><span class="tick-label">2500</span></span>
                    <span class="tick"><span class="tick-label">3000</span></span>
                    <span class="tick"><span class="tick-label">3500</span></span>
                    <span class="tick"><span class="tick-label">4000</span></span>
                    <span class="tick"><span class="tick-label">4500</span></span>
                    <span class="tick"><span class="tick-label">5000</span></span>
                </div>
            </div>
            <div class="slider-value"><span id="n1ql_select_val">0</span></div>
            <div class="metrics">
                <div class="metric-count" id="m_n1ql_select">0</div>
                <div class="metric-rate" id="r_n1ql_select">0 ops/s</div>
            </div>
        </div>

        <div class="slider-row">
            <div class="slider-label">N1QL UPSERT</div>
            <div class="slider-control">
                <div class="slider-bubble" id="b_n1ql_upsert_ops">0</div>
                <input type="range" id="n1ql_upsert_ops" min="0" max="5000" value="0" step="10"
                       oninput="onSliderInput('n1ql_upsert_ops')" onchange="updateConfig()">
                <div class="tick-marks">
                    <span class="tick"><span class="tick-label">0</span></span>
                    <span class="tick"><span class="tick-label">500</span></span>
                    <span class="tick"><span class="tick-label">1000</span></span>
                    <span class="tick"><span class="tick-label">1500</span></span>
                    <span class="tick"><span class="tick-label">2000</span></span>
                    <span class="tick"><span class="tick-label">2500</span></span>
                    <span class="tick"><span class="tick-label">3000</span></span>
                    <span class="tick"><span class="tick-label">3500</span></span>
                    <span class="tick"><span class="tick-label">4000</span></span>
                    <span class="tick"><span class="tick-label">4500</span></span>
                    <span class="tick"><span class="tick-label">5000</span></span>
                </div>
            </div>
            <div class="slider-value"><span id="n1ql_upsert_val">0</span></div>
            <div class="metrics">
                <div class="metric-count" id="m_n1ql_upsert">0</div>
                <div class="metric-rate" id="r_n1ql_upsert">0 ops/s</div>
            </div>
        </div>

        <div class="slider-row">
            <div class="slider-label">N1QL JOIN</div>
            <div class="slider-control">
                <div class="slider-bubble" id="b_n1ql_join_ops">0</div>
                <input type="range" id="n1ql_join_ops" min="0" max="5000" value="0" step="10"
                       oninput="onSliderInput('n1ql_join_ops')" onchange="updateConfig()">
                <div class="tick-marks">
                    <span class="tick"><span class="tick-label">0</span></span>
                    <span class="tick"><span class="tick-label">500</span></span>
                    <span class="tick"><span class="tick-label">1000</span></span>
                    <span class="tick"><span class="tick-label">1500</span></span>
                    <span class="tick"><span class="tick-label">2000</span></span>
                    <span class="tick"><span class="tick-label">2500</span></span>
                    <span class="tick"><span class="tick-label">3000</span></span>
                    <span class="tick"><span class="tick-label">3500</span></span>
                    <span class="tick"><span class="tick-label">4000</span></span>
                    <span class="tick"><span class="tick-label">4500</span></span>
                    <span class="tick"><span class="tick-label">5000</span></span>
                </div>
            </div>
            <div class="slider-value"><span id="n1ql_join_val">0</span></div>
            <div class="metrics">
                <div class="metric-count" id="m_n1ql_join">0</div>
                <div class="metric-rate" id="r_n1ql_join">0 ops/s</div>
            </div>
        </div>
    </div>

<div class="logs-section">
        <div class="logs-header">
            <h3 style="margin: 0;">Operation Log</h3>
            <div>
                <input type="text" id="filter" placeholder="Filter logs..." style="padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                <button onclick="toggleLogPause()" style="padding: 8px 15px; margin-left: 10px;">Pause</button>
                <button onclick="clearLogs()" style="padding: 8px 15px; margin-left: 10px;">Clear</button>
            </div>
        </div>
        <div id="logs"></div>
    </div>

    <script>
        let logsPaused = false;
        let lastMetrics = {};
        const bubbleHideTimers = {};

        function onSliderInput(sliderId) {
            const slider = document.getElementById(sliderId);
            const val = parseInt(slider.value);

            document.getElementById(sliderId.replace('_ops', '_val')).textContent = val;

            const bubble = document.getElementById('b_' + sliderId);
            bubble.textContent = val.toLocaleString();
            bubble.classList.add('visible');

            const min = parseInt(slider.min);
            const max = parseInt(slider.max);
            const percent = (val - min) / (max - min);
            const thumbWidth = 16;
            const offset = percent * (slider.offsetWidth - thumbWidth) + thumbWidth / 2;
            bubble.style.left = offset + 'px';

            clearTimeout(bubbleHideTimers[sliderId]);
            bubbleHideTimers[sliderId] = setTimeout(() => bubble.classList.remove('visible'), 1000);
        }

        function updateConfig() {
            const config = {
                kv_get_ops: parseInt(document.getElementById('kv_get_ops').value),
                kv_set_ops: parseInt(document.getElementById('kv_set_ops').value),
                kv_upsert_ops: parseInt(document.getElementById('kv_upsert_ops').value),
                n1ql_select_ops: parseInt(document.getElementById('n1ql_select_ops').value),
                n1ql_upsert_ops: parseInt(document.getElementById('n1ql_upsert_ops').value),
                n1ql_join_ops: parseInt(document.getElementById('n1ql_join_ops').value),
            };

            document.getElementById('kv_get_val').textContent = config.kv_get_ops;
            document.getElementById('kv_set_val').textContent = config.kv_set_ops;
            document.getElementById('kv_upsert_val').textContent = config.kv_upsert_ops;
            document.getElementById('n1ql_select_val').textContent = config.n1ql_select_ops;
            document.getElementById('n1ql_upsert_val').textContent = config.n1ql_upsert_ops;
            document.getElementById('n1ql_join_val').textContent = config.n1ql_join_ops;

            fetch('/api/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(config)
            });
        }

        function updateRunningState(isRunning) {
            document.getElementById('status').textContent = 'Status: ' + (isRunning ? 'Running' : 'Stopped');
            document.getElementById('start-btn').disabled = isRunning;
            document.getElementById('stop-btn').disabled = !isRunning;
        }

        function start() {
            fetch('/api/control/start', {method: 'POST'})
                .then(() => updateRunningState(true));
        }

        function stop() {
            fetch('/api/control/stop', {method: 'POST'})
                .then(() => updateRunningState(false));
        }

        function toggleLogPause() {
            logsPaused = !logsPaused;
            event.target.textContent = logsPaused ? 'Resume' : 'Pause';
        }

        function clearLogs() {
            fetch('/api/logs/clear', {method: 'POST'})
                .then(() => { document.getElementById('logs').innerHTML = ''; });
        }

        const OP_LABELS = {
            kv_get: 'KV GET',
            kv_set: 'KV SET',
            kv_upsert: 'KV UPSERT',
            n1ql_select: 'N1QL SELECT',
            n1ql_upsert: 'N1QL UPSERT',
            n1ql_join: 'N1QL JOIN',
        };

        function escapeHtml(str) {
            return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        setInterval(() => {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    const metrics = {
                        kv_get: data.kv_get,
                        kv_set: data.kv_set,
                        kv_upsert: data.kv_upsert,
                        n1ql_select: data.n1ql_select,
                        n1ql_upsert: data.n1ql_upsert,
                        n1ql_join: data.n1ql_join,
                    };

                    // Calculate rates
                    const elapsed = 1; // 1 second intervals
                    for (const [key, value] of Object.entries(metrics)) {
                        const rate = (value - (lastMetrics[key] || 0)) / elapsed;
                        document.getElementById('m_' + key).textContent = value.toLocaleString();
                        document.getElementById('r_' + key).textContent = Math.round(rate) + ' ops/s';
                    }
                    lastMetrics = metrics;

                    updateRunningState(data.is_running);
                });

            // Fetch and update logs
            if (!logsPaused) {
                fetch('/api/logs')
                    .then(r => r.json())
                    .then(data => {
                        const filter = document.getElementById('filter').value.toLowerCase();
                        const logsDiv = document.getElementById('logs');
                        let html = '';
                        for (const entry of data.logs || []) {
                            const label = OP_LABELS[entry.op] || entry.op;
                            const detail = entry.detail || '';
                            const searchText = (label + ' ' + detail).toLowerCase();
                            if (filter === '' || searchText.includes(filter)) {
                                html += '<div class="log-entry">' +
                                    '<span class="op-badge ' + entry.op + '">' + label + '</span>' +
                                    '<span class="log-time">' + entry.time + '</span>' +
                                    '<span class="log-detail" title="' + escapeHtml(detail) + '">' + escapeHtml(detail) + '</span>' +
                                    '</div>';
                            }
                        }
                        logsDiv.innerHTML = html;
                        logsDiv.scrollTop = logsDiv.scrollHeight;
                    });
            }
        }, 1000);
    </script>
</body>
</html>`
	w.Header().Set("Content-Type", "text/html")
	fmt.Fprint(w, html)
}

func apiStatus(w http.ResponseWriter, r *http.Request) {
	mu.RLock()
	defer mu.RUnlock()

	m := metrics
	m.IsRunning = config.IsRunning
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(m)
}

func apiConfig(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodPost {
		var newConfig map[string]interface{}
		if err := json.NewDecoder(r.Body).Decode(&newConfig); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		mu.Lock()
		if v, ok := newConfig["kv_get_ops"]; ok {
			config.KVGetOps = int(v.(float64))
		}
		if v, ok := newConfig["kv_set_ops"]; ok {
			config.KVSetOps = int(v.(float64))
		}
		if v, ok := newConfig["kv_upsert_ops"]; ok {
			config.KVUpsertOps = int(v.(float64))
		}
		if v, ok := newConfig["n1ql_select_ops"]; ok {
			config.N1QLSelectOps = int(v.(float64))
		}
		if v, ok := newConfig["n1ql_upsert_ops"]; ok {
			config.N1QLUpsertOps = int(v.(float64))
		}
		if v, ok := newConfig["n1ql_join_ops"]; ok {
			config.N1QLJoinOps = int(v.(float64))
		}
		mu.Unlock()
	}

	mu.RLock()
	defer mu.RUnlock()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(config)
}

func apiStart(w http.ResponseWriter, r *http.Request) {
	mu.Lock()
	// Stop any previous generation cleanly before starting a new one, so its
	// workers cannot outlive it and leak (see note on stopChan below).
	if config.IsRunning && stopChan != nil {
		close(stopChan)
	}

	newStopChan := make(chan struct{})
	stopChan = newStopChan
	config.IsRunning = true
	metrics = Metrics{}
	mu.Unlock()

	lastTime = time.Now()
	lastCount = 0

	// Pass this generation's stop channel explicitly instead of letting workers
	// read the shared global: if the global gets reassigned by a subsequent
	// apiStart before these workers wake from their rate-limit sleep, they'd
	// start reading the NEW channel (not closed) and never terminate - a
	// goroutine leak that grows without bound across repeated start/stop
	// cycles and was the likely cause of the process eventually crashing.
	go workloadGenerator(newStopChan)

	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"status":"started"}`)
}

func apiStop(w http.ResponseWriter, r *http.Request) {
	mu.Lock()
	isRunning := config.IsRunning
	config.IsRunning = false
	mu.Unlock()

	if isRunning && stopChan != nil {
		select {
		case <-stopChan:
		default:
			close(stopChan)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"status":"stopped"}`)
}

func apiHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"status":"healthy"}`)
}

func apiLogs(w http.ResponseWriter, r *http.Request) {
	logMutex.RLock()
	defer logMutex.RUnlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"logs": logs,
	})
}

func apiLogsClear(w http.ResponseWriter, r *http.Request) {
	logMutex.Lock()
	logs = nil
	logMutex.Unlock()

	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"status":"cleared"}`)
}

// allOpTypes lists every operation type along with how to read its current
// target ops/sec out of a Config snapshot.
var allOpTypes = []struct {
	name      string
	getTarget func(c Config) int
}{
	{"kv_get", func(c Config) int { return c.KVGetOps }},
	{"kv_set", func(c Config) int { return c.KVSetOps }},
	{"kv_upsert", func(c Config) int { return c.KVUpsertOps }},
	{"n1ql_select", func(c Config) int { return c.N1QLSelectOps }},
	{"n1ql_upsert", func(c Config) int { return c.N1QLUpsertOps }},
	{"n1ql_join", func(c Config) int { return c.N1QLJoinOps }},
}

// workloadGenerator gives each operation type its own dedicated, independently
// rate-limited worker pool instead of sharing one pool across all types. A
// single shared pool meant a slower operation type (e.g. N1QL, which has real
// network + query-engine latency) occupied worker "slots" for longer, which
// silently throttled the fast KV operations sharing that pool too - so
// dialing in an N1QL rate would drag down KV throughput even though the KV
// targets themselves hadn't changed. Per-type pools make each type's rate
// fully independent of what any other slider is set to.
func workloadGenerator(stop chan struct{}) {
	var wg sync.WaitGroup
	for _, ot := range allOpTypes {
		ot := ot
		wg.Add(1)
		go func() {
			defer wg.Done()
			runOpTypePool(stop, ot.name, ot.getTarget)
		}()
	}

	// Metrics updater
	go func() {
		ticker := time.NewTicker(1 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-stop:
				return
			case <-ticker.C:
			}
			mu.Lock()
			if !config.IsRunning {
				mu.Unlock()
				return
			}

			currentCount := metrics.Total
			elapsed := time.Since(lastTime).Seconds()
			if elapsed > 0 {
				metrics.CurrentRate = int64((float64(currentCount-lastCount) / elapsed))
			}
			lastCount = currentCount
			lastTime = time.Now()
			mu.Unlock()
		}
	}()

	wg.Wait()
}

// runOpTypePool spawns a fixed-size worker pool dedicated to a single
// operation type. Workers idle-poll (cheaply, no network calls) whenever that
// type's target is 0, so turning a slider up mid-run picks up immediately
// without needing to restart the pool.
func runOpTypePool(stop chan struct{}, opType string, getTarget func(c Config) int) {
	numWorkers := 128

	var wg sync.WaitGroup
	for i := 0; i < numWorkers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
					runIteration(opType, getTarget, numWorkers)
				}
			}
		}()
	}
	wg.Wait()
}

// runIteration executes one worker cycle for a single operation type (run it,
// pace the rate). It recovers from any panic so one bad operation can't take
// down the whole process - previously an unhandled panic in any of these
// background goroutines (not covered by net/http's per-request recover) would
// crash the entire binary, and Docker's restart policy would silently bring
// it back up with all workload state reset, which looked like "it just
// stopped".
func runIteration(opType string, getTarget func(c Config) int, numWorkers int) {
	defer func() {
		if rec := recover(); rec != nil {
			log.Printf("worker recovered from panic: %v", rec)
		}
	}()

	mu.RLock()
	if !config.IsRunning {
		mu.RUnlock()
		return
	}
	target := getTarget(config)
	mu.RUnlock()

	if target <= 0 {
		time.Sleep(10 * time.Millisecond)
		return
	}

	executeOperation(opType)

	// Rate limiting: pace this worker so the aggregate across all workers of
	// THIS type matches its own target ops/sec - fully independent of any
	// other operation type's rate. Uses float math (no integer flooring) so
	// it stays accurate at any target.
	delayNs := float64(numWorkers) * 1e9 / float64(target)
	time.Sleep(time.Duration(delayNs))
}

const n1qlSelectStmt = "SELECT * FROM `travel-sample` LIMIT 5"
const n1qlJoinStmt = "SELECT h.name, r.sourceairport FROM `travel-sample` r JOIN `travel-sample` h ON r.airlineid = META(h).id WHERE r.type = 'route' LIMIT 5"

// runQuery executes a N1QL statement and fully drains + closes the result.
// cluster.Query() returns a *QueryResult backed by a live HTTP connection;
// leaving it unread/unclosed means gocbcore can never return that connection
// to its pool. Under sustained concurrent load this leaked one persistConn
// (and its two background readLoop/writeLoop goroutines) per query - tens of
// thousands of goroutines within minutes - which is what caused CPU to spike
// and throughput to decay over time.
func runQuery(statement string, ctx context.Context) {
	result, err := cluster.Query(statement, &gocb.QueryOptions{Context: ctx})
	if err != nil {
		return
	}
	for result.Next() {
	}
	_ = result.Close()
}

func executeOperation(opType string) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	var detail string

	switch opType {
	case "kv_get":
		detail = "dummy-key"
		_, _ = bucket.DefaultCollection().Get(detail, &gocb.GetOptions{Context: ctx})
		atomic.AddInt64(&metrics.KVGet, 1)
	case "kv_set":
		detail = fmt.Sprintf("key-%d-%d", time.Now().UnixNano(), rand.Int63())
		_, _ = bucket.DefaultCollection().Upsert(detail, map[string]interface{}{"type": "load-gen"}, &gocb.UpsertOptions{Context: ctx})
		atomic.AddInt64(&metrics.KVSet, 1)
	case "kv_upsert":
		detail = fmt.Sprintf("key-%d-%d", time.Now().UnixNano(), rand.Int63())
		_, _ = bucket.DefaultCollection().Upsert(detail, map[string]interface{}{"type": "load-gen"}, &gocb.UpsertOptions{Context: ctx})
		atomic.AddInt64(&metrics.KVUpsert, 1)
	case "n1ql_select":
		detail = n1qlSelectStmt
		runQuery(detail, ctx)
		atomic.AddInt64(&metrics.N1QLSelect, 1)
	case "n1ql_upsert":
		key := fmt.Sprintf("key-%d-%d", time.Now().UnixNano(), rand.Int63())
		detail = fmt.Sprintf("UPSERT INTO `travel-sample` (KEY, VALUE) VALUES (\"%s\", {\"type\":\"load-gen\"})", key)
		runQuery(detail, ctx)
		atomic.AddInt64(&metrics.N1QLUpsert, 1)
	case "n1ql_join":
		detail = n1qlJoinStmt
		runQuery(detail, ctx)
		atomic.AddInt64(&metrics.N1QLJoin, 1)
	}

	atomic.AddInt64(&metrics.Total, 1)
	addLog(opType, detail)
}

func addLog(opType, detail string) {
	logMutex.Lock()
	defer logMutex.Unlock()

	logs = append(logs, LogEntry{Time: time.Now().Format("15:04:05"), Op: opType, Detail: detail})
	if len(logs) > maxLogs {
		logs = logs[1:]
	}
}
