# Quick Start Guide

## Start the Full Stack

From the root directory (`/Users/fabriceleray/Clients/Kering/2026/demo_mcp_server_observability/`):

```bash
docker-compose up -d
```

This will:
1. Start Couchbase Server 8.0.2
2. Initialize the cluster and load the **travel-sample** bucket
3. Start Prometheus for metrics collection
4. Start Grafana for visualization
5. Start the new Load Generator web app

## Access the Load Generator

**Admin Panel**: http://localhost:5000

The interface provides:
- ✅ Real-time workload control (Start/Stop)
- ✅ Operation metrics dashboard
- ✅ Dynamic configuration adjustments
- ✅ Pre-configured load profiles for quick switching
- ✅ Quick load increase/decrease buttons

## First Steps

1. **Open the Admin Panel**: http://localhost:5000
2. **Check Status**: Green indicator shows Couchbase is connected
3. **Load a Profile**: Select from the dropdown (e.g., "Light Demo Load")
4. **Start Workload**: Click "Start Workload" button
5. **Monitor**: Watch the metrics update in real-time
6. **Adjust**: Modify sliders and click "Load" to apply changes immediately

## Available Workload Profiles

- **Light Demo Load** (10 ops/sec): Default setup for demos
- **Balanced Workload** (50 ops/sec): 50% KV, 50% N1QL
- **KV Heavy Workload** (500 ops/sec): 95% KV operations
- **Query Heavy Workload** (100 ops/sec): 70% N1QL queries
- **Read-Only Workload** (200 ops/sec): 100% SELECT + GET
- **Write Heavy Workload** (100 ops/sec): 80% write operations
- **Stress Test** (1000 ops/sec): High throughput test
- **Analytics Workload** (50 ops/sec): Complex JOINs with analytics

## API Examples

### Get Current Status
```bash
curl http://localhost:5000/api/status
```

### Start Workload
```bash
curl -X POST http://localhost:5000/api/control/start
```

### Update Configuration
```bash
curl -X POST http://localhost:5000/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "kv_get_percentage": 40,
    "kv_set_percentage": 30,
    "operations_per_second": 100
  }'
```

### Load a Preset Profile
```bash
curl -X POST http://localhost:5000/api/profile/balanced
```

### Get Available Profiles
```bash
curl http://localhost:5000/api/profiles
```

### Health Check
```bash
curl http://localhost:5000/api/health
```

## What's Different from the Old Script

| Feature | Old (`generate-load.sh`) | New App |
|---------|--------------------------|---------|
| **Interface** | CLI only | Web UI + REST API |
| **Control** | Only hardcoded behavior | Full dynamic control |
| **Operations** | Only UPSERT + SELECT | KV + N1QL mix |
| **Query Types** | 2 types | SELECT, UPSERT, GET, SET, JOIN |
| **Configuration** | Hardcoded in script | JSON profiles, real-time updates |
| **Metrics** | Invisible | Real-time dashboard |
| **Adjustments** | Restart required | Live updates |
| **Bucket** | Hard-coded `demo` | Configurable (default: travel-sample) |
| **Throughput** | Fixed 2-sec intervals | Configurable 1-1000 ops/sec |

## Troubleshooting

### Load generator container won't start
```bash
# Check logs
docker logs load-generator

# Verify Couchbase is running
docker logs couchbase
```

### Admin panel shows "Disconnected"
- Wait 30 seconds for Couchbase to fully initialize
- Check that travel-sample bucket was created
- Verify credentials match docker-compose.yml

### High error rates in metrics
- Reduce `operations_per_second` value
- Check Couchbase logs: `docker logs couchbase`
- Check if cluster resources are exhausted

### Can't access admin panel
- Ensure port 5000 is not already in use
- Check firewall settings
- Verify container is running: `docker ps | grep load-generator`

## Monitoring

### Couchbase Admin Console
- **URL**: http://localhost:8091
- **User**: Administrator
- **Password**: password123
- View bucket stats, query performance, memory usage

### Prometheus
- **URL**: http://localhost:9090
- Scrapes Couchbase metrics automatically
- Query builder available

### Grafana
- **URL**: http://localhost:3000
- **User**: admin
- **Password**: admin
- Pre-configured dashboards for Couchbase

## Next Steps

1. **Explore Different Profiles**: Try various workload mixes
2. **Monitor in Grafana**: Watch cluster performance metrics
3. **Custom Queries**: Modify JSON config for specific needs
4. **Scale Up**: Gradually increase operations_per_second
5. **Production**: Adjust resources and deployment strategy

## Architecture

```
┌─────────────────────────────────────────────────┐
│         Admin Dashboard (HTML/CSS/JS)           │
│  - Real-time metrics chart                      │
│  - Configuration sliders                        │
│  - Start/Stop controls                          │
│  - Profile selector                             │
└──────────────────┬──────────────────────────────┘
                   │ HTTP / REST API
┌──────────────────▼──────────────────────────────┐
│         Flask Web Server (Python)               │
│  - /api/status      → Get current metrics       │
│  - /api/config      → Update configuration      │
│  - /api/control/*   → Start/Stop workload       │
│  - /api/profiles    → List available profiles   │
└──────────────────┬──────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
┌───────▼────────┐    ┌───────▼────────┐
│  Workload      │    │  Couchbase     │
│  Engine        │───▶│  Cluster       │
│  (Threading)   │    │  (travel-      │
│                │    │   sample)      │
└────────────────┘    └────────────────┘
```

## Performance Tips

1. **Monitor Memory**: Watch Docker stats while running
2. **Batch Operations**: Use profiles with realistic mix
3. **Slow Queries**: Monitor N1QL query performance in Couchbase console
4. **Error Handling**: Check error rates in metrics
5. **Rate Limiting**: Start low (10 ops/sec) and gradually increase

## Questions?

- Check **README.md** for detailed documentation
- Review **workload_profiles.json** for configuration examples
- Inspect **app.py** source code for implementation details
