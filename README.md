# Couchbase Observability Agent — Demo Stack

A Docker Compose stack that demos an **"observability agent"**: an AI agent
that answers questions about a live Couchbase cluster (health, schema,
query performance) through the **official Couchbase MCP server**, side by
side with a **Grafana** dashboard fed by Couchbase's native Prometheus
metrics — plus a live workload generator so there's always something
happening to observe.

`docker compose up -d` provisions everything: the cluster, the
`travel-sample` bucket + index, the dashboards, and the workload. No manual
clicking required.

## 1. Services & ports

| Service | Port(s) | URL | What it is |
|---|---|---|---|
| **couchbase** | `8091-8096`, `11210` | [localhost:8091](http://localhost:8091) | The Couchbase Server cluster itself (Admin UI/REST on 8091-8096, KV binary protocol on 11210) |
| **couchbase-init** | – | – | One-shot bootstrap job (creates the cluster + `travel-sample` bucket + index), exits when done — `Exited (0)` in `docker compose ps` is expected, not an error |
| **mcp-server** | `8000` | [localhost:8000/mcp](http://localhost:8000/mcp) | Official Couchbase MCP server, **read-only** — this is what an MCP client (Claude Desktop, Cursor, the `streamlit-agent` below, ...) talks to |
| **prometheus** | `9090` | [localhost:9090](http://localhost:9090) | Scrapes Couchbase's native `/metrics` endpoint and the load generator's `/metrics` |
| **grafana** | `3000` | [localhost:3000](http://localhost:3000) | Dashboards fed by Prometheus (`admin`/`admin`, or browse anonymously) |
| **load-generator** | `5000` | [localhost:5000](http://localhost:5000) | Drives a continuous SQL++ workload (`cbc-n1qlback`) against `travel-sample` and shows it live — see [`load-generator/README.md`](load-generator/README.md) |
| **streamlit-agent** | `8501` | [localhost:8501](http://localhost:8501) | Chat UI: asks an LLM (OpenAI) questions, which it answers by calling the MCP server — see [`streamlit-agent/SETUP.md`](streamlit-agent/SETUP.md) |

```
┌────────────────┐  SQL++   ┌─────────────┐  /metrics  ┌────────────┐  PromQL  ┌─────────┐
│ load-generator │ ───────▶ │  Couchbase  │ ─────────▶ │ Prometheus │ ───────▶ │ Grafana │
│     :5000      │          │    :8091    │            │   :9090    │          │  :3000  │
└────────────────┘          └─────────────┘            └────────────┘          └─────────┘
                                    ▲
                                    │ SDK / REST (read-only)
                                    │
                             ┌─────────────┐          ┌─────────────────┐
                             │ MCP server  │ ◀──────  │ streamlit-agent │
                             │    :8000    │          │      :8501      │
                             └─────────────┘          └─────────────────┘
                                    ▲
                                    │
                        your own MCP client (Claude Desktop, Cursor, ...)
```

## 2. Prerequisites

- Docker Desktop (or Docker Engine + Compose v2) — `docker compose version`
- **At least 4 GB of RAM allocated to Docker** (6–8 GB is safer). Couchbase's
  own services alone reserve ~1.8 GB; if Docker is capped at 2 GB the node
  will fail health checks or crash-loop. Docker Desktop → Settings →
  Resources → Memory.
- Ports free on the host: `8091-8096`, `11210`, `8000`, `9090`, `3000`,
  `5000`, `8501` (see the table above).
- On Apple Silicon: `load-generator` needs an amd64 image (its CLI tool has
  no arm64 build) and runs under emulation — see
  [`load-generator/README.md`](load-generator/README.md#platform-note).

## 3. Start everything

```bash
docker compose up -d
docker compose logs -f couchbase-init   # watch until you see "✅ Couchbase demo cluster is ready"
```

First boot takes ~1-2 minutes (image pulls + cluster bootstrap).

### Credentials

| | |
|---|---|
| Couchbase Administrator | `Administrator` / `password123` |
| Bucket | `travel-sample` |
| Grafana | `admin` / `admin` |

Change any of these in `docker-compose.yml` + `prometheus/prometheus.yml` +
`init/init-cluster.sh` together if you want different ones.

> This demo reuses the `Administrator` account for Prometheus scraping to
> keep the compose file simple. In anything beyond a demo, create a
> dedicated user with the **External Stats Reader** role instead — see
> [Couchbase docs: Configure Prometheus](https://docs.couchbase.com/server/current/manage/monitor/set-up-prometheus-for-monitoring.html).

The **streamlit-agent** needs its own OpenAI API key, kept separate from
everything above (its own `.env`, never committed) — see
[`streamlit-agent/SETUP.md`](streamlit-agent/SETUP.md).

## 4. Verify each piece

**Couchbase** — [localhost:8091](http://localhost:8091), log in with
`Administrator` / `password123`. You should see one node, the
`travel-sample` bucket, and query activity from the load generator.

**Prometheus** — [localhost:9090/targets](http://localhost:9090/targets).
Both the `couchbase` and `load-generator` jobs should show as `UP`.

**Grafana** — [localhost:3000](http://localhost:3000). Two dashboards are
pre-provisioned: **Couchbase Cluster Overview** and **Load Generator -
Query Duration**. Panels should be moving within ~30 seconds.

**Load generator** — [localhost:5000](http://localhost:5000). Live
latency histograms + throughput for the 3 demo queries, with per-query and
global Stop/Start controls. See [`load-generator/README.md`](load-generator/README.md).

**MCP server** — Streamable HTTP at `http://localhost:8000/mcp`:

```bash
curl -i http://localhost:8000/mcp
```

A non-connection-refused response confirms it's up (MCP itself needs a real
client handshake, so `curl` alone won't return tool data).

**streamlit-agent** — [localhost:8501](http://localhost:8501). Use the
sidebar's "Test MCP connection" button first.

## 5. Connect an MCP client

The server runs in **read-only mode** (`CB_MCP_READ_ONLY_MODE=true`) —
appropriate for an observability agent that should never write to the
cluster.

**Claude Desktop or claude.ai (Pro/Max/Team/Enterprise):**
Settings → Connectors → Add custom connector → paste `http://localhost:8000/mcp` → Add → Connect.

> Only works from **Claude Desktop** (or any MCP client on the same machine
> as Docker) — it needs to reach `localhost`. Claude's web app and Cowork
> can't reach your local machine. See Anthropic's
> [custom connectors guide](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
> for specifics.

**Any MCP client that supports Streamable HTTP** (Cursor, and others — see
the [official server README](https://github.com/couchbase/mcp-server-couchbase#streamable-http-transport-mode)):

```json
{
  "mcpServers": {
    "couchbase-demo": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Or just use the bundled **streamlit-agent** at
[localhost:8501](http://localhost:8501) — no client setup needed, just an
OpenAI API key (see [`streamlit-agent/SETUP.md`](streamlit-agent/SETUP.md)).

## 6. Demo script — things to ask

1. **"What's the health of my Couchbase cluster?"**
   → `get_cluster_health_and_services` / `test_cluster_connection`.
2. **"What buckets exist and what's in the `travel-sample` bucket's schema?"**
   → `get_buckets_in_cluster`, `get_scopes_and_collections_in_bucket`, `get_schema_for_collection`.
3. **"What are the longest-running or most frequent queries right now?"**
   → `get_longest_running_queries`, `get_most_frequent_queries` — the load
   generator's 3 continuously-running queries give it something to report
   on (edit them in [`load-generator/queries.json`](load-generator/queries.json)).
4. **"Are any queries using the primary index instead of a covering index?"**
   → `get_queries_using_primary_index`, `get_queries_not_using_covering_index`.
5. Pull up Grafana side-by-side and ask the same health question again — a
   natural way to show the agent's answer lining up with what's on screen.

## 7. Reset / tear down

```bash
docker compose down          # stop everything, keep data
docker compose down -v       # stop everything and wipe all volumes (full reset)
```

To re-run `couchbase-init` after a partial failure without wiping
everything: `docker compose up -d --force-recreate couchbase-init`.

## 8. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `couchbase` container keeps restarting / health check never passes | Docker has too little memory allocated — raise it to ≥4 GB |
| `couchbase-init` exits with "Cluster is already initialized" errors | Harmless — the script is idempotent and only logs this, it doesn't fail |
| Grafana panels show "No data" | Check the Prometheus targets page first — if a job is down, check the Administrator password matches in both `docker-compose.yml` and `prometheus/prometheus.yml` |
| MCP client can't connect | Confirm `docker compose ps` shows `mcp-server` running and `curl http://localhost:8000/mcp` doesn't refuse the connection; on claude.ai (web), switch to Claude Desktop — web can't reach localhost |
| `load-generator` won't build/start | On Apple Silicon this image runs under amd64 emulation — see [`load-generator/README.md`](load-generator/README.md#platform-note) |
| Ports already in use | Something else on your machine is using one of the ports in the table above — stop it or remap ports on the left side of the `ports:` entries in `docker-compose.yml` |

## 9. Going from this demo to the real thing

This stack deliberately mirrors the "Phase 1 — Observability agent" step
from the evaluation summary: official MCP server, read-only, feeding
insight back through your own tools rather than acting on the cluster. If
you outgrow it, the natural next steps are a multi-node cluster, a real
alerting path from Prometheus (Alertmanager) into whatever your agent
watches, and — only after that's solid — a sandboxed Phase 2 evaluation of
admin-REST-driven automation, kept strictly out of this observability path.
