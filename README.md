# Couchbase Observability Agent — Demo Stack

Single-node **Couchbase Server 8.0.2**, the **official Couchbase MCP server**
(read-only), **Prometheus**, and **Grafana** — wired together with Docker
Compose to demo the "observability agent" use case: an agent that can query
live cluster health and query-performance data through MCP, side by side
with a Grafana dashboard fed by Couchbase's native Prometheus metrics.

No manual clicking required — `docker compose up` provisions the cluster,
bucket, index, dashboards, and a small workload generator automatically.

```
┌─────────────┐   /metrics    ┌────────────┐   PromQL   ┌─────────┐
│  Couchbase  │──────────────▶│ Prometheus │───────────▶│ Grafana │
│   8.0.2     │               └────────────┘            └─────────┘
│  (1 node)   │
└─────────────┘
      ▲  SDK / REST (read-only)
      │
┌─────────────┐
│ MCP server  │◀── your MCP client (Claude, Cursor, ...)
│ (official)  │
└─────────────┘
```

## 1. Prerequisites

- Docker Desktop (or Docker Engine + Compose v2) — `docker compose version`
- **At least 4 GB of RAM allocated to Docker** (6–8 GB is safer). Couchbase's
  own services alone reserve ~1.8 GB; if Docker is capped at 2 GB the node
  will fail health checks or crash-loop. Docker Desktop → Settings →
  Resources → Memory.
- Ports free on the host: `8091-8096`, `11210`, `8000`, `9090`, `3000`.

## 2. Layout

```
.
├── docker-compose.yml
├── init/init-cluster.sh              # one-shot cluster/bucket/index bootstrap
├── prometheus/prometheus.yml         # scrapes Couchbase's native /metrics
├── grafana/provisioning/...          # datasource + dashboard auto-provisioned
├── grafana/dashboards/couchbase-overview.json
└── load-generator/generate-load.sh   # light continuous workload for a "live" demo
```

## 3. Start everything

```bash
docker compose up -d
docker compose logs -f couchbase-init   # watch until you see "✅ Couchbase demo cluster is ready"
```

First boot takes ~1-2 minutes (image pulls + cluster bootstrap). `couchbase-init`
is a one-shot job — `docker compose ps` will show it as `Exited (0)`, that's expected.

Credentials used throughout this demo (change them in `docker-compose.yml`,
`prometheus/prometheus.yml`, and `init/init-cluster.sh` if you want different ones):

| | |
|---|---|
| Couchbase Administrator | `Administrator` / `password123` |
| Bucket | `demo` |
| Grafana | `admin` / `admin` |

> This demo reuses the `Administrator` account for Prometheus scraping to
> keep the compose file simple. In anything beyond a demo, create a
> dedicated user with the **External Stats Reader** role instead — see
> [Couchbase docs: Configure Prometheus](https://docs.couchbase.com/server/current/manage/monitor/set-up-prometheus-for-monitoring.html).

## 4. Verify each piece

**Couchbase** — [http://localhost:8091](http://localhost:8091), log in with
`Administrator` / `password123`. You should see one node, the `demo` bucket,
and item counts climbing (the load generator is writing to it).

**Prometheus** — [http://localhost:9090/targets](http://localhost:9090/targets).
The `couchbase` job should show as `UP`. Try the query `kv_ops` in the
Prometheus expression browser to confirm data is flowing.

**Grafana** — [http://localhost:3000](http://localhost:3000) (`admin`/`admin`,
or just browse anonymously — anonymous viewer access is enabled for the demo).
Open **Dashboards → Couchbase → Couchbase Cluster Overview**. Panels should
be moving within ~30 seconds thanks to the load generator.

**MCP server** — it's running in Streamable HTTP mode at
`http://localhost:8000/mcp`. Quick sanity check:

```bash
curl -i http://localhost:8000/mcp
```

You should get an HTTP response (not a connection error) — MCP itself
expects a proper client handshake, so a raw `curl` won't return tool data,
but a non-connection-refused response confirms the server is up.

## 5. Connect an MCP client

The server is running in **read-only mode** (`CB_MCP_READ_ONLY_MODE=true`) —
appropriate for an observability agent that should never write to the cluster.

**Claude Desktop or claude.ai (Pro/Max/Team/Enterprise):**
Settings → Connectors → Add custom connector → paste `http://localhost:8000/mcp` → Add → Connect.

> Note: this only works from **Claude Desktop** (or any MCP client running on
> the same machine as Docker), since it needs to reach `localhost`. Claude's
> web app and Cowork can't reach a server on your local machine — only the
> desktop app's local-network access can. See Anthropic's
> [custom connectors guide](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
> for specifics, since connector behavior can change — check there if
> anything above doesn't match what you see.

**Any MCP client that supports Streamable HTTP** (Cursor, and others per the
[official server README](https://github.com/couchbase/mcp-server-couchbase#streamable-http-transport-mode)):

```json
{
  "mcpServers": {
    "couchbase-demo": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

## 6. Demo script — observability agent prompts

Once connected, try asking your MCP client things like:

1. **"What's the health of my Couchbase cluster?"**
   → calls `get_cluster_health_and_services` / `test_cluster_connection`.
2. **"What buckets exist and what's in the `demo` bucket's schema?"**
   → `get_buckets_in_cluster`, `get_scopes_and_collections_in_bucket`, `get_schema_for_collection`.
3. **"What are the longest-running or most frequent queries right now?"**
   → `get_longest_running_queries`, `get_most_frequent_queries` — the load
   generator's `UPSERT`/`SELECT COUNT(*)` loop gives it something to report on.
4. **"Are any queries using the primary index instead of a covering index?"**
   → `get_queries_using_primary_index`, `get_queries_not_using_covering_index`
   — a good prompt to pivot into "here's what a remediation agent could flag,
   even though this server can only observe, not act."
5. Pull up the Grafana dashboard side-by-side and ask the same health
   question again — a natural way to show the agent's answer lining up with
   what's on screen.

## 7. Reset / tear down

```bash
docker compose down          # stop everything, keep data
docker compose down -v       # stop everything and wipe all volumes (full reset)
```

If you need to re-run `couchbase-init` after a partial failure without
wiping everything: `docker compose up -d --force-recreate couchbase-init`.

## 8. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `couchbase` container keeps restarting / health check never passes | Docker has too little memory allocated — raise it to ≥4 GB |
| `couchbase-init` exits with "Cluster is already initialized" errors | Harmless — the script is idempotent and only logs this, it doesn't fail |
| Grafana panels show "No data" | Check Prometheus targets page first — if the `couchbase` job is down, check the Administrator password matches in both `docker-compose.yml` and `prometheus/prometheus.yml` |
| MCP client can't connect | Confirm `docker compose ps` shows `mcp-server` as running and `curl http://localhost:8000/mcp` doesn't refuse the connection; if you're on claude.ai (web), switch to Claude Desktop — web can't reach localhost |
| Ports already in use | Something else on your machine is using 8091-8096, 9090, 3000, or 8000 — stop it or remap ports on the left side of the `ports:` entries in `docker-compose.yml` |

## 9. Going from this demo to the real thing

This stack deliberately mirrors the "Phase 1 — Observability agent" step
from the evaluation summary: official MCP server, read-only, feeding
insight back through your own tools rather than acting on the cluster. If
you outgrow it, the natural next steps are a multi-node cluster, a real
alerting path from Prometheus (Alertmanager) into whatever your agent
watches, and — only after that's solid — a sandboxed Phase 2 evaluation of
admin-REST-driven automation, kept strictly out of this observability path.
