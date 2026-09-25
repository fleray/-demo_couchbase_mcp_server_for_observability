# Couchbase Load Generator

A Go-based load generator for Couchbase with a live web control panel:
per-operation-type throughput sliders, real-time metrics, and a searchable
operation log — backed directly by the native Couchbase Go SDK (`gocb/v2`)
for high throughput.

> This started as a Python/Flask prototype (`app.py`) but was rewritten in Go
> because the Python SDK could not sustain the target throughput — client-side
> overhead capped it around 5K ops/sec regardless of threading. The files in
> this directory from that era (`app.py`, `requirements.txt`, `cbc-pillowfight`,
> `libcouchbase.so*`, `workload.c`, `templates/`, `static/`, `couchbase_lib/`)
> are no longer used; `main.go` is the current implementation.

## Features

- **Web control panel** (served by the Go binary itself, no separate frontend
  build): one slider per operation type, each showing its live cumulative
  count and current ops/sec rate inline.
- **Six operation types**, each independently configurable from 0-5,000
  ops/sec:
  - KV: GET, SET, UPSERT
  - N1QL (SQL++): SELECT, UPSERT, JOIN
- **Per-type dedicated worker pools** — each operation type gets its own
  independently rate-limited pool of workers, so one type's throughput can
  never throttle another's (see [Recent Improvements](#recent-improvements)).
- **Operation log** with colored badges per operation type, the actual doc
  ID (KV) or full SQL++ statement (N1QL) executed, a text filter, a
  pause/resume toggle, and a clear button.
- **Travel-sample dataset**: all queries run against Couchbase's `travel-sample`
  sample bucket.
- **REST API** for programmatic control (see below).

## Quick Start

```bash
docker-compose up -d
```

Then open **http://localhost:5000**.

To rebuild after editing `main.go`:

```bash
docker-compose build load-generator
docker-compose up -d --no-deps --force-recreate load-generator
```

`--force-recreate` matters here: `docker-compose restart` reuses whatever
image the container was already created from and will **not** pick up a
freshly built image.

## Web Control Panel

- **Status bar**: shows Running/Stopped, with Start/Stop buttons that
  disable themselves appropriately (Start is disabled while running, Stop
  is disabled while stopped).
- **Operation Load sliders**: one per operation type, 0-5,000 ops/sec, with
  tick marks and labels every 500 units and a live value bubble while
  dragging. To the right of each slider: the cumulative op count and the
  current ops/sec rate for that type.
- **Operation Log**: live-updating list of executed operations. Each entry
  shows a colored badge (KV GET blue, KV SET green, KV UPSERT teal, N1QL
  SELECT orange, N1QL UPSERT pink, N1QL JOIN purple), a timestamp, and the
  actual doc ID or SQL++ statement that ran. Filter box searches both the
  operation label and its detail text; Pause freezes the view without
  stopping the workload; Clear wipes the log.

## API Endpoints

### Get current status (cumulative counts + current rate)

```bash
GET /api/status
```

```json
{
  "kv_get": 12034,
  "kv_set": 8021,
  "kv_upsert": 15872,
  "n1ql_select": 3021,
  "n1ql_upsert": 1204,
  "n1ql_join": 512,
  "total": 40664,
  "current_rate": 987,
  "is_running": true
}
```

### Get / update configuration (absolute ops/sec per type, not percentages)

```bash
GET /api/config
POST /api/config
Content-Type: application/json

{
  "kv_get_ops": 1000,
  "kv_set_ops": 500,
  "kv_upsert_ops": 500,
  "n1ql_select_ops": 200,
  "n1ql_upsert_ops": 100,
  "n1ql_join_ops": 50
}
```

Sliders can be changed at any time, including while the workload is running
— each operation type's dedicated worker pool picks up the new target
immediately without needing a restart.

### Workload control

```bash
POST /api/control/start
POST /api/control/stop
```

### Operation log

```bash
GET  /api/logs        # {"logs": [{"time": "...", "op": "kv_upsert", "detail": "key-..."}]}
POST /api/logs/clear
```

### Health check

```bash
GET /api/health
```

## Environment Variables

- `CB_HOST`: Couchbase hostname (default: `couchbase`)
- `CB_USERNAME`: Username (default: `Administrator`)
- `CB_PASSWORD`: Password (default: `password123`)
- `CB_BUCKET`: Bucket name (default: `travel-sample`)

## N1QL Statements Used

```sql
-- n1ql_select
SELECT * FROM `travel-sample` LIMIT 5

-- n1ql_upsert
UPSERT INTO `travel-sample` (KEY, VALUE) VALUES ("<generated-key>", {"type":"load-gen"})

-- n1ql_join
SELECT h.name, r.sourceairport
FROM `travel-sample` r
JOIN `travel-sample` h ON r.airlineid = META(h).id
WHERE r.type = 'route'
LIMIT 5
```

All identifiers containing a hyphen (`travel-sample`) must be backtick-quoted
in N1QL — an earlier version of the UPSERT statement omitted this and failed.

## Architecture

- **`main.go`**: single Go binary, no external dependencies at runtime.
  - Serves the control panel UI and REST API over `net/http`.
  - Connects to Couchbase via `gocb/v2`, using a tuned connection string
    (`max_idle_http_connections=200&max_perhost_idle_http_connections=200`)
    to give the SDK's HTTP transport enough headroom for concurrent N1QL
    workers (see below).
  - One dedicated goroutine pool (128 workers) per operation type, each
    independently rate-limited to that type's own configured ops/sec.
  - In-memory rolling operation log (last 100 entries) guarded by its own
    mutex, separate from the config/metrics mutex.

## Recent Improvements

- **Migrated Python/Flask + Python SDK → Go + native Couchbase Go SDK.**
  The Python implementation plateaued around 5K ops/sec purely from
  client-side overhead (confirmed by testing `cbc-pillowfight`, the
  Couchbase C SDK benchmark tool, which reached 95K+ ops/sec on the same
  cluster). Go removes that ceiling.

- **Per-operation-type worker pools**, replacing a single shared pool of
  workers that randomly picked which operation type to run each iteration.
  Under that design, a slower operation type (N1QL, with real network +
  query-engine latency) occupied worker "slots" longer, which silently
  throttled whatever fast KV operations happened to share that pool — so
  turning up an N1QL slider would drag down KV throughput even though the
  KV targets hadn't changed. Each type now has its own pool, fully isolated
  from every other type's rate and latency.

- **Fixed a rate-limiter bug** where integer-division flooring in the
  per-worker delay calculation pinned throughput at exactly the worker
  count (128 ops/sec) for any target below that, regardless of the
  configured slider value. Replaced with floating-point pacing.

- **Fixed a goroutine leak across Start/Stop cycles.** The stop signal was
  read from a mutable global variable; if a worker was mid-sleep when
  Start was clicked again before it noticed Stop, it would silently start
  reading the *new* run's channel and never terminate. Repeated start/stop
  cycles leaked goroutines and Couchbase connections without bound,
  eventually crashing the process — Docker's restart policy would then
  bring it back up with everything reset to zero, which looked like "it
  just stopped" from the UI. Each run now gets its own stop channel passed
  explicitly instead of read from a shared global.

- **Fixed a critical N1QL connection leak.** `cluster.Query()`'s returned
  `*QueryResult` was never drained or closed, so gocb could never return
  its underlying HTTP connection to the pool. Under sustained load this
  leaked one connection (and its two background goroutines) per query —
  tens of thousands of goroutines within minutes, driving client CPU past
  1000% and causing measured throughput to silently decay over time with
  no logged errors. Every query path now fully drains (`result.Next()`)
  and closes (`result.Close()`) its result.

- **Added panic recovery per worker iteration** so a single failing
  operation can no longer crash the entire process (previously, panics in
  background worker goroutines weren't covered by `net/http`'s per-request
  recovery).

- **Fixed the N1QL JOIN statement**, which used an invalid `ON KEYS
  r.airlineid` clause (type mismatch — `airlineid` is numeric, not a
  document key) and errored in the Query Workbench. Replaced with
  `ON r.airlineid = META(h).id`.

- **Fixed the N1QL UPSERT statement**, which referenced `travel-sample`
  unquoted; bucket names containing a hyphen must be backtick-quoted in
  N1QL.

- **UI redesign**: switched from percentage-based sliders (which had to sum
  to 100%) to independent absolute ops/sec sliders (0-5,000) per operation
  type, each with tick marks, a live value bubble, and inline
  cumulative-count/rate display. Added the operation log panel (colored
  badges, doc ID / SQL++ detail per entry, filter, pause, clear) and
  Start/Stop button disabled-state handling.

## Troubleshooting

### Couchbase connection failed at startup
- Ensure the `couchbase` container is up and the `travel-sample` bucket is
  loaded (`couchbase-init` handles this on first run).
- Check `CB_HOST` / `CB_USERNAME` / `CB_PASSWORD` match `docker-compose.yml`.

### Throughput won't reach the configured target
- Check `docker stats load-generator couchbase` — if Couchbase CPU is high
  and load-generator CPU is low, the cluster itself is the bottleneck (real
  capacity limit, not a bug). If load-generator CPU is unexpectedly high
  (100%+) for a modest ops/sec, suspect a connection leak — check
  `docker exec load-generator sh -c "ls /proc/1/task | wc -l"`; it should
  stay under ~30 threads at steady state.
- Confirm the Couchbase query engine is actually up:
  `docker exec couchbase ps aux | grep -E "cbq-engine|indexer"`. Both must
  be running for N1QL operations to work at all.
- Check host disk space — Couchbase's indexer will crash with
  `no space left on device` if the Docker VM's disk fills up, taking the
  N1QL query engine down with it (KV operations keep working since they
  don't depend on it).

### One operation type's rate affects another's
This was a real bug (see Recent Improvements above) and should no longer
happen. If you see it again, it's a regression — each operation type should
have a fully independent worker pool.

## License

Part of the Couchbase demo observability stack.
