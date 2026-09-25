# Couchbase Load Generator

Drives continuous SQL++ traffic against the `travel-sample` bucket using
Couchbase's own `cbc-n1qlback` CLI tool (from `libcouchbase`), and exposes a
live 3-pane web view plus Prometheus metrics.

> Earlier iterations of this directory (a Python/Flask app driving
> `cbc-pillowfight`, and later a Go binary with its own control-panel UI) are
> archived in [`OLD/`](OLD/) for reference and are not used by the current
> `Dockerfile`.

## Architecture

- **`run-new.sh`** — entrypoint, prints config, execs `app-new.py`.
- **`queries.json`** — the list of queries to run, loaded at startup (fails
  fast with a clear error if it's missing a `key`/`statement`, or has a
  duplicate/reserved key). Edit it to change what runs — no code change
  needed. It's bind-mounted into the container read-only
  (`docker-compose.yml`), so editing it on the host and restarting the
  container (`docker compose restart load-generator`, no rebuild required)
  is enough to pick up the change.
- **`app-new.py`** — for each query in `queries.json`, runs a long-lived
  `cbc-n1qlback -T ...` process (`-T`/`--timings` enables its live per-second
  latency histogram, printed right alongside `QUERIES/SEC`). Every line of
  its output is parsed:
  - `QUERIES/SEC` / `ROWS/SEC` / `ERRORS` / `+Ns` rows update that query's
    live stats readout.
  - histogram bucket rows (e.g. `[360 - 369]us |# - 1`) update that query's
    latest bucket-count snapshot (their counts are cumulative per process,
    so the latest value per bucket is always current) and feed the
    newly-observed delta into a Prometheus histogram — every individual
    latency sample `cbc-n1qlback` itself recorded, not a separate
    approximation.
- **Web view** (`http://localhost:5000`) — page split into 3 horizontal
  panels, one per query: the full statement as a header, a one-line live
  stats readout, then two Chart.js charts side by side (left/right halves):
  - **left** — a live-updating horizontal bar chart of the latency
    histogram.
  - **right** — a live-updating line chart of that query's queries/sec over
    time (category x-axis: one tick per collected sample, in arrival
    order), with a time-range dropdown (5m/15m/30m/1h/2h) and the
    Stop/Start toggle button both sitting at its top-right. That history
    lives only in the browser tab's memory (capped at 2h of 1-per-second
    samples, reading 0 for any stopped period) — nothing is persisted
    server-side; switching the dropdown just re-slices what's already in
    memory, no refetch.

  `static/chart.umd.min.js` is vendored locally so the demo doesn't depend
  on a CDN at runtime. The Stop/Start button controls both of that query's
  charts together: stopping kills its `cbc-n1qlback` process and freezes
  the histogram at its last frame (the QPS line drops to 0); starting again
  wipes that query's histogram/stats and launches a fresh process. A
  "Stop All" / "Start All" button in the top bar controls all 3 queries at
  once.
- **`/metrics`** (`http://localhost:5000/metrics`) — Prometheus exposition
  of `loadgen_query_duration_seconds` (a `Histogram`, labeled by `query`),
  scraped by the `load-generator` job in `prometheus/prometheus.yml` and
  charted in Grafana's **Load Generator - Query Duration** dashboard.

Note: `cbc-n1qlback -T` also supports a single `Mean = ..., StdDeviation = ...`
summary line in some libcouchbase builds, but the version pinned in Debian
12's apt repo (3.3.19) never printed one in testing (checked exhaustively,
including under SIGINT) — only the per-second bucket histogram. Parsing that
histogram directly, as above, sidesteps the difference entirely.

## The queries

Defined in [`queries.json`](queries.json), currently:

```sql
-- count_all
SELECT COUNT(*) FROM `travel-sample`

-- select_5000
SELECT * FROM `travel-sample` LIMIT 5000

-- route_airline_join
SELECT h.name, r.sourceairport FROM `travel-sample` r
JOIN `travel-sample` h ON r.airlineid = META(h).id
WHERE r.type = 'route' LIMIT 5
```

`key` becomes that query's URL-safe identifier (used in `/api/control/<key>/...`
and as its Prometheus `query` label) and its panel title in the web view is
just its `statement` — so keep `key` short and stable, and free to add,
remove, or reorder entries (the web view and `/metrics` labels follow
whatever's in the file; `key` must be unique and can't be `all`, which is
reserved for the "control every query" endpoints).

## Platform note

`cbc-n1qlback` comes from libcouchbase's `libcouchbase3-tools` apt package,
which Couchbase only publishes for **amd64** — there is no arm64 build. The
image is pinned to `linux/amd64` (in both the `Dockerfile` and
`docker-compose.yml`) and runs under emulation (Rosetta/QEMU) on Apple
Silicon hosts.

## Environment variables

- `CB_HOST` (default `couchbase`)
- `CB_USERNAME` (default `Administrator`)
- `CB_PASSWORD` (default `password123`)
- `CB_BUCKET` (default `travel-sample`)
- `QUERIES_FILE` (default `queries.json` next to `app-new.py`)
